"""Unit tests for the pure Fence Bar state machine."""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from fence_bar_strategy import FenceBarStrategy, FENCE_BAR_DEFAULTS
from strategy_lab import deep_merge


def _bar(ts, o, h, l, c, volume=10000):
    return pd.Series({"Open": o, "High": h, "Low": l, "Close": c, "Volume": volume}, name=pd.Timestamp(ts))


def _params():
    return deep_merge(FENCE_BAR_DEFAULTS, {
        "anchor": {"enabled": False},
        "fence": {"min_range_pct": 0.1, "max_range_pct": 10.0},
    })


def test_bullish_breakout_retest_emits_two_r_signal():
    strategy = FenceBarStrategy("QQQ", _params())
    bars = [
        ("2026-03-25 09:30", 100, 102, 98, 101),
        ("2026-03-25 09:35", 102.1, 104, 102.0, 103),
        ("2026-03-25 09:40", 102.5, 103.5, 101.5, 102.5),
    ]
    signals = [strategy.on_bar(ts, _bar(ts, o, h, l, c), i) for i, (ts, o, h, l, c) in enumerate(bars)]
    signal = signals[-1]
    assert signal is not None
    assert signal.side == "long"
    assert signal.entry_price == 102.5
    assert signal.stop_price == 100.0
    assert signal.target_price == 107.5
    assert strategy.state == "POSITION_OPEN"


def test_bearish_breakout_retest_emits_short_signal():
    strategy = FenceBarStrategy("SPY", _params())
    bars = [
        ("2026-03-25 09:30", 100, 102, 98, 99),
        ("2026-03-25 09:35", 97.9, 98.0, 96, 97),
        ("2026-03-25 09:40", 97.5, 98.5, 96.5, 97.5),
    ]
    signals = [strategy.on_bar(ts, _bar(ts, o, h, l, c), i) for i, (ts, o, h, l, c) in enumerate(bars)]
    signal = signals[-1]
    assert signal is not None
    assert signal.side == "short"
    assert signal.stop_price == 100.0
    assert signal.target_price == 92.5


def test_wick_without_close_outside_is_not_a_retest():
    strategy = FenceBarStrategy("QQQ", _params())
    bars = [
        ("2026-03-25 09:30", 100, 102, 98, 101),
        ("2026-03-25 09:35", 102.1, 104, 102, 103),
        ("2026-03-25 09:40", 102.5, 103, 101, 101.8),
    ]
    signals = [strategy.on_bar(ts, _bar(ts, o, h, l, c), i) for i, (ts, o, h, l, c) in enumerate(bars)]
    assert signals[-1] is None
    assert strategy.state == "WAIT_FOR_RETEST"


def test_invalid_fence_range_disables_session():
    params = deep_merge(FENCE_BAR_DEFAULTS, {"fence": {"min_range_pct": 5.0, "max_range_pct": 6.0}})
    strategy = FenceBarStrategy("QQQ", params)
    bar = _bar("2026-03-25 09:30", 100, 102, 98, 101)
    assert strategy.on_bar(bar.name, bar, 0) is None
    assert strategy.state == "DONE_FOR_DAY"


def test_new_session_resets_state():
    strategy = FenceBarStrategy("QQQ", _params())
    first = _bar("2026-03-25 09:30", 100, 102, 98, 101)
    strategy.on_bar(first.name, first, 0)
    next_day = _bar("2026-03-26 09:30", 200, 202, 198, 201)
    strategy.on_bar(next_day.name, next_day, 0)
    assert strategy.session_date == "2026-03-26"
    assert strategy.fence is not None
    assert strategy.fence.high == 202
