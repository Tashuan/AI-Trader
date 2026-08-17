"""Black-Scholes options ORB backtester.

Uses the same ORB signal generation as orb_options_backtester.py, but
prices options theoretically via Black-Scholes instead of fetching
historical option bars. This allows backtesting across all date ranges
(including expired contracts) using only equity 1m bars + current IV.

Assumptions:
  - IV is fetched once from Schwab's live chain (current value as proxy)
  - IV held constant during the holding period (avg 2.8h)
  - Risk-free rate = 5% (configurable)
  - No dividends (close enough for short-term options on growth stocks)
  - Option price = BS theoretical value (no bid-ask spread modeling)
  - Slippage modeled as bps on theoretical price

Usage:
  cd agents
  python3 ../research/strategy_search/orb_options_bs_backtester.py \
    --symbols NVDA,TSLA,AAPL,META,COIN \
    --start 2026-04-01 --end 2026-08-16 \
    --strike-offset 1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time as time_mod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import time as dt_time, datetime, date as dt_date
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
from scalp_alt_signals import (
    fetch_1m_data, fetch_prev_closes, SLIPPAGE_BPS,
    DEFAULT_SYMBOLS, DEFAULT_START, DEFAULT_END,
)
from orb_options_backtester import (
    STRIKE_STEPS, _find_expiration, build_schwab_symbol,
    ORBSignalGenerator, ORB_CONFIG,
)

# ── Black-Scholes pricing ──────────────────────────────────────────────

# Standard normal CDF (no scipy needed)
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

# Standard normal PDF
def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes option price.

    S: underlying price
    K: strike price
    T: time to expiration in years
    r: risk-free rate (e.g. 0.05 for 5%)
    sigma: implied volatility (e.g. 0.50 for 50%)
    option_type: 'call' or 'put'
    """
    if T <= 0 or sigma <= 0:
        # At expiration: intrinsic value
        if option_type == "call":
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

    return max(price, 0.01)  # Floor at $0.01


def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes delta."""
    if T <= 0 or sigma <= 0:
        return 1.0 if (option_type == "call" and S > K) else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    if option_type == "call":
        return _norm_cdf(d1)
    else:
        return _norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_theta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str) -> float:
    """Black-Scholes theta (per day)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf_term = (-S * _norm_pdf(d1) * sigma) / (2.0 * math.sqrt(T))
    if option_type == "call":
        theta = pdf_term - r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        theta = pdf_term + r * K * math.exp(-r * T) * _norm_cdf(-d2)
    return theta / 365.0  # Convert annual to daily


# ── IV cache ───────────────────────────────────────────────────────────

class IVCache:
    """Caches implied volatility per symbol from Schwab's live chain.

    Fetches IV once per symbol at startup, then uses it as a constant
    for all backtest trades. This is an approximation — real IV varies
    by strike (volatility smile) and over time — but it's sufficient
    for validating the edge across regimes.
    """

    def __init__(self):
        self._iv: dict[str, float] = {}  # symbol -> average IV
        self._iv_by_strike: dict[str, dict[str, float]] = {}  # symbol -> {strike_key -> IV}

    def get_iv(self, symbol: str, strike: float, option_type: str,
               expiration: str) -> float:
        """Get IV for a specific contract, falling back to symbol average."""
        key = f"{option_type[0].upper()}{strike:.1f}_{expiration}"
        if symbol in self._iv_by_strike and key in self._iv_by_strike[symbol]:
            return self._iv_by_strike[symbol][key]
        return self._iv.get(symbol, 0.50)  # Default 50% if no data

    def fetch_ivs(self, symbols: list[str]):
        """Fetch current IV from Schwab chain for each symbol."""
        try:
            from schwab_options_provider import SchwabOptionsProvider
            provider = SchwabOptionsProvider()
        except Exception as e:
            print(f"  Warning: Could not init Schwab provider for IV: {e}")
            return

        for sym in symbols:
            try:
                chain = provider.get_chain(sym, strike_count=50)
                if not chain:
                    print(f"  {sym}: no chain data, using default IV=50%")
                    self._iv[sym] = 0.50
                    continue

                # Average IV across all contracts
                ivs = [c.iv for c in chain if c.iv > 0]
                if ivs:
                    self._iv[sym] = sum(ivs) / len(ivs)
                    print(f"  {sym}: avg IV={self._iv[sym]*100:.1f}% ({len(ivs)} contracts)")
                else:
                    self._iv[sym] = 0.50
                    print(f"  {sym}: no IV data, using default 50%")

                # Also cache per-strike IV for more accuracy
                strike_map = {}
                for c in chain:
                    if c.iv > 0:
                        k = f"{c.option_type[0].upper()}{c.strike:.1f}_{c.expiration}"
                        strike_map[k] = c.iv
                self._iv_by_strike[sym] = strike_map

            except Exception as e:
                print(f"  {sym}: IV fetch failed ({e}), using default 50%")
                self._iv[sym] = 0.50


# ── Position ───────────────────────────────────────────────────────────

@dataclass
class BSPosition:
    symbol: str
    option_type: str  # 'call' or 'put'
    strike: float
    expiration: str   # YYYY-MM-DD
    entry_ts: Any
    entry_price: float  # option entry price (theoretical)
    entry_underlying: float  # underlying price at entry
    stop_price: float  # underlying stop
    target_price: float  # underlying target
    qty: int  # contracts
    bars_held: int = 0
    iv: float = 0.0  # IV used for pricing
    entry_fee: float = 0.0


# ── Backtest ───────────────────────────────────────────────────────────

def run_bs_options_backtest(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    prev_closes: dict[str, dict],
    iv_cache: IVCache,
    capital: float = 10000.0,
    slippage_bps: float = SLIPPAGE_BPS,
    option_slippage_bps: float = 10.0,
    fee_rate: float = 0.0,
    config: dict = ORB_CONFIG,
    start_date: str = DEFAULT_START,
    end_date: str = DEFAULT_END,
    strike_offset: int = 0,
    dte_min: int = 2,
    dte_max: int = 14,
    risk_free_rate: float = 0.05,
    spy_frames: dict[str, pd.DataFrame] = None,
    use_spy_filter: bool = False,
    confirmation_minutes: int = 0,
    circuit_breaker: int = 0,
    min_entry_time: str = "09:30",
) -> dict:
    """Run options ORB backtest using Black-Scholes theoretical pricing.

    No historical option bars needed — options are priced via BS using
    current IV from Schwab as a constant.

    Risk management additions:
    - confirmation_minutes: Don't check stops for first N minutes after entry
      (filters whipsaws where breakout immediately reverses)
    - circuit_breaker: Stop trading after N consecutive losses in a day
      (prevents cascading drawdowns from losing streaks)
    - min_entry_time: Skip entries before this time (first 15 min are noisy)
    """
    max_positions = config.get("max_positions", 3)
    position_pct = config.get("position_pct", 30.0)
    min_entry_h, min_entry_m = map(int, min_entry_time.split(":"))
    min_entry_time_dt = dt_time(min_entry_h, min_entry_m)

    # Pre-build index lookups
    ts_to_idx: dict[str, dict] = {}
    day_groups: dict[str, dict] = {}
    all_dates = set()
    for sym, df in frames.items():
        ts_to_idx[sym] = {ts: i for i, ts in enumerate(df["Timestamp"])}
        day_groups[sym] = {d: g for d, g in df.groupby(df["Timestamp"].dt.date)}
        all_dates.update(day_groups[sym].keys())
    all_dates = sorted(all_dates)

    strategies: dict[str, ORBSignalGenerator] = {}
    positions: dict[str, BSPosition] = {}
    trades: list[TradeRecord] = []
    curve: list[dict] = []
    cash = capital
    first_ts = None
    last_ts = None
    diagnostics: dict[str, int] = defaultdict(int)

    # Pre-compute SPY opening direction per day (for regime filter)
    spy_direction: dict[Any, str] = {}  # date -> 'up'/'down'/'flat'
    if use_spy_filter and spy_frames and "SPY" in spy_frames:
        spy_df = spy_frames["SPY"]
        for d, g in spy_df.groupby(spy_df["Timestamp"].dt.date):
            morning = g[g["Timestamp"].dt.time <= dt_time(9, 35)]
            if len(morning) >= 2:
                ret = (morning.iloc[-1]["Close"] - morning.iloc[0]["Open"]) / morning.iloc[0]["Open"]
                if ret > 0.001:
                    spy_direction[d] = "up"
                elif ret < -0.001:
                    spy_direction[d] = "down"
                else:
                    spy_direction[d] = "flat"

    # Circuit breaker state: track consecutive losses per symbol per day
    current_trade_day = None
    day_loss_streaks: dict[str, int] = {}  # symbol -> consecutive losses

    for date in all_dates:
        if date != current_trade_day:
            current_trade_day = date
            day_loss_streaks = {}  # reset per day
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

                exit_reason = None
                # Confirmation period: don't check stops for first N minutes
                in_confirmation = pos.bars_held < confirmation_minutes

                if pos.option_type == "call":
                    # Long call = long underlying
                    # Take profit always honored, stop only after confirmation
                    if hi >= pos.target_price:
                        exit_reason = "take_profit"
                    elif not in_confirmation and lo <= pos.stop_price:
                        exit_reason = "stop_loss"
                else:
                    # Long put = short underlying
                    if lo <= pos.target_price:
                        exit_reason = "take_profit"
                    elif not in_confirmation and hi >= pos.stop_price:
                        exit_reason = "stop_loss"

                if exit_reason is None and ts.time() >= dt_time(15, 55):
                    exit_reason = "eod_close"

                if exit_reason is not None:
                    # Price option via BS at exit
                    T_exit = _time_to_expiry(ts, pos.expiration)
                    option_px = bs_price(
                        px, pos.strike, T_exit, risk_free_rate,
                        pos.iv, pos.option_type
                    )
                    fill_px = option_px * (1 - option_slippage_bps / 10000)
                    fee = fill_px * pos.qty * 100 * fee_rate
                    pnl = (fill_px - pos.entry_price) * pos.qty * 100 - fee - pos.entry_fee
                    cash += fill_px * pos.qty * 100 - fee

                    pnl_pct = pnl / (pos.entry_price * pos.qty * 100) * 100 if pos.entry_price > 0 else 0
                    hold_hours = pos.bars_held / 60.0
                    trades.append(TradeRecord(
                        symbol=f"{sym} {pos.option_type[:1].upper()}{pos.strike:.0f}",
                        side="long",
                        entry_date=pos.entry_ts, exit_date=str(ts),
                        entry_price=pos.entry_price, exit_price=fill_px,
                        quantity=pos.qty * 100, pnl=pnl, pnl_pct=pnl_pct,
                        hold_days=int(hold_hours / 24), hold_hours=hold_hours,
                        reason=exit_reason,
                    ))
                    diagnostics[exit_reason] += 1
                    # Track circuit breaker per symbol
                    if pnl <= 0:
                        day_loss_streaks[sym] = day_loss_streaks.get(sym, 0) + 1
                    else:
                        day_loss_streaks[sym] = 0
                    del positions[sym]

            # ── Equity calc (mark-to-market via BS) ───────────────
            equity = cash
            for sym, pos in positions.items():
                if sym in prices:
                    T = _time_to_expiry(ts, pos.expiration)
                    opt_px = bs_price(
                        prices[sym], pos.strike, T, risk_free_rate,
                        pos.iv, pos.option_type
                    )
                else:
                    opt_px = pos.entry_price
                equity += opt_px * pos.qty * 100
            curve.append({"date": str(ts), "equity": round(equity, 2)})

            # ── Entry signals ─────────────────────────────────────
            if len(positions) >= max_positions:
                continue
            if ts.time() >= dt_time(15, 50):
                continue

            # Skip entries before minimum entry time (first 15 min are noisy)
            if ts.time() < min_entry_time_dt:
                continue

            # Circuit breaker: stop trading a symbol after N consecutive losses
            if circuit_breaker > 0:
                # Check if ALL symbols are blocked
                all_blocked = all(
                    day_loss_streaks.get(s, 0) >= circuit_breaker
                    for s in symbols if s not in positions
                )
                if all_blocked:
                    diagnostics["circuit_breaker"] += 1
                    continue

            for sym in symbols:
                if sym in positions or sym not in bars:
                    continue
                strat = strategies.get(sym)
                if strat is None or strat.session_date != date:
                    continue
                # Per-symbol circuit breaker
                if circuit_breaker > 0 and day_loss_streaks.get(sym, 0) >= circuit_breaker:
                    diagnostics["cb_blocked_" + sym] += 1
                    continue
                day_df = day_bars_map.get(sym)
                if day_df is None or day_df.empty:
                    continue
                idx = len(day_df) - 1
                signal = strat.on_bar(ts, bars[sym], idx, day_df)
                if signal is None:
                    continue

                # ── SPY regime filter ────────────────────────────
                if use_spy_filter and date in spy_direction:
                    spy_dir = spy_direction[date]
                    if spy_dir == "up" and signal["side"] == "short":
                        diagnostics["spy_filtered"] += 1
                        continue
                    if spy_dir == "down" and signal["side"] == "long":
                        diagnostics["spy_filtered"] += 1
                        continue

                # ── Buy option via BS pricing ────────────────────
                spot = signal["entry_price"]
                option_type = "call" if signal["side"] == "long" else "put"
                strike_step = STRIKE_STEPS.get(sym, 2.5)

                expiry = _find_expiration(date, dte_min, dte_max)
                if expiry is None:
                    diagnostics["no_expiry"] += 1
                    continue

                atm_strike = round(spot / strike_step) * strike_step
                target_strike = atm_strike + strike_offset * strike_step

                # Get IV for this contract
                iv = iv_cache.get_iv(sym, target_strike, option_type, expiry)

                # Price option at entry via BS
                T_entry = _time_to_expiry(ts, expiry)
                option_entry = bs_price(
                    spot, target_strike, T_entry, risk_free_rate,
                    iv, option_type
                )

                if option_entry <= 0:
                    diagnostics["zero_option_price"] += 1
                    continue

                # Position sizing: use position_pct of equity for option premium
                notional = equity * position_pct / 100.0
                # Apply entry slippage (buy fills higher)
                fill_entry = option_entry * (1 + option_slippage_bps / 10000)
                qty = max(1, int(notional / (fill_entry * 100)))
                entry_fee = fill_entry * qty * 100 * fee_rate
                cost = fill_entry * qty * 100 + entry_fee
                if cost > cash:
                    qty = max(1, int(cash / (fill_entry * 100 + entry_fee)))
                    if qty < 1:
                        diagnostics["insufficient_capital"] += 1
                        continue
                    cost = fill_entry * qty * 100 + entry_fee
                cash -= cost

                stop_price = spot * (1 - config["stop_pct"] / 100) if option_type == "call" else spot * (1 + config["stop_pct"] / 100)
                target_price = spot * (1 + config["target_pct"] / 100) if option_type == "call" else spot * (1 - config["target_pct"] / 100)

                positions[sym] = BSPosition(
                    symbol=sym, option_type=option_type,
                    strike=target_strike, expiration=expiry,
                    entry_ts=ts, entry_price=fill_entry,
                    entry_underlying=spot,
                    stop_price=stop_price, target_price=target_price,
                    qty=qty, iv=iv, entry_fee=entry_fee,
                )
                diagnostics["entries"] += 1

    # Close any remaining positions at last known price
    for sym, pos in positions.items():
        T = _time_to_expiry(last_ts, pos.expiration)
        last_px = pos.entry_underlying  # fallback
        option_px = bs_price(last_px, pos.strike, T, risk_free_rate, pos.iv, pos.option_type)
        fill_px = option_px * (1 - option_slippage_bps / 10000)
        fee = fill_px * pos.qty * 100 * fee_rate
        pnl = (fill_px - pos.entry_price) * pos.qty * 100 - fee - pos.entry_fee
        cash += fill_px * pos.qty * 100 - fee
        pnl_pct = pnl / (pos.entry_price * pos.qty * 100) * 100 if pos.entry_price > 0 else 0
        trades.append(TradeRecord(
            symbol=f"{sym} {pos.option_type[:1].upper()}{pos.strike:.0f}",
            side="long",
            entry_date=pos.entry_ts, exit_date=str(last_ts),
            entry_price=pos.entry_price, exit_price=fill_px,
            quantity=pos.qty * 100, pnl=pnl, pnl_pct=pnl_pct,
            hold_days=0, hold_hours=pos.bars_held / 60.0,
            reason="force_close",
        ))
        diagnostics["force_close"] += 1

    # Build report
    final_equity = cash
    for sym, pos in positions.items():
        # Should be empty by now, but just in case
        final_equity += pos.entry_price * pos.qty * 100

    report = BacktestReport.calculate_metrics(
        agent_name="orb_options_bs",
        symbols=symbols,
        start_date=start_date, end_date=end_date,
        initial_capital=capital, final_equity=final_equity,
        equity_curve=curve, trades=trades,
        interval="1m", slippage_bps=slippage_bps,
    )
    r = report.to_dict()
    r["diagnostics"] = dict(diagnostics)
    r["config"] = {
        "strike_offset": strike_offset, "dte_min": dte_min,
        "dte_max": dte_max, "position_pct": position_pct,
        "risk_free_rate": risk_free_rate,
        "option_slippage_bps": option_slippage_bps,
        "pricing_model": "black_scholes",
    }
    return r


def _time_to_expiry(ts: pd.Timestamp, expiration: str) -> float:
    """Compute time to expiration in years from a timestamp."""
    exp_date = datetime.fromisoformat(expiration)
    # Options expire at 16:00 ET on expiration date
    exp_dt = datetime.combine(exp_date, dt_time(16, 0))
    if hasattr(ts, 'to_pydatetime'):
        ts_dt = ts.to_pydatetime()
    else:
        ts_dt = datetime.fromisoformat(str(ts))
    delta = (exp_dt - ts_dt).total_seconds()
    if delta <= 0:
        return 0.0
    return delta / (365.25 * 24 * 3600)


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Black-Scholes options ORB backtester")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--slippage", type=float, default=10.0, help="Option slippage in bps")
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--strike-offset", type=int, default=0)
    parser.add_argument("--dte-min", type=int, default=2)
    parser.add_argument("--dte-max", type=int, default=14)
    parser.add_argument("--risk-free-rate", type=float, default=0.05)
    parser.add_argument("--zero-cost", action="store_true")
    parser.add_argument("--json", type=str, default="")
    parser.add_argument("--no-iv-fetch", action="store_true",
                        help="Skip Schwab IV fetch, use default 50%")
    parser.add_argument("--spy-filter", action="store_true",
                        help="Enable SPY opening direction regime filter")
    parser.add_argument("--confirmation-min", type=int, default=0,
                        help="Don't check stops for first N minutes after entry")
    parser.add_argument("--circuit-breaker", type=int, default=0,
                        help="Stop trading after N consecutive losses in a day")
    parser.add_argument("--min-entry-time", type=str, default="09:30",
                        help="Skip entries before this time (HH:MM)")
    parser.add_argument("--position-pct", type=float, default=0,
                        help="Override position size (0 = use config default 30%)")
    parser.add_argument("--stop-pct", type=float, default=0,
                        help="Override stop percentage (0 = use config default 0.7%)")
    parser.add_argument("--target-pct", type=float, default=0,
                        help="Override target percentage (0 = use config default 1.2%)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    opt_slippage = 0.0 if args.zero_cost else args.slippage
    fee_rate = 0.0 if args.zero_cost else args.fee_rate

    print(f"\nBlack-Scholes Options ORB Backtester")
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Date range:    {args.start} → {args.end}")
    print(f"  Capital:       ${args.capital:,.0f}")
    print(f"  Option slip:   {opt_slippage} bps")
    print(f"  Fee rate:      {fee_rate}")
    print(f"  Strike offset: {args.strike_offset}")
    print(f"  DTE range:     {args.dte_min}-{args.dte_max} days")
    print(f"  Risk-free rate: {args.risk_free_rate}")
    print(f"  Pricing model: Black-Scholes (constant IV)")
    print(f"  Mode:          {'zero-cost' if args.zero_cost else 'realistic costs'}")
    print()

    # Fetch IV from Schwab
    iv_cache = IVCache()
    if args.no_iv_fetch:
        print("  IV: Using default 50% for all symbols (--no-iv-fetch)")
        for sym in symbols:
            iv_cache._iv[sym] = 0.50
    else:
        print("  Fetching IV from Schwab chain...")
        iv_cache.fetch_ivs(symbols)
    print()

    # Fetch equity data
    print("  Fetching 1m equity data...")
    provider = CachedProvider(AlpacaProvider())
    frames = fetch_1m_data(symbols, args.start, args.end, provider)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    prev_closes = fetch_prev_closes(symbols, all_dates, provider)

    # Fetch SPY data for regime filter
    spy_frames = None
    if args.spy_filter:
        print("  Fetching SPY 1m data for regime filter...")
        spy_frames = fetch_1m_data(["SPY"], args.start, args.end, provider)
    print()

    # Build config with overrides
    config = dict(ORB_CONFIG)
    if args.position_pct > 0:
        config["position_pct"] = args.position_pct
    if args.stop_pct > 0:
        config["stop_pct"] = args.stop_pct
    if args.target_pct > 0:
        config["target_pct"] = args.target_pct

    # Run backtest
    print("  Running BS options ORB backtest...")
    t0 = time_mod.time()
    result = run_bs_options_backtest(
        symbols=symbols, frames=frames, prev_closes=prev_closes,
        iv_cache=iv_cache, capital=args.capital,
        slippage_bps=SLIPPAGE_BPS, option_slippage_bps=opt_slippage,
        fee_rate=fee_rate, config=config,
        start_date=args.start, end_date=args.end,
        strike_offset=args.strike_offset, dte_min=args.dte_min,
        dte_max=args.dte_max, risk_free_rate=args.risk_free_rate,
        spy_frames=spy_frames, use_spy_filter=args.spy_filter,
        confirmation_minutes=args.confirmation_min,
        circuit_breaker=args.circuit_breaker,
        min_entry_time=args.min_entry_time,
    )
    elapsed = time_mod.time() - t0

    # Print results
    print()
    print("=" * 70)
    status = "PASS" if result["total_return_pct"] > 0 else "FAIL"
    print(f"  orb_options_bs  [{status}]")
    print("=" * 70)
    print(f"  Return:       {result['total_return_pct']:+.2f}%")
    print(f"  Profit Factor: {result['profit_factor']:.3f}")
    print(f"  Win Rate:     {result['win_rate']:.0%}  ({result['total_trades']} trades)")
    print(f"  Max DD:       {result["max_drawdown_pct"]:.2f}%")
    print(f"  Sharpe:       {result["sharpe_ratio"]:.3f}")
    print(f"  Avg Hold:     {result['avg_hold_hours']:.1f}h")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Runtime:      {elapsed:.1f}s")
    print()
    print("  Diagnostics:")
    for k, v in result.get("diagnostics", {}).items():
        print(f"    {k}: {v}")
    print()

    # Per-symbol
    ps = result.get("per_symbol_stats", {})
    if ps:
        print("  --- Per-Symbol ---")
        for sym in symbols:
            s = ps.get(sym, {})
            print(f"    {sym:8s}: {s.get('trades',0):3d} trades, "
                  f"WR={s.get('win_rate',0):.0%}, "
                  f"PnL=${s.get('total_pnl',0):+,.2f}, "
                  f"avg={s.get('avg_pnl_pct',0):+.2f}%")
        print()

    # Sample trades
    if result.get("trades"):
        print("  --- Sample Trades (first 10) ---")
        for t in result["trades"][:10]:
            if isinstance(t, dict):
                print(f"    {t.get('symbol',''):12s} {t.get('side',''):5s} "
                      f"pnl=${t.get('pnl',0):+8.2f} ({t.get('pnl_pct',0):+6.2f}%) "
                      f"hold={t.get('hold_hours',0):.1f}h reason={t.get('reason','')}")
            else:
                print(f"    {t.symbol:12s} {t.side:5s} "
                      f"pnl=${t.pnl:+8.2f} ({t.pnl_pct:+6.2f}%) "
                      f"hold={t.hold_hours:.1f}h reason={t.reason}")
        print()

    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"  Results saved to: {args.json}")


if __name__ == "__main__":
    main()
