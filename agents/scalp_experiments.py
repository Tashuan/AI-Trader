"""ScalpRunner timeframe and configuration experiment runner."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from data_cache import CacheOnlyProvider, CachedProvider
from equity_data_providers import AlpacaProvider
from execution_simulator import FillConfig
from market_data import YFinanceProvider
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge
from schwab_provider import get_schwab_provider


DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
DEFAULT_INTERVALS = ["5m", "15m", "30m"]


def build_experiment_matrix() -> dict[str, dict[str, Any]]:
    profiles = {
        "baseline": {},
        "strict": {
            "entry_criteria": {
                "min_signals": 4,
                "min_signal_families": 3,
                "min_vol_ratio": 1.8,
            },
        },
        "frequent": {
            "entry_criteria": {
                "min_signals": 2,
                "min_signal_families": 1,
                "min_vol_ratio": 1.1,
            },
        },
        "tight_exits": {
            "exit_rules": {
                "stop_loss_pct": -0.8,
                "take_profit_pct": 1.0,
                "trailing_sl_pct": 0.4,
                "trailing_activation_pct": 0.6,
            },
        },
        # ── v2 profiles: based on trade-level analysis ──
        "short_only": {
            "entry_criteria": {"direction_mode": "short"},
        },
        "favorable_rr": {
            "order": {
                "sl_atr_multiple": 0.7,
                "tp_atr_multiple": 2.5,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.4,
                "trailing_activation_pct": 0.5,
            },
        },
        "asymmetric": {
            "order": {
                "long_sl_atr_multiple": 0.7,
                "long_tp_atr_multiple": 2.0,
                "short_sl_atr_multiple": 1.0,
                "short_tp_atr_multiple": 1.8,
            },
            "exit_rules": {
                "long_trailing_sl_pct": 0.35,
                "long_trailing_activation_pct": 0.5,
                "short_trailing_sl_pct": 0.45,
                "short_trailing_activation_pct": 0.7,
            },
        },
        "short_favorable": {
            "entry_criteria": {"direction_mode": "short"},
            "order": {
                "sl_atr_multiple": 0.8,
                "tp_atr_multiple": 2.2,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.35,
                "trailing_activation_pct": 0.5,
            },
        },
        "tight_trail": {
            "exit_rules": {
                "trailing_sl_pct": 0.3,
                "trailing_activation_pct": 0.4,
            },
        },
        "short_tight_trail": {
            "entry_criteria": {"direction_mode": "short"},
            "exit_rules": {
                "trailing_sl_pct": 0.3,
                "trailing_activation_pct": 0.4,
            },
        },
    }
    matrix = {}
    for interval in DEFAULT_INTERVALS:
        for profile, override in profiles.items():
            interval_override = {
                "order": {"order_expiry_minutes": {"5m": 30, "15m": 90, "30m": 180}[interval]},
            }
            matrix[f"{interval}_{profile}"] = {
                "interval": interval,
                "profile": profile,
                "override": deep_merge(interval_override, override),
            }
    return matrix


def resolve_provider(name: str, cache: bool):
    if name == "cache":
        return CacheOnlyProvider(), "cache"
    if name == "alpaca":
        provider = AlpacaProvider()
        if not provider.available:
            raise RuntimeError("Alpaca is not configured")
        selected = "alpaca"
    elif name == "schwab":
        provider = get_schwab_provider()
        if not provider.is_configured:
            raise RuntimeError("Schwab is not configured")
        selected = "schwab"
    elif name == "yfinance":
        provider, selected = YFinanceProvider(), "yfinance"
    else:
        alpaca = AlpacaProvider()
        schwab = get_schwab_provider()
        if alpaca.available:
            provider, selected = alpaca, "alpaca"
        elif schwab.is_configured:
            provider, selected = schwab, "schwab"
        else:
            provider, selected = YFinanceProvider(), "yfinance"
    if cache:
        provider = CachedProvider(provider)
        selected = f"cached-{selected}"
    return provider, selected


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


def score_report(report) -> float:
    trade_factor = min(report.total_trades / 100.0, 1.0)
    return round(
        report.total_return_pct
        + max(report.profit_factor - 1.0, -1.0) * 2.0
        - report.max_drawdown_pct * 0.5
        + trade_factor,
        4,
    )


def run_matrix(
    symbols: list[str], start: str, end: str, provider_name: str = "auto",
    cache: bool = True, capital: float = 100_000.0, slippage_bps: float = 2.0,
    fee_rate: float = 0.001, realistic: bool = True,
) -> dict[str, Any]:
    provider, selected_provider = resolve_provider(provider_name, cache)
    results = []
    matrix = build_experiment_matrix()
    for experiment_id, config in matrix.items():
        params = deep_merge(SCALP_DEFAULT_PARAMS, config["override"])
        bt = ScalpScanBacktester(
            symbols=symbols,
            params=params,
            start_date=start,
            end_date=end,
            initial_capital=capital,
            slippage_bps=slippage_bps,
            provider=provider,
            base_interval=config["interval"],
            fill_config=build_fill_config(config["interval"], slippage_bps, fee_rate, realistic),
        )
        report = bt.run()
        results.append({
            "experiment_id": experiment_id,
            "interval": config["interval"],
            "profile": config["profile"],
            "score": score_report(report),
            "report": report.to_dict(),
        })
    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "provider": selected_provider,
        "symbols": symbols,
        "start_date": start,
        "end_date": end,
        "results": results,
        "ranking": [
            {
                "rank": index,
                "experiment_id": result["experiment_id"],
                "interval": result["interval"],
                "profile": result["profile"],
                "score": result["score"],
                "return_pct": result["report"]["total_return_pct"],
                "profit_factor": result["report"]["profit_factor"],
                "max_drawdown_pct": result["report"]["max_drawdown_pct"],
                "win_rate": result["report"]["win_rate"],
                "trades": result["report"]["total_trades"],
            }
            for index, result in enumerate(results, start=1)
        ],
    }


def run_holdout(
    symbols: list[str], train_start: str, train_end: str, test_start: str, test_end: str,
    provider_name: str, cache: bool, capital: float, slippage_bps: float,
    fee_rate: float, realistic: bool,
) -> dict[str, Any]:
    train = run_matrix(
        symbols, train_start, train_end, provider_name, cache, capital,
        slippage_bps, fee_rate, realistic,
    )
    test = run_matrix(
        symbols, test_start, test_end, provider_name, cache, capital,
        slippage_bps, fee_rate, realistic,
    )
    train_by_id = {item["experiment_id"]: item for item in train["results"]}
    test_by_id = {item["experiment_id"]: item for item in test["results"]}
    selected = sorted(train_by_id, key=lambda key: train_by_id[key]["score"], reverse=True)[:4]
    return {
        "train": train,
        "test": test,
        "selected_from_train": selected,
        "out_of_sample_ranking": sorted(
            [
                {
                    "experiment_id": key,
                    "train_score": train_by_id[key]["score"],
                    "test_score": test_by_id[key]["score"],
                    "test_return_pct": test_by_id[key]["report"]["total_return_pct"],
                    "test_profit_factor": test_by_id[key]["report"]["profit_factor"],
                    "test_drawdown_pct": test_by_id[key]["report"]["max_drawdown_pct"],
                    "test_trades": test_by_id[key]["report"]["total_trades"],
                    "selected": key in selected,
                }
                for key in test_by_id
            ],
            key=lambda item: item["test_score"], reverse=True,
        ),
    }


def generate_walk_forward_windows(
    start_date: str, end_date: str,
    train_days: int = 14, test_days: int = 14, step_days: int = 7,
) -> list[dict[str, str]]:
    """Generate rolling walk-forward windows."""
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    windows = []
    current = start
    wid = 0
    while current + timedelta(days=train_days + test_days) <= end:
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


def run_walk_forward(
    symbols: list[str], start: str, end: str,
    candidates: dict[str, dict[str, Any]] | None = None,
    provider_name: str = "cache", cache: bool = False,
    capital: float = 100_000.0, slippage_bps: float = 5.0,
    fee_rate: float = 0.001, realistic: bool = True,
    train_days: int = 14, test_days: int = 14, step_days: int = 7,
    interval: str = "30m",
) -> dict[str, Any]:
    """Run rolling walk-forward validation for top candidates."""
    provider, selected_provider = resolve_provider(provider_name, cache)
    windows = generate_walk_forward_windows(start, end, train_days, test_days, step_days)
    if not windows:
        return {"error": "No windows generated for the given date range"}

    if candidates is None:
        # Default candidates: the top performers from v2 experiments
        candidates = {
            "short_sl1.5": {
                "entry_criteria": {"direction_mode": "short"},
                "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
                "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            },
            "short_sl2.0": {
                "entry_criteria": {"direction_mode": "short"},
                "order": {"sl_atr_multiple": 2.0, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
                "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            },
            "both_sl1.5": {
                "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
                "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            },
            "both_sl0.7": {
                "order": {"sl_atr_multiple": 0.7, "tp_atr_multiple": 2.5, "order_expiry_minutes": 180},
                "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5},
            },
        }

    fill_cfg = build_fill_config(interval, slippage_bps, fee_rate, realistic)
    all_results = {}

    for cand_id, override in candidates.items():
        params = deep_merge(SCALP_DEFAULT_PARAMS, override)
        window_results = []
        for w in windows:
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
                "train_start": w["train_start"],
                "train_end": w["train_end"],
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
        all_results[cand_id] = {
            "candidate_id": cand_id,
            "windows_run": len(window_results),
            "windows_passed": passed,
            "pass_rate": round(passed / len(window_results), 3) if window_results else 0,
            "avg_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
            "total_return_pct": round(sum(returns), 4) if returns else 0,
            "avg_profit_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
            "min_profit_factor": round(min(pfs), 4) if pfs else 0,
            "max_profit_factor": round(max(pfs), 4) if pfs else 0,
            "total_trades": sum(trades),
            "max_drawdown_pct": round(max(r["max_drawdown_pct"] for r in window_results), 4) if window_results else 0,
            "positive_windows": passed,
            "window_details": window_results,
        }

    # Rank by total return, then by pass rate
    ranking = sorted(all_results.values(), key=lambda x: (x["total_return_pct"], x["pass_rate"]), reverse=True)
    return {
        "provider": selected_provider,
        "symbols": symbols,
        "start_date": start,
        "end_date": end,
        "interval": interval,
        "slippage_bps": slippage_bps,
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days,
        "num_windows": len(windows),
        "windows": windows,
        "candidates": all_results,
        "ranking": [
            {
                "rank": i + 1,
                "candidate_id": c["candidate_id"],
                "total_return_pct": c["total_return_pct"],
                "avg_return_pct": c["avg_return_pct"],
                "pass_rate": c["pass_rate"],
                "windows_passed": c["windows_passed"],
                "windows_run": c["windows_run"],
                "avg_profit_factor": c["avg_profit_factor"],
                "min_profit_factor": c["min_profit_factor"],
                "total_trades": c["total_trades"],
                "max_drawdown_pct": c["max_drawdown_pct"],
            }
            for i, c in enumerate(ranking)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ScalpRunner timeframe/configuration experiments")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--train-end", default="")
    parser.add_argument("--test-start", default="")
    parser.add_argument("--provider", choices=("auto", "alpaca", "schwab", "yfinance", "cache"), default="auto")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--slippage", type=float, default=2.0)
    parser.add_argument("--fee-rate", type=float, default=0.001)
    parser.add_argument("--no-realistic-fills", action="store_true")
    parser.add_argument("--walk-forward", action="store_true",
                        help="Run rolling walk-forward validation instead of single holdout")
    parser.add_argument("--train-days", type=int, default=14)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--interval", default="30m",
                        help="Interval for walk-forward mode (default: 30m)")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    cache = not args.no_cache and args.provider != "cache"
    if bool(args.train_end) != bool(args.test_start):
        parser.error("--train-end and --test-start must be supplied together")
    if args.walk_forward:
        result = run_walk_forward(
            symbols=symbols,
            start=args.start,
            end=args.end,
            provider_name=args.provider,
            cache=cache,
            capital=args.capital,
            slippage_bps=args.slippage,
            fee_rate=args.fee_rate,
            realistic=not args.no_realistic_fills,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            interval=args.interval,
        )
        print(json.dumps(result["ranking"], indent=2))
    elif args.train_end and args.test_start:
        result = run_holdout(
            symbols=symbols,
            train_start=args.start,
            train_end=args.train_end,
            test_start=args.test_start,
            test_end=args.end,
            provider_name=args.provider,
            cache=cache,
            capital=args.capital,
            slippage_bps=args.slippage,
            fee_rate=args.fee_rate,
            realistic=not args.no_realistic_fills,
        )
        print(json.dumps(result["out_of_sample_ranking"], indent=2))
    else:
        result = run_matrix(
            symbols=symbols,
            start=args.start,
            end=args.end,
            provider_name=args.provider,
            cache=cache,
            capital=args.capital,
            slippage_bps=args.slippage,
            fee_rate=args.fee_rate,
            realistic=not args.no_realistic_fills,
        )
        print(json.dumps(result["ranking"], indent=2))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=2)
        print(f"Full experiment report saved to: {args.json}")


if __name__ == "__main__":
    main()
