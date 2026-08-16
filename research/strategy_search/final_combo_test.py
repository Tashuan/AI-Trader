#!/usr/bin/env python3
"""Final combination test: ETF exclusion + HITL no-breakeven.

Tests four variants at ATR 1.2%:
  1. Baseline (ETFs included, no HITL)
  2. ETF exclusion only (no HITL)
  3. HITL no-breakeven only (ETFs included)
  4. ETF exclusion + HITL no-breakeven (the combined fix)

Also runs the combined fix at multiple slippage levels to find breakeven.

Usage:
    python3 research/strategy_search/final_combo_test.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"
sys.path.insert(0, str(AGENTS_DIR))
sys.path.insert(0, str(RESEARCH_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

import strategy_walk_forward as swf
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

from human_in_loop_backtester import HumanInLoopBacktester

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


def make_hitl_no_be_cls():
    """Create a HumanInLoopBacktester subclass with breakeven disabled."""
    class _HITLNoBE(HumanInLoopBacktester):
        def __init__(self, *a, **kw):
            super().__init__(*a, hitl_enabled={
                "vol_override": True,
                "entry_veto": True,
                "breakeven": False,
                "early_exit": True,
            }, **kw)
    _HITLNoBE.__name__ = "HITLNoBE"
    return _HITLNoBE


def run_variant(label, backtester_cls, exclude_etfs, slippage, provider, provider_label):
    original = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols(exclude_etfs)
    try:
        params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)
        print(f"\n=== {label} (slip={slippage}bps, etf_excl={exclude_etfs}) ===", file=sys.stderr)
        return swf.run_walk_forward(
            backtester_cls=backtester_cls,
            override=params,
            start="2024-10-01",
            end="2026-08-11",
            slippage_bps=slippage,
            max_symbols=15,
            provider=provider,
            provider_label=provider_label,
            strategy_name=label,
        )
    finally:
        swf.discover_symbols = original


def main():
    from data_cache import CachedProvider
    from equity_data_providers import AlpacaProvider

    provider = CachedProvider(AlpacaProvider())
    provider_label = "cached-alpaca"
    HITLNoBE = make_hitl_no_be_cls()

    # ── Phase 1: Four-way comparison at 5 bps ──────────────────────────
    print("=" * 70, file=sys.stderr)
    print("  PHASE 1: Four-way comparison at 5 bps slippage", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    variants = [
        ("baseline", FenceBarBacktester, False, 5.0),
        ("etf_excl", FenceBarBacktester, True, 5.0),
        ("hitl_no_be", HITLNoBE, False, 5.0),
        ("etf_excl+hitl_no_be", HITLNoBE, True, 5.0),
    ]

    results = []
    for label, cls, excl_etf, slip in variants:
        r = run_variant(label, cls, excl_etf, slip, provider, provider_label)
        results.append({"name": label, **r})

    print("\n" + "=" * 80)
    print("  PHASE 1 RESULTS: Four-way comparison at 5 bps")
    print(f"  {'Variant':<24} {'Return':>10} {'Trades':>8} {'ActiveW':>10} {'AggPF':>8} {'MaxDD':>8}")
    print("  " + "-" * 76)
    for v in results:
        print(f"  {v['name']:<24} {v['total_return_pct']:>+9.2f}% {v['total_trades']:>8} "
              f"{v['active_windows']:>3}/{v['num_windows']:<6} {v['avg_profit_factor']:>8.2f} {v['max_drawdown_pct']:>7.2f}%")
    print("=" * 80)

    # ── Phase 2: Slippage sensitivity for the best variant ─────────────
    best_label = "etf_excl+hitl_no_be"
    print(f"\n\n{'=' * 70}", file=sys.stderr)
    print(f"  PHASE 2: Slippage sensitivity for {best_label}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    slip_results = []
    for slip in [0.0, 1.0, 2.0, 5.0, 10.0]:
        r = run_variant(f"combo_slip{slip}", HITLNoBE, True, slip, provider, provider_label)
        slip_results.append({"slippage_bps": slip, **r})

    print("\n" + "=" * 80)
    print(f"  PHASE 2 RESULTS: {best_label} slippage sensitivity")
    print(f"  {'Slip(bps)':>10} {'Return':>10} {'Trades':>8} {'AggPF':>8} {'MaxDD':>8}")
    print("  " + "-" * 50)
    for v in slip_results:
        print(f"  {v['slippage_bps']:>10.1f} {v['total_return_pct']:>+9.2f}% {v['total_trades']:>8} "
              f"{v['avg_profit_factor']:>8.2f} {v['max_drawdown_pct']:>7.2f}%")
    print("=" * 80)

    # Save
    output = {
        "phase1_comparison": results,
        "phase2_slippage_sensitivity": slip_results,
    }
    out_path = RESEARCH_DIR / "final_combo_test.json"
    with out_path.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
