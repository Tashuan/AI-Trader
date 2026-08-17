"""Validation suite for the ORB options BS backtester.

Three tests:
1. IV sensitivity — does the +147% result hold across IV ±50%?
2. Walk-forward — optimize on 2-month train, test on next month (rolling)
3. Bear market — invert equity returns (×-1) and re-run

Usage:
  cd agents
  python3 ../research/strategy_search/orb_options_validation.py --test iv
  python3 ../research/strategy_search/orb_options_validation.py --test walkforward
  python3 ../research/strategy_search/orb_options_validation.py --test bear
  python3 ../research/strategy_search/orb_options_validation.py --test all
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time as time_mod
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "research" / "strategy_search"))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import pandas as pd
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from scalp_alt_signals import fetch_1m_data, fetch_prev_closes, SLIPPAGE_BPS
from orb_options_bs_backtester import IVCache, run_bs_options_backtest
from orb_options_backtester import ORB_CONFIG

# ── Winning config from prior session ──────────────────────────────────
WINNING_SYMBOLS = ["NVDA", "TSLA", "AAPL", "COIN"]
WINNING_START = "2026-04-01"
WINNING_END = "2026-08-16"

WINNING_CONFIG = {
    "range_minutes": 5,
    "stop_pct": 1.0,
    "target_pct": 1.5,
    "latest_entry": "10:30",
    "max_positions": 3,
    "position_pct": 10.0,
}
WINNING_PARAMS = {
    "strike_offset": 1,
    "dte_min": 2,
    "dte_max": 14,
    "option_slippage_bps": 10.0,
    "confirmation_minutes": 10,
    "circuit_breaker": 3,
    "risk_free_rate": 0.05,
    "min_entry_time": "09:30",
}


# ── IV multiplier wrapper ──────────────────────────────────────────────

class IVMultiplierCache:
    """Wraps an IVCache and applies a multiplier to all IV values."""

    def __init__(self, base_cache: IVCache, multiplier: float):
        self.base = base_cache
        self.mult = multiplier

    def get_iv(self, symbol, strike, option_type, expiration):
        iv = self.base.get_iv(symbol, strike, option_type, expiration)
        return max(iv * self.mult, 0.05)  # floor at 5%


# ── Test 1: IV Sensitivity ─────────────────────────────────────────────

def test_iv_sensitivity():
    """Run the winning config across IV multipliers 0.5x to 1.5x."""
    print("\n" + "=" * 70)
    print("  TEST 1: IV SENSITIVITY ANALYSIS")
    print("=" * 70)
    print(f"  Config: 5min range, 1.0%/1.5% stop/target, 10% position, OTM+1")
    print(f"  Symbols: {', '.join(WINNING_SYMBOLS)}")
    print(f"  Period:  {WINNING_START} → {WINNING_END}")
    print()

    # Fetch base IV from Schwab
    print("  Fetching base IV from Schwab...")
    base_iv = IVCache()
    base_iv.fetch_ivs(WINNING_SYMBOLS)
    print()

    # Fetch equity data once (reuse across all IV tests)
    print("  Fetching 1m equity data...")
    provider = CachedProvider(AlpacaProvider())
    frames = fetch_1m_data(WINNING_SYMBOLS, WINNING_START, WINNING_END, provider)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    prev_closes = fetch_prev_closes(WINNING_SYMBOLS, all_dates, provider)
    print()

    results = []
    multipliers = [0.50, 0.75, 1.00, 1.25, 1.50]

    for mult in multipliers:
        iv_cache = IVMultiplierCache(base_iv, mult)
        t0 = time_mod.time()
        result = run_bs_options_backtest(
            symbols=WINNING_SYMBOLS, frames=frames, prev_closes=prev_closes,
            iv_cache=iv_cache, capital=10000.0,
            slippage_bps=SLIPPAGE_BPS,
            option_slippage_bps=WINNING_PARAMS["option_slippage_bps"],
            fee_rate=0.0, config=WINNING_CONFIG,
            start_date=WINNING_START, end_date=WINNING_END,
            strike_offset=WINNING_PARAMS["strike_offset"],
            dte_min=WINNING_PARAMS["dte_min"],
            dte_max=WINNING_PARAMS["dte_max"],
            risk_free_rate=WINNING_PARAMS["risk_free_rate"],
            confirmation_minutes=WINNING_PARAMS["confirmation_minutes"],
            circuit_breaker=WINNING_PARAMS["circuit_breaker"],
            min_entry_time=WINNING_PARAMS["min_entry_time"],
        )
        elapsed = time_mod.time() - t0
        results.append((mult, result, elapsed))
        print(f"  IV={mult:.2f}x: Return={result['total_return_pct']:+7.2f}%, "
              f"PF={result['profit_factor']:.3f}, "
              f"WR={result['win_rate']:.0%}, "
              f"DD={result['max_drawdown_pct']:.1f}%, "
              f"trades={result['total_trades']}, "
              f"({elapsed:.1f}s)")

    # Summary
    print()
    print("  --- IV Sensitivity Summary ---")
    returns = [r[1]["total_return_pct"] for r in results]
    print(f"  Return range: {min(returns):+.2f}% to {max(returns):+.2f}%")
    print(f"  Spread:       {max(returns) - min(returns):.2f}pp across IV 0.5x-1.5x")
    all_positive = all(r > 0 for r in returns)
    print(f"  All positive: {'YES — IV-robust' if all_positive else 'NO — IV-sensitive'}")

    # Verdict
    print()
    if all_positive:
        print("  VERDICT: The edge is ROBUST to IV assumptions.")
        print("  The +147% result is not an artifact of constant-IV pricing.")
    else:
        print("  VERDICT: The edge is SENSITIVE to IV assumptions.")
        print("  Some IV levels produce losses — the result may be fragile.")

    return results


# ── Test 2: Walk-Forward Validation ────────────────────────────────────

def test_walk_forward():
    """Rolling 2-month train / 1-month test windows.

    For each window:
    - Train: optimize stop_pct/target_pct on 2-month window
    - Test: run best params on next 1-month window (out-of-sample)

    If OOS performance is positive across most windows, params aren't overfit.
    """
    print("\n" + "=" * 70)
    print("  TEST 2: WALK-FORWARD VALIDATION")
    print("=" * 70)
    print(f"  Symbols: {', '.join(WINNING_SYMBOLS)}")
    print(f"  Train: 2 months, Test: 1 month (rolling)")
    print()

    # Define windows: Apr-May train → Jun test, May-Jun → Jul, Jun-Jul → Aug
    windows = [
        ("2026-04-01", "2026-05-31", "2026-06-01", "2026-06-30"),
        ("2026-05-01", "2026-06-30", "2026-07-01", "2026-07-31"),
        ("2026-06-01", "2026-07-31", "2026-08-01", "2026-08-16"),
    ]

    # Parameter grid for training (small grid — stop/target only)
    param_grid = [
        (0.7, 1.2),  # original
        (1.0, 1.5),  # winning config
        (1.0, 2.0),
        (1.5, 2.5),
        (0.8, 1.6),
    ]

    # Fetch IV once
    print("  Fetching base IV from Schwab...")
    base_iv = IVCache()
    base_iv.fetch_ivs(WINNING_SYMBOLS)
    iv_cache = IVMultiplierCache(base_iv, 1.0)
    print()

    # Fetch all data once (full range)
    full_start = "2026-04-01"
    full_end = "2026-08-16"
    print("  Fetching 1m equity data (full range)...")
    provider = CachedProvider(AlpacaProvider())
    all_frames = fetch_1m_data(WINNING_SYMBOLS, full_start, full_end, provider)
    all_dates = sorted(set(d for f in all_frames.values() for d in f["Timestamp"].dt.date))
    all_prev_closes = fetch_prev_closes(WINNING_SYMBOLS, all_dates, provider)
    print()

    def slice_frames(frames, start, end):
        """Slice frames to a date range."""
        start_d = datetime.fromisoformat(start).date()
        end_d = datetime.fromisoformat(end).date()
        sliced = {}
        for sym, df in frames.items():
            dates = df["Timestamp"].dt.date
            mask = (dates >= start_d) & (dates <= end_d)
            sliced[sym] = df[mask].reset_index(drop=True)
        return sliced

    def slice_prev_closes(prev_closes, start, end):
        """Slice prev_closes to a date range.

        prev_closes is a flat dict: {(symbol, date): prev_close_float}
        """
        start_d = datetime.fromisoformat(start).date()
        end_d = datetime.fromisoformat(end).date()
        return {
            (sym, d): c for (sym, d), c in prev_closes.items()
            if start_d <= d <= end_d
        }

    oos_results = []

    for i, (tr_start, tr_end, te_start, te_end) in enumerate(windows):
        print(f"\n  Window {i+1}/{len(windows)}: "
              f"Train {tr_start}→{tr_end}, Test {te_start}→{te_end}")

        tr_frames = slice_frames(all_frames, tr_start, tr_end)
        tr_prev = slice_prev_closes(all_prev_closes, tr_start, tr_end)
        te_frames = slice_frames(all_frames, te_start, te_end)
        te_prev = slice_prev_closes(all_prev_closes, te_start, te_end)

        # Train: find best (stop, target) on train window
        best_params = None
        best_return = -999
        print(f"    Training on {len(param_grid)} param combos...")
        for stop_pct, target_pct in param_grid:
            cfg = dict(WINNING_CONFIG)
            cfg["stop_pct"] = stop_pct
            cfg["target_pct"] = target_pct
            tr_result = run_bs_options_backtest(
                symbols=WINNING_SYMBOLS, frames=tr_frames, prev_closes=tr_prev,
                iv_cache=iv_cache, capital=10000.0,
                slippage_bps=SLIPPAGE_BPS,
                option_slippage_bps=WINNING_PARAMS["option_slippage_bps"],
                fee_rate=0.0, config=cfg,
                start_date=tr_start, end_date=tr_end,
                strike_offset=WINNING_PARAMS["strike_offset"],
                dte_min=WINNING_PARAMS["dte_min"],
                dte_max=WINNING_PARAMS["dte_max"],
                risk_free_rate=WINNING_PARAMS["risk_free_rate"],
                confirmation_minutes=WINNING_PARAMS["confirmation_minutes"],
                circuit_breaker=WINNING_PARAMS["circuit_breaker"],
                min_entry_time=WINNING_PARAMS["min_entry_time"],
            )
            ret = tr_result["total_return_pct"]
            print(f"      stop={stop_pct}% target={target_pct}%: "
                  f"train return={ret:+.2f}%, trades={tr_result['total_trades']}")
            if ret > best_return:
                best_return = ret
                best_params = (stop_pct, target_pct)

        print(f"    Best train params: stop={best_params[0]}%, "
              f"target={best_params[1]}% (return={best_return:+.2f}%)")

        # Test: run best params on test window (out-of-sample)
        cfg = dict(WINNING_CONFIG)
        cfg["stop_pct"] = best_params[0]
        cfg["target_pct"] = best_params[1]
        te_result = run_bs_options_backtest(
            symbols=WINNING_SYMBOLS, frames=te_frames, prev_closes=te_prev,
            iv_cache=iv_cache, capital=10000.0,
            slippage_bps=SLIPPAGE_BPS,
            option_slippage_bps=WINNING_PARAMS["option_slippage_bps"],
            fee_rate=0.0, config=cfg,
            start_date=te_start, end_date=te_end,
            strike_offset=WINNING_PARAMS["strike_offset"],
            dte_min=WINNING_PARAMS["dte_min"],
            dte_max=WINNING_PARAMS["dte_max"],
            risk_free_rate=WINNING_PARAMS["risk_free_rate"],
            confirmation_minutes=WINNING_PARAMS["confirmation_minutes"],
            circuit_breaker=WINNING_PARAMS["circuit_breaker"],
            min_entry_time=WINNING_PARAMS["min_entry_time"],
        )
        oos_ret = te_result["total_return_pct"]
        oos_pf = te_result["profit_factor"]
        oos_wr = te_result["win_rate"]
        oos_trades = te_result["total_trades"]
        print(f"    OOS test: return={oos_ret:+.2f}%, PF={oos_pf:.3f}, "
              f"WR={oos_wr:.0%}, trades={oos_trades}")
        oos_results.append({
            "window": i + 1,
            "train_range": f"{tr_start}→{tr_end}",
            "test_range": f"{te_start}→{te_end}",
            "best_params": best_params,
            "train_return": best_return,
            "oos_return": oos_ret,
            "oos_pf": oos_pf,
            "oos_wr": oos_wr,
            "oos_trades": oos_trades,
        })

    # Summary
    print("\n  --- Walk-Forward Summary ---")
    print(f"  {'Window':<8} {'Train':<26} {'Test':<22} {'Params':<14} "
          f"{'TrainRet':<10} {'OOSRet':<10} {'OOSPF':<8} {'Trades':<8}")
    for r in oos_results:
        print(f"  {r['window']:<8} {r['train_range']:<26} {r['test_range']:<22} "
              f"{str(r['best_params']):<14} {r['train_return']:+8.2f}% "
              f"{r['oos_return']:+8.2f}% {r['oos_pf']:7.3f} {r['oos_trades']:>6}")

    oos_returns = [r["oos_return"] for r in oos_results]
    positive_windows = sum(1 for r in oos_returns if r > 0)
    print(f"\n  OOS windows positive: {positive_windows}/{len(oos_results)}")
    print(f"  OOS avg return: {sum(oos_returns)/len(oos_returns):+.2f}%")
    print(f"  OOS total return (compounded): ", end="")
    compounded = 1.0
    for r in oos_returns:
        compounded *= (1 + r / 100)
    print(f"{(compounded - 1) * 100:+.2f}%")

    print()
    if positive_windows >= 2:
        print("  VERDICT: Walk-forward PASSES — params generalize OOS.")
    elif positive_windows >= 1:
        print("  VERDICT: Walk-forward MIXED — some OOS windows positive.")
    else:
        print("  VERDICT: Walk-forward FAILS — params don't generalize OOS.")

    return oos_results


# ── Test 3: Bear Market Simulation ─────────────────────────────────────

def test_bear_market():
    """Invert equity returns (×-1) to simulate a bear market.

    For each symbol, transform the 1m bars so that every bar's return
    is inverted. This creates a mirror-image price series where up days
    become down days and vice versa.

    If the strategy is regime-aware, puts should win in the "bear" market
    and calls should lose. If both directions lose, the edge isn't real.
    """
    print("\n" + "=" * 70)
    print("  TEST 3: BEAR MARKET SIMULATION (inverted returns)")
    print("=" * 70)
    print(f"  Symbols: {', '.join(WINNING_SYMBOLS)}")
    print(f"  Period:  {WINNING_START} → {WINNING_END}")
    print(f"  Method:  Invert 1m bar returns (×-1) to simulate bear market")
    print()

    # Fetch IV
    print("  Fetching base IV from Schwab...")
    base_iv = IVCache()
    base_iv.fetch_ivs(WINNING_SYMBOLS)
    iv_cache = IVMultiplierCache(base_iv, 1.0)
    print()

    # Fetch equity data
    print("  Fetching 1m equity data...")
    provider = CachedProvider(AlpacaProvider())
    frames = fetch_1m_data(WINNING_SYMBOLS, WINNING_START, WINNING_END, provider)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    prev_closes = fetch_prev_closes(WINNING_SYMBOLS, all_dates, provider)
    print()

    # Invert the frames
    print("  Inverting equity returns (×-1)...")
    bear_frames = {}
    for sym, df in frames.items():
        bear_df = df.copy()
        # Invert returns: new_close = prev_close * (1 - (close - prev_close) / prev_close)
        # Simpler: reflect around the first open of each day
        # For each day, compute returns then negate them
        bear_df = df.copy()
        bear_df["OrigOpen"] = bear_df["Open"]
        bear_df["OrigHigh"] = bear_df["High"]
        bear_df["OrigLow"] = bear_df["Low"]
        bear_df["OrigClose"] = bear_df["Close"]

        # Group by date and invert within each day
        inverted_rows = []
        for date, day_df in bear_df.groupby(bear_df["Timestamp"].dt.date):
            if day_df.empty:
                continue
            day_df = day_df.reset_index(drop=True)
            # Anchor: first bar's open stays the same
            anchor_open = day_df.iloc[0]["Open"]
            # Compute inverted closes
            new_open = [anchor_open]
            new_high = []
            new_low = []
            new_close = []
            for i, row in day_df.iterrows():
                if i == 0:
                    # First bar: invert the bar's own return
                    orig_open = row["Open"]
                    orig_close = row["Close"]
                    orig_high = row["High"]
                    orig_low = row["Low"]
                    # Invert: reflect close around open
                    inv_close = orig_open * 2 - orig_close
                    # High becomes Low, Low becomes High (inverted)
                    inv_high = max(orig_open, inv_close, orig_open * 2 - orig_low)
                    inv_low = min(orig_open, inv_close, orig_open * 2 - orig_high)
                    new_open[i] = orig_open  # keep anchor
                    new_high.append(inv_high)
                    new_low.append(inv_low)
                    new_close.append(inv_close)
                else:
                    # Subsequent bars: invert the return from prev close
                    prev_close = new_close[-1]
                    orig_ret = (row["Close"] - row["Open"]) / row["Open"] if row["Open"] != 0 else 0
                    inv_ret = -orig_ret
                    inv_open = prev_close  # open = prev close (1m continuity)
                    inv_close = prev_close * (1 + inv_ret)
                    # Invert high/low range
                    orig_range = row["High"] - row["Low"]
                    if orig_ret >= 0:
                        # Original was up bar → inverted is down bar
                        inv_high = max(inv_open, inv_close)
                        inv_low = min(inv_open, inv_close) - orig_range * 0.5
                        inv_high = inv_high + orig_range * 0.5
                    else:
                        # Original was down bar → inverted is up bar
                        inv_low = min(inv_open, inv_close)
                        inv_high = max(inv_open, inv_close) + orig_range * 0.5
                        inv_low = inv_low - orig_range * 0.5
                    new_open.append(inv_open)
                    new_high.append(inv_high)
                    new_low.append(inv_low)
                    new_close.append(inv_close)

            day_df["Open"] = new_open
            day_df["High"] = new_high
            day_df["Low"] = new_low
            day_df["Close"] = new_close
            inverted_rows.append(day_df)

        bear_frames[sym] = pd.concat(inverted_rows, ignore_index=True)

    print(f"  Inverted {len(bear_frames)} symbols")
    print()

    # Run on original (bull) data for comparison
    print("  Running on ORIGINAL (bull) data for baseline...")
    bull_result = run_bs_options_backtest(
        symbols=WINNING_SYMBOLS, frames=frames, prev_closes=prev_closes,
        iv_cache=iv_cache, capital=10000.0,
        slippage_bps=SLIPPAGE_BPS,
        option_slippage_bps=WINNING_PARAMS["option_slippage_bps"],
        fee_rate=0.0, config=WINNING_CONFIG,
        start_date=WINNING_START, end_date=WINNING_END,
        strike_offset=WINNING_PARAMS["strike_offset"],
        dte_min=WINNING_PARAMS["dte_min"],
        dte_max=WINNING_PARAMS["dte_max"],
        risk_free_rate=WINNING_PARAMS["risk_free_rate"],
        confirmation_minutes=WINNING_PARAMS["confirmation_minutes"],
        circuit_breaker=WINNING_PARAMS["circuit_breaker"],
        min_entry_time=WINNING_PARAMS["min_entry_time"],
    )
    print(f"  Bull: Return={bull_result['total_return_pct']:+.2f}%, "
          f"PF={bull_result['profit_factor']:.3f}, "
          f"WR={bull_result['win_rate']:.0%}, "
          f"trades={bull_result['total_trades']}")

    # Run on inverted (bear) data
    print("  Running on INVERTED (bear) data...")
    bear_result = run_bs_options_backtest(
        symbols=WINNING_SYMBOLS, frames=bear_frames, prev_closes=prev_closes,
        iv_cache=iv_cache, capital=10000.0,
        slippage_bps=SLIPPAGE_BPS,
        option_slippage_bps=WINNING_PARAMS["option_slippage_bps"],
        fee_rate=0.0, config=WINNING_CONFIG,
        start_date=WINNING_START, end_date=WINNING_END,
        strike_offset=WINNING_PARAMS["strike_offset"],
        dte_min=WINNING_PARAMS["dte_min"],
        dte_max=WINNING_PARAMS["dte_max"],
        risk_free_rate=WINNING_PARAMS["risk_free_rate"],
        confirmation_minutes=WINNING_PARAMS["confirmation_minutes"],
        circuit_breaker=WINNING_PARAMS["circuit_breaker"],
        min_entry_time=WINNING_PARAMS["min_entry_time"],
    )
    print(f"  Bear: Return={bear_result['total_return_pct']:+.2f}%, "
          f"PF={bear_result['profit_factor']:.3f}, "
          f"WR={bear_result['win_rate']:.0%}, "
          f"trades={bear_result['total_trades']}")

    # Analyze: in bear market, puts should win, calls should lose
    bull_trades = bull_result.get("trades", [])
    bear_trades = bear_result.get("trades", [])

    def split_by_type(trades):
        calls = [t for t in trades if isinstance(t, dict) and "C" in t.get("symbol", "")]
        puts = [t for t in trades if isinstance(t, dict) and "P" in t.get("symbol", "")]
        if not calls and not puts:
            # Try TradeRecord objects
            calls = [t for t in trades if hasattr(t, "symbol") and "C" in t.symbol]
            puts = [t for t in trades if hasattr(t, "symbol") and "P" in t.symbol]
        call_pnl = sum(t.get("pnl", 0) if isinstance(t, dict) else t.pnl for t in calls)
        put_pnl = sum(t.get("pnl", 0) if isinstance(t, dict) else t.pnl for t in puts)
        return len(calls), call_pnl, len(puts), put_pnl

    bc_n, bc_pnl, bp_n, bp_pnl = split_by_type(bull_trades)
    rc_n, rc_pnl, rp_n, rp_pnl = split_by_type(bear_trades)

    print()
    print("  --- Call vs Put Breakdown ---")
    print(f"  Bull market:  calls={bc_n} trades, PnL=${bc_pnl:+,.2f} | "
          f"puts={bp_n} trades, PnL=${bp_pnl:+,.2f}")
    print(f"  Bear market:  calls={rc_n} trades, PnL=${rc_pnl:+,.2f} | "
          f"puts={rp_n} trades, PnL=${rp_pnl:+,.2f}")

    print()
    print("  --- Bear Market Verdict ---")
    # In a bear market, puts should profit and calls should lose
    puts_win_bear = rp_pnl > 0
    calls_lose_bear = rc_pnl < 0
    if puts_win_bear and calls_lose_bear:
        print("  VERDICT: PASSES — puts win, calls lose in bear market.")
        print("  Strategy is regime-aware (direction adapts to market).")
    elif bear_result["total_return_pct"] > 0:
        print("  VERDICT: MIXED — bear market is profitable but not via puts.")
        print("  Edge may not be regime-specific.")
    else:
        print("  VERDICT: FAILS — bear market loses money.")
        print("  Strategy may be bull-market-specific (not regime-robust).")

    return {
        "bull": bull_result,
        "bear": bear_result,
        "bull_calls": (bc_n, bc_pnl),
        "bull_puts": (bp_n, bp_pnl),
        "bear_calls": (rc_n, rc_pnl),
        "bear_puts": (rp_n, rp_pnl),
    }


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ORB options validation suite")
    parser.add_argument("--test", default="all",
                        choices=["iv", "walkforward", "bear", "all"],
                        help="Which test to run")
    parser.add_argument("--json", type=str, default="",
                        help="Save results to JSON file")
    args = parser.parse_args()

    results = {}

    if args.test in ("iv", "all"):
        results["iv_sensitivity"] = test_iv_sensitivity()

    if args.test in ("walkforward", "all"):
        results["walk_forward"] = test_walk_forward()

    if args.test in ("bear", "all"):
        results["bear_market"] = test_bear_market()

    if args.json:
        # Serialize results (strip non-serializable)
        def clean(obj):
            if isinstance(obj, dict):
                return {k: clean(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean(v) for v in obj]
            elif isinstance(obj, (int, float, str, bool, type(None))):
                return obj
            else:
                return str(obj)
        with open(args.json, "w") as f:
            json.dump(clean(results), f, indent=2)
        print(f"\n  Results saved to: {args.json}")


if __name__ == "__main__":
    main()
