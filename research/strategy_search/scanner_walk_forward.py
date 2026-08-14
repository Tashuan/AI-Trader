#!/usr/bin/env python3
"""Research-only walk-forward harness using the premarket scanner for symbol discovery.

For each walk-forward test window, runs the premarket scanner on the first
trading day of the window to discover the top candidates, then runs the
ScalpRunner backtest on those dynamically selected symbols. This closes the
biggest fidelity gap in the static-watchlist backtest.

Usage:
    python3 research/strategy_search/scanner_walk_forward.py --candidate cap2_spy10
    python3 research/strategy_search/scanner_walk_forward.py --candidate cap2_spy10 --slippage 10
"""

from __future__ import annotations

import argparse
import json
import sys
import os
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
from execution_simulator import FillConfig
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge
from premarket_replay import PremarketReplayProvider
from premarket_scanner import DEFAULT_CONFIG, scan
from strategy_lab import load_json_config

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"
SCANNER_CONFIG = AGENTS_DIR / "config" / "premarket_scanner.json"

SLIPPAGE = 5.0
FEE_RATE = 0.001
CAPITAL = 100_000.0
TRAIN_DAYS = 14
TEST_DAYS = 14
STEP_DAYS = 7
MAX_SYMBOLS = 10  # Top N scanner candidates to trade


def build_provider():
    alpaca = AlpacaProvider()
    if not alpaca.available:
        raise RuntimeError("Alpaca provider not available")
    return CachedProvider(alpaca), "cached-alpaca"


def build_fill_config(interval: str, slippage_bps: float, fee_rate: float):
    return FillConfig(
        slippage_bps=slippage_bps,
        fee_rate=fee_rate,
        enable_size_impact=True,
        enable_vol_widening=True,
        enable_partial_fills=True,
        enable_tick_rounding=True,
        market="us-stock",
        interval=interval,
        enable_quote_side_pricing=True,
    )


def load_candidate(config_id: str) -> dict[str, Any]:
    path = RESEARCH_DIR / f"candidate_{config_id}.json"
    if path.exists():
        with path.open() as f:
            return json.load(f)
    raise FileNotFoundError(f"No candidate file: {path}")


def generate_windows(start: str, end: str,
                     train_days: int = TRAIN_DAYS, test_days: int = TEST_DAYS,
                     step_days: int = STEP_DAYS) -> list[dict[str, str]]:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    windows = []
    current = s
    wid = 0
    while current + timedelta(days=train_days + test_days) <= e:
        windows.append({
            "window_id": wid,
            "train_start": current.strftime("%Y-%m-%d"),
            "train_end": (current + timedelta(days=train_days)).strftime("%Y-%m-%d"),
            "test_start": (current + timedelta(days=train_days)).strftime("%Y-%m-%d"),
            "test_end": (current + timedelta(days=train_days + test_days)).strftime("%Y-%m-%d"),
        })
        wid += 1
        current += timedelta(days=step_days)
    return windows


def discover_symbols_for_window(test_start: str, test_end: str,
                                scanner_config: dict, max_symbols: int = MAX_SYMBOLS) -> list[str]:
    """Run the premarket scanner on the test_start date to discover top candidates.

    Falls back to the default 5-symbol watchlist if the scanner fails.
    """
    # Try scanning on the test_start date
    try:
        provider = PremarketReplayProvider(test_start, interval="5m")
        universe = scanner_config["universe"]
        provider.prepare(universe, period=scanner_config["history_period"])
        result = scan(
            scanner_config,
            provider=provider,
            mover_fetcher=provider.mover_fetcher,
            news_fetcher=None,  # Skip news for speed
        )
        # Extract top symbols from monitor candidates
        symbols = [c["symbol"] for c in result["watchlist"]
                   if c["status"] == "monitor"][:max_symbols]
        if not symbols:
            symbols = [c["symbol"] for c in result["watchlist"]][:max_symbols]
        if symbols:
            return symbols
    except Exception as exc:
        print(f"  Scanner failed for {test_start}: {exc}", file=sys.stderr)

    # Fallback: use the default static watchlist
    return ["NVDA", "TSLA", "AAPL", "AMD", "META"]


def run_scanner_walk_forward(
    candidate_id: str, override: dict[str, Any],
    start: str = "2026-03-02", end: str = "2026-08-11",
    interval: str = "30m", slippage_bps: float = SLIPPAGE,
    fee_rate: float = FEE_RATE, capital: float = CAPITAL,
    max_symbols: int = MAX_SYMBOLS,
) -> dict[str, Any]:
    provider, provider_label = build_provider()
    params = deep_merge(SCALP_DEFAULT_PARAMS, override)
    fill_cfg = build_fill_config(interval, slippage_bps, fee_rate)
    windows = generate_windows(start, end)
    if not windows:
        return {"error": "No windows generated", "candidate_id": candidate_id}

    scanner_config = load_json_config(SCANNER_CONFIG, DEFAULT_CONFIG)

    window_results = []
    for w in windows:
        # Discover symbols for this window using the scanner
        symbols = discover_symbols_for_window(w["test_start"], w["test_end"],
                                               scanner_config, max_symbols)
        print(f"  Win {w['window_id']}: {w['test_start']} → {w['test_end']} | "
              f"symbols={symbols}", file=sys.stderr)

        bt = ScalpScanBacktester(
            symbols=symbols, params=params,
            start_date=w["test_start"], end_date=w["test_end"],
            initial_capital=capital, slippage_bps=slippage_bps,
            provider=provider, base_interval=interval,
            fill_config=fill_cfg,
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
            "per_symbol_stats": report.to_dict().get("per_symbol_stats", {}),
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in window_results]
    pfs = [r["profit_factor"] for r in window_results if r["profit_factor"] > 0]
    trades = [r["total_trades"] for r in window_results]
    passed = sum(1 for r in window_results if r["passed"])

    return {
        "candidate_id": candidate_id,
        "provider": provider_label,
        "discovery": "premarket_scanner",
        "max_symbols": max_symbols,
        "start_date": start,
        "end_date": end,
        "interval": interval,
        "slippage_bps": slippage_bps,
        "fee_rate": fee_rate,
        "enable_quote_side_pricing": True,
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


def main():
    parser = argparse.ArgumentParser(description="Scanner-driven ScalpRunner walk-forward")
    parser.add_argument("--candidate", default="cap2_spy10", help="Candidate config ID")
    parser.add_argument("--slippage", type=float, default=SLIPPAGE, help="Slippage in bps")
    parser.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS, help="Max scanner symbols per window")
    parser.add_argument("--json", default="", help="Save full results to JSON path")
    args = parser.parse_args()

    cand = load_candidate(args.candidate)
    result = run_scanner_walk_forward(
        cand["config_id"], cand["override"],
        slippage_bps=args.slippage, max_symbols=args.max_symbols,
    )

    print(json.dumps(result, indent=2, default=str))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nSaved to: {out}", file=sys.stderr)

    # Summary
    r = result
    print(f"\n{'='*70}")
    print(f"  Candidate: {r['candidate_id']} (scanner discovery, max {r['max_symbols']} symbols)")
    print(f"  Windows: {r['num_windows']} | Passed: {r['windows_passed']} ({r['pass_rate']:.0%})")
    print(f"  Total return: {r['total_return_pct']:+.2f}%")
    print(f"  Avg PF: {r['avg_profit_factor']:.3f}")
    print(f"  Trades: {r['total_trades']} | Max DD: {r['max_drawdown_pct']:.2f}%")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
