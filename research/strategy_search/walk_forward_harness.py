#!/usr/bin/env python3
"""Research-only walk-forward harness for ScalpRunner candidates with filter support.

Uses the Strategy Lab's effective_params + deep_merge to build candidate configs,
then runs rolling walk-forward validation via ScalpScanBacktester with the premove
and SPY regime filters that scalp_experiments.py --walk-forward does not expose.

Usage:
    python3 research/strategy_search/walk_forward_harness.py --candidate cap2_spy10
    python3 research/strategy_search/walk_forward_harness.py --ablation
    python3 research/strategy_search/walk_forward_harness.py --sweep
    python3 research/strategy_search/walk_forward_harness.py --sensitivity
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Bootstrap paths
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
sys.path.insert(0, str(AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from data_cache import CacheOnlyProvider, CachedProvider
from equity_data_providers import AlpacaProvider
from execution_simulator import FillConfig
from market_data import YFinanceProvider
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge, effective_params
from schwab_provider import get_schwab_provider
from backtest_discovery import make_discovery_fn

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"

SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
START_DATE = "2026-03-02"
END_DATE = "2026-08-11"
INTERVAL = "30m"
SLIPPAGE = 5.0
FEE_RATE = 0.001
CAPITAL = 100_000.0
TRAIN_DAYS = 14
TEST_DAYS = 14
STEP_DAYS = 7


# ── Provider ─────────────────────────────────────────────────────────

def build_provider(use_cache_30m: bool = True):
    """CachedProvider(AlpacaProvider()) — fetches 30m + SPY daily from Alpaca, caches all.

    CacheOnlyProvider alone cannot serve SPY daily, which silently disables the
    regime filter. Using CachedProvider(AlpacaProvider()) ensures SPY daily is
    fetched and cached while reusing any existing 30m cache entries.
    """
    alpaca = AlpacaProvider()
    if not alpaca.available:
        raise RuntimeError("Alpaca provider not available — SPY daily required for regime filter")
    provider = CachedProvider(alpaca)
    return provider, "cached-alpaca"


def build_fill_config(interval: str, slippage_bps: float, fee_rate: float, realistic: bool):
    return FillConfig(
        slippage_bps=slippage_bps,
        fee_rate=fee_rate if realistic else 0.0,
        enable_size_impact=realistic,
        enable_vol_widening=realistic,
        enable_partial_fills=realistic,
        enable_tick_rounding=realistic,
        market="us-stock",
        interval=interval,
    )


# ── Candidate definitions ────────────────────────────────────────────

def load_candidate(config_id: str) -> dict[str, Any]:
    path = RESEARCH_DIR / f"candidate_{config_id}.json"
    if path.exists():
        with path.open() as f:
            return json.load(f)
    raise FileNotFoundError(f"No candidate file: {path}")


def build_params(override: dict[str, Any]) -> dict[str, Any]:
    """Merge override over SCALP_DEFAULT_PARAMS using Strategy Lab deep_merge."""
    return deep_merge(SCALP_DEFAULT_PARAMS, override)


# ── Walk-forward ─────────────────────────────────────────────────────

def generate_windows(start: str, end: str,
                     train_days: int = TRAIN_DAYS, test_days: int = TEST_DAYS,
                     step_days: int = STEP_DAYS) -> list[dict[str, str]]:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    windows = []
    current = s
    wid = 0
    while current + timedelta(days=train_days + test_days) <= e:
        train_start = current
        train_end = current + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        windows.append({
            "window_id": wid,
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })
        wid += 1
        current += timedelta(days=step_days)
    return windows


def run_walk_forward_candidate(
    candidate_id: str, override: dict[str, Any],
    symbols: list[str] = SYMBOLS, start: str = START_DATE, end: str = END_DATE,
    interval: str = INTERVAL, slippage_bps: float = SLIPPAGE,
    fee_rate: float = FEE_RATE, capital: float = CAPITAL,
    realistic: bool = True, provider=None, provider_label: str = "",
    discovery_mode: str = "static", max_symbols: int = 10,
    catalyst_fn=None,
) -> dict[str, Any]:
    if provider is None:
        provider, provider_label = build_provider()
    params = build_params(override)
    fill_cfg = build_fill_config(interval, slippage_bps, fee_rate, realistic)
    windows = generate_windows(start, end)
    if not windows:
        return {"error": "No windows generated", "candidate_id": candidate_id}

    # Build discovery callback if mode is not static
    discovery_fn = make_discovery_fn(
        mode=discovery_mode, provider=provider, max_symbols=max_symbols,
        interval=interval,
    ) if discovery_mode != "static" else None

    window_results = []
    for w in windows:
        bt = ScalpScanBacktester(
            symbols=symbols, params=params,
            start_date=w["test_start"], end_date=w["test_end"],
            initial_capital=capital, slippage_bps=slippage_bps,
            provider=provider, base_interval=interval,
            fill_config=fill_cfg,
            discovery_fn=discovery_fn,
            catalyst_fn=catalyst_fn,
        )
        report = bt.run()
        window_results.append({
            "window_id": w["window_id"],
            "test_start": w["test_start"],
            "test_end": w["test_end"],
            "return_pct": report.total_return_pct,
            "profit_factor": report.profit_factor,
            "max_drawdown_pct": report.max_drawdown_pct,
            "total_trades": report.total_trades,
            "win_rate": report.win_rate,
            "sharpe_ratio": report.sharpe_ratio,
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in window_results]
    pfs = [r["profit_factor"] for r in window_results]
    trades = [r["total_trades"] for r in window_results]
    passed = sum(1 for r in window_results if r["passed"])

    return {
        "candidate_id": candidate_id,
        "provider": provider_label,
        "symbols": symbols,
        "start_date": start,
        "end_date": end,
        "interval": interval,
        "slippage_bps": slippage_bps,
        "fee_rate": fee_rate,
        "realistic_fills": realistic,
        "num_windows": len(window_results),
        "windows_passed": passed,
        "pass_rate": round(passed / len(window_results), 4) if window_results else 0,
        "total_return_pct": round(sum(returns), 4) if returns else 0,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
        "avg_profit_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
        "min_profit_factor": round(min(pfs), 4) if pfs else 0,
        "max_profit_factor": round(max(pfs), 4) if pfs else 0,
        "total_trades": sum(trades),
        "max_drawdown_pct": round(max(r["max_drawdown_pct"] for r in window_results), 4) if window_results else 0,
        "window_details": window_results,
    }


# ── Experiment modes ─────────────────────────────────────────────────

def mode_reproduce(args):
    """Reproduce a single candidate walk-forward."""
    cand = load_candidate(args.candidate)
    result = run_walk_forward_candidate(cand["config_id"], cand["override"],
                                         slippage_bps=args.slippage,
                                         discovery_mode=args.discovery,
                                         max_symbols=args.max_symbols)
    print(json.dumps(result, indent=2))
    if args.json:
        _save(result, args.json, f"run_reproduce_{cand['config_id']}")
    _print_summary(result)


def mode_ablation(args):
    """Run ablation: baseline, cap2_only, spy10_only, cap2_spy10."""
    provider, label = build_provider()
    candidates = {
        "base_short": {
            "entry_criteria": {"direction_mode": "short"},
            "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
            "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            "premove_filter": {"enabled": False},
            "market_regime": {"enabled": False},
        },
        "cap2_only": {
            "entry_criteria": {"direction_mode": "short"},
            "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
            "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            "premove_filter": {"enabled": True, "max_move_pct": 2.0, "lookback_bars": 8},
            "market_regime": {"enabled": False},
        },
        "spy10_only": {
            "entry_criteria": {"direction_mode": "short"},
            "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
            "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            "premove_filter": {"enabled": False},
            "market_regime": {"enabled": True, "symbol": "SPY", "daily_ema_period": 10,
                              "block_shorts_in_bull": True, "threshold_pct": 0.0},
        },
        "cap2_spy10": {
            "entry_criteria": {"direction_mode": "short"},
            "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
            "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            "premove_filter": {"enabled": True, "max_move_pct": 2.0, "lookback_bars": 8},
            "market_regime": {"enabled": True, "symbol": "SPY", "daily_ema_period": 10,
                              "block_shorts_in_bull": True, "threshold_pct": 0.0},
        },
    }
    results = {}
    for cid, override in candidates.items():
        print(f"\n--- Running {cid} ---", file=sys.stderr)
        results[cid] = run_walk_forward_candidate(cid, override, provider=provider,
                                                   provider_label=label,
                                                   slippage_bps=args.slippage)
    ranking = sorted(results.values(), key=lambda x: x["total_return_pct"], reverse=True)
    summary = {
        "experiment": "ablation",
        "slippage_bps": args.slippage,
        "ranking": [{k: v for k, v in r.items() if k != "window_details"} for r in ranking],
        "full_results": results,
    }
    print(json.dumps(summary["ranking"], indent=2))
    if args.json:
        _save(summary, args.json, "run_ablation")
    _print_ranking(ranking)


def mode_sweep(args):
    """Robustness sweep: premove cap x EMA period."""
    provider, label = build_provider()
    caps = [1.5, 2.0, 2.5, 3.0]
    ema_periods = [8, 10, 12, 15, 20]
    results = []
    for cap in caps:
        for ema in ema_periods:
            cid = f"cap{cap}_spy{ema}"
            override = {
                "entry_criteria": {"direction_mode": "short"},
                "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
                "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
                "premove_filter": {"enabled": True, "max_move_pct": cap, "lookback_bars": 8},
                "market_regime": {"enabled": True, "symbol": "SPY", "daily_ema_period": ema,
                                  "block_shorts_in_bull": True, "threshold_pct": 0.0},
            }
            print(f"\n--- Running {cid} ---", file=sys.stderr)
            r = run_walk_forward_candidate(cid, override, provider=provider,
                                           provider_label=label, slippage_bps=args.slippage)
            results.append(r)
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    summary = {
        "experiment": "robustness_sweep",
        "slippage_bps": args.slippage,
        "ranking": [{k: v for k, v in r.items() if k != "window_details"} for r in results],
        "full_results": results,
    }
    print(json.dumps(summary["ranking"], indent=2))
    if args.json:
        _save(summary, args.json, "run_sweep")


def mode_sensitivity(args):
    """Slippage sensitivity for cap2_spy10."""
    cand = load_candidate("cap2_spy10")
    provider, label = build_provider()
    slippage_levels = [2.0, 5.0, 10.0]
    results = {}
    for slip in slippage_levels:
        print(f"\n--- Running cap2_spy10 at {slip} bps ---", file=sys.stderr)
        results[f"{slip}bps"] = run_walk_forward_candidate(
            "cap2_spy10", cand["override"], provider=provider,
            provider_label=label, slippage_bps=slip)
    summary = {
        "experiment": "slippage_sensitivity",
        "candidate": "cap2_spy10",
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "window_details"} for k, v in results.items()},
        "full_results": results,
    }
    print(json.dumps(summary["results"], indent=2))
    if args.json:
        _save(summary, args.json, "run_sensitivity")


# ── Helpers ──────────────────────────────────────────────────────────

def _save(data: dict, path: str, default_prefix: str):
    out = Path(path) if path else RESEARCH_DIR / f"{default_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved to: {out}", file=sys.stderr)


def _print_summary(r: dict):
    print(f"\n{'='*60}")
    print(f"  Candidate: {r['candidate_id']}")
    print(f"  Windows: {r['num_windows']} | Passed: {r['windows_passed']} ({r['pass_rate']:.0%})")
    print(f"  Total return: {r['total_return_pct']:+.2f}%")
    print(f"  Avg PF: {r['avg_profit_factor']:.3f} | Min PF: {r['min_profit_factor']:.3f}")
    print(f"  Trades: {r['total_trades']} | Max DD: {r['max_drawdown_pct']:.2f}%")
    print(f"{'='*60}")


def _print_ranking(ranking: list):
    print(f"\n{'='*70}")
    print(f"  {'Rank':<5} {'Candidate':<15} {'Return':>8} {'Pass%':>7} {'AvgPF':>7} {'Trades':>7} {'MaxDD':>7}")
    print(f"  {'-'*5} {'-'*15} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for i, r in enumerate(ranking, 1):
        print(f"  {i:<5} {r['candidate_id']:<15} {r['total_return_pct']:>+7.2f}% {r['pass_rate']:>6.0%} {r['avg_profit_factor']:>7.3f} {r['total_trades']:>7} {r['max_drawdown_pct']:>6.2f}%")
    print(f"{'='*70}")


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ScalpRunner walk-forward research harness")
    parser.add_argument("--candidate", default="cap2_spy10", help="Candidate config ID")
    parser.add_argument("--slippage", type=float, default=SLIPPAGE, help="Slippage in bps")
    parser.add_argument("--json", default="", help="Save full results to JSON path")
    parser.add_argument("--discovery", choices=("static", "daily", "intraday"), default="static",
                        help="Symbol discovery mode (default: static)")
    parser.add_argument("--max-symbols", type=int, default=10,
                        help="Max symbols per discovery window")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reproduce", action="store_true", help="Reproduce single candidate")
    mode.add_argument("--ablation", action="store_true", help="Run ablation: base/cap2/spy10/both")
    mode.add_argument("--sweep", action="store_true", help="Robustness sweep: cap x EMA")
    mode.add_argument("--sensitivity", action="store_true", help="Slippage sensitivity")
    args = parser.parse_args()

    if args.ablation:
        mode_ablation(args)
    elif args.sweep:
        mode_sweep(args)
    elif args.sensitivity:
        mode_sensitivity(args)
    else:
        mode_reproduce(args)


if __name__ == "__main__":
    main()
