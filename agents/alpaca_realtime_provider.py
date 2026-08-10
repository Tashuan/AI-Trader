"""Alpaca real-time market data provider.

Implements the MarketDataProvider protocol (same interface as
YFinanceProvider / SchwabProvider) so the scalp scan, backtester,
and server price fetcher can use Alpaca real-time data transparently.

Alpaca free tier (Basic plan) offers:
  - Historical OHLCV bars for US equities (1m/5m/15m/1h/1Day, back to 2016)
  - Real-time stock quotes (NBBO bid/ask/size)
  - Real-time stock trades (last price/size/timestamp)
  - Stock snapshots (day stats + last quote + last trade + minute bar)
  - Crypto bars, quotes, trades, snapshots, and full L2 orderbook
  - Market clock (open/closed + next open/close)
  - Market calendar (holidays/sessions)
  - Full asset universe (thousands of tickers, filterable)
  - Options contracts and chains
  - News articles (stock + crypto)

Requires APCA_API_KEY_ID and APCA_API_SECRET_KEY (or ALPACA_API_KEY /
ALPACA_SECRET_KEY) environment variables. Sign up at alpaca.markets
(free paper trading account) and get keys from the dashboard.

All history() calls return a pandas DataFrame with a UTC DatetimeIndex
and capitalized OHLCV columns, matching the yfinance contract.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

# Alpaca accepts both APCA_API_KEY_ID/APCA_API_SECRET_KEY (legacy) and
# ALPACA_API_KEY/ALPACA_SECRET_KEY (newer naming). Try both.
_API_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
_SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")

# Paper trading data endpoint (free tier). Live data endpoint is the same
# for market data — trading endpoint differs but we don't trade here.
_DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets/v2")
_CRYPTO_DATA_URL = os.environ.get("ALPACA_CRYPTO_DATA_URL", "https://data.alpaca.markets/v1beta3/crypto/us")
_TRADING_URL = os.environ.get("ALPACA_TRADING_URL", "https://api.alpaca.markets/v2")
_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 2
_RETRY_DELAY = 0.5
_MAX_BARS = 10_000  # Alpaca page limit per request

# Map our interval strings to Alpaca timeframe strings.
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1Min",
    "2m": "2Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "60m": "1Hour",
    "1h": "1Hour",
    "1d": "1Day",
    "1w": "1Week",
    "1mo": "1Month",
}

_PERIOD_DAYS: dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
    "max": 3650,
}


# ── Helpers ───────────────────────────────────────────────────────

def _resolve_time_range(period: Optional[str], start: Optional[str],
                        end: Optional[str]) -> tuple[str, str]:
    """Resolve period/start/end into (start_iso, end_iso) RFC3339 strings."""
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
    return start_dt.isoformat(), end_dt.isoformat()


def _strip_symbol(symbol: str) -> str:
    """Strip yfinance-style suffixes for Alpaca API calls."""
    return symbol.strip().upper().replace("-USD", "").replace("=F", "").replace("^", "")


def _build_ohlcv_df(bars: list[dict]) -> "pd.DataFrame":
    """Build a yfinance-shaped DataFrame from Alpaca bar dicts."""
    import pandas as pd
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["Datetime"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df = df.set_index("Datetime")
    cols = ["Open", "High", "Low", "Close", "Volume"]
    for c in cols:
        if c not in df.columns:
            df[c] = float("nan")
    df = df[cols].astype(float)
    df.index.name = "Datetime"
    return df.sort_index()


class AlpacaRealtimeProvider:
    """Alpaca real-time market data provider implementing the MarketDataProvider protocol.

    Provides real-time quotes/trades, historical bars, crypto L2 orderbook,
    market clock, and asset universe via Alpaca's free-tier REST API.
    Requires APCA_API_KEY_ID / APCA_API_SECRET_KEY (or ALPACA_API_KEY /
    ALPACA_SECRET_KEY).
    """

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self._api_key = api_key or _API_KEY
        self._secret_key = secret_key or _SECRET_KEY
        if not self._api_key or not self._secret_key:
            logger.warning("AlpacaRealtimeProvider: API keys not set. "
                           "Sign up at alpaca.markets (free) and add "
                           "APCA_API_KEY_ID/APCA_API_SECRET_KEY to .env.")

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._secret_key)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._secret_key)

    # ── HTTP helper ──────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _get(self, base_url: str, path: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET request with retry. Returns parsed JSON or None."""
        if not self.available:
            return None
        url = f"{base_url}{path}"
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=self._headers(), params=params,
                                    timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    wait = 1.0 * (attempt + 1)
                    logger.warning("AlpacaRealtimeProvider: rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                logger.warning("AlpacaRealtimeProvider: GET %s failed (attempt %d): %s",
                               path, attempt + 1, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
        return None

    # ── MarketDataProvider protocol ──────────────────────────────

    def history(self, symbol: str, *, period: Optional[str] = "1mo",
                interval: str = "1d", **kwargs) -> Optional["pd.DataFrame"]:
        """Fetch historical OHLCV bars from Alpaca."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        tf = _INTERVAL_MAP.get(interval)
        if tf is None:
            logger.warning("AlpacaRealtimeProvider: unsupported interval %s, defaulting to 1Day", interval)
            tf = "1Day"
        start_iso, end_iso = _resolve_time_range(period, kwargs.get("start"), kwargs.get("end"))
        all_bars: list[dict] = []
        page_token = None
        while True:
            params: dict[str, Any] = {
                "timeframe": tf, "start": start_iso, "end": end_iso,
                "limit": _MAX_BARS, "feed": "iex",
            }
            if page_token:
                params["page_token"] = page_token
            data = self._get(_DATA_URL, f"/stocks/{sym}/bars", params)
            if data is None:
                break
            all_bars.extend(data.get("bars") or [])
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(0.1)
        df = _build_ohlcv_df(all_bars)
        if df.empty:
            logger.warning("AlpacaRealtimeProvider: no bars for %s %s (%s → %s)",
                           sym, interval, start_iso, end_iso)
            return None
        logger.info("AlpacaRealtimeProvider: %s %s → %d bars (%s to %s)",
                    sym, interval, len(df), df.index[0], df.index[-1])
        return df

    def quote(self, symbol: str) -> Optional[dict]:
        """Fetch real-time NBBO quote.

        Returns {symbol, bid, ask, spread, spread_pct, last, bid_size, ask_size}
        or None if unavailable.
        """
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        data = self._get(_DATA_URL, f"/stocks/{sym}/quotes/latest")
        if not data:
            return None
        # Latest quote endpoint returns {"quote": {...}, "symbol": "..."}
        q = data.get("quote") or data.get("quotes", {}).get(sym)
        if not q:
            return None
        bid = float(q.get("bp", 0) or 0)
        ask = float(q.get("ap", 0) or 0)
        bid_size = float(q.get("bs", 0) or 0)
        ask_size = float(q.get("as", 0) or 0)
        spread = ask - bid if bid > 0 and ask > 0 else 0.0
        last = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        # Try last trade for 'last'
        trade = self.last_trade(symbol)
        if trade and trade.get("price", 0) > 0:
            last = trade["price"]
        return {
            "symbol": sym, "bid": bid, "ask": ask, "last": last,
            "bid_size": bid_size, "ask_size": ask_size,
            "spread": spread,
            "spread_pct": (spread / last * 100) if last > 0 else 0.0,
        }

    # ── Extended methods (beyond the protocol) ───────────────────

    def last_trade(self, symbol: str) -> Optional[dict]:
        """Fetch the most recent trade for a symbol."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        data = self._get(_DATA_URL, f"/stocks/{sym}/trades/latest")
        if not data:
            return None
        # Latest trade endpoint returns {"trade": {...}, "symbol": "..."}
        t = data.get("trade") or data.get("trades", {}).get(sym)
        if not t:
            return None
        return {
            "symbol": sym,
            "price": float(t.get("p", 0) or 0),
            "size": float(t.get("s", 0) or 0),
            "exchange": t.get("x", ""),
            "timestamp": t.get("t", ""),
        }

    def snapshot(self, symbol: str) -> Optional[dict]:
        """Fetch full stock snapshot (day stats + last quote + last trade + minute bar)."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        data = self._get(_DATA_URL, "/stocks/snapshots", {"symbols": sym})
        if not data:
            return None
        return data.get(sym)

    def snapshots_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch snapshots for multiple symbols in one call."""
        if not self.available or not symbols:
            return {}
        syms = ",".join(_strip_symbol(s) for s in symbols)
        data = self._get(_DATA_URL, "/stocks/snapshots", {"symbols": syms})
        if not data:
            return {}
        return data

    def crypto_orderbook(self, symbol: str) -> Optional[dict]:
        """Fetch full L2 orderbook for a crypto pair (e.g. 'BTC/USD').

        Returns {symbol, bids: [{p, s}], asks: [{p, s}], timestamp} or None.
        """
        if not self.available:
            return None
        sym = symbol.strip().upper()
        if "/" not in sym:
            sym = f"{sym}/USD"
        data = self._get(_CRYPTO_DATA_URL, "/latest/orderbooks", {"symbols": sym})
        if not data or "orderbooks" not in data:
            return None
        ob = data["orderbooks"].get(sym)
        if not ob:
            return None
        return {
            "symbol": sym,
            "bids": [{"p": float(b["p"]), "s": float(b["s"])} for b in ob.get("b", [])],
            "asks": [{"p": float(a["p"]), "s": float(a["s"])} for a in ob.get("a", [])],
            "timestamp": ob.get("t", ""),
        }

    def crypto_quote(self, symbol: str) -> Optional[dict]:
        """Fetch latest crypto quote (NBBO)."""
        if not self.available:
            return None
        sym = symbol.strip().upper()
        if "/" not in sym:
            sym = f"{sym}/USD"
        data = self._get(_CRYPTO_DATA_URL, "/latest/quotes", {"symbols": sym})
        if not data or "quotes" not in data:
            return None
        q = data["quotes"].get(sym)
        if not q:
            return None
        bid = float(q.get("bp", 0) or 0)
        ask = float(q.get("ap", 0) or 0)
        return {
            "symbol": sym, "bid": bid, "ask": ask,
            "spread": ask - bid if bid > 0 and ask > 0 else 0.0,
            "last": (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0,
        }

    def market_clock(self) -> Optional[dict]:
        """Fetch current market status (open/closed + next open/close)."""
        if not self.available:
            return None
        return self._get(_TRADING_URL, "/clock")

    def market_calendar(self, start: str, end: str) -> Optional[list[dict]]:
        """Fetch market calendar (sessions/holidays) for a date range."""
        if not self.available:
            return None
        data = self._get(_TRADING_URL, "/calendar", {"start": start, "end": end})
        return data if isinstance(data, list) else None

    def screen_movers(self, symbols: list[str], top_n: int = 20) -> list[dict]:
        """Screen a universe of symbols for top movers by daily change %.

        Uses batch snapshots (efficient — one API call for many symbols).
        Returns list of {symbol, change_pct, price, volume} sorted by abs(change).
        """
        if not self.available or not symbols:
            return []
        # Batch in groups of 50 (URL length safety)
        results: list[dict] = []
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i + 50]
            snaps = self.snapshots_batch(batch)
            for sym, snap in snaps.items():
                if not snap:
                    continue
                daily = snap.get("dailyBar") or {}
                prev = snap.get("prevDailyBar") or {}
                close = float(daily.get("c", 0) or 0)
                prev_close = float(prev.get("c", 0) or 0)
                if prev_close <= 0 or close <= 0:
                    continue
                change_pct = ((close - prev_close) / prev_close) * 100
                results.append({
                    "symbol": sym,
                    "change_pct": change_pct,
                    "price": close,
                    "volume": float(daily.get("v", 0) or 0),
                })
            if i + 50 < len(symbols):
                time.sleep(0.2)
        results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        return results[:top_n]

    def level2(self, symbol: str) -> Optional[dict]:
        """Fetch Level 2 order book depth.

        For crypto pairs (contains '/'), uses Alpaca crypto orderbook.
        For equities, returns None (Alpaca free tier doesn't offer equity L2).
        """
        sym = symbol.strip().upper()
        if "/" in sym or sym in ("BTC", "ETH", "SOL", "DOGE", "LTC", "BCH", "AVAX", "LINK"):
            ob = self.crypto_orderbook(sym)
            if not ob:
                return None
            bids = ob["bids"]
            asks = ob["asks"]
            total_depth_dollars = sum(b["p"] * b["s"] for b in bids) + sum(a["p"] * a["s"] for a in asks)
            best_bid = bids[0]["p"] if bids else 0
            best_ask = asks[0]["p"] if asks else 0
            return {
                "symbol": sym,
                "bids": bids,
                "asks": asks,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0,
                "total_depth_dollars": total_depth_dollars,
                "bid_depth_dollars": sum(b["p"] * b["s"] for b in bids),
                "ask_depth_dollars": sum(a["p"] * a["s"] for a in asks),
            }
        return None  # Equity L2 not available on Alpaca free tier


# ── Module-level singleton ────────────────────────────────────────

_provider_instance: Optional[AlpacaRealtimeProvider] = None


def get_alpaca_provider() -> AlpacaRealtimeProvider:
    """Return a shared AlpacaRealtimeProvider singleton."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = AlpacaRealtimeProvider()
    return _provider_instance
