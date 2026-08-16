"""Pre-fetch and cache option bars for the ORB options backtest.

Since the Alpaca options bars API requires the OPRA agreement to be signed,
this script pre-fetches all the option bars we need and caches them locally
as JSON files. The backtester then reads from the cache instead of making
API calls.

The cache structure:
  research/strategy_search/option_bars_cache/
    NVDA260821C00200000_2026-08-14.json
    NVDA260821P00225000_2026-08-14.json
    ...

Usage:
  cd agents
  python3 ../research/strategy_search/fetch_option_bars.py --symbols NVDA,TSLA --start 2026-06-15 --end 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "research" / "strategy_search"))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import pandas as pd
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from alpaca_options_provider import AlpacaOptionsProvider, build_occ_symbol
from scalp_alt_signals import fetch_1m_data, DEFAULT_SYMBOLS, DEFAULT_START, DEFAULT_END

# Strike steps per symbol
STRIKE_STEPS = {
    "NVDA": 2.5, "TSLA": 2.5, "AMD": 0.5, "AAPL": 2.5, "META": 2.5,
    "AMZN": 2.5, "MSFT": 2.5, "GOOGL": 2.5, "NFLX": 5.0, "INTC": 0.5,
}

CACHE_DIR = _PROJECT_ROOT / "research" / "strategy_search" / "option_bars_cache"


def find_expiration(date, dte_min: int = 1, dte_max: int = 14) -> str | None:
    """Find nearest Friday expiration within DTE range."""
    d = datetime.fromisoformat(str(date))
    days_to_friday = (4 - d.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    friday = d + timedelta(days=days_to_friday)
    dte = (friday - d).days
    if dte < dte_min:
        friday += timedelta(days=7)
        dte = (friday - d).days
    if dte > dte_max:
        return None
    return friday.strftime("%Y-%m-%d")


def generate_option_symbols(
    symbols: list[str], frames: dict[str, pd.DataFrame],
    dte_min: int = 1, dte_max: int = 14,
    strike_offsets: list[int] = (-1, 0, 1),
) -> dict[str, list[dict]]:
    """Generate all option symbols we'll need for the backtest.

    For each trading day and each symbol, we need:
      - ATM call + put (for long/short signals)
      - OTM and ITM strikes (for parameter sweep)

    Returns:
        Dict mapping option_symbol -> list of {date, underlying, strike, type, expiry}
    """
    needed: dict[str, list[dict]] = {}

    for sym in frames:
        df = frames[sym]
        strike_step = STRIKE_STEPS.get(sym, 2.5)
        dates = sorted(df["Timestamp"].dt.date.unique())

        for date in dates:
            day_df = df[df["Timestamp"].dt.date == date]
            if day_df.empty:
                continue
            # Get the opening price (first bar close) as spot reference
            spot = float(day_df.iloc[0]["Close"])
            atm_strike = round(spot / strike_step) * strike_step

            expiry = find_expiration(date, dte_min, dte_max)
            if expiry is None:
                continue

            for offset in strike_offsets:
                strike = atm_strike + offset * strike_step
                for opt_type in ("call", "put"):
                    occ = build_occ_symbol(sym, expiry, opt_type, strike)
                    if occ not in needed:
                        needed[occ] = []
                    needed[occ].append({
                        "date": str(date),
                        "underlying": sym,
                        "strike": strike,
                        "type": opt_type,
                        "expiry": expiry,
                    })
    return needed


def fetch_and_cache(
    option_symbols: dict[str, list[dict]],
    provider: AlpacaOptionsProvider,
    start: str, end: str,
) -> dict[str, int]:
    """Fetch option bars for all symbols and cache to disk.

    Returns:
        Dict with stats: {fetched, cached, failed, total_bars}
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"fetched": 0, "cached": 0, "failed": 0, "total_bars": 0}

    total = len(option_symbols)
    for i, (occ_sym, refs) in enumerate(sorted(option_symbols.items()), 1):
        cache_file = CACHE_DIR / f"{occ_sym}_{start}_{end}.json"
        if cache_file.exists():
            stats["cached"] += 1
            with open(cache_file) as f:
                data = json.load(f)
            stats["total_bars"] += len(data.get("bars", []))
            if i % 50 == 0:
                print(f"  [{i}/{total}] cached ({stats['cached']} cached, {stats['fetched']} fetched, {stats['failed']} failed)")
            continue

        print(f"  [{i}/{total}] Fetching {occ_sym}...", end=" ")
        bars = provider.get_bars(occ_sym, start, end, "1Min", 10000)
        if bars:
            stats["fetched"] += 1
            stats["total_bars"] += len(bars)
            print(f"{len(bars)} bars")
            with open(cache_file, "w") as f:
                json.dump({"symbol": occ_sym, "bars": bars, "refs": refs}, f)
        else:
            stats["failed"] += 1
            print("FAILED (no data or 403)")

        # Rate limit: 10 requests per second max
        time.sleep(0.1)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Pre-fetch option bars for ORB backtest")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--dte-min", type=int, default=1)
    parser.add_argument("--dte-max", type=int, default=14)
    parser.add_argument("--strike-offsets", default="-1,0,1",
                        help="Comma-separated strike offsets from ATM")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    strike_offsets = [int(x) for x in args.strike_offsets.split(",")]

    print(f"\nOption Bars Pre-Fetcher")
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Date range:    {args.start} → {args.end}")
    print(f"  DTE range:     {args.dte_min}-{args.dte_max} days")
    print(f"  Strike offsets: {strike_offsets}")

    # Fetch equity bars to determine which option contracts we need
    alpaca = AlpacaProvider()
    if not alpaca.available:
        print("ERROR: Alpaca not configured")
        sys.exit(1)
    equity_provider = CachedProvider(alpaca)

    print(f"\n  Fetching 1m equity data...")
    frames = fetch_1m_data(symbols, args.start, args.end, equity_provider)
    if not frames:
        sys.exit(1)

    # Generate all option symbols we need
    print(f"\n  Generating option symbol list...")
    needed = generate_option_symbols(
        symbols, frames, args.dte_min, args.dte_max, strike_offsets
    )
    print(f"  Need to fetch {len(needed)} unique option contracts")

    # Fetch and cache
    options_provider = AlpacaOptionsProvider()
    if not options_provider.available:
        print("ERROR: Alpaca options not configured")
        sys.exit(1)

    print(f"\n  Fetching option bars...")
    t0 = time.time()
    stats = fetch_and_cache(needed, options_provider, args.start, args.end)
    elapsed = time.time() - t0

    print(f"\n  Done in {elapsed:.0f}s")
    print(f"  Fetched: {stats['fetched']}")
    print(f"  Cached:  {stats['cached']}")
    print(f"  Failed:  {stats['failed']}")
    print(f"  Total bars: {stats['total_bars']}")
    print(f"  Cache dir: {CACHE_DIR}")


if __name__ == "__main__":
    main()
