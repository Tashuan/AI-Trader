#!/usr/bin/env python3
"""Walk-forward harness for the Fence Bar strategy with daily-bar discovery.

For each walk-forward test window, runs the Fence Bar strategy on dynamically
discovered symbols (using the same daily-bar scanner as the ScalpRunner
discovery harness). Tests the opening-range breakout with retest confirmation
on 5-minute bars.

Usage:
    python3 research/strategy_search/fence_walk_forward.py --reproduce
    python3 research/strategy_search/fence_walk_forward.py --sweep
"""

from __future__ import annotations

import argparse
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

from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"

SLIPPAGE = 5.0
FEE_RATE = 0.001
CAPITAL = 100_000.0
TRAIN_DAYS = 14
TEST_DAYS = 14
STEP_DAYS = 7
MAX_SYMBOLS = 10


def build_provider():
    alpaca = AlpacaProvider()
    if not alpaca.available:
        raise RuntimeError("Alpaca provider not available")
    return CachedProvider(alpaca), "cached-alpaca"


def generate_windows(start: str, end: str) -> list[dict[str, str]]:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    windows = []
    current = s
    wid = 0
    while current + timedelta(days=TRAIN_DAYS + TEST_DAYS) <= e:
        windows.append({
            "window_id": wid,
            "test_start": (current + timedelta(days=TRAIN_DAYS)).strftime("%Y-%m-%d"),
            "test_end": (current + timedelta(days=TRAIN_DAYS + TEST_DAYS)).strftime("%Y-%m-%d"),
        })
        wid += 1
        current += timedelta(days=STEP_DAYS)
    return windows


def discover_symbols(test_start: str, provider, max_symbols: int = MAX_SYMBOLS) -> list[str]:
    """Rank universe symbols by gap, volume ratio, and proximity to prior-day levels."""
    import pandas as pd

    UNIVERSE = [
        "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
        "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
        "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
        "XPEV", "PLUG", "DKNG",
    ]
    DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]

    end_date = test_start
    start_date = (datetime.fromisoformat(test_start) - timedelta(days=10)).strftime("%Y-%m-%d")

    candidates = []
    for sym in UNIVERSE:
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
    return symbols if symbols else DEFAULT_SYMBOLS


def run_fence_walk_forward(
    override: dict[str, Any],
    start: str = "2026-03-02", end: str = "2026-08-11",
    slippage_bps: float = SLIPPAGE,
    fee_rate: float = FEE_RATE,
    capital: float = CAPITAL,
    max_symbols: int = MAX_SYMBOLS,
    provider=None, provider_label: str = "",
) -> dict[str, Any]:
    if provider is None:
        provider, provider_label = build_provider()
    params = deep_merge(FENCE_BAR_DEFAULTS, override)
    windows = generate_windows(start, end)
    if not windows:
        return {"error": "No windows generated"}

    window_results = []
    for w in windows:
        symbols = discover_symbols(w["test_start"], provider, max_symbols)
        print(f"  Win {w['window_id']}: {w['test_start']} → {w['test_end']} | "
              f"symbols={symbols}", file=sys.stderr)

        bt = FenceBarBacktester(
            symbols=symbols, params=params,
            start_date=w["test_start"], end_date=w["test_end"],
            initial_capital=capital, slippage_bps=slippage_bps,
            fee_rate=fee_rate, provider=provider,
        )
        report = bt.run()
        window_results.append({
            "window_id": w["window_id"],
            "test_start": w["test_start"],
            "test_end": w["test_end"],
            "symbols": symbols,
            "return_pct": report.total_return_pct,
            "profit_factor": report.profit_factor,
            "max_drawdown_pct": report.max_drawdown_pct,
            "total_trades": report.total_trades,
            "win_rate": report.win_rate,
            "sharpe_ratio": report.sharpe_ratio,
            "avg_r": report.diagnostics.get("avg_r", 0),
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in window_results]
    pfs = [r["profit_factor"] for r in window_results if r["profit_factor"] > 0]
    trades = [r["total_trades"] for r in window_results]
    passed = sum(1 for r in window_results if r["passed"])

    return {
        "candidate_id": "fence_bar",
        "provider": provider_label,
        "discovery": "daily_bar_scanner",
        "max_symbols": max_symbols,
        "start_date": start,
        "end_date": end,
        "interval": "5m",
        "slippage_bps": slippage_bps,
        "fee_rate": fee_rate,
        "num_windows": len(window_results),
        "windows_passed": passed,
        "pass_rate": round(passed / len(window_results), 4) if window_results else 0,
        "total_return_pct": round(sum(returns), 4) if returns else 0,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
        "avg_profit_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
        "total_trades": sum(trades),
        "max_drawdown_pct": round(max(r["max_drawdown_pct"] for r in window_results), 4) if window_results else 0,
        "window_details": window_results,
    }


def mode_reproduce(args):
    r = run_fence_walk_forward({}, slippage_bps=args.slippage, max_symbols=args.max_symbols)
    print(json.dumps({k: v for k, v in r.items() if k != "window_details"}, indent=2))
    if args.json:
        _save(r, args.json)
    _print_summary(r)


def mode_sweep(args):
    provider, label = build_provider()
    configs = {
        "default": {},
        "no_retest": {"retest": {"enabled": False}},
        "no_anchor": {"anchor": {"enabled": False}},
        "trailing": {"exit": {"mode": "trailing", "trailing_pct": 0.3, "trailing_activation_pct": 0.3}},
        "tight_fence": {"fence": {"min_range_pct": 0.05, "max_range_pct": 0.80}},
        "wide_fence": {"fence": {"min_range_pct": 0.20, "max_range_pct": 2.50}},
        "3r_target": {"risk": {"target_multiple_r": 3.0}},
        "2_trades_day": {"risk": {"max_trades_per_day": 2}},
    }
    results = []
    for cid, override in configs.items():
        print(f"\n--- {cid} ---", file=sys.stderr)
        r = run_fence_walk_forward(override, provider=provider, provider_label=label,
                                    slippage_bps=args.slippage, max_symbols=args.max_symbols)
        r["candidate_id"] = f"fence_{cid}"
        results.append(r)
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    print(json.dumps([{k: v for k, v in r.items() if k != "window_details"} for r in results], indent=2))
    if args.json:
        _save({"experiment": "fence_sweep", "results": results}, args.json)
    _print_ranking(results)


def _save(data: dict, path: str):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\nSaved to: {out}", file=sys.stderr)


def _print_summary(r: dict):
    print(f"\n{'='*70}")
    print(f"  Fence Bar (daily discovery, max {r['max_symbols']} symbols)")
    print(f"  Windows: {r['num_windows']} | Passed: {r['windows_passed']} ({r['pass_rate']:.0%})")
    print(f"  Total return: {r['total_return_pct']:+.2f}%")
    print(f"  Avg PF: {r['avg_profit_factor']:.3f}")
    print(f"  Trades: {r['total_trades']} | Max DD: {r['max_drawdown_pct']:.2f}%")
    print(f"{'='*70}")


def _print_ranking(ranking: list):
    print(f"\n{'='*70}")
    print(f"  {'Rank':<5} {'Candidate':<20} {'Return':>8} {'Pass%':>7} {'AvgPF':>7} {'Trades':>7} {'MaxDD':>7}")
    print(f"  {'-'*5} {'-'*20} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for i, r in enumerate(ranking, 1):
        print(f"  {i:<5} {r['candidate_id']:<20} {r['total_return_pct']:>+7.2f}% "
              f"{r['pass_rate']:>6.0%} {r['avg_profit_factor']:>7.3f} "
              f"{r['total_trades']:>7} {r['max_drawdown_pct']:>6.2f}%")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Fence Bar walk-forward with discovery")
    parser.add_argument("--slippage", type=float, default=SLIPPAGE)
    parser.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS)
    parser.add_argument("--json", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reproduce", action="store_true")
    mode.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    if args.sweep:
        mode_sweep(args)
    else:
        mode_reproduce(args)


if __name__ == "__main__":
    main()
