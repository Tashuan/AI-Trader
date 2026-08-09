#!/usr/bin/env python3
"""1D timeframe parameter sweep for CryptoRunner — aggressive position trading.

Tests daily-candle position trading with varying:
  - Symbol count (2, 5, 10, full watchlist)
  - Risk per trade (0.5%, 1%, 2%, 3%, 5%)
  - Sizing mode (risk_based vs notional)
  - TP/SL (wider for daily moves)
  - Trailing stop (let winners run)
  - Max positions (1, 3, 5)
  - Min signals (3, 4, 5)

Usage:
    python3 sweep_1d.py
    python3 sweep_1d.py --goal-target 100 --goal-max-loss 500
    python3 sweep_1d.py --json /tmp/sweep_1d.json
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


def run_single(params, symbols, start, end, capital, goal_target=None, goal_max_loss=None):
    bt = CryptoScanBacktester(
        symbols, params, start, end, capital, "1d", 5.0,
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
    print(f"\n{'='*145}")
    print(f"{'Experiment':<40} {'Return%':>8} {'Sharpe':>8} {'MaxDD%':>8} {'WinRate':>8} {'PF':>6} {'Trades':>7} {'Hold(h)':>8} {'$/trade':>8} {'Goal':>12}")
    print(f"{'-'*40} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*12}")
    for name, s in results.items():
        per_trade = (s["final_equity"] - 10000) / s["trades"] if s["trades"] > 0 else 0
        goal_str = s["goal_status"] if s["goal_status"] != "n/a" else "-"
        marker = ""
        if s["goal_achieved"]:
            halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
            marker = f" ✓{halt_day}"
        elif s["goal_status"] == "max_loss_hit":
            halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
            marker = f" ✗{halt_day}"
        print(f"{name:<40} {s['return_pct']:>8.2f} {s['sharpe']:>8.3f} {s['max_dd']:>8.2f} {s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} {s['avg_hold_h']:>8.1f} {per_trade:>+8.2f} {goal_str:>12}{marker}")
    print(f"{'='*145}\n")


# Symbol groups
SYMBOLS_2 = ["BTC", "SOL"]
SYMBOLS_5 = ["BTC", "SOL", "DOGE", "AVAX", "LINK"]
SYMBOLS_10 = ["BTC", "SOL", "DOGE", "AVAX", "LINK", "XRP", "ADA", "DOT", "LTC", "ATOM"]
SYMBOLS_FULL = ["BTC", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK",
                "DOT", "LTC", "UNI", "ATOM", "NEAR", "ARB", "OP", "INJ",
                "SUI", "SEI", "TIA", "PEPE", "SHIB", "MATIC", "APT", "BCH"]


def main():
    parser = argparse.ArgumentParser(description="1D aggressive parameter sweep for CryptoRunner")
    parser.add_argument("--start", type=str, default="2025-08-09")
    parser.add_argument("--end", type=str, default="2026-08-09")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--goal-target", type=float, default=None)
    parser.add_argument("--goal-max-loss", type=float, default=None)
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    base_params = effective_params("CryptoRunner", "crypto_swing")

    # 1d baseline overrides — stagnation in days, grace in days
    DAILY_BASE = [
        ("exit_rules.stagnation_hours", 3),          # 3 days
        ("exit_rules.momentum_death_grace_hours", 5), # 5 days
    ]

    # ─── Experiment Groups ──────────────────────────────────────
    # Each: (name, symbols, [(path, value), ...] overrides beyond DAILY_BASE)

    experiments = []

    # ── Group A: Symbol count (baseline 1d params, 0.5% risk) ──
    experiments.append(("A1_symbols_2", SYMBOLS_2, []))
    experiments.append(("A2_symbols_5", SYMBOLS_5, []))
    experiments.append(("A3_symbols_10", SYMBOLS_10, []))
    experiments.append(("A4_symbols_full", SYMBOLS_FULL, []))

    # ── Group B: Risk per trade (5 symbols, risk_based sizing) ──
    for risk in [0.5, 1.0, 2.0, 3.0, 5.0]:
        experiments.append((f"B_risk_{risk}pct", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", risk),
        ]))

    # ── Group C: Sizing mode (5 symbols, notional mode at various %) ──
    for pct in [10, 20, 30, 50, 80]:
        experiments.append((f"C_notional_{pct}pct", SYMBOLS_5, [
            ("risk_controls.sizing_mode", "notional"),
            ("position_sizing.normal_sizing_max_pct", float(pct)),
            ("position_sizing.normal_sizing_min_pct", float(max(5, pct - 5))),
        ]))

    # ── Group D: Max positions (5 symbols, 2% risk) ──
    for mp in [1, 3, 5, 8]:
        experiments.append((f"D_maxpos_{mp}", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", 2.0),
            ("position_sizing.max_positions", mp),
        ]))

    # ── Group E: TP/SL on daily (5 symbols, 2% risk, 3 max pos) ──
    for tp, sl in [(6, -3), (8, -5), (10, -5), (12, -6), (15, -7), (8, -3), (10, -3)]:
        experiments.append((f"E_tp{tp}_sl{abs(sl)}", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", 2.0),
            ("position_sizing.max_positions", 3),
            ("exit_rules.take_profit_pct", float(tp)),
            ("exit_rules.stop_loss_pct", float(sl)),
        ]))

    # ── Group F: Trailing stop (5 symbols, 2% risk, 3 max pos) ──
    for act, trail in [(5, 3), (7, 5), (10, 7), (7, 3), (10, 5), (15, 10)]:
        experiments.append((f"F_trail_{act}_{trail}", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", 2.0),
            ("position_sizing.max_positions", 3),
            ("exit_rules.trailing_activation_pct", float(act)),
            ("exit_rules.trailing_sl_pct", float(trail)),
        ]))

    # ── Group G: Min signals (5 symbols, 2% risk, 3 max pos) ──
    for ms in [3, 4, 5, 6]:
        experiments.append((f"G_minsig_{ms}", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", 2.0),
            ("position_sizing.max_positions", 3),
            ("entry_criteria.min_signals", ms),
        ]))

    # ── Group H: Stagnation days (5 symbols, 2% risk, 3 max pos) ──
    for days in [1, 2, 3, 5, 7]:
        experiments.append((f"H_stagnation_{days}d", SYMBOLS_5, [
            ("risk_controls.risk_per_trade_pct", 2.0),
            ("position_sizing.max_positions", 3),
            ("exit_rules.stagnation_hours", days),  # on 1d, hours=cycles=days
        ]))

    # ── Group I: Combo — best of each group combined ──
    # Will be filled after analysis, but let's pre-set some promising combos
    experiments.append(("I_combo_aggressive_5sym", SYMBOLS_5, [
        ("risk_controls.risk_per_trade_pct", 3.0),
        ("position_sizing.max_positions", 5),
        ("exit_rules.take_profit_pct", 10.0),
        ("exit_rules.stop_loss_pct", -5.0),
        ("exit_rules.trailing_activation_pct", 7.0),
        ("exit_rules.trailing_sl_pct", 5.0),
        ("exit_rules.stagnation_hours", 2),
        ("entry_criteria.min_signals", 4),
    ]))

    experiments.append(("I_combo_aggressive_10sym", SYMBOLS_10, [
        ("risk_controls.risk_per_trade_pct", 3.0),
        ("position_sizing.max_positions", 5),
        ("exit_rules.take_profit_pct", 10.0),
        ("exit_rules.stop_loss_pct", -5.0),
        ("exit_rules.trailing_activation_pct", 7.0),
        ("exit_rules.trailing_sl_pct", 5.0),
        ("exit_rules.stagnation_hours", 2),
        ("entry_criteria.min_signals", 4),
    ]))

    experiments.append(("I_combo_max_aggressive", SYMBOLS_10, [
        ("risk_controls.risk_per_trade_pct", 5.0),
        ("position_sizing.max_positions", 8),
        ("exit_rules.take_profit_pct", 12.0),
        ("exit_rules.stop_loss_pct", -5.0),
        ("exit_rules.trailing_activation_pct", 10.0),
        ("exit_rules.trailing_sl_pct", 7.0),
        ("exit_rules.stagnation_hours", 2),
        ("entry_criteria.min_signals", 3),
    ]))

    # ── Run all experiments ──
    results = {}
    total = len(experiments)
    print(f"Running {total} daily-candle experiments from {args.start} to {args.end} (${args.capital:,.0f})")
    if args.goal_target:
        print(f"Goal: target=${args.goal_target}, max_loss={args.goal_max_loss}")
    print()

    for i, (name, symbols, overrides) in enumerate(experiments, 1):
        params = copy.deepcopy(base_params)
        deep_set_many(params, DAILY_BASE)
        deep_set_many(params, overrides)
        t0 = time.time()
        try:
            r = run_single(params, symbols, args.start, args.end, args.capital,
                           args.goal_target, args.goal_max_loss)
            s = extract_summary(r)
            elapsed = time.time() - t0
            goal_marker = ""
            if s["goal_achieved"]:
                halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
                goal_marker = f" ✓{halt_day}"
            elif s["goal_status"] == "max_loss_hit":
                halt_day = s["goal_halt_ts"][:10] if s["goal_halt_ts"] else "?"
                goal_marker = f" ✗{halt_day}"
            print(f"  [{i:>2}/{total}] {name:<35} {len(symbols)}sym ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} dd={s['max_dd']:.1f}% trades={s['trades']:>3} pf={s['profit_factor']:.2f} ({elapsed:.1f}s){goal_marker}")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [{i:>2}/{total}] {name:<35} ERROR: {e} ({elapsed:.1f}s)")
            s = {"return_pct": 0, "final_equity": args.capital, "sharpe": 0, "max_dd": 0,
                 "win_rate": 0, "profit_factor": 0, "trades": 0, "avg_hold_h": 0,
                 "goal_status": "error", "goal_achieved": False, "goal_halt_ts": None}
        results[name] = s

    print_table(results)

    # ── Top 5 by return ──
    sorted_by_return = sorted(results.items(), key=lambda x: x[1]["return_pct"], reverse=True)
    print("── Top 5 by Return ──")
    for name, s in sorted_by_return[:5]:
        print(f"  {name:<35} ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} pf={s['profit_factor']:.2f} trades={s['trades']} dd={s['max_dd']:.1f}%")

    print("\n── Top 5 by Sharpe ──")
    sorted_by_sharpe = sorted(results.items(), key=lambda x: x[1]["sharpe"], reverse=True)
    for name, s in sorted_by_sharpe[:5]:
        print(f"  {name:<35} ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} pf={s['profit_factor']:.2f} trades={s['trades']} dd={s['max_dd']:.1f}%")

    print("\n── Top 5 by Profit Factor ──")
    sorted_by_pf = sorted(results.items(), key=lambda x: x[1]["profit_factor"], reverse=True)
    for name, s in sorted_by_pf[:5]:
        print(f"  {name:<35} ret={s['return_pct']:+.2f}% sharpe={s['sharpe']:.3f} pf={s['profit_factor']:.2f} trades={s['trades']} dd={s['max_dd']:.1f}%")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.json}")


if __name__ == "__main__":
    main()
