#!/usr/bin/env python3
"""Fence Bar parameter sweep — tests single-variable changes against baseline.

Runs the Fence Bar backtester with one parameter varied at a time and prints
a comparison table. Data is fetched once per provider and reused across runs.

Usage:
    python3 sweep_fence_bar.py
    python3 sweep_fence_bar.py --symbols QQQ,SPY,NVDA,AMD,TSLA
    python3 sweep_fence_bar.py --start 2025-01-01 --end 2025-08-13
    python3 sweep_fence_bar.py --provider cache
"""

import sys
import os
import copy
import json
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fence_bar_strategy import FENCE_BAR_DEFAULTS
from fence_bar_backtester import FenceBarBacktester
from strategy_lab import deep_merge
from run_backtest import _build_scalp_provider


def run_single(params, symbols, start, end, capital, provider, slippage_bps=5.0, fee_rate=0.001):
    """Run a single backtest and return the report dict."""
    bt = FenceBarBacktester(
        symbols=symbols, params=params, start_date=start, end_date=end,
        initial_capital=capital, slippage_bps=slippage_bps,
        fee_rate=fee_rate, provider=provider,
    )
    return bt.run().to_dict()


def extract_summary(r):
    """Extract key metrics from a report dict."""
    d = r.get("diagnostics", {})
    return {
        "return_pct": r["total_return_pct"],
        "final_equity": r["final_equity"],
        "sharpe": r["sharpe_ratio"],
        "max_dd": r["max_drawdown_pct"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "trades": r["total_trades"],
        "avg_hold_h": r.get("avg_hold_hours", 0),
        "sessions": d.get("sessions", 0),
        "entries": d.get("entries", 0),
        "breakouts": d.get("breakouts", 0),
        "retests": d.get("retests", 0),
        "fence_rejected": d.get("fence_rejected", 0),
        "avg_r": d.get("avg_r", 0),
    }


def print_table(results, baseline_key="baseline"):
    """Print a formatted comparison table."""
    print(f"\n{'='*140}")
    print(f"{'Experiment':<35} {'Return%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'WinRate':>8} {'PF':>6} "
          f"{'Trades':>7} {'Hold(h)':>8} {'Sessions':>9} {'Entries':>8} {'AvgR':>7}")
    print(f"{'-'*35} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*9} {'-'*8} {'-'*7}")
    for name, s in results.items():
        marker = " ◄ baseline" if name == baseline_key else ""
        print(f"{name:<35} {s['return_pct']:>8.2f} {s['sharpe']:>8.3f} {s['max_dd']:>8.2f} "
              f"{s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} "
              f"{s['avg_hold_h']:>8.1f} {s['sessions']:>9} {s['entries']:>8} {s['avg_r']:>7}{marker}")
    print(f"{'='*140}\n")


def deep_set(d, path, value):
    """Set a nested dict value by dot-path."""
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def make_variant(base_params, overrides):
    """Create a deep-copied variant with overrides applied."""
    params = copy.deepcopy(base_params)
    for path, value in overrides.items():
        deep_set(params, path, value)
    return params


def main():
    parser = argparse.ArgumentParser(description="Run parameter sweeps for Fence Bar strategy")
    parser.add_argument("--symbols", type=str, default="QQQ,SPY,NVDA,AMD,TSLA",
                        help="Comma-separated symbols (default: QQQ,SPY,NVDA,AMD,TSLA)")
    parser.add_argument("--start", type=str, default="2025-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2025-08-13", help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    parser.add_argument("--provider", type=str, default="auto",
                        choices=("auto", "alpaca", "schwab", "yfinance", "cache"),
                        help="Data provider")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate")
    parser.add_argument("--json", type=str, default="", help="Save full results as JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    provider, provider_label = _build_scalp_provider(args.provider, True)

    print(f"\nFence Bar Parameter Sweep")
    print(f"  Symbols:  {', '.join(symbols)}")
    print(f"  Period:   {args.start} → {args.end}")
    print(f"  Capital:  ${args.capital:,.2f}")
    print(f"  Provider: {provider_label}")
    print(f"  Slippage: {args.slippage} bps, Fee: {args.fee_rate}")
    print()

    base_params = copy.deepcopy(FENCE_BAR_DEFAULTS)

    # Define all experiments: name → overrides
    experiments = {
        # ── Baseline ──
        "baseline": {},

        # ── No retest (enter on breakout close) ──
        "no_retest": {
            "retest.enabled": False,
        },

        # ── Target variants ──
        "target_1R": {
            "risk.target_multiple_r": 1.0,
        },
        "target_1.5R": {
            "risk.target_multiple_r": 1.5,
        },
        "target_3R": {
            "risk.target_multiple_r": 3.0,
        },

        # ── Fence range variants ──
        "wide_fence_3pct": {
            "fence.max_range_pct": 3.0,
        },
        "wide_fence_5pct": {
            "fence.max_range_pct": 5.0,
        },
        "narrow_fence_min_005": {
            "fence.min_range_pct": 0.05,
        },

        # ── Breakout body requirement ──
        "no_body_requirement": {
            "breakout.require_body_outside": False,
        },

        # ── Later breakout cutoff ──
        "late_breakout_11am": {
            "session.latest_breakout": "11:00",
        },
        "late_breakout_12pm": {
            "session.latest_breakout": "12:00",
        },

        # ── SMA filter variants ──
        "no_sma_filter": {
            "anchor.enabled": False,
        },
        "sma_require_alignment": {
            "anchor.require_trend_alignment": True,
        },
        "sma_wide_distance_5pct": {
            "anchor.max_distance_pct": 5.0,
        },

        # ── Retest relaxation ──
        "retest_5_bars": {
            "retest.max_bars_after_breakout": 5,
        },
        "retest_no_wick_required": {
            "retest.require_wick_into_fence": False,
        },
        "retest_allow_same_bar": {
            "retest.allow_breakout_bar_retest": True,
        },

        # ── Combo: no retest + no SMA + wide fence ──
        "combo_aggressive": {
            "retest.enabled": False,
            "anchor.enabled": False,
            "fence.max_range_pct": 5.0,
        },

        # ── Combo: no retest + 1R target ──
        "combo_no_retest_1R": {
            "retest.enabled": False,
            "risk.target_multiple_r": 1.0,
        },

        # ── Combo: no retest + no body + late cutoff ──
        "combo_max_entries": {
            "retest.enabled": False,
            "breakout.require_body_outside": False,
            "session.latest_breakout": "11:00",
            "anchor.enabled": False,
        },

        # ── Combo: no retest + 1R + max entries ──
        "combo_max_entries_1R": {
            "retest.enabled": False,
            "breakout.require_body_outside": False,
            "session.latest_breakout": "11:00",
            "anchor.enabled": False,
            "risk.target_multiple_r": 1.0,
        },

        # ── Risk sizing variants ──
        "risk_1pct": {
            "risk.risk_per_trade_pct": 1.0,
        },
        "risk_2pct": {
            "risk.risk_per_trade_pct": 2.0,
        },
    }

    results = {}
    full_reports = {}
    total = len(experiments)
    start_time = time.time()

    for i, (name, overrides) in enumerate(experiments.items(), 1):
        params = make_variant(base_params, overrides)
        elapsed = time.time() - start_time
        print(f"  [{i}/{total}] Running {name}... ({elapsed:.0f}s elapsed)", flush=True)
        try:
            report = run_single(params, symbols, args.start, args.end, args.capital,
                                provider, args.slippage, args.fee_rate)
            results[name] = extract_summary(report)
            full_reports[name] = report
        except Exception as e:
            print(f"    ERROR: {e}")
            results[name] = {"return_pct": 0, "final_equity": 0, "sharpe": 0,
                             "max_dd": 0, "win_rate": 0, "profit_factor": 0,
                             "trades": 0, "avg_hold_h": 0, "sessions": 0,
                             "entries": 0, "breakouts": 0, "retests": 0,
                             "fence_rejected": 0, "avg_r": 0}
            full_reports[name] = {"error": str(e)}

    print_table(results)

    # Identify best performers
    profitable = {k: v for k, v in results.items() if v["profit_factor"] > 1.0 and v["trades"] > 0}
    if profitable:
        print(f"  Profitable variants (PF > 1.0):")
        for name, s in sorted(profitable.items(), key=lambda x: x[1]["profit_factor"], reverse=True):
            print(f"    {name:<35} PF={s['profit_factor']:.3f}  Win={s['win_rate']:.1%}  "
                  f"Trades={s['trades']}  Return={s['return_pct']:+.2f}%")
    else:
        print(f"  No profitable variants found (PF > 1.0)")

    best_trades = max(results.items(), key=lambda x: x[1]["trades"])
    print(f"\n  Most trades: {best_trades[0]} ({best_trades[1]['trades']} trades)")
    best_return = max(results.items(), key=lambda x: x[1]["return_pct"])
    print(f"  Best return: {best_return[0]} ({best_return[1]['return_pct']:+.2f}%)")
    best_pf = max(results.items(), key=lambda x: x[1]["profit_factor"] if x[1]["trades"] > 0 else -1)
    print(f"  Best PF:     {best_pf[0]} (PF={best_pf[1]['profit_factor']:.3f})")

    total_elapsed = time.time() - start_time
    print(f"\n  Sweep completed in {total_elapsed:.0f}s ({total} experiments)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summaries": results, "reports": full_reports}, f, indent=2, default=str)
        print(f"  Full results saved to: {args.json}")


if __name__ == "__main__":
    main()
