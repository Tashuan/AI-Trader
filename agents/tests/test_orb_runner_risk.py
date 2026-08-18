"""Tests for ORBRunner paper-only risk guardrails."""

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from orb_runner import (
    ORB_CONFIG,
    _elapsed_minutes,
    apply_platform_runtime_config,
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
            "discovery_mode": "fixed",
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
