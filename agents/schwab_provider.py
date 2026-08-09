"""
schwab_provider.py — Schwab API market data provider.

Implements the MarketDataProvider protocol (same interface as YFinanceProvider)
so the scalp scan and backtester can use Schwab real-time data transparently.

Endpoints used:
  GET /marketdata/v1/quotes       — real-time quotes (bid/ask/spread/volume)
  GET /marketdata/v1/movers/{id}  — top gainers/losers/actives by index
  GET /marketdata/v1/{symbol}/pricehistory — historical OHLCV candles
  GET /marketdata/v1/level2/quotes/{symbol} — Level 2 order book depth
  GET /accounts/v1/accountPositions — account positions (Phase 3: live trading)

The provider degrades gracefully: if Schwab credentials are not configured,
methods return None / empty results so the caller can fall back to yfinance.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Any, Optional
from datetime import datetime, timezone

import pandas as pd

from schwab_auth import SchwabOAuth, from_env as _auth_from_env

logger = logging.getLogger("SchwabProvider")

_BASE_URL = "https://api.schwabapi.com"

# Interval mapping: our interval strings → Schwab frequency params
_INTERVAL_MAP = {
    "1m": {"frequency": 1, "period": "minute"},
    "5m": {"frequency": 5, "period": "minute"},
    "15m": {"frequency": 15, "period": "minute"},
    "30m": {"frequency": 30, "period": "minute"},
    "1h": {"frequency": 60, "period": "minute"},
    "1d": {"frequency": 1, "period": "daily"},
    "1w": {"frequency": 1, "period": "weekly"},
}

# Period mapping: our period strings → Schwab periodType
_PERIOD_MAP = {
    "1d": "day",
    "5d": "day",
    "1mo": "month",
    "3mo": "month",
    "6mo": "month",
    "1y": "year",
    "2y": "year",
    "5y": "year",
    "ytd": "ytd",
}

# Default equity universe for the scanner (broad sweep)
DEFAULT_SCANNER_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS",
    "BABA", "JD", "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL",
    "F", "GM", "NIO", "XPEV", "PLUG", "FCEL", "DKNG", "PENN",
]


class SchwabProvider:
    """Schwab API market data provider implementing the MarketDataProvider protocol."""

    def __init__(self, auth: Optional[SchwabOAuth] = None):
        self._auth = auth or _auth_from_env()
        if self._auth and self._auth.is_configured:
            logger.info("SchwabProvider initialized with valid credentials")
        else:
            logger.warning("SchwabProvider initialized without credentials — will return empty data")

    @property
    def is_configured(self) -> bool:
        return self._auth is not None and self._auth.is_configured

    # ── HTTP helper ───────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        """GET request to Schwab API with auth header. Returns parsed JSON or None."""
        if not self.is_configured:
            return None
        token = self._auth.get_access_token()
        if not token:
            logger.error("No valid access token")
            return None

        url = f"{_BASE_URL}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query:
                url = f"{url}?{query}"

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            logger.error(f"Schwab API {path} failed: HTTP {e.code} — {e.read()[:200]}")
            return None
        except Exception as e:
            logger.error(f"Schwab API {path} failed: {e}")
            return None

    # ── MarketDataProvider protocol ───────────────────────────

    def history(self, symbol: str, *, period: str = "1mo",
                interval: str = "1d", **kwargs) -> pd.DataFrame:
        """Fetch historical OHLCV candles.

        Returns a yfinance-shaped DataFrame (Open, High, Low, Close, Volume)
        with a DatetimeIndex so it's a drop-in replacement for yfinance.
        """
        if not self.is_configured:
            return pd.DataFrame()

        # Handle start/end date kwargs (used by backtester)
        start = kwargs.get("start")
        end = kwargs.get("end")
        if start or end:
            return self._history_by_date(symbol, start, end, interval)

        freq = _INTERVAL_MAP.get(interval)
        if not freq:
            logger.warning(f"Unknown interval: {interval}")
            return pd.DataFrame()

        period_type = _PERIOD_MAP.get(period, "month")
        params = {
            "periodType": period_type,
            "period": _period_value(period, period_type),
            "frequencyType": freq["period"],
            "frequency": freq["frequency"],
            "needExtendedHoursData": "false",
        }

        data = self._get(f"/marketdata/v1/{symbol}/pricehistory", params)
        if not data or "candles" not in data:
            return pd.DataFrame()

        candles = data["candles"]
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        # Schwab uses millis timestamps
        df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df.index = df.index.tz_convert("US/Eastern")
        df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        }, inplace=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        return df

    def _history_by_date(self, symbol: str, start: Any, end: Any,
                         interval: str) -> pd.DataFrame:
        """Fetch history using start/end dates (for backtester)."""
        freq = _INTERVAL_MAP.get(interval, _INTERVAL_MAP["1d"])
        params = {
            "periodType": "year",
            "frequencyType": freq["period"],
            "frequency": freq["frequency"],
            "startDate": _to_millis(start),
            "endDate": _to_millis(end),
            "needExtendedHoursData": "false",
        }
        data = self._get(f"/marketdata/v1/{symbol}/pricehistory", params)
        if not data or "candles" not in data:
            return pd.DataFrame()
        df = pd.DataFrame(data["candles"])
        df.index = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df.index = df.index.tz_convert("US/Eastern")
        df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        }, inplace=True)
        return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)

    def quote(self, symbol: str) -> Optional[dict]:
        """Fetch real-time quote for a symbol.

        Returns {bid, ask, bid_size, ask_size, spread, last, volume, ...}
        or None if unavailable.
        """
        data = self._get(f"/marketdata/v1/{symbol}/quotes")
        if not data:
            return None
        # API returns a dict keyed by symbol
        q = data.get(symbol, data) if isinstance(data, dict) else None
        if not q:
            return None
        bid = float(q.get("quote", {}).get("bidPrice", 0) or 0)
        ask = float(q.get("quote", {}).get("askPrice", 0) or 0)
        last = float(q.get("quote", {}).get("lastPrice", 0) or 0)
        bid_size = float(q.get("quote", {}).get("bidSize", 0) or 0)
        ask_size = float(q.get("quote", {}).get("askSize", 0) or 0)
        total_volume = float(q.get("quote", {}).get("totalVolume", 0) or 0)
        spread = ask - bid if bid > 0 and ask > 0 else 0.0
        return {
            "symbol": symbol,
            "bid": bid, "ask": ask, "last": last,
            "bid_size": bid_size, "ask_size": ask_size,
            "spread": spread,
            "spread_pct": (spread / last * 100) if last > 0 else 0.0,
            "total_volume": total_volume,
        }

    def quotes_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Batch-fetch quotes for multiple symbols. Returns {symbol: quote_dict}."""
        if not symbols:
            return {}
        # Schwab quotes endpoint supports comma-separated symbols
        joined = ",".join(symbols)
        data = self._get(f"/marketdata/v1/{joined}/quotes")
        if not data:
            return {}
        result = {}
        for sym in symbols:
            q = data.get(sym, {})
            if not q:
                continue
            quote_data = q.get("quote", q)
            bid = float(quote_data.get("bidPrice", 0) or 0)
            ask = float(quote_data.get("askPrice", 0) or 0)
            last = float(quote_data.get("lastPrice", 0) or 0)
            spread = ask - bid if bid > 0 and ask > 0 else 0.0
            result[sym] = {
                "symbol": sym,
                "bid": bid, "ask": ask, "last": last,
                "spread": spread,
                "spread_pct": (spread / last * 100) if last > 0 else 0.0,
                "total_volume": float(quote_data.get("totalVolume", 0) or 0),
            }
        return result

    # ── Movers ────────────────────────────────────────────────

    def movers(self, index_id: str = "$COMPX", direction: str = "up") -> list[dict]:
        """Fetch top movers for an index.

        index_id: $COMPX (Nasdaq), $DJI (Dow), $SPX (S&P 500)
        direction: 'up', 'down', or 'active'
        """
        # Schwab uses direction param
        params = {"direction": direction}
        data = self._get(f"/marketdata/v1/movers/{index_id}", params)
        if not data or "movers" not in data:
            return []
        result = []
        for m in data["movers"]:
            result.append({
                "symbol": m.get("symbol", ""),
                "change_pct": float(m.get("change", 0) or 0),
                "last": float(m.get("last", 0) or 0),
                "total_volume": float(m.get("totalVolume", 0) or 0),
                "direction": direction,
            })
        return result

    def movers_all(self) -> list[dict]:
        """Fetch movers from all configured indices (up + down)."""
        indices = ["$COMPX", "$DJI", "$SPX"]
        all_movers = []
        for idx in indices:
            for direction in ("up", "down"):
                movers = self.movers(idx, direction)
                all_movers.extend(movers)
        # Deduplicate by symbol (keep highest abs change_pct)
        seen: dict[str, dict] = {}
        for m in all_movers:
            sym = m["symbol"]
            if sym and (sym not in seen or abs(m["change_pct"]) > abs(seen[sym]["change_pct"])):
                seen[sym] = m
        return list(seen.values())

    # ── Level 2 ───────────────────────────────────────────────

    def level2(self, symbol: str) -> Optional[dict]:
        """Fetch Level 2 order book depth for a symbol.

        Returns {bids: [{price, size}], asks: [{price, size}], spread, depth}
        or None if unavailable (may require Level 2 subscription).
        """
        data = self._get(f"/marketdata/v1/level2/quotes/{symbol}")
        if not data:
            return None
        book = data.get(symbol, data) if isinstance(data, dict) else None
        if not book:
            return None
        bids = [
            {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
            for b in book.get("bids", [])[:10]
        ]
        asks = [
            {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
            for a in book.get("asks", [])[:10]
        ]
        bid_depth = sum(b["price"] * b["size"] for b in bids[:3])
        ask_depth = sum(a["price"] * a["size"] for a in asks[:3])
        spread = (asks[0]["price"] - bids[0]["price"]) if bids and asks else 0.0
        return {
            "symbol": symbol,
            "bids": bids, "asks": asks,
            "spread": spread,
            "bid_depth_dollars": bid_depth,
            "ask_depth_dollars": ask_depth,
            "total_depth_dollars": bid_depth + ask_depth,
        }

    # ── Account (Phase 3: live trading) ───────────────────────

    def account_positions(self) -> list[dict]:
        """Fetch account positions (for live trading Phase 3)."""
        data = self._get("/accounts/v1/accountPositions")
        if not data:
            return []
        return data if isinstance(data, list) else []


# ── Helpers ────────────────────────────────────────────────────

def _period_value(period: str, period_type: str) -> int:
    """Map our period string to Schwab's period integer."""
    mapping = {
        "day": {"1d": 1, "5d": 5},
        "month": {"1mo": 1, "3mo": 3, "6mo": 6},
        "year": {"1y": 1, "2y": 2, "5y": 5},
        "ytd": {"ytd": 1},
    }
    return mapping.get(period_type, {}).get(period, 1)


def _to_millis(date_val: Any) -> Optional[int]:
    """Convert a date string/datetime to milliseconds since epoch."""
    if date_val is None:
        return None
    if isinstance(date_val, (int, float)):
        return int(date_val)
    if isinstance(date_val, str):
        dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
    else:
        dt = date_val
    return int(dt.timestamp() * 1000)


# ── Singleton accessor ─────────────────────────────────────────

_provider_instance: Optional[SchwabProvider] = None


def get_schwab_provider() -> SchwabProvider:
    """Get or create the singleton SchwabProvider instance."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = SchwabProvider()
    return _provider_instance
