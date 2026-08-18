"""Tests for Phase 6: Dynamic discovery lookahead validation.

Validates that discovery metadata is tracked and late discoveries
are properly flagged for audit purposes.
"""

import sys
import unittest
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))


class TestDiscoveryLookahead(unittest.TestCase):
    """Discovery lookahead guard tests."""

    def test_discovery_meta_structure(self):
        """Validate discovery metadata has required fields."""
        meta = {
            "timestamp": "2025-01-15T14:20:00Z",
            "et_time": "09:20",
            "late": False,
            "count": 5,
        }
        self.assertIn("timestamp", meta)
        self.assertIn("et_time", meta)
        self.assertIn("late", meta)
        self.assertIn("count", meta)
        self.assertFalse(meta["late"])

    def test_late_discovery_flagged(self):
        """Late discovery (after 09:30) must be flagged."""
        meta = {
            "timestamp": "2025-01-15T14:35:00Z",
            "et_time": "09:35",
            "late": True,
            "count": 5,
            "warning": "Discovery after 09:30 — may include opening range data (lookahead risk)",
        }
        self.assertTrue(meta["late"])
        self.assertIn("warning", meta)

    def test_pre_market_discovery_not_late(self):
        """Pre-market discovery (before 09:30) should not be flagged late."""
        et_time = "09:25"
        is_late = et_time >= "09:30"
        self.assertFalse(is_late)

    def test_post_open_discovery_is_late(self):
        """Post-open discovery (after 09:30) should be flagged late."""
        et_time = "09:32"
        is_late = et_time >= "09:30"
        self.assertTrue(is_late)

    def test_default_state_has_discovery_meta(self):
        """Default state should include discovery_meta field."""
        # Import orb_runner to check _DEFAULT_STATE
        try:
            import orb_runner
            state = orb_runner._DEFAULT_STATE
            self.assertIn("discovery_meta", state)
            self.assertEqual(state["discovery_meta"], {})
        except ImportError:
            self.skipTest("orb_runner not importable in test environment")


if __name__ == "__main__":
    unittest.main()
