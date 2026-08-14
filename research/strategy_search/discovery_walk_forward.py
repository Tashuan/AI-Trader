#!/usr/bin/env python3
"""Research-only walk-forward harness using daily-bar discovery for symbol selection.

For each walk-forward test window, ranks all universe symbols by gap,
volume ratio, and proximity to prior-day levels using daily OHLCV from
Alpaca (available for any historical date). Selects the top N candidates
and runs the ScalpRunner backtest on those dynamically selected symbols.

This closes the biggest fidelity gap (static 5-symbol watchlist) without
requiring yfinance premarket data (which is limited to 60 days back).

Usage:
    python3 research/strategy_search/discovery_walk_forward.py --candidate cap2_spy10
    python3 research/strategy_search/discovery_walk_forward.py --candidate cap2_spy10 --slippage 10
    python3 research/strategy_search/discovery_walk_forward.py --ablation
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
from execution_simulator import FillConfig
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"

UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
    "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
    "XPEV", "PLUG", "DKNG",
]
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
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
    """Rank universe symbols by gap, volume ratio, and proximity to prior-day levels.

    Uses daily OHLCV from Alpaca for the trading day before test_start.
    Falls back to DEFAULT_SYMBOLS if discovery fails.
    """
    import pandas as pd

    # Fetch daily bars ending 5 days before test_start (to get prior day)
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

            # Prior day and current day
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

            # Score: gap momentum + volume + proximity (same logic as premarket scanner)
            score = 0.0
            score += min(25.0, abs(gap_pct) * 5) if abs(gap_pct) >= 1.0 else 0
            score += min(20.0, vol_ratio * 6) if vol_ratio >= 1.25 else 0
            adv = today_close * avg_volume
            score += 20.0 if adv >= 25_000_000 else 0
            score += 15.0 if min_dist <= 1.0 else 0

            direction = "long" if gap_pct >= 0 else "short"

            candidates.append({
                "symbol": sym,
                "score": round(score, 2),
                "gap_pct": round(gap_pct, 2),
                "vol_ratio": round(vol_ratio, 3),
                "direction": direction,
                "dist_to_level": round(min_dist, 2),
            })
        except Exception:
            continue

    candidates.sort(key=lambda c: c["score"], reverse=True)
    symbols = [c["symbol"] for c in candidates[:max_symbols]]
    return symbols if symbols else DEFAULT_SYMBOLS


def run_walk_forward(
    candidate_id: str, override: dict[str, Any],
    start: str = "2026-03-02", end: str = "2026-08-11",
    interval: str = "30m", slippage_bps: float = SLIPPAGE,
    fee_rate: float = FEE_RATE, capital: float = CAPITAL,
    max_symbols: int = MAX_SYMBOLS, provider=None, provider_label: str = "",
) -> dict[str, Any]:
    if provider is None:
        provider, provider_label = build_provider()
    params = deep_merge(SCALP_DEFAULT_PARAMS, override)
    fill_cfg = build_fill_config(interval, slippage_bps, fee_rate)
    windows = generate_windows(start, end)
    if not windows:
        return {"error": "No windows generated", "candidate_id": candidate_id}

    window_results = []
    for w in windows:
        symbols = discover_symbols(w["test_start"], provider, max_symbols)
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
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in window_results]
    pfs = [r["profit_factor"] for r in window_results if r["profit_factor"] > 0]
    trades = [r["total_trades"] for r in window_results]
    passed = sum(1 for r in window_results if r["passed"])

    return {
        "candidate_id": candidate_id,
        "provider": provider_label,
        "discovery": "daily_bar_scanner",
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


def mode_reproduce(args):
    cand = load_candidate(args.candidate)
    result = run_walk_forward(cand["config_id"], cand["override"],
                               slippage_bps=args.slippage, max_symbols=args.max_symbols)
    print(json.dumps(result, indent=2, default=str))
    if args.json:
        _save(result, args.json)
    _print_summary(result)


def mode_ablation(args):
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
        results[cid] = run_walk_forward(cid, override, provider=provider,
                                         provider_label=label,
                                         slippage_bps=args.slippage,
                                         max_symbols=args.max_symbols)
    ranking = sorted(results.values(), key=lambda x: x["total_return_pct"], reverse=True)
    print(json.dumps([{k: v for k, v in r.items() if k != "window_details"} for r in ranking], indent=2))
    if args.json:
        _save({"experiment": "ablation", "results": results}, args.json)
    _print_ranking(ranking)


def mode_sweep(args):
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
            r = run_walk_forward(cid, override, provider=provider, provider_label=label,
                                 slippage_bps=args.slippage, max_symbols=args.max_symbols)
            results.append(r)
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    print(json.dumps([{k: v for k, v in r.items() if k != "window_details"} for r in results], indent=2))
    if args.json:
        _save({"experiment": "sweep", "results": results}, args.json)


def mode_sensitivity(args):
    cand = load_candidate("cap2_spy10")
    provider, label = build_provider()
    results = {}
    for slip in [2.0, 5.0, 10.0]:
        print(f"\n--- cap2_spy10 at {slip} bps ---", file=sys.stderr)
        results[f"{slip}bps"] = run_walk_forward("cap2_spy10", cand["override"],
                                                  provider=provider, provider_label=label,
                                                  slippage_bps=slip, max_symbols=args.max_symbols)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "window_details"}
                       for k, v in results.items()}, indent=2))
    if args.json:
        _save({"experiment": "sensitivity", "results": results}, args.json)


def _save(data: dict, path: str):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\nSaved to: {out}", file=sys.stderr)


def _print_summary(r: dict):
    print(f"\n{'='*70}")
    print(f"  Candidate: {r['candidate_id']} (daily discovery, max {r['max_symbols']} symbols)")
    print(f"  Windows: {r['num_windows']} | Passed: {r['windows_passed']} ({r['pass_rate']:.0%})")
    print(f"  Total return: {r['total_return_pct']:+.2f}%")
    print(f"  Avg PF: {r['avg_profit_factor']:.3f}")
    print(f"  Trades: {r['total_trades']} | Max DD: {r['max_drawdown_pct']:.2f}%")
    print(f"{'='*70}")


def _print_ranking(ranking: list):
    print(f"\n{'='*70}")
    print(f"  {'Rank':<5} {'Candidate':<15} {'Return':>8} {'Pass%':>7} {'AvgPF':>7} {'Trades':>7} {'MaxDD':>7}")
    print(f"  {'-'*5} {'-'*15} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
    for i, r in enumerate(ranking, 1):
        print(f"  {i:<5} {r['candidate_id']:<15} {r['total_return_pct']:>+7.2f}% "
              f"{r['pass_rate']:>6.0%} {r['avg_profit_factor']:>7.3f} "
              f"{r['total_trades']:>7} {r['max_drawdown_pct']:>6.2f}%")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Discovery-driven ScalpRunner walk-forward")
    parser.add_argument("--candidate", default="cap2_spy10")
    parser.add_argument("--slippage", type=float, default=SLIPPAGE)
    parser.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS)
    parser.add_argument("--json", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reproduce", action="store_true")
    mode.add_argument("--ablation", action="store_true")
    mode.add_argument("--sweep", action="store_true")
    mode.add_argument("--sensitivity", action="store_true")
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
