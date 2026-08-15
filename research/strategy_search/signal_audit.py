#!/usr/bin/env python3
"""Audit raw signal quality before running expensive strategy sweeps."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compression_backtester import CompressionBacktester
from relstrength_backtester import RelativeStrengthBacktester
from strategy_walk_forward import DEFAULT_END, DEFAULT_START, build_provider, discover_symbols, generate_windows
from sweep_reclaim_backtester import SweepReclaimBacktester

STRATEGIES = {
    "relstrength": RelativeStrengthBacktester,
    "sweep_reclaim": SweepReclaimBacktester,
    "compression": CompressionBacktester,
}


def _forward_metrics(day: pd.DataFrame, index: int, signal) -> dict:
    future = day.iloc[index + 1:index + 13]
    entry = float(signal.entry_price)
    risk = max(float(signal.risk_per_share), 1e-9)
    long = signal.side == "long"
    metrics = {
        "forward_return_3": None,
        "forward_return_6": None,
        "forward_return_12": None,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "one_r_before_minus_one_r": False,
    }
    for bars in (3, 6, 12):
        if len(future) >= bars:
            close = float(future.iloc[bars - 1]["Close"])
            metrics[f"forward_return_{bars}"] = (close / entry - 1) * 100 if long else (entry / close - 1) * 100
    if future.empty:
        return metrics
    highs = pd.to_numeric(future["High"])
    lows = pd.to_numeric(future["Low"])
    if long:
        metrics["mfe_r"] = float((highs.max() - entry) / risk)
        metrics["mae_r"] = float((lows.min() - entry) / risk)
    else:
        metrics["mfe_r"] = float((entry - lows.min()) / risk)
        metrics["mae_r"] = float((entry - highs.max()) / risk)
    for _, bar in future.iterrows():
        high, low = float(bar["High"]), float(bar["Low"])
        if long:
            stop_hit, target_hit = low <= entry - risk, high >= entry + risk
        else:
            stop_hit, target_hit = high >= entry + risk, low <= entry - risk
        if stop_hit:
            break
        if target_hit:
            metrics["one_r_before_minus_one_r"] = True
            break
    return metrics


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _group_summary(signals: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for signal in signals:
        groups[signal[key]].append(signal)
    return {
        str(name): {
            "signals": len(rows),
            "avg_forward_3": _mean([r["forward_return_3"] for r in rows if r["forward_return_3"] is not None]),
            "avg_forward_6": _mean([r["forward_return_6"] for r in rows if r["forward_return_6"] is not None]),
            "avg_mfe_r": _mean([r["mfe_r"] for r in rows]),
            "avg_mae_r": _mean([r["mae_r"] for r in rows]),
            "one_r_rate": round(sum(r["one_r_before_minus_one_r"] for r in rows) / len(rows), 4),
        }
        for name, rows in groups.items()
    }


def audit_strategy(backtester_cls, override: dict, start: str, end: str,
                   max_symbols: int, provider=None, provider_label: str = "") -> dict:
    if provider is None:
        provider, provider_label = build_provider()
    signals = []
    seen: set[tuple[str, str]] = set()
    eligible_windows = 0
    windows = generate_windows(start, end)
    for window in windows:
        symbols = discover_symbols(window["test_start"], provider, max_symbols)
        bt = backtester_cls(symbols=symbols, params=override,
                            start_date=window["test_start"], end_date=window["test_end"],
                            provider=provider, slippage_bps=0.0)
        if not bt._vol_filter_passes(pd.Timestamp(window["test_start"]).date()):
            continue
        eligible_windows += 1
        frames = {symbol: bt._fetch(symbol) for symbol in symbols}
        frames = {symbol: frame for symbol, frame in frames.items() if frame is not None and not frame.empty}
        dates = sorted({date for frame in frames.values() for date in frame["Timestamp"].dt.date})
        for date in dates:
            symbol = bt._choose_symbol(frames, date)
            if not symbol:
                continue
            key = (str(date), symbol)
            if key in seen:
                continue
            seen.add(key)
            day = frames[symbol][frames[symbol]["Timestamp"].dt.date == date].reset_index(drop=True)
            strategy = bt.create_strategy(symbol, date=date, day=day)
            for index, bar in day.iterrows():
                signal = strategy.on_bar(bar["Timestamp"], bar, index)
                if signal is None:
                    continue
                metrics = _forward_metrics(day, index, signal)
                ts = pd.Timestamp(signal.timestamp)
                signals.append({
                    "window_id": window["window_id"],
                    "date": str(date),
                    "symbol": symbol,
                    "side": signal.side,
                    "entry_time": str(signal.timestamp),
                    "entry_price": signal.entry_price,
                    "reason": signal.reason,
                    "time_band": "09:30-10:00" if ts.time() < pd.Timestamp("10:00").time() else "10:00-11:00" if ts.time() < pd.Timestamp("11:00").time() else "11:00-13:00",
                    **metrics,
                })
                break
    summary = {
        "strategy": backtester_cls.__name__,
        "provider": provider_label,
        "start_date": start,
        "end_date": end,
        "eligible_windows": eligible_windows,
        "signals": len(signals),
        "long_signals": sum(row["side"] == "long" for row in signals),
        "short_signals": sum(row["side"] == "short" for row in signals),
        "avg_forward_3": _mean([row["forward_return_3"] for row in signals if row["forward_return_3"] is not None]),
        "avg_forward_6": _mean([row["forward_return_6"] for row in signals if row["forward_return_6"] is not None]),
        "avg_forward_12": _mean([row["forward_return_12"] for row in signals if row["forward_return_12"] is not None]),
        "avg_mfe_r": _mean([row["mfe_r"] for row in signals]),
        "avg_mae_r": _mean([row["mae_r"] for row in signals]),
        "one_r_rate": round(sum(row["one_r_before_minus_one_r"] for row in signals) / len(signals), 4) if signals else 0,
        "by_side": _group_summary(signals, "side"),
        "by_time_band": _group_summary(signals, "time_band"),
        "signals_detail": signals,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit raw intraday signal quality")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--config", default="", help="JSON file containing parameter overrides")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    override = {}
    if args.config:
        override = json.loads(Path(args.config).read_text())
    provider, label = build_provider()
    result = audit_strategy(STRATEGIES[args.strategy], override, args.start, args.end,
                            args.max_symbols, provider, label)
    print(json.dumps({key: value for key, value in result.items() if key != "signals_detail"}, indent=2))
    if args.json:
        output = Path(args.json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
