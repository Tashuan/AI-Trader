#!/usr/bin/env python3
"""Holdout validation for the ETF-exclusion Fence Bar config.

The best Fence Bar config excludes ETFs (SPY/QQQ/IWM) from the universe and
uses ATR 1.2%.  On the full 22-month walk-forward it produced +0.26% return
with AggPF 1.40.  This script validates that config on a 70/30 train/holdout
split so we can see whether the edge generalises to unseen data.

Usage:
    python3 research/strategy_search/holdout_etf_excl_test.py
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
OUTPUT_PATH = RESEARCH_DIR / "holdout_etf_excl_test.json"

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
        "spy_atr_threshold": 1.2,
    },
}


def _make_discover_symbols(exclude_etfs: bool):
    """Return a discover_symbols replacement with an ETF-filtered universe."""
    import pandas as pd

    base_universe = [
        "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
        "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
        "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
        "XPEV", "PLUG", "DKNG",
    ]
    universe = [s for s in base_universe if s not in ETF_SYMBOLS] if exclude_etfs else list(base_universe)
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


def run_variant(label: str, exclude_etfs: bool, provider, provider_label: str) -> dict[str, Any]:
    """Run holdout split with either the baseline or no-ETF universe."""
    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols(exclude_etfs)
    try:
        params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Holdout variant: {label}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        result = swf.run_holdout_split(
            backtester_cls=FenceBarBacktester,
            override=params,
            start="2024-10-01",
            end="2026-08-11",
            slippage_bps=5.0,
            max_symbols=15,
            provider=provider,
            provider_label=provider_label,
        )
    finally:
        swf.discover_symbols = original_discover
    return result


def print_comparison(baseline: dict, no_etf: dict) -> None:
    """Print a side-by-side comparison table of train vs holdout for both variants."""
    print("\n" + "=" * 80)
    print("  HOLDOUT VALIDATION  (ATR 1.2%, slippage 5 bps, 70/30 split)")
    print("=" * 80)

    for variant_name, variant in [("Baseline (with ETFs)", baseline), ("No-ETF (SPY/QQQ/IWM excluded)", no_etf)]:
        print(f"\n  --- {variant_name} ---")
        print(f"  {'Metric':<22} {'Train':>18} {'Holdout':>18}")
        print("  " + "-" * 60)
        train = variant.get("train", {})
        holdout = variant.get("holdout", {})
        rows = [
            ("Return %", "return_pct"),
            ("Trades", "trades"),
            ("Pass Rate", "pass_rate"),
            ("Max DD", "max_dd"),
            ("Active Windows", "active_windows"),
        ]
        for name, key in rows:
            t = train.get(key, "N/A")
            h = holdout.get(key, "N/A")
            if isinstance(t, float):
                t = f"{t:.4f}"
            if isinstance(h, float):
                h = f"{h:.4f}"
            print(f"  {name:<22} {str(t):>18} {str(h):>18}")

    print("\n" + "=" * 80)


def main() -> None:
    provider, provider_label = swf.build_provider()

    baseline = run_variant("Baseline (with ETFs)", exclude_etfs=False,
                           provider=provider, provider_label=provider_label)
    no_etf = run_variant("No-ETF (SPY/QQQ/IWM excluded)", exclude_etfs=True,
                         provider=provider, provider_label=provider_label)

    print_comparison(baseline, no_etf)

    output = {
        "baseline": baseline,
        "no_etf": no_etf,
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "override": OVERRIDE,
        "split_ratio": 0.7,
        "start": "2024-10-01",
        "end": "2026-08-11",
        "slippage_bps": 5.0,
        "max_symbols": 15,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
