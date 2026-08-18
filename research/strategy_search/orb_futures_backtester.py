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
  python3 research/strategy_search/orb_futures_backtester.py
  python3 research/strategy_search/orb_futures_backtester.py \
    --symbols MES=F,MNQ=F,M2K=F,MYM=F \
    --risk-pct 1.0 --stop-range-multiplier 0.5 --target-r 2.0
  python3 research/strategy_search/orb_futures_backtester.py \
    --extension-filter 0.05
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

from orb_strategy import (
    BreakoutChecker,
    IntrabarPolicy,
    OpeningRangeBuilder,
    ORBStrategyConfig,
    RangeEndPolicy,
)

# ── Contract specifications ─────────────────────────────────────────────

@dataclass(frozen=True)
class FuturesContract:
    yf_symbol: str       # yfinance ticker (e.g. "ES=F")
    name: str
    multiplier: float    # $ per 1.0 price move
    tick_size: float     # minimum price increment
    tick_value: float    # $ per tick (= multiplier * tick_size)


CONTRACTS: dict[str, FuturesContract] = {
    # Micro contracts are the research default for a $10,000 account.
    "MES=F": FuturesContract("MES=F", "Micro E-mini S&P 500", 5.0, 0.25, 1.25),
    "MNQ=F": FuturesContract("MNQ=F", "Micro E-mini Nasdaq 100", 2.0, 0.25, 0.50),
    "M2K=F": FuturesContract("M2K=F", "Micro E-mini Russell 2000", 5.0, 0.10, 0.50),
    "MYM=F": FuturesContract("MYM=F", "Micro E-mini Dow", 0.50, 1.0, 0.50),
    "ES=F": FuturesContract("ES=F", "E-mini S&P 500", 50.0, 0.25, 12.50),
    "NQ=F": FuturesContract("NQ=F", "E-mini Nasdaq 100", 20.0, 0.25, 5.00),
    "CL=F": FuturesContract("CL=F", "Crude Oil WTI", 1000.0, 0.01, 10.00),
    "GC=F": FuturesContract("GC=F", "Gold", 100.0, 0.10, 10.00),
    "RTY=F": FuturesContract("RTY=F", "E-mini Russell 2000", 50.0, 0.10, 5.00),
    "YM=F": FuturesContract("YM=F", "E-mini Dow", 5.0, 1.0, 5.00),
}

# ── Default config ──────────────────────────────────────────────────────

FUTURES_ORB_CONFIG = {
    "config_version": "futures-orb-baseline-v0.1",
    "bar_interval_minutes": 5,
    "range_minutes": 5,
    "range_end_policy": "exclusive",
    "confirmation_bars": 2,
    "skip_first_post_range_bar": True,
    "latest_entry": "10:00",
    "min_entry_time": "09:30",
    "max_positions": 1,
    "risk_per_trade_pct": 0.5,
    "max_contracts": 4,
    "stop_model": "range_width",
    "stop_range_multiplier": 1.0,
    "stop_ticks": 8,
    "target_ticks": 12,
    "target_r_multiple": 2.0,
    # Optional legacy percentage model for comparison only.
    "stop_pct": 0.2,
    "target_pct": 0.4,
    "confirmation_minutes": 10,
    "circuit_breaker": 3,
    "extension_filter_pct": 0.0,
    "min_range_width_pct": 0.0,
    "force_exit_time": "15:55",
    "market_open": "09:30",
    "slippage_ticks": 1,
    "commission_per_side": 2.50,
}

# ── Data fetching ───────────────────────────────────────────────────────

def fetch_futures_5m(
    symbols: list[str],
    period: str = "60d",
    provider: str = "yfinance",
    start: str | None = None,
    end: str | None = None,
    interval: str = "5m",
) -> dict[str, pd.DataFrame]:
    """Fetch intraday bars from yfinance or Massive, filtered to RTH."""
    if provider == "massive":
        if not start or not end:
            raise ValueError("Massive provider requires --start and --end")
        from massive_futures_data import fetch_massive_futures_bars
        return fetch_massive_futures_bars(symbols, start, end, resolution=interval)
    import yfinance as yf
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(period=period, interval=interval)
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

class CanonicalFuturesSignalAdapter:
    """Adapt canonical ORB signals to futures bars without I/O."""

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self.strategy = ORBStrategyConfig(
            range_minutes=config.get("range_minutes", 5),
            range_end_policy=RangeEndPolicy(
                config.get("range_end_policy", "exclusive")
            ),
            latest_entry=config.get("latest_entry", "10:00"),
            min_entry_time=config.get("min_entry_time", "09:30"),
            confirmation_bars=config.get("confirmation_bars", 2),
            skip_first_post_range_bar=config.get(
                "skip_first_post_range_bar", True
            ),
            confirmation_minutes=config.get("confirmation_minutes", 10),
            max_signal_age_seconds=10**9,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        )
        self.builder = OpeningRangeBuilder(self.strategy)
        self.checker = BreakoutChecker(self.strategy)
        self.orb_range = None

    def reset(self) -> None:
        self.checker.reset()
        self.orb_range = None

    def on_bar(self, ts: datetime, bar: dict, day_bars: pd.DataFrame):
        if self.orb_range is None:
            records = day_bars[
                ["Timestamp", "Open", "High", "Low", "Close"]
            ].to_dict("records")
            candidate = self.builder.build(self.symbol, records)
            if candidate and ts.time() > candidate.range_end_ts.time():
                self.orb_range = candidate
        if self.orb_range is None:
            return None
        return self.checker.check(
            self.symbol, {**bar, "Timestamp": ts}, self.orb_range,
            current_ts=ts,
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
    bar_minutes = config.get("bar_interval_minutes", 5)
    in_confirmation = pos.bars_held * bar_minutes < confirm_mins

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
    max_contracts = config["max_contracts"]
    risk_per_trade_pct = config["risk_per_trade_pct"]
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
    diagnostics = {
        "days": len(all_dates),
        "signals": 0,
        "extension_rejections": 0,
        "sizing_rejections": 0,
        "missing_ranges": 0,
    }
    strategies = {
        sym: CanonicalFuturesSignalAdapter(sym, config) for sym in symbols
    }

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

        for strategy in strategies.values():
            strategy.reset()

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
                    net_pnl = gross_pnl - exit_commission - pos.entry_cost
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

            signal = strategies[sym].on_bar(ts, bar, day_groups[sym][date])
            if signal is None:
                continue
            diagnostics["signals"] += 1
            rh, rl = signal.range_high, signal.range_low
            extension = (
                (signal.entry_price - rh) / rh * 100
                if signal.side == "long"
                else (rl - signal.entry_price) / rl * 100
            )
            if extension > config["extension_filter_pct"] > 0:
                diagnostics["extension_rejections"] += 1
                continue

            contract = CONTRACTS.get(sym)
            if contract is None:
                continue

            entry_price = apply_slippage(
                signal.entry_price, signal.side, is_entry=True,
                contract=contract, slippage_ticks=slippage_ticks,
            )
            entry_price = round_to_tick(entry_price, contract)
            range_width = rh - rl
            if config["stop_model"] == "ticks":
                stop_dist = contract.tick_size * config["stop_ticks"]
                target_dist = contract.tick_size * config["target_ticks"]
            else:
                stop_dist = range_width * config["stop_range_multiplier"]
                if config["stop_model"] == "percentage":
                    stop_dist = signal.entry_price * config.get("stop_pct", 0.2) / 100
                target_dist = stop_dist * config["target_r_multiple"]
            stop_price = (
                signal.entry_price - stop_dist
                if signal.side == "long" else signal.entry_price + stop_dist
            )
            target_price = (
                signal.entry_price + target_dist
                if signal.side == "long" else signal.entry_price - target_dist
            )
            stop_price = round_to_tick(stop_price, contract)
            target_price = round_to_tick(target_price, contract)
            stop_ticks = max(1, round(abs(entry_price - stop_price) / contract.tick_size))
            risk_per_contract = (
                stop_ticks * contract.tick_value
                + 2 * commission
                + 2 * slippage_ticks * contract.tick_value
            )
            risk_budget = equity * risk_per_trade_pct / 100
            qty = min(max_contracts, int(risk_budget / risk_per_contract))
            if qty < 1:
                diagnostics["sizing_rejections"] += 1
                continue
            entry_commission = commission * qty

            pos = FuturesPosition(
                symbol=sym, side=signal.side, entry_ts=ts,
                entry_price=entry_price, raw_entry=signal.entry_price,
                stop_price=stop_price, target_price=target_price,
                qty=qty, bars_held=0, entry_cost=entry_commission,
            )
            positions.append(pos)

        # Force-exit any remaining positions at end of day
        for pos in positions[:]:
            # Find the last bar for this symbol on this date
            if pos.symbol not in day_groups or date not in day_groups[pos.symbol]:
                continue
            df = day_groups[pos.symbol][date]
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
            net_pnl = gross_pnl - exit_commission - pos.entry_cost
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
        "diagnostics": diagnostics,
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
    stop_desc = (
        f"{cfg['stop_range_multiplier']}x range"
        if cfg.get("stop_model") == "range_width"
        else f"{cfg.get('stop_pct', 0)}%"
    )
    print(f"  Stop={stop_desc}  Target={cfg['target_r_multiple']}R  "
          f"ExtFilter={cfg['extension_filter_pct']}%  "
          f"Risk={cfg['risk_per_trade_pct']}%  "
          f"Confirm={cfg['confirmation_minutes']}m")
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
    diag = result.get("diagnostics", {})
    print(f"  Signals:        {diag.get('signals', 0)}  "
          f"Size rejects: {diag.get('sizing_rejections', 0)}  "
          f"Ext rejects: {diag.get('extension_rejections', 0)}")
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
    parser.add_argument("--symbols", default="MES=F,MNQ=F,M2K=F,MYM=F",
                        help="Comma-separated futures symbols")
    parser.add_argument("--provider", choices=["yfinance", "massive"], default="yfinance")
    parser.add_argument("--interval", choices=["1m", "5m"], default="5m")
    parser.add_argument("--period", default="60d",
                        help="yfinance history period (5m max is ~60d)")
    parser.add_argument("--start", default=None, help="Massive start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="Massive end date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--risk-pct", type=float, default=None)
    parser.add_argument("--stop-range-multiplier", type=float, default=None)
    parser.add_argument("--stop-ticks", type=int, default=None)
    parser.add_argument("--target-ticks", type=int, default=None)
    parser.add_argument("--target-r", type=float, default=None)
    parser.add_argument("--max-contracts", type=int, default=None)
    parser.add_argument("--stop-pct", type=float, default=None,
                        help="Legacy percentage stop; enables percentage stop model")
    parser.add_argument("--target-pct", type=float, default=None,
                        help="Legacy percentage target")
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
    source_desc = (
        f"Massive ({args.start} to {args.end})"
        if args.provider == "massive"
        else f"yfinance (period={args.period})"
    )
    print(f"Fetching 5m futures data from {source_desc}...")
    frames = fetch_futures_5m(
        symbols, period=args.period, provider=args.provider,
        start=args.start, end=args.end, interval=args.interval,
    )
    if not frames:
        print("No data fetched. Exiting.")
        return

    # Build config from CLI overrides
    config = {**FUTURES_ORB_CONFIG, "bar_interval_minutes": int(args.interval.rstrip("m"))}
    if args.risk_pct is not None:
        config["risk_per_trade_pct"] = args.risk_pct
    if args.stop_range_multiplier is not None:
        config["stop_range_multiplier"] = args.stop_range_multiplier
    if args.stop_ticks is not None:
        config["stop_model"] = "ticks"
        config["stop_ticks"] = args.stop_ticks
    if args.target_ticks is not None:
        config["target_ticks"] = args.target_ticks
    if args.target_r is not None:
        config["target_r_multiple"] = args.target_r
    if args.max_contracts is not None:
        config["max_contracts"] = args.max_contracts
    if args.stop_pct is not None:
        config["stop_model"] = "percentage"
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
