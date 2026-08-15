"""Intraday volatility-compression expansion strategy."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import pandas as pd

from fence_bar_strategy import EntrySignal


COMPRESSION_DEFAULTS: dict[str, Any] = {
    "session": {"entry_window_start": "10:00", "entry_window_end": "13:00"},
    "entry": {
        "compression_bars": 3,
        "compression_atr_multiple": 0.60,
        "min_inside_bars": 1,
        "max_move_from_open_pct": 3.0,
        "ema_fast": 9,
        "ema_slow": 20,
        "vwap_slope_lookback": 3,
        "atr_period": 10,
        "require_volume_contraction": True,
    },
    "risk": {"stop_buffer_pct": 0.05, "target_multiple_r": 2.0},
    "exit": {"mode": "fixed_sl_tp", "max_bars": 0},
}


def _clock(value: Any) -> time:
    return datetime.strptime(str(value), "%H:%M").time()


class CompressionStrategy:
    """Trade directional expansion after a narrow intraday compression."""

    def __init__(self, symbol: str, params: dict[str, Any] | None = None):
        self.symbol = symbol.upper()
        self.params = params or COMPRESSION_DEFAULTS
        self.session_date = None
        self.first_open = None
        self.prev_close = None
        self.tr_ranges: list[float] = []
        self.ema_fast = self.ema_slow = None
        self.vwap_values: list[float] = []
        self.vwap_pv = self.vwap_volume = 0.0
        self.compression: list[dict[str, float]] = []
        self.compression_ready = False
        self.compression_high = self.compression_low = None
        self.entry_emitted = False

    def _reset(self, date) -> None:
        self.session_date = date
        self.first_open = self.prev_close = None
        self.tr_ranges = []
        self.ema_fast = self.ema_slow = None
        self.vwap_values = []
        self.vwap_pv = self.vwap_volume = 0.0
        self.compression = []
        self.compression_ready = False
        self.compression_high = self.compression_low = None
        self.entry_emitted = False

    def _ema(self, value: float, previous: float | None, period: int) -> float:
        alpha = 2 / (max(1, period) + 1)
        return value if previous is None else alpha * value + (1 - alpha) * previous

    def on_bar(self, timestamp: Any, bar: pd.Series, index: int) -> EntrySignal | None:
        ts = pd.Timestamp(timestamp)
        if ts.date() != self.session_date:
            self._reset(ts.date())
        high, low, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
        volume = max(0.0, float(bar.get("Volume", 0)))
        if self.first_open is None:
            self.first_open = float(bar["Open"])
        tr = high - low if self.prev_close is None else max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.prev_close = close
        self.tr_ranges.append(tr)
        entry_cfg = self.params["entry"]
        self.ema_fast = self._ema(close, self.ema_fast, int(entry_cfg.get("ema_fast", 9)))
        self.ema_slow = self._ema(close, self.ema_slow, int(entry_cfg.get("ema_slow", 20)))
        self.vwap_pv += ((high + low + close) / 3) * volume
        self.vwap_volume += volume
        vwap = self.vwap_pv / self.vwap_volume if self.vwap_volume else close
        self.vwap_values.append(vwap)
        session = self.params["session"]
        if self.entry_emitted or ts.time() < _clock(session.get("entry_window_start", "10:00")) or ts.time() > _clock(session.get("entry_window_end", "13:00")):
            return None
        atr_period = max(2, int(entry_cfg.get("atr_period", 10)))
        if len(self.tr_ranges) < atr_period:
            return None
        move = abs(close / self.first_open - 1) * 100
        if move > float(entry_cfg.get("max_move_from_open_pct", 3.0)):
            self.compression = []
            self.compression_ready = False
            return None
        lookback = int(entry_cfg.get("vwap_slope_lookback", 3))
        slope_up = lookback <= 0 or (len(self.vwap_values) > lookback and vwap > self.vwap_values[-lookback - 1])
        if self.compression_ready:
            long_trend = close > vwap and self.ema_fast > self.ema_slow and slope_up
            short_trend = close < vwap and self.ema_fast < self.ema_slow and (lookback <= 0 or not slope_up)
            side = "long" if long_trend else "short" if short_trend else None
            broke = side == "long" and close > self.compression_high or side == "short" and close < self.compression_low
            if broke:
                buffer = float(self.params["risk"].get("stop_buffer_pct", .05)) / 100 * close
                stop = self.compression_low - buffer if side == "long" else self.compression_high + buffer
                risk = close - stop if side == "long" else stop - close
                if risk > 0:
                    target = close + risk * float(self.params["risk"].get("target_multiple_r", 2)) if side == "long" else close - risk * float(self.params["risk"].get("target_multiple_r", 2))
                    self.entry_emitted = True
                    return EntrySignal(self.symbol, side, str(ts), close, stop, target, risk,
                                       self.compression_high, self.compression_low, str(ts), "compression_expansion")
            self.compression = []
            self.compression_ready = False
        atr = sum(self.tr_ranges[-atr_period:]) / atr_period
        previous = self.compression[-1] if self.compression else None
        inside = previous is not None and high <= previous["high"] and low >= previous["low"]
        volume_ok = not self.compression or volume <= self.compression[-1]["volume"]
        qualifies = high - low <= atr * float(entry_cfg.get("compression_atr_multiple", .60))
        if entry_cfg.get("require_volume_contraction", True):
            qualifies = qualifies and volume_ok
        if qualifies:
            self.compression.append({"high": high, "low": low, "volume": volume, "inside": float(inside)})
            needed = int(entry_cfg.get("compression_bars", 3))
            inside_count = sum(item["inside"] for item in self.compression)
            if len(self.compression) >= needed and inside_count >= int(entry_cfg.get("min_inside_bars", 1)):
                self.compression_high = max(item["high"] for item in self.compression)
                self.compression_low = min(item["low"] for item in self.compression)
                self.compression_ready = True
        else:
            self.compression = []
        return None
