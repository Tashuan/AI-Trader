#!/usr/bin/env python3
"""Target multiple R sweep for the Fence Bar strategy (ETF-excluded, ATR 1.0%).

The best config so far is ETF exclusion at ATR 1.0%, 1R target (+0.44% return,
AggPF 1.42, 18 trades).  MFE analysis chose 1R because 2R looked too ambitious,
but with ETF exclusion + ATR 1.0% giving more trades we re-test whether a higher
target captures more upside.

Sweeps target_multiple_r: [0.75, 1.0, 1.25, 1.5, 2.0, 2.5] with the ETF-exclusion
monkey-patch (SPY/QQQ/IWM filtered from the universe).

Usage:
    python3 research/strategy_search/target_sweep_etf10.py
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
OUTPUT_PATH = RESEARCH_DIR / "target_sweep_etf10.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

TARGET_GRID: list[float] = [0.75, 1.0, 1.25, 1.5, 2.0, 2.5]

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
        "spy_atr_threshold": 1.0,
    },
}


def _make_discover_symbols():
    """Return a discover_symbols replacement with an ETF-filtered universe."""
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


def _aggregate_win_rate(result: dict[str, Any]) -> float:
    """Trade-weighted win rate across active windows."""
    total_wins = 0.0
    total_trades = 0
    for w in result.get("window_details", []):
        tr = w.get("total_trades", 0)
        if tr <= 0:
            continue
        total_wins += w.get("win_rate", 0) * tr
        total_trades += tr
    if total_trades == 0:
        return 0.0
    return round((total_wins / total_trades) * 100, 2)


def run_target(target_r: float, provider, provider_label: str) -> dict[str, Any]:
    """Run walk-forward with ETF exclusion at a given target_multiple_r."""
    override = {**BASE_OVERRIDE}
    override["risk"] = {**BASE_OVERRIDE["risk"], "target_multiple_r": target_r}
    params = deep_merge(FENCE_BAR_DEFAULTS, override)

    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols()
    try:
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Target R = {target_r}", file=sys.stderr)
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
            strategy_name=f"ETF10_target_{target_r}",
        )
    finally:
        swf.discover_symbols = original_discover
    return result


def print_table(rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 82)
    print("  TARGET R SWEEP  (ETF-excluded, ATR 1.0%, slippage 5 bps)")
    print("=" * 82)
    hdr = f"{'Target R':>9} | {'Return %':>9} | {'Trades':>6} | {'Active':>6} | {'AggPF':>7} | {'Max DD %':>8} | {'Win Rate %':>10}"
    print(hdr)
    print("-" * 82)
    for r in rows:
        print(
            f"{r['target_r']:>9.2f} | {r['return_pct']:>9.4f} | {r['trades']:>6} | "
            f"{r['active_windows']:>6} | {r['agg_pf']:>7.4f} | {r['max_dd_pct']:>8.4f} | {r['win_rate_pct']:>10.2f}"
        )
    print("=" * 82)


def main() -> None:
    provider, provider_label = swf.build_provider()

    rows = []
    full_results = {}
    for target_r in TARGET_GRID:
        result = run_target(target_r, provider, provider_label)
        win_rate = _aggregate_win_rate(result)
        rows.append({
            "target_r": target_r,
            "return_pct": result.get("total_return_pct", 0),
            "trades": result.get("total_trades", 0),
            "active_windows": result.get("active_windows", 0),
            "agg_pf": result.get("avg_profit_factor", 0),
            "max_dd_pct": result.get("max_drawdown_pct", 0),
            "win_rate_pct": win_rate,
        })
        full_results[f"target_{target_r}"] = result

    print_table(rows)

    # Identify optimal target by return % (tiebreak: AggPF)
    best = max(rows, key=lambda r: (r["return_pct"], r["agg_pf"]))
    print(f"\n  Optimal target_multiple_r = {best['target_r']}  "
          f"(Return {best['return_pct']:+.4f}%, AggPF {best['agg_pf']:.4f}, "
          f"{best['trades']} trades, Win {best['win_rate_pct']:.2f}%)")

    output = {
        "config": {
            "etf_symbols_excluded": sorted(ETF_SYMBOLS),
            "atr_threshold": 1.0,
            "slippage_bps": 5.0,
            "max_symbols": 15,
            "start": "2024-10-01",
            "end": "2026-08-11",
            "base_override": BASE_OVERRIDE,
        },
        "sweep": rows,
        "results": full_results,
        "optimal_target_r": best["target_r"],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
