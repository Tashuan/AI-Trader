#!/usr/bin/env python3
"""HITL ablation + vol-filter sweep + threshold tuning for the Fence Bar strategy.

Modes:
  ablation  — baseline, full HITL, HITL minus each detector (one ATR level)
  atr_sweep — baseline vs full HITL across ATR 1.0/1.2/1.5/1.8
  tune      — sweep breakeven and early-exit MFE thresholds at fixed ATR

Usage:
    python3 research/strategy_search/hitl_ablation.py ablation --atr 1.2
    python3 research/strategy_search/hitl_ablation.py atr_sweep
    python3 research/strategy_search/hitl_ablation.py tune --atr 1.2
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


def make_hitl_cls(hitl_enabled: dict[str, bool], hitl_thresholds: dict[str, float] | None = None, name: str = "HITL"):
    """Create a pre-bound HumanInLoopBacktester subclass with fixed config."""
    class _BoundHITL(HumanInLoopBacktester):
        def __init__(self, *a, **kw):
            super().__init__(*a, hitl_enabled=hitl_enabled, hitl_thresholds=hitl_thresholds, **kw)
    _BoundHITL.__name__ = name
    return _BoundHITL


def run_variant(params, backtester_cls, name, args):
    print(f"\n=== {name} ===", file=sys.stderr)
    return run_walk_forward(
        backtester_cls, params,
        start=args.start, end=args.end,
        slippage_bps=args.slippage, fee_rate=args.fee_rate,
        capital=args.capital, max_symbols=args.max_symbols,
        strategy_name=name,
    )


def print_table(results, title=""):
    print("\n" + "=" * 80)
    if title:
        print(f"  {title}")
    print(f"  {'Variant':<32} {'Return':>10} {'Trades':>8} {'ActiveW':>10} {'AggPF':>8} {'MaxDD':>8}")
    print("  " + "-" * 76)
    for v in results:
        print(f"  {v['name']:<32} {v['total_return_pct']:>+9.2f}% {v['total_trades']:>8} "
              f"{v['active_windows']:>3}/{v['num_windows']:<6} {v['avg_profit_factor']:>8.2f} {v['max_drawdown_pct']:>7.2f}%")
    print("=" * 80)


# ── Mode: ablation ─────────────────────────────────────────────────────

def mode_ablation(args):
    params = build_params(args.atr)
    results = []
    detectors = ["vol_override", "entry_veto", "breakeven", "early_exit"]

    baseline = run_variant(params, FenceBarBacktester, "baseline", args)
    results.append({"name": "baseline", **baseline})

    full = run_variant(params, HumanInLoopBacktester, "hitl_full", args)
    results.append({"name": "hitl_full", **full})

    for det in detectors:
        enabled = {k: True for k in detectors}
        enabled[det] = False
        cls = make_hitl_cls(enabled, name=f"HITL_minus_{det}")
        r = run_variant(params, cls, f"hitl_minus_{det}", args)
        results.append({"name": f"hitl_minus_{det}", **r})

    print_table(results, title=f"ATR threshold: {args.atr}%")
    _save(args, {"atr_threshold": args.atr, "variants": results})


# ── Mode: atr_sweep ────────────────────────────────────────────────────

def mode_atr_sweep(args):
    atr_levels = [1.0, 1.2, 1.5, 1.8]
    results = []
    for atr in atr_levels:
        params = build_params(atr)
        baseline = run_variant(params, FenceBarBacktester, f"baseline_atr{atr}", args)
        results.append({"name": f"baseline_atr{atr}", "atr": atr, **baseline})
        full = run_variant(params, HumanInLoopBacktester, f"hitl_atr{atr}", args)
        results.append({"name": f"hitl_atr{atr}", "atr": atr, **full})

    print_table(results, title="ATR sweep: baseline vs full HITL")
    _save(args, {"atr_sweep": results})


# ── Mode: tune ─────────────────────────────────────────────────────────

def mode_tune(args):
    params = build_params(args.atr)
    results = []

    # Baseline (no HITL) for reference
    baseline = run_variant(params, FenceBarBacktester, "baseline", args)
    results.append({"name": "baseline", **baseline})

    # Full HITL with default thresholds
    full_default = run_variant(params, HumanInLoopBacktester, "hitl_default", args)
    results.append({"name": "hitl_default", **full_default})

    # No-breakeven (best from ablation)
    no_be = make_hitl_cls({"vol_override": True, "entry_veto": True, "breakeven": False, "early_exit": True}, name="HITL_no_be")
    r = run_variant(params, no_be, "hitl_no_breakeven", args)
    results.append({"name": "hitl_no_breakeven", **r})

    # Breakeven threshold sweep: higher MFE required, longer stalls
    be_configs = [
        ("be_mfe0.8_stall30", {"mfe_breakeven_pct": 0.8, "mfe_breakeven_stall_minutes": 30}),
        ("be_mfe1.0_stall30", {"mfe_breakeven_pct": 1.0, "mfe_breakeven_stall_minutes": 30}),
        ("be_mfe1.0_stall45", {"mfe_breakeven_pct": 1.0, "mfe_breakeven_stall_minutes": 45}),
        ("be_mfe1.5_stall30", {"mfe_breakeven_pct": 1.5, "mfe_breakeven_stall_minutes": 30}),
    ]
    for label, be_overrides in be_configs:
        thresholds = {
            "mfe_breakeven_pct": 0.5,
            "mfe_breakeven_entry_minutes": 10,
            "mfe_breakeven_stall_minutes": 15,
            "mfe_early_pct": 0.5,
            "mfe_early_stall_minutes": 30,
            "mfe_early_after_time": "11:00",
        }
        thresholds.update(be_overrides)
        cls = make_hitl_cls(
            {"vol_override": True, "entry_veto": True, "breakeven": True, "early_exit": True},
            hitl_thresholds=thresholds, name=label,
        )
        r = run_variant(params, cls, label, args)
        results.append({"name": label, **r})

    # Early-exit threshold sweep: lower MFE, shorter stall
    ee_configs = [
        ("ee_mfe0.3_stall20", {"mfe_early_pct": 0.3, "mfe_early_stall_minutes": 20}),
        ("ee_mfe0.3_stall15", {"mfe_early_pct": 0.3, "mfe_early_stall_minutes": 15}),
        ("ee_mfe0.5_stall15", {"mfe_early_pct": 0.5, "mfe_early_stall_minutes": 15}),
        ("ee_mfe0.8_stall45", {"mfe_early_pct": 0.8, "mfe_early_stall_minutes": 45}),
    ]
    for label, ee_overrides in ee_configs:
        thresholds = {
            "mfe_breakeven_pct": 0.5,
            "mfe_breakeven_entry_minutes": 10,
            "mfe_breakeven_stall_minutes": 15,
            "mfe_early_pct": 0.5,
            "mfe_early_stall_minutes": 30,
            "mfe_early_after_time": "11:00",
        }
        thresholds.update(ee_overrides)
        cls = make_hitl_cls(
            {"vol_override": True, "entry_veto": True, "breakeven": False, "early_exit": True},
            hitl_thresholds=thresholds, name=label,
        )
        r = run_variant(params, cls, label, args)
        results.append({"name": label, **r})

    print_table(results, title=f"Threshold tuning at ATR {args.atr}%")
    _save(args, {"atr_threshold": args.atr, "tune_results": results})


def _save(args, data):
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\nSaved to: {out}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="HITL ablation / ATR sweep / threshold tuning")
    parser.add_argument("mode", choices=["ablation", "atr_sweep", "tune"], help="Run mode")
    parser.add_argument("--start", default="2024-10-01", help="Backtest start date")
    parser.add_argument("--end", default="2026-08-11", help="Backtest end date")
    parser.add_argument("--atr", type=float, default=1.2, help="SPY ATR threshold %%")
    parser.add_argument("--slippage", type=float, default=5.0, help="Slippage in bps")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fee rate per fill")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    parser.add_argument("--max-symbols", type=int, default=15, help="Max symbols per window")
    parser.add_argument("--json", default=str(RESEARCH_DIR / "hitl_ablation.json"), help="Output JSON path")
    args = parser.parse_args()

    if args.mode == "ablation":
        mode_ablation(args)
    elif args.mode == "atr_sweep":
        mode_atr_sweep(args)
    elif args.mode == "tune":
        mode_tune(args)


if __name__ == "__main__":
    main()
