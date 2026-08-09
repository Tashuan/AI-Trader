"""
Crypto historical data providers for backtesting.

Hyperliquid, Binance.US, and Coinbase all offer free, public, no-auth
OHLCV candle endpoints with full multi-year history — far better than
yfinance's 60-day limit on 15m crypto data.

All providers implement the same `history(symbol, *, period, interval, **kwargs)`
contract as YFinanceProvider, returning a pandas DataFrame with a
UTC DatetimeIndex and capitalized OHLCV columns so backtesters can
drop them in as a direct replacement.

Symbol handling: providers receive yfinance-format symbols (e.g. "BTC-USD")
and strip the "-USD" suffix internally.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Hyperliquid public info endpoint (no auth required)
HYPERLIQUID_API_URL = os.environ.get("HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz/info")
_HYPERLIQUID_MAX_CANDLES = 5000

# Binance.US public REST endpoint (no auth required).
# Binance.com returns HTTP 451 in the US; .us is the compliant alternative.
# Override via env if you're outside the US and want the full Binance.com.
_BINANCE_KLINES_URL = os.environ.get("BINANCE_KLINES_URL", "https://api.binance.us/api/v3/klines")
_BINANCE_MAX_CANDLES = 1000

# Coinbase Exchange public candles endpoint (no auth required, US-friendly).
_COINBASE_CANDLES_URL = os.environ.get(
    "COINBASE_CANDLES_URL", "https://api.exchange.coinbase.com/products"
)
_COINBASE_MAX_CANDLES = 300  # Coinbase API rejects requests exceeding 300 aggregations

# Map yfinance interval strings to provider-native intervals.
# Both Hyperliquid and Binance accept the same tokens for these.
_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

_PERIOD_DAYS = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
    "max": 3650,
}


def _strip_usd(symbol: str) -> str:
    """Convert yfinance crypto symbol (BTC-USD) to base symbol (BTC)."""
    s = symbol.strip().upper()
    for suffix in ("-USD", "=X"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def _resolve_time_range(period: Optional[str], start: Optional[str], end: Optional[str]) -> tuple[int, int]:
    """Resolve period/start/end kwargs into (start_ms, end_ms) epoch milliseconds."""
    now = datetime.now(timezone.utc)

    if start is not None:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    elif period is not None:
        days = _PERIOD_DAYS.get(period, 30)
        start_dt = now - timedelta(days=days)
    else:
        start_dt = now - timedelta(days=30)

    if end is not None:
        end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    else:
        end_dt = now

    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


def _build_dataframe(rows: list[dict], tz: timezone = timezone.utc):
    """Build a yfinance-shaped DataFrame from a list of candle dicts."""
    import pandas as pd

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.set_index("Datetime")
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.index.name = "Datetime"
    return df.sort_index()


# ============================================================
# Hyperliquid Provider
# ============================================================

class HyperliquidProvider:
    """Fetches crypto OHLCV history from Hyperliquid's public candleSnapshot endpoint.

    Free, no auth, no rate-limit headaches. Covers ~150 perps including
    BTC, ETH, SOL. Max 5000 candles per request — paginates automatically.
    """

    def history(self, symbol: str, *, period: Optional[str] = "1mo", interval: str = "1d", **kwargs):
        coin = _strip_usd(symbol)
        if interval not in _INTERVAL_MS:
            return None  # unsupported interval — let fallback handle it

        start_ms, end_ms = _resolve_time_range(period, kwargs.get("start"), kwargs.get("end"))
        candles = _fetch_hyperliquid_candles(coin, interval, start_ms, end_ms)
        if not candles:
            return None
        return _build_dataframe(candles)

    def quote(self, symbol: str):
        df = self.history(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])


def _fetch_hyperliquid_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Paginate Hyperliquid candleSnapshot to cover the full time range."""
    interval_ms = _INTERVAL_MS[interval]
    chunk_ms = _HYPERLIQUID_MAX_CANDLES * interval_ms
    all_candles: list[dict] = []
    cursor = start_ms

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": cursor, "endTime": chunk_end},
        }
        try:
            resp = requests.post(HYPERLIQUID_API_URL, json=payload, timeout=15)
            if not resp.ok:
                logger.warning("Hyperliquid candleSnapshot HTTP %s for %s", resp.status_code, coin)
                break
            data = resp.json()
        except Exception as exc:
            logger.warning("Hyperliquid fetch failed for %s: %s", coin, exc)
            break

        if not isinstance(data, list) or len(data) == 0:
            break

        for c in data:
            if not isinstance(c, dict):
                continue
            t = c.get("t")
            if t is None:
                continue
            all_candles.append({
                "t": int(t), "Open": float(c["o"]), "High": float(c["h"]),
                "Low": float(c["l"]), "Close": float(c["c"]), "Volume": float(c.get("v", 0)),
            })

        last_t = int(data[-1].get("t", cursor))
        if last_t <= cursor:
            break  # no progress — avoid infinite loop
        cursor = last_t + interval_ms
        time.sleep(0.15)  # be gentle

    # Deduplicate by timestamp
    seen: dict[int, dict] = {}
    for c in all_candles:
        seen[c["t"]] = c
    return sorted(seen.values(), key=lambda c: c["t"])


# ============================================================
# Binance Provider
# ============================================================

class BinanceProvider:
    """Fetches crypto OHLCV history from Binance's public klines endpoint.

    Free, no auth. Full history at 1m/5m/15m/1h/4h/1d. Max 1000 candles
    per request — paginates automatically. Best source for 15m crypto
    history that yfinance can't provide.
    """

    def history(self, symbol: str, *, period: Optional[str] = "1mo", interval: str = "1d", **kwargs):
        base = _strip_usd(symbol)
        if interval not in _INTERVAL_MS:
            return None

        binance_symbol = f"{base}USDT"
        start_ms, end_ms = _resolve_time_range(period, kwargs.get("start"), kwargs.get("end"))
        candles = _fetch_binance_candles(binance_symbol, interval, start_ms, end_ms)
        if not candles:
            # Try BUSD pair as fallback (some coins only have BUSD)
            candles = _fetch_binance_candles(f"{base}BUSD", interval, start_ms, end_ms)
        if not candles:
            return None
        return _build_dataframe(candles)

    def quote(self, symbol: str):
        df = self.history(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])


def _fetch_binance_candles(binance_symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Paginate Binance klines to cover the full time range."""
    all_candles: list[dict] = []
    cursor = start_ms

    while cursor < end_ms:
        params = {
            "symbol": binance_symbol, "interval": interval,
            "startTime": cursor, "endTime": end_ms,
            "limit": _BINANCE_MAX_CANDLES,
        }
        try:
            resp = requests.get(_BINANCE_KLINES_URL, params=params, timeout=15)
            if resp.status_code == 400 or resp.status_code == 404:
                # Symbol doesn't exist on Binance — bail immediately
                return []
            if not resp.ok:
                logger.warning("Binance klines HTTP %s for %s", resp.status_code, binance_symbol)
                break
            data = resp.json()
        except Exception as exc:
            logger.warning("Binance fetch failed for %s: %s", binance_symbol, exc)
            break

        if not isinstance(data, list) or len(data) == 0:
            break

        for k in data:
            # kline format: [openTime, open, high, low, close, volume, closeTime, ...]
            all_candles.append({
                "t": int(k[0]), "Open": float(k[1]), "High": float(k[2]),
                "Low": float(k[3]), "Close": float(k[4]), "Volume": float(k[5]),
            })

        last_t = int(data[-1][0])
        if len(data) < _BINANCE_MAX_CANDLES or last_t <= cursor:
            break  # got all remaining candles or no progress
        cursor = last_t + 1
        time.sleep(0.1)  # be gentle

    return all_candles


# ============================================================
# Coinbase Provider
# ============================================================

# Coinbase granularity is in seconds
_INTERVAL_SECONDS = {k: v // 1000 for k, v in _INTERVAL_MS.items()}


class CoinbaseProvider:
    """Fetches crypto OHLCV history from Coinbase Exchange's public candles endpoint.

    Free, no auth, US-friendly. Max 350 candles per request — paginates
    automatically. Good fallback when Hyperliquid and Binance.US don't
    list a coin. Note: Coinbase only supports 1m/5m/15m/1h/6h/1d granularities.
    """

    def history(self, symbol: str, *, period: Optional[str] = "1mo", interval: str = "1d", **kwargs):
        base = _strip_usd(symbol)
        if interval not in _INTERVAL_SECONDS:
            return None

        product_id = f"{base}-USD"
        start_ms, end_ms = _resolve_time_range(period, kwargs.get("start"), kwargs.get("end"))
        candles = _fetch_coinbase_candles(product_id, interval, start_ms, end_ms)
        if not candles:
            return None
        return _build_dataframe(candles)

    def quote(self, symbol: str):
        df = self.history(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])


def _fetch_coinbase_candles(product_id: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Paginate Coinbase candles to cover the full time range."""
    granularity = _INTERVAL_SECONDS[interval]
    chunk_ms = _COINBASE_MAX_CANDLES * granularity * 1000
    all_candles: list[dict] = []
    cursor = start_ms

    while cursor < end_ms:
        chunk_end = min(cursor + chunk_ms, end_ms)
        params = {"granularity": granularity, "start": _ms_to_iso(cursor), "end": _ms_to_iso(chunk_end)}
        url = f"{_COINBASE_CANDLES_URL}/{product_id}/candles"
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code in (400, 404):
                return []  # product doesn't exist
            if not resp.ok:
                logger.warning("Coinbase candles HTTP %s for %s", resp.status_code, product_id)
                break
            data = resp.json()
        except Exception as exc:
            logger.warning("Coinbase fetch failed for %s: %s", product_id, exc)
            break

        if not isinstance(data, list) or len(data) == 0:
            break

        # Coinbase format: [time(epoch sec), low, high, open, close, volume]
        # Note: Coinbase returns candles in descending order (newest first)
        for c in data:
            all_candles.append({
                "t": int(c[0]) * 1000, "Open": float(c[3]), "High": float(c[2]),
                "Low": float(c[1]), "Close": float(c[4]), "Volume": float(c[5]),
            })

        # Use the newest candle (data[0] since descending) to advance cursor
        newest_t = int(data[0][0]) * 1000
        if len(data) < _COINBASE_MAX_CANDLES or newest_t <= cursor:
            break
        cursor = newest_t + granularity * 1000
        time.sleep(0.12)

    return all_candles


def _ms_to_iso(ms: int) -> str:
    """Convert epoch milliseconds to ISO 8601 string for Coinbase API."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# Fallback Chain Provider
# ============================================================

class CryptoFallbackProvider:
    """Tries Hyperliquid → Binance.US → Coinbase → yfinance for crypto history.

    Use this as the default provider for crypto backtesting. It picks
    the best available source automatically and falls back gracefully.
    For non-crypto symbols (equities), it delegates directly to yfinance.
    """

    # Symbols that are crypto (same set as scan_core.CRYPTO_SYMBOLS)
    CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "DOT", "LINK", "MATIC",
                      "UNI", "ATOM", "NEAR", "APT", "OP", "ARB", "INJ", "TIA", "SUI"}

    def __init__(self):
        self._hyperliquid = HyperliquidProvider()
        self._binance = BinanceProvider()
        self._coinbase = CoinbaseProvider()
        self._yfinance = None  # lazy import

    def _is_crypto(self, symbol: str) -> bool:
        base = _strip_usd(symbol)
        return base.upper() in self.CRYPTO_SYMBOLS

    def _yf_provider(self):
        if self._yfinance is None:
            from market_data import YFinanceProvider
            self._yfinance = YFinanceProvider()
        return self._yfinance

    def history(self, symbol: str, *, period: Optional[str] = "1mo", interval: str = "1d", **kwargs):
        if not self._is_crypto(symbol):
            return self._yf_provider().history(symbol, period=period, interval=interval, **kwargs)

        # Try each source in order of preference
        for provider in (self._hyperliquid, self._binance, self._coinbase):
            df = provider.history(symbol, period=period, interval=interval, **kwargs)
            if df is not None and not df.empty:
                return df

        # Last resort: yfinance (limited but better than nothing)
        return self._yf_provider().history(symbol, period=period, interval=interval, **kwargs)

    def quote(self, symbol: str):
        if not self._is_crypto(symbol):
            return self._yf_provider().quote(symbol)
        for provider in (self._hyperliquid, self._binance, self._coinbase, self._yf_provider()):
            price = provider.quote(symbol)
            if price is not None:
                return price
        return None
