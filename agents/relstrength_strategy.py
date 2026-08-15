"""Relative-strength opening-drive continuation strategy."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


RELSTRENGTH_DEFAULTS: dict[str, Any] = {
    "session": {"market_open": "09:30", "opening_end": "09:45", "latest_entry": "11:00"},
    "entry": {
        "min_move_pct": 0.75,
        "rs_threshold": 0.50,
        "consolidation_bars": 3,
        "max_consolidation_pct": 0.40,
        "require_vwap_alignment": True,
        "require_volume_decline": True,
    },
    "risk": {"stop_buffer_pct": 0.10, "target_multiple_r": 2.0},
    "exit": {"mode": "fixed_sl_tp", "max_bars": 0},
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class RelativeStrengthStrategy:
    """Continue the strongest stock-relative-to-SPY opening drive."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None,
                 spy_opening_return: float | None = None):
        self.symbol = symbol.upper()
        self.params = params or RELSTRENGTH_DEFAULTS
        self.spy_opening_return = spy_opening_return
        self.session_date = None
        self.first_open = None
        self.opening_high = None
        self.opening_low = None
        self.opening_return = None
        self.direction = None
        self.vwap_pv = 0.0
        self.vwap_volume = 0.0
        self.ema = None
        self.compression: list[dict[str, float]] = []
        self.compression_ready = False
        self.compression_high = None
        self.compression_low = None
        self.entry_emitted = False

    def _reset(self, date) -> None:
        self.session_date = date
        self.first_open = self.opening_high = self.opening_low = None
        self.opening_return = self.direction = None
        self.vwap_pv = self.vwap_volume = 0.0
        self.ema = None
        self.compression = []
        self.compression_ready = False
        self.compression_high = self.compression_low = None
        self.entry_emitted = False

    def _vwap(self, high: float, low: float, close: float, volume: float) -> float:
        self.vwap_pv += ((high + low + close) / 3) * max(volume, 0)
        self.vwap_volume += max(volume, 0)
        return self.vwap_pv / self.vwap_volume if self.vwap_volume else close

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        if ts.date() != self.session_date:
            self._reset(ts.date())
        high, low = float(bar["High"]), float(bar["Low"])
        close, open_px = float(bar["Close"]), float(bar["Open"])
        volume = max(0.0, float(bar.get("Volume", 0)))
        vwap = self._vwap(high, low, close, volume)
        entry_cfg = self.params["entry"]
        session_cfg = self.params["session"]
        end = _clock(session_cfg.get("opening_end", "09:45"))

        if self.first_open is None:
            self.first_open = open_px
        if ts.time() < end:
            self.opening_high = high if self.opening_high is None else max(self.opening_high, high)
            self.opening_low = low if self.opening_low is None else min(self.opening_low, low)
            return None
        if self.opening_return is None:
            self.opening_high = high if self.opening_high is None else max(self.opening_high, high)
            self.opening_low = low if self.opening_low is None else min(self.opening_low, low)
            self.opening_return = (close / self.first_open - 1) * 100
            rs = self.opening_return - (self.spy_opening_return or 0.0)
            if abs(self.opening_return) < float(entry_cfg.get("min_move_pct", .75)):
                self.direction = "rejected"
            elif abs(rs) < float(entry_cfg.get("rs_threshold", .50)):
                self.direction = "rejected"
            else:
                self.direction = "long" if rs > 0 else "short"
            return None
        if self.entry_emitted or self.direction in (None, "rejected"):
            return None
        if ts.time() > _clock(session_cfg.get("latest_entry", "11:00")):
            return None
        drive_range = max(0.01, self.opening_high - self.opening_low)
        bar_range = high - low
        aligned = close >= vwap if self.direction == "long" else close <= vwap
        if entry_cfg.get("require_vwap_alignment", True) and not aligned:
            self.compression = []
            self.compression_ready = False
            return None
        max_range = drive_range * float(entry_cfg.get("max_consolidation_pct", .40))
        volume_declines = not self.compression or volume <= self.compression[-1]["volume"]
        qualifies = bar_range <= max_range and (volume_declines or not entry_cfg.get("require_volume_decline", True))
        if self.compression_ready:
            broke = close > self.compression_high if self.direction == "long" else close < self.compression_low
            if broke:
                buffer = float(self.params["risk"].get("stop_buffer_pct", .10)) / 100 * close
                stop = self.compression_low - buffer if self.direction == "long" else self.compression_high + buffer
                risk = close - stop if self.direction == "long" else stop - close
                if risk > 0:
                    target = close + risk * float(self.params["risk"].get("target_multiple_r", 2)) if self.direction == "long" else close - risk * float(self.params["risk"].get("target_multiple_r", 2))
                    self.entry_emitted = True
                    return EntrySignal(self.symbol, self.direction, str(ts), close, stop, target, risk,
                                       self.compression_high, self.compression_low, str(ts), "relative_strength_drive")
            self.compression = []
            self.compression_ready = False
        if qualifies:
            self.compression.append({"high": high, "low": low, "volume": volume})
            needed = int(entry_cfg.get("consolidation_bars", 3))
            if len(self.compression) >= needed:
                self.compression_high = max(item["high"] for item in self.compression)
                self.compression_low = min(item["low"] for item in self.compression)
                self.compression_ready = True
        else:
            self.compression = []
        return None
