#!/usr/bin/env python3
"""Slippage sensitivity sweep for the Fence Bar strategy at ATR 1.2%.

Quantifies how much round-trip costs matter when the average winner is
only ~0.51% — thin enough that 0.20% costs eat 39% of every winner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_walk_forward import run_walk_forward
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

OUT_JSON = Path(__file__).resolve().parent / "slippage_sensitivity_test.json"

OVERRIDE = {
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
        "spy_atr_threshold": 1.2,
    },
}

SLIPPAGE_LEVELS = [0.0, 1.0, 2.0, 5.0, 10.0]
FEE_RATE = 0.001  # 0.1% per side


def round_trip_cost_pct(slip_bps: float) -> float:
    """2 * slippage (bps→%) + 2 * fee (per side)."""
    return 2 * slip_bps / 10000 + 2 * FEE_RATE * 100


def main() -> None:
    params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)
    rows = []
    for slip in SLIPPAGE_LEVELS:
        print(f"\n=== Slippage: {slip} bps ===", file=sys.stderr)
        res = run_walk_forward(
            FenceBarBacktester,
            params,
            start="2024-10-01",
            end="2026-08-11",
            slippage_bps=slip,
            max_symbols=15,
        )
        rows.append({
            "slippage_bps": slip,
            "round_trip_cost_pct": round(round_trip_cost_pct(slip), 4),
            "total_return_pct": res.get("total_return_pct", 0),
            "total_trades": res.get("total_trades", 0),
            "avg_profit_factor": res.get("avg_profit_factor", 0),
            "max_drawdown_pct": res.get("max_drawdown_pct", 0),
        })

    # ── Print table ──────────────────────────────────────────────
    hdr = f"{'Slip(bps)':>10} {'RT Cost%':>10} {'TotRet%':>10} {'Trades':>8} {'AggPF':>8} {'MaxDD%':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['slippage_bps']:>10.1f} {r['round_trip_cost_pct']:>10.4f} "
              f"{r['total_return_pct']:>10.4f} {r['total_trades']:>8d} "
              f"{r['avg_profit_factor']:>8.4f} {r['max_drawdown_pct']:>8.4f}")

    # ── Break-even interpolation ─────────────────────────────────
    be_slip = None
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        if a["total_return_pct"] >= 0 >= b["total_return_pct"] or \
           a["total_return_pct"] <= 0 <= b["total_return_pct"]:
            # linear interp on return vs slippage
            ra, rb = a["total_return_pct"], b["total_return_pct"]
            sa, sb = a["slippage_bps"], b["slippage_bps"]
            if rb != ra:
                be_slip = sa + (0 - ra) * (sb - sa) / (rb - ra)
                break
    if be_slip is not None:
        be_cost = round_trip_cost_pct(be_slip)
        print(f"\nBreak-even slippage: {be_slip:.2f} bps  "
              f"(round-trip cost {be_cost:.4f}%)")
    else:
        # all positive or all negative
        if all(r["total_return_pct"] > 0 for r in rows):
            print("\nBreak-even: strategy stays positive through 10 bps — no crossover in range.")
        else:
            print("\nBreak-even: strategy negative even at 0 bps — no crossover in range.")

    OUT_JSON.write_text(json.dumps({
        "params": params,
        "fee_rate": FEE_RATE,
        "rows": rows,
        "break_even_slippage_bps": be_slip,
    }, indent=2, default=str))
    print(f"\nSaved to: {OUT_JSON}", file=sys.stderr)


if __name__ == "__main__":
    main()
