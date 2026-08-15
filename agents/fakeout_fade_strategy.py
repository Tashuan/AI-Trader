"""Pure failed-opening-range-breakout reversal strategy logic."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


FAKEOUT_FADE_DEFAULTS: dict[str, Any] = {
    "session": {
        "market_open": "09:30",
        "range_end": "09:45",
        "latest_entry": "11:00",
        "force_exit": "15:55",
    },
    "entry": {"max_bars_to_confirm_failure": 2},
    "risk": {
        "stop_buffer_pct": 0.30,
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {"mode": "fixed_sl_tp", "max_bars": 0},
    "vol_filter": {"enabled": True, "mode": "window"},
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class FakeoutFadeStrategy:
    """Fade a breakout that closes back inside the first 15m range."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None):
        self.symbol = symbol.upper()
        self.params = params or FAKEOUT_FADE_DEFAULTS
        self.session_date = None
        self.range_high = None
        self.range_low = None
        self.range_complete = False
        self.breakout_side = None
        self.breakout_index = None
        self.breakout_extreme = None
        self.entry_emitted = False

    def _reset(self, date) -> None:
        self.session_date = date
        self.range_high = None
        self.range_low = None
        self.range_complete = False
        self.breakout_side = None
        self.breakout_index = None
        self.breakout_extreme = None
        self.entry_emitted = False

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        if ts.date() != self.session_date:
            self._reset(ts.date())

        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        session = self.params["session"]
        range_end = _clock(session.get("range_end", "09:45"))

        if not self.range_complete:
            if ts.time() >= range_end:
                self.range_complete = True
            else:
                self.range_high = high if self.range_high is None else max(self.range_high, high)
                self.range_low = low if self.range_low is None else min(self.range_low, low)
                return None
        if self.entry_emitted or ts.time() > _clock(session.get("latest_entry", "11:00")):
            return None

        if self.breakout_side is None:
            if close > self.range_high:
                self.breakout_side = "long"
                self.breakout_index = index
                self.breakout_extreme = high
            elif close < self.range_low:
                self.breakout_side = "short"
                self.breakout_index = index
                self.breakout_extreme = low
            return None

        self.breakout_extreme = max(self.breakout_extreme, high) if self.breakout_side == "long" else min(self.breakout_extreme, low)
        max_bars = int(self.params["entry"].get("max_bars_to_confirm_failure", 2))
        bars_since = index - self.breakout_index
        back_inside = self.range_low <= close <= self.range_high
        if bars_since <= max_bars and back_inside:
            buffer_pct = float(self.params["risk"].get("stop_buffer_pct", 0.30)) / 100.0
            if self.breakout_side == "long":
                side = "short"
                stop = self.breakout_extreme + close * buffer_pct
                target = self.range_low
            else:
                side = "long"
                stop = self.breakout_extreme - close * buffer_pct
                target = self.range_high
            risk = (stop - close) if side == "short" else (close - stop)
            if risk <= 0 or (side == "short" and target >= close) or (side == "long" and target <= close):
                self.breakout_side = None
                return None
            self.entry_emitted = True
            return EntrySignal(
                symbol=self.symbol,
                side=side,
                timestamp=str(ts),
                entry_price=close,
                stop_price=stop,
                target_price=target,
                risk_per_share=risk,
                fence_high=self.range_high,
                fence_low=self.range_low,
                breakout_timestamp=str(ts),
                reason=f"fakeout_fade failed_{self.breakout_side}",
            )
        if bars_since > max_bars:
            self.breakout_side = None
            self.breakout_index = None
            self.breakout_extreme = None
        return None
