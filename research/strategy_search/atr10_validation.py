#!/usr/bin/env python3
"""ATR 1.0% validation: holdout split + slippage sensitivity.

The ETF-exclusion config (SPY/QQQ/IWM removed) at ATR 1.0% is the current
best Fence Bar config (+0.44% return, AggPF 1.42, 18 trades).  This script
validates it two ways:

  1. Holdout (70/30 split) — does the edge survive out-of-sample windows?
  2. Slippage sensitivity [0, 1, 2, 5, 10] bps — where does it break even?

Usage:
    python3 research/strategy_search/atr10_validation.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
sys.path.insert(0, str(AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

import strategy_walk_forward as swf
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"
OUTPUT_PATH = RESEARCH_DIR / "atr10_validation.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

OVERRIDE: dict[str, Any] = {
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
        "spy_atr_threshold": 1.0,
    },
}

SLIPPAGE_LEVELS = [0.0, 1.0, 2.0, 5.0, 10.0]
START = "2024-10-01"
END = "2026-08-11"
SLIPPAGE_BPS = 5.0
MAX_SYMBOLS = 15


def _make_discover_symbols():
    """Return a discover_symbols replacement with ETFs excluded."""
    import pandas as pd

    base_universe = [
        "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
        "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
        "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
        "XPEV", "PLUG", "DKNG",
    ]
    universe = [s for s in base_universe if s not in ETF_SYMBOLS]
    default_symbols = ["NVDA", "TSLA", "AAPL", "AMD", "META"]

    def _discover(test_start: str, provider, max_symbols: int = 15) -> list[str]:
        end_date = test_start
        start_date = (datetime.fromisoformat(test_start) - timedelta(days=10)).strftime("%Y-%m-%d")
        candidates = []
        for sym in universe:
            try:
                df = provider.history(sym, interval="1d", start=start_date, end=end_date)
                if df is None or df.empty or len(df) < 2:
                    continue
                df = df.reset_index() if df.index.name else df
                col = "Datetime" if "Datetime" in df.columns else "Date"
                df[col] = pd.to_datetime(df[col])
                prior = df.iloc[-2]
                today = df.iloc[-1]
                prev_close = float(prior["Close"])
                today_open = float(today["Open"])
                today_close = float(today["Close"])
                today_volume = float(today["Volume"])
                avg_volume = float(df["Volume"].iloc[:-1].tail(20).mean())
                if prev_close <= 0 or avg_volume <= 0:
                    continue
                gap_pct = (today_open / prev_close - 1) * 100
                vol_ratio = today_volume / avg_volume
                prior_high = float(prior["High"])
                prior_low = float(prior["Low"])
                dist_to_high = abs(today_close - prior_high) / prior_high * 100
                dist_to_low = abs(today_close - prior_low) / prior_low * 100
                min_dist = min(dist_to_high, dist_to_low)
                score = 0.0
                score += min(25.0, abs(gap_pct) * 5) if abs(gap_pct) >= 1.0 else 0
                score += min(20.0, vol_ratio * 6) if vol_ratio >= 1.25 else 0
                adv = today_close * avg_volume
                score += 20.0 if adv >= 25_000_000 else 0
                score += 15.0 if min_dist <= 1.0 else 0
                candidates.append({"symbol": sym, "score": round(score, 2)})
            except Exception:
                continue
        candidates.sort(key=lambda c: c["score"], reverse=True)
        symbols = [c["symbol"] for c in candidates[:max_symbols]]
        return symbols if symbols else default_symbols

    return _discover


def _patch_discover():
    """Monkey-patch swf.discover_symbols to exclude ETFs. Returns restore fn."""
    original = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols()
    return lambda: setattr(swf, "discover_symbols", original)


def print_holdout_table(holdout: dict[str, Any]) -> None:
    """Print the 70/30 train vs holdout comparison."""
    print("\n" + "=" * 72)
    print("  PART 1 — HOLDOUT VALIDATION (70/30 split, ATR 1.0%, 5 bps)")
    print("=" * 72)
    hdr = f"{'Metric':<28} {'Train (70%)':>18} {'Holdout (30%)':>18}"
    print(hdr)
    print("-" * 72)
    train = holdout.get("train", {})
    hold = holdout.get("holdout", {})
    rows = [
        ("Active Windows", "active_windows"),
        ("Total Trades", "trades"),
        ("Return %", "return_pct"),
        ("Pass Rate", "pass_rate"),
        ("Max Drawdown %", "max_dd"),
    ]
    for name, key in rows:
        t = train.get(key, "N/A")
        h = hold.get(key, "N/A")
        if isinstance(t, float):
            t = f"{t:.4f}"
        if isinstance(h, float):
            h = f"{h:.4f}"
        print(f"  {name:<26} {str(t):>18} {str(h):>18}")
    print("=" * 72)
    gen = hold.get("return_pct", -1) > 0
    print(f"  Generalizes to holdout? {'YES' if gen else 'NO'}")


def print_slippage_table(results: list[dict[str, Any]]) -> None:
    """Print slippage sensitivity table and break-even estimate."""
    print("\n" + "=" * 72)
    print("  PART 2 — SLIPPAGE SENSITIVITY (ATR 1.0%, ETF-excluded)")
    print("=" * 72)
    hdr = f"{'Slip (bps)':>10} {'Return %':>10} {'Trades':>8} {'AggPF':>8} {'Pass%':>8} {'MaxDD%':>8}"
    print(hdr)
    print("-" * 72)
    for r in results:
        slip = r.get("slippage_bps", "?")
        ret = r.get("total_return_pct", 0)
        tr = r.get("total_trades", 0)
        pf = r.get("avg_profit_factor", 0)
        pr = r.get("pass_rate", 0)
        dd = r.get("max_drawdown_pct", 0)
        print(f"  {slip:>10.1f} {ret:>10.4f} {tr:>8} {pf:>8.4f} {pr:>8.4f} {dd:>8.4f}")
    print("=" * 72)

    # Break-even slippage: linear interpolation between adjacent levels
    breakeven = _estimate_breakeven(results)
    if breakeven is not None:
        print(f"  Break-even slippage: ~{breakeven:.2f} bps")
    else:
        print("  Break-even slippage: not reached within tested range (still positive at 10 bps)"
              if results and results[-1].get("total_return_pct", -1) > 0
              else "  Break-even slippage: already negative at 0 bps")


def _estimate_breakeven(results: list[dict[str, Any]]) -> float | None:
    """Linearly interpolate the slippage where return crosses zero."""
    sorted_r = sorted(results, key=lambda r: r.get("slippage_bps", 0))
    for i in range(len(sorted_r) - 1):
        r0, r1 = sorted_r[i], sorted_r[i + 1]
        y0, y1 = r0.get("total_return_pct", 0), r1.get("total_return_pct", 0)
        x0, x1 = r0.get("slippage_bps", 0), r1.get("slippage_bps", 0)
        if y0 > 0 and y1 <= 0:
            # interpolate x where y=0
            return x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
    if sorted_r and sorted_r[-1].get("total_return_pct", 0) > 0:
        return None  # never crosses
    return None


def main() -> None:
    provider, provider_label = swf.build_provider()
    params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)

    # ---- Part 1: Holdout split ----
    print("\n" + "=" * 60, file=sys.stderr)
    print("  PART 1: Holdout 70/30 split", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    restore = _patch_discover()
    try:
        holdout = swf.run_holdout_split(
            backtester_cls=FenceBarBacktester,
            override=params,
            start=START,
            end=END,
            slippage_bps=SLIPPAGE_BPS,
            max_symbols=MAX_SYMBOLS,
            provider=provider,
            provider_label=provider_label,
        )
    finally:
        restore()
    print_holdout_table(holdout)

    # ---- Part 2: Slippage sensitivity ----
    print("\n" + "=" * 60, file=sys.stderr)
    print("  PART 2: Slippage sensitivity", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    restore = _patch_discover()
    sensitivity = []
    try:
        for slip in SLIPPAGE_LEVELS:
            print(f"\n--- Slippage {slip} bps ---", file=sys.stderr)
            r = swf.run_walk_forward(
                backtester_cls=FenceBarBacktester,
                override=params,
                start=START,
                end=END,
                slippage_bps=slip,
                max_symbols=MAX_SYMBOLS,
                provider=provider,
                provider_label=provider_label,
                strategy_name=f"ATR10_noETF_{slip}bps",
            )
            sensitivity.append(r)
    finally:
        restore()
    print_slippage_table(sensitivity)

    # ---- Save ----
    breakeven = _estimate_breakeven(sensitivity)
    output = {
        "config": {
            "atr_threshold": 1.0,
            "etf_symbols_excluded": sorted(ETF_SYMBOLS),
            "override": OVERRIDE,
            "start": START,
            "end": END,
            "max_symbols": MAX_SYMBOLS,
        },
        "holdout": holdout,
        "slippage_sensitivity": sensitivity,
        "breakeven_slippage_bps": breakeven,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
