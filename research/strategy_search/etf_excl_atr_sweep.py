#!/usr/bin/env python3
"""ATR sweep for the Fence Bar strategy with ETF exclusion.

The best Fence Bar config excludes ETFs (SPY/QQQ/IWM) from the universe.
At ATR 1.2% it produced +0.26% return with AggPF 1.40.  This script sweeps
ATR thresholds [0.8, 1.0, 1.2, 1.5, 1.8, 2.0] with ETF exclusion to find
the optimal ATR setting.

Uses the same monkey-patching approach as etf_exclusion_test.py: patches
strategy_walk_forward.discover_symbols with a version that filters out
SPY/QQQ/IWM from the UNIVERSE list.

Usage:
    python3 research/strategy_search/etf_excl_atr_sweep.py
"""

from __future__ import annotations

import copy
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
OUTPUT_PATH = RESEARCH_DIR / "etf_excl_atr_sweep.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

ATR_THRESHOLDS = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0]

BASE_OVERRIDE: dict[str, Any] = {
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
    },
}


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


def run_atr_variant(atr: float, provider, provider_label: str) -> dict[str, Any]:
    """Run walk-forward with ETF exclusion at a given ATR threshold."""
    override = copy.deepcopy(BASE_OVERRIDE)
    override["vol_filter"]["spy_atr_threshold"] = atr
    params = deep_merge(FENCE_BAR_DEFAULTS, override)

    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols()
    try:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Running ETF-excl ATR={atr}", file=sys.stderr)
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
            strategy_name=f"ETF-excl ATR={atr}",
        )
    finally:
        swf.discover_symbols = original_discover
    return result


def print_sweep_table(rows: list[dict[str, Any]]) -> None:
    """Print the ATR sweep summary table."""
    print("\n" + "=" * 78)
    print("  ETF-EXCLUSION ATR SWEEP  (slippage 5 bps, max_symbols=15)")
    print("=" * 78)
    hdr = (
        f"{'ATR':>5} | {'Return %':>9} | {'Trades':>7} | "
        f"{'Active Win':>11} | {'AggPF':>7} | {'Max DD %':>9} | {'Pass Rate %':>12}"
    )
    print(hdr)
    print("-" * 78)
    for r in rows:
        atr = r["atr"]
        ret = r["result"].get("total_return_pct", 0)
        trades = r["result"].get("total_trades", 0)
        active = r["result"].get("active_windows", 0)
        aggpf = r["result"].get("avg_profit_factor", 0)
        maxdd = r["result"].get("max_drawdown_pct", 0)
        pass_rate = r["result"].get("pass_rate", 0) * 100
        print(
            f"{atr:>5} | {ret:>9.4f} | {trades:>7} | "
            f"{active:>11} | {aggpf:>7.4f} | {maxdd:>9.4f} | {pass_rate:>11.2f}"
        )
    print("=" * 78)


def main() -> None:
    provider, provider_label = swf.build_provider()

    rows = []
    for atr in ATR_THRESHOLDS:
        result = run_atr_variant(atr, provider=provider, provider_label=provider_label)
        rows.append({"atr": atr, "result": result})

    print_sweep_table(rows)

    # Identify optimal ATR by aggregate profit factor, tie-break on return.
    best = max(rows, key=lambda r: (r["result"].get("avg_profit_factor", 0),
                                    r["result"].get("total_return_pct", 0)))
    print(f"\n  Optimal ATR (ETF-excl): {best['atr']}  "
          f"(AggPF={best['result'].get('avg_profit_factor', 0)}, "
          f"Return={best['result'].get('total_return_pct', 0)}%)")

    output = {
        "etf_symbols_excluded": sorted(ETF_SYMBOLS),
        "atr_thresholds": ATR_THRESHOLDS,
        "base_override": BASE_OVERRIDE,
        "sweep": [
            {"atr": r["atr"], **r["result"]} for r in rows
        ],
        "optimal_atr": best["atr"],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
