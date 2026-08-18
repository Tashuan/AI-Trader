"""Tests for the canonical ORB strategy core (agents/orb_strategy.py).

These tests validate the pure strategy logic without any network or broker
dependencies.  They ensure that signal generation, exit logic, strike
selection, and ranking are deterministic and match the documented behavior.
"""

import sys
import unittest
from datetime import datetime, time as dt_time
from pathlib import Path

# Ensure agents/ is importable
AGENTS_DIR = Path(__file__).resolve().parent.parent
RESEARCH_DIR = AGENTS_DIR.parent / "research" / "strategy_search"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from orb_strategy import (
    ORBStrategyConfig, ORBRange, ORBSignal,
    OpeningRangeBuilder, BreakoutChecker,
    select_strike, validate_strike, rank_signals, check_exit,
    find_expiration,
    StrategyMode, ExecutionMode, IntrabarPolicy, RangeEndPolicy, DiscoveryMode,
    STRIKE_STEPS,
)
from orb_options_bs_backtester import option_fill_price


def _bar(ts: str, o: float, h: float, l: float, c: float) -> dict:
    """Make a 1-minute bar dict."""
    return {"Timestamp": ts, "Open": o, "High": h, "Low": l, "Close": c}


class TestORBStrategyConfig(unittest.TestCase):
    """Config immutability and versioning."""

    def test_default_config_is_symmetric_otm(self):
        cfg = ORBStrategyConfig()
        self.assertEqual(cfg.strategy_mode, StrategyMode.SYMMETRIC_OTM)
        self.assertEqual(cfg.config_version, "2.0")

    def test_legacy_config_reproduces_historical_behavior(self):
        cfg = ORBStrategyConfig.legacy()
        self.assertEqual(cfg.strategy_mode, StrategyMode.LEGACY_PLUS_STRIKE)
        self.assertEqual(cfg.range_end_policy, RangeEndPolicy.INCLUSIVE)
        self.assertEqual(cfg.intrabar_policy, IntrabarPolicy.LEGACY)
        self.assertEqual(cfg.config_version, "1.0-legacy")

    def test_config_is_frozen(self):
        cfg = ORBStrategyConfig()
        with self.assertRaises(Exception):
            cfg.stop_pct = 2.0  # type: ignore

    def test_to_dict_and_from_dict_roundtrip(self):
        cfg = ORBStrategyConfig(stop_pct=2.5, target_pct=4.0)
        d = cfg.to_dict()
        cfg2 = ORBStrategyConfig.from_dict(d)
        self.assertEqual(cfg2.stop_pct, 2.5)
        self.assertEqual(cfg2.target_pct, 4.0)

    def test_from_dict_ignores_unknown_keys(self):
        cfg = ORBStrategyConfig.from_dict({"stop_pct": 3.0, "unknown_key": "ignore"})
        self.assertEqual(cfg.stop_pct, 3.0)


class TestOpeningRangeBuilder(unittest.TestCase):
    """Opening range construction from 1-minute bars."""

    def test_exclusive_policy_5min_range(self):
        """EXCLUSIVE: 09:30-09:34 are range bars, 09:35 is first breakout."""
        cfg = ORBStrategyConfig(range_end_policy=RangeEndPolicy.EXCLUSIVE)
        builder = OpeningRangeBuilder(cfg)
        bars = [
            _bar("2025-01-15 09:30:00", 100, 101, 99, 100),
            _bar("2025-01-15 09:31:00", 100, 102, 99, 101),
            _bar("2025-01-15 09:32:00", 101, 103, 100, 102),
            _bar("2025-01-15 09:33:00", 102, 103, 101, 102),
            _bar("2025-01-15 09:34:00", 102, 104, 101, 103),
            _bar("2025-01-15 09:35:00", 103, 105, 102, 104),
        ]
        r = builder.build("NVDA", bars)
        self.assertIsNotNone(r)
        self.assertEqual(r.range_high, 104)
        self.assertEqual(r.range_low, 99)
        self.assertEqual(r.bar_count, 5)

    def test_inclusive_policy_5min_range(self):
        """INCLUSIVE: 09:30-09:35 are range bars (legacy behavior)."""
        cfg = ORBStrategyConfig(range_end_policy=RangeEndPolicy.INCLUSIVE)
        builder = OpeningRangeBuilder(cfg)
        bars = [
            _bar("2025-01-15 09:30:00", 100, 101, 99, 100),
            _bar("2025-01-15 09:31:00", 100, 102, 99, 101),
            _bar("2025-01-15 09:32:00", 101, 103, 100, 102),
            _bar("2025-01-15 09:33:00", 102, 103, 101, 102),
            _bar("2025-01-15 09:34:00", 102, 104, 101, 103),
            _bar("2025-01-15 09:35:00", 103, 105, 102, 104),
        ]
        r = builder.build("NVDA", bars)
        self.assertIsNotNone(r)
        self.assertEqual(r.bar_count, 6)
        self.assertEqual(r.range_high, 105)

    def test_empty_bars_returns_none(self):
        cfg = ORBStrategyConfig()
        builder = OpeningRangeBuilder(cfg)
        self.assertIsNone(builder.build("NVDA", []))

    def test_pre_market_bars_ignored(self):
        cfg = ORBStrategyConfig()
        builder = OpeningRangeBuilder(cfg)
        bars = [
            _bar("2025-01-15 09:28:00", 100, 101, 99, 100),
            _bar("2025-01-15 09:29:00", 100, 101, 99, 100),
            _bar("2025-01-15 09:30:00", 100, 101, 99, 100),
            _bar("2025-01-15 09:31:00", 100, 102, 99, 101),
            _bar("2025-01-15 09:32:00", 101, 103, 100, 102),
            _bar("2025-01-15 09:33:00", 102, 103, 101, 102),
            _bar("2025-01-15 09:34:00", 102, 104, 101, 103),
        ]
        r = builder.build("NVDA", bars)
        self.assertIsNotNone(r)
        self.assertEqual(r.bar_count, 5)


class TestBreakoutChecker(unittest.TestCase):
    """Breakout signal generation."""

    def _make_range(self) -> ORBRange:
        return ORBRange(
            symbol="NVDA", range_high=104, range_low=99,
            range_start_ts=datetime(2025, 1, 15, 9, 30),
            range_end_ts=datetime(2025, 1, 15, 9, 34),
            bar_count=5,
        )

    def test_long_breakout_above_range_high(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "long")
        self.assertEqual(sig.option_type, "call")
        self.assertAlmostEqual(sig.entry_price, 104.5)
        self.assertAlmostEqual(sig.stop_price, 104.5 * 0.99)
        self.assertAlmostEqual(sig.target_price, 104.5 * 1.015)

    def test_short_breakout_below_range_low(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 100, 101, 97, 98.5)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "short")
        self.assertEqual(sig.option_type, "put")
        self.assertAlmostEqual(sig.entry_price, 98.5)
        self.assertAlmostEqual(sig.stop_price, 98.5 * 1.01)
        self.assertAlmostEqual(sig.target_price, 98.5 * 0.985)

    def test_two_bar_confirmation_requires_two_closes(self):
        cfg = ORBStrategyConfig(confirmation_bars=2)
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        first = checker.check("NVDA", _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5), r)
        second = checker.check("NVDA", _bar("2025-01-15 09:36:00", 104, 106, 103, 105.5), r)
        self.assertIsNone(first)
        self.assertIsNotNone(second)

    def test_skip_first_post_range_bar(self):
        cfg = ORBStrategyConfig(skip_first_post_range_bar=True)
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        first = checker.check("NVDA", _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5), r)
        second = checker.check("NVDA", _bar("2025-01-15 09:36:00", 104, 106, 103, 105.5), r)
        self.assertIsNone(first)
        self.assertIsNotNone(second)

    def test_no_breakout_inside_range(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 101, 102, 100, 101)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNone(sig)

    def test_wick_breakout_does_not_trigger(self):
        """Only closes outside the range trigger signals."""
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        # High wicks above range but close is inside
        bar = _bar("2025-01-15 09:35:00", 101, 106, 100, 102)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNone(sig)

    def test_one_signal_per_symbol_per_session(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar1 = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        bar2 = _bar("2025-01-15 09:36:00", 104, 106, 103, 105.5)
        sig1 = checker.check("NVDA", bar1, r)
        sig2 = checker.check("NVDA", bar2, r)
        self.assertIsNotNone(sig1)
        self.assertIsNone(sig2)

    def test_duplicate_bar_timestamp_ignored(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        sig1 = checker.check("NVDA", bar, r)
        sig2 = checker.check("NVDA", bar, r)
        self.assertIsNotNone(sig1)
        self.assertIsNone(sig2)

    def test_signal_before_range_end_ignored(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:34:00", 103, 105, 102, 104.5)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNone(sig)

    def test_signal_after_latest_entry_ignored(self):
        cfg = ORBStrategyConfig(latest_entry="09:40")
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:41:00", 103, 105, 102, 104.5)
        sig = checker.check("NVDA", bar, r)
        self.assertIsNone(sig)

    def test_stale_signal_rejected(self):
        cfg = ORBStrategyConfig(max_signal_age_seconds=30)
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        current = datetime(2025, 1, 15, 9, 36, 30)
        sig = checker.check("NVDA", bar, r, current_ts=current)
        self.assertIsNone(sig)

    def test_fresh_signal_accepted(self):
        cfg = ORBStrategyConfig(max_signal_age_seconds=120)
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        current = datetime(2025, 1, 15, 9, 36, 0)
        sig = checker.check("NVDA", bar, r, current_ts=current)
        self.assertIsNotNone(sig)

    def test_reset_clears_state(self):
        cfg = ORBStrategyConfig()
        checker = BreakoutChecker(cfg)
        r = self._make_range()
        bar1 = _bar("2025-01-15 09:35:00", 103, 105, 102, 104.5)
        sig1 = checker.check("NVDA", bar1, r)
        self.assertIsNotNone(sig1)
        checker.reset()
        bar2 = _bar("2025-01-15 09:36:00", 104, 106, 103, 105.5)
        sig2 = checker.check("NVDA", bar2, r)
        self.assertIsNotNone(sig2)


class TestSelectStrike(unittest.TestCase):
    """Option strike selection."""

    def test_symmetric_otm_call(self):
        cfg = ORBStrategyConfig(strategy_mode=StrategyMode.SYMMETRIC_OTM)
        strike = select_strike(spot=100, option_type="call", symbol="NVDA", config=cfg)
        step = STRIKE_STEPS["NVDA"]
        atm = round(100 / step) * step
        self.assertEqual(strike, atm + step)

    def test_symmetric_otm_put(self):
        cfg = ORBStrategyConfig(strategy_mode=StrategyMode.SYMMETRIC_OTM)
        strike = select_strike(spot=100, option_type="put", symbol="NVDA", config=cfg)
        step = STRIKE_STEPS["NVDA"]
        atm = round(100 / step) * step
        self.assertEqual(strike, atm - step)

    def test_legacy_plus_strike_call(self):
        cfg = ORBStrategyConfig.legacy()
        strike = select_strike(spot=100, option_type="call", symbol="NVDA", config=cfg)
        step = STRIKE_STEPS["NVDA"]
        atm = round(100 / step) * step
        self.assertEqual(strike, atm + step)

    def test_legacy_plus_strike_put(self):
        """Legacy mode: puts also use ATM+offset (the known asymmetry)."""
        cfg = ORBStrategyConfig.legacy()
        strike = select_strike(spot=100, option_type="put", symbol="NVDA", config=cfg)
        step = STRIKE_STEPS["NVDA"]
        atm = round(100 / step) * step
        self.assertEqual(strike, atm + step)

    def test_validate_strike_on_grid(self):
        self.assertTrue(validate_strike(100.0, "NVDA", "call"))
        self.assertTrue(validate_strike(102.5, "NVDA", "call"))

    def test_validate_strike_off_grid(self):
        self.assertFalse(validate_strike(101.3, "NVDA", "call"))


class TestRankSignals(unittest.TestCase):
    """Signal ranking for position admission."""

    def test_wider_range_ranks_first(self):
        sig1 = ORBSignal(
            symbol="AAPL", side="long", option_type="call",
            entry_price=150, stop_price=148, target_price=153,
            signal_ts=datetime(2025, 1, 15, 9, 35),
            range_high=151, range_low=149, range_width=2,
        )
        sig2 = ORBSignal(
            symbol="NVDA", side="long", option_type="call",
            entry_price=100, stop_price=99, target_price=101.5,
            signal_ts=datetime(2025, 1, 15, 9, 36),
            range_high=101, range_low=95, range_width=6,
        )
        ranked = rank_signals([sig1, sig2])
        self.assertEqual(ranked[0].symbol, "NVDA")
        self.assertEqual(ranked[1].symbol, "AAPL")

    def test_earlier_timestamp_breaks_tie(self):
        sig1 = ORBSignal(
            symbol="AAPL", side="long", option_type="call",
            entry_price=150, stop_price=148, target_price=153,
            signal_ts=datetime(2025, 1, 15, 9, 36),
            range_high=151, range_low=149, range_width=2,
        )
        sig2 = ORBSignal(
            symbol="NVDA", side="long", option_type="call",
            entry_price=100, stop_price=99, target_price=101.5,
            signal_ts=datetime(2025, 1, 15, 9, 35),
            range_high=101, range_low=99, range_width=2,
        )
        ranked = rank_signals([sig1, sig2])
        self.assertEqual(ranked[0].symbol, "NVDA")


class TestCheckExit(unittest.TestCase):
    """Exit logic: stop loss, take profit, intrabar conflict."""

    def test_long_take_profit(self):
        result = check_exit(
            side="long", current_high=105, current_low=100,
            stop_price=98, target_price=104,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertEqual(result, "take_profit")

    def test_long_stop_loss(self):
        result = check_exit(
            side="long", current_high=102, current_low=97,
            stop_price=98, target_price=104,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertEqual(result, "stop_loss")

    def test_short_take_profit(self):
        result = check_exit(
            side="short", current_high=100, current_low=95,
            stop_price=102, target_price=96,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertEqual(result, "take_profit")

    def test_short_stop_loss(self):
        result = check_exit(
            side="short", current_high=103, current_low=100,
            stop_price=102, target_price=96,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertEqual(result, "stop_loss")

    def test_conservative_intrabar_conflict_stop_first(self):
        """When both stop and target are touched, conservative = stop first."""
        result = check_exit(
            side="long", current_high=105, current_low=97,
            stop_price=98, target_price=104,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertEqual(result, "stop_loss")

    def test_legacy_intrabar_conflict_target_first(self):
        """Legacy policy: target first when both touched."""
        result = check_exit(
            side="long", current_high=105, current_low=97,
            stop_price=98, target_price=104,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.LEGACY,
        )
        self.assertEqual(result, "take_profit")

    def test_stop_ignored_during_confirmation(self):
        """Stops are not checked during confirmation period."""
        result = check_exit(
            side="long", current_high=102, current_low=97,
            stop_price=98, target_price=104,
            in_confirmation=True,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertIsNone(result)

    def test_no_exit_when_neither_touched(self):
        result = check_exit(
            side="long", current_high=102, current_low=100,
            stop_price=98, target_price=104,
            in_confirmation=False,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.assertIsNone(result)


class TestOptionFillModel(unittest.TestCase):
    """Spread and adverse-slippage fill assumptions."""

    def test_entry_pays_ask_plus_slippage(self):
        self.assertAlmostEqual(option_fill_price(100.0, True, 50, 100), 101.0)

    def test_exit_receives_bid_minus_slippage(self):
        self.assertAlmostEqual(option_fill_price(100.0, False, 50, 100), 99.0)


class TestFindExpiration(unittest.TestCase):
    """Expiration date finder."""

    def test_finds_nearest_friday(self):
        # Monday Jan 13, 2025 → Friday Jan 17 (4 days)
        result = find_expiration("2025-01-13", dte_min=1, dte_max=14)
        self.assertEqual(result, "2025-01-17")

    def test_skips_too_close_expiration(self):
        # Friday Jan 17, 2025 → next Friday Jan 24 (7 days)
        result = find_expiration("2025-01-17", dte_min=2, dte_max=14)
        self.assertEqual(result, "2025-01-24")

    def test_returns_none_if_too_far(self):
        result = find_expiration("2025-01-13", dte_min=1, dte_max=3)
        # Jan 17 is 4 days out → exceeds dte_max=3
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
