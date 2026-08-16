#!/usr/bin/env python3
"""Fence range sweep + per-symbol PnL attribution for the Fence Bar strategy.

Part 1: Sweep different fence min/max range combinations with ETF exclusion
        (SPY/QQQ/IWM removed) at ATR 1.0, slippage 5 bps.
Part 2: Run a single full-period backtest with the current best params
        (fence 0.35-0.80) and attribute PnL to individual symbols.

Usage:
    python3 research/strategy_search/fence_sweep_attribution.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
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
OUTPUT_PATH = RESEARCH_DIR / "fence_sweep_attribution.json"

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}

NO_ETF_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "BA", "DIS", "BABA", "COIN", "MARA", "RIOT",
    "SOFI", "AAL", "UAL", "F", "GM", "NIO", "XPEV", "PLUG", "DKNG",
]

FENCE_RANGES = [
    (0.20, 0.60),
    (0.25, 0.70),
    (0.30, 0.80),
    (0.35, 0.80),
    (0.35, 1.00),
    (0.40, 1.00),
    (0.50, 1.50),
]

BEST_RANGE = (0.35, 0.80)


def _make_override(min_range: float, max_range: float) -> dict[str, Any]:
    """Build the deep-merged override for a given fence range."""
    override = {
        "retest": {"enabled": False},
        "fence": {"min_range_pct": min_range, "max_range_pct": max_range},
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
    return deep_merge(FENCE_BAR_DEFAULTS, override)


def _make_discover_symbols(exclude_etfs: bool):
    """Return a discover_symbols replacement with an ETF-filtered universe."""
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
        import pandas as pd

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


# ── Part 1: Fence range sweep ──────────────────────────────────────────────

def run_fence_sweep(provider, provider_label: str) -> list[dict[str, Any]]:
    """Sweep fence ranges with ETF exclusion via walk-forward."""
    results = []
    original_discover = swf.discover_symbols
    swf.discover_symbols = _make_discover_symbols(exclude_etfs=True)
    try:
        for min_pct, max_pct in FENCE_RANGES:
            label = f"fence_{min_pct:.2f}_{max_pct:.2f}"
            params = _make_override(min_pct, max_pct)
            print(f"\n{'=' * 60}", file=sys.stderr)
            print(f"  Sweep: fence {min_pct:.2f}-{max_pct:.2f}% (ETF excluded, ATR 1.0)", file=sys.stderr)
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
            results.append({
                "min_range_pct": min_pct,
                "max_range_pct": max_pct,
                "total_return_pct": result.get("total_return_pct", 0),
                "total_trades": result.get("total_trades", 0),
                "avg_profit_factor": result.get("avg_profit_factor", 0),
                "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                "pass_rate": result.get("pass_rate", 0),
                "active_windows": result.get("active_windows", 0),
                "full_result": result,
            })
    finally:
        swf.discover_symbols = original_discover
    return results


def print_sweep_table(results: list[dict[str, Any]]) -> None:
    """Print the fence range sweep comparison table."""
    print("\n" + "=" * 78)
    print("  FENCE RANGE SWEEP  (ETF excluded, ATR 1.0, slippage 5 bps)")
    print("=" * 78)
    hdr = f"{'Min%':>6} | {'Max%':>6} | {'Return %':>10} | {'Trades':>7} | {'AggPF':>7} | {'Max DD %':>9}"
    print(hdr)
    print("-" * 78)
    for r in results:
        print(
            f"{r['min_range_pct']:>6.2f} | {r['max_range_pct']:>6.2f} | "
            f"{r['total_return_pct']:>10.4f} | {r['total_trades']:>7} | "
            f"{r['avg_profit_factor']:>7.4f} | {r['max_drawdown_pct']:>9.4f}"
        )
    print("=" * 78)

    best = max(results, key=lambda r: r["total_return_pct"])
    print(f"\n  Best by return: {best['min_range_pct']:.2f}-{best['max_range_pct']:.2f}% "
          f"→ {best['total_return_pct']:.4f}% return, AggPF {best['avg_profit_factor']:.4f}, "
          f"{best['total_trades']} trades")


# ── Part 2: Per-symbol PnL attribution ─────────────────────────────────────

def run_attribution(provider, provider_label: str) -> dict[str, Any]:
    """Run a single full-period backtest with best params and attribute PnL."""
    params = _make_override(BEST_RANGE[0], BEST_RANGE[1])
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Per-symbol attribution: fence {BEST_RANGE[0]}-{BEST_RANGE[1]}%", file=sys.stderr)
    print(f"  Full no-ETF universe ({len(NO_ETF_UNIVERSE)} symbols), single backtest", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    bt = FenceBarBacktester(
        symbols=NO_ETF_UNIVERSE,
        params=params,
        start_date="2024-10-01",
        end_date="2026-08-11",
        initial_capital=100_000,
        slippage_bps=5.0,
        fee_rate=0.001,
        provider=provider,
    )
    report = bt.run()

    trades = report.trades
    by_symbol: dict[str, list] = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", "?") if isinstance(t, dict) else t.symbol
        pnl = t.get("pnl", 0) if isinstance(t, dict) else t.pnl
        by_symbol[sym].append(pnl)

    symbol_stats = []
    for sym, pnls in by_symbol.items():
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        symbol_stats.append({
            "symbol": sym,
            "trades": total,
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(wins / total * 100, 2) if total else 0.0,
            "avg_pnl": round(sum(pnls) / total, 2) if total else 0.0,
        })
    symbol_stats.sort(key=lambda s: s["total_pnl"], reverse=True)

    return {
        "fence_range": {"min": BEST_RANGE[0], "max": BEST_RANGE[1]},
        "total_return_pct": report.total_return_pct,
        "total_trades": report.total_trades,
        "profit_factor": report.profit_factor,
        "max_drawdown_pct": report.max_drawdown_pct,
        "win_rate": report.win_rate,
        "per_symbol": symbol_stats,
    }


def print_attribution_table(attribution: dict[str, Any]) -> None:
    """Print the per-symbol PnL attribution table."""
    print("\n" + "=" * 78)
    print(f"  PER-SYMBOL PnL ATTRIBUTION  (fence {attribution['fence_range']['min']}-"
          f"{attribution['fence_range']['max']}%, ATR 1.0, ETF excluded)")
    print(f"  Total return: {attribution['total_return_pct']:.4f}%  |  "
          f"Trades: {attribution['total_trades']}  |  "
          f"PF: {attribution['profit_factor']:.4f}  |  "
          f"Max DD: {attribution['max_drawdown_pct']:.4f}%")
    print("=" * 78)
    hdr = f"{'Symbol':>8} | {'Trades':>7} | {'Total PnL':>12} | {'Win Rate%':>10} | {'Avg PnL':>10}"
    print(hdr)
    print("-" * 78)
    for s in attribution["per_symbol"]:
        print(
            f"{s['symbol']:>8} | {s['trades']:>7} | {s['total_pnl']:>12.2f} | "
            f"{s['win_rate']:>10.2f} | {s['avg_pnl']:>10.2f}"
        )
    print("=" * 78)

    per_sym = attribution["per_symbol"]
    if per_sym:
        winners = [s for s in per_sym if s["total_pnl"] > 0]
        losers = [s for s in per_sym if s["total_pnl"] < 0]
        print(f"\n  Biggest winners:")
        for s in winners[:5]:
            print(f"    {s['symbol']:>6}  +{s['total_pnl']:>10.2f}  ({s['trades']} trades, {s['win_rate']:.1f}% win)")
        print(f"\n  Biggest losers:")
        for s in sorted(losers, key=lambda x: x["total_pnl"])[:5]:
            print(f"    {s['symbol']:>6}  {s['total_pnl']:>11.2f}  ({s['trades']} trades, {s['win_rate']:.1f}% win)")
        print(f"\n  Summary: {len(winners)} winning symbols, {len(losers)} losing symbols, "
              f"{len(per_sym)} total active")


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    provider, provider_label = swf.build_provider()

    sweep_results = run_fence_sweep(provider, provider_label)
    print_sweep_table(sweep_results)

    attribution = run_attribution(provider, provider_label)
    print_attribution_table(attribution)

    output = {
        "fence_sweep": sweep_results,
        "attribution": attribution,
        "config": {
            "etf_symbols_excluded": sorted(ETF_SYMBOLS),
            "no_etf_universe": NO_ETF_UNIVERSE,
            "atr_threshold": 1.0,
            "slippage_bps": 5.0,
            "start": "2024-10-01",
            "end": "2026-08-11",
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
