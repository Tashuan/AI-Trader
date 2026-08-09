"""Regression tests for regime-aware signal selection, confluence scoring,
exposure controls, walk-forward engine, promotion gates, and diagnostics."""

import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class TestRegimeClassification(unittest.TestCase):
    """Test classify_regime and regime_filter_entry."""

    def _make_daily_df(self, closes: list[float], start: str = "2023-01-01") -> pd.DataFrame:
        dates = pd.date_range(start, periods=len(closes), freq="D")
        return pd.DataFrame({
            "Date": dates,
            "Close": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Volume": [1000000] * len(closes),
        })

    def test_bullish_regime(self):
        from crypto_scan_core import classify_regime
        closes = [100 + i * 0.5 for i in range(60)]
        df = self._make_daily_df(closes)
        regime = classify_regime(df, params={"entry_criteria": {"regime_persistence_bars": 3}})
        self.assertEqual(regime["regime"], "bullish")
        self.assertTrue(regime["allows_long"])
        self.assertFalse(regime["allows_short"])

    def test_bearish_regime(self):
        from crypto_scan_core import classify_regime
        closes = [100 - i * 0.5 for i in range(60)]
        df = self._make_daily_df(closes)
        regime = classify_regime(df, params={"entry_criteria": {"regime_persistence_bars": 3}})
        self.assertEqual(regime["regime"], "bearish")
        self.assertFalse(regime["allows_long"])
        self.assertTrue(regime["allows_short"])

    def test_neutral_regime_block(self):
        from crypto_scan_core import classify_regime
        closes = [100] * 60
        df = self._make_daily_df(closes)
        regime = classify_regime(df, params={"entry_criteria": {"regime_persistence_bars": 3, "regime_neutral_mode": "block"}})
        self.assertEqual(regime["regime"], "neutral")
        self.assertFalse(regime["allows_long"])
        self.assertFalse(regime["allows_short"])

    def test_neutral_regime_allow(self):
        from crypto_scan_core import classify_regime
        closes = [100] * 60
        df = self._make_daily_df(closes)
        regime = classify_regime(df, params={"entry_criteria": {"regime_persistence_bars": 3, "regime_neutral_mode": "allow"}})
        self.assertEqual(regime["regime"], "neutral")
        self.assertTrue(regime["allows_long"])
        self.assertTrue(regime["allows_short"])

    def test_persistence_requirement(self):
        from crypto_scan_core import classify_regime
        closes = [100 + i * 0.5 for i in range(55)] + [100] * 5
        df = self._make_daily_df(closes)
        regime = classify_regime(df, params={"entry_criteria": {"regime_persistence_bars": 10}})
        self.assertEqual(regime["regime"], "neutral")

    def test_regime_filter_btc_self_filter_disabled(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "bearish"}
        params = {"entry_criteria": {"btc_self_filter": False}}
        ok, reason = regime_filter_entry("BTC", "long", regime, params)
        self.assertTrue(ok)

    def test_regime_filter_btc_self_filter_enabled(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "bearish"}
        params = {"entry_criteria": {"btc_self_filter": True}}
        ok, reason = regime_filter_entry("BTC", "long", regime, params)
        self.assertFalse(ok)
        self.assertIn("bearish", reason)

    def test_regime_filter_bearish_blocks_long(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "bearish"}
        params = {"entry_criteria": {}}
        ok, reason = regime_filter_entry("ETH", "long", regime, params)
        self.assertFalse(ok)
        self.assertIn("bearish", reason)

    def test_regime_filter_bullish_blocks_short(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "bullish"}
        params = {"entry_criteria": {}}
        ok, reason = regime_filter_entry("ETH", "short", regime, params)
        self.assertFalse(ok)
        self.assertIn("bullish", reason)

    def test_regime_filter_neutral_block(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "neutral"}
        params = {"entry_criteria": {"regime_neutral_mode": "block"}}
        ok, reason = regime_filter_entry("ETH", "long", regime, params)
        self.assertFalse(ok)
        self.assertIn("neutral", reason)

    def test_regime_filter_neutral_allow(self):
        from crypto_scan_core import regime_filter_entry
        regime = {"regime": "neutral"}
        params = {"entry_criteria": {"regime_neutral_mode": "allow"}}
        ok, reason = regime_filter_entry("ETH", "long", regime, params)
        self.assertTrue(ok)


class TestConfluenceScoring(unittest.TestCase):
    """Test family_confluence_score for correlated indicator grouping."""

    def test_all_neutral(self):
        from crypto_scan_core import family_confluence_score
        indicators = {
            "rsi": {"value": 50, "signal": "neutral", "family": "momentum"},
            "vol_ratio": {"value": 1.0, "signal": "neutral", "family": "volume"},
        }
        count, diversity = family_confluence_score(indicators)
        self.assertEqual(count, 0)
        self.assertEqual(diversity, 0.0)

    def test_correlated_families_counted_once(self):
        from crypto_scan_core import family_confluence_score
        indicators = {
            "sma_alignment": {"value": "20>50", "signal": "bullish", "family": "trend"},
            "ema21": {"value": 100, "signal": "bullish", "family": "trend"},
            "macd_hist": {"value": 0.5, "signal": "bullish", "family": "trend"},
            "ema_alignment": {"value": "bullish", "signal": "bullish", "family": "trend_strength"},
            "rsi": {"value": 60, "signal": "bullish", "family": "momentum"},
        }
        count, diversity = family_confluence_score(indicators)
        self.assertEqual(count, 2)

    def test_all_families_independent(self):
        from crypto_scan_core import family_confluence_score
        indicators = {
            "rsi": {"value": 60, "signal": "bullish", "family": "momentum"},
            "vol_ratio": {"value": 2.0, "signal": "bullish", "family": "volume"},
            "bb_state": {"value": "expanding", "signal": "bullish", "family": "volatility"},
            "vwap": {"value": 100, "signal": "bullish", "family": "timing"},
            "sma_alignment": {"value": "20>50", "signal": "bullish", "family": "trend"},
        }
        count, diversity = family_confluence_score(indicators)
        self.assertEqual(count, 5)


class TestExposureControls(unittest.TestCase):
    """Test correlation exposure and BTC slot reservation."""

    def test_correlation_limit(self):
        from crypto_scan_core import check_correlation_exposure
        params = {"exposure_controls": {"max_correlated_positions": 2, "correlation_buckets": [["BTC", "WBTC"], ["ETH", "STETH", "ETC"]]}}
        ok, _ = check_correlation_exposure("ETH", {"BTC": {}}, params)
        self.assertTrue(ok)
        ok, _ = check_correlation_exposure("ETH", {"STETH": {}, "ETC": {}, "BTC": {}}, params)
        self.assertFalse(ok)

    def test_no_bucket_unrestricted(self):
        from crypto_scan_core import check_correlation_exposure
        params = {"exposure_controls": {"max_correlated_positions": 1, "correlation_buckets": [["BTC"]]} }
        ok, _ = check_correlation_exposure("DOGE", {"BTC": {}}, params)
        self.assertTrue(ok)

    def test_btc_slot_reservation(self):
        from crypto_scan_core import check_btc_slot_reservation
        params = {"exposure_controls": {"reserve_btc_slot": True}}
        ok, _ = check_btc_slot_reservation({"ETH": {}}, params, max_positions=3)
        self.assertTrue(ok)
        ok, _ = check_btc_slot_reservation({"ETH": {}, "SOL": {}}, params, max_positions=3)
        self.assertFalse(ok)

    def test_btc_slot_not_reserved(self):
        from crypto_scan_core import check_btc_slot_reservation
        params = {"exposure_controls": {"reserve_btc_slot": False}}
        ok, _ = check_btc_slot_reservation({"ETH": {}, "SOL": {}}, params, max_positions=3)
        self.assertTrue(ok)

    def test_symbol_eligibility_no_data(self):
        from crypto_scan_core import check_symbol_eligibility
        ok, reason = check_symbol_eligibility("BTC", None, {})
        self.assertFalse(ok)
        self.assertEqual(reason, "no_data")

    def test_symbol_eligibility_insufficient_bars(self):
        from crypto_scan_core import check_symbol_eligibility
        df = pd.DataFrame({"Close": [100]*10, "Volume": [1000]*10})
        ok, reason = check_symbol_eligibility("BTC", df, {})
        self.assertFalse(ok)
        self.assertIn("insufficient", reason)


class TestATRSLTP(unittest.TestCase):
    """Test ATR-based SL/TP computation."""

    def test_2_to_1_ratio(self):
        from crypto_scan_core import compute_atr_sl_tp
        scan_data = {"atr": 10.0}
        params = {"exit_rules": {"stop_loss_pct_clamp": [-1.0, -50.0], "take_profit_pct_clamp": [1.0, 100.0]}}
        sl, tp, _, _ = compute_atr_sl_tp(100.0, "long", scan_data, params)
        sl_dist = 100 - sl
        tp_dist = tp - 100
        self.assertAlmostEqual(sl_dist, 15.0, places=1)
        self.assertAlmostEqual(tp_dist, 30.0, places=1)
        ratio = tp_dist / sl_dist
        self.assertAlmostEqual(ratio, 2.0, places=1)

    def test_short_side(self):
        from crypto_scan_core import compute_atr_sl_tp
        scan_data = {"atr": 10.0}
        params = {"exit_rules": {"stop_loss_pct_clamp": [-1.0, -50.0], "take_profit_pct_clamp": [1.0, 100.0]}}
        sl, tp, _, _ = compute_atr_sl_tp(100.0, "short", scan_data, params)
        sl_dist = sl - 100
        tp_dist = 100 - tp
        self.assertAlmostEqual(sl_dist, 15.0, places=1)
        self.assertAlmostEqual(tp_dist, 30.0, places=1)

    def test_fallback_atr(self):
        from crypto_scan_core import compute_atr_sl_tp
        scan_data = {"atr": 0}
        params = {"exit_rules": {}}
        sl, tp, _, _ = compute_atr_sl_tp(100.0, "long", scan_data, params)
        self.assertLess(sl, 100)
        self.assertGreater(tp, 100)


class TestWalkForward(unittest.TestCase):
    """Test walk-forward window generation."""

    def test_window_generation(self):
        from walk_forward import generate_windows
        windows = generate_windows("2023-01-01", "2023-06-30", train_days=60, test_days=30, step_days=30)
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[0].train_start, "2023-01-01")
        self.assertEqual(windows[0].test_start, "2023-03-02")

    def test_no_windows_short_range(self):
        from walk_forward import generate_windows
        windows = generate_windows("2023-01-01", "2023-01-15", train_days=60, test_days=30, step_days=30)
        self.assertEqual(len(windows), 0)


class TestPromotionGates(unittest.TestCase):
    """Test promotion evaluation."""

    def test_passing_candidate(self):
        from promotion import evaluate_promotion
        from walk_forward import WalkForwardSummary
        s = WalkForwardSummary(
            candidate_id="test", windows_run=5, windows_passed=4, avg_score=15.0,
            avg_return_pct=5.0, avg_sharpe=0.8, max_drawdown_pct=12.0, total_trades=20,
            win_rate=55.0, passed=True, results=[],
        )
        evals = evaluate_promotion({"test": s})
        self.assertTrue(evals["test"].passed)

    def test_failing_candidate(self):
        from promotion import evaluate_promotion
        from walk_forward import WalkForwardSummary
        s = WalkForwardSummary(
            candidate_id="bad", windows_run=5, windows_passed=1, avg_score=-50.0,
            avg_return_pct=-10.0, avg_sharpe=-2.0, max_drawdown_pct=40.0, total_trades=2,
            win_rate=20.0, passed=False, results=[],
        )
        evals = evaluate_promotion({"bad": s})
        self.assertFalse(evals["bad"].passed)
        self.assertGreater(len(evals["bad"].fail_reasons), 0)

    def test_select_best(self):
        from promotion import evaluate_promotion, select_best_candidate
        from walk_forward import WalkForwardSummary
        s1 = WalkForwardSummary("a", 5, 4, 15.0, 5.0, 0.8, 12.0, 20, 55.0, True, [])
        s2 = WalkForwardSummary("b", 5, 5, 20.0, 8.0, 1.2, 10.0, 25, 60.0, True, [])
        evals = evaluate_promotion({"a": s1, "b": s2})
        best = select_best_candidate(evals)
        self.assertEqual(best, "b")


class TestReportDiagnostics(unittest.TestCase):
    """Test exit attribution and data coverage in BacktestReport."""

    def test_exit_attribution(self):
        from backtest_report import _compute_exit_attribution, TradeRecord
        trades = [
            TradeRecord("BTC", "long", "2023-01-01", "2023-01-05", 100, 108, 1, 8, 8, 4, 96, "take_profit"),
            TradeRecord("ETH", "long", "2023-01-03", "2023-01-07", 200, 190, 2, -20, -5, 4, 96, "stop_loss"),
        ]
        ea = _compute_exit_attribution(trades)
        self.assertEqual(ea["counts"]["take_profit"], 1)
        self.assertEqual(ea["counts"]["stop_loss"], 1)
        self.assertEqual(ea["pnl_by_reason"]["take_profit"], 8)
        self.assertEqual(ea["pnl_by_reason"]["stop_loss"], -20)

    def test_data_coverage(self):
        from backtest_report import _compute_data_coverage
        curve = [{"date": "2023-01-01", "equity": 10000}, {"date": "2023-01-31", "equity": 10500}]
        dc = _compute_data_coverage(["BTC"], "2023-01-01", "2023-01-31", curve)
        self.assertEqual(dc["coverage_pct"], 100.0)
        self.assertEqual(dc["bars_in_equity_curve"], 2)


if __name__ == "__main__":
    unittest.main()
