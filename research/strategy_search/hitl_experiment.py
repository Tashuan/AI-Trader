#!/usr/bin/env python3
"""Run the human-in-the-loop backtest experiment.

Compares the baseline Fence Bar strategy (1R target, ATR 1.8%, day-mode
vol filter, dynamic 15-symbol discovery) against the same strategy with the
four StockBoy supervisor decisions applied, using the same walk-forward harness.

Usage:
    python3 research/strategy_search/hitl_experiment.py
    python3 research/strategy_search/hitl_experiment.py --slippage 5 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"
sys.path.insert(0, str(AGENTS_DIR))
sys.path.insert(0, str(RESEARCH_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

from human_in_loop_backtester import HumanInLoopBacktester
from strategy_walk_forward import run_walk_forward


def run_comparison(args):
    # Winning overrides from 22-month walk-forward
    override = {
        "retest": {"enabled": False},
        "fence": {"min_range_pct": 0.35, "max_range_pct": 0.80},
        "risk": {
            "stop_mode": "fence_midpoint",
            "target_multiple_r": 1.0,
            "risk_per_trade_pct": 0.50,
            "max_trades_per_day": 1,
        },
        "exit": {
            "mode": "fixed_sl_tp",
            "trailing_pct": 0.3,
            "trailing_activation_pct": 0.3,
            "max_bars": 0,
        },
        "vol_filter": {
            "enabled": True,
            "mode": "day",
            "spy_vol_threshold": 1.0,
            "spy_atr_threshold": 1.8,
        },
    }
    params = deep_merge(FENCE_BAR_DEFAULTS, override)

    print(f"Running baseline vs HITL walk-forward on {args.start} to {args.end}", file=sys.stderr)
    print(f"Slippage: {args.slippage} bps | Max symbols: {args.max_symbols}", file=sys.stderr)

    # Baseline
    print("\n=== BASELINE ===", file=sys.stderr)
    baseline = run_walk_forward(
        FenceBarBacktester, params,
        start=args.start, end=args.end,
        slippage_bps=args.slippage, fee_rate=args.fee_rate,
        capital=args.capital, max_symbols=args.max_symbols,
        strategy_name="FenceBar",
    )

    # HITL
    print("\n=== HUMAN-IN-THE-LOOP ===", file=sys.stderr)
    hitl = run_walk_forward(
        HumanInLoopBacktester, params,
        start=args.start, end=args.end,
        slippage_bps=args.slippage, fee_rate=args.fee_rate,
        capital=args.capital, max_symbols=args.max_symbols,
        strategy_name="FenceBar+HITL",
    )

    result = {
        "start_date": args.start,
        "end_date": args.end,
        "slippage_bps": args.slippage,
        "fee_rate": args.fee_rate,
        "capital": args.capital,
        "max_symbols": args.max_symbols,
        "baseline": baseline,
        "hitl": hitl,
    }

    print("\n" + "=" * 70)
    print("  BASELINE")
    print(f"  Return: {baseline['total_return_pct']:+.2f}%")
    print(f"  Trades: {baseline['total_trades']}")
    print(f"  Active windows: {baseline['active_windows']} / {baseline['num_windows']}")
    print(f"  Avg PF: {baseline['avg_profit_factor']:.3f}")
    print(f"  Max DD: {baseline['max_drawdown_pct']:.2f}%")
    print("-" * 70)
    print("  HUMAN-IN-THE-LOOP")
    print(f"  Return: {hitl['total_return_pct']:+.2f}%")
    print(f"  Trades: {hitl['total_trades']}")
    print(f"  Active windows: {hitl['active_windows']} / {hitl['num_windows']}")
    print(f"  Avg PF: {hitl['avg_profit_factor']:.3f}")
    print(f"  Max DD: {hitl['max_drawdown_pct']:.2f}%")
    print("=" * 70)

    diff = hitl["total_return_pct"] - baseline["total_return_pct"]
    print(f"\nHITL delta: {diff:+.2f} percentage points")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nSaved to: {out}", file=sys.stderr)

    return result


def main():
    parser = argparse.ArgumentParser(description="Human-in-the-loop Fence Bar walk-forward experiment")
    parser.add_argument("--start", default="2024-10-01", help="Backtest start date")
    parser.add_argument("--end", default="2026-08-11", help="Backtest end date")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate per fill")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    parser.add_argument("--max-symbols", type=int, default=15, help="Max symbols per window")
    parser.add_argument("--json", default=str(RESEARCH_DIR / "hitl_experiment.json"), help="Output JSON path")
    args = parser.parse_args()
    run_comparison(args)


if __name__ == "__main__":
    main()
