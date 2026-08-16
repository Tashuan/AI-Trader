"""Options-based ORB backtester.

Amplifies the thin ORB equity edge via options leverage. The ORB signal
is generated on the underlying stock (1m bars), but instead of buying
shares, we buy ATM options (calls for longs, puts for shorts).

The option is held until:
  - The underlying hits the stop/target price (option is sold at market)
  - End of day force-close
  - Option bar data runs out

Key differences from equity backtest:
  - Option prices are fetched from Alpaca's options bars API (sparse — only
    bars with trades, not every minute)
  - Option P&L is calculated from actual option bar data where available,
    and interpolated from delta where no bar exists
  - Bid-ask spread is wider for options (modeled as slippage_bps on option price)
  - 1 option contract = 100 shares of underlying exposure

Usage:
  cd agents
  python3 ../research/strategy_search/orb_options_backtester.py
  python3 ../research/strategy_search/orb_options_backtester.py --zero-cost
  python3 ../research/strategy_search/orb_options_backtester.py --symbols NVDA,TSLA
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import time as dt_time, datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "research" / "strategy_search"))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import pandas as pd
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from backtest_report import BacktestReport, TradeRecord
from schwab_options_provider import SchwabOptionsProvider, build_schwab_symbol
from scalp_alt_signals import (
    fetch_1m_data, fetch_prev_closes, SLIPPAGE_BPS,
    DEFAULT_SYMBOLS, DEFAULT_START, DEFAULT_END,
)

# ── Config ─────────────────────────────────────────────────────────────
ORB_CONFIG = {
    "range_minutes": 5,
    "stop_pct": 0.7,
    "target_pct": 1.2,
    "latest_entry": "10:30",
    "max_positions": 3,
    "position_pct": 15.0,  # % of equity per trade (equity equivalent)
}

# Strike steps per symbol (common US equity option strike increments)
STRIKE_STEPS = {
    "NVDA": 2.5, "TSLA": 2.5, "AMD": 0.5, "AAPL": 2.5, "META": 2.5,
    "AMZN": 2.5, "MSFT": 2.5, "GOOGL": 2.5, "NFLX": 5.0, "INTC": 0.5,
}

# Option slippage is wider than equity — options have wider bid-ask
OPTION_SLIPPAGE_BPS = 10.0  # 10 bps = 0.1% per fill


# ── Data classes ───────────────────────────────────────────────────────
@dataclass
class OptionPosition:
    symbol: str           # underlying symbol
    option_symbol: str    # OCC option symbol
    side: str             # "long" or "short" (direction of underlying trade)
    option_type: str      # "call" or "put"
    strike: float
    expiration: str
    entry_price: float    # option entry price (per contract)
    entry_ts: str
    qty: int              # number of contracts
    entry_fee: float
    # Underlying reference prices for stop/target
    underlying_entry: float
    stop_price: float     # underlying stop
    target_price: float   # underlying target
    bars_held: int = 0
    max_favorable: float = 0.0
    max_adverse: float = 0.0


# ── ORB Signal (simplified, from equity backtest) ──────────────────────
class ORBSignalGenerator:
    """Generates ORB signals on the underlying stock."""

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self.session_date = None
        self.range_high = None
        self.range_low = None
        self.entered = False
        self.range_end = dt_time(9, 30 + config.get("range_minutes", 5))
        self.latest_entry = dt_time(*map(int, config.get("latest_entry", "10:30").split(":")))

    def reset(self, date):
        self.session_date = date
        self.range_high = None
        self.range_low = None
        self.entered = False

    def on_bar(self, ts, bar, idx, day_bars) -> dict | None:
        if ts.time() < dt_time(9, 30):
            return None
        if ts.time() <= self.range_end:
            high = float(bar["High"])
            low = float(bar["Low"])
            if self.range_high is None:
                self.range_high, self.range_low = high, low
            else:
                self.range_high = max(self.range_high, high)
                self.range_low = min(self.range_low, low)
            return None
        if self.entered:
            return None
        if ts.time() > self.latest_entry:
            return None
        if self.range_high is None:
            return None
        close = float(bar["Close"])
        if close > self.range_high:
            side = "long"
        elif close < self.range_low:
            side = "short"
        else:
            return None
        self.entered = True
        stop_dist = close * self.config.get("stop_pct", 0.7) / 100
        target_dist = close * self.config.get("target_pct", 1.2) / 100
        return {
            "symbol": self.symbol,
            "side": side,
            "entry_price": close,
            "stop_price": close - stop_dist if side == "long" else close + stop_dist,
            "target_price": close + target_dist if side == "long" else close - target_dist,
            "ts": str(ts),
        }


# ── Option bar cache ───────────────────────────────────────────────────
_CACHE_DIR = _PROJECT_ROOT / "research" / "strategy_search" / "option_bars_cache"


class OptionBarCache:
    """Caches option bars per contract, with disk persistence.

    Uses Schwab options provider which returns DataFrames with UTC DatetimeIndex.
    Bars are cached both in-memory and on disk (parquet format) so subsequent
    backtest runs skip the API calls entirely.
    """

    def __init__(self, provider: SchwabOptionsProvider):
        self.provider = provider
        self._cache: dict[str, pd.DataFrame] = {}
        self._disk_hits = 0
        self._api_calls = 0
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, option_symbol: str) -> Path:
        """Filesystem path for a cached contract's bars."""
        safe = option_symbol.replace(" ", "__").replace("/", "_")
        return _CACHE_DIR / f"{safe}.parquet"

    def get_bars(self, option_symbol: str, start: str, end: str) -> pd.DataFrame | None:
        """Get option bars as a DataFrame (memory → disk → API)."""
        # 1. In-memory cache
        if option_symbol in self._cache:
            return self._cache[option_symbol]

        # 2. Disk cache (parquet or NO_DATA marker)
        cache_file = self._cache_path(option_symbol)
        if cache_file.exists():
            # NO_DATA marker files are tiny text; parquet files are binary
            if cache_file.stat().st_size < 20:
                if cache_file.read_text() == "NO_DATA":
                    self._cache[option_symbol] = None
                    self._disk_hits += 1
                    return None
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty:
                    self._cache[option_symbol] = df
                    self._disk_hits += 1
                    return df
            except Exception:
                pass  # Corrupt cache file — re-fetch

        # 3. API fetch
        self._api_calls += 1
        df = self.provider.get_bars(option_symbol, start, end, "minute", 1)
        if df is not None and not df.empty:
            self._cache[option_symbol] = df
            # Persist to disk
            try:
                df.to_parquet(cache_file)
            except Exception:
                pass  # Disk write failed — still cached in memory
        else:
            self._cache[option_symbol] = None
            # Cache "no data" result with a marker file to avoid re-fetching
            try:
                cache_file.write_text("NO_DATA")
            except Exception:
                pass
        return self._cache[option_symbol]

    def get_price_at(self, option_symbol: str, target_ts: pd.Timestamp,
                     bars_df: pd.DataFrame = None) -> float | None:
        """Get option close price at or nearest before target timestamp.

        Handles timezone mismatch: equity bars are tz-naive (ET times),
        Schwab option bars are tz-aware UTC. We convert the target timestamp
        to UTC before comparing.
        """
        if bars_df is None:
            bars_df = self.get_bars(option_symbol, "", "")
        if bars_df is None or bars_df.empty:
            return None
        idx = bars_df.index
        # Normalize: option bars are UTC, equity timestamps are naive ET
        if idx.tz is not None:
            if target_ts.tz is None:
                # target_ts is naive ET — localize to ET then convert to UTC
                target_ts = target_ts.tz_localize("US/Eastern").tz_convert("UTC")
        else:
            if target_ts.tz is not None:
                target_ts = target_ts.tz_localize(None)
        # Find the nearest bar at or before target_ts
        mask = idx <= target_ts
        if not mask.any():
            return None
        return float(bars_df.loc[mask].iloc[-1]["Close"])


# ── Backtest runner ────────────────────────────────────────────────────
def run_options_backtest(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    prev_closes: dict,
    options_provider: SchwabOptionsProvider,
    capital: float,
    slippage_bps: float,
    option_slippage_bps: float,
    fee_rate: float,
    config: dict,
    start_date: str,
    end_date: str,
    strike_offset: int = 0,
    dte_min: int = 1,
    dte_max: int = 14,
) -> dict[str, Any]:
    """Run the options-based ORB backtest.

    Args:
        symbols: List of underlying tickers
        frames: 1m equity bars per symbol
        prev_closes: Previous day closes (unused for ORB but kept for compat)
        options_provider: SchwabOptionsProvider instance
        capital: Starting capital
        slippage_bps: Equity slippage (for signal generation reference)
        option_slippage_bps: Option slippage (wider bid-ask)
        fee_rate: Fee rate as decimal (0.0 = no fees)
        config: ORB config dict
        start_date: Backtest start date
        end_date: Backtest end date
        strike_offset: Strike offset from ATM (0 = ATM, 1 = OTM, -1 = ITM)
        dte_min: Minimum days to expiration
        dte_max: Maximum days to expiration

    Returns:
        Results dict with metrics and trades
    """
    max_positions = config.get("max_positions", 3)
    position_pct = config.get("position_pct", 30.0)

    # Pre-build index lookups for equity bars
    ts_to_idx: dict[str, dict] = {}
    day_groups: dict[str, dict] = {}
    all_dates = set()
    for sym, df in frames.items():
        ts_to_idx[sym] = {ts: i for i, ts in enumerate(df["Timestamp"])}
        day_groups[sym] = {d: g for d, g in df.groupby(df["Timestamp"].dt.date)}
        all_dates.update(day_groups[sym].keys())
    all_dates = sorted(all_dates)

    # Initialize
    strategies: dict[str, ORBSignalGenerator] = {}
    positions: dict[str, OptionPosition] = {}
    trades: list[TradeRecord] = []
    curve: list[dict] = []
    cash = capital
    first_ts = None
    last_ts = None
    last_prices: dict[str, float] = {}
    diagnostics: dict[str, int] = defaultdict(int)
    option_bar_cache = OptionBarCache(options_provider)
    skipped_no_option_data = 0

    # Pre-fetch option bars for each symbol's ATM contracts per day
    # We need to know which expiration to use for each trading day
    # Strategy: use the nearest expiration that's >= dte_min days away and <= dte_max

    for date in all_dates:
        for sym in frames:
            if sym not in strategies:
                strategies[sym] = ORBSignalGenerator(sym, config)
            strat = strategies[sym]
            if strat.session_date != date:
                strat.reset(date)

        day_data: dict[str, pd.DataFrame] = {}
        for sym in frames:
            day_df = day_groups.get(sym, {}).get(date)
            if day_df is not None and not day_df.empty:
                day_data[sym] = day_df
        if not day_data:
            continue

        day_ts = sorted(set(t for df in day_data.values() for t in df["Timestamp"]))

        for ts in day_ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            prices: dict[str, float] = {}
            highs: dict[str, float] = {}
            lows: dict[str, float] = {}
            bars: dict[str, pd.Series] = {}
            day_bars_map: dict[str, pd.DataFrame] = {}

            for sym, day_df in day_data.items():
                idx = ts_to_idx[sym].get(ts)
                if idx is None:
                    continue
                local_idx = idx - day_df.index[0]
                bar = day_df.iloc[local_idx]
                bars[sym] = bar
                prices[sym] = float(bar["Close"])
                highs[sym] = float(bar["High"])
                lows[sym] = float(bar["Low"])
                day_bars_map[sym] = day_df.iloc[:local_idx + 1]
            last_prices.update(prices)

            # ── Exit management ───────────────────────────────────
            for sym in list(positions.keys()):
                pos = positions[sym]
                if sym not in prices:
                    pos.bars_held += 1
                    continue
                px = prices[sym]
                hi = highs[sym]
                lo = lows[sym]
                pos.bars_held += 1

                # Check stop/target on the UNDERLYING
                exit_reason = None
                if pos.side == "long":
                    if lo <= pos.stop_price:
                        exit_reason = "stop_loss"
                    elif hi >= pos.target_price:
                        exit_reason = "take_profit"
                else:
                    if hi >= pos.stop_price:
                        exit_reason = "stop_loss"
                    elif lo <= pos.target_price:
                        exit_reason = "take_profit"

                # EOD force close
                if exit_reason is None and ts.time() >= dt_time(15, 55):
                    exit_reason = "eod_close"

                if exit_reason is not None:
                    # Get option exit price
                    option_bars = option_bar_cache.get_bars(
                        pos.option_symbol, start_date, end_date
                    )
                    option_px = option_bar_cache.get_price_at(
                        pos.option_symbol, ts, option_bars
                    )
                    if option_px is None:
                        # No option bar at this time — use last known option price
                        # or fall back to entry price (conservative)
                        option_px = pos.entry_price  # worst case: no change
                        diagnostics["no_option_exit_bar"] += 1

                    # Apply option slippage
                    fill_px = option_px * (1 - option_slippage_bps / 10000)  # sell fills lower
                    fee = fill_px * pos.qty * 100 * fee_rate  # qty in contracts, *100 shares

                    pnl = (fill_px - pos.entry_price) * pos.qty * 100 - fee - pos.entry_fee
                    cash += fill_px * pos.qty * 100 - fee

                    pnl_pct = pnl / (pos.entry_price * pos.qty * 100) * 100 if pos.entry_price > 0 else 0
                    hold_hours = pos.bars_held / 60.0
                    trades.append(TradeRecord(
                        symbol=f"{sym} {pos.option_type[:1].upper()}{pos.strike:.0f}",
                        side="long",  # always buying options
                        entry_date=pos.entry_ts, exit_date=str(ts),
                        entry_price=pos.entry_price, exit_price=fill_px,
                        quantity=pos.qty * 100, pnl=pnl, pnl_pct=pnl_pct,
                        hold_days=int(hold_hours / 24), hold_hours=hold_hours,
                        reason=exit_reason,
                    ))
                    diagnostics[exit_reason] += 1
                    del positions[sym]

            # ── Equity calc ───────────────────────────────────────
            equity = cash
            for sym, pos in positions.items():
                # Mark-to-market using latest option bar
                option_bars = option_bar_cache.get_bars(pos.option_symbol, start_date, end_date)
                opt_px = option_bar_cache.get_price_at(pos.option_symbol, ts, option_bars)
                if opt_px is None:
                    opt_px = pos.entry_price
                equity += opt_px * pos.qty * 100
            curve.append({"date": str(ts), "equity": round(equity, 2)})

            # ── Entry signals ─────────────────────────────────────
            if len(positions) >= max_positions:
                continue
            if ts.time() >= dt_time(15, 50):
                continue

            for sym in symbols:
                if sym in positions or sym not in bars:
                    continue
                strat = strategies.get(sym)
                if strat is None or strat.session_date != date:
                    continue
                day_df = day_bars_map.get(sym)
                if day_df is None or day_df.empty:
                    continue
                idx = len(day_df) - 1
                signal = strat.on_bar(ts, bars[sym], idx, day_df)
                if signal is None:
                    continue

                # ── Buy option instead of stock ───────────────────
                spot = signal["entry_price"]
                option_type = "call" if signal["side"] == "long" else "put"
                strike_step = STRIKE_STEPS.get(sym, 2.5)

                # Find nearest expiration within DTE range
                expiry = _find_expiration(date, dte_min, dte_max)
                if expiry is None:
                    skipped_no_option_data += 1
                    continue

                # Construct Schwab option symbol directly (no API call needed)
                atm_strike = round(spot / strike_step) * strike_step
                target_strike = atm_strike + strike_offset * strike_step

                # Try the expiration, with fallback to next week if no data
                option_bars = None
                option_symbol = None
                for attempt in range(3):
                    test_expiry = _find_expiration(
                        date, dte_min + attempt * 7, dte_max + attempt * 7
                    )
                    if test_expiry is None:
                        break
                    test_sym = build_schwab_symbol(
                        sym, test_expiry, option_type, target_strike
                    )
                    test_bars = option_bar_cache.get_bars(
                        test_sym, start_date, end_date
                    )
                    if test_bars is not None and not test_bars.empty:
                        option_symbol = test_sym
                        option_bars = test_bars
                        expiry = test_expiry
                        break

                if option_bars is None or option_symbol is None:
                    skipped_no_option_data += 1
                    continue
                # Get option entry price
                option_entry = option_bar_cache.get_price_at(
                    option_symbol, ts, option_bars
                )
                if option_entry is None or option_entry <= 0:
                    skipped_no_option_data += 1
                    continue

                # Apply entry slippage (buy fills higher)
                entry_px = option_entry * (1 + option_slippage_bps / 10000)

                # Position sizing: use position_pct of equity for the option premium
                notional = equity * position_pct / 100.0
                qty_contracts = max(1, int(notional / (entry_px * 100)))
                if qty_contracts <= 0:
                    continue

                entry_fee = entry_px * qty_contracts * 100 * fee_rate
                cash -= entry_px * qty_contracts * 100 + entry_fee

                positions[sym] = OptionPosition(
                    symbol=sym,
                    option_symbol=option_symbol,
                    side=signal["side"],
                    option_type=option_type,
                    strike=target_strike,
                    expiration=expiry,
                    entry_price=entry_px,
                    entry_ts=str(ts),
                    qty=qty_contracts,
                    entry_fee=entry_fee,
                    underlying_entry=spot,
                    stop_price=signal["stop_price"],
                    target_price=signal["target_price"],
                )
                diagnostics["entries"] += 1

    # Close remaining positions at backtest end
    for sym, pos in list(positions.items()):
        option_bars = option_bar_cache.get_bars(pos.option_symbol, start_date, end_date)
        opt_px = option_bar_cache.get_price_at(pos.option_symbol, last_ts, option_bars)
        if opt_px is None:
            opt_px = pos.entry_price
        fill_px = opt_px * (1 - option_slippage_bps / 10000)
        fee = fill_px * pos.qty * 100 * fee_rate
        pnl = (fill_px - pos.entry_price) * pos.qty * 100 - fee - pos.entry_fee
        cash += fill_px * pos.qty * 100 - fee
        pnl_pct = pnl / (pos.entry_price * pos.qty * 100) * 100 if pos.entry_price > 0 else 0
        hold_hours = pos.bars_held / 60.0
        trades.append(TradeRecord(
            symbol=f"{sym} {pos.option_type[:1].upper()}{pos.strike:.0f}",
            side="long", entry_date=pos.entry_ts, exit_date=str(last_ts),
            entry_price=pos.entry_price, exit_price=fill_px,
            quantity=pos.qty * 100, pnl=pnl, pnl_pct=pnl_pct,
            hold_days=int(hold_hours / 24), hold_hours=hold_hours,
            reason="backtest_end",
        ))
        del positions[sym]

    # Calculate metrics
    if not curve:
        return {"error": "no trades", "trades": [], "return_pct": 0}

    report = BacktestReport.calculate_metrics(
        agent_name="orb_options", symbols=symbols,
        start_date=str(first_ts) if first_ts else "",
        end_date=str(last_ts) if last_ts else "",
        initial_capital=capital, final_equity=cash,
        equity_curve=curve, trades=trades, interval="1m",
        slippage_bps=option_slippage_bps, periods_per_year=390 * 252,
    )
    r = report.to_dict()
    r["diagnostics"] = dict(diagnostics)
    r["skipped_no_option_data"] = skipped_no_option_data
    r["cache_disk_hits"] = option_bar_cache._disk_hits
    r["cache_api_calls"] = option_bar_cache._api_calls
    return r


def _find_expiration(date, dte_min: int, dte_max: int) -> str | None:
    """Find the nearest Friday expiration within DTE range.

    Options typically expire on Fridays. We find the nearest Friday
    that's >= dte_min days away and <= dte_max days away.

    Note: dte_min should be >= 2 to avoid expiration-day contracts
    which may have no historical data after expiry.
    """
    d = datetime.fromisoformat(str(date))
    # Find next Friday (0=Monday, 4=Friday)
    days_to_friday = (4 - d.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7  # If today is Friday, use next Friday
    friday = d + pd.Timedelta(days=days_to_friday)
    dte = (friday - d).days
    if dte < dte_min:
        friday += pd.Timedelta(days=7)
        dte = (friday - d).days
    if dte > dte_max:
        return None
    return friday.strftime("%Y-%m-%d")


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Options-based ORB backtester")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--slippage", type=float, default=OPTION_SLIPPAGE_BPS)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--zero-cost", action="store_true")
    parser.add_argument("--strike-offset", type=int, default=0,
                        help="0=ATM, 1=OTM, -1=ITM")
    parser.add_argument("--dte-min", type=int, default=2)
    parser.add_argument("--dte-max", type=int, default=14)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    opt_slippage = 0.0 if args.zero_cost else args.slippage
    fee_rate = 0.0 if args.zero_cost else args.fee_rate

    print(f"\nOptions-Based ORB Backtester")
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Date range:    {args.start} → {args.end}")
    print(f"  Capital:       ${args.capital:,.0f}")
    print(f"  Option slip:   {opt_slippage} bps")
    print(f"  Fee rate:      {fee_rate}")
    print(f"  Strike offset: {args.strike_offset} ({'ATM' if args.strike_offset == 0 else 'OTM' if args.strike_offset > 0 else 'ITM'})")
    print(f"  DTE range:     {args.dte_min}-{args.dte_max} days")
    print(f"  Mode:          {'ZERO COST' if args.zero_cost else 'realistic costs'}")

    # Initialize providers
    alpaca = AlpacaProvider()
    if not alpaca.available:
        print("ERROR: Alpaca not configured")
        sys.exit(1)
    equity_provider = CachedProvider(alpaca)
    options_provider = SchwabOptionsProvider()
    if not options_provider.available:
        print("ERROR: Schwab options not configured. Run schwab_oauth_flow.py first.")
        sys.exit(1)

    # Fetch equity 1m bars
    print(f"\n  Fetching 1m equity data...")
    frames = fetch_1m_data(symbols, args.start, args.end, equity_provider)
    if not frames:
        sys.exit(1)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    prev_closes = fetch_prev_closes(symbols, all_dates, equity_provider)

    # Run backtest
    print(f"\n  Running options ORB backtest...")
    t0 = time.time()
    result = run_options_backtest(
        symbols=symbols, frames=frames, prev_closes=prev_closes,
        options_provider=options_provider, capital=args.capital,
        slippage_bps=SLIPPAGE_BPS, option_slippage_bps=opt_slippage,
        fee_rate=fee_rate, config=ORB_CONFIG,
        start_date=args.start, end_date=args.end,
        strike_offset=args.strike_offset,
        dte_min=args.dte_min, dte_max=args.dte_max,
    )
    elapsed = time.time() - t0

    # Print results
    if "error" in result:
        print(f"\n  ERROR: {result['error']}")
        sys.exit(1)

    status = "PASS" if result["total_return_pct"] > 0 and result["profit_factor"] > 1.0 else "FAIL"
    print(f"\n{'='*70}")
    print(f"  orb_options  [{status}]")
    print(f"{'='*70}")
    print(f"  Return:       {result['total_return_pct']:+.2f}%")
    print(f"  Profit Factor: {result['profit_factor']:.3f}")
    print(f"  Win Rate:     {result['win_rate']:.0%}  ({result['total_trades']} trades)")
    print(f"  Max DD:       {result['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe:       {result['sharpe_ratio']:.3f}")
    print(f"  Avg Hold:     {result['avg_hold_hours']:.1f}h")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Runtime:      {elapsed:.1f}s")

    diag = result.get("diagnostics", {})
    print(f"\n  Diagnostics:")
    for k, v in sorted(diag.items()):
        print(f"    {k}: {v}")
    print(f"    skipped_no_option_data: {result.get('skipped_no_option_data', 0)}")
    print(f"    cache disk hits: {result.get('cache_disk_hits', 0)}")
    print(f"    cache api calls: {result.get('cache_api_calls', 0)}")

    # Per-symbol breakdown
    ps = result.get("per_symbol_stats", {})
    if ps:
        print(f"\n  --- Per-Symbol ---")
        for sym, stats in sorted(ps.items()):
            print(f"    {sym:8s}: {stats['trades']:3d} trades, "
                  f"WR={stats['win_rate']:.0%}, "
                  f"PnL=${stats['total_pnl']:+.2f}, "
                  f"avg={stats['avg_pnl_pct']:+.2f}%")

    # Sample trades
    if result.get("trades"):
        print(f"\n  --- Sample Trades (first 10) ---")
        for t in result["trades"][:10]:
            if isinstance(t, dict):
                print(f"    {t.get('symbol',''):12s} {t.get('side',''):5s} "
                      f"pnl=${t.get('pnl',0):+8.2f} ({t.get('pnl_pct',0):+6.2f}%) "
                      f"hold={t.get('hold_hours',0):.1f}h reason={t.get('reason','')}")
            else:
                print(f"    {t.symbol:12s} {t.side:5s} "
                      f"pnl=${t.pnl:+8.2f} ({t.pnl_pct:+6.2f}%) "
                      f"hold={t.hold_hours:.1f}h reason={t.reason}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\n  Results saved to: {args.json}")


if __name__ == "__main__":
    main()
