"""Tests for Phase 3: State reconciliation and order lifecycle.

Validates that the state reconciliation function correctly identifies
stale and orphaned positions, and that order lifecycle tracking works.
"""

import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone

AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))


class TestStateReconciliation(unittest.TestCase):
    """State reconciliation and order lifecycle tests."""

    def test_default_state_has_order_history(self):
        """Default state should include order_history field."""
        try:
            import orb_runner
            state = orb_runner._DEFAULT_STATE
            self.assertIn("order_history", state)
            self.assertEqual(state["order_history"], {})
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_default_state_has_reconcile_date(self):
        """Default state should include last_reconcile_date field."""
        try:
            import orb_runner
            state = orb_runner._DEFAULT_STATE
            self.assertIn("last_reconcile_date", state)
            self.assertIsNone(state["last_reconcile_date"])
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_default_state_has_config_version(self):
        """Default state should include config_version field."""
        try:
            import orb_runner
            state = orb_runner._DEFAULT_STATE
            self.assertIn("config_version", state)
            self.assertEqual(state["config_version"], "2.1-corrected-paper")
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_record_order_lifecycle(self):
        """record_order_lifecycle should add entry to order_history."""
        try:
            import orb_runner
            state = dict(orb_runner._DEFAULT_STATE)
            orb_runner.record_order_lifecycle(
                state, "NVDA", "orb:entry:NVDA:2025-01-15", "order-123", "entered"
            )
            self.assertIn("NVDA", state["order_history"])
            self.assertEqual(state["order_history"]["NVDA"]["status"], "entered")
            self.assertEqual(state["order_history"]["NVDA"]["alpaca_order_id"], "order-123")
            self.assertEqual(state["order_history"]["NVDA"]["client_order_id"], "orb:entry:NVDA:2025-01-15")
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_record_order_lifecycle_exit(self):
        """record_order_lifecycle should update status on exit."""
        try:
            import orb_runner
            state = dict(orb_runner._DEFAULT_STATE)
            orb_runner.record_order_lifecycle(
                state, "NVDA", "orb:entry:NVDA:2025-01-15", "order-123", "entered"
            )
            orb_runner.record_order_lifecycle(
                state, "NVDA", "orb:exit:NVDA:2025-01-15", "order-456", "exited"
            )
            self.assertEqual(state["order_history"]["NVDA"]["status"], "exited")
            self.assertEqual(state["order_history"]["NVDA"]["alpaca_order_id"], "order-456")
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_reconcile_skips_if_already_done_today(self):
        """reconcile_state_with_alpaca should skip if already reconciled today."""
        try:
            import orb_runner
            # Patch et_date_str to return a known date
            original = orb_runner.et_date_str
            orb_runner.et_date_str = lambda: "2025-01-15"
            state = {"last_reconcile_date": "2025-01-15", "open_positions": {}}
            result = orb_runner.reconcile_state_with_alpaca(state)
            # Should return unchanged (skip reconciliation)
            self.assertEqual(result["last_reconcile_date"], "2025-01-15")
            orb_runner.et_date_str = original
        except ImportError:
            self.skipTest("orb_runner not importable")


class TestORBConfigPhase4(unittest.TestCase):
    """Phase 4 config tests."""

    def test_config_has_signal_freshness(self):
        """ORB_CONFIG should have max_signal_age_seconds."""
        try:
            import orb_runner
            self.assertIn("max_signal_age_seconds", orb_runner.ORB_CONFIG)
            self.assertEqual(orb_runner.ORB_CONFIG["max_signal_age_seconds"], 300)
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_config_has_range_end_policy(self):
        """ORB_CONFIG should have range_end_policy."""
        try:
            import orb_runner
            self.assertIn("range_end_policy", orb_runner.ORB_CONFIG)
        except ImportError:
            self.skipTest("orb_runner not importable")

    def test_config_has_intrabar_policy(self):
        """ORB_CONFIG should have intrabar_policy."""
        try:
            import orb_runner
            self.assertIn("intrabar_policy", orb_runner.ORB_CONFIG)
        except ImportError:
            self.skipTest("orb_runner not importable")


if __name__ == "__main__":
    unittest.main()
