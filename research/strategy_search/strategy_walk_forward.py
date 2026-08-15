#!/usr/bin/env python3
"""Generic walk-forward harness for any vol-filtered strategy backtester.

Reuses generate_windows() and discover_symbols() from fence_walk_forward.py.
Works with any backtester class that subclasses VolFilteredBacktester.

Usage:
    from strategy_walk_forward import run_walk_forward
    result = run_walk_forward(
        backtester_cls=VWAPMagnetBacktester,
        override={...},
        start="2024-10-01", end="2026-08-11",
        slippage_bps=5.0, max_symbols=15,
    )
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

from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"

SLIPPAGE = 5.0
FEE_RATE = 0.001
CAPITAL = 100_000.0
DEFAULT_START = "2024-10-01"
DEFAULT_END = "2026-08-11"


def build_provider():
    alpaca = AlpacaProvider()
    if not alpaca.available:
        raise RuntimeError("Alpaca provider not available")
    return CachedProvider(alpaca), "cached-alpaca"


def generate_windows(start: str, end: str,
                     train_days: int = 14, test_days: int = 14, step_days: int = 7) -> list[dict[str, str]]:
    s = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    windows = []
    current = s
    wid = 0
    while current + timedelta(days=train_days + test_days) <= e:
        windows.append({
            "window_id": wid,
            "test_start": (current + timedelta(days=train_days)).strftime("%Y-%m-%d"),
            "test_end": (current + timedelta(days=train_days + test_days)).strftime("%Y-%m-%d"),
        })
        wid += 1
        current += timedelta(days=step_days)
    return windows


def discover_symbols(test_start: str, provider, max_symbols: int = 15) -> list[str]:
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


def run_walk_forward(
    backtester_cls,
    override: dict[str, Any],
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    slippage_bps: float = SLIPPAGE,
    fee_rate: float = FEE_RATE,
    capital: float = CAPITAL,
    max_symbols: int = 15,
    provider=None,
    provider_label: str = "",
    strategy_name: str = "",
) -> dict[str, Any]:
    """Run a walk-forward backtest for any VolFilteredBacktester subclass."""
    if provider is None:
        provider, provider_label = build_provider()
    windows = generate_windows(start, end)
    if not windows:
        return {"error": "No windows generated"}

    window_results = []
    for w in windows:
        symbols = discover_symbols(w["test_start"], provider, max_symbols)
        print(f"  Win {w['window_id']}: {w['test_start']} → {w['test_end']} | "
              f"symbols={symbols[:5]}...", file=sys.stderr)

        bt = backtester_cls(
            symbols=symbols, params=override,
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
            "eligible": bool(report.diagnostics.get("vol_filter_passed", True)),
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in window_results]
    pfs = [r["profit_factor"] for r in window_results if r["profit_factor"] > 0]
    trades = [r["total_trades"] for r in window_results]
    eligible = [r for r in window_results if r["eligible"]]
    passed = sum(1 for r in eligible if r["passed"])
    active = [r for r in eligible if r["total_trades"] > 0]
    active_passed = sum(1 for r in active if r["passed"])

    return {
        "strategy": strategy_name or backtester_cls.__name__,
        "provider": provider_label,
        "discovery": "daily_bar_scanner",
        "max_symbols": max_symbols,
        "start_date": start,
        "end_date": end,
        "interval": "5m",
        "slippage_bps": slippage_bps,
        "fee_rate": fee_rate,
        "num_windows": len(window_results),
        "eligible_windows": len(eligible),
        "active_windows": len(active),
        "windows_passed": passed,
        "pass_rate": round(passed / len(eligible), 4) if eligible else 0,
        "active_pass_rate": round(active_passed / len(active), 4) if active else 0,
        "total_return_pct": round(sum(returns), 4) if returns else 0,
        "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
        "avg_profit_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
        "total_trades": sum(trades),
        "max_drawdown_pct": round(max(r["max_drawdown_pct"] for r in window_results), 4) if window_results else 0,
        "window_details": window_results,
    }


def run_slippage_sensitivity(
    backtester_cls,
    override: dict[str, Any],
    slippage_levels: list[float] = None,
    **kwargs,
) -> list[dict[str, Any]]:
    """Run walk-forward at multiple slippage levels."""
    if slippage_levels is None:
        slippage_levels = [0.0, 2.0, 5.0, 10.0]
    results = []
    for slip in slippage_levels:
        print(f"\n=== Slippage: {slip} bps ===", file=sys.stderr)
        r = run_walk_forward(backtester_cls, override, slippage_bps=slip, **kwargs)
        results.append(r)
    return results


def run_holdout_split(
    backtester_cls,
    override: dict[str, Any],
    split_ratio: float = 0.7,
    **kwargs,
) -> dict[str, Any]:
    """Run 70/30 train/holdout split."""
    start = kwargs.pop("start", DEFAULT_START)
    end = kwargs.pop("end", DEFAULT_END)
    windows = generate_windows(start, end)
    split_idx = int(len(windows) * split_ratio)
    train_windows = windows[:split_idx]
    holdout_windows = windows[split_idx:]

    provider = kwargs.pop("provider", None)
    if provider is None:
        provider, label = build_provider()
    else:
        label = kwargs.pop("provider_label", "")

    max_symbols = kwargs.pop("max_symbols", 15)
    slippage_bps = kwargs.pop("slippage_bps", SLIPPAGE)
    fee_rate = kwargs.pop("fee_rate", FEE_RATE)
    capital = kwargs.pop("capital", CAPITAL)

    def _run_subset(wins, name):
        active = 0; passed = 0; all_ret = []; all_tr = 0; max_dd = 0
        for w in wins:
            symbols = discover_symbols(w["test_start"], provider, max_symbols)
            bt = backtester_cls(
                symbols=symbols, params=override,
                start_date=w["test_start"], end_date=w["test_end"],
                initial_capital=capital, slippage_bps=slippage_bps,
                fee_rate=fee_rate, provider=provider,
            )
            report = bt.run()
            if report.total_trades > 0:
                active += 1
                all_ret.append(report.total_return_pct)
                all_tr += report.total_trades
                max_dd = max(max_dd, report.max_drawdown_pct)
                if report.total_return_pct > 0 and report.profit_factor > 1.0:
                    passed += 1
        return {
            "name": name,
            "active_windows": active,
            "return_pct": round(sum(all_ret), 4) if all_ret else 0,
            "pass_rate": round(passed / active, 4) if active else 0,
            "trades": all_tr,
            "max_dd": round(max_dd, 4),
        }

    train_result = _run_subset(train_windows, "train")
    holdout_result = _run_subset(holdout_windows, "holdout")
    return {"train": train_result, "holdout": holdout_result}


def validate_promotion(result: dict[str, Any], holdout: dict[str, Any] | None = None,
                       sensitivity: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Apply the common promotion gate and return reasons for every failure."""
    checks = {
        "positive_5bps": result.get("total_return_pct", 0) > 0,
        "pass_rate_60pct": result.get("pass_rate", 0) >= 0.60,
        "profit_factor_115": result.get("avg_profit_factor", 0) > 1.15,
        "minimum_75_trades": result.get("total_trades", 0) >= 75,
    }
    if sensitivity:
        ten_bps = next((row for row in sensitivity if row.get("slippage_bps") == 10.0), None)
        checks["nonnegative_10bps"] = ten_bps is not None and ten_bps.get("total_return_pct", -1) >= 0
    if holdout:
        holdout_result = holdout.get("holdout", holdout)
        checks["positive_holdout"] = holdout_result.get("return_pct", -1) > 0
    return {"promoted": all(checks.values()), "checks": checks,
            "failed_checks": [name for name, passed in checks.items() if not passed]}


def save_results(data: dict, path: str):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\nSaved to: {out}", file=sys.stderr)
