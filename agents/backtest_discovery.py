"""Shared backtest discovery module — daily-bar and intraday symbol selection.

Extracted from fence_walk_forward.py's discover_symbols() so both ScalpRunner
and Fence Bar harnesses use the same ranking logic.

Two discovery modes:
  1. Daily-bar: rank by gap, volume ratio, ADV, proximity to prior-day levels
  2. Intraday: rank by volume ratio + price change on first N bars of the session
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# Expanded universe: 40+ symbols
DEFAULT_UNIVERSE: list[str] = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
    "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
    "XPEV", "PLUG", "DKNG",
    # Expanded set
    "SMCI", "ARM", "PLTR", "SOUN", "AI", "PATH", "HOOD", "MSTR",
    "PENN", "FCEL", "JD", "NVAX", "SOFI",
]

FALLBACK_SYMBOLS: list[str] = ["NVDA", "TSLA", "AAPL", "AMD", "META"]


def discover_symbols_for_date(
    date: str,
    provider: Any,
    universe: list[str] | None = None,
    max_symbols: int = 10,
) -> list[str]:
    """Rank universe symbols by gap, volume ratio, and proximity to prior-day levels.

    Uses the same scoring logic as the original fence_walk_forward.discover_symbols():
      - Gap % from prior close to today's open (max 25 pts)
      - Volume ratio vs 20-bar avg (max 20 pts)
      - ADV >= $25M (20 pts)
      - Proximity to prior-day high/low within 1% (15 pts)
    """
    universe = universe or DEFAULT_UNIVERSE
    end_date = date
    start_date = (datetime.fromisoformat(date) - timedelta(days=10)).strftime("%Y-%m-%d")

    candidates: list[dict[str, Any]] = []
    for sym in universe:
        try:
            df = provider.history(sym, interval="1d", start=start_date, end=end_date)
            if df is None or df.empty or len(df) < 2:
                continue
            df = df.reset_index() if df.index.name else df
            col = "Datetime" if "Datetime" in df.columns else "Date"
            df[col] = pd.to_datetime(df[col])

            prior = df.iloc[-2]
            today = df.iloc[-1]

            prev_close = float(prior["Close"])
            today_open = float(today["Open"])
            today_close = float(today["Close"])
            today_volume = float(today["Volume"])
            avg_volume = float(df["Volume"].iloc[:-1].tail(20).mean())

            if prev_close <= 0 or avg_volume <= 0:
                continue

            gap_pct = (today_open / prev_close - 1) * 100
            vol_ratio = today_volume / avg_volume
            prior_high = float(prior["High"])
            prior_low = float(prior["Low"])
            dist_to_high = abs(today_close - prior_high) / prior_high * 100
            dist_to_low = abs(today_close - prior_low) / prior_low * 100
            min_dist = min(dist_to_high, dist_to_low)

            score = 0.0
            score += min(25.0, abs(gap_pct) * 5) if abs(gap_pct) >= 1.0 else 0
            score += min(20.0, vol_ratio * 6) if vol_ratio >= 1.25 else 0
            adv = today_close * avg_volume
            score += 20.0 if adv >= 25_000_000 else 0
            score += 15.0 if min_dist <= 1.0 else 0

            candidates.append({"symbol": sym, "score": round(score, 2)})
        except Exception:
            continue

    candidates.sort(key=lambda c: c["score"], reverse=True)
    symbols = [c["symbol"] for c in candidates[:max_symbols]]
    return symbols if symbols else FALLBACK_SYMBOLS


def discover_symbols_intraday(
    date: str,
    provider: Any,
    universe: list[str] | None = None,
    interval: str = "5m",
    max_symbols: int = 10,
    lookback_bars: int = 10,
) -> list[str]:
    """Rank universe symbols by intraday volume ratio + price change on first N bars.

    Mirrors the live scanner's _discover_shortlist logic: scan the first
    `lookback_bars` bars of the session to find the strongest movers.
    """
    universe = universe or DEFAULT_UNIVERSE
    date_obj = datetime.fromisoformat(date)
    start = (date_obj - timedelta(days=3)).strftime("%Y-%m-%d")
    end = (date_obj + timedelta(days=1)).strftime("%Y-%m-%d")

    candidates: list[dict[str, Any]] = []
    for sym in universe:
        try:
            df = provider.history(sym, interval=interval, start=start, end=end,
                                  auto_adjust=False, raise_errors=False)
            if df is None or df.empty:
                continue
            df = df.reset_index() if df.index.name else df
            time_col = "Datetime" if "Datetime" in df.columns else "Date"
            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
            df = df.dropna(subset=[time_col])

            # Filter to the target trading day
            if df[time_col].dt.tz is not None:
                df[time_col] = df[time_col].dt.tz_convert("America/New_York").dt.tz_localize(None)
            else:
                df[time_col] = df[time_col].dt.tz_localize(None)

            day_mask = df[time_col].dt.date == date_obj.date()
            day_df = df[day_mask].head(lookback_bars)
            if day_df.empty or len(day_df) < 3:
                continue

            open_px = float(day_df["Open"].iloc[0])
            close_px = float(day_df["Close"].iloc[-1])
            total_vol = float(day_df["Volume"].sum())

            if open_px <= 0 or total_vol <= 0:
                continue

            price_change_pct = (close_px / open_px - 1) * 100
            # Volume ratio: first N bars vs full-day average per-bar volume
            full_day = df[day_mask]
            if len(full_day) > lookback_bars:
                avg_bar_vol = float(full_day["Volume"].mean())
            else:
                avg_bar_vol = total_vol / len(day_df)
            vol_ratio = (total_vol / len(day_df)) / avg_bar_vol if avg_bar_vol > 0 else 1.0

            # Score: volume surge + absolute price change
            score = 0.0
            score += min(40.0, vol_ratio * 15) if vol_ratio >= 1.2 else 0
            score += min(30.0, abs(price_change_pct) * 10) if abs(price_change_pct) >= 0.3 else 0
            adv = close_px * total_vol
            score += 20.0 if adv >= 5_000_000 else 0
            # Momentum direction bonus
            score += 10.0 if abs(price_change_pct) >= 1.0 else 0

            candidates.append({
                "symbol": sym,
                "score": round(score, 2),
                "price_change_pct": round(price_change_pct, 2),
                "vol_ratio": round(vol_ratio, 2),
            })
        except Exception:
            continue

    candidates.sort(key=lambda c: c["score"], reverse=True)
    symbols = [c["symbol"] for c in candidates[:max_symbols]]
    return symbols if symbols else FALLBACK_SYMBOLS


def make_discovery_fn(
    mode: str = "static",
    provider: Any = None,
    universe: list[str] | None = None,
    max_symbols: int = 10,
    interval: str = "5m",
    lookback_bars: int = 10,
) -> Optional[Callable[[str], list[str]]]:
    """Build a discovery callback for the given mode.

    Modes:
      - "static": returns None (use the backtester's static symbol list)
      - "daily": daily-bar discovery via discover_symbols_for_date
      - "intraday": intraday-bar discovery via discover_symbols_intraday
    """
    if mode == "static" or mode is None:
        return None

    if mode == "daily":
        def fn(date: str) -> list[str]:
            return discover_symbols_for_date(
                date, provider, universe, max_symbols,
            )
        return fn

    if mode == "intraday":
        def fn(date: str) -> list[str]:
            return discover_symbols_intraday(
                date, provider, universe, interval, max_symbols, lookback_bars,
            )
        return fn

    logger.warning("Unknown discovery mode: %s, falling back to static", mode)
    return None
