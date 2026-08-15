"""Pure VWAP Magnet strategy logic for one symbol and one session."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


VWAP_MAGNET_DEFAULTS: dict[str, Any] = {
    "session": {
        "timezone": "America/New_York",
        "market_open": "09:30",
        "settle_end": "09:45",
        "latest_entry": "11:00",
        "force_exit": "15:55",
    },
    "entry": {
        "min_displacement_pct": 0.50,
    },
    "risk": {
        "stop_buffer_pct": 0.30,
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {
        "mode": "fixed_sl_tp",
        "max_bars": 0,
    },
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class VWAPMagnetStrategy:
    """Enter once when price is displaced from session VWAP."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None):
        self.symbol = symbol.upper()
        self.params = params or VWAP_MAGNET_DEFAULTS
        self.session_date = None
        self.pv = 0.0
        self.volume = 0.0
        self.session_high = None
        self.session_low = None
        self.entry_emitted = False

    def _reset(self, date) -> None:
        self.session_date = date
        self.pv = 0.0
        self.volume = 0.0
        self.session_high = None
        self.session_low = None
        self.entry_emitted = False

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        date = ts.date()
        if date != self.session_date:
            self._reset(date)

        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        volume = max(0.0, float(bar.get("Volume", 0.0)))
        typical = (high + low + close) / 3.0
        self.pv += typical * volume
        self.volume += volume
        self.session_high = high if self.session_high is None else max(self.session_high, high)
        self.session_low = low if self.session_low is None else min(self.session_low, low)

        if self.entry_emitted:
            return None
        cfg = self.params.get("session", {})
        entry_cfg = self.params.get("entry", {})
        risk_cfg = self.params.get("risk", {})
        if ts.time() < _clock(cfg.get("settle_end", "09:45")):
            return None
        if ts.time() > _clock(cfg.get("latest_entry", "11:00")):
            return None
        if self.volume <= 0 or close <= 0:
            return None

        vwap = self.pv / self.volume
        displacement = (close - vwap) / vwap * 100.0
        minimum = float(entry_cfg.get("min_displacement_pct", 0.50))
        if abs(displacement) < minimum:
            return None

        buffer_pct = float(risk_cfg.get("stop_buffer_pct", 0.30)) / 100.0
        if displacement < 0:
            side = "long"
            stop = self.session_low - close * buffer_pct
            target = vwap
        else:
            side = "short"
            stop = self.session_high + close * buffer_pct
            target = vwap
        risk = (close - stop) if side == "long" else (stop - close)
        if risk <= 0 or (side == "long" and target <= close) or (side == "short" and target >= close):
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
            fence_high=high,
            fence_low=low,
            breakout_timestamp=str(ts),
            reason=f"vwap_magnet displacement={displacement:+.2f}% vwap={vwap:.4f}",
        )
