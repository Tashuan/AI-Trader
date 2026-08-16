"""Premarket context detector for the FenceBarRunner vol filter.

Runs once pre-market to decide whether StockBoy should override the
ATR volatility filter when a catalyst is present but ATR sits in the
gray zone (1.2%–1.8%). The override lowers the threshold to 1.2% so
the runner trades on catalyst days it would otherwise skip.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stockboy_market_data import fetch_earnings_calendar, fetch_premarket_gap, fetch_vix

logger = logging.getLogger("StockBoy.Premarket")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[StockBoy.Premarket] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False

_ET = ZoneInfo("America/New_York")

# Gray zone boundaries for the ATR vol filter.
_ATR_GRAY_LOW = 1.2
_ATR_GRAY_HIGH = 1.8
_OVERRIDE_THRESHOLD = 1.2   # lower the filter to this when overriding

# Catalyst triggers.
_SPY_GAP_THRESHOLD = 1.5    # % gap from prior close
_VIX_THRESHOLD = 25.0

_OVERRIDE_TTL_MINUTES = 390  # one US trading day


def _today_et() -> str:
    return datetime.now(_ET).date().isoformat()


def _tomorrow_et() -> str:
    return (datetime.now(_ET) + timedelta(days=1)).date().isoformat()


def evaluate_vol_override(symbols: list[str], current_atr: float) -> dict:
    """Evaluate whether to override the vol filter for today.

    Args:
        symbols: list of universe symbols to check for earnings.
        current_atr: SPY 20-day ATR percentage (e.g. 1.5).

    Returns:
        ``{'override': bool, 'new_atr_threshold': float|None,
           'reasons': list[str], 'expires_in_minutes': int}``
    """
    reasons: list[str] = []

    # ATR already passes the filter naturally.
    if current_atr >= _ATR_GRAY_HIGH:
        return _no_override(f"ATR {current_atr:.2f}% >= {_ATR_GRAY_HIGH}% — filter passes naturally")

    # ATR too low — filter correctly blocks; no catalyst justifies trading.
    if current_atr < _ATR_GRAY_LOW:
        return _no_override(f"ATR {current_atr:.2f}% < {_ATR_GRAY_LOW}% — filter correctly blocks")

    # Gray zone: look for a catalyst.
    today, tomorrow = _today_et(), _tomorrow_et()
    earnings = []
    try:
        earnings = fetch_earnings_calendar(symbols, today) + fetch_earnings_calendar(symbols, tomorrow)
    except Exception as exc:
        logger.warning("earnings fetch failed: %s", exc)

    if earnings:
        names = ", ".join(sorted({e["symbol"] for e in earnings}))
        reasons.append(f"earnings today/tomorrow: {names}")

    spy_gap = None
    try:
        spy_gap = fetch_premarket_gap("SPY")
    except Exception as exc:
        logger.warning("SPY gap fetch failed: %s", exc)
    if spy_gap is not None and abs(spy_gap) > _SPY_GAP_THRESHOLD:
        reasons.append(f"SPY gap {spy_gap:+.2f}% exceeds ±{_SPY_GAP_THRESHOLD}%")

    vix = None
    try:
        vix = fetch_vix()
    except Exception as exc:
        logger.warning("VIX fetch failed: %s", exc)
    if vix is not None and vix > _VIX_THRESHOLD:
        reasons.append(f"VIX {vix:.1f} > {_VIX_THRESHOLD}")

    if not reasons:
        return _no_override(f"ATR {current_atr:.2f}% in gray zone but no catalyst detected")

    return {
        "override": True,
        "new_atr_threshold": _OVERRIDE_THRESHOLD,
        "reasons": reasons,
        "expires_in_minutes": _OVERRIDE_TTL_MINUTES,
    }


def _no_override(reason: str) -> dict:
    return {"override": False, "new_atr_threshold": None, "reasons": [reason], "expires_in_minutes": 0}
