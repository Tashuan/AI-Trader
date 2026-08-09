"""Equity historical data providers for backtesting.

Alpaca offers free historical OHLCV bars for US equities and ETFs going
back to 2016 — far beyond yfinance's 60-day intraday cap. Requires a free
Alpaca account (paper trading) and API keys.

All providers implement the same `history(symbol, *, period, interval, **kwargs)`
contract as YFinanceProvider, returning a pandas DataFrame with a
UTC DatetimeIndex and capitalized OHLCV columns so backtesters can
drop them in as a direct replacement.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_ALPACA_BARS_URL = os.environ.get(
    "ALPACA_BARS_URL", "https://data.alpaca.markets/v2/stocks"
)
_ALPACA_MAX_BARS = 10_000  # Alpaca page limit per request

# Map yfinance interval strings to Alpaca timeframe strings.
_INTERVAL_MAP = {
    "1m": "1Min",
    "2m": "2Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "60m": "1Hour",
    "1h": "1Hour",
    "1d": "1Day",
}

_PERIOD_DAYS = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650,
    "max": 3650,
}


def _resolve_time_range(period, start, end):
    """Resolve period/start/end kwargs into (start_iso, end_iso) RFC3339 strings."""
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


def _build_dataframe(bars: list[dict]):
    """Build a yfinance-shaped DataFrame from Alpaca bar dicts."""
    import pandas as pd
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df["Datetime"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df = df.set_index("Datetime")
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.index.name = "Datetime"
    return df.sort_index()


class AlpacaProvider:
    """Fetches equity OHLCV history from Alpaca's market data API.

    Free tier (Basic plan): data since 2016, IEX feed, 200 calls/min.
    Requires APCA_API_KEY_ID and APCA_API_SECRET_KEY environment variables.
    """

    def __init__(self):
        self._key_id = os.environ.get("APCA_API_KEY_ID", "")
        self._secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not self._key_id or not self._secret:
            logger.warning(
                "AlpacaProvider: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set. "
                "Sign up at alpaca.markets (free) and add to .env."
            )

    @property
    def available(self) -> bool:
        return bool(self._key_id and self._secret)

    def history(self, symbol: str, *, period: Optional[str] = "1mo", interval: str = "1d", **kwargs):
        if not self.available:
            raise RuntimeError("AlpacaProvider: API keys not configured")

        # Strip yfinance-style suffixes (equities don't need them, but be safe)
        sym = symbol.strip().upper().replace("-USD", "").replace("=F", "").replace("^", "")
        tf = _INTERVAL_MAP.get(interval)
        if tf is None:
            logger.warning("AlpacaProvider: unsupported interval %s, defaulting to 1Day", interval)
            tf = "1Day"

        start_iso, end_iso = _resolve_time_range(period, kwargs.get("start"), kwargs.get("end"))
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
        }

        all_bars: list[dict] = []
        page_token = None
        url = f"{_ALPACA_BARS_URL}/{sym}/bars"

        while True:
            params = {
                "timeframe": tf,
                "start": start_iso,
                "end": end_iso,
                "limit": _ALPACA_MAX_BARS,
                "feed": "iex",
            }
            if page_token:
                params["page_token"] = page_token
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as exc:
                logger.warning("AlpacaProvider: fetch failed for %s: %s", sym, exc)
                return _build_dataframe(all_bars) if all_bars else None

            data = resp.json()
            all_bars.extend(data.get("bars", []))
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(0.1)  # gentle on rate limit

        df = _build_dataframe(all_bars)
        if df is None or df.empty:
            logger.warning("AlpacaProvider: no bars returned for %s (%s → %s)", sym, start_iso, end_iso)
            return None
        logger.info("AlpacaProvider: %s %s → %d bars (%s to %s)",
                     sym, interval, len(df), df.index[0], df.index[-1])
        return df
