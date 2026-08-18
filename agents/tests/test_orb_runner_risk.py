"""Tests for ORBRunner paper-only risk guardrails."""

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
RESEARCH_DIR = AGENTS_DIR.parent / "research" / "strategy_search"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from orb_options_bs_backtester import dynamic_entry_budget
from orb_runner import (
    ORB_CONFIG,
    ORBSignal,
    _elapsed_minutes,
    _entry_sizing,
    _reserve_entry_budget,
    apply_platform_runtime_config,
    execute_entry,
    update_risk_state,
)


class TestORBRunnerConfig(unittest.TestCase):
    def test_uses_corrected_paper_strategy(self):
        expected = {
            "range_minutes": 5,
            "range_end_policy": "exclusive",
            "confirmation_bars": 2,
            "skip_first_post_range_bar": True,
            "stop_pct": 1.0,
            "target_pct": 2.0,
            "latest_entry": "10:00",
            "max_positions": 4,
            "position_pct": 3.0,
            "strategy_mode": "symmetric_otm",
            "intrabar_policy": "conservative",
            "discovery_mode": "dynamic",
            "dynamic_sizing": True,
            "max_position_pct": 6.0,
            "max_total_pct": 12.0,
            "min_option_entry_price": 0.20,
            "paper_only": True,
            "shadow_mode": True,
        }
        for key, value in expected.items():
            self.assertEqual(ORB_CONFIG[key], value, key)

    def test_confirmation_uses_market_time_not_poll_count(self):
        elapsed = _elapsed_minutes(
            "2026-08-17T09:36:00",
            "2026-08-17T09:45:00",
        )
        self.assertEqual(elapsed, 9.0)

    def test_platform_watchlist_cannot_replace_strategy_universe(self):
        symbols = ["NVDA", "TSLA", "AAPL", "COIN"]
        result = apply_platform_runtime_config(
            {"watchlist": ["META"], "poll_interval": 5}, symbols
        )
        self.assertEqual(result, symbols)


class TestDynamicSizing(unittest.TestCase):
    def test_caps_each_trade_and_preserves_remaining_total_budget(self):
        self.assertEqual(dynamic_entry_budget(10000, 0, 6, 12), 600)
        self.assertEqual(dynamic_entry_budget(10000, 600, 6, 12), 600)
        self.assertEqual(dynamic_entry_budget(10000, 1200, 6, 12), 0)

    def test_closed_positions_do_not_release_daily_budget(self):
        self.assertEqual(dynamic_entry_budget(10000, 900, 6, 12), 300)

    def test_runner_reserves_two_dynamic_entries_then_exhausts_cap(self):
        state = {"risk_state": {"day_start_equity": 10000.0}}
        first = _entry_sizing(ORB_CONFIG, 10000.0, state, "2026-08-18")
        self.assertEqual(first["allocated_budget"], 600.0)
        _reserve_entry_budget(state, "2026-08-18", first["allocated_budget"])
        second = _entry_sizing(ORB_CONFIG, 10000.0, state, "2026-08-18")
        self.assertEqual(second["allocated_budget"], 600.0)
        _reserve_entry_budget(state, "2026-08-18", second["allocated_budget"])
        third = _entry_sizing(ORB_CONFIG, 10000.0, state, "2026-08-18")
        self.assertEqual(third["allocated_budget"], 0.0)

    def test_shadow_signal_contains_sizing_metadata_without_order(self):
        state = {"risk_state": {"day_start_equity": 10000.0}}
        signal = ORBSignal("RIOT", "long", 19.8, 19.6, 20.2, "09:37", "call")
        self.assertFalse(execute_entry(None, signal, ORB_CONFIG, 10000.0, state))
        today = next(iter(state["shadow_signals"]))
        logged = state["shadow_signals"][today][0]
        self.assertEqual(logged["sizing"]["allocated_budget"], 600.0)
        self.assertEqual(logged["sizing"]["allocation_pct"], 6.0)
        self.assertEqual(state["sizing_state"][today]["deployed"], 600.0)


class TestORBRiskState(unittest.TestCase):
    def test_initializes_day_and_peak_equity(self):
        state = {}
        update_risk_state(state, 10000.0, "2026-08-17", {})
        self.assertEqual(state["risk_state"]["day_start_equity"], 10000.0)
        self.assertEqual(state["risk_state"]["peak_equity"], 10000.0)

    def test_daily_loss_halts_at_configured_limit(self):
        state = {}
        update_risk_state(state, 10000.0, "2026-08-17", {"daily_loss_limit_pct": 10.0})
        update_risk_state(state, 9000.0, "2026-08-17", {"daily_loss_limit_pct": 10.0})
        self.assertEqual(state["risk_state"]["daily_halt_reason"], "daily_loss_limit_10%")

    def test_drawdown_halt_tracks_peak_across_days(self):
        state = {}
        config = {"max_drawdown_limit_pct": 30.0}
        update_risk_state(state, 10000.0, "2026-08-17", config)
        update_risk_state(state, 13000.0, "2026-08-18", config)
        update_risk_state(state, 9000.0, "2026-08-19", config)
        self.assertEqual(state["risk_state"]["rolling_halt_reason"], "drawdown_limit_30%")


if __name__ == "__main__":
    unittest.main()
