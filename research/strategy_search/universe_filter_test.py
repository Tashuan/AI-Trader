#!/usr/bin/env python3
"""Universe filtering test for the Fence Bar strategy.

Excluding ETFs (SPY/QQQ/IWM) flipped the strategy from -0.36% to +0.26%
(AggPF 1.40).  The hypothesis is that low-volatility stocks also hurt the
strategy because they don't have enough opening-range follow-through.

This script compares four universe variants at ATR 1.2%:
  - baseline            : full universe (includes ETFs)
  - no_etf              : exclude SPY, QQQ, IWM
  - no_etf_no_lowvol    : exclude ETFs + low-vol (BABA, XPEV, NIO, PLUG, SOFI)
  - no_etf_tight        : exclude ETFs + keep only high-liquidity names

Usage:
    python3 research/strategy_search/universe_filter_test.py
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
OUTPUT_PATH = RESEARCH_DIR / "universe_filter_test.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}
LOWVOL_SYMBOLS = {"BABA", "XPEV", "NIO", "PLUG", "SOFI"}

# High-liquidity only universe (no ETFs, no low-vol names).
TIGHT_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "COIN", "MARA", "RIOT", "DKNG",
    "UAL", "AAL", "F", "GM", "BA", "DIS",
]

BASE_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
    "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
    "XPEV", "PLUG", "DKNG",
]

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

VARIANT_LABELS = [
    "baseline",
    "no_etf",
    "no_etf_no_lowvol",
    "no_etf_tight",
]


def _universe_for(variant: str) -> list[str]:
    """Return the symbol universe for a given variant label."""
    if variant == "baseline":
        return list(BASE_UNIVERSE)
    if variant == "no_etf":
        return [s for s in BASE_UNIVERSE if s not in ETF_SYMBOLS]
    if variant == "no_etf_no_lowvol":
        return [s for s in BASE_UNIVERSE
                if s not in ETF_SYMBOLS and s not in LOWVOL_SYMBOLS]
    if variant == "no_etf_tight":
        return list(TIGHT_UNIVERSE)
    raise ValueError(f"Unknown variant: {variant}")


def _make_discover_symbols(universe: list[str]):
    """Return a discover_symbols replacement bound to a custom universe."""
    import pandas as pd

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


def run_variant(variant: str, provider, provider_label: str) -> dict[str, Any]:
    """Run walk-forward with the universe for a given variant."""
    universe = _universe_for(variant)
    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols(universe)
    try:
        params = deep_merge(FENCE_BAR_DEFAULTS, OVERRIDE)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Running variant: {variant}  ({len(universe)} symbols)", file=sys.stderr)
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
            strategy_name=variant,
        )
    finally:
        swf.discover_symbols = original_discover
    return result


def print_comparison(results: dict[str, dict[str, Any]]) -> None:
    """Print a comparison table across all variants."""
    print("\n" + "=" * 92)
    print("  UNIVERSE FILTER COMPARISON  (ATR 1.2%, slippage 5 bps, max_symbols=15)")
    print("=" * 92)
    hdr = (f"{'Variant':<22} {'Return %':>10} {'Trades':>8} "
           f"{'Active Win':>11} {'AggPF':>8} {'Max DD %':>10} {'Pass Rate %':>12}")
    print(hdr)
    print("-" * 92)
    for variant in VARIANT_LABELS:
        r = results.get(variant, {})
        ret = r.get("total_return_pct", 0)
        trades = r.get("total_trades", 0)
        active = r.get("active_windows", 0)
        aggpf = r.get("avg_profit_factor", 0)
        maxdd = r.get("max_drawdown_pct", 0)
        pass_rate = r.get("pass_rate", 0) * 100
        print(f"  {variant:<22} {ret:>10.4f} {trades:>8} {active:>11} "
              f"{aggpf:>8.4f} {maxdd:>10.4f} {pass_rate:>12.2f}")
    print("=" * 92)


def main() -> None:
    provider, provider_label = swf.build_provider()

    results: dict[str, dict[str, Any]] = {}
    for variant in VARIANT_LABELS:
        results[variant] = run_variant(variant, provider=provider,
                                       provider_label=provider_label)

    print_comparison(results)

    output = {
        "variants": results,
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "lowvol_symbols_excluded": sorted(LOWVOL_SYMBOLS),
        "tight_universe": TIGHT_UNIVERSE,
        "override": OVERRIDE,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
