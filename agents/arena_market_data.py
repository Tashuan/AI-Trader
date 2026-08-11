"""Canonical market-data routing for Arena runners and backtests."""

from __future__ import annotations

from typing import Optional

_CRYPTO = {
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC",
    "UNI", "ATOM", "NEAR", "APT", "OP", "ARB", "INJ", "TIA", "SUI", "LTC",
    "BCH", "XRP",
}
_components: Optional[tuple] = None
_router: Optional["ArenaMarketDataProvider"] = None


def _base(symbol: str) -> str:
    return symbol.strip().upper().replace("-USD", "")


def _is_crypto(symbol: str) -> bool:
    return _base(symbol) in _CRYPTO or "/" in symbol


def _is_equity(symbol: str) -> bool:
    value = symbol.strip().upper()
    return not _is_crypto(value) and not value.endswith(("=F", "=X")) and not value.startswith("^")


def _sources() -> tuple:
    global _components
    if _components is None:
        from crypto_data_providers import CryptoFallbackProvider
        from equity_data_providers import AlpacaProvider
        from schwab_provider import get_schwab_provider
        _components = (AlpacaProvider(), get_schwab_provider(), CryptoFallbackProvider(), None)
    return _components


def _yfinance():
    global _components
    alpaca, schwab, crypto, yf = _sources()
    if yf is None:
        from market_data import YFinanceProvider
        _components = (alpaca, schwab, crypto, YFinanceProvider())
    return _components[3]


def _history(symbol: str, period: str, interval: str, kwargs: dict):
    alpaca, schwab, crypto, _ = _sources()
    if _is_crypto(symbol):
        return crypto.history(symbol, period=period, interval=interval, **kwargs)
    if not _is_equity(symbol):
        return _yfinance().history(symbol, period=period, interval=interval, **kwargs)
    if alpaca.available:
        try:
            frame = alpaca.history(symbol, period=period, interval=interval, **kwargs)
            if frame is not None and not frame.empty:
                return frame
        except Exception:
            pass
    if schwab.is_configured:
        frame = schwab.history(symbol, period=period, interval=interval, **kwargs)
        if frame is not None and not frame.empty:
            return frame
    return _yfinance().history(symbol, period=period, interval=interval, **kwargs)


def _quote(symbol: str):
    alpaca, schwab, crypto, _ = _sources()
    if _is_crypto(symbol):
        return crypto.quote(symbol)
    if not _is_equity(symbol):
        return _yfinance().quote(symbol)
    if schwab.is_configured:
        try:
            quote = schwab.quote(symbol)
            if quote and quote.get("last", 0) > 0:
                return quote
        except Exception:
            pass
    if alpaca.available:
        try:
            from alpaca_realtime_provider import get_alpaca_provider
            quote = get_alpaca_provider().quote(symbol)
            if quote and quote.get("last", 0) > 0:
                return quote
        except Exception:
            pass
    return _yfinance().quote(symbol)


class ArenaMarketDataProvider:
    """Small facade over the Arena provider routing functions."""

    def history(self, symbol: str, *, period: str = "1mo",
                interval: str = "1d", **kwargs):
        return _history(symbol, period, interval, kwargs)

    def quote(self, symbol: str):
        return _quote(symbol)

    @property
    def uses_yfinance_fallback(self) -> bool:
        alpaca, schwab, _, _ = _sources()
        return not alpaca.available and not schwab.is_configured


def get_arena_market_data() -> ArenaMarketDataProvider:
    """Return the shared provider router used by Arena runtime paths."""
    global _router
    if _router is None:
        _router = ArenaMarketDataProvider()
    return _router
