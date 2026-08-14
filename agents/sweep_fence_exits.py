#!/usr/bin/env python3
"""Fence Bar exit-method sweep — tests different exit strategies per stock.

Runs the Fence Bar backtester with different exit modes on each individual
stock, plus the basket, to find which exit method works best where.

Usage:
    python3 sweep_fence_exits.py
    python3 sweep_fence_exits.py --start 2025-01-01 --end 2025-08-13
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
    bt = FenceBarBacktester(
        symbols=symbols, params=params, start_date=start, end_date=end,
        initial_capital=capital, slippage_bps=slippage_bps,
        fee_rate=fee_rate, provider=provider,
    )
    return bt.run().to_dict()


def extract_summary(r):
    d = r.get("diagnostics", {})
    # Count exit reasons
    exit_reasons = {}
    for t in r.get("trades", []):
        reason = t.get("reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    return {
        "return_pct": r["total_return_pct"],
        "sharpe": r["sharpe_ratio"],
        "max_dd": r["max_drawdown_pct"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "trades": r["total_trades"],
        "avg_hold_h": r.get("avg_hold_hours", 0),
        "avg_r": d.get("avg_r", 0),
        "exit_reasons": exit_reasons,
    }


def print_table(results):
    """Print a formatted comparison table grouped by stock."""
    # Group by stock
    stocks = {}
    for key, s in results.items():
        stock, exit_name = key.split(" | ")
        stocks.setdefault(stock, {})[exit_name] = s

    for stock, exit_results in stocks.items():
        print(f"\n  ── {stock} {'─' * (60 - len(stock))}")
        print(f"  {'Exit Method':<30} {'Return%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'WinRate':>8} {'PF':>6} {'Trades':>7} {'Hold(h)':>8} {'AvgR':>7}")
        print(f"  {'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*7}")
        for name, s in exit_results.items():
            marker = " ◄" if name == "fixed_2R" else ""
            print(f"  {name:<30} {s['return_pct']:>8.2f} {s['sharpe']:>8.3f} {s['max_dd']:>8.2f} "
                  f"{s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} "
                  f"{s['avg_hold_h']:>8.1f} {s['avg_r']:>7}{marker}")

        # Show exit reason breakdown for this stock
        if exit_results:
            best = max(exit_results.items(), key=lambda x: x[1]["profit_factor"] if x[1]["trades"] > 0 else -1)
            reasons = best[1].get("exit_reasons", {})
            if reasons:
                reason_str = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
                print(f"  Best PF: {best[0]} — exits: {reason_str}")


def make_variant(base_params, overrides):
    params = copy.deepcopy(base_params)
    for path, value in overrides.items():
        keys = path.split(".")
        d = params
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
    return params


def main():
    parser = argparse.ArgumentParser(description="Fence Bar exit-method sweep")
    parser.add_argument("--start", type=str, default="2025-01-01")
    parser.add_argument("--end", type=str, default="2025-08-13")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--provider", type=str, default="cache")
    parser.add_argument("--slippage", type=float, default=5.0)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    provider, provider_label = _build_scalp_provider(args.provider, True)

    # Test each stock individually + the basket
    test_groups = {
        "NVDA": ["NVDA"],
        "SPY": ["SPY"],
        "QQQ": ["QQQ"],
        "AMD": ["AMD"],
        "TSLA": ["TSLA"],
        "BASKET": ["QQQ", "SPY", "NVDA", "AMD", "TSLA"],
    }

    # Exit method variants
    exit_variants = {
        # Baseline: fixed 2R target
        "fixed_2R": {
            "exit.mode": "fixed_sl_tp",
            "risk.target_multiple_r": 2.0,
        },
        # Fixed 1R target
        "fixed_1R": {
            "exit.mode": "fixed_sl_tp",
            "risk.target_multiple_r": 1.0,
        },
        # Fixed 1.5R target
        "fixed_1.5R": {
            "exit.mode": "fixed_sl_tp",
            "risk.target_multiple_r": 1.5,
        },
        # Trailing stop: 0.3% trail, activate at 0.3% gain
        "trail_0.3pct": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.3,
            "exit.trailing_activation_pct": 0.3,
        },
        # Trailing stop: 0.5% trail, activate at 0.5% gain
        "trail_0.5pct": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.5,
            "exit.trailing_activation_pct": 0.5,
        },
        # Trailing stop: 0.5% trail, activate at 1.0% gain (let it run more)
        "trail_0.5_act1.0": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.5,
            "exit.trailing_activation_pct": 1.0,
        },
        # Trailing stop: 1.0% trail, activate at 0.5% gain (wider trail)
        "trail_1.0pct": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 1.0,
            "exit.trailing_activation_pct": 0.5,
        },
        # Time-based: exit after 6 bars (30 min)
        "time_6bars": {
            "exit.mode": "time_based",
            "exit.max_bars": 6,
        },
        # Time-based: exit after 12 bars (1 hour)
        "time_12bars": {
            "exit.mode": "time_based",
            "exit.max_bars": 12,
        },
        # Time-based: exit after 24 bars (2 hours)
        "time_24bars": {
            "exit.mode": "time_based",
            "exit.max_bars": 24,
        },
        # Trailing + time cap: trail 0.5%, max 12 bars
        "trail_0.5_time12": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.5,
            "exit.trailing_activation_pct": 0.5,
            "exit.max_bars": 12,
        },
        # Trailing + time cap: trail 0.3%, max 6 bars (fast scalp)
        "trail_0.3_time6": {
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.3,
            "exit.trailing_activation_pct": 0.3,
            "exit.max_bars": 6,
        },
        # No retest + trailing 0.5% (capture runners)
        "no_retest_trail_0.5": {
            "retest.enabled": False,
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.5,
            "exit.trailing_activation_pct": 0.5,
        },
        # No retest + trailing 0.3% + time cap 6
        "no_retest_trail_fast": {
            "retest.enabled": False,
            "exit.mode": "trailing",
            "exit.trailing_pct": 0.3,
            "exit.trailing_activation_pct": 0.3,
            "exit.max_bars": 6,
        },
    }

    base_params = copy.deepcopy(FENCE_BAR_DEFAULTS)
    results = {}
    full_reports = {}
    total = len(test_groups) * len(exit_variants)
    done = 0
    start_time = time.time()

    for group_name, symbols in test_groups.items():
        print(f"\n{'='*80}")
        print(f"  Testing {group_name} ({', '.join(symbols)})")
        print(f"{'='*80}")

        for exit_name, overrides in exit_variants.items():
            done += 1
            key = f"{group_name} | {exit_name}"
            params = make_variant(base_params, overrides)
            elapsed = time.time() - start_time
            print(f"  [{done}/{total}] {key}... ({elapsed:.0f}s)", flush=True)
            try:
                report = run_single(params, symbols, args.start, args.end, args.capital,
                                    provider, args.slippage, args.fee_rate)
                results[key] = extract_summary(report)
                full_reports[key] = report
            except Exception as e:
                print(f"    ERROR: {e}")
                results[key] = {"return_pct": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0,
                                "profit_factor": 0, "trades": 0, "avg_hold_h": 0, "avg_r": 0,
                                "exit_reasons": {}}

    print_table(results)

    # Overall best performers
    print(f"\n{'='*80}")
    print(f"  TOP 10 PROFITABLE VARIANTS (PF > 1.0, trades > 5)")
    print(f"{'='*80}")
    profitable = {k: v for k, v in results.items() if v["profit_factor"] > 1.0 and v["trades"] > 5}
    if profitable:
        for name, s in sorted(profitable.items(), key=lambda x: x[1]["profit_factor"], reverse=True)[:10]:
            print(f"  {name:<45} PF={s['profit_factor']:.3f}  Win={s['win_rate']:.1%}  "
                  f"Trades={s['trades']}  Return={s['return_pct']:+.2f}%  Hold={s['avg_hold_h']:.1f}h")
    else:
        print(f"  No profitable variants found.")
        # Show closest to profitable
        close = sorted(results.items(), key=lambda x: x[1]["profit_factor"], reverse=True)[:10]
        print(f"\n  Closest to profitable (top 10 by PF):")
        for name, s in close:
            if s["trades"] > 0:
                print(f"  {name:<45} PF={s['profit_factor']:.3f}  Win={s['win_rate']:.1%}  "
                      f"Trades={s['trades']}  Return={s['return_pct']:+.2f}%")

    total_elapsed = time.time() - start_time
    print(f"\n  Sweep completed in {total_elapsed:.0f}s ({total} experiments)")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summaries": results, "reports": full_reports}, f, indent=2, default=str)
        print(f"  Full results saved to: {args.json}")


if __name__ == "__main__":
    main()
