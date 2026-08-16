"""Market data fetch helpers for StockBoy detectors.

Wraps Alpaca market-data and trading endpoints plus Finnhub earnings
calendar calls. Every function is fault-tolerant: on any API error it
returns ``None`` (or an empty list) so detectors can degrade to a
no-action state instead of crashing the supervisor loop.

Results are cached in-process with TTLs appropriate to each feed:
  quotes      5s
  bars       60s
  earnings  300s
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

logger = logging.getLogger("StockBoy.MarketData")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[StockBoy.MD] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False

# ── Endpoints / credentials ────────────────────────────────────────

_ALPACA_DATA_URL = os.environ.get(
    "ALPACA_BARS_URL", "https://data.alpaca.markets/v2/stocks"
).rsplit("/v2/stocks", 1)[0] + "/v2/stocks"

_ALPACA_TRADING_URL = os.environ.get(
    "ALPACA_PAPER_TRADING_URL", "https://paper-api.alpaca.markets/v2"
)

_API_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
_SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
_FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

_TIMEOUT = 15

# ── In-process TTL cache ───────────────────────────────────────────

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float):
    """Return cached value if fresh, else None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        return None
    return value


def _store(key: str, value: object) -> None:
    _cache[key] = (time.time(), value)


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": _API_KEY,
        "APCA-API-SECRET-KEY": _SECRET_KEY,
    }


def _clean_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("-USD", "").replace("=F", "").replace("^", "")


# ── Bars ───────────────────────────────────────────────────────────

def fetch_recent_bars(symbol: str, interval: str = "5Min", bars_back: int = 78) -> pd.DataFrame | None:
    """Fetch recent OHLCV bars.

    Returns a DataFrame with a UTC DatetimeIndex and capitalized
    Open/High/Low/Close/Volume columns, or ``None`` on failure.
    """
    sym = _clean_symbol(symbol)
    cache_key = f"bars:{sym}:{interval}:{bars_back}"
    cached = _cached(cache_key, 60.0)
    if cached is not None:
        return cached
    if not _API_KEY or not _SECRET_KEY:
        logger.warning("fetch_recent_bars: Alpaca keys not configured")
        return None
    end = datetime.now(timezone.utc)
    # 5m bars → ~6.5h/session; pad generously for multi-day lookback.
    minutes = bars_back * {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60}.get(interval, 5)
    start = end - timedelta(minutes=minutes * 2 + 120)
    url = f"{_ALPACA_DATA_URL}/{sym}/bars"
    params = {
        "timeframe": interval, "start": start.isoformat(), "end": end.isoformat(),
        "limit": bars_back, "feed": "iex",
    }
    try:
        resp = requests.get(url, headers=_alpaca_headers(), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        bars = resp.json().get("bars", [])
    except Exception as exc:
        logger.warning("fetch_recent_bars(%s) failed: %s", sym, exc)
        return None
    if not bars:
        return None
    df = pd.DataFrame(bars)
    df["Datetime"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    df = df.set_index("Datetime")[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df = df.sort_index()
    _store(cache_key, df)
    return df


# ── Latest quote ───────────────────────────────────────────────────

def fetch_latest_quote(symbol: str) -> dict | None:
    """Fetch the latest bid/ask quote.

    Returns ``{'bid', 'ask', 'spread', 'spread_pct', 'mid'}`` or ``None``.
    """
    sym = _clean_symbol(symbol)
    cache_key = f"quote:{sym}"
    cached = _cached(cache_key, 5.0)
    if cached is not None:
        return cached
    if not _API_KEY or not _SECRET_KEY:
        logger.warning("fetch_latest_quote: Alpaca keys not configured")
        return None
    url = f"{_ALPACA_DATA_URL}/{sym}/quotes/latest"
    try:
        resp = requests.get(url, headers=_alpaca_headers(), params={"feed": "iex"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning("fetch_latest_quote(%s) failed: %s", sym, exc)
        return None
    quote = payload.get("quote") or payload
    bid = float(quote.get("bp") or quote.get("bid") or 0)
    ask = float(quote.get("ap") or quote.get("ask") or 0)
    if bid <= 0 or ask <= 0:
        return None
    spread = ask - bid
    mid = (bid + ask) / 2
    result = {
        "bid": bid, "ask": ask, "spread": spread,
        "spread_pct": (spread / mid * 100) if mid else 0.0, "mid": mid,
    }
    _store(cache_key, result)
    return result


# ── SPY pre-market gap ─────────────────────────────────────────────

def fetch_premarket_gap(symbol: str = "SPY") -> float | None:
    """Return the pre-market gap % from the prior session close.

    Uses the latest quote vs. the previous daily close. Positive = gap up.
    """
    sym = _clean_symbol(symbol)
    cache_key = f"gap:{sym}"
    cached = _cached(cache_key, 60.0)
    if cached is not None:
        return cached
    quote = fetch_latest_quote(sym)
    if not quote:
        return None
    bars = fetch_recent_bars(sym, interval="1Day", bars_back=5)
    if bars is None or bars.empty:
        return None
    prior_close = float(bars["Close"].iloc[-2]) if len(bars) >= 2 else float(bars["Close"].iloc[-1])
    if prior_close <= 0:
        return None
    gap_pct = (quote["mid"] - prior_close) / prior_close * 100
    _store(cache_key, gap_pct)
    return gap_pct


# ── SPY ATR ────────────────────────────────────────────────────────

def fetch_spy_atr(period: int = 20) -> float | None:
    """Return SPY 20-day ATR as a percentage of price (e.g. 1.8 = 1.8%)."""
    cache_key = f"atr:SPY:{period}"
    cached = _cached(cache_key, 300.0)
    if cached is not None:
        return cached
    bars = fetch_recent_bars("SPY", interval="1Day", bars_back=period + 10)
    if bars is None or len(bars) < period + 1:
        return None
    high = bars["High"]; low = bars["Low"]; close = bars["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.iloc[-period:].mean()
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return None
    atr_pct = float(atr) / last_close * 100
    _store(cache_key, atr_pct)
    return atr_pct


# ── VIX ────────────────────────────────────────────────────────────

def fetch_vix() -> float | None:
    """Return the current VIX value, or ``None`` if unavailable.

    Alpaca's IEX feed does not carry the CBOE VIX index, so this attempts
    a lightweight fallback via the SPY daily range approximation. Detectors
    must treat ``None`` as "skip the VIX check".
    """
    cache_key = "vix"
    cached = _cached(cache_key, 60.0)
    if cached is not None:
        return cached
    # Alpaca IEX cannot serve ^VIX; approximate from recent SPY daily range.
    bars = fetch_recent_bars("SPY", interval="1Day", bars_back=5)
    if bars is None or bars.empty:
        return None
    last = bars.iloc[-1]
    prior_close = float(bars["Close"].iloc[-2]) if len(bars) >= 2 else float(last["Close"])
    if prior_close <= 0:
        return None
    range_pct = (last["High"] - last["Low"]) / prior_close * 100
    # Rough heuristic: VIX ≈ daily range % * ~16 (annualization-ish, loose).
    approx_vix = round(range_pct * 16.0, 2)
    _store(cache_key, approx_vix)
    return approx_vix


# ── Earnings calendar (Finnhub) ────────────────────────────────────

def fetch_earnings_calendar(symbols: list[str], date: str) -> list[dict]:
    """Fetch earnings calendar for ``symbols`` on ``date`` (YYYY-MM-DD).

    Returns a list of ``{'symbol', 'date', 'time'}`` dicts. Uses Finnhub.
    """
    if not _FINNHUB_KEY:
        logger.warning("fetch_earnings_calendar: FINNHUB_API_KEY not set")
        return []
    cache_key = f"earnings:{date}"
    cached = _cached(cache_key, 300.0)
    if cached is not None:
        return [e for e in cached if _clean_symbol(e["symbol"]) in {_clean_symbol(s) for s in symbols}]
    url = "https://finnhub.io/api/v1/calendar/earnings"
    params = {"from": date, "to": date, "token": _FINNHUB_KEY}
    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("fetch_earnings_calendar(%s) failed: %s", date, exc)
        return []
    entries = []
    for item in data.get("earningsCalendar", []):
        entries.append({
            "symbol": item.get("symbol", ""),
            "date": item.get("date", date),
            "time": item.get("time", ""),
        })
    _store(cache_key, entries)
    wanted = {_clean_symbol(s) for s in symbols}
    return [e for e in entries if _clean_symbol(e["symbol"]) in wanted]
