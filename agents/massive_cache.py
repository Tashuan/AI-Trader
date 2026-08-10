#!/usr/bin/env python3
"""Bulk-prefill the .data_cache/ directory with Massive OHLCV data.

Wraps MassiveProvider in CachedProvider so each fetch is saved to disk
as parquet — identical to the existing Alpaca/yfinance cache workflow.
Subsequent backtests load from disk instantly instead of hitting the API.

Usage:
    # Cache 5m bars for the default ScalpRunner watchlist, last 30 days
    python3 massive_cache.py --interval 5m --days 30

    # Cache specific symbols
    python3 massive_cache.py --symbols NVDA,TSLA,AAPL --interval 5m --days 60

    # Cache multiple intervals
    python3 massive_cache.py --interval 5m,15m,1d --days 90

    # Use a custom date range
    python3 massive_cache.py --symbols NVDA,AAPL --interval 5m --start 2026-01-01 --end 2026-08-09
"""

import argparse
import sys
import os
import time
from datetime import datetime, timedelta, timezone

_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

from massive_provider import MassiveProvider
from data_cache import CachedProvider

_DEFAULT_SYMBOLS = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM",
]


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-prefill .data_cache/ with Massive OHLCV data")
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated tickers (default: ScalpRunner watchlist)")
    parser.add_argument("--interval", type=str, default="5m",
                        help="Comma-separated intervals (e.g. 5m,15m,1d)")
    parser.add_argument("--days", type=int, default=30,
                        help="Number of days of history to fetch (default: 30)")
    parser.add_argument("--start", type=str, default="",
                        help="Start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", type=str, default="",
                        help="End date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else _DEFAULT_SYMBOLS)
    intervals = [i.strip() for i in args.interval.split(",") if i.strip()]

    if args.start:
        start = args.start
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    provider = MassiveProvider()
    if not provider.is_configured:
        print("ERROR: MASSIVE_API_KEY not set. Get one at https://massive.com/dashboard/keys")
        sys.exit(1)

    cached = CachedProvider(provider)

    total = len(symbols) * len(intervals)
    done = 0
    failed = 0

    print(f"Caching {total} datasets: {len(symbols)} symbols × {len(intervals)} intervals")
    print(f"Date range: {start} → {end}")
    print(f"Cache dir: {cached._cache_dir}")
    print()

    for interval in intervals:
        print(f"── {interval} ──")
        for sym in symbols:
            done += 1
            try:
                t0 = time.time()
                df = cached.history(sym, start=start, end=end,
                                    interval=interval, auto_adjust=False)
                elapsed = time.time() - t0
                if df is not None and not df.empty:
                    print(f"  [{done}/{total}] {sym} {interval}: {len(df)} bars "
                          f"({df.index[0].date()} → {df.index[-1].date()}) "
                          f"[{elapsed:.1f}s]")
                else:
                    print(f"  [{done}/{total}] {sym} {interval}: NO DATA [{elapsed:.1f}s]")
                    failed += 1
            except Exception as e:
                print(f"  [{done}/{total}] {sym} {interval}: FAILED — {e}")
                failed += 1
            # Gentle rate limiting
            time.sleep(0.2)
        print()

    print(f"Done: {done - failed}/{total} succeeded, {failed} failed")
    if failed > 0:
        print("Check that your Massive plan covers the requested intervals and date range.")
        sys.exit(1)


if __name__ == "__main__":
    main()
