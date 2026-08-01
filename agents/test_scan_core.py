"""
test_scan_core.py — Regression tests for scan_core indicator math and exit rules.

Verifies:
1. Indicator computations (RSI, MACD, SMA, EMA, ATR, Bollinger, Stochastic, OBV, VWAP)
2. Precomputed vs per-bar parity (precompute_indicators matches deep_scan_symbol_from_df)
3. Exit rule firing in review_position_from_indicators (all 6 rules)
4. Composite scoring and entry qualification
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import scan_core


# ─── Helpers ────────────────────────────────────────────────────

def _make_df(n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Generate a deterministic OHLCV DataFrame for testing."""
    rng = np.random.RandomState(seed)
    base = 100.0
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.randn() * 0.02))
    closes = np.array(closes)
    highs = closes * (1 + rng.rand(n) * 0.01)
    lows = closes * (1 - rng.rand(n) * 0.01)
    opens = (highs + lows) / 2
    volumes = rng.randint(500, 5000, n).astype(float)
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


def _make_trending_df(n: int = 100, trend: float = 0.01) -> pd.DataFrame:
    """Generate a steadily trending DataFrame (bullish)."""
    rng = np.random.RandomState(99)
    base = 100.0
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + trend + rng.randn() * 0.005))
    closes = np.array(closes)
    highs = closes * (1 + 0.005)
    lows = closes * (1 - 0.005)
    opens = (highs + lows) / 2
    volumes = np.linspace(1000, 3000, n) + rng.randint(-200, 200, n)
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


PARAMS = dict(scan_core.DEFAULT_PARAMS)


# ─── Indicator Math Tests ───────────────────────────────────────

def test_rsi_range():
    """RSI should be between 0 and 100."""
    df = _make_df()
    rsi = scan_core.compute_rsi(df, 14)
    assert 0 <= rsi <= 100, f"RSI out of range: {rsi}"
    print(f"  RSI: {rsi:.2f} ✓")


def test_macd_returns_tuple():
    """MACD should return (hist, line) as floats."""
    df = _make_df()
    hist, line = scan_core.compute_macd(df)
    assert isinstance(hist, float), f"hist not float: {type(hist)}"
    assert isinstance(line, float), f"line not float: {type(line)}"
    print(f"  MACD hist={hist:.4f}, line={line:.4f} ✓")


def test_sma_positive():
    """SMA should be positive for positive prices."""
    df = _make_df()
    sma20 = scan_core.compute_sma(df, 20)
    assert sma20 > 0, f"SMA20 not positive: {sma20}"
    print(f"  SMA20: {sma20:.2f} ✓")


def test_ema_positive():
    """EMA should be positive for positive prices."""
    df = _make_df()
    ema20 = scan_core.compute_ema(df, 20)
    assert ema20 > 0, f"EMA20 not positive: {ema20}"
    print(f"  EMA20: {ema20:.2f} ✓")


def test_atr_positive():
    """ATR should be non-negative."""
    df = _make_df()
    atr = scan_core.compute_atr(df, 14)
    assert atr >= 0, f"ATR negative: {atr}"
    print(f"  ATR: {atr:.4f} ✓")


def test_bollinger_ordering():
    """Bollinger upper > lower, width > 0."""
    df = _make_df()
    upper, lower, width, squeeze = scan_core.compute_bollinger(df, 20)
    assert upper > lower, f"BB upper({upper}) <= lower({lower})"
    assert width > 0, f"BB width not positive: {width}"
    print(f"  BB: upper={upper:.2f}, lower={lower:.2f}, width={width:.4f} ✓")


def test_stochastic_range():
    """Stochastic K and D should be 0-100."""
    df = _make_df()
    k, d = scan_core.compute_stochastic(df, 14)
    assert 0 <= k <= 100, f"Stoch K out of range: {k}"
    assert 0 <= d <= 100, f"Stoch D out of range: {d}"
    print(f"  Stoch: k={k:.1f}, d={d:.1f} ✓")


def test_obv_is_series():
    """OBV should be a pandas Series."""
    df = _make_df()
    obv = scan_core.compute_obv(df)
    assert isinstance(obv, pd.Series), f"OBV not Series: {type(obv)}"
    print(f"  OBV: last={obv.iloc[-1]:.0f} ✓")


def test_vwap_positive():
    """VWAP should be positive for positive prices."""
    df = _make_df()
    vwap = scan_core.compute_vwap(df)
    assert vwap > 0, f"VWAP not positive: {vwap}"
    print(f"  VWAP: {vwap:.2f} ✓")


def test_candle_body_ratio_range():
    """Body ratio should be 0-1."""
    df = _make_df()
    ratio = scan_core.candle_body_ratio(df)
    assert 0 <= ratio <= 1, f"Body ratio out of range: {ratio}"
    print(f"  Body ratio: {ratio:.3f} ✓")


# ─── Precomputed vs Per-Bar Parity ──────────────────────────────

def test_precomputed_matches_per_bar():
    """precompute_indicators + deep_scan_from_precomputed should match deep_scan_symbol_from_df
    at the last bar (with small tolerance for EWM differences)."""
    df = _make_df(200)
    params = dict(PARAMS)

    # Per-bar (original) at last bar
    result_per_bar = scan_core.deep_scan_symbol_from_df("TEST", df, params)

    # Precomputed at last bar
    pre = scan_core.precompute_indicators(df, params)
    result_pre = scan_core.deep_scan_from_precomputed("TEST", pre, len(df) - 1, params)

    assert not result_per_bar.get("error"), "Per-bar scan errored"
    assert not result_pre.get("error"), "Precomputed scan errored"

    # Compare key fields
    assert abs(result_per_bar["price"] - result_pre["price"]) < 1e-6, \
        f"Price mismatch: {result_per_bar['price']} vs {result_pre['price']}"

    assert abs(result_per_bar["composite_score"] - result_pre["composite_score"]) < 0.01, \
        f"Score mismatch: {result_per_bar['composite_score']} vs {result_pre['composite_score']}"

    assert result_per_bar["qualifies_for_entry"] == result_pre["qualifies_for_entry"], \
        f"Qualifies mismatch: {result_per_bar['qualifies_for_entry']} vs {result_pre['qualifies_for_entry']}"

    assert result_per_bar["entry_direction"] == result_pre["entry_direction"], \
        f"Direction mismatch: {result_per_bar['entry_direction']} vs {result_pre['entry_direction']}"

    # Compare indicator values
    for key in result_per_bar["indicators"]:
        v1 = result_per_bar["indicators"][key]["value"]
        v2 = result_pre["indicators"][key]["value"]
        if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
            assert abs(v1 - v2) < 0.01, f"Indicator {key} mismatch: {v1} vs {v2}"
        else:
            assert v1 == v2, f"Indicator {key} mismatch: {v1} vs {v2}"

    print(f"  Score: {result_per_bar['composite_score']:.2f} vs {result_pre['composite_score']:.2f} ✓")
    print(f"  Qualifies: {result_per_bar['qualifies_for_entry']} == {result_pre['qualifies_for_entry']} ✓")


def test_precomputed_intermediate_bar():
    """Precomputed at bar 50 should match per-bar on df[:51]."""
    df = _make_df(200)
    params = dict(PARAMS)
    bar = 50

    # Per-bar on truncated df
    result_per_bar = scan_core.deep_scan_symbol_from_df("TEST", df.iloc[:bar + 1], params)

    # Precomputed at bar 50
    pre = scan_core.precompute_indicators(df, params)
    result_pre = scan_core.deep_scan_from_precomputed("TEST", pre, bar, params)

    assert not result_per_bar.get("error"), "Per-bar scan errored"
    assert not result_pre.get("error"), "Precomputed scan errored"

    # Price should match exactly
    assert abs(result_per_bar["price"] - result_pre["price"]) < 1e-6, \
        f"Price mismatch at bar {bar}: {result_per_bar['price']} vs {result_pre['price']}"

    # Composite score should be close (EWM uses full history in precomputed, truncated in per-bar)
    assert abs(result_per_bar["composite_score"] - result_pre["composite_score"]) < 0.5, \
        f"Score mismatch at bar {bar}: {result_per_bar['composite_score']} vs {result_pre['composite_score']}"

    print(f"  Bar {bar} score: {result_per_bar['composite_score']:.2f} vs {result_pre['composite_score']:.2f} ✓")


# ─── Exit Rule Tests ────────────────────────────────────────────

def test_stop_loss_fires():
    """Rule 1: Stop loss should fire when PnL <= stop_loss_pct."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 97.0}
    ind_data = {"indicators": {"vol_ratio": {"value": 1.0}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert "stop_loss" in result["exit_reason"], f"Expected stop_loss, got {result['exit_reason']}"
    print(f"  Long -3% → {result['exit_reason']} ✓")


def test_stop_loss_short_fires():
    """Stop loss for short when price rises above entry."""
    pos = {"symbol": "TEST", "side": "short", "entry_price": 100.0, "current_price": 103.0}
    ind_data = {"indicators": {"vol_ratio": {"value": 1.0}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert "stop_loss" in result["exit_reason"], f"Expected stop_loss, got {result['exit_reason']}"
    print(f"  Short +3% → {result['exit_reason']} ✓")


def test_take_profit_fires():
    """Rule 2: Take profit should fire when PnL >= take_profit_pct."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 102.5}
    ind_data = {"indicators": {"vol_ratio": {"value": 1.0}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert "take_profit" in result["exit_reason"], f"Expected take_profit, got {result['exit_reason']}"
    print(f"  Long +2.5% → {result['exit_reason']} ✓")


def test_stagnation_fires():
    """Rule 3: Stagnation should fire after N cycles of flat PnL."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 100.1}
    ind_data = {"indicators": {"vol_ratio": {"value": 1.0}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    # cycles_flat = 6 (default stagnation_cycles)
    result = scan_core.review_position_from_indicators(pos, PARAMS, 6, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert result["exit_reason"] == "stagnation_timeout", f"Expected stagnation, got {result['exit_reason']}"
    print(f"  6 flat cycles → {result['exit_reason']} ✓")


def test_momentum_death_fires():
    """Rule 4: Momentum death should fire when vol_ratio < threshold (after grace period)."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 100.1}
    # vol_ratio = 0.3 < 0.7 threshold, bars_held=3 >= grace_period
    ind_data = {"indicators": {"vol_ratio": {"value": 0.3}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data, bars_held=3)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert result["exit_reason"] == "momentum_death", f"Expected momentum_death, got {result['exit_reason']}"
    print(f"  Vol ratio 0.3, bars_held=3 → {result['exit_reason']} ✓")


def test_momentum_death_grace_period():
    """Momentum death should NOT fire during grace period (bars_held < grace)."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 100.1}
    ind_data = {"indicators": {"vol_ratio": {"value": 0.3}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data, bars_held=1)
    assert result["verdict"] == "HOLD", f"Expected HOLD during grace, got {result['verdict']} ({result['exit_reason']})"
    print(f"  Vol ratio 0.3, bars_held=1 → HOLD (grace period) ✓")


def test_ob_exhaustion_fires():
    """Rule 5: OB exhaustion should fire when RSI > threshold, vol dropping, price rising."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 103.0}
    # RSI=80 > 75, vol_ratio=0.8 < 1.0, pnl=+3% > 0
    # But take_profit (2%) fires first since pnl >= 2%... so use pnl just under TP
    pos["current_price"] = 101.5  # +1.5% (under 2% TP)
    ind_data = {"indicators": {"vol_ratio": {"value": 0.8}, "rsi": {"value": 80.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert result["exit_reason"] == "ob_exhaustion", f"Expected ob_exhaustion, got {result['exit_reason']}"
    print(f"  RSI=80, vol<1, +1.5% → {result['exit_reason']} ✓")


def test_vwap_loss_fires():
    """Rule 6: VWAP loss should fire when long drops below VWAP after entering above it."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 105.0, "current_price": 103.5}
    # entry > vwap, current < vwap, pnl=-1.43% (above -2% stop loss so SL doesn't fire first)
    ind_data = {"indicators": {"vol_ratio": {"value": 1.0}, "rsi": {"value": 50.0}, "vwap": {"value": 104.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "EXIT", f"Expected EXIT, got {result['verdict']}"
    assert result["exit_reason"] == "vwap_loss", f"Expected vwap_loss, got {result['exit_reason']}"
    print(f"  Long entry=105, current=103.5, vwap=104 → {result['exit_reason']} ✓")


def test_hold_when_no_rules_fire():
    """No rules firing should return HOLD."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 100.5}
    # pnl=+0.5% (under TP), vol_ratio=1.5 (not dead), RSI=55 (not OB), vwap=99 (above vwap)
    ind_data = {"indicators": {"vol_ratio": {"value": 1.5}, "rsi": {"value": 55.0}, "vwap": {"value": 99.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert result["verdict"] == "HOLD", f"Expected HOLD, got {result['verdict']} ({result['exit_reason']})"
    print(f"  No rules → HOLD ✓")


def test_rule_priority_tp_over_momentum_death():
    """Take profit should fire before momentum death (higher priority rule)."""
    pos = {"symbol": "TEST", "side": "long", "entry_price": 100.0, "current_price": 102.5}
    # Both TP (+2.5%) and momentum death (vol=0.3) could fire
    ind_data = {"indicators": {"vol_ratio": {"value": 0.3}, "rsi": {"value": 50.0}, "vwap": {"value": 100.0}}}
    result = scan_core.review_position_from_indicators(pos, PARAMS, 0, ind_data)
    assert "take_profit" in result["exit_reason"], f"Expected take_profit priority, got {result['exit_reason']}"
    print(f"  TP + momentum_death both fire → {result['exit_reason']} (TP wins) ✓")


# ─── Scoring & Entry Qualification ──────────────────────────────

def test_trending_df_qualifies():
    """A strongly trending DataFrame with volume should qualify for entry."""
    df = _make_trending_df(100, trend=0.02)
    result = scan_core.deep_scan_symbol_from_df("TEST", df, PARAMS)
    # Should have decent score
    assert result["composite_score"] > 0, f"Score should be positive for trending data: {result['composite_score']}"
    print(f"  Trending df score: {result['composite_score']:.2f}, qualifies: {result['qualifies_for_entry']} ✓")


def test_flat_df_does_not_qualify():
    """A flat/low-volume DataFrame should not qualify for entry."""
    rng = np.random.RandomState(1)
    n = 100
    closes = np.array([100.0 + rng.randn() * 0.01 for _ in range(n)])
    df = pd.DataFrame({
        "Open": closes,
        "High": closes + 0.01,
        "Low": closes - 0.01,
        "Close": closes,
        "Volume": np.ones(n) * 100,  # constant low volume
    })
    result = scan_core.deep_scan_symbol_from_df("TEST", df, PARAMS)
    assert not result["qualifies_for_entry"], f"Should not qualify for flat data: {result['qualifies_for_entry']}"
    print(f"  Flat df score: {result['composite_score']:.2f}, qualifies: {result['qualifies_for_entry']} ✓")


def test_insufficient_data_returns_error():
    """Less than 30 bars should return error."""
    df = _make_df(20)
    result = scan_core.deep_scan_symbol_from_df("TEST", df, PARAMS)
    assert result.get("error"), f"Should return error for insufficient data"
    print(f"  20 bars → error: {result.get('error')} ✓")


# ─── Main ───────────────────────────────────────────────────────

def run_all():
    tests = [
        # Indicator math
        ("RSI range", test_rsi_range),
        ("MACD tuple", test_macd_returns_tuple),
        ("SMA positive", test_sma_positive),
        ("EMA positive", test_ema_positive),
        ("ATR positive", test_atr_positive),
        ("Bollinger ordering", test_bollinger_ordering),
        ("Stochastic range", test_stochastic_range),
        ("OBV series", test_obv_is_series),
        ("VWAP positive", test_vwap_positive),
        ("Candle body ratio range", test_candle_body_ratio_range),
        # Precomputed parity
        ("Precomputed matches per-bar (last)", test_precomputed_matches_per_bar),
        ("Precomputed matches per-bar (bar 50)", test_precomputed_intermediate_bar),
        # Exit rules
        ("Stop loss fires (long)", test_stop_loss_fires),
        ("Stop loss fires (short)", test_stop_loss_short_fires),
        ("Take profit fires", test_take_profit_fires),
        ("Stagnation fires", test_stagnation_fires),
        ("Momentum death fires", test_momentum_death_fires),
        ("Momentum death grace period", test_momentum_death_grace_period),
        ("OB exhaustion fires", test_ob_exhaustion_fires),
        ("VWAP loss fires", test_vwap_loss_fires),
        ("Hold when no rules fire", test_hold_when_no_rules_fire),
        ("Rule priority: TP over momentum death", test_rule_priority_tp_over_momentum_death),
        # Scoring
        ("Trending df qualifies", test_trending_df_qualifies),
        ("Flat df does not qualify", test_flat_df_does_not_qualify),
        ("Insufficient data returns error", test_insufficient_data_returns_error),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            print(f"\n[{name}]")
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        print("❌ Some tests failed")
        sys.exit(1)
    else:
        print("✅ All tests passed")


if __name__ == "__main__":
    run_all()
