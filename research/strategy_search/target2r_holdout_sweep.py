#!/usr/bin/env python3
"""Holdout validation sweep for 2.0R target Fence Bar config.

The 2.0R target with ETF exclusion at ATR 1.0% produced +1.88% return,
AggPF 2.38 — a 4x improvement over 1R.  But the ATR 1.0% config with 1R
failed holdout validation (train +0.76%, holdout -0.31%).  This script
tests three ATR levels (1.0, 1.2, 1.5) with the 2.0R target + ETF
exclusion to find which generalizes to holdout.

Usage:
    python3 research/strategy_search/target2r_holdout_sweep.py
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
OUTPUT_PATH = RESEARCH_DIR / "target2r_holdout_sweep.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

ATR_LEVELS = [1.0, 1.2, 1.5]

START = "2024-10-01"
END = "2026-08-11"
SLIPPAGE_BPS = 5.0
MAX_SYMBOLS = 15


def _make_base_override(atr: float) -> dict[str, Any]:
    """Build the override dict for a given ATR level with 2.0R target."""
    return {
        "retest": {"enabled": False},
        "fence": {"min_range_pct": 0.35, "max_range_pct": 0.80},
        "risk": {
            "stop_mode": "fence_midpoint",
            "target_multiple_r": 2.0,
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


def _make_discover_symbols():
    """Return a discover_symbols replacement with ETF-filtered universe."""
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


def run_atr_level(atr: float, provider, provider_label: str) -> dict[str, Any]:
    """Run holdout split + full-period walk-forward for one ATR level."""
    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols()
    try:
        override = deep_merge(FENCE_BAR_DEFAULTS, _make_base_override(atr))
        label = f"2.0R-ETFexcl-ATR{atr}"

        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Holdout split: {label}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        holdout = swf.run_holdout_split(
            backtester_cls=FenceBarBacktester,
            override=override,
            start=START,
            end=END,
            slippage_bps=SLIPPAGE_BPS,
            max_symbols=MAX_SYMBOLS,
            provider=provider,
            provider_label=provider_label,
        )

        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Full-period walk-forward: {label}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        full = swf.run_walk_forward(
            backtester_cls=FenceBarBacktester,
            override=override,
            start=START,
            end=END,
            slippage_bps=SLIPPAGE_BPS,
            max_symbols=MAX_SYMBOLS,
            provider=provider,
            provider_label=provider_label,
            strategy_name=label,
        )
    finally:
        swf.discover_symbols = original_discover

    return {
        "atr": atr,
        "target_multiple_r": 2.0,
        "label": label,
        "holdout": holdout,
        "full": {
            "total_return_pct": full.get("total_return_pct"),
            "avg_profit_factor": full.get("avg_profit_factor"),
            "total_trades": full.get("total_trades"),
            "pass_rate": full.get("pass_rate"),
            "active_pass_rate": full.get("active_pass_rate"),
            "num_windows": full.get("num_windows"),
            "eligible_windows": full.get("eligible_windows"),
            "active_windows": full.get("active_windows"),
            "windows_passed": full.get("windows_passed"),
            "max_drawdown_pct": full.get("max_drawdown_pct"),
        },
        "full_detail": full,
    }


def print_comparison(results: list[dict[str, Any]]) -> None:
    """Print the comparison table across ATR levels."""
    print("\n" + "=" * 92)
    print("  2.0R TARGET + ETF EXCLUSION — HOLDOUT VALIDATION SWEEP")
    print("=" * 92)
    hdr = f"{'ATR':>5} | {'TrnRet%':>8} {'TrnTrd':>7} {'TrnPass':>8} | {'HldRet%':>8} {'HldTrd':>7} {'HldPass':>8} | {'FullRet%':>9} {'AggPF':>7} {'Gen?':>5}"
    print(hdr)
    print("-" * 92)
    for r in results:
        atr = r["atr"]
        t = r["holdout"]["train"]
        h = r["holdout"]["holdout"]
        f = r["full"]
        tr_ret = t["return_pct"]
        tr_trd = t["trades"]
        tr_pass = t["pass_rate"]
        hd_ret = h["return_pct"]
        hd_trd = h["trades"]
        hd_pass = h["pass_rate"]
        fl_ret = f["total_return_pct"]
        fl_pf = f["avg_profit_factor"]
        generalizes = tr_ret > 0 and hd_ret > 0
        gen_str = "YES" if generalizes else "NO"
        print(f"  {atr:>3.1f} | {tr_ret:>8.4f} {tr_trd:>7} {tr_pass:>8.2%} | {hd_ret:>8.4f} {hd_trd:>7} {hd_pass:>8.2%} | {fl_ret:>9.4f} {fl_pf:>7.2f} {gen_str:>5}")
    print("=" * 92)

    print("\n  Generalization summary (both train & holdout positive):")
    for r in results:
        t = r["holdout"]["train"]
        h = r["holdout"]["holdout"]
        gen = t["return_pct"] > 0 and h["return_pct"] > 0
        tag = "GENERALIZES" if gen else "FAILS"
        print(f"    ATR {r['atr']:.1f}: train {t['return_pct']:+.4f}%  holdout {h['return_pct']:+.4f}%  -> {tag}")


def main() -> None:
    provider, provider_label = swf.build_provider()

    results = []
    for atr in ATR_LEVELS:
        r = run_atr_level(atr, provider, provider_label)
        results.append(r)

    print_comparison(results)

    output = {
        "description": "2.0R target + ETF exclusion holdout sweep across ATR levels",
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "start": START,
        "end": END,
        "slippage_bps": SLIPPAGE_BPS,
        "max_symbols": MAX_SYMBOLS,
        "atr_levels": ATR_LEVELS,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
