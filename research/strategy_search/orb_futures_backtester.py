"""Futures ORB backtester.

Applies the opening-range-breakout concept to US futures contracts.
Trades the underlying contract directly (no options), using leverage
that's built into futures margin. P&L is calculated from price movement
multiplied by the contract's dollar multiplier.

Data source: yfinance 5m bars (ES=F, NQ=F, CL=F, GC=F). yfinance gives
~71 days of 5m history for futures, which is enough for an initial
honest test. The 09:30 5m bar is the opening range; the 09:35 bar is
the first breakout-eligible bar.

Realistic execution assumptions:
  - Slippage in ticks (not BPS) — futures have discrete tick sizes
  - Commission per contract per side (exchange + broker fees)
  - Conservative intrabar policy (stop-first when both touched in one bar)
  - Confirmation window after entry (no stops for first N minutes)
  - Force exit at 15:55 ET

The 0.1% extension filter from the equity ORB audit is included — it
rejects breakouts where the close is already stretched more than X%
beyond the range edge, which was the only thing that turned the equity
version positive.

Usage:
  cd agents
  python3 ../research/strategy_search/orb_futures_backtester.py
  python3 ../research/strategy_search/orb_futures_backtester.py --symbols ES=F,NQ=F,CL=F,GC=F
  python3 ../research/strategy_search/orb_futures_backtester.py --stop-pct 0.3 --target-pct 0.5
  python3 ../research/strategy_search/orb_futures_backtester.py --extension-filter 0.1
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "agents"))

import pandas as pd

# ── Contract specifications ─────────────────────────────────────────────

@dataclass(frozen=True)
class FuturesContract:
    yf_symbol: str       # yfinance ticker (e.g. "ES=F")
    name: str
    multiplier: float    # $ per 1.0 price move
    tick_size: float     # minimum price increment
    tick_value: float    # $ per tick (= multiplier * tick_size)


CONTRACTS: dict[str, FuturesContract] = {
    "ES=F": FuturesContract("ES=F", "E-mini S&P 500", 50.0, 0.25, 12.50),
    "NQ=F": FuturesContract("NQ=F", "E-mini Nasdaq 100", 20.0, 0.25, 5.00),
    "CL=F": FuturesContract("CL=F", "Crude Oil WTI", 1000.0, 0.01, 10.00),
    "GC=F": FuturesContract("GC=F", "Gold", 100.0, 0.10, 10.00),
    "RTY=F": FuturesContract("RTY=F", "E-mini Russell 2000", 50.0, 0.10, 5.00),
    "YM=F": FuturesContract("YM=F", "E-mini Dow", 5.0, 1.0, 5.00),
}

# ── Default config ──────────────────────────────────────────────────────

FUTURES_ORB_CONFIG = {
    "range_minutes": 5,          # opening range length (one 5m bar)
    "stop_pct": 0.3,             # stop loss as % of entry price
    "target_pct": 0.5,           # profit target as % of entry price
    "latest_entry": "10:30",     # no new entries after this ET time
    "max_positions": 3,          # max concurrent positions
    "contracts_per_position": 1, # fixed contract count per position
    "confirmation_minutes": 10,  # no stops for first N minutes after entry
    "circuit_breaker": 3,        # consecutive losses before halting a symbol
    "extension_filter_pct": 0.0, # reject breakouts >X% beyond range edge (0=off)
    "min_range_width_pct": 0.0,  # skip if opening range <X% of price (0=off)
    "force_exit_time": "15:55",  # force close all positions
    "market_open": "09:30",      # RTH open
    "slippage_ticks": 1,         # slippage in ticks per fill
    "commission_per_side": 2.50, # commission per contract per side
}

# ── Data fetching ───────────────────────────────────────────────────────

def fetch_futures_5m(symbols: list[str], period: str = "60d") -> dict[str, pd.DataFrame]:
    """Fetch 5m bars from yfinance, filtered to RTH (09:30-16:00 ET)."""
    import yfinance as yf
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(period=period, interval="5m")
            if df is None or df.empty:
                print(f"  {sym}: no data")
                continue
            # yfinance returns tz-aware index (America/New_York for US futures)
            # Filter to RTH: 09:30 through 15:55 ET
            df = df.copy()
            if df.index.tz is None:
                df.index = df.index.tz_localize("America/New_York")
            else:
                df.index = df.index.tz_convert("America/New_York")
            rth_start = dt_time(9, 30)
            rth_end = dt_time(15, 55)
            mask = (df.index.time >= rth_start) & (df.index.time <= rth_end)
            df = df[mask]
            # Normalize column names
            df = df.rename(columns={
                "Open": "Open", "High": "High", "Low": "Low",
                "Close": "Close", "Volume": "Volume",
            })
            df["Timestamp"] = df.index
            frames[sym] = df
            print(f"  {sym}: {len(df)} RTH 5m bars, {df.index[0].date()} to {df.index[-1].date()}")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
    return frames


# ── Position ────────────────────────────────────────────────────────────

@dataclass
class FuturesPosition:
    symbol: str
    side: str              # "long" or "short"
    entry_ts: datetime
    entry_price: float     # fill price including slippage
    raw_entry: float       # close price at breakout (before slippage)
    stop_price: float
    target_price: float
    qty: int               # contracts
    bars_held: int = 0
    entry_cost: float = 0.0  # commission paid at entry


# ── Signal generation ───────────────────────────────────────────────────

@dataclass
class FuturesORBSignal:
    symbol: str
    side: str
    entry_price: float     # close at breakout
    stop_price: float
    target_price: float
    range_high: float
    range_low: float
    ts: datetime
    extension_pct: float   # how far beyond range edge (0 = at edge)


def generate_signal(
    symbol: str,
    range_high: float,
    range_low: float,
    bar_close: float,
    bar_ts: datetime,
    config: dict,
) -> Optional[FuturesORBSignal]:
    """Check if a bar close is a valid ORB breakout."""
    stop_pct = config.get("stop_pct", 0.3)
    target_pct = config.get("target_pct", 0.5)
    ext_filter = config.get("extension_filter_pct", 0.0)

    if bar_close > range_high:
        side = "long"
        extension = (bar_close - range_high) / range_high * 100
    elif bar_close < range_low:
        side = "short"
        extension = (range_low - bar_close) / range_low * 100
    else:
        return None

    # Extension filter: reject breakouts already stretched too far
    if ext_filter > 0 and extension > ext_filter:
        return None

    stop_dist = bar_close * stop_pct / 100
    target_dist = bar_close * target_pct / 100

    return FuturesORBSignal(
        symbol=symbol,
        side=side,
        entry_price=bar_close,
        stop_price=bar_close - stop_dist if side == "long" else bar_close + stop_dist,
        target_price=bar_close + target_dist if side == "long" else bar_close - target_dist,
        range_high=range_high,
        range_low=range_low,
        ts=bar_ts,
        extension_pct=extension,
    )


# ── Exit checking ───────────────────────────────────────────────────────

def check_exit(
    pos: FuturesPosition,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bar_ts: datetime,
    config: dict,
) -> Optional[tuple[str, float]]:
    """Check if position should exit on this bar.

    Returns (exit_reason, exit_price) or None.
    Conservative intrabar policy: if both stop and target are touched,
    assume the stop fires first.
    """
    force_exit_str = config.get("force_exit_time", "15:55")
    force_exit_t = dt_time(*map(int, force_exit_str.split(":")))
    confirm_mins = config.get("confirmation_minutes", 10)

    # Force exit at EOD
    if bar_ts.time() >= force_exit_t:
        return ("force_exit", bar_close)

    # Confirmation period: only check target, not stop
    in_confirmation = pos.bars_held * 5 < confirm_mins  # 5m bars

    if pos.side == "long":
        target_touched = bar_high >= pos.target_price
        stop_touched = bar_low <= pos.stop_price
    else:
        target_touched = bar_low <= pos.target_price
        stop_touched = bar_high >= pos.stop_price

    # Both touched — conservative assumes stop first (unless in confirmation)
    if stop_touched and target_touched:
        if in_confirmation:
            return ("take_profit", pos.target_price)
        return ("stop_loss", pos.stop_price)

    if target_touched:
        return ("take_profit", pos.target_price)

    if stop_touched and not in_confirmation:
        return ("stop_loss", pos.stop_price)

    return None


# ── Fill price with slippage ────────────────────────────────────────────

def apply_slippage(
    raw_price: float,
    side: str,
    is_entry: bool,
    contract: FuturesContract,
    slippage_ticks: int,
) -> float:
    """Apply slippage in ticks. Entry pays worse, exit gets worse."""
    slip = slippage_ticks * contract.tick_size
    if is_entry:
        # Long entry: pay higher; short entry: pay lower
        return raw_price + slip if side == "long" else raw_price - slip
    else:
        # Long exit: sell lower; short exit: buy higher
        return raw_price - slip if side == "long" else raw_price + slip


def round_to_tick(price: float, contract: FuturesContract) -> float:
    """Round price to the contract's tick size."""
    return round(price / contract.tick_size) * contract.tick_size


# ── Backtest engine ─────────────────────────────────────────────────────

@dataclass
class FuturesTrade:
    symbol: str
    side: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    qty: int
    pnl: float           # net P&L in dollars (after commission + slippage)
    pnl_pct: float       # % return on notional
    reason: str
    bars_held: int
    extension_pct: float


def run_futures_orb_backtest(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    capital: float = 10000.0,
    config: dict = None,
) -> dict:
    """Run the futures ORB backtest."""
    config = {**FUTURES_ORB_CONFIG, **(config or {})}
    max_positions = config["max_positions"]
    contracts_per_pos = config["contracts_per_position"]
    slippage_ticks = config["slippage_ticks"]
    commission = config["commission_per_side"]
    confirm_mins = config["confirmation_minutes"]
    circuit_breaker = config["circuit_breaker"]
    min_range_width = config["min_range_width_pct"]
    latest_entry_t = dt_time(*map(int, config["latest_entry"].split(":")))
    market_open_t = dt_time(*map(int, config["market_open"].split(":")))

    # Group bars by date per symbol
    day_groups: dict[str, dict] = {}
    all_dates = set()
    for sym, df in frames.items():
        df = df.copy()
        df["date"] = df.index.date
        day_groups[sym] = {d: g for d, g in df.groupby("date")}
        all_dates.update(day_groups[sym].keys())
    all_dates = sorted(all_dates)

    # State
    equity = capital
    peak_equity = capital
    max_dd = 0.0
    trades: list[FuturesTrade] = []
    positions: list[FuturesPosition] = []
    consecutive_losses: dict[str, int] = {s: 0 for s in symbols}
    halted: dict[str, set] = {}  # date -> set of halted symbols
    daily_pnl: dict[str, float] = {}

    for date in all_dates:
        date_str = str(date)
        halted_today = halted.setdefault(date, set())

        # Reset circuit breaker halts at the start of each day
        # (circuit breaker is per-day in this model)

        # Collect all bars for this date across symbols, sorted by timestamp
        day_bars: list[tuple[datetime, str, dict]] = []
        for sym in symbols:
            if sym not in day_groups or date not in day_groups[sym]:
                continue
            df = day_groups[sym][date]
            for _, row in df.iterrows():
                ts = row["Timestamp"]
                if hasattr(ts, "to_pydatetime"):
                    ts = ts.to_pydatetime()
                day_bars.append((ts, sym, {
                    "Open": float(row["Open"]),
                    "High": float(row["High"]),
                    "Low": float(row["Low"]),
                    "Close": float(row["Close"]),
                    "Volume": float(row.get("Volume", 0)),
                }))
        day_bars.sort(key=lambda x: x[0])

        # Build opening ranges (first 5m bar at 09:30)
        opening_ranges: dict[str, tuple[float, float]] = {}
        for sym in symbols:
            if sym not in day_groups or date not in day_groups[sym]:
                continue
            df = day_groups[sym][date]
            # The 09:30 5m bar is the opening range
            range_bars = df[df.index.time == market_open_t]
            if range_bars.empty:
                continue
            rh = float(range_bars["High"].iloc[0])
            rl = float(range_bars["Low"].iloc[0])
            # Min range width filter
            if min_range_width > 0 and rl > 0:
                width_pct = (rh - rl) / rl * 100
                if width_pct < min_range_width:
                    continue
            opening_ranges[sym] = (rh, rl)

        # Process bars chronologically
        for ts, sym, bar in day_bars:
            # ── Check exits on open positions ────────────────────────
            for pos in positions[:]:
                if pos.symbol != sym:
                    continue
                pos.bars_held += 1
                exit_result = check_exit(pos, bar["High"], bar["Low"], bar["Close"], ts, config)
                if exit_result:
                    reason, raw_exit = exit_result
                    contract = CONTRACTS.get(sym)
                    if contract is None:
                        continue
                    exit_price = apply_slippage(
                        raw_exit, pos.side, is_entry=False,
                        contract=contract, slippage_ticks=slippage_ticks,
                    )
                    exit_price = round_to_tick(exit_price, contract)
                    # P&L calculation
                    if pos.side == "long":
                        price_diff = exit_price - pos.entry_price
                    else:
                        price_diff = pos.entry_price - exit_price
                    gross_pnl = price_diff * contract.multiplier * pos.qty
                    exit_commission = commission * pos.qty
                    net_pnl = gross_pnl - exit_commission
                    equity += net_pnl

                    # Track circuit breaker
                    if net_pnl < 0:
                        consecutive_losses[sym] += 1
                    else:
                        consecutive_losses[sym] = 0

                    trades.append(FuturesTrade(
                        symbol=sym, side=pos.side,
                        entry_ts=str(pos.entry_ts), exit_ts=str(ts),
                        entry_price=pos.entry_price, exit_price=exit_price,
                        qty=pos.qty, pnl=net_pnl,
                        pnl_pct=net_pnl / (pos.entry_price * contract.multiplier * pos.qty) * 100,
                        reason=reason, bars_held=pos.bars_held,
                        extension_pct=0.0,  # could track from signal
                    ))
                    daily_pnl[date_str] = daily_pnl.get(date_str, 0) + net_pnl
                    positions.remove(pos)

                    # Circuit breaker check
                    if circuit_breaker > 0 and consecutive_losses[sym] >= circuit_breaker:
                        halted_today.add(sym)

            # ── Check for new entries ────────────────────────────────
            if len(positions) >= max_positions:
                continue
            if sym in halted_today:
                continue
            if sym not in opening_ranges:
                continue
            if ts.time() <= market_open_t:
                continue  # skip the range bar itself
            if ts.time() > latest_entry_t:
                continue

            rh, rl = opening_ranges[sym]
            signal = generate_signal(sym, rh, rl, bar["Close"], ts, config)
            if signal is None:
                continue

            contract = CONTRACTS.get(sym)
            if contract is None:
                continue

            entry_price = apply_slippage(
                signal.entry_price, signal.side, is_entry=True,
                contract=contract, slippage_ticks=slippage_ticks,
            )
            entry_price = round_to_tick(entry_price, contract)
            entry_commission = commission * contracts_per_pos

            pos = FuturesPosition(
                symbol=sym, side=signal.side, entry_ts=ts,
                entry_price=entry_price, raw_entry=signal.entry_price,
                stop_price=signal.stop_price, target_price=signal.target_price,
                qty=contracts_per_pos, bars_held=0, entry_cost=entry_commission,
            )
            # Deduct entry commission from equity immediately
            equity -= entry_commission
            positions.append(pos)

        # Force-exit any remaining positions at end of day
        for pos in positions[:]:
            # Find the last bar for this symbol on this date
            if sym not in day_groups or date not in day_groups[sym]:
                continue
            df = day_groups[sym][date]
            last_bar = df.iloc[-1]
            last_ts = df.index[-1]
            if hasattr(last_ts, "to_pydatetime"):
                last_ts = last_ts.to_pydatetime()
            contract = CONTRACTS.get(pos.symbol)
            if contract is None:
                continue
            exit_price = apply_slippage(
                float(last_bar["Close"]), pos.side, is_entry=False,
                contract=contract, slippage_ticks=slippage_ticks,
            )
            exit_price = round_to_tick(exit_price, contract)
            if pos.side == "long":
                price_diff = exit_price - pos.entry_price
            else:
                price_diff = pos.entry_price - exit_price
            gross_pnl = price_diff * contract.multiplier * pos.qty
            exit_commission = commission * pos.qty
            net_pnl = gross_pnl - exit_commission
            equity += net_pnl

            trades.append(FuturesTrade(
                symbol=pos.symbol, side=pos.side,
                entry_ts=str(pos.entry_ts), exit_ts=str(last_ts),
                entry_price=pos.entry_price, exit_price=exit_price,
                qty=pos.qty, pnl=net_pnl,
                pnl_pct=net_pnl / (pos.entry_price * contract.multiplier * pos.qty) * 100,
                reason="eod_force_exit", bars_held=pos.bars_held,
                extension_pct=0.0,
            ))
            daily_pnl[date_str] = daily_pnl.get(date_str, 0) + net_pnl
            positions.remove(pos)

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd

    # ── Compute metrics ──────────────────────────────────────────────
    total_pnl = equity - capital
    total_return_pct = total_pnl / capital * 100
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

    # Per-symbol breakdown
    per_symbol: dict[str, dict] = {}
    for sym in symbols:
        sym_trades = [t for t in trades if t.symbol == sym]
        if not sym_trades:
            per_symbol[sym] = {"trades": 0, "pnl": 0, "win_rate": 0}
            continue
        sym_wins = [t for t in sym_trades if t.pnl > 0]
        per_symbol[sym] = {
            "trades": len(sym_trades),
            "pnl": sum(t.pnl for t in sym_trades),
            "win_rate": len(sym_wins) / len(sym_trades) * 100,
        }

    # Exit reason breakdown
    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t.reason] = exit_reasons.get(t.reason, 0) + 1

    return {
        "total_return_pct": total_return_pct,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "max_drawdown_pct": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "per_symbol": per_symbol,
        "exit_reasons": exit_reasons,
        "daily_pnl": daily_pnl,
        "trades": trades,
        "config": config,
    }


# ── Reporting ───────────────────────────────────────────────────────────

def print_report(result: dict, label: str = ""):
    """Print a concise backtest report."""
    print(f"\n{'='*70}")
    if label:
        print(f"  {label}")
        print(f"{'='*70}")
    cfg = result["config"]
    print(f"  Stop={cfg['stop_pct']}%  Target={cfg['target_pct']}%  "
          f"ExtFilter={cfg['extension_filter_pct']}%  "
          f"Confirm={cfg['confirmation_minutes']}m  "
          f"CB={cfg['circuit_breaker']}")
    print(f"  Slippage={cfg['slippage_ticks']}t  Commission=${cfg['commission_per_side']}/side")
    print(f"{'─'*70}")
    print(f"  Total Return:   {result['total_return_pct']:+.2f}%  "
          f"(${result['total_pnl']:+,.2f})")
    print(f"  Final Equity:   ${result['final_equity']:,.2f}")
    print(f"  Max Drawdown:   {result['max_drawdown_pct']:.1f}%")
    print(f"  Win Rate:       {result['win_rate']:.1f}%  "
          f"({result['wins']}W / {result['losses']}L)")
    print(f"  Profit Factor:  {result['profit_factor']:.3f}")
    print(f"  Avg Win:        ${result['avg_win']:+,.2f}")
    print(f"  Avg Loss:       ${result['avg_loss']:+,.2f}")
    print(f"  Total Trades:   {result['total_trades']}")
    print(f"{'─'*70}")
    print(f"  Per-Symbol:")
    for sym, stats in result["per_symbol"].items():
        print(f"    {sym:8s}: {stats['trades']:3d} trades, "
              f"PnL ${stats['pnl']:+,.2f}, WR {stats['win_rate']:.0f}%")
    print(f"{'─'*70}")
    print(f"  Exit Reasons:")
    for reason, count in sorted(result["exit_reasons"].items()):
        print(f"    {reason:20s}: {count}")
    print(f"{'─'*70}")
    # Show worst and best days
    daily = result["daily_pnl"]
    if daily:
        best_day = max(daily, key=daily.get)
        worst_day = min(daily, key=daily.get)
        print(f"  Best Day:  {best_day}  ${daily[best_day]:+,.2f}")
        print(f"  Worst Day: {worst_day}  ${daily[worst_day]:+,.2f}")
    print(f"{'='*70}\n")


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Futures ORB backtester")
    parser.add_argument("--symbols", default="ES=F,NQ=F,CL=F,GC=F",
                        help="Comma-separated yfinance futures symbols")
    parser.add_argument("--period", default="60d",
                        help="yfinance history period (5m max is ~60d)")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--stop-pct", type=float, default=None)
    parser.add_argument("--target-pct", type=float, default=None)
    parser.add_argument("--extension-filter", type=float, default=None)
    parser.add_argument("--confirmation-minutes", type=int, default=None)
    parser.add_argument("--circuit-breaker", type=int, default=None)
    parser.add_argument("--max-positions", type=int, default=None)
    parser.add_argument("--slippage-ticks", type=int, default=None)
    parser.add_argument("--commission", type=float, default=None)
    parser.add_argument("--min-range-width", type=float, default=None)
    parser.add_argument("--sweep", action="store_true",
                        help="Run a parameter sweep")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    print(f"Fetching 5m futures data from yfinance (period={args.period})...")
    frames = fetch_futures_5m(symbols, period=args.period)
    if not frames:
        print("No data fetched. Exiting.")
        return

    # Build config from CLI overrides
    config = {**FUTURES_ORB_CONFIG}
    if args.stop_pct is not None:
        config["stop_pct"] = args.stop_pct
    if args.target_pct is not None:
        config["target_pct"] = args.target_pct
    if args.extension_filter is not None:
        config["extension_filter_pct"] = args.extension_filter
    if args.confirmation_minutes is not None:
        config["confirmation_minutes"] = args.confirmation_minutes
    if args.circuit_breaker is not None:
        config["circuit_breaker"] = args.circuit_breaker
    if args.max_positions is not None:
        config["max_positions"] = args.max_positions
    if args.slippage_ticks is not None:
        config["slippage_ticks"] = args.slippage_ticks
    if args.commission is not None:
        config["commission_per_side"] = args.commission
    if args.min_range_width is not None:
        config["min_range_width_pct"] = args.min_range_width

    if args.sweep:
        # Parameter sweep: stop/target grid + extension filter on/off
        print("\n=== FUTURES ORB PARAMETER SWEEP ===")
        stop_targets = [
            (0.2, 0.3), (0.2, 0.4), (0.3, 0.5), (0.3, 0.6),
            (0.4, 0.6), (0.4, 0.8), (0.5, 1.0),
        ]
        ext_filters = [0.0, 0.05, 0.1, 0.15, 0.2]
        print(f"{'Stop%':>6} {'Tgt%':>6} {'ExtFilt':>8} {'Return':>8} {'PF':>6} "
              f"{'WR':>5} {'MaxDD':>7} {'Trades':>7}")
        print("-" * 60)
        for ext_f in ext_filters:
            for stop, target in stop_targets:
                sweep_cfg = {**config, "stop_pct": stop, "target_pct": target,
                             "extension_filter_pct": ext_f}
                result = run_futures_orb_backtest(symbols, frames, args.capital, sweep_cfg)
                print(f"{stop:6.2f} {target:6.2f} {ext_f:8.2f} "
                      f"{result['total_return_pct']:+7.2f}% {result['profit_factor']:6.3f} "
                      f"{result['win_rate']:4.0f}% {result['max_drawdown_pct']:6.1f}% "
                      f"{result['total_trades']:7d}")
    else:
        result = run_futures_orb_backtest(symbols, frames, args.capital, config)
        print_report(result, "FUTURES ORB BACKTEST")

        # Show individual trades
        if result["trades"]:
            print(f"\n--- Individual Trades ({len(result['trades'])}) ---")
            print(f"{'#':>3} {'Symbol':>8} {'Side':>5} {'Entry':>10} {'Exit':>10} "
                  f"{'PnL':>10} {'Reason':>15} {'Bars':>5}")
            for i, t in enumerate(result["trades"]):
                print(f"{i+1:3d} {t.symbol:>8} {t.side:>5} "
                      f"{t.entry_price:10.2f} {t.exit_price:10.2f} "
                      f"${t.pnl:+9.2f} {t.reason:>15} {t.bars_held:5d}")


if __name__ == "__main__":
    main()
