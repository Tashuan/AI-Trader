#!/usr/bin/env python3
"""Run the standalone premarket candidate scanner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from premarket_scanner import DEFAULT_CONFIG, scan
from strategy_lab import load_json_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an AI-ready premarket watchlist")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config" / "premarket_scanner.json"))
    parser.add_argument("--symbols", default="", help="Optional comma-separated symbol override")
    parser.add_argument("--token", default="", help="Platform token for news enrichment")
    parser.add_argument("--json", default="", help="Write full watchlist JSON to this path")
    args = parser.parse_args()
    config = load_json_config(args.config, DEFAULT_CONFIG)
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()] or None
    result = scan(config, symbols=symbols, token=args.token or None)
    print(f"Premarket candidates: {result['candidate_count']} | monitor: {result['monitor_count']}")
    for index, candidate in enumerate(result["watchlist"], 1):
        print(f"{index:>2}. {candidate['symbol']:<6} {candidate['direction']:<5} "
              f"score={candidate['score']:>5.1f} status={candidate['status']:<7} "
              f"move={candidate['change_pct']:+.2f}% trend={candidate['trend']}")
        if candidate["evidence"]:
            print(f"    evidence: {'; '.join(candidate['evidence'])}")
        if candidate["risks"]:
            print(f"    risks:    {'; '.join(candidate['risks'])}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, default=str)
        print(f"Saved watchlist: {args.json}")


if __name__ == "__main__":
    main()
