"""backtest_liquidity.py — Conservative spread/depth estimation for backtests.

When historical bid/ask/Level 2 data is unavailable, the backtester must not
assume a zero spread. This module produces conservative, deterministic quote
and Level 2 estimates from OHLCV bars so that:

  - Liquidity scoring rejects candidates that would fail in live trading.
  - Fill simulation crosses a realistic spread (entries at ask, exits at bid).
  - Every estimate is explicitly labeled with its provenance so reports can
    distinguish observed microstructure from modeled estimates.

The spread model is intentionally conservative (overestimates cost):
  1. Base spread scales with bar volatility (high-low range).
  2. Spread widens when dollar volume is low (thin book).
  3. Minimum spread is one tick size.
  4. An optional multiplier allows sensitivity analysis.

Depth is estimated conservatively from dollar volume and capped so that
the liquidity score's depth component does not overstate available liquidity.
"""

from __future__ import annotations

import math
from typing import Any, Optional


# ── Tick sizes per market ─────────────────────────────────────────────
_TICK_SIZES = {
    "us-stock": lambda p: 0.01 if p >= 1.0 else 0.0001,
    "crypto": lambda p: 0.01 if p >= 1.0 else 0.0001,
    "forex": lambda p: 0.001 if p >= 50.0 else 0.00001,
    "futures": lambda p: 0.25 if p >= 1000.0 else 0.01,
    "polymarket": lambda p: 0.001,
}


def _tick_size(price: float, market: str = "us-stock") -> float:
    fn = _TICK_SIZES.get(market, _TICK_SIZES["us-stock"])
    return fn(price)


def _bar_value(bar: Any, name: str, default: float = 0.0) -> float:
    """Read a bar field from dicts, pandas rows, or provider-shaped objects."""
    aliases = (name, name.capitalize(), name.upper())
    for key in aliases:
        try:
            if hasattr(bar, "__contains__") and key not in bar:
                continue
            value = bar.get(key) if hasattr(bar, "get") else getattr(bar, key)
        except (AttributeError, KeyError, TypeError):
            continue
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return default


def estimate_quote(
    bar: Any,
    market: str = "us-stock",
    spread_multiplier: float = 1.0,
    observed: Optional[dict] = None,
) -> dict:
    """Build a conservative quote from an OHLCV bar.

    Parameters
    ----------
    bar : dict | pandas row
        Must contain High, Low, Close, Volume fields.
    market : str
        Market type for tick-size rules.
    spread_multiplier : float
        Multiplier on the estimated spread (for sensitivity analysis).
        1.0 = baseline conservative, 2.0 = doubly conservative.
    observed : dict | None
        If a real historical quote is available, pass it here to use directly
        instead of estimating. The quote must contain at least bid and ask.

    Returns
    -------
    dict with keys: symbol, bid, ask, last, spread, spread_pct,
                    total_volume, spread_source, is_estimated
    """
    if observed is not None and observed.get("bid", 0) > 0 and observed.get("ask", 0) > 0:
        bid = float(observed["bid"])
        ask = float(observed["ask"])
        last = float(observed.get("last", (bid + ask) / 2.0))
        spread = ask - bid
        return {
            "bid": bid,
            "ask": ask,
            "last": last,
            "spread": spread,
            "spread_pct": (spread / last * 100) if last > 0 else 0.0,
            "total_volume": float(observed.get("total_volume", _bar_value(bar, "volume", 0))),
            "spread_source": "observed",
            "is_estimated": False,
        }

    close = _bar_value(bar, "close", 0.0)
    high = _bar_value(bar, "high", close)
    low = _bar_value(bar, "low", close)
    volume = _bar_value(bar, "volume", 0.0)

    if close <= 0:
        return {
            "bid": 0.0, "ask": 0.0, "last": 0.0,
            "spread": 0.0, "spread_pct": 999.0,
            "total_volume": 0.0,
            "spread_source": "unavailable",
            "is_estimated": True,
        }

    tick = _tick_size(close, market)

    # Bar volatility as percentage of close.
    bar_range_pct = abs(high - low) / close if close > 0 else 0.0

    # Base spread: half the bar range in bps, minimum 1 bps.
    base_bps = max(1.0, bar_range_pct * 10_000.0 * 0.5)

    # Dollar volume — thin books get wider spreads.
    dollar_vol = close * volume
    if dollar_vol > 0:
        # Gentle scaling: 4th root of (1M / dollar_vol), clamped to [1.0, 3.0].
        vol_factor = min(3.0, max(1.0, (1_000_000.0 / dollar_vol) ** 0.25))
    else:
        vol_factor = 3.0

    spread_bps = base_bps * vol_factor * spread_multiplier
    spread = max(tick, close * spread_bps / 10_000.0)

    # Round spread up to the nearest tick.
    if tick > 0:
        spread = math.ceil(spread / tick) * tick

    bid = close - spread / 2.0
    ask = close + spread / 2.0

    # Round bid/ask to tick in adverse direction.
    bid = math.floor(bid / tick) * tick if tick > 0 else bid
    ask = math.ceil(ask / tick) * tick if tick > 0 else ask

    return {
        "bid": bid,
        "ask": ask,
        "last": close,
        "spread": ask - bid,
        "spread_pct": ((ask - bid) / close * 100) if close > 0 else 999.0,
        "total_volume": volume,
        "spread_source": "estimated",
        "is_estimated": True,
    }


def estimate_level2(
    bar: Any,
    market: str = "us-stock",
    observed: Optional[dict] = None,
) -> Optional[dict]:
    """Build a conservative Level 2 depth estimate from an OHLCV bar.

    Returns None when no real Level 2 is available and the estimate should
    not be used as a substitute (caller decides via params).

    If observed Level 2 is provided, it is returned unchanged with
    depth_source="observed".
    """
    if observed is not None and observed.get("total_depth_dollars", 0) > 0:
        result = dict(observed)
        result["depth_source"] = "observed"
        result["is_estimated"] = False
        return result

    close = _bar_value(bar, "close", 0.0)
    volume = _bar_value(bar, "volume", 0.0)
    dollar_vol = close * volume

    if dollar_vol <= 0:
        return None

    # Conservative: assume top-of-book depth is ~0.1% of bar dollar volume,
    # capped at $50K (the default min_depth_dollars threshold).
    estimated_depth = min(dollar_vol * 0.001, 50_000.0)

    return {
        "total_depth_dollars": estimated_depth,
        "bid_depth_dollars": estimated_depth / 2.0,
        "ask_depth_dollars": estimated_depth / 2.0,
        "depth_source": "estimated",
        "is_estimated": True,
    }
