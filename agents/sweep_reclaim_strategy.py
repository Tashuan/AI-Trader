"""Prior-day liquidity sweep and reclaim strategy."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


SWEEP_RECLAIM_DEFAULTS: dict[str, Any] = {
    "session": {"market_open": "09:30", "latest_entry": "11:30"},
    "entry": {
        "min_sweep_pct": 0.10,
        "max_sweep_pct": 0.75,
        "reclaim_window_bars": 2,
        "volume_multiple": 1.5,
        "use_vwap_target": True,
        "min_vwap_r": 1.5,
    },
    "risk": {"stop_buffer_pct": 0.15, "target_multiple_r": 2.0},
    "exit": {"mode": "fixed_sl_tp", "max_bars": 0},
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class SweepReclaimStrategy:
    """Trade a failed break of a prior-day high or low."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None,
                 prior_levels: dict[str, float] | None = None):
        self.symbol = symbol.upper()
        self.params = params or SWEEP_RECLAIM_DEFAULTS
        self.prior_levels = prior_levels
        self.session_date = None
        self.sweep_side = None
        self.sweep_extreme = None
        self.sweep_index = None
        self.used_sides: set[str] = set()
        self.volumes: list[float] = []
        self.pv = 0.0
        self.total_volume = 0.0
        self.entry_emitted = False

    def _reset(self, date) -> None:
        self.session_date = date
        self.sweep_side = self.sweep_extreme = self.sweep_index = None
        self.used_sides = set()
        self.volumes = []
        self.pv = self.total_volume = 0.0
        self.entry_emitted = False

    def _vwap(self, high: float, low: float, close: float, volume: float) -> float:
        self.pv += ((high + low + close) / 3) * volume
        self.total_volume += volume
        return self.pv / self.total_volume if self.total_volume else close

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        if ts.date() != self.session_date:
            self._reset(ts.date())
        if not self.prior_levels:
            return None
        high, low, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
        volume = max(0.0, float(bar.get("Volume", 0)))
        vwap = self._vwap(high, low, close, volume)
        self.volumes.append(volume)
        cfg = self.params["entry"]
        if self.entry_emitted or ts.time() > _clock(self.params["session"].get("latest_entry", "11:30")):
            return None
        median_volume = float(pd.Series(self.volumes[-6:-1]).median()) if len(self.volumes) > 1 else 0.0
        volume_ok = median_volume <= 0 or volume >= median_volume * float(cfg.get("volume_multiple", 1.5))
        min_depth = float(cfg.get("min_sweep_pct", .10))
        max_depth = float(cfg.get("max_sweep_pct", .75))

        if self.sweep_side is None:
            below = (self.prior_levels["Low"] - low) / self.prior_levels["Low"] * 100
            above = (high - self.prior_levels["High"]) / self.prior_levels["High"] * 100
            if "long" not in self.used_sides and min_depth <= below <= max_depth:
                self.sweep_side, self.sweep_extreme, self.sweep_index = "long", low, index
            elif "short" not in self.used_sides and min_depth <= above <= max_depth:
                self.sweep_side, self.sweep_extreme, self.sweep_index = "short", high, index
            else:
                return None

        if self.sweep_side == "long":
            self.sweep_extreme = min(self.sweep_extreme, low)
            reclaimed = close > self.prior_levels["Low"]
            level = self.prior_levels["Low"]
        else:
            self.sweep_extreme = max(self.sweep_extreme, high)
            reclaimed = close < self.prior_levels["High"]
            level = self.prior_levels["High"]
        elapsed = index - self.sweep_index
        if elapsed <= int(cfg.get("reclaim_window_bars", 2)) and reclaimed and volume_ok:
            side = self.sweep_side
            buffer = float(self.params["risk"].get("stop_buffer_pct", .15)) / 100 * close
            stop = self.sweep_extreme - buffer if side == "long" else self.sweep_extreme + buffer
            risk = close - stop if side == "long" else stop - close
            fixed_target = close + risk * float(self.params["risk"].get("target_multiple_r", 2)) if side == "long" else close - risk * float(self.params["risk"].get("target_multiple_r", 2))
            vwap_target_ok = (vwap - close >= risk * float(cfg.get("min_vwap_r", 1.5))) if side == "long" else (close - vwap >= risk * float(cfg.get("min_vwap_r", 1.5)))
            target = vwap if cfg.get("use_vwap_target", True) and vwap_target_ok else fixed_target
            if risk > 0 and ((side == "long" and target > close) or (side == "short" and target < close)):
                self.entry_emitted = True
                return EntrySignal(self.symbol, side, str(ts), close, stop, target, risk,
                                   self.prior_levels["High"], self.prior_levels["Low"], str(ts),
                                   f"sweep_reclaim_{side}")
        if elapsed > int(cfg.get("reclaim_window_bars", 2)):
            self.used_sides.add(self.sweep_side)
            self.sweep_side = self.sweep_extreme = self.sweep_index = None
        return None
