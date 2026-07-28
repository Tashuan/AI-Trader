#!/usr/bin/env python3
"""
Backtest CLI — Run strategy backtests from the command line.

Usage:
    python run_backtest.py --agent blitztrader --start 2025-01-01 --end 2025-06-30
    python run_backtest.py --agent chartmaster --symbols BTC,ETH --start 2025-01-01
    python run_backtest.py --agent fademaster --start 2025-01-01 --json report.json
    python run_backtest.py --list
"""

import sys
import os
import argparse
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from personality import PERSONALITIES, list_personalities
from strategy_news import NewsHoundAgent
from strategy_technical import ChartMasterAgent
from strategy_contrarian import FadeMasterAgent
from strategy_momentum import BlitzTraderAgent
from backtester import Backtester

AGENT_CLASSES = {
    "newshound": NewsHoundAgent,
    "chartmaster": ChartMasterAgent,
    "fademaster": FadeMasterAgent,
    "blitztrader": BlitzTraderAgent,
}


def print_report(report_dict: dict) -> None:
    """Print a human-readable summary of the backtest report."""
    r = report_dict
    print(f"\n{'='*60}")
    print(f"  Backtest Report: {r['agent_name']}")
    print(f"{'='*60}")
    print(f"  Period:          {r['start_date']} → {r['end_date']}")
    print(f"  Symbols:         {', '.join(r['symbols'])}")
    print(f"  Initial Capital: ${r['initial_capital']:,.2f}")
    print(f"  Final Equity:    ${r['final_equity']:,.2f}")
    print(f"  Total Return:    {r['total_return_pct']:+.2f}%")
    print(f"  Sharpe Ratio:    {r['sharpe_ratio']:.3f}")
    print(f"  Max Drawdown:    {r['max_drawdown_pct']:.2f}%")
    print(f"  Win Rate:        {r['win_rate']:.1%} ({r['winning_trades']}/{r['total_trades']})")
    print(f"  Profit Factor:   {r['profit_factor']:.3f}")
    print(f"  Avg Hold Days:   {r['avg_hold_days']:.1f}")
    print(f"  Total Trades:    {r['total_trades']}")

    if r.get("per_symbol_stats"):
        print(f"\n  Per-Symbol Breakdown:")
        print(f"  {'Symbol':<10} {'Trades':>7} {'Wins':>5} {'Win Rate':>10} {'Total PnL':>12} {'Avg PnL%':>10}")
        print(f"  {'-'*10} {'-'*7} {'-'*5} {'-'*10} {'-'*12} {'-'*10}")
        for sym, stats in r["per_symbol_stats"].items():
            print(f"  {sym:<10} {stats['trades']:>7} {stats['wins']:>5} {stats['win_rate']:>10.1%} {stats['total_pnl']:>12.2f} {stats['avg_pnl_pct']:>10.2f}")

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Run AI-Trader strategy backtests")
    parser.add_argument("agent", nargs="?", help="Agent key to backtest (e.g. blitztrader)")
    parser.add_argument("--list", action="store_true", help="List available agent strategies")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols (default: agent watchlist)")
    parser.add_argument("--start", type=str, default="", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital (default: $100,000)")
    parser.add_argument("--json", type=str, default="", help="Save full report as JSON to this path")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable strategies for backtesting:")
        for key, info in list_personalities().items():
            if key in AGENT_CLASSES:
                print(f"  {key:15s} — {info['name']}: {info['tagline']} [{info['strategy']}]")
        return

    if not args.agent:
        parser.error("agent key is required (or use --list)")

    if args.agent not in PERSONALITIES:
        print(f"Unknown agent: {args.agent}")
        print(f"Available: {', '.join(k for k in PERSONALITIES if k in AGENT_CLASSES)}")
        return

    if args.agent not in AGENT_CLASSES:
        print(f"Agent '{args.agent}' does not have a strategy implementation for backtesting")
        return

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None

    personality = PERSONALITIES[args.agent]
    agent_class = AGENT_CLASSES[args.agent]

    print(f"\nRunning backtest: {personality.name} ({args.agent})")
    print(f"  Symbols: {', '.join(symbols) if symbols else ', '.join(personality.watchlist)}")
    print(f"  Period:  {args.start or '2y'} → {args.end or 'now'}")
    print(f"  Capital: ${args.capital:,.2f}")
    print()

    bt = Backtester(
        agent_class=agent_class,
        personality=personality,
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
    )

    report = bt.run()
    report_dict = report.to_dict()

    print_report(report_dict)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report_dict, f, indent=2)
        print(f"Full report saved to: {args.json}")


if __name__ == "__main__":
    main()
