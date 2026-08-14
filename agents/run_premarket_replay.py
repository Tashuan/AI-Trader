#!/usr/bin/env python3
"""Run the premarket scanner on a historical premarket session.

Replays a past trading day's premarket data through the scanner using
yfinance extended-hours bars (67 bars/day at 5m, 60 days back) plus
Finnhub company news, producing the same ranked watchlist the scanner
would have produced live that morning.

Usage:
    python agents/run_premarket_replay.py --date 2026-08-11
    python agents/run_premarket_replay.py --date 2026-08-11 --json /tmp/replay.json
    python agents/run_premarket_replay.py --date 2026-08-11 --symbols NVDA,TSLA,AMD
    python agents/run_premarket_replay.py --date 2026-08-11 --no-news
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from premarket_replay import PremarketReplayProvider
from premarket_scanner import DEFAULT_CONFIG, scan
from strategy_lab import load_json_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the premarket scanner on a historical premarket session"
    )
    parser.add_argument("--date", required=True, help="Target trading date (YYYY-MM-DD)")
    parser.add_argument(
        "--config", default=str(Path(__file__).parent / "config" / "premarket_scanner.json"),
        help="Scanner config JSON path",
    )
    parser.add_argument(
        "--symbols", default="",
        help="Optional comma-separated symbol override",
    )
    parser.add_argument(
        "--interval", default="5m",
        help="Intraday bar interval (1m=7 days back, 5m=60 days back, 15m=60 days back)",
    )
    parser.add_argument(
        "--no-news", action="store_true",
        help="Skip Finnhub news enrichment",
    )
    parser.add_argument(
        "--json", default="",
        help="Write full watchlist JSON to this path",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Print per-symbol premarket summary before the watchlist",
    )
    args = parser.parse_args()

    config = load_json_config(args.config, DEFAULT_CONFIG)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None

    try:
        provider = PremarketReplayProvider(args.date, interval=args.interval)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Pre-populate caches for the universe so mover_fetcher has data.
    universe = symbols or config["universe"]
    provider.prepare(universe, period=config["history_period"])

    if args.summary:
        print(f"\n{'='*70}")
        print(f"  Premarket Replay — {args.date}")
        print(f"{'='*70}\n")
        print(f"{'Symbol':<8} {'PrevClose':>10} {'PMClose':>10} {'Change':>8} "
              f"{'PMVol':>12} {'PMRV':>7} {'Bars':>6}")
        print("-" * 80)
        for symbol in universe:
            s = provider.premarket_summary(symbol)
            if not s:
                continue
            quote = provider.quote(symbol) or {}
            print(f"{s['symbol']:<8} {s['prev_close']:>10.2f} {s['pm_close']:>10.2f} "
                  f"{s['change_pct']:>+7.2f}% {s['pm_volume']:>12,} "
                  f"{quote.get('premarket_relative_volume', 0):>6.2f}x {s['bar_count']:>6}")
        print()

    result = scan(
        config,
        provider=provider,
        symbols=symbols,
        mover_fetcher=provider.mover_fetcher,
        news_fetcher=None if args.no_news else provider.news_fetcher,
    )

    print(f"Premarket replay: {args.date} | "
          f"candidates: {result['candidate_count']} | "
          f"monitor: {result['monitor_count']}")
    print()
    for i, c in enumerate(result["watchlist"], 1):
        print(f"{i:>2}. {c['symbol']:<6} {c['direction']:<5} "
              f"score={c['score']:>5.1f} status={c['status']:<7} "
              f"move={c['change_pct']:+.2f}% trend={c['trend']}")
        if c["evidence"]:
            print(f"    evidence: {'; '.join(c['evidence'])}")
        if c["risks"]:
            print(f"    risks:    {'; '.join(c['risks'])}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nSaved watchlist: {args.json}")


if __name__ == "__main__":
    main()
