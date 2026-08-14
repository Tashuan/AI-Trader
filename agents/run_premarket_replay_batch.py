#!/usr/bin/env python3
"""Evaluate the premarket scanner across several historical sessions."""
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


def _parse_dates(value: str) -> list[str]:
    dates = [item.strip() for item in value.split(",") if item.strip()]
    if not dates:
        raise argparse.ArgumentTypeError("at least one date is required")
    return dates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the premarket scanner across historical sessions"
    )
    parser.add_argument(
        "--dates", required=True, type=_parse_dates,
        help="Comma-separated trading dates (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--config", default=str(Path(__file__).parent / "config" / "premarket_scanner.json"),
        help="Scanner config JSON path",
    )
    parser.add_argument("--symbols", default="", help="Optional comma-separated symbol override")
    parser.add_argument(
        "--interval", default="5m",
        help="Premarket interval (5m supports 60 days of history)",
    )
    parser.add_argument("--no-news", action="store_true", help="Skip Finnhub news enrichment")
    parser.add_argument("--json", default="", help="Write batch results to this path")
    args = parser.parse_args()

    config = load_json_config(args.config, DEFAULT_CONFIG)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    universe = symbols or config["universe"]
    results: list[dict] = []

    for date in args.dates:
        print(f"Running {date}...", flush=True)
        try:
            provider = PremarketReplayProvider(date, interval=args.interval)
            provider.prepare(universe, period=config["history_period"])
            result = scan(
                config,
                provider=provider,
                symbols=symbols,
                mover_fetcher=provider.mover_fetcher,
                news_fetcher=None if args.no_news else provider.news_fetcher,
            )
        except Exception as exc:
            print(f"ERROR {date}: {exc}", file=sys.stderr)
            continue

        watchlist = result["watchlist"]
        row = {
            "date": date,
            "candidate_count": result["candidate_count"],
            "monitor_count": result["monitor_count"],
            "watchlist": [
                {
                    "rank": index,
                    "symbol": candidate["symbol"],
                    "direction": candidate["direction"],
                    "score": candidate["score"],
                    "status": candidate["status"],
                    "change_pct": candidate["change_pct"],
                    "relative_volume": candidate["relative_volume"],
                    "spread_pct": candidate["spread_pct"],
                }
                for index, candidate in enumerate(watchlist, 1)
            ],
        }
        results.append(row)

        print(
            f"  candidates={row['candidate_count']} monitor={row['monitor_count']} | "
            + ", ".join(
                f"{item['symbol']} {item['direction']} {item['change_pct']:+.2f}% "
                f"rv={item['relative_volume']:.2f}x ({item['status']})"
                for item in row["watchlist"][:5]
            )
        )

    counts: dict[str, int] = {}
    for result in results:
        for candidate in result["watchlist"]:
            counts[candidate["symbol"]] = counts.get(candidate["symbol"], 0) + 1
    recurring = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    output = {
        "dates": args.dates,
        "results": results,
        "recurring_symbols": [
            {"symbol": symbol, "appearances": appearances}
            for symbol, appearances in recurring
        ],
        "limitations": [
            "Premarket bars come from yfinance extended-hours data; historical NBBO bid/ask is unavailable.",
            "spread_pct is a candle-range liquidity proxy, not a historical quote spread.",
            "Scanner rankings are not trade outcomes or proof of profitability.",
        ],
    }

    print("\nRecurring symbols:")
    for symbol, appearances in recurring:
        print(f"  {symbol}: {appearances}/{len(results)} sessions")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2)
        print(f"\nSaved batch results: {args.json}")


if __name__ == "__main__":
    main()
