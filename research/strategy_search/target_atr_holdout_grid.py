#!/usr/bin/env python3
"""Target R x ATR grid sweep with holdout validation for the Fence Bar strategy.

ETF-exclusion monkey-patch is applied (SPY/QQQ/IWM removed from universe).
For each ATR x Target-R combination we run BOTH the full-period walk-forward
and the 70/30 train/holdout split, then print two summary tables.

Usage:
    python3 research/strategy_search/target_atr_holdout_grid.py
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
OUTPUT_PATH = RESEARCH_DIR / "target_atr_holdout_grid.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

ATR_LEVELS = [1.2, 1.5]
TARGET_LEVELS = [1.0, 1.5, 2.0, 2.5]

START = "2024-10-01"
END = "2026-08-11"
SLIPPAGE_BPS = 5.0
MAX_SYMBOLS = 15


def _make_discover_symbols():
    """Return a discover_symbols replacement with ETFs filtered out."""
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


def build_override(target_r: float, atr: float) -> dict[str, Any]:
    """Build the deep-merged override dict for a given target/ATR combo."""
    override = {
        "retest": {"enabled": False},
        "fence": {"min_range_pct": 0.35, "max_range_pct": 0.80},
        "risk": {
            "stop_mode": "fence_midpoint",
            "target_multiple_r": target_r,
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
            "spy_atr_threshold": atr,
        },
    }
    return deep_merge(FENCE_BAR_DEFAULTS, override)


def run_combo(target_r: float, atr: float, provider, provider_label: str) -> dict[str, Any]:
    """Run full walk-forward + holdout split for one ATR x Target combo."""
    params = build_override(target_r, atr)
    label = f"ATR{atr}_T{target_r}"
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Combo: ATR={atr}%  Target={target_r}R  (ETF-excluded)", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols()
    try:
        wf = swf.run_walk_forward(
            backtester_cls=FenceBarBacktester,
            override=params,
            start=START,
            end=END,
            slippage_bps=SLIPPAGE_BPS,
            max_symbols=MAX_SYMBOLS,
            provider=provider,
            provider_label=provider_label,
            strategy_name=label,
        )
        ho = swf.run_holdout_split(
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
        swf.discover_symbols = original_discover

    return {
        "atr": atr,
        "target_r": target_r,
        "full": {
            "return_pct": wf.get("total_return_pct", 0),
            "total_trades": wf.get("total_trades", 0),
            "agg_pf": wf.get("avg_profit_factor", 0),
            "max_dd_pct": wf.get("max_drawdown_pct", 0),
        },
        "holdout": {
            "train_return_pct": ho["train"].get("return_pct", 0),
            "holdout_return_pct": ho["holdout"].get("return_pct", 0),
            "train_trades": ho["train"].get("trades", 0),
            "holdout_trades": ho["holdout"].get("trades", 0),
        },
    }


def print_table_full(results: list[dict[str, Any]]) -> None:
    """Table 1 — Full period walk-forward results."""
    print("\n" + "=" * 78)
    print("  TABLE 1 — Full Period Walk-Forward  (ETF-excluded, slippage 5 bps)")
    print("=" * 78)
    hdr = f"{'ATR%':>6} {'Target R':>9} {'Return %':>10} {'Trades':>8} {'AggPF':>8} {'Max DD %':>10}"
    print(hdr)
    print("-" * 78)
    for r in results:
        f = r["full"]
        pf = f["agg_pf"]
        pf_str = f"{pf:.4f}" if pf != 999.0 else "inf"
        print(f"{r['atr']:>6.1f} {r['target_r']:>9.1f} {f['return_pct']:>10.4f} "
              f"{f['total_trades']:>8} {pf_str:>8} {f['max_dd_pct']:>10.4f}")
    print("=" * 78)


def print_table_holdout(results: list[dict[str, Any]]) -> None:
    """Table 2 — 70/30 train/holdout split results."""
    print("\n" + "=" * 92)
    print("  TABLE 2 — Holdout Split (70/30)  (ETF-excluded, slippage 5 bps)")
    print("=" * 92)
    hdr = (f"{'ATR%':>6} {'Target R':>9} {'Train Ret%':>11} {'Hold Ret%':>10} "
           f"{'Train Tr':>9} {'Hold Tr':>8} {'Generalizes?':>14}")
    print(hdr)
    print("-" * 92)
    for r in results:
        h = r["holdout"]
        gen = "YES" if (h["train_return_pct"] > 0 and h["holdout_return_pct"] > 0) else "NO"
        print(f"{r['atr']:>6.1f} {r['target_r']:>9.1f} {h['train_return_pct']:>11.4f} "
              f"{h['holdout_return_pct']:>10.4f} {h['train_trades']:>9} "
              f"{h['holdout_trades']:>8} {gen:>14}")
    print("=" * 92)


def main() -> None:
    provider, provider_label = swf.build_provider()

    results = []
    for atr in ATR_LEVELS:
        for target_r in TARGET_LEVELS:
            r = run_combo(target_r, atr, provider, provider_label)
            results.append(r)

    print_table_full(results)
    print_table_holdout(results)

    # Identify best holdout combo that is also positive in train
    candidates = [r for r in results
                  if r["holdout"]["train_return_pct"] > 0 and r["holdout"]["holdout_return_pct"] > 0]
    if candidates:
        best = max(candidates, key=lambda r: r["holdout"]["holdout_return_pct"])
        print(f"\n  BEST holdout (positive train): "
              f"ATR={best['atr']}%  Target={best['target_r']}R  "
              f"holdout={best['holdout']['holdout_return_pct']:.4f}%  "
              f"train={best['holdout']['train_return_pct']:.4f}%")
    else:
        print("\n  No combo generalized (positive in both train & holdout).")

    output = {
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "atr_levels": ATR_LEVELS,
        "target_levels": TARGET_LEVELS,
        "start": START,
        "end": END,
        "slippage_bps": SLIPPAGE_BPS,
        "max_symbols": MAX_SYMBOLS,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
