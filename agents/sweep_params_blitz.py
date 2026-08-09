#!/usr/bin/env python3
"""Parameter + timeframe sweep for BlitzRunner — aggressive scalp tuning.

Goal: find a BlitzRunner (equity momentum_scalp) config that maximizes
average $/day on a small paper account, in the style of a fast momentum
scalper (minutes-scale holds, quick 2% pops, cut losers immediately).

Mirrors the CryptoRunner sweep workflow (sweep_params.py /
timeframe_comparison.py) but targets ScanBacktester + scan_core params
and a high-beta equity watchlist instead of crypto.

Usage:
    python3 sweep_params_blitz.py
    python3 sweep_params_blitz.py --interval 5m --symbols NVDA,TSLA,AMD,COIN
    python3 sweep_params_blitz.py --goal-target 100 --goal-max-loss 200 --capital 10000
    python3 sweep_params_blitz.py --mode timeframe
"""

import sys
import os
import copy
import json
import argparse
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_registry import effective_params
from scan_backtester import ScanBacktester
from market_data import YFinanceProvider
from equity_data_providers import AlpacaProvider
from data_cache import CachedProvider

# yfinance intraday lookback ceilings — keep test windows inside these or
# the provider silently truncates the range. Crypto providers (below) have
# multi-year intraday history, so these only apply to the yfinance provider.
_MAX_LOOKBACK_DAYS = {
    "1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1h": 730,
}

_PROVIDERS = {
    "yfinance": YFinanceProvider,
    "alpaca": AlpacaProvider,
}

# High-beta, high-volume momentum equities — the kind of tickers that
# make quick 2% moves on volume spikes, good scalp candidates.
DEFAULT_SYMBOLS = [
    # mega-cap tech / mag-7 movers
    "NVDA", "TSLA", "AMD", "META", "AAPL", "AMZN", "MSFT", "GOOGL", "NFLX",
    # semis / AI infra
    "AVGO", "MU", "ARM", "SMCI", "DELL",
    # crypto-proxies (equities that track crypto, but trade on equity hours)
    "COIN", "MSTR", "MARA", "RIOT", "CLSK", "HUT",
    # high-beta momentum / meme-adjacent
    "PLTR", "SOFI", "AFRM", "UPST", "RIVN", "LCID", "GME", "CVNA", "RKLB",
    # leveraged ETFs (amplified intraday moves, good scalp vehicles)
    "SOXL", "TQQQ", "SPXL",
]


def deep_set(d, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def deep_set_many(d, overrides):
    for path, value in overrides:
        deep_set(d, path, value)


def run_single(params, symbols, start, end, capital, interval, slippage_bps=5.0,
                goal_target=None, goal_max_loss=None, provider=None):
    bt = ScanBacktester(
        symbols, params, start, end, capital, interval, slippage_bps,
        goal_target=goal_target, goal_max_loss=goal_max_loss,
        provider=provider,
    )
    return bt.run().to_dict()


def _daily_pnl_stats(report: dict) -> dict:
    """Bucket closed trades by exit calendar day → avg/best/worst $ per day."""
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


def build_param_experiments(base_params: dict) -> dict:
    """Single-variable sweeps around the aggressive scalp baseline."""
    experiments = {}
    experiments["baseline"] = []

    for sl in [-0.5, -0.8, -1.0, -1.5, -2.0]:
        experiments[f"sl_{sl}pct"] = [("exit_rules.stop_loss_pct", sl)]

    for tp in [0.8, 1.0, 1.5, 2.0, 3.0]:
        experiments[f"tp_{tp}pct"] = [("exit_rules.take_profit_pct", tp)]

    for sc in [2, 3, 4, 6, 8]:
        experiments[f"stagnation_{sc}cyc"] = [("exit_rules.stagnation_cycles", sc)]

    trail_combos = [(0.6, 0.4), (0.8, 0.5), (1.0, 0.6), (1.2, 0.8), (1.5, 1.0)]
    for act, tsl in trail_combos:
        experiments[f"trail_{act}_{tsl}"] = [
            ("exit_rules.trailing_activation_pct", act),
            ("exit_rules.trailing_sl_pct", tsl),
        ]

    for ms in [2, 3, 4, 5, 6]:
        experiments[f"minsig_{ms}"] = [("entry_criteria.min_signals", ms)]

    for vr in [1.1, 1.3, 1.5, 1.8, 2.2]:
        experiments[f"volratio_{vr}"] = [("entry_criteria.min_vol_ratio", vr)]

    for mp in [1, 3, 5, 8]:
        experiments[f"maxpos_{mp}"] = [
            ("position_sizing.max_positions", mp),
            ("risk_controls.max_positions", mp),
        ]

    for risk in [0.5, 1.0, 1.5, 2.5]:
        experiments[f"risk_{risk}pct"] = [("risk_controls.risk_per_trade_pct", risk)]

    return experiments


def build_timeframe_experiments() -> list:
    """(name, interval, overrides) — scalp posture across intraday timeframes."""
    aggressive_scalp = [
        ("exit_rules.stop_loss_pct", -1.0),
        ("exit_rules.take_profit_pct", 1.5),
        ("exit_rules.stagnation_cycles", 4),
        ("exit_rules.trailing_activation_pct", 1.0),
        ("exit_rules.trailing_sl_pct", 0.6),
        ("exit_rules.momentum_death_vol_ratio", 0.7),
        ("exit_rules.momentum_death_grace_bars", 3),
        ("entry_criteria.min_signals", 3),
        ("entry_criteria.min_vol_ratio", 1.3),
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 25),
        ("position_sizing.normal_sizing_max_pct", 40),
        ("risk_controls.max_positions", 5),
        ("risk_controls.risk_per_trade_pct", 1.0),
        ("risk_controls.max_trade_notional_pct", 35.0),
    ]
    degen_scalp = [
        ("exit_rules.stop_loss_pct", -0.7),
        ("exit_rules.take_profit_pct", 1.2),
        ("exit_rules.stagnation_cycles", 3),
        ("exit_rules.trailing_activation_pct", 0.8),
        ("exit_rules.trailing_sl_pct", 0.5),
        ("exit_rules.momentum_death_vol_ratio", 0.8),
        ("exit_rules.momentum_death_grace_bars", 2),
        ("entry_criteria.min_signals", 2),
        ("entry_criteria.min_vol_ratio", 1.1),
        ("position_sizing.max_positions", 8),
        ("position_sizing.normal_sizing_min_pct", 30),
        ("position_sizing.normal_sizing_max_pct", 45),
        ("risk_controls.max_positions", 8),
        ("risk_controls.risk_per_trade_pct", 1.5),
        ("risk_controls.max_trade_notional_pct", 40.0),
    ]
    # Loosened entry gate on top of the aggressive exits — more qualifying
    # setups per day so results aren't just 2-3 lucky trades over 60 days.
    frequent_scalp = aggressive_scalp + [
        ("entry_criteria.min_signals", 2),
        ("entry_criteria.min_signal_families", 1),
        ("entry_criteria.min_vol_ratio", 1.1),
    ]

    return [
        ("5m_standard", "5m", []),
        ("5m_aggressive", "5m", aggressive_scalp),
        ("5m_degen", "5m", degen_scalp),
        ("5m_frequent", "5m", frequent_scalp),
        ("15m_standard", "15m", []),
        ("15m_aggressive", "15m", aggressive_scalp),
        ("15m_frequent", "15m", frequent_scalp),
        ("30m_aggressive", "30m", aggressive_scalp),
        ("30m_frequent", "30m", frequent_scalp),
    ]


def build_combined_experiments() -> dict:
    """Combine the 30m winner (aggressive exits + max_positions) with
    scan_core entry-gate loosening (the OBV-divergence veto is a hard,
    unconditional block regardless of signal count — now configurable via
    entry_criteria.block_on_obv_divergence).
    """
    aggressive_exits = [
        ("exit_rules.stop_loss_pct", -1.0),
        ("exit_rules.take_profit_pct", 1.5),
        ("exit_rules.stagnation_cycles", 4),
        ("exit_rules.trailing_activation_pct", 1.0),
        ("exit_rules.trailing_sl_pct", 0.6),
        ("exit_rules.momentum_death_vol_ratio", 0.7),
        ("exit_rules.momentum_death_grace_bars", 3),
        ("entry_criteria.min_signals", 3),
        ("entry_criteria.min_vol_ratio", 1.3),
        ("position_sizing.normal_sizing_min_pct", 25),
        ("position_sizing.normal_sizing_max_pct", 40),
        ("risk_controls.risk_per_trade_pct", 1.0),
        ("risk_controls.max_trade_notional_pct", 35.0),
    ]

    def with_maxpos(mp):
        return aggressive_exits + [
            ("position_sizing.max_positions", mp),
            ("risk_controls.max_positions", mp),
        ]

    def with_maxpos_and_gate(mp, min_signals=None, min_vol=None, min_families=None, obv_off=False):
        exp = with_maxpos(mp)
        if min_signals is not None:
            exp = exp + [("entry_criteria.min_signals", min_signals)]
        if min_vol is not None:
            exp = exp + [("entry_criteria.min_vol_ratio", min_vol)]
        if min_families is not None:
            exp = exp + [("entry_criteria.min_signal_families", min_families)]
        if obv_off:
            exp = exp + [("entry_criteria.block_on_obv_divergence", False)]
        return exp

    experiments = {
        "30m_aggr_maxpos5": with_maxpos(5),
        "30m_aggr_maxpos8": with_maxpos(8),
        "30m_aggr_maxpos10": with_maxpos(10),
        "30m_aggr_maxpos5_obvoff": with_maxpos_and_gate(5, obv_off=True),
        "30m_aggr_maxpos8_obvoff": with_maxpos_and_gate(8, obv_off=True),
        "30m_loosegate_maxpos5": with_maxpos_and_gate(
            5, min_signals=2, min_vol=1.1, min_families=1, obv_off=True),
        "30m_loosegate_maxpos8": with_maxpos_and_gate(
            8, min_signals=2, min_vol=1.1, min_families=1, obv_off=True),
        "30m_loosegate_maxpos10": with_maxpos_and_gate(
            10, min_signals=2, min_vol=1.1, min_families=1, obv_off=True),
    }
    return experiments


def build_scalp_experiments() -> dict:
    """Scalp posture: tight TP/SL, bigger position size, more slots.

    Baseline is the current 30m winner (aggr_maxpos5). Each variant tightens
    exits and/or increases position size to test 'small moves, big size' vs
    the swing-style 'big moves, small size' approach.
    """
    # Current winner — the benchmark to beat
    current_winner = [
        ("exit_rules.stop_loss_pct", -1.0),
        ("exit_rules.take_profit_pct", 1.5),
        ("exit_rules.stagnation_cycles", 4),
        ("exit_rules.trailing_activation_pct", 1.0),
        ("exit_rules.trailing_sl_pct", 0.6),
        ("exit_rules.momentum_death_vol_ratio", 0.7),
        ("exit_rules.momentum_death_grace_bars", 3),
        ("entry_criteria.min_signals", 3),
        ("entry_criteria.min_vol_ratio", 1.3),
        ("position_sizing.max_positions", 5),
        ("position_sizing.normal_sizing_min_pct", 25),
        ("position_sizing.normal_sizing_max_pct", 40),
        ("risk_controls.max_positions", 5),
        ("risk_controls.risk_per_trade_pct", 1.0),
        ("risk_controls.max_trade_notional_pct", 35.0),
    ]

    def with_overrides(base, overrides):
        return base + overrides

    # Position size scaling — same exits, just bigger size
    size_up = [
        ("position_sizing.normal_sizing_min_pct", 35),
        ("position_sizing.normal_sizing_max_pct", 50),
        ("risk_controls.max_trade_notional_pct", 45.0),
    ]
    size_degen = [
        ("position_sizing.normal_sizing_min_pct", 45),
        ("position_sizing.normal_sizing_max_pct", 60),
        ("risk_controls.max_trade_notional_pct", 55.0),
    ]

    # Tighter exits — capture smaller moves
    tight_exits = [
        ("exit_rules.take_profit_pct", 1.0),
        ("exit_rules.stop_loss_pct", -0.8),
        ("exit_rules.trailing_activation_pct", 0.8),
        ("exit_rules.trailing_sl_pct", 0.5),
        ("exit_rules.stagnation_cycles", 3),
    ]
    degen_exits = [
        ("exit_rules.take_profit_pct", 0.8),
        ("exit_rules.stop_loss_pct", -0.5),
        ("exit_rules.trailing_activation_pct", 0.6),
        ("exit_rules.trailing_sl_pct", 0.4),
        ("exit_rules.stagnation_cycles", 2),
    ]

    experiments = {
        "current_winner_maxpos5": current_winner,
        # Size-only variants (same exits, bigger positions)
        "sizeup_maxpos5": with_overrides(current_winner, size_up),
        "degen_size_maxpos5": with_overrides(current_winner, size_degen),
        # Tight exits only (same size)
        "tight_exits_maxpos5": with_overrides(current_winner, tight_exits),
        "degen_exits_maxpos5": with_overrides(current_winner, degen_exits),
        # Tight exits + bigger size (the scalp thesis)
        "scalp_tight_maxpos5": with_overrides(current_winner, tight_exits + size_up),
        "scalp_degen_maxpos5": with_overrides(current_winner, degen_exits + size_degen),
        # Same configs but with maxpos=8 (more concurrent scalp slots)
        "scalp_tight_maxpos8": with_overrides(current_winner, tight_exits + size_up + [
            ("position_sizing.max_positions", 8),
            ("risk_controls.max_positions", 8),
        ]),
        "scalp_degen_maxpos8": with_overrides(current_winner, degen_exits + size_degen + [
            ("position_sizing.max_positions", 8),
            ("risk_controls.max_positions", 8),
        ]),
        # Balanced scalp: moderate tightness + moderate size
        "scalp_balanced_maxpos5": with_overrides(current_winner, [
            ("exit_rules.take_profit_pct", 1.2),
            ("exit_rules.stop_loss_pct", -0.8),
            ("exit_rules.trailing_activation_pct", 0.8),
            ("exit_rules.trailing_sl_pct", 0.5),
            ("exit_rules.stagnation_cycles", 3),
            ("position_sizing.normal_sizing_min_pct", 30),
            ("position_sizing.normal_sizing_max_pct", 45),
            ("risk_controls.max_trade_notional_pct", 40.0),
        ]),
        "scalp_balanced_maxpos8": with_overrides(current_winner, [
            ("exit_rules.take_profit_pct", 1.2),
            ("exit_rules.stop_loss_pct", -0.8),
            ("exit_rules.trailing_activation_pct", 0.8),
            ("exit_rules.trailing_sl_pct", 0.5),
            ("exit_rules.stagnation_cycles", 3),
            ("position_sizing.normal_sizing_min_pct", 30),
            ("position_sizing.normal_sizing_max_pct", 45),
            ("risk_controls.max_trade_notional_pct", 40.0),
            ("position_sizing.max_positions", 8),
            ("risk_controls.max_positions", 8),
        ]),
    }
    return experiments


def main():
    parser = argparse.ArgumentParser(description="BlitzRunner scalp sweep")
    parser.add_argument("--mode", choices=["params", "timeframe", "combined", "scalp"], default="params",
                         help="params = sweep entry/exit params at one interval; "
                              "timeframe = compare 1m/5m/15m/30m scalp postures; "
                              "combined = 30m aggressive exits x max_positions x scan_core gate loosening; "
                              "scalp = tight TP/SL + bigger position size variants vs current winner")
    parser.add_argument("--provider", choices=list(_PROVIDERS.keys()), default="yfinance",
                         help="Data provider. yfinance = equities (60d intraday cap); "
                              "alpaca = equities, multi-year intraday (needs API keys).")
    parser.add_argument("--symbols", type=str, default="",
                         help=f"Comma-separated equity symbols. Default: high-beta basket.")
    parser.add_argument("--interval", type=str, default="5m", help="Only used in --mode params")
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--goal-target", type=float, default=100.0)
    parser.add_argument("--goal-max-loss", type=float, default=200.0)
    parser.add_argument("--sort-by", type=str, default="avg_daily_pnl")
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    provider = CachedProvider(_PROVIDERS[args.provider]())

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = DEFAULT_SYMBOLS

    base_params = effective_params("BlitzRunner", "momentum_scalp")

    if not args.start and not args.end:
        if args.provider == "alpaca":
            print(f"No --start/--end given; Alpaca will pull a long default window.")
        else:
            lookback = _MAX_LOOKBACK_DAYS.get(args.interval, 60)
            print(f"No --start/--end given; yfinance will clamp to last ~{lookback}d "
                  f"for interval={args.interval}")

    results = {}

    if args.mode == "params":
        experiments = build_param_experiments(base_params)
        total = len(experiments)
        print(f"Running {total} param experiments on {symbols} @ {args.interval} "
              f"[provider={args.provider}] (${args.capital:,.0f}, "
              f"goal=${args.goal_target}/day-style target, max_loss=${args.goal_max_loss})\n")
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
    elif args.mode == "timeframe":
        experiments = build_timeframe_experiments()
        total = len(experiments)
        print(f"Running {total} timeframe experiments on {symbols} "
              f"[provider={args.provider}] (${args.capital:,.0f}, "
              f"goal=${args.goal_target}/day-style target, max_loss=${args.goal_max_loss})\n")
        for i, (name, interval, overrides) in enumerate(experiments, 1):
            params = copy.deepcopy(base_params)
            deep_set_many(params, overrides)
            t0 = time.time()
            try:
                r = run_single(params, symbols, args.start, args.end, args.capital,
                                interval, goal_target=args.goal_target,
                                goal_max_loss=args.goal_max_loss, provider=provider)
            except Exception as exc:
                print(f"  [{i}/{total}] {name:<25} FAILED: {exc}")
                continue
            elapsed = time.time() - t0
            s = extract_summary(r)
            results[name] = s
            print(f"  [{i}/{total}] {name:<25} {interval:<5} avg/day=${s['avg_daily_pnl']:>+8.2f} "
                  f"trades={s['trades']:>4} winrate={s['win_rate']:.1%} ({elapsed:.1f}s)")
    elif args.mode == "scalp":
        experiments = build_scalp_experiments()
        total = len(experiments)
        interval = args.interval or "30m"
        print(f"Running {total} scalp experiments on {symbols} @ {interval} "
              f"[provider={args.provider}] (${args.capital:,.0f}, "
              f"goal=${args.goal_target}/day-style target, max_loss=${args.goal_max_loss})\n")
        for i, (name, overrides) in enumerate(experiments.items(), 1):
            params = copy.deepcopy(base_params)
            deep_set_many(params, overrides)
            t0 = time.time()
            try:
                r = run_single(params, symbols, args.start, args.end, args.capital,
                                interval, goal_target=args.goal_target,
                                goal_max_loss=args.goal_max_loss, provider=provider)
            except Exception as exc:
                print(f"  [{i}/{total}] {name:<25} FAILED: {exc}")
                continue
            elapsed = time.time() - t0
            s = extract_summary(r)
            results[name] = s
            print(f"  [{i}/{total}] {name:<25} avg/day=${s['avg_daily_pnl']:>+8.2f} "
                  f"trades={s['trades']:>4} winrate={s['win_rate']:.1%} ({elapsed:.1f}s)")
    else:  # combined
        experiments = build_combined_experiments()
        total = len(experiments)
        interval = args.interval or "30m"
        print(f"Running {total} combined experiments on {symbols} @ {interval} "
              f"[provider={args.provider}] (${args.capital:,.0f}, "
              f"goal=${args.goal_target}/day-style target, max_loss=${args.goal_max_loss})\n")
        for i, (name, overrides) in enumerate(experiments.items(), 1):
            params = copy.deepcopy(base_params)
            deep_set_many(params, overrides)
            t0 = time.time()
            try:
                r = run_single(params, symbols, args.start, args.end, args.capital,
                                interval, goal_target=args.goal_target,
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
