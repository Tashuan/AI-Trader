import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_registry import effective_params, position_notional


class StrategyRegistryTests(unittest.TestCase):
    def test_profiles_are_agent_specific(self):
        equity = effective_params("BlitzRunner")
        crypto = effective_params("CryptoRunner")
        self.assertEqual(equity["profile"], "equity_momentum")
        self.assertEqual(crypto["profile"], "crypto_swing")
        self.assertEqual(equity["indicators"]["candle_interval"], "1h")
        self.assertEqual(crypto["indicators"]["candle_interval"], "4h")
        self.assertEqual(crypto["position_sizing"]["max_positions"], 3)

    def test_nested_overrides_preserve_profile_defaults(self):
        params = effective_params(
            "CryptoRunner",
            stored={"exit_rules": {"take_profit_pct": 5.0}, "risk_controls": {"risk_per_trade_pct": 0.25}},
        )
        self.assertEqual(params["exit_rules"]["take_profit_pct"], 5.0)
        self.assertEqual(params["exit_rules"]["stop_loss_pct"], -5.0)
        self.assertEqual(params["risk_controls"]["paper_account_budget"], 10000.0)

    def test_risk_notional_respects_budget_and_stop(self):
        params = effective_params("CryptoRunner")
        notional = position_notional(10000.0, 5.0, 0.0, params)
        self.assertAlmostEqual(notional, 1000.0)
        self.assertEqual(position_notional(10000.0, 5.0, 10000.0, params), 0.0)

    def test_invalid_risk_value_rejected(self):
        with self.assertRaises(ValueError):
            effective_params("CryptoRunner", stored={"risk_controls": {"risk_per_trade_pct": 20}})


if __name__ == "__main__":
    unittest.main()
