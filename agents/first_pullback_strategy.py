"""Pure first-pullback momentum strategy logic."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


FIRST_PULLBACK_DEFAULTS: dict[str, Any] = {
    "session": {
        "market_open": "09:30",
        "confirm_end": "09:45",
        "latest_entry": "11:00",
        "force_exit": "15:55",
    },
    "entry": {
        "gap_threshold_pct": 1.00,
        "ema_period": 9,
        "min_confirm_move_pct": 0.25,
    },
    "risk": {
        "stop_buffer_pct": 0.10,
        "target_multiple_r": 2.0,
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {"mode": "fixed_sl_tp", "max_bars": 0},
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class FirstPullbackStrategy:
    """Enter the first EMA pullback after a confirmed opening gap move."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None,
                 previous_close: float | None = None):
        self.symbol = symbol.upper()
        self.params = params or FIRST_PULLBACK_DEFAULTS
        self.previous_close = previous_close
        self.session_date = None
        self.first_open = None
        self.ema = None
        self.gap_direction = None
        self.confirmed = False
        self.entry_emitted = False
        self.pullback_extreme = None

    def _reset(self, date) -> None:
        self.session_date = date
        self.first_open = None
        self.ema = None
        self.gap_direction = None
        self.confirmed = False
        self.entry_emitted = False
        self.pullback_extreme = None

    def _update_ema(self, close: float) -> None:
        period = max(1, int(self.params["entry"].get("ema_period", 9)))
        alpha = 2.0 / (period + 1.0)
        self.ema = close if self.ema is None else alpha * close + (1.0 - alpha) * self.ema

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        if ts.date() != self.session_date:
            self._reset(ts.date())

        open_px = float(bar["Open"])
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        if self.first_open is None:
            self.first_open = open_px
            if self.previous_close and self.previous_close > 0:
                gap = (self.first_open / self.previous_close - 1.0) * 100.0
                threshold = float(self.params["entry"].get("gap_threshold_pct", 1.0))
                if abs(gap) >= threshold:
                    self.gap_direction = "long" if gap > 0 else "short"
        self._update_ema(close)

        if self.entry_emitted or self.gap_direction is None:
            return None
        session = self.params["session"]
        if ts.time() < _clock(session.get("confirm_end", "09:45")):
            return None
        if ts.time() > _clock(session.get("latest_entry", "11:00")):
            return None

        if not self.confirmed:
            move_pct = (close / self.first_open - 1.0) * 100.0
            minimum = float(self.params["entry"].get("min_confirm_move_pct", 0.25))
            aligned = move_pct >= minimum if self.gap_direction == "long" else move_pct <= -minimum
            if not aligned:
                return None
            self.confirmed = True
            return None

        if self.ema is None:
            return None
        touched = low <= self.ema <= high
        bounced = close >= self.ema if self.gap_direction == "long" else close <= self.ema
        if not touched or not bounced:
            return None

        buffer_pct = float(self.params["risk"].get("stop_buffer_pct", 0.10)) / 100.0
        if self.gap_direction == "long":
            stop = low - close * buffer_pct
            risk = close - stop
            target = close + risk * float(self.params["risk"].get("target_multiple_r", 2.0))
        else:
            stop = high + close * buffer_pct
            risk = stop - close
            target = close - risk * float(self.params["risk"].get("target_multiple_r", 2.0))
        if risk <= 0:
            return None

        self.entry_emitted = True
        return EntrySignal(
            symbol=self.symbol,
            side=self.gap_direction,
            timestamp=str(ts),
            entry_price=close,
            stop_price=stop,
            target_price=target,
            risk_per_share=risk,
            fence_high=high,
            fence_low=low,
            breakout_timestamp=str(ts),
            reason=f"first_pullback ema={self.ema:.4f} direction={self.gap_direction}",
        )
