import sys
from pathlib import Path
import unittest

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

from execution_simulator import FillConfig, simulate_entry, simulate_exit


class ExecutionSimulatorTests(unittest.TestCase):
    def test_long_entry_is_adverse_and_charges_fee(self):
        config = FillConfig(
            slippage_bps=10,
            fee_rate=0.001,
            enable_size_impact=False,
            enable_vol_widening=False,
            enable_partial_fills=False,
            enable_tick_rounding=False,
        )
        result = simulate_entry(100.0, "long", 10.0, "AAPL", config)
        self.assertAlmostEqual(result.fill_price, 100.10)
        self.assertEqual(result.fill_qty, 10.0)
        self.assertAlmostEqual(result.fee, 1.001)

    def test_short_exit_buys_back_at_adverse_price(self):
        config = FillConfig(
            slippage_bps=10,
            fee_rate=0.0,
            enable_size_impact=False,
            enable_vol_widening=False,
            enable_partial_fills=False,
            enable_tick_rounding=False,
        )
        result = simulate_exit(100.0, "short", 10.0, "AAPL", config)
        self.assertAlmostEqual(result.fill_price, 100.10)

    def test_partial_fill_uses_bar_dollar_volume(self):
        config = FillConfig(
            slippage_bps=0,
            fee_rate=0.0,
            enable_size_impact=False,
            enable_vol_widening=False,
            enable_partial_fills=True,
            enable_tick_rounding=False,
            max_fill_pct_of_adv=0.10,
            interval="1d",
        )
        bar = {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000}
        result = simulate_entry(100.0, "long", 500.0, "AAPL", config, bar)
        self.assertTrue(result.partial_fill)
        self.assertAlmostEqual(result.fill_qty, 100.0)

    def test_tick_rounding_never_improves_direction(self):
        config = FillConfig(
            slippage_bps=0,
            fee_rate=0.0,
            enable_size_impact=False,
            enable_vol_widening=False,
            enable_partial_fills=False,
            enable_tick_rounding=True,
        )
        buy = simulate_entry(100.001, "long", 1.0, "AAPL", config)
        sell = simulate_exit(100.009, "long", 1.0, "AAPL", config)
        self.assertGreaterEqual(buy.fill_price, 100.001)
        self.assertLessEqual(sell.fill_price, 100.009)

    def test_invalid_orders_do_not_fill(self):
        config = FillConfig()
        self.assertEqual(simulate_entry(0, "long", 1, "AAPL", config).fill_qty, 0)
        self.assertEqual(simulate_exit(100, "long", 0, "AAPL", config).fill_qty, 0)


if __name__ == "__main__":
    unittest.main()
