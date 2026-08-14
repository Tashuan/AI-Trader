"""
test_backtest_liquidity.py — Tests for conservative spread/depth estimation.

Verifies:
1. Estimated spread is non-zero and >= 1 tick.
2. Spread widens with volatility and low volume.
3. Bid < ask (spread is positive).
4. Observed quotes are passed through unchanged.
5. Depth estimate is conservative and labeled.
6. Provenance metadata is correct.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from backtest_liquidity import estimate_quote, estimate_level2


class EstimateQuoteTests(unittest.TestCase):
    def _bar(self, close=100.0, high=101.0, low=99.0, volume=100_000):
        return {"Close": close, "High": high, "Low": low, "Volume": volume}

    def test_estimated_spread_is_nonzero_and_at_least_one_tick(self):
        bar = self._bar()
        q = estimate_quote(bar, market="us-stock")
        self.assertGreater(q["spread"], 0)
        self.assertGreaterEqual(q["spread"], 0.01)  # 1 tick for $100 stock
        self.assertLess(q["bid"], q["ask"])
        self.assertEqual(q["spread_source"], "estimated")
        self.assertTrue(q["is_estimated"])

    def test_spread_widens_with_higher_volatility(self):
        calm = self._bar(close=100, high=100.5, low=99.5, volume=100_000)
        volatile = self._bar(close=100, high=105, low=95, volume=100_000)
        q_calm = estimate_quote(calm)
        q_vol = estimate_quote(volatile)
        self.assertGreater(q_vol["spread"], q_calm["spread"])

    def test_spread_widens_with_lower_volume(self):
        liquid = self._bar(close=100, high=101, low=99, volume=10_000_000)
        thin = self._bar(close=100, high=101, low=99, volume=1_000)
        q_liquid = estimate_quote(liquid)
        q_thin = estimate_quote(thin)
        self.assertGreater(q_thin["spread"], q_liquid["spread"])

    def test_spread_multiplier_scales_spread(self):
        bar = self._bar()
        q1 = estimate_quote(bar, spread_multiplier=1.0)
        q2 = estimate_quote(bar, spread_multiplier=2.0)
        self.assertGreaterEqual(q2["spread"], q1["spread"] * 1.9)

    def test_observed_quote_is_passed_through(self):
        observed = {"bid": 99.50, "ask": 100.50, "last": 100.0, "total_volume": 50000}
        q = estimate_quote(self._bar(), observed=observed)
        self.assertEqual(q["bid"], 99.50)
        self.assertEqual(q["ask"], 100.50)
        self.assertEqual(q["spread_source"], "observed")
        self.assertFalse(q["is_estimated"])

    def test_zero_close_returns_unavailable(self):
        q = estimate_quote({"Close": 0, "High": 0, "Low": 0, "Volume": 0})
        self.assertEqual(q["spread_source"], "unavailable")
        self.assertTrue(q["is_estimated"])
        self.assertEqual(q["bid"], 0.0)

    def test_bid_ask_bracket_close(self):
        bar = self._bar(close=100, high=101, low=99, volume=100_000)
        q = estimate_quote(bar)
        self.assertLessEqual(q["bid"], 100.0)
        self.assertGreaterEqual(q["ask"], 100.0)


class EstimateLevel2Tests(unittest.TestCase):
    def test_estimated_depth_is_conservative(self):
        bar = {"Close": 100.0, "Volume": 1_000_000}
        l2 = estimate_level2(bar)
        self.assertIsNotNone(l2)
        self.assertEqual(l2["depth_source"], "estimated")
        self.assertTrue(l2["is_estimated"])
        # 100 * 1M * 0.001 = 100K, capped at 50K
        self.assertLessEqual(l2["total_depth_dollars"], 50_000)

    def test_observed_level2_is_passed_through(self):
        observed = {"total_depth_dollars": 200_000, "bid_depth_dollars": 100_000,
                    "ask_depth_dollars": 100_000}
        l2 = estimate_level2({}, observed=observed)
        self.assertEqual(l2["depth_source"], "observed")
        self.assertFalse(l2["is_estimated"])

    def test_zero_volume_returns_none(self):
        l2 = estimate_level2({"Close": 100, "Volume": 0})
        self.assertIsNone(l2)


if __name__ == "__main__":
    unittest.main()
