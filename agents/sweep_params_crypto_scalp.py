#!/usr/bin/env python3
"""Scalp parameter sweep for CryptoRunner — tight TP/SL + bigger size.

Tests the thesis: "small moves, big size" vs the current swing config
(big moves, small size). Runs at 1h interval for more granular exits
while keeping enough yfinance history (730d for 1h crypto).

Usage:
    python3 sweep_params_crypto_scalp.py
    python3 sweep_params_crypto_scalp.py --interval 1h --symbols BTC,ETH,SOL
    python3 sweep_params_crypto_scalp.py --goal-target 100 --goal-max-loss 200
"""

import sys
import os
import copy
import json
import argparse
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_registry import effective_params
from crypto_scan_backtester import CryptoScanBacktester
from data_cache import CachedProvider
from market_data import YFinanceProvider

# yfinance intraday lookback for crypto (more generous than equities)
_MAX_LOOKBACK_DAYS = {
    "1m": 7, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1h": 730,
}

DEFAULT_SYMBOLS = [
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK",
    "DOT", "LTC", "UNI", "ATOM", "NEAR", "ARB", "OP", "INJ",
]


def deep_set(d, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def deep_set_many(d, overrides):
    for path, value in overrides:
        deep_set(d, path, value)


def run_single(params, symbols, start, end, capital, interval, goal_target=None,
               goal_max_loss=None, provider=None):
    bt = CryptoScanBacktester(
        symbols, params, start, end, capital, interval, 5.0,
        goal_target=goal_target, goal_max_loss=goal_max_loss,
        provider=provider,
    )
    return bt.run().to_dict()


def _daily_pnl_stats(report: dict) -> dict:
    trades = report.get("trades", [])
    by_day = defaultdict(float)
    for t in trades:
        day = str(t.get("exit_date", ""))[:10]
        if day:
            by_day[day] += t.get("pnl", 0.0)
    if not by_day:
        return {"avg_daily_pnl": 0.0, "best_day": 0.0, "worst_day": 0.0,
                "trading_days": 0, "days_hit_100": 0, "pct_days_hit_100": 0}
    vals = list(by_day.values())
    days_hit_100 = sum(1 for v in vals if v >= 100.0)
    return {
        "avg_daily_pnl": sum(vals) / len(vals),
        "best_day": max(vals),
        "worst_day": min(vals),
        "trading_days": len(vals),
        "days_hit_100": days_hit_100,
        "pct_days_hit_100": days_hit_100 / len(vals) * 100,
    }


def extract_summary(report: dict) -> dict:
    g = report.get("goal_simulation", {})
    daily = _daily_pnl_stats(report)
    return {
        "return_pct": report["total_return_pct"],
        "final_equity": report["final_equity"],
        "sharpe": report["sharpe_ratio"],
        "max_dd": report["max_drawdown_pct"],
        "win_rate": report["win_rate"],
        "profit_factor": report["profit_factor"],
        "trades": report["total_trades"],
        "avg_hold_h": report.get("avg_hold_hours", 0),
        "goal_status": g.get("status", "n/a"),
        "goal_achieved": g.get("goal_achieved", False),
        "goal_halt_ts": g.get("halt_timestamp", None),
        **daily,
    }


def print_table(results: dict, sort_by: str = "avg_daily_pnl"):
    ordered = dict(sorted(results.items(), key=lambda kv: kv[1][sort_by], reverse=True))
    print(f"\n{'='*160}")
    print(f"{'Experiment':<30} {'AvgDay$':>9} {'BestDay$':>9} {'WorstDay$':>10} "
          f"{'%Days>=100':>10} {'Ret%':>7} {'Sharpe':>7} {'MaxDD%':>7} "
          f"{'WinRate':>8} {'PF':>6} {'Trades':>7} {'Hold(h)':>8} {'Days':>5}")
    print(f"{'-'*30} {'-'*9} {'-'*9} {'-'*10} {'-'*10} {'-'*7} {'-'*7} {'-'*7} "
          f"{'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*5}")
    for name, s in ordered.items():
        print(f"{name:<30} {s['avg_daily_pnl']:>9.2f} {s['best_day']:>9.2f} "
              f"{s['worst_day']:>10.2f} {s['pct_days_hit_100']:>9.1f}% "
              f"{s['return_pct']:>7.2f} {s['sharpe']:>7.3f} {s['max_dd']:>7.2f} "
              f"{s['win_rate']:>8.1%} {s['profit_factor']:>6.3f} {s['trades']:>7} "
              f"{s['avg_hold_h']:>8.2f} {s['trading_days']:>5}")
    print(f"{'='*160}\n")
    best_name = next(iter(ordered))
    print(f"Best by {sort_by}: {best_name} → ${ordered[best_name]['avg_daily_pnl']:.2f}/day "
          f"({ordered[best_name]['pct_days_hit_100']:.0f}% of days hit $100+)\n")


def build_scalp_experiments() -> dict:
    """Scalp posture variants for CryptoRunner.

    Baseline is the current swing config (TP=8%, SL=-5%, size=12-16%).
    Scalp variants tighten exits and increase position size.
    """
    # Current swing config — the benchmark
    current_swing = []

    # Position size scaling — crypto currently uses 12-16% per position
    size_up = [
        ("position_sizing.normal_sizing_min_pct", 20),
        ("position_sizing.normal_sizing_max_pct", 30),
    ]
    size_degen = [
        ("position_sizing.normal_sizing_min_pct", 30),
        ("position_sizing.normal_sizing_max_pct", 40),
    ]

    # Tighter exits — capture smaller moves
    tight_exits = [
        ("exit_rules.take_profit_pct", 2.0),
        ("exit_rules.take_profit_pct_clamp", [1.5, 3.0]),
        ("exit_rules.stop_loss_pct", -1.5),
        ("exit_rules.stop_loss_pct_clamp", [-1.0, -2.0]),
        ("exit_rules.trailing_activation_pct", 1.2),
        ("exit_rules.trailing_sl_pct", 0.8),
        ("exit_rules.stagnation_hours", 2),
        ("exit_rules.stagnation_threshold_pct", 0.5),
        ("exit_rules.momentum_death_grace_hours", 3),
    ]
    degen_exits = [
        ("exit_rules.take_profit_pct", 1.5),
        ("exit_rules.take_profit_pct_clamp", [1.0, 2.0]),
        ("exit_rules.stop_loss_pct", -1.0),
        ("exit_rules.stop_loss_pct_clamp", [-0.5, -1.5]),
        ("exit_rules.trailing_activation_pct", 0.8),
        ("exit_rules.trailing_sl_pct", 0.5),
        ("exit_rules.stagnation_hours", 1),
        ("exit_rules.stagnation_threshold_pct", 0.3),
        ("exit_rules.momentum_death_grace_hours", 2),
    ]

    def with_overrides(base, overrides):
        return base + overrides

    experiments = {
        "swing_baseline": current_swing,
        # Size-only variants (same swing exits, bigger positions)
        "swing_sizeup": with_overrides(current_swing, size_up),
        "swing_degen_size": with_overrides(current_swing, size_degen),
        # Tight exits only (same size)
        "tight_exits": with_overrides(current_swing, tight_exits),
        "degen_exits": with_overrides(current_swing, degen_exits),
        # Scalp: tight exits + bigger size (the thesis)
        "scalp_tight": with_overrides(current_swing, tight_exits + size_up),
        "scalp_degen": with_overrides(current_swing, degen_exits + size_degen),
        # Scalp + more positions
        "scalp_tight_maxpos5": with_overrides(current_swing, tight_exits + size_up + [
            ("position_sizing.max_positions", 5),
        ]),
        "scalp_degen_maxpos5": with_overrides(current_swing, degen_exits + size_degen + [
            ("position_sizing.max_positions", 5),
        ]),
        # Balanced scalp
        "scalp_balanced": with_overrides(current_swing, [
            ("exit_rules.take_profit_pct", 2.5),
            ("exit_rules.take_profit_pct_clamp", [2.0, 3.5]),
            ("exit_rules.stop_loss_pct", -1.5),
            ("exit_rules.stop_loss_pct_clamp", [-1.0, -2.0]),
            ("exit_rules.trailing_activation_pct", 1.5),
            ("exit_rules.trailing_sl_pct", 1.0),
            ("exit_rules.stagnation_hours", 2),
            ("exit_rules.stagnation_threshold_pct", 0.5),
            ("position_sizing.normal_sizing_min_pct", 20),
            ("position_sizing.normal_sizing_max_pct", 30),
        ]),
        "scalp_balanced_maxpos5": with_overrides(current_swing, [
            ("exit_rules.take_profit_pct", 2.5),
            ("exit_rules.take_profit_pct_clamp", [2.0, 3.5]),
            ("exit_rules.stop_loss_pct", -1.5),
            ("exit_rules.stop_loss_pct_clamp", [-1.0, -2.0]),
            ("exit_rules.trailing_activation_pct", 1.5),
            ("exit_rules.trailing_sl_pct", 1.0),
            ("exit_rules.stagnation_hours", 2),
            ("exit_rules.stagnation_threshold_pct", 0.5),
            ("position_sizing.normal_sizing_min_pct", 20),
            ("position_sizing.normal_sizing_max_pct", 30),
            ("position_sizing.max_positions", 5),
        ]),
    }
    return experiments


def main():
    parser = argparse.ArgumentParser(description="CryptoRunner scalp sweep")
    parser.add_argument("--symbols", type=str, default="",
                         help=f"Comma-separated crypto symbols. Default: {DEFAULT_SYMBOLS}")
    parser.add_argument("--interval", type=str, default="1h",
                         help="Bar interval (1h recommended for scalp, 4h for swing comparison)")
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--goal-target", type=float, default=100.0)
    parser.add_argument("--goal-max-loss", type=float, default=200.0)
    parser.add_argument("--sort-by", type=str, default="avg_daily_pnl")
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    provider = CachedProvider(YFinanceProvider())

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = DEFAULT_SYMBOLS

    base_params = effective_params("CryptoRunner", "crypto_swing")

    if not args.start and not args.end:
        lookback = _MAX_LOOKBACK_DAYS.get(args.interval, 730)
        print(f"No --start/--end given; yfinance will clamp to last ~{lookback}d "
              f"for interval={args.interval}")

    experiments = build_scalp_experiments()
    total = len(experiments)
    print(f"Running {total} crypto scalp experiments on {symbols} @ {args.interval} "
          f"[provider=yfinance] (${args.capital:,.0f}, "
          f"goal=${args.goal_target}/day-style target, max_loss=${args.goal_max_loss})\n")

    results = {}
    for i, (name, overrides) in enumerate(experiments.items(), 1):
        params = copy.deepcopy(base_params)
        deep_set_many(params, overrides)
        t0 = time.time()
        try:
            r = run_single(params, symbols, args.start, args.end, args.capital,
                            args.interval, goal_target=args.goal_target,
                            goal_max_loss=args.goal_max_loss, provider=provider)
        except Exception as exc:
            print(f"  [{i}/{total}] {name:<25} FAILED: {exc}")
            continue
        elapsed = time.time() - t0
        s = extract_summary(r)
        results[name] = s
        print(f"  [{i}/{total}] {name:<25} avg/day=${s['avg_daily_pnl']:>+8.2f} "
              f"trades={s['trades']:>4} winrate={s['win_rate']:.1%} ({elapsed:.1f}s)")

    if not results:
        print("\nNo successful experiments — check data availability / errors above.")
        return

    print_table(results, sort_by=args.sort_by)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results saved to: {args.json}")


if __name__ == "__main__":
    main()
