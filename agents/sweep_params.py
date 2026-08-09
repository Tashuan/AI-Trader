#!/usr/bin/env python3
"""Parameter sweep runner — tests single-variable changes against baseline.

Runs backtests with one parameter varied at a time, at $10k capital on BTC+SOL,
and prints a comparison table. Uses the in-process backtester (no subprocess)
for speed. Data is fetched once and reused across runs where possible.

Usage:
    python3 sweep_params.py
    python3 sweep_params.py --goal-target 100 --goal-max-loss 500
    python3 sweep_params.py --symbols BTC,SOL,DOGE
"""

import sys
import os
import copy
import json
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_registry import effective_params
from crypto_scan_backtester import CryptoScanBacktester


def run_single(params, symbols, start, end, capital, goal_target=None, goal_max_loss=None):
    """Run a single backtest and return the report dict."""
    bt = CryptoScanBacktester(
        symbols, params, start, end, capital, "4h", 5.0,
        goal_target=goal_target, goal_max_loss=goal_max_loss,
    )
    return bt.run().to_dict()


def extract_summary(r):
    """Extract key metrics from a report dict for the comparison table."""
    g = r.get("goal_simulation", {})
    return {
        "return_pct": r["total_return_pct"],
        "final_equity": r["final_equity"],
        "sharpe": r["sharpe_ratio"],
        "max_dd": r["max_drawdown_pct"],
        "win_rate": r["win_rate"],
        "profit_factor": r["profit_factor"],
        "trades": r["total_trades"],
        "avg_hold_h": r.get("avg_hold_hours", 0),
        "goal_status": g.get("status", "n/a"),
        "goal_achieved": g.get("goal_achieved", False),
        "goal_halt_ts": g.get("halt_timestamp", None),
    }


def print_table(results, baseline_key="baseline"):
    """Print a formatted comparison table."""
    headers = ["Experiment", "Return%", "Sharpe", "MaxDD%", "WinRate", "PF", "Trades", "Hold(h)", "Goal"]
    print(f"\n{'='*120}")
    print(f"{'Experiment':<30} {'Return%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'WinRate':>8} {'PF':>6} {'Trades':>7} {'Hold(h)':>8} {'Goal':>12}")
    print(f"{'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*12}")
    for name, s in results.items():
        goal_str = s["goal_status"] if s["goal_status"] != "n/a" else "-"
        marker = " ◄ baseline" if name == baseline_key else ""
        print(f"{name:<30} {s['return_pct']:>8.2f} {s['sharpe']:>8.3f} {s['max_dd']:>8.2f} {s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} {s['avg_hold_h']:>8.1f} {goal_str:>12}{marker}")
    print(f"{'='*120}\n")


def deep_set(d, path, value):
    """Set a nested dict value by dot-path (e.g. 'exit_rules.stagnation_hours')."""
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def main():
    parser = argparse.ArgumentParser(description="Run parameter sweeps for CryptoRunner")
    parser.add_argument("--symbols", type=str, default="BTC,SOL", help="Comma-separated symbols")
    parser.add_argument("--start", type=str, default="2025-08-09", help="Start date")
    parser.add_argument("--end", type=str, default="2026-08-09", help="End date")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital")
    parser.add_argument("--goal-target", type=float, default=None, help="Goal target $")
    parser.add_argument("--goal-max-loss", type=float, default=None, help="Goal max loss $")
    parser.add_argument("--json", type=str, default="", help="Save full results as JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    base_params = effective_params("CryptoRunner", "crypto_swing")

    # Define all experiments: name -> list of (path, value) overrides
    experiments = {}

    # Baseline
    experiments["baseline"] = []

    # Phase 1A: Stagnation sweep
    for h in [16, 24, 36, 48, 72]:
        experiments[f"stagnation_{h}h"] = [("exit_rules.stagnation_hours", h)]

    # Phase 1B: SL clamp sweep
    for sl in [-3.0, -4.0, -5.0, -6.0]:
        experiments[f"sl_{sl}pct"] = [("exit_rules.stop_loss_pct", sl)]

    # Phase 1C: TP sweep
    for tp in [4, 6, 8, 10, 12]:
        experiments[f"tp_{tp}pct"] = [("exit_rules.take_profit_pct", float(tp))]

    # Phase 1D: Trailing stop sweep (activation / sl)
    trail_combos = [(3, 2), (4, 3), (5, 3), (6, 4), (4, 2)]
    for act, sl in trail_combos:
        experiments[f"trail_{act}_{sl}"] = [
            ("exit_rules.trailing_activation_pct", float(act)),
            ("exit_rules.trailing_sl_pct", float(sl)),
        ]

    # Phase 1E: Min signals sweep
    for ms in [4, 5, 6, 7, 8]:
        experiments[f"minsig_{ms}"] = [("entry_criteria.min_signals", ms)]

    # Phase 1F: Vol ratio sweep
    for vr in [1.3, 1.5, 1.8, 2.0]:
        experiments[f"volratio_{vr}"] = [("entry_criteria.min_vol_ratio", vr)]

    # Run all experiments
    results = {}
    total = len(experiments)
    print(f"Running {total} experiments on {symbols} from {args.start} to {args.end} (${args.capital:,.0f})")
    if args.goal_target:
        print(f"Goal: target=${args.goal_target}, max_loss={args.goal_max_loss}")
    print()

    for i, (name, overrides) in enumerate(experiments.items(), 1):
        params = copy.deepcopy(base_params)
        for path, value in overrides:
            deep_set(params, path, value)
        t0 = time.time()
        r = run_single(params, symbols, args.start, args.end, args.capital,
                       args.goal_target, args.goal_max_loss)
        elapsed = time.time() - t0
        s = extract_summary(r)
        results[name] = s
        goal_marker = ""
        if s["goal_achieved"]:
            goal_marker = f" ✓ GOAL HIT at {s['goal_halt_ts']}"
        elif s["goal_status"] == "max_loss_hit":
            goal_marker = f" ✗ MAX LOSS at {s['goal_halt_ts']}"
        print(f"  [{i}/{total}] {name:<25} ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} trades={s['trades']:>3} ({elapsed:.1f}s){goal_marker}")

    print_table(results)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {args.json}")


if __name__ == "__main__":
    main()
