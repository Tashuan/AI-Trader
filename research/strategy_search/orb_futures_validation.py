"""Validation harness for the futures ORB research backtester.

This harness deliberately reports candidate performance by window and
instrument. It does not promote a strategy or start a runner.

The current yfinance 5m source is limited to roughly 60 days, so results
from this harness remain exploratory until a longer futures data source is
available.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from orb_futures_backtester import (
    FUTURES_ORB_CONFIG,
    fetch_futures_5m,
    run_futures_orb_backtest,
)

DEFAULT_SYMBOLS = ["MES=F", "MNQ=F", "M2K=F", "MYM=F"]

CANDIDATES: dict[str, dict[str, Any]] = {
    "baseline": {
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.0,
    },
    "extension_005": {
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.05,
    },
    "extension_010": {
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.10,
    },
    "target_150": {
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 1.5,
        "extension_filter_pct": 0.0,
    },
    "stop_100": {
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 1.0,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.0,
    },
    "range_10m": {
        "range_minutes": 10,
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.0,
    },
    "range_15m": {
        "range_minutes": 15,
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.0,
    },
    "confirm_1": {
        "confirmation_bars": 1,
        "risk_per_trade_pct": 1.0,
        "stop_range_multiplier": 0.5,
        "target_r_multiple": 2.0,
        "extension_filter_pct": 0.0,
    },
}


def _filter_dates(
    frames: dict[str, pd.DataFrame],
    start: date,
    end: date,
) -> dict[str, pd.DataFrame]:
    """Return copies restricted to inclusive session dates."""
    result = {}
    for symbol, frame in frames.items():
        mask = (frame.index.date >= start) & (frame.index.date <= end)
        result[symbol] = frame.loc[mask].copy()
    return result


def _date_bounds(frames: dict[str, pd.DataFrame]) -> tuple[date, date]:
    dates = [ts.date() for frame in frames.values() for ts in frame.index]
    if not dates:
        raise ValueError("No futures bars were loaded")
    return min(dates), max(dates)


def _windows(start: date, end: date, window_days: int, step_days: int):
    """Build chronological, non-overlapping-ish test windows."""
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=window_days - 1))
        yield cursor, window_end
        cursor += timedelta(days=step_days)


def run_validation(
    symbols: list[str],
    period: str,
    capital: float,
    window_days: int,
    step_days: int,
    provider: str = "yfinance",
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    frames = fetch_futures_5m(
        symbols, period=period, provider=provider, start=start, end=end
    )
    start, end = _date_bounds(frames)
    windows = list(_windows(start, end, window_days, step_days))
    reports = []

    for candidate_id, overrides in CANDIDATES.items():
        candidate_reports = []
        config = {**FUTURES_ORB_CONFIG, **overrides}
        for window_start, window_end in windows:
            window_frames = _filter_dates(frames, window_start, window_end)
            result = run_futures_orb_backtest(
                symbols, window_frames, capital=capital, config=config
            )
            candidate_reports.append({
                "start": str(window_start),
                "end": str(window_end),
                "return_pct": result["total_return_pct"],
                "pnl": result["total_pnl"],
                "max_drawdown_pct": result["max_drawdown_pct"],
                "profit_factor": result["profit_factor"],
                "win_rate": result["win_rate"],
                "trades": result["total_trades"],
                "per_symbol": result["per_symbol"],
                "diagnostics": result["diagnostics"],
            })
        profitable = [r for r in candidate_reports if r["pnl"] > 0]
        full_result = run_futures_orb_backtest(
            symbols, frames, capital=capital, config=config
        )
        daily_pnl = full_result["daily_pnl"]
        best_day_pnl = max(daily_pnl.values()) if daily_pnl else 0.0
        symbol_pnl = {
            symbol: stats["pnl"]
            for symbol, stats in full_result["per_symbol"].items()
        }
        positive_symbol_pnl = sum(v for v in symbol_pnl.values() if v > 0)
        reports.append({
            "candidate": candidate_id,
            "config": overrides,
            "windows": candidate_reports,
            "windows_profitable": len(profitable),
            "windows_total": len(candidate_reports),
            "pass_rate": len(profitable) / len(candidate_reports) if candidate_reports else 0,
            "total_pnl": full_result["total_pnl"],
            "full_period": {
                "return_pct": full_result["total_return_pct"],
                "max_drawdown_pct": full_result["max_drawdown_pct"],
                "profit_factor": full_result["profit_factor"],
                "trades": full_result["total_trades"],
                "best_day_pnl": best_day_pnl,
                "pnl_without_best_day": full_result["total_pnl"] - best_day_pnl,
                "per_symbol": symbol_pnl,
                "largest_positive_symbol_share": (
                    max(symbol_pnl.values()) / positive_symbol_pnl
                    if positive_symbol_pnl > 0 else 0.0
                ),
                "diagnostics": full_result["diagnostics"],
            },
        })

    return {
        "data_source": provider,
        "period": period,
        "start": start,
        "end": end,
        "symbols": symbols,
        "available_start": str(start),
        "available_end": str(end),
        "window_days": window_days,
        "step_days": step_days,
        "capital": capital,
        "status": "exploratory_not_validated",
        "candidates": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate futures ORB candidates")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--provider", choices=["yfinance", "massive"], default="yfinance")
    parser.add_argument("--period", default="60d")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--window-days", type=int, default=10)
    parser.add_argument("--step-days", type=int, default=10)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    report = run_validation(
        symbols, args.period, args.capital, args.window_days, args.step_days,
        provider=args.provider, start=args.start, end=args.end,
    )
    print(json.dumps(report, indent=2, default=str))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"Saved validation report to {args.output}")


if __name__ == "__main__":
    main()
