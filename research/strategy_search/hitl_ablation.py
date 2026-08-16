#!/usr/bin/env python3
"""HITL ablation + vol-filter sweep for the Fence Bar strategy.

For a given ATR threshold, runs:
  - baseline (no HITL)
  - full HITL (all 4 detectors)
  - HITL minus vol_override
  - HITL minus entry_veto
  - HITL minus breakeven
  - HITL minus early_exit

Usage:
    python3 research/strategy_search/hitl_ablation.py --atr 1.5
    python3 research/strategy_search/hitl_ablation.py --atr 1.2 --json out.json
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


def build_params(atr_threshold: float):
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
            "spy_atr_threshold": atr_threshold,
        },
    }
    return deep_merge(FENCE_BAR_DEFAULTS, override)


def run_variant(params, backtester_cls, name, args):
    print(f"\n=== {name} ===", file=sys.stderr)
    return run_walk_forward(
        backtester_cls, params,
        start=args.start, end=args.end,
        slippage_bps=args.slippage, fee_rate=args.fee_rate,
        capital=args.capital, max_symbols=args.max_symbols,
        strategy_name=name,
    )


def run_ablation(args):
    params = build_params(args.atr)
    results = {"atr_threshold": args.atr, "variants": []}

    # Baseline
    baseline = run_variant(params, FenceBarBacktester, "baseline", args)
    results["variants"].append({"name": "baseline", **baseline})

    # Full HITL
    hitl = run_variant(params, HumanInLoopBacktester, "hitl_full", args)
    results["variants"].append({"name": "hitl_full", **hitl})

    # Ablation: disable one detector at a time
    for detector in ["vol_override", "entry_veto", "breakeven", "early_exit"]:
        enabled = {k: True for k in ["vol_override", "entry_veto", "breakeven", "early_exit"]}
        enabled[detector] = False
        # Pass hitl_enabled into the backtester via the class binding isn't straightforward.
        # Instead, the backtester class needs to accept the flag. We can't easily vary per call.
        # Simpler: run full HITL for now and note which detector is disabled via a wrapper.
        # But run_walk_forward instantiates with no extra kwargs. We can't pass hitl_enabled.
        # Workaround: set detector-specific param overrides in params to disable the logic.
        # Each detector is controlled by hitl_enabled dict passed to __init__, but run_walk_forward
        # does not support passing extra kwargs. Modify run_walk_forward? Or pre-bind a subclass.
        # We'll create a pre-bound subclass for this run.
        class _PartialHITL(HumanInLoopBacktester):
            def __init__(self, *a, **kw):
                super().__init__(*a, hitl_enabled=enabled, **kw)
        _PartialHITL.__name__ = f"HITL_minus_{detector}"
        r = run_variant(params, _PartialHITL, f"hitl_minus_{detector}", args)
        results["variants"].append({"name": f"hitl_minus_{detector}", **r})

    # Summary
    print("\n" + "=" * 70)
    print(f"  ATR threshold: {args.atr}%")
    print(f"  {'Variant':<22} {'Return':>10} {'Trades':>8} {'ActiveW':>10} {'AvgPF':>8} {'MaxDD':>8}")
    print("  " + "-" * 66)
    for v in results["variants"]:
        print(f"  {v['name']:<22} {v['total_return_pct']:>+9.2f}% {v['total_trades']:>8} "
              f"{v['active_windows']:>3}/{v['num_windows']:<6} {v['avg_profit_factor']:>8.2f} {v['max_drawdown_pct']:>7.2f}%")
    print("=" * 70)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nSaved to: {out}", file=sys.stderr)

    return results


def main():
    parser = argparse.ArgumentParser(description="HITL ablation and vol-filter sweep")
    parser.add_argument("--start", default="2024-10-01", help="Backtest start date")
    parser.add_argument("--end", default="2026-08-11", help="Backtest end date")
    parser.add_argument("--atr", type=float, default=1.5, help="SPY ATR threshold %%")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate per fill")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    parser.add_argument("--max-symbols", type=int, default=15, help="Max symbols per window")
    parser.add_argument("--json", default=str(RESEARCH_DIR / "hitl_ablation.json"), help="Output JSON path")
    args = parser.parse_args()
    run_ablation(args)


if __name__ == "__main__":
    main()
