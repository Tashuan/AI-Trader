#!/usr/bin/env python3
"""ETF exclusion test for the Fence Bar strategy.

SPY is the single biggest loser (-287 USD, 25% of all absolute PnL).
ETFs like SPY/QQQ/IWM don't have the opening-range follow-through that
individual stocks do.  This script compares the current universe (which
includes ETFs) against a no-ETF universe (SPY/QQQ/IWM excluded) using
the walk-forward harness at ATR 1.2%.

Usage:
    python3 research/strategy_search/etf_exclusion_test.py
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
OUTPUT_PATH = RESEARCH_DIR / "etf_exclusion_test.json"

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
    """Run walk-forward with either the baseline or no-ETF universe."""
    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols(exclude_etfs)
    try:
        params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Running variant: {label}", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        result = swf.run_walk_forward(
            backtester_cls=FenceBarBacktester,
            override=params,
            start="2024-10-01",
            end="2026-08-11",
            slippage_bps=5.0,
            max_symbols=15,
            provider=provider,
            provider_label=provider_label,
            strategy_name=label,
        )
    finally:
        swf.discover_symbols = original_discover
    return result


def print_comparison(baseline: dict, no_etf: dict) -> None:
    """Print a side-by-side comparison table of the two variants."""
    print("\n" + "=" * 72)
    print("  ETF EXCLUSION COMPARISON  (ATR 1.2%, slippage 5 bps)")
    print("=" * 72)
    hdr = f"{'Metric':<30} {'Baseline':>18} {'No-ETF':>18}"
    print(hdr)
    print("-" * 72)

    rows = [
        ("Total Return %", "total_return_pct"),
        ("Avg Return %", "avg_return_pct"),
        ("Total Trades", "total_trades"),
        ("Aggregate PF", "avg_profit_factor"),
        ("Max Drawdown %", "max_drawdown_pct"),
        ("Num Windows", "num_windows"),
        ("Eligible Windows", "eligible_windows"),
        ("Active Windows", "active_windows"),
        ("Windows Passed", "windows_passed"),
        ("Pass Rate", "pass_rate"),
        ("Active Pass Rate", "active_pass_rate"),
    ]
    for name, key in rows:
        b = baseline.get(key, "N/A")
        n = no_etf.get(key, "N/A")
        if isinstance(b, float):
            b = f"{b:.4f}"
        if isinstance(n, float):
            n = f"{n:.4f}"
        print(f"  {name:<28} {str(b):>18} {str(n):>18}")
    print("=" * 72)


def print_window_diff(baseline: dict, no_etf: dict) -> None:
    """Show windows where trade counts differ between variants."""
    bw = {w["window_id"]: w for w in baseline.get("window_details", [])}
    nw = {w["window_id"]: w for w in no_etf.get("window_details", [])}
    all_ids = sorted(set(bw) | set(nw))

    diffs = []
    for wid in all_ids:
        b = bw.get(wid, {})
        n = nw.get(wid, {})
        bt = b.get("total_trades", 0)
        nt = n.get("total_trades", 0)
        if bt != nt:
            diffs.append((wid, b, n, bt, nt))

    if not diffs:
        print("\n  No windows with differing trade counts. ETFs never made the cut.")
        return

    print(f"\n  Windows with trade-count differences ({len(diffs)} of {len(all_ids)}):")
    print(f"  {'Win':>4}  {'Start':<12} {'End':<12} {'Base Tr':>7} {'NoETF Tr':>8}  {'Base Ret%':>9} {'NoETF Ret%':>10}")
    print("  " + "-" * 68)
    for wid, b, n, bt, nt in diffs:
        ts = b.get("test_start", n.get("test_start", "?"))
        te = b.get("test_end", n.get("test_end", "?"))
        br = b.get("return_pct", 0)
        nr = n.get("return_pct", 0)
        flag = ""
        if bt > 0 and nt == 0:
            flag = "  <-- lost all trades (ETF-only?)"
        elif bt == 0 and nt > 0:
            flag = "  <-- new trades (ETF was blocking?)"
        print(f"  {wid:>4}  {ts:<12} {te:<12} {bt:>7} {nt:>8}  {br:>9.4f} {nr:>10.4f}{flag}")


def main() -> None:
    provider, provider_label = swf.build_provider()

    baseline = run_variant("Baseline (with ETFs)", exclude_etfs=False,
                           provider=provider, provider_label=provider_label)
    no_etf = run_variant("No-ETF (SPY/QQQ/IWM excluded)", exclude_etfs=True,
                         provider=provider, provider_label=provider_label)

    print_comparison(baseline, no_etf)
    print_window_diff(baseline, no_etf)

    output = {
        "baseline": baseline,
        "no_etf": no_etf,
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "override": OVERRIDE,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
