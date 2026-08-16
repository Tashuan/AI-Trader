#!/usr/bin/env python3
"""Stop-mode comparison for the Fence Bar strategy at ATR 1.2%.

The fence-midpoint stop is too tight — 3 of 4 losers stop out within
5-12 minutes (~0.175-0.40% from entry with a 0.35-0.80% fence range).

Tests three variants:
  1. fence_midpoint       — baseline (current default, tight stop)
  2. fence_low_high       — full fence range as stop (wider)
  3. fence_midpoint @0.25 — tight stop, halved risk per trade

Uses the walk-forward harness helpers (generate_windows, discover_symbols,
build_provider) and FenceBarBacktester directly so we can collect both
aggregate stats and individual trade records for cross-variant comparison.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_walk_forward import (
    build_provider,
    discover_symbols,
    generate_windows,
    FEE_RATE,
    CAPITAL,
)
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

OUT_JSON = Path(__file__).resolve().parent / "stop_mode_test.json"

# ── Base override (deep_merge with FENCE_BAR_DEFAULTS) ───────────
BASE_OVERRIDE: dict[str, Any] = {
    "retest": {"enabled": False},
    "fence": {"min_range_pct": 0.35, "max_range_pct": 0.80},
    "risk": {
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
        "spy_atr_threshold": 1.2,
    },
}

# ── Three stop_mode variants ─────────────────────────────────────
VARIANTS = [
    {
        "label": "fence_midpoint",
        "description": "baseline — tight midpoint stop",
        "extra": {"risk": {"stop_mode": "fence_midpoint"}},
    },
    {
        "label": "fence_low_high",
        "description": "wider stop — full fence range",
        "extra": {"risk": {"stop_mode": "fence_low_high"}},
    },
    {
        "label": "fence_midpoint_r025",
        "description": "tight stop, halved risk per trade",
        "extra": {
            "risk": {
                "stop_mode": "fence_midpoint",
                "risk_per_trade_pct": 0.25,
            }
        },
    },
]

START = "2024-10-01"
END = "2026-08-11"
SLIPPAGE_BPS = 5.0
MAX_SYMBOLS = 15


def run_variant_walk_forward(
    override: dict[str, Any],
    provider,
) -> dict[str, Any]:
    """Run walk-forward for one variant, collecting trades for detail.

    Mirrors strategy_walk_forward.run_walk_forward but also accumulates
    individual TradeRecord objects so we can compare which trades differ.
    """
    windows = generate_windows(START, END)
    if not windows:
        return {"error": "No windows generated"}

    window_results = []
    all_trades: list[dict] = []

    for w in windows:
        symbols = discover_symbols(w["test_start"], provider, MAX_SYMBOLS)
        print(
            f"  Win {w['window_id']}: {w['test_start']} -> {w['test_end']} | "
            f"symbols={symbols[:5]}...",
            file=sys.stderr,
        )

        bt = FenceBarBacktester(
            symbols=symbols,
            params=override,
            start_date=w["test_start"],
            end_date=w["test_end"],
            initial_capital=CAPITAL,
            slippage_bps=SLIPPAGE_BPS,
            fee_rate=FEE_RATE,
            provider=provider,
        )
        report = bt.run()

        gross_profit = sum(t["pnl"] for t in report.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in report.trades if t["pnl"] <= 0))
        window_pf = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0.0)
        )

        window_results.append({
            "window_id": w["window_id"],
            "test_start": w["test_start"],
            "test_end": w["test_end"],
            "symbols": symbols,
            "return_pct": report.total_return_pct,
            "profit_factor": report.profit_factor,
            "aggregate_profit_factor": (
                999.0 if window_pf == float("inf") else round(window_pf, 4)
            ),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "max_drawdown_pct": report.max_drawdown_pct,
            "total_trades": report.total_trades,
            "win_rate": report.win_rate,
            "sharpe_ratio": report.sharpe_ratio,
            "avg_r": report.diagnostics.get("avg_r", 0),
            "eligible": bool(report.diagnostics.get("vol_filter_passed", True)),
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

        # Collect trade-level detail
        for t in report.trades:
            all_trades.append({
                "window_id": w["window_id"],
                "symbol": t["symbol"],
                "side": t["side"],
                "entry_date": t["entry_date"],
                "exit_date": t["exit_date"],
                "entry_price": round(t["entry_price"], 4),
                "exit_price": round(t["exit_price"], 4),
                "quantity": round(t["quantity"], 4),
                "pnl": round(t["pnl"], 2),
                "pnl_pct": round(t["pnl_pct"], 4),
                "hold_hours": round(t["hold_hours"], 2),
                "reason": t["reason"],
            })

    returns = [r["return_pct"] for r in window_results]
    total_gp = sum(r["gross_profit"] for r in window_results)
    total_gl = sum(r["gross_loss"] for r in window_results)
    agg_pf = (
        total_gp / total_gl
        if total_gl > 0
        else (float("inf") if total_gp > 0 else 0.0)
    )
    trades_counts = [r["total_trades"] for r in window_results]
    eligible = [r for r in window_results if r["eligible"]]
    passed = sum(1 for r in eligible if r["passed"])
    active = [r for r in eligible if r["total_trades"] > 0]
    active_passed = sum(1 for r in active if r["passed"])

    # Trade-level stats
    stop_losses = [t for t in all_trades if t["reason"] == "stop_loss"]
    take_profits = [t for t in all_trades if t["reason"] == "take_profit"]
    force_exits = [t for t in all_trades if t["reason"] == "force_exit"]
    quick_stops = [t for t in stop_losses if t["hold_hours"] <= 0.25]  # <=15 min

    return {
        "total_return_pct": round(sum(returns), 4) if returns else 0,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
        "avg_profit_factor": (
            999.0 if agg_pf == float("inf") else round(agg_pf, 4)
        ),
        "total_trades": sum(trades_counts),
        "max_drawdown_pct": (
            round(max(r["max_drawdown_pct"] for r in window_results), 4)
            if window_results
            else 0
        ),
        "num_windows": len(window_results),
        "eligible_windows": len(eligible),
        "active_windows": len(active),
        "windows_passed": passed,
        "pass_rate": round(passed / len(eligible), 4) if eligible else 0,
        "active_pass_rate": round(active_passed / len(active), 4) if active else 0,
        "stop_loss_count": len(stop_losses),
        "take_profit_count": len(take_profits),
        "force_exit_count": len(force_exits),
        "quick_stop_count": len(quick_stops),
        "quick_stop_pct": (
            round(len(quick_stops) / len(stop_losses) * 100, 1)
            if stop_losses
            else 0
        ),
        "avg_hold_hours_stop": (
            round(sum(t["hold_hours"] for t in stop_losses) / len(stop_losses), 2)
            if stop_losses
            else 0
        ),
        "avg_hold_hours_tp": (
            round(sum(t["hold_hours"] for t in take_profits) / len(take_profits), 2)
            if take_profits
            else 0
        ),
        "window_details": window_results,
        "trades": all_trades,
    }


def print_comparison_table(results: list[dict[str, Any]]) -> None:
    """Print the main comparison table."""
    hdr = (
        f"{'Variant':<28} {'Return%':>10} {'Trades':>8} {'AggPF':>8} "
        f"{'MaxDD%':>8} {'WinRate':>8} {'SL':>5} {'TP':>5} {'FE':>5} "
        f"{'QuickSL':>8} {'AvgSL_h':>9}"
    )
    print("\n" + "=" * 120)
    print("STOP MODE COMPARISON — Fence Bar @ ATR 1.2%")
    print("=" * 120)
    print(hdr)
    print("-" * len(hdr))

    for r in results:
        # Compute aggregate win rate from trades
        trades = r["trades"]
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = round(wins / len(trades) * 100, 1) if trades else 0
        print(
            f"{r['label']:<28} {r['total_return_pct']:>10.4f} "
            f"{r['total_trades']:>8d} {r['avg_profit_factor']:>8.4f} "
            f"{r['max_drawdown_pct']:>8.4f} {win_rate:>7.1f}% "
            f"{r['stop_loss_count']:>5d} {r['take_profit_count']:>5d} "
            f"{r['force_exit_count']:>5d} {r['quick_stop_count']:>8d} "
            f"{r['avg_hold_hours_stop']:>9.2f}"
        )
    print("=" * 120)
    print(
        "Legend: SL=stop_loss  TP=take_profit  FE=force_exit  "
        "QuickSL=stops within 15min  AvgSL_h=avg hold hours for stop-loss trades"
    )


def print_trade_detail(results: list[dict[str, Any]]) -> None:
    """Print trade-level comparison — which trades differ between variants."""
    print("\n" + "=" * 120)
    print("TRADE-LEVEL DETAIL — Trades that differ between variants")
    print("=" * 120)

    # Build a key -> trade map for each variant
    # Key: (window_id, symbol, entry_date) — should be same entry signal
    variant_maps = []
    for r in results:
        m = {}
        for t in r["trades"]:
            key = (t["window_id"], t["symbol"], t["entry_date"])
            m[key] = t
        variant_maps.append(m)

    baseline = variant_maps[0]
    labels = [r["label"] for r in results]

    # Find trades present in baseline and compare exit reason / pnl across variants
    changed = []
    only_in = {label: [] for label in labels}
    for key, base_trade in baseline.items():
        row = {"key": key, "base": base_trade}
        for i, m in enumerate(variant_maps[1:], 1):
            other = m.get(key)
            row[labels[i]] = other
        changed.append(row)

    # Trades only in variant 2 or 3 (not in baseline)
    for i, m in enumerate(variant_maps[1:], 1):
        for key, t in m.items():
            if key not in baseline:
                only_in[labels[i]].append(t)

    # Print trades where exit reason or pnl changed
    print(f"\n{'Win':>4} {'Symbol':>8} {'Entry':>22} ", end="")
    for label in labels:
        print(f"| {label:>20} ", end="")
    print()
    print(f"{'':>4} {'':>8} {'':>22} ", end="")
    for label in labels:
        print(f"| {'reason/pnl/hold':>20} ", end="")
    print()
    print("-" * 130)

    diffs_found = 0
    for row in changed:
        base = row["base"]
        any_diff = False
        for label in labels[1:]:
            other = row.get(label)
            if other is None:
                any_diff = True
                break
            if other["reason"] != base["reason"] or abs(other["pnl"] - base["pnl"]) > 0.01:
                any_diff = True
                break
        if not any_diff:
            continue
        diffs_found += 1
        win, sym, entry = row["key"]
        entry_short = entry[:19] if len(entry) > 19 else entry
        print(f"{win:>4} {sym:>8} {entry_short:>22} ", end="")
        print(f"| {base['reason']:>8}/{base['pnl']:>8.1f}/{base['hold_hours']:>4.1f}h ", end="")
        for label in labels[1:]:
            other = row.get(label)
            if other is None:
                print(f"| {'MISSING':>20} ", end="")
            else:
                print(
                    f"| {other['reason']:>8}/{other['pnl']:>8.1f}/{other['hold_hours']:>4.1f}h ",
                    end="",
                )
        print()

    if diffs_found == 0:
        print("  (no trade-level differences between variants)")
    else:
        print(f"\n  {diffs_found} trades differ between variants.")

    # Trades only in non-baseline variants
    for label in labels[1:]:
        extra = only_in[label]
        if extra:
            print(f"\n  Trades only in {label} ({len(extra)}):")
            for t in extra[:20]:
                print(
                    f"    Win {t['window_id']:>3} {t['symbol']:>8} "
                    f"entry={t['entry_date'][:19]} reason={t['reason']} "
                    f"pnl={t['pnl']:.1f} hold={t['hold_hours']:.1f}h"
                )
            if len(extra) > 20:
                print(f"    ... and {len(extra) - 20} more")


def main() -> None:
    print("Building provider...", file=sys.stderr)
    provider, provider_label = build_provider()

    results = []
    for v in VARIANTS:
        override = deep_merge(FENCE_BAR_DEFAULTS, BASE_OVERRIDE)
        override = deep_merge(override, v["extra"])
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Running variant: {v['label']} — {v['description']}", file=sys.stderr)
        print(f"  stop_mode={override['risk']['stop_mode']}  "
              f"risk_per_trade_pct={override['risk']['risk_per_trade_pct']}",
              file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        res = run_variant_walk_forward(override, provider)
        res["label"] = v["label"]
        res["description"] = v["description"]
        res["override"] = override
        results.append(res)

    # ── Print comparison table ───────────────────────────────────
    print_comparison_table(results)

    # ── Print trade-level detail ─────────────────────────────────
    print_trade_detail(results)

    # ── Save to JSON ─────────────────────────────────────────────
    output = {
        "start": START,
        "end": END,
        "slippage_bps": SLIPPAGE_BPS,
        "max_symbols": MAX_SYMBOLS,
        "provider": provider_label,
        "variants": [
            {
                "label": r["label"],
                "description": r["description"],
                "total_return_pct": r["total_return_pct"],
                "avg_return_pct": r["avg_return_pct"],
                "avg_profit_factor": r["avg_profit_factor"],
                "total_trades": r["total_trades"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "num_windows": r["num_windows"],
                "eligible_windows": r["eligible_windows"],
                "active_windows": r["active_windows"],
                "windows_passed": r["windows_passed"],
                "pass_rate": r["pass_rate"],
                "active_pass_rate": r["active_pass_rate"],
                "stop_loss_count": r["stop_loss_count"],
                "take_profit_count": r["take_profit_count"],
                "force_exit_count": r["force_exit_count"],
                "quick_stop_count": r["quick_stop_count"],
                "quick_stop_pct": r["quick_stop_pct"],
                "avg_hold_hours_stop": r["avg_hold_hours_stop"],
                "avg_hold_hours_tp": r["avg_hold_hours_tp"],
                "override": r["override"],
                "window_details": r["window_details"],
                "trades": r["trades"],
            }
            for r in results
        ],
    }
    OUT_JSON.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved to: {OUT_JSON}", file=sys.stderr)


if __name__ == "__main__":
    main()
