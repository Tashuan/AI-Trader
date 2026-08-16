"""Pure Fence Bar opening-range strategy logic.

The module contains no I/O and can be used by both the standalone backtester
and a future live runner.  All decisions are made from completed 5-minute
bars, so the replay and live implementations can share the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

import pandas as pd

from strategy_lab import require_range


FENCE_BAR_DEFAULTS: dict[str, Any] = {
    "session": {
        "timezone": "America/New_York",
        "market_open": "09:30",
        "fence_end": "09:35",
        "latest_breakout": "10:30",
        "force_exit": "15:55",
    },
    "fence": {
        "min_range_pct": 0.10,
        "max_range_pct": 1.50,
    },
    "breakout": {
        "require_body_outside": True,
        "min_close_distance_pct": 0.0,
        "max_bars_after_fence": 12,
    },
    "retest": {
        "enabled": True,
        "max_bars_after_breakout": 3,
        "require_wick_into_fence": True,
        "require_close_back_outside": True,
        "allow_breakout_bar_retest": False,
        "max_retest_depth_pct": 100.0,
    },
    "anchor": {
        "enabled": True,
        "period": 20,
        "require_trend_alignment": False,
        "max_distance_pct": 2.0,
        "extended_action": "reject",
    },
    "risk": {
        "stop_mode": "fence_midpoint",
        "target_multiple_r": 2.0,
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {
        "mode": "fixed_sl_tp",
        "trailing_pct": 0.3,
        "trailing_activation_pct": 0.3,
        "max_bars": 0,
    },
    "premarket": {
        "enabled": False,
        "interval": "5m",
        "require_monitor": True,
        "min_score": 35.0,
        "use_news": False,
        "history_period": "3mo",
    },
}


@dataclass(frozen=True)
class Fence:
    high: float
    low: float
    midpoint: float
    range_pct: float
    date: str


@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    side: str
    timestamp: str
    entry_price: float
    stop_price: float
    target_price: float
    risk_per_share: float
    fence_high: float
    fence_low: float
    breakout_timestamp: str
    reason: str


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _ts_value(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York").tz_localize(None)
    return ts


def _bar_time(value: Any) -> time:
    return _ts_value(value).time()


def _bar_date(value: Any) -> str:
    return _ts_value(value).date().isoformat()


def _close_distance_ok(close: float, rail: float, minimum_pct: float) -> bool:
    if minimum_pct <= 0:
        return True
    return abs(close - rail) / rail * 100 >= minimum_pct


def validate_config(params: dict[str, Any]) -> None:
    """Validate settings that could otherwise create impossible trades."""
    session = params.get("session", {})
    fence = params.get("fence", {})
    retest = params.get("retest", {})
    risk = params.get("risk", {})
    min_range = require_range(fence.get("min_range_pct", 0.1), "min_range_pct", 0)
    max_range = require_range(fence.get("max_range_pct", 1.5), "max_range_pct", min_range)
    require_range(retest.get("max_bars_after_breakout", 3), "max_bars_after_breakout", 0)
    require_range(risk.get("target_multiple_r", 2), "target_multiple_r", 0.1)
    require_range(risk.get("risk_per_trade_pct", 0.5), "risk_per_trade_pct", 0, 100)
    if _parse_time(session.get("market_open", "09:30")) >= _parse_time(session.get("fence_end", "09:35")):
        raise ValueError("market_open must be before fence_end")
    if min_range > max_range:
        raise ValueError("fence min_range_pct must not exceed max_range_pct")


class FenceBarStrategy:
    """Finite-state machine for one symbol and one regular session."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None):
        self.symbol = symbol.upper()
        self.params = params or FENCE_BAR_DEFAULTS
        validate_config(self.params)
        self.reset()

    def reset(self) -> None:
        self.session_date: str | None = None
        self.fence: Fence | None = None
        self.breakout_side: str | None = None
        self.breakout_timestamp: str | None = None
        self.breakout_index: int | None = None
        self.fence_index: int | None = None
        self.entry_emitted = False
        self.state = "WAIT_FOR_FENCE"
        self._bars_seen = 0
        self._closes: list[float] = []

    def _new_session_if_needed(self, timestamp: Any) -> None:
        date = _bar_date(timestamp)
        if date != self.session_date:
            self.reset()
            self.session_date = date

    def _capture_fence(self, bar: pd.Series, timestamp: Any) -> None:
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        if low <= 0 or high <= low:
            self.state = "DONE_FOR_DAY"
            return
        range_pct = (high - low) / close * 100
        fence_cfg = self.params.get("fence", {})
        if not (float(fence_cfg.get("min_range_pct", 0.1)) <= range_pct <= float(fence_cfg.get("max_range_pct", 1.5))):
            self.state = "DONE_FOR_DAY"
            return
        self.fence = Fence(high, low, (high + low) / 2, range_pct, _bar_date(timestamp))
        self.state = "WAIT_FOR_BREAKOUT"

    def _breakout(self, bar: pd.Series, side: str) -> bool:
        assert self.fence is not None
        open_px, close = float(bar["Open"]), float(bar["Close"])
        cfg = self.params.get("breakout", {})
        rail = self.fence.high if side == "long" else self.fence.low
        if side == "long":
            outside = close > rail and (not cfg.get("require_body_outside", True) or open_px >= rail)
        else:
            outside = close < rail and (not cfg.get("require_body_outside", True) or open_px <= rail)
        return outside and _close_distance_ok(close, rail, float(cfg.get("min_close_distance_pct", 0)))

    def _valid_retest(self, bar: pd.Series, side: str) -> bool:
        assert self.fence is not None
        open_px, high, low, close = [float(bar[k]) for k in ("Open", "High", "Low", "Close")]
        cfg = self.params.get("retest", {})
        if side == "long":
            touched = low <= self.fence.high
            closed_outside = close > self.fence.high
            started_outside = open_px >= self.fence.high
            depth = max(0.0, (self.fence.high - low) / (self.fence.high - self.fence.low) * 100)
        else:
            touched = high >= self.fence.low
            closed_outside = close < self.fence.low
            started_outside = open_px <= self.fence.low
            depth = max(0.0, (high - self.fence.low) / (self.fence.high - self.fence.low) * 100)
        return (
            started_outside
            and (touched if cfg.get("require_wick_into_fence", True) else True)
            and (closed_outside if cfg.get("require_close_back_outside", True) else True)
            and depth <= float(cfg.get("max_retest_depth_pct", 100.0))
        )

    def _anchor_allows(self, entry: float, side: str) -> bool:
        cfg = self.params.get("anchor", {})
        if not cfg.get("enabled", True):
            return True
        period = int(cfg.get("period", 20))
        if len(self._closes) < period:
            return not cfg.get("require_trend_alignment", False)
        sma = sum(self._closes[-period:]) / period
        distance = abs(entry - sma) / sma * 100 if sma > 0 else 0.0
        if distance > float(cfg.get("max_distance_pct", 2.0)) and cfg.get("extended_action", "reject") == "reject":
            return False
        if cfg.get("require_trend_alignment", False):
            return entry >= sma if side == "long" else entry <= sma
        return True

    def _signal(self, bar: pd.Series, timestamp: Any, side: str) -> EntrySignal | None:
        assert self.fence is not None
        entry = float(bar["Close"])
        if not self._anchor_allows(entry, side):
            return None
        stop_mode = self.params.get("risk", {}).get("stop_mode", "fence_midpoint")
        if stop_mode == "fence_low_high":
            stop = self.fence.low if side == "long" else self.fence.high
        else:
            stop = self.fence.midpoint
        risk = entry - stop if side == "long" else stop - entry
        if risk <= 0:
            return None
        multiple = float(self.params.get("risk", {}).get("target_multiple_r", 2.0))
        target = entry + multiple * risk if side == "long" else entry - multiple * risk
        return EntrySignal(
            symbol=self.symbol, side=side, timestamp=str(_ts_value(timestamp)),
            entry_price=entry, stop_price=stop, target_price=target,
            risk_per_share=risk, fence_high=self.fence.high, fence_low=self.fence.low,
            breakout_timestamp=self.breakout_timestamp or "",
            reason=f"{side} fence breakout and retest close",
        )

    def on_bar(self, timestamp: Any, bar: pd.Series, bar_index: int) -> EntrySignal | None:
        """Consume one completed bar and optionally emit one entry signal."""
        self._new_session_if_needed(timestamp)
        current_time = _bar_time(timestamp)
        session = self.params.get("session", {})
        open_time = _parse_time(session.get("market_open", "09:30"))
        fence_end = _parse_time(session.get("fence_end", "09:35"))
        latest_breakout = _parse_time(session.get("latest_breakout", "10:30"))
        force_exit = _parse_time(session.get("force_exit", "15:55"))
        if current_time < open_time or current_time >= force_exit or self.state == "DONE_FOR_DAY":
            return None
        self._closes.append(float(bar["Close"]))
        if current_time == open_time and current_time < fence_end:
            self._capture_fence(bar, timestamp)
            if self.fence is not None:
                self.fence_index = bar_index
            return None
        self._bars_seen += 1
        if self.fence is None or self.entry_emitted:
            return None
        if self.state == "WAIT_FOR_BREAKOUT":
            assert self.fence_index is not None
            if bar_index - self.fence_index > int(self.params.get("breakout", {}).get("max_bars_after_fence", 12)):
                self.state = "DONE_FOR_DAY"
                return None
            if current_time > latest_breakout:
                self.state = "DONE_FOR_DAY"
                return None
            for side in ("long", "short"):
                if self._breakout(bar, side):
                    self.breakout_side = side
                    self.breakout_timestamp = str(_ts_value(timestamp))
                    self.breakout_index = bar_index
                    retest_cfg = self.params.get("retest", {})
                    if not retest_cfg.get("enabled", True):
                        signal = self._signal(bar, timestamp, side)
                        if signal:
                            self.entry_emitted = True
                            self.state = "POSITION_OPEN"
                            return signal
                        self.state = "DONE_FOR_DAY"
                        return None
                    self.state = "WAIT_FOR_RETEST"
                    return None
        elif self.state == "WAIT_FOR_RETEST":
            assert self.breakout_index is not None and self.breakout_side is not None
            age = bar_index - self.breakout_index
            max_age = int(self.params.get("retest", {}).get("max_bars_after_breakout", 3))
            allow_same = bool(self.params.get("retest", {}).get("allow_breakout_bar_retest", False))
            if age == 0 and not allow_same:
                return None
            if age < 0 or age > max_age:
                self.state = "DONE_FOR_DAY"
                return None
            if self._valid_retest(bar, self.breakout_side):
                signal = self._signal(bar, timestamp, self.breakout_side)
                if signal:
                    self.entry_emitted = True
                    self.state = "POSITION_OPEN"
                    return signal
        return None
