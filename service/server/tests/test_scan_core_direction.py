import sys
from pathlib import Path
import unittest

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

import scan_core


class ScanCoreDirectionTests(unittest.TestCase):
    @staticmethod
    def _precomputed(bullish: bool) -> dict:
        n = 31
        def values(value):
            return [value] * n
        if bullish:
            return {
                "n": n, "close": values(100), "vol_ratio": values(2),
                "atr": values(1), "bb_width": values(0.1), "bb_squeeze": values(1),
                "sma20": values(103), "sma50": values(102), "sma200": values(101),
                "ema20": values(99), "macd_hist": values(1), "rsi": values(60),
                "stoch_k": values(70), "stoch_d": values(50), "obv_div": values(False),
                "vwap": values(99), "body_ratio": values(0.8),
                "consolidation_bo": values(True), "ret_1h": values(1),
            }
        return {
            "n": n, "close": values(100), "vol_ratio": values(2),
            "atr": values(1), "bb_width": values(0.1), "bb_squeeze": values(1),
            "sma20": values(97), "sma50": values(98), "sma200": values(99),
            "ema20": values(101), "macd_hist": values(-1), "rsi": values(20),
            "stoch_k": values(20), "stoch_d": values(40), "obv_div": values(False),
            "vwap": values(101), "body_ratio": values(0.1),
            "consolidation_bo": values(False), "ret_1h": values(-1),
        }

    def test_direction_mode_filters_bearish_signal_for_long_only(self):
        result = scan_core.deep_scan_from_precomputed(
            "TEST", self._precomputed(False), 30,
            {"entry_criteria": {"min_signals": 4, "min_signal_families": 2,
                                 "min_vol_ratio": 1.5, "direction_mode": "long"}},
        )
        self.assertEqual(result["entry_direction"], "short")
        self.assertFalse(result["qualifies_for_entry"])

    def test_direction_mode_filters_bullish_signal_for_short_only(self):
        result = scan_core.deep_scan_from_precomputed(
            "TEST", self._precomputed(True), 30,
            {"entry_criteria": {"min_signals": 4, "min_signal_families": 2,
                                 "min_vol_ratio": 1.5, "direction_mode": "short"}},
        )
        self.assertEqual(result["entry_direction"], "long")
        self.assertFalse(result["qualifies_for_entry"])

    def test_both_mode_preserves_qualified_direction(self):
        result = scan_core.deep_scan_from_precomputed(
            "TEST", self._precomputed(True), 30,
            {"entry_criteria": {"min_signals": 4, "min_signal_families": 2,
                                 "min_vol_ratio": 1.5, "direction_mode": "both"}},
        )
        self.assertEqual(result["entry_direction"], "long")
        self.assertTrue(result["qualifies_for_entry"])


if __name__ == "__main__":
    unittest.main()
