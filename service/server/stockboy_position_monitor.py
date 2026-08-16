"""Position monitor detector for FenceBarRunner open positions.

Implements two intra-trade loss-prevention decisions:

  Decision 3 — Move stop to breakeven:
    Position reached +0.5% MFE, has been open ≥10 min, and momentum has
    stalled for ≥3 five-minute bars (15 min). Tighten the stop to entry.

  Decision 4 — Take profit early:
    Position peaked at +0.5%+ MFE, stalled ≥30 min (6 bars), is still
    profitable, and price is drifting back toward entry. Only after
    11:00 ET so morning momentum has room to develop.

All functions are fault-tolerant: missing bars or stale prices yield a
``'none'`` action so the supervisor loop never crashes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("StockBoy.PositionMonitor")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[StockBoy.Pos] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False

_ET = ZoneInfo("America/New_York")

# Decision 3 — breakeven stop
_MFE_BREAKEVEN_PCT = 0.5
_MIN_MINUTES_BREAKEVEN = 10
_MIN_STALL_BARS_BREAKEVEN = 3

# Decision 4 — early exit
_MIN_MINUTES_SINCE_MFE = 30
_MIN_STALL_BARS_EARLY_EXIT = 6
_EARLY_EXIT_AFTER_HOUR = 11  # ET


def _parse_entry_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        raw = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _compute_mfe_mae(bars: pd.DataFrame, entry: float, side: str) -> tuple[float | None, float | None, int]:
    """Return (mfe_pct, mae_pct, bars_since_new_extreme)."""
    if bars is None or bars.empty or entry <= 0:
        return None, None, 0
    if side == "long":
        favorable = bars["High"]; adverse = bars["Low"]
    else:
        favorable = bars["Low"]; adverse = bars["High"]
    if side == "long":
        mfe_pct = (favorable.max() - entry) / entry * 100
        mae_pct = (entry - adverse.min()) / entry * 100
        extrema = favorable
    else:
        mfe_pct = (entry - favorable.min()) / entry * 100
        mae_pct = (adverse.max() - entry) / entry * 100
        extrema = favorable
    # Bars since the most recent new MFE extreme.
    last_extreme_idx = extrema.idxmax() if side == "long" else extrema.idxmin()
    bars_since = len(bars.loc[last_extreme_idx:]) - 1
    return float(mfe_pct), float(mae_pct), max(0, bars_since)


def _current_pnl_pct(position: dict) -> float | None:
    entry = float(position.get("entry_price") or 0)
    current = position.get("current_price")
    if current is None or entry <= 0:
        return None
    current = float(current)
    side = (position.get("side") or "long").lower()
    if side == "long":
        return (current - entry) / entry * 100
    return (entry - current) / entry * 100


def _drifting_back(position: dict, mfe_pct: float | None) -> bool:
    """True when the last close is closer to entry than to the MFE price."""
    if mfe_pct is None or mfe_pct <= 0:
        return False
    entry = float(position.get("entry_price") or 0)
    current = position.get("current_price")
    if current is None or entry <= 0:
        return False
    current = float(current)
    side = (position.get("side") or "long").lower()
    if side == "long":
        mfe_price = entry * (1 + mfe_pct / 100)
        return (mfe_price - current) > (current - entry)
    mfe_price = entry * (1 - mfe_pct / 100)
    return (current - mfe_price) > (entry - current)


def _now_et() -> datetime:
    return datetime.now(_ET)


def monitor_position(position: dict, bars_since_entry: pd.DataFrame) -> dict:
    """Monitor an open position for breakeven-stop and early-exit decisions.

    Args:
        position: dict with entry_price, side, entry_timestamp, stop_loss_price,
                  current_price.
        bars_since_entry: 5m bars from entry to now.

    Returns:
        ``{'action': 'none'|'set_stop_breakeven'|'close_position',
           'rationale': str, 'metrics': dict}``
    """
    entry = float(position.get("entry_price") or 0)
    side = (position.get("side") or "long").lower()
    if entry <= 0 or bars_since_entry is None or bars_since_entry.empty:
        return _no_action("missing entry price or bars")

    mfe_pct, mae_pct, bars_since_extreme = _compute_mfe_mae(bars_since_entry, entry, side)
    if mfe_pct is None:
        return _no_action("could not compute MFE")

    entry_ts = _parse_entry_ts(position.get("entry_timestamp") or position.get("opened_at"))
    minutes_since_entry = None
    if entry_ts is not None:
        minutes_since_entry = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 60

    # Approximate minutes since MFE from bar count (5m bars).
    minutes_since_mfe = bars_since_extreme * 5.0

    metrics = {
        "mfe_pct": mfe_pct, "mae_pct": mae_pct,
        "bars_since_new_extreme": bars_since_extreme,
        "minutes_since_entry": minutes_since_entry,
        "minutes_since_mfe": minutes_since_mfe,
    }

    # Decision 3 — move stop to breakeven.
    if (
        mfe_pct >= _MFE_BREAKEVEN_PCT
        and (minutes_since_entry is None or minutes_since_entry >= _MIN_MINUTES_BREAKEVEN)
        and bars_since_extreme >= _MIN_STALL_BARS_BREAKEVEN
    ):
        return {
            "action": "set_stop_breakeven",
            "rationale": f"MFE {mfe_pct:.2f}% stalled {bars_since_extreme} bars — moving stop to breakeven",
            "metrics": metrics,
        }

    # Decision 4 — take profit early.
    pnl_pct = _current_pnl_pct(position)
    now_et = _now_et()
    after_morning = now_et.hour >= _EARLY_EXIT_AFTER_HOUR
    if (
        mfe_pct >= _MFE_BREAKEVEN_PCT
        and minutes_since_mfe >= _MIN_MINUTES_SINCE_MFE
        and bars_since_extreme >= _MIN_STALL_BARS_EARLY_EXIT
        and pnl_pct is not None and pnl_pct > 0
        and _drifting_back(position, mfe_pct)
        and after_morning
    ):
        return {
            "action": "close_position",
            "rationale": (
                f"Peaked +{mfe_pct:.2f}%, stalled {minutes_since_mfe:.0f}min, "
                f"P&L {pnl_pct:.2f}% drifting back — taking profit early"
            ),
            "metrics": metrics,
        }

    return _no_action("no action", metrics)


def _no_action(reason: str, metrics: dict | None = None) -> dict:
    return {"action": "none", "rationale": reason, "metrics": metrics or {}}
