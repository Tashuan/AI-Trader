#!/usr/bin/env python3
"""Timeframe + strategy comparison for CryptoRunner.

Tests CryptoRunner across 1h (day trading), 4h (swing), and 1d (position)
timeframes with both standard and aggressive parameter variants. Answers:
"What style of trading should CryptoRunner actually do?"

Usage:
    python3 timeframe_comparison.py
    python3 timeframe_comparison.py --goal-target 100 --goal-max-loss 500
    python3 timeframe_comparison.py --symbols BTC,SOL,DOGE
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


def deep_set(d, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def deep_set_many(d, overrides):
    for path, value in overrides:
        deep_set(d, path, value)


def run_single(params, symbols, start, end, capital, interval, goal_target=None, goal_max_loss=None):
    bt = CryptoScanBacktester(
        symbols, params, start, end, capital, interval, 5.0,
        goal_target=goal_target, goal_max_loss=goal_max_loss,
    )
    return bt.run().to_dict()


def extract_summary(r):
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


def print_table(results):
    print(f"\n{'='*140}")
    print(f"{'Experiment':<35} {'Return%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'WinRate':>8} {'PF':>6} {'Trades':>7} {'Hold(h)':>8} {'Daily$':>8} {'Goal':>12}")
    print(f"{'-'*35} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*12}")
    for name, s in results.items():
        daily_pnl = s["return_pct"] / 365 * 100  # approx daily $ on $10k
        goal_str = s["goal_status"] if s["goal_status"] != "n/a" else "-"
        marker = ""
        if s["goal_achieved"]:
            marker = f" ✓{s['goal_halt_ts'][:10] if s['goal_halt_ts'] else ''}"
        elif s["goal_status"] == "max_loss_hit":
            marker = f" ✗MAXLOSS"
        print(f"{name:<35} {s['return_pct']:>8.2f} {s['sharpe']:>8.3f} {s['max_dd']:>8.2f} {s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} {s['avg_hold_h']:>8.1f} {daily_pnl:>+7.2f} {goal_str:>12}{marker}")
    print(f"{'='*140}\n")


def main():
    parser = argparse.ArgumentParser(description="Timeframe comparison for CryptoRunner")
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

    # ─── Experiment Definitions ───────────────────────────────────
    # Each experiment: (name, interval, [(path, value), ...] overrides)
    #
    # Parameter scaling logic:
    #   stagnation_hours on 1d = number of DAYS (not hours) due to bars_per_day=24
    #   So 1d: stagnation_hours=3 means 3 cycles = 3 days
    #   1h/4h: stagnation_hours=36 means 36 hours = 1.5 days (works correctly)

    experiments = []

    # ── 1H: Day Trading ──
    # 1h has 24 candles/day. More signals, more noise.
    # Stagnation 36h = 1.5 days (same as 4h baseline). TP/SL auto-scale via ATR.
    experiments.append(("1h_standard", "1h", []))

    # Aggressive 1h: more positions, bigger sizing, faster stagnation
    experiments.append(("1h_aggressive", "1h", [
        ("exit_rules.stagnation_hours", 16),        # 16h = faster bail
        ("exit_rules.take_profit_pct", 5.0),         # tighter TP
        ("exit_rules.stop_loss_pct", -3.0),          # tighter SL
        ("exit_rules.trailing_activation_pct", 4.0),
        ("exit_rules.trailing_sl_pct", 2.0),
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 18),
        ("position_sizing.normal_sizing_max_pct", 25),
        ("entry_criteria.min_signals", 4),
    ]))

    # ── 4H: Swing Trading (current baseline) ──
    experiments.append(("4h_standard", "4h", []))

    # Aggressive 4h: more positions, bigger sizing, tighter exits
    experiments.append(("4h_aggressive", "4h", [
        ("exit_rules.stagnation_hours", 24),         # faster bail
        ("exit_rules.take_profit_pct", 6.0),         # tighter TP
        ("exit_rules.stop_loss_pct", -3.0),          # tighter SL
        ("exit_rules.trailing_activation_pct", 6.0), # from sweep winner
        ("exit_rules.trailing_sl_pct", 4.0),         # from sweep winner
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 18),
        ("position_sizing.normal_sizing_max_pct", 25),
        ("entry_criteria.min_signals", 4),
    ]))

    # 4h aggressive + wider trailing (let winners run more)
    experiments.append(("4h_aggressive_wide_trail", "4h", [
        ("exit_rules.stagnation_hours", 24),
        ("exit_rules.take_profit_pct", 6.0),
        ("exit_rules.stop_loss_pct", -3.0),
        ("exit_rules.trailing_activation_pct", 5.0),
        ("exit_rules.trailing_sl_pct", 3.0),
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 18),
        ("position_sizing.normal_sizing_max_pct", 25),
        ("entry_criteria.min_signals", 4),
    ]))

    # ── 1D: Position Trading ──
    # 1d has 1 candle/day. Fewer signals, bigger moves, less noise.
    # Must override stagnation: 3 = 3 days, grace: 5 = 5 days
    experiments.append(("1d_standard", "1d", [
        ("exit_rules.stagnation_hours", 3),          # 3 days
        ("exit_rules.momentum_death_grace_hours", 5), # 5 days
    ]))

    # Aggressive 1d: tighter exits, more positions
    experiments.append(("1d_aggressive", "1d", [
        ("exit_rules.stagnation_hours", 2),           # 2 days
        ("exit_rules.momentum_death_grace_hours", 3), # 3 days
        ("exit_rules.take_profit_pct", 10.0),
        ("exit_rules.stop_loss_pct", -5.0),
        ("exit_rules.trailing_activation_pct", 7.0),
        ("exit_rules.trailing_sl_pct", 5.0),
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 18),
        ("position_sizing.normal_sizing_max_pct", 25),
        ("entry_criteria.min_signals", 4),
    ]))

    # ── Run all experiments ──
    results = {}
    total = len(experiments)
    print(f"Running {total} experiments on {symbols} from {args.start} to {args.end} (${args.capital:,.0f})")
    if args.goal_target:
        print(f"Goal: target=${args.goal_target}, max_loss={args.goal_max_loss}")
    print()

    for i, (name, interval, overrides) in enumerate(experiments, 1):
        params = copy.deepcopy(base_params)
        deep_set_many(params, overrides)
        t0 = time.time()
        r = run_single(params, symbols, args.start, args.end, args.capital,
                       interval, args.goal_target, args.goal_max_loss)
        elapsed = time.time() - t0
        s = extract_summary(r)
        results[name] = s
        goal_marker = ""
        if s["goal_achieved"]:
            halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
            goal_marker = f" ✓ GOAL HIT {halt_day}"
        elif s["goal_status"] == "max_loss_hit":
            halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
            goal_marker = f" ✗ MAX LOSS {halt_day}"
        print(f"  [{i}/{total}] {name:<30} {interval} ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} dd={s['max_dd']:.1f}% trades={s['trades']:>3} pf={s['profit_factor']:.2f} ({elapsed:.1f}s){goal_marker}")

    print_table(results)

    # ── Analysis ──
    print("── Analysis ──")
    best_return = max(results.values(), key=lambda x: x["return_pct"])
    best_sharpe = max(results.values(), key=lambda x: x["sharpe"])
    best_pf = max(results.values(), key=lambda x: x["profit_factor"])
    best_names = {v: k for k, v in [(name, s) for name, s in results.items()]}
    
    for metric, label in [(best_return, "return"), (best_sharpe, "Sharpe"), (best_pf, "profit factor")]:
        name = [k for k, v in results.items() if v is metric][0]
        print(f"  Best {label:>13}: {name}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.json}")


if __name__ == "__main__":
    main()
