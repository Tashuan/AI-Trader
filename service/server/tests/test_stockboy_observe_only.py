"""Tests for Phase 7: StockBoy observe-only integration.

Validates that observed runners (ORBRunner) cannot have actions
or overrides applied, but can be identified as observed.
"""

import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent  # service/server/
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


class TestObserveOnlyPolicy(unittest.TestCase):
    """Observe-only runner policy tests."""

    def test_orbrunner_is_observed(self):
        """ORBRunner should be in OBSERVED_RUNNERS."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        self.assertIn('"orbrunner"', source)
        self.assertIn("OBSERVED_RUNNERS", source)

    def test_orbrunner_not_in_controlled(self):
        """ORBRunner should NOT be in CONTROLLED_RUNNERS."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        start = source.index("CONTROLLED_RUNNERS")
        end = source.index("}", start) + 1
        block = source[start:end]
        self.assertNotIn("orbrunner", block)

    def test_observe_only_guard_in_validate_action(self):
        """validate_action should have observe-only guard."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        self.assertIn("is_observed_runner(request.runner_key)", source)
        self.assertIn("observe_only", source)

    def test_observe_only_guard_in_validate_override(self):
        """validate_override should have observe-only guard."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        override_start = source.index("def validate_override")
        override_section = source[override_start:]
        self.assertIn("is_observed_runner", override_section)

    def test_is_observed_runner_function_exists(self):
        """is_observed_runner function should exist in source."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        self.assertIn("def is_observed_runner", source)

    def test_is_actionable_runner_function_exists(self):
        """is_actionable_runner function should exist in source."""
        policy_path = SERVER_DIR / "stockboy_policy.py"
        source = policy_path.read_text()
        self.assertIn("def is_actionable_runner", source)


if __name__ == "__main__":
    unittest.main()
