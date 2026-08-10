"""Massive.com market data provider.

Implements the MarketDataProvider protocol (same interface as
YFinanceProvider / SchwabProvider / AlpacaProvider) so the scalp scan,
backtester, and server price fetcher can use Massive real-time data
transparently.

Massive offers:
  - Real-time stock snapshots, NBBO quotes, last trades
  - Historical OHLC aggregates at any timespan (second/minute/hour/day)
  - Tick-level historical trades and quotes (nanosecond timestamps)
  - Built-in technical indicators (SMA, EMA, RSI, MACD)
  - Crypto trades and snapshots
  - Market movers (top gainers/losers)

Requires MASSIVE_API_KEY environment variable. Sign up at
https://massive.com/dashboard/signup and get your key at
https://massive.com/dashboard/keys.

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

_BASE_URL = os.environ.get("MASSIVE_API_BASE", "https://api.massive.com")
_API_KEY = os.environ.get("MASSIVE_API_KEY", "")
_REQUEST_TIMEOUT = 30
_MAX_RETRIES = 2
_RETRY_DELAY = 0.5

# Map our interval strings to (multiplier, timespan) for Massive aggs endpoint.
_INTERVAL_MAP: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"),
    "2m": (2, "minute"),
    "5m": (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "60m": (60, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"),
    "1w": (1, "week"),
    "1mo": (1, "month"),
}

_PERIOD_DAYS: dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
    "max": 3650,
}


def _resolve_date_range(period: Optional[str], start: Optional[str],
                        end: Optional[str]) -> tuple[str, str]:
    """Resolve period/start/end into (from_date, to_date) YYYY-MM-DD strings."""
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
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def _strip_symbol(symbol: str) -> str:
    """Strip yfinance-style suffixes for Massive API calls."""
    return symbol.strip().upper().replace("-USD", "").replace("=F", "").replace("^", "")


def _build_ohlcv_df(results: list[dict]) -> "pd.DataFrame":
    """Build a yfinance-shaped DataFrame from Massive aggregate results."""
    import pandas as pd
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    df["Datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    rename = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
    df = df.rename(columns=rename)
    cols = ["Open", "High", "Low", "Close", "Volume"]
    for c in cols:
        if c not in df.columns:
            df[c] = float("nan")
    df = df.set_index("Datetime")
    df = df[cols].astype(float)
    df.index.name = "Datetime"
    return df.sort_index()


def _build_trades_df(results: list[dict]) -> "pd.DataFrame":
    """Build a DataFrame from Massive tick trade results (nanosecond timestamps)."""
    import pandas as pd
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    ts_col = "participant_timestamp" if "participant_timestamp" in df.columns else "sip_timestamp"
    df["Datetime"] = pd.to_datetime(df[ts_col], unit="ns", utc=True)
    rename = {"price": "Price", "size": "Size", "exchange": "Exchange",
              "conditions": "Conditions", "id": "TradeId"}
    df = df.rename(columns=rename)
    keep = ["Datetime", "Price", "Size", "Exchange", "Conditions", "TradeId"]
    keep = [c for c in keep if c in df.columns]
    df = df[keep]
    if "Price" in df.columns:
        df["Price"] = df["Price"].astype(float)
    if "Size" in df.columns:
        df["Size"] = df["Size"].astype(float)
    return df.sort_values("Datetime").reset_index(drop=True)


def _build_quotes_df(results: list[dict]) -> "pd.DataFrame":
    """Build a DataFrame from Massive NBBO quote results (nanosecond timestamps)."""
    import pandas as pd
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    ts_col = "sip_timestamp" if "sip_timestamp" in df.columns else "timestamp"
    df["Datetime"] = pd.to_datetime(df[ts_col], unit="ns", utc=True)
    # Massive uses uppercase for bid, lowercase for ask: P=bid_price, p=ask_price, S=bid_size, s=ask_size
    rename = {"P": "BidPrice", "p": "AskPrice", "S": "BidSize", "s": "AskSize",
              "X": "BidExchange", "x": "AskExchange"}
    df = df.rename(columns=rename)
    for c in ("BidPrice", "AskPrice", "BidSize", "AskSize"):
        if c in df.columns:
            df[c] = df[c].astype(float)
    if "BidPrice" in df.columns and "AskPrice" in df.columns:
        df["Mid"] = (df["BidPrice"] + df["AskPrice"]) / 2.0
        df["Spread"] = df["AskPrice"] - df["BidPrice"]
    keep = ["Datetime", "BidPrice", "AskPrice", "BidSize", "AskSize", "Mid", "Spread"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].sort_values("Datetime").reset_index(drop=True)


class MassiveProvider:
    """Massive.com market data provider implementing the MarketDataProvider protocol.

    Provides real-time and historical data for US equities, crypto, forex,
    futures, and indices via Massive's REST API. Requires MASSIVE_API_KEY.

    Not all plans include all endpoints. The provider probes once on first
    use and caches which endpoint categories are authorized, skipping 403'd
    categories silently instead of retrying on every call.
    """

    # Endpoint categories that may be plan-gated
    _CAT_REALTIME = "realtime"      # last trade, last NBBO, snapshot, movers
    _CAT_TICK = "tick"              # tick-level trades/quotes (v3)
    _CAT_HISTORY = "history"        # aggregates, prev day, indicators

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or _API_KEY
        self._base_url = _BASE_URL
        self._capabilities: Optional[dict[str, bool]] = None
        if not self._api_key:
            logger.warning("MassiveProvider: MASSIVE_API_KEY not set. "
                           "Sign up at https://massive.com/dashboard/signup "
                           "and add to .env.")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ── Capability probe ─────────────────────────────────────────

    def _probe_capabilities(self):
        """Probe which endpoint categories are authorized by the current plan.

        Called once on first use. Caches results so subsequent 403'd calls
        are skipped without hitting the API.
        """
        if self._capabilities is not None:
            return
        caps = {self._CAT_HISTORY: True, self._CAT_REALTIME: True, self._CAT_TICK: True}
        if not self._api_key:
            self._capabilities = {k: False for k in caps}
            return
        headers = {"Authorization": f"Bearer {self._api_key}"}
        probes = [
            (self._CAT_HISTORY, "/v2/aggs/ticker/AAPL/prev"),
            (self._CAT_REALTIME, "/v2/last/trade/AAPL"),
            (self._CAT_TICK, "/v3/trades/AAPL?limit=1"),
        ]
        for cat, path in probes:
            try:
                resp = requests.get(f"{self._base_url}{path}", headers=headers,
                                    timeout=10)
                caps[cat] = resp.status_code == 200
                if not caps[cat]:
                    logger.info("MassiveProvider: %s endpoints NOT authorized (HTTP %d) — "
                                "these will be skipped", cat, resp.status_code)
            except requests.RequestException:
                caps[cat] = False
        self._capabilities = caps
        available_cats = [k for k, v in caps.items() if v]
        logger.info("MassiveProvider: plan capabilities = %s", available_cats)

    def _can(self, cat: str) -> bool:
        """Check if an endpoint category is authorized."""
        self._probe_capabilities()
        return self._capabilities.get(cat, False)

    # ── HTTP helper ──────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None,
             category: str = _CAT_HISTORY) -> Optional[dict]:
        """GET request to Massive API with retry and error handling.

        Args:
            category: Endpoint category for capability checking. If the
                      plan doesn't include this category, returns None
                      immediately without hitting the API.
        """
        if not self._api_key:
            return None
        if not self._can(category):
            return None
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=headers, params=params,
                                    timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    wait = 1.0 * (attempt + 1)
                    logger.warning("MassiveProvider: rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    # Mark this category as unavailable for future calls
                    if self._capabilities is not None:
                        self._capabilities[category] = False
                    logger.debug("MassiveProvider: %s not authorized (403), skipping", category)
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                logger.warning("MassiveProvider: GET %s failed (attempt %d): %s",
                               path, attempt + 1, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
        return None

    # ── MarketDataProvider protocol ──────────────────────────────

    def history(self, symbol: str, *, period: Optional[str] = "1mo",
                interval: str = "1d", **kwargs) -> Optional["pd.DataFrame"]:
        """Fetch historical OHLCV bars from Massive aggregates endpoint."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        ts = _INTERVAL_MAP.get(interval)
        if ts is None:
            logger.warning("MassiveProvider: unsupported interval %s, defaulting to 1d", interval)
            ts = (1, "day")
        multiplier, timespan = ts
        from_date, to_date = _resolve_date_range(period, kwargs.get("start"), kwargs.get("end"))
        adjusted = kwargs.get("adjusted", True)
        path = f"/v2/aggs/ticker/{sym}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        params: dict[str, Any] = {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000}
        all_results: list[dict] = []
        while True:
            data = self._get(path, params)
            if data is None:
                break
            all_results.extend(data.get("results", []) or [])
            next_url = data.get("next_url")
            if not next_url:
                break
            # next_url is a full URL — extract the path for our _get helper
            if next_url.startswith(self._base_url):
                path = next_url[len(self._base_url):]
            else:
                # Follow the full URL directly
                try:
                    resp = requests.get(next_url, headers={"Authorization": f"Bearer {self._api_key}"},
                                        timeout=_REQUEST_TIMEOUT)
                    resp.raise_for_status()
                    data = resp.json()
                    all_results.extend(data.get("results", []) or [])
                    next_url = data.get("next_url")
                    if not next_url:
                        break
                    continue
                except requests.RequestException:
                    break
            # Reset params — pagination token is embedded in next_url path
            params = {}
        df = _build_ohlcv_df(all_results)
        if df.empty:
            logger.warning("MassiveProvider: no bars for %s %s (%s → %s)",
                           sym, interval, from_date, to_date)
            return None
        logger.info("MassiveProvider: %s %s → %d bars (%s to %s)",
                     sym, interval, len(df), df.index[0], df.index[-1])
        return df

    def quote(self, symbol: str) -> Optional[dict]:
        """Fetch real-time NBBO quote for a symbol.

        Returns {symbol, bid, ask, spread, spread_pct, last, bid_size, ask_size}
        or None if unavailable.
        """
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        # Try last NBBO first
        data = self._get(f"/v2/last/nbbo/{sym}", category=self._CAT_REALTIME)
        if data and data.get("results"):
            q = data["results"]
            if isinstance(q, list):
                q = q[0] if q else {}
            bid = float(q.get("bid_price", q.get("P", 0)) or 0)
            ask = float(q.get("ask_price", q.get("p", 0)) or 0)
            bid_size = float(q.get("bid_size", q.get("S", 0)) or 0)
            ask_size = float(q.get("ask_size", q.get("s", 0)) or 0)
            spread = ask - bid if bid > 0 and ask > 0 else 0.0
            last = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            # Try to get last trade price for 'last'
            trade = self._get(f"/v2/last/trade/{sym}", category=self._CAT_REALTIME)
            if trade and trade.get("results"):
                t = trade["results"]
                if isinstance(t, list):
                    t = t[0] if t else {}
                last = float(t.get("price", t.get("p", 0)) or last)
            return {
                "symbol": sym,
                "bid": bid, "ask": ask, "last": last,
                "bid_size": bid_size, "ask_size": ask_size,
                "spread": spread,
                "spread_pct": (spread / last * 100) if last > 0 else 0.0,
            }
        return None

    # ── Extended methods (beyond the protocol) ───────────────────

    def snapshot(self, symbol: str) -> Optional[dict]:
        """Fetch full single-ticker snapshot (day stats + last trade + last quote)."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        return self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{sym}",
                         category=self._CAT_REALTIME)

    def last_trade(self, symbol: str) -> Optional[dict]:
        """Fetch the most recent trade for a symbol."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        data = self._get(f"/v2/last/trade/{sym}", category=self._CAT_REALTIME)
        if data and data.get("results"):
            t = data["results"]
            if isinstance(t, list):
                t = t[0] if t else {}
            return {
                "symbol": sym,
                "price": float(t.get("price", t.get("p", 0)) or 0),
                "size": float(t.get("size", t.get("s", 0)) or 0),
                "exchange": t.get("exchange", t.get("x", "")),
                "timestamp": t.get("sip_timestamp", t.get("t", "")),
            }
        return None

    def trades(self, symbol: str, *, date: Optional[str] = None,
               limit: int = 50000) -> Optional["pd.DataFrame"]:
        """Fetch tick-level historical trades for a symbol.

        Args:
            symbol: Stock ticker (e.g. "AAPL").
            date: Optional date filter (YYYY-MM-DD or nanosecond timestamp).
            limit: Max trades to return (API max 50000).
        """
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        params: dict[str, Any] = {"limit": min(limit, 50000), "sort": "asc"}
        if date:
            params["timestamp.gte"] = date
        data = self._get(f"/v3/trades/{sym}", params, category=self._CAT_TICK)
        if data is None:
            return None
        return _build_trades_df(data.get("results", []))

    def quotes(self, symbol: str, *, date: Optional[str] = None,
               limit: int = 50000) -> Optional["pd.DataFrame"]:
        """Fetch tick-level historical NBBO quotes for a symbol."""
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        params: dict[str, Any] = {"limit": min(limit, 50000), "sort": "asc"}
        if date:
            params["timestamp.gte"] = date
        data = self._get(f"/v3/quotes/{sym}", params, category=self._CAT_TICK)
        if data is None:
            return None
        return _build_quotes_df(data.get("results", []))

    def movers(self, direction: str = "gainers") -> list[dict]:
        """Fetch top 20 market movers (gainers or losers)."""
        if not self.available:
            return []
        direction = direction.lower()
        if direction not in ("gainers", "losers"):
            direction = "gainers"
        data = self._get(f"/v2/snapshot/locale/us/markets/stocks/{direction}",
                         category=self._CAT_REALTIME)
        if data is None:
            return []
        return data.get("tickers", [])

    def market_status(self) -> Optional[dict]:
        """Fetch current market status (open/closed, session times)."""
        if not self.available:
            return None
        return self._get("/v1/marketstatus/now")

    def crypto_last_trade(self, from_sym: str, to_sym: str = "USD") -> Optional[dict]:
        """Fetch last crypto trade for a pair (e.g. BTC/USD)."""
        if not self.available:
            return None
        data = self._get(f"/v1/last/crypto/{from_sym.upper()}/{to_sym.upper()}",
                         category=self._CAT_REALTIME)
        if data and data.get("results"):
            t = data["results"]
            if isinstance(t, list):
                t = t[0] if t else {}
            return {
                "from": from_sym.upper(), "to": to_sym.upper(),
                "price": float(t.get("price", t.get("p", 0)) or 0),
                "size": float(t.get("size", t.get("s", 0)) or 0),
                "timestamp": t.get("last_trade", {}).get("t", t.get("t", "")),
            }
        return None

    def crypto_snapshot(self, ticker: str) -> Optional[dict]:
        """Fetch crypto snapshot for a pair (e.g. 'X:BTCUSD')."""
        if not self.available:
            return None
        return self._get(f"/v2/snapshot/locale/global/markets/crypto/tickers/{ticker}",
                         category=self._CAT_REALTIME)

    def indicators(self, symbol: str, indicator: str = "rsi", *,
                   timespan: str = "minute", window: int = 14,
                   limit: int = 5000) -> Optional["pd.DataFrame"]:
        """Fetch a built-in technical indicator (rsi, ema, sma, macd).

        Returns a DataFrame with a DatetimeIndex and a value column.
        """
        if not self.available:
            return None
        sym = _strip_symbol(symbol)
        valid = {"rsi", "ema", "sma", "macd"}
        if indicator not in valid:
            logger.warning("MassiveProvider: unsupported indicator %s", indicator)
            return None
        params: dict[str, Any] = {
            "timespan": timespan, "window": window,
            "series_type": "close", "limit": limit, "order": "asc",
        }
        data = self._get(f"/v1/indicators/{indicator}/{sym}", params)
        if data is None:
            return None
        import pandas as pd
        results = data.get("results", {}).get("values", [])
        if not results:
            return None
        df = pd.DataFrame(results)
        df["Datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("Datetime")
        df.index.name = "Datetime"
        val_col = indicator if indicator in df.columns else df.columns[0]
        df[val_col] = df[val_col].astype(float)
        return df[[val_col]]


# ── Module-level singleton (like get_schwab_provider) ─────────────

_provider_instance: Optional[MassiveProvider] = None


def get_massive_provider() -> MassiveProvider:
    """Return a shared MassiveProvider singleton."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = MassiveProvider()
    return _provider_instance
