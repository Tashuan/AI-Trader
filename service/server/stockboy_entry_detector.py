"""Entry quality detector for FenceBarRunner pending orders.

Evaluates whether a pending fence-bar entry should be allowed or vetoed
based on fence-bar volume, bid-ask spread, and close location within the
fence range. Designed to be the "human in the loop" loss-prevention
layer that cancels weak entries before they fill.

All functions are fault-tolerant: if market data is unavailable the
detector returns a ``confirm`` decision so the runner is not blocked by
a data outage.
"""

from __future__ import annotations

import logging
from typing import Any

from stockboy_market_data import fetch_latest_quote, fetch_recent_bars

logger = logging.getLogger("StockBoy.EntryDetector")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[StockBoy.Entry] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False

# Veto thresholds — any single condition triggers a veto.
_MIN_FENCE_VOLUME_RATIO = 2.0   # fence bar vol < 2x prior 3-day avg 5m vol
_MAX_SPREAD_PCT = 0.05          # bid-ask spread > 0.05% of price
_MIN_CLOSE_PCT_LONG = 0.75      # longs must close in upper 25% of fence range
_MAX_CLOSE_PCT_SHORT = 0.25     # shorts must close in lower 25% of fence range


def _fence_volume_ratio(bars_5m: Any) -> float | None:
    """Ratio of the latest 5m bar volume to the prior 3-day average 5m volume."""
    if bars_5m is None or bars_5m.empty or len(bars_5m) < 2:
        return None
    latest_vol = float(bars_5m["Volume"].iloc[-1])
    prior = bars_5m["Volume"].iloc[:-1].tail(234)  # ~3 sessions of 5m bars
    if prior.empty:
        return None
    avg_vol = float(prior.mean())
    if avg_vol <= 0:
        return None
    return latest_vol / avg_vol


def _close_position_pct(bars_5m: Any) -> float | None:
    """Where the latest fence bar closed within its own range (0=low, 1=high)."""
    if bars_5m is None or bars_5m.empty:
        return None
    last = bars_5m.iloc[-1]
    low = float(last["Low"]); high = float(last["High"]); close = float(last["Close"])
    if high <= low:
        return None
    return (close - low) / (high - low)


def evaluate_entry_quality(symbol: str, side: str, pending_order: dict) -> dict:
    """Evaluate whether a pending fence bar entry should be allowed or vetoed.

    Args:
        symbol: Ticker symbol.
        side: ``'long'`` or ``'short'``.
        pending_order: Dict with at least ``order_id`` (used for logging only).

    Returns:
        ``{'decision': 'confirm'|'veto', 'reasons': list[str], 'metrics': dict}``
    """
    side = (side or "").lower()
    bars = fetch_recent_bars(symbol, interval="5Min", bars_back=240)
    quote = fetch_latest_quote(symbol)

    fence_vol_ratio = _fence_volume_ratio(bars)
    close_pct = _close_position_pct(bars)
    spread_pct = quote["spread_pct"] if quote else None

    metrics = {
        "fence_volume_ratio": fence_vol_ratio,
        "spread_pct": spread_pct,
        "close_position_pct": close_pct,
    }
    reasons: list[str] = []

    # If we cannot read market data, do not block the runner.
    if fence_vol_ratio is None and close_pct is None and spread_pct is None:
        return {"decision": "confirm", "reasons": ["market data unavailable — allowing"], "metrics": metrics}

    if fence_vol_ratio is not None and fence_vol_ratio < _MIN_FENCE_VOLUME_RATIO:
        reasons.append(f"fence volume ratio {fence_vol_ratio:.2f} < {_MIN_FENCE_VOLUME_RATIO}")

    if spread_pct is not None and spread_pct > _MAX_SPREAD_PCT:
        reasons.append(f"spread {spread_pct:.4f}% > {_MAX_SPREAD_PCT}%")

    if close_pct is not None:
        if side == "long" and close_pct < _MIN_CLOSE_PCT_LONG:
            reasons.append(f"weak close for long ({close_pct:.2f} < {_MIN_CLOSE_PCT_LONG})")
        elif side == "short" and close_pct > _MAX_CLOSE_PCT_SHORT:
            reasons.append(f"weak close for short ({close_pct:.2f} > {_MAX_CLOSE_PCT_SHORT})")

    decision = "veto" if reasons else "confirm"
    return {"decision": decision, "reasons": reasons, "metrics": metrics}
