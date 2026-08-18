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
  - Option price = BS theoretical mid-price
  - Bid/ask spread and adverse slippage are configurable fill assumptions
  - Contract fees are configurable per-contract, per-side

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
# Phase 5: import canonical strategy core
from orb_strategy import (
    check_exit as canonical_check_exit,
    IntrabarPolicy,
    ORBStrategyConfig,
    StrategyMode,
    RangeEndPolicy,
    OpeningRangeBuilder,
    BreakoutChecker,
    select_strike as canonical_select_strike,
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


def option_fill_price(
    mid_price: float,
    is_entry: bool,
    option_slippage_bps: float,
    option_spread_bps: float,
) -> float:
    """Model market fills from a BS mid-price.

    option_spread_bps is the full bid/ask spread. Entries pay the ask and
    exits receive the bid; slippage is adverse on top of the half-spread.
    """
    half_spread = option_spread_bps / 20000.0
    slippage = option_slippage_bps / 10000.0
    multiplier = 1.0 + half_spread + slippage if is_entry else 1.0 - half_spread - slippage
    return max(0.01, mid_price * multiplier)


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


class CanonicalSignalAdapter:
    """Incremental adapter from canonical ORB signals to dicts."""

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = ORBStrategyConfig(
            strategy_mode=StrategyMode(config.get("strategy_mode", "symmetric_otm")),
            range_minutes=config.get("range_minutes", 5),
            range_end_policy=RangeEndPolicy(config.get("range_end_policy", "exclusive")),
            latest_entry=config.get("latest_entry", "10:30"),
            stop_pct=config.get("stop_pct", 1.0),
            target_pct=config.get("target_pct", 1.5),
            confirmation_minutes=config.get("confirmation_minutes", 10),
            confirmation_bars=config.get("confirmation_bars", 1),
            skip_first_post_range_bar=config.get("skip_first_post_range_bar", False),
            min_entry_time=config.get("min_entry_time", "09:30"),
            max_signal_age_seconds=10**9,
        )
        self.builder = OpeningRangeBuilder(self.config)
        self.checker = BreakoutChecker(self.config)
        self.session_date = None
        self.orb_range = None

    def reset(self, date) -> None:
        self.session_date = date
        self.orb_range = None
        self.checker.reset()

    def on_bar(self, ts, bar, idx, day_bars) -> dict | None:
        if self.orb_range is None:
            records = day_bars[["Timestamp", "Open", "High", "Low", "Close"]].to_dict("records")
            candidate = self.builder.build(self.symbol, records)
            if candidate is not None and ts.time() > candidate.range_end_ts.time():
                self.orb_range = candidate
        if self.orb_range is None:
            return None
        signal = self.checker.check(self.symbol, bar.to_dict(), self.orb_range, current_ts=ts)
        if signal is None:
            return None
        return {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "range_high": signal.range_high,
            "range_low": signal.range_low,
            "ts": signal.signal_timestamp_str,
        }


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
    discovery_config: dict = None,
    premarket_changes: dict[Any, dict[str, float]] | None = None,
    frozen_symbols: dict[Any, list[str]] | None = None,
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.LEGACY,
    option_spread_bps: float = 0.0,
    option_spread_by_symbol: dict[str, float] | None = None,
    contract_fee: float = 0.0,
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

    Signal extensions (read from config dict):
    - confirmation_bars: N consecutive closes outside range (default 1)
    - min_range_width_pct: skip symbols with narrow opening ranges (default 0.0)
    - skip_first_post_range_bar: never enter on first bar after range (default False)
    - range_end_policy: "inclusive" (legacy) or "exclusive" (09:35 is first breakout bar)

    Dynamic discovery:
    - discovery_config: dict with universe, max_symbols, and min_change_pct.
    - premarket_changes: date -> symbol -> percent change measured from the
      last pre-09:30 quote versus previous close. Required with discovery_config;
      the backtester refuses to use the 09:30 opening print for discovery.
    - frozen_symbols: date -> already-selected symbols. When supplied, no
      discovery occurs inside the replay loop.
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
    start_d = dt_date.fromisoformat(start_date)
    end_d = dt_date.fromisoformat(end_date)
    all_dates = sorted(d for d in all_dates if start_d <= d <= end_d)

    # ── Frozen discovery: selection is completed before replay ───────
    day_symbols: dict[Any, list[str]] = {}
    if frozen_symbols is not None:
        day_symbols = {
            date: [sym for sym in frozen_symbols.get(date, []) if sym in frames]
            for date in all_dates
        }
        active_symbols = symbols
    elif discovery_config:
        if premarket_changes is None:
            raise ValueError(
                "premarket_changes is required for dynamic discovery; "
                "09:30 opening prices are not valid pre-market inputs"
            )
        disc_universe = discovery_config.get("universe", symbols)
        disc_max = discovery_config.get("max_symbols", 8)
        disc_min_change = discovery_config.get("min_change_pct", 1.0)
        disc_exclude = set(discovery_config.get("exclude_symbols", []))
        for date in all_dates:
            changes = premarket_changes.get(date, {})
            movers = [
                (sym, abs(float(changes[sym])), float(changes[sym]))
                for sym in disc_universe
                if sym not in disc_exclude
                and sym in frames
                and sym in changes
                and abs(float(changes[sym])) >= disc_min_change
            ]
            movers.sort(key=lambda x: x[1], reverse=True)
            day_symbols[date] = [m[0] for m in movers[:disc_max]]
        active_symbols = disc_universe
    else:
        active_symbols = symbols
        for date in all_dates:
            day_symbols[date] = list(symbols)

    strategies: dict[str, CanonicalSignalAdapter] = {}
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
                strategies[sym] = CanonicalSignalAdapter(sym, config)
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

                # Phase 5: Use canonical check_exit from orb_strategy.py
                # BSPosition has option_type not side: call=long, put=short
                pos_side = "long" if pos.option_type == "call" else "short"
                exit_reason = canonical_check_exit(
                    side=pos_side,
                    current_high=hi,
                    current_low=lo,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    in_confirmation=in_confirmation,
                    intrabar_policy=intrabar_policy,
                )

                if exit_reason is None and ts.time() >= dt_time(15, 55):
                    exit_reason = "eod_close"

                if exit_reason is not None:
                    # Stop/target exits fill at the touched underlying level;
                    # only EOD exits use the bar close.
                    exit_spot = px
                    if exit_reason == "take_profit":
                        exit_spot = pos.target_price
                    elif exit_reason == "stop_loss":
                        exit_spot = pos.stop_price
                    T_exit = _time_to_expiry(ts, pos.expiration)
                    option_px = bs_price(
                        exit_spot, pos.strike, T_exit, risk_free_rate,
                        pos.iv, pos.option_type
                    )
                    fill_px = option_fill_price(
                        option_px, is_entry=False,
                        option_slippage_bps=option_slippage_bps,
                        option_spread_bps=(option_spread_by_symbol or {}).get(
                            pos.symbol, option_spread_bps
                        ),
                    )
                    fee = fill_px * pos.qty * 100 * fee_rate + contract_fee * pos.qty
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
            current_day_syms = day_symbols.get(date, [])
            if circuit_breaker > 0:
                # Check if ALL symbols are blocked
                all_blocked = all(
                    day_loss_streaks.get(s, 0) >= circuit_breaker
                    for s in current_day_syms if s not in positions
                )
                if all_blocked:
                    diagnostics["circuit_breaker"] += 1
                    continue

            for sym in current_day_syms:
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

                # ── Entry-quality filters using current information ──
                premarket_change = (premarket_changes or {}).get(date, {}).get(sym)
                if config.get("require_premarket_alignment") and premarket_change is not None:
                    aligned = (signal["side"] == "long" and premarket_change > 0) or (
                        signal["side"] == "short" and premarket_change < 0
                    )
                    if not aligned:
                        diagnostics["premarket_misaligned"] += 1
                        continue

                max_extension = config.get("max_entry_extension_pct", 0.0)
                if max_extension > 0:
                    edge = signal["range_high"] if signal["side"] == "long" else signal["range_low"]
                    extension_pct = abs(signal["entry_price"] - edge) / signal["entry_price"] * 100
                    if extension_pct > max_extension:
                        diagnostics["overextended_entry"] += 1
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

                strategy_mode = config.get("strategy_mode", "symmetric_otm")
                strike_config = ORBStrategyConfig(
                    strategy_mode=(
                        StrategyMode.SYMMETRIC_OTM
                        if strategy_mode == "symmetric_otm"
                        else StrategyMode.LEGACY_PLUS_STRIKE
                    ),
                    strike_offset=strike_offset,
                )
                target_strike = canonical_select_strike(
                    spot, option_type, sym, strike_config
                )

                # Get IV for this contract
                iv = iv_cache.get_iv(sym, target_strike, option_type, expiry)

                # Price option at entry via BS
                T_entry = _time_to_expiry(ts, expiry)
                option_entry = bs_price(
                    spot, target_strike, T_entry, risk_free_rate,
                    iv, option_type
                )

                min_option_price = config.get("min_option_entry_price", 0.0)
                if option_entry <= 0 or option_entry < min_option_price:
                    diagnostics["option_price_filter"] += 1
                    continue

                # Position sizing: use position_pct of equity for option premium
                notional = equity * position_pct / 100.0
                # Apply entry slippage (buy fills higher)
                fill_entry = option_fill_price(
                    option_entry, is_entry=True,
                    option_slippage_bps=option_slippage_bps,
                    option_spread_bps=(option_spread_by_symbol or {}).get(
                        sym, option_spread_bps
                    ),
                )
                qty = max(1, int(notional / (fill_entry * 100)))
                entry_fee = fill_entry * qty * 100 * fee_rate + contract_fee * qty
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
        fill_px = option_fill_price(
            option_px, is_entry=False,
            option_slippage_bps=option_slippage_bps,
            option_spread_bps=(option_spread_by_symbol or {}).get(
                pos.symbol, option_spread_bps
            ),
        )
        fee = fill_px * pos.qty * 100 * fee_rate + contract_fee * pos.qty
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
        "option_spread_bps": option_spread_bps,
        "option_spread_by_symbol": option_spread_by_symbol,
        "contract_fee": contract_fee,
        "intrabar_policy": intrabar_policy.value,
        "strategy_mode": config.get("strategy_mode", "symmetric_otm"),
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
    parser.add_argument("--slippage", type=float, default=10.0, help="Adverse option slippage per fill in bps")
    parser.add_argument("--spread", type=float, default=0.0, help="Full option bid/ask spread in bps")
    parser.add_argument("--contract-fee", type=float, default=0.0, help="Fee per contract per side")
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
    parser.add_argument("--intrabar-policy", choices=["legacy", "conservative"], default="legacy")
    parser.add_argument("--strategy-mode", choices=["legacy_plus_strike", "symmetric_otm"], default="symmetric_otm")
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
    config["strategy_mode"] = args.strategy_mode

    # Run backtest
    print("  Running BS options ORB backtest...")
    t0 = time_mod.time()
    result = run_bs_options_backtest(
        symbols=symbols, frames=frames, prev_closes=prev_closes,
        iv_cache=iv_cache, capital=args.capital,
        slippage_bps=SLIPPAGE_BPS, option_slippage_bps=opt_slippage,
        option_spread_bps=args.spread, contract_fee=args.contract_fee,
        fee_rate=fee_rate, config=config,
        start_date=args.start, end_date=args.end,
        strike_offset=args.strike_offset, dte_min=args.dte_min,
        dte_max=args.dte_max, risk_free_rate=args.risk_free_rate,
        spy_frames=spy_frames, use_spy_filter=args.spy_filter,
        confirmation_minutes=args.confirmation_min,
        circuit_breaker=args.circuit_breaker,
        min_entry_time=args.min_entry_time,
        intrabar_policy=(IntrabarPolicy.CONSERVATIVE
                         if args.intrabar_policy == "conservative"
                         else IntrabarPolicy.LEGACY),
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
