"""Multi-strategy 1m scalping backtest — alternative signal types.

Tests 5 fundamentally different signal types on 1m bars, none of which
use indicator confluence (SMA/RSI/MACD alignment). These are structural
and statistical signals:

  1. Gap Fade       — fade opening gaps > threshold, target = prev close
  2. VWAP Reversion — fade price >X% from VWAP, target = VWAP
  3. Vol Spike      — fade bars that move >N std dev, target = reversion
  4. ORB            — opening range breakout (first 15min), trend follow
  5. Momentum Burst — ride sharp velocity spikes, quick exit

All strategies use:
  - 1m bars from Alpaca (cached)
  - Zero commission (realistic for modern brokers)
  - 2 bps slippage (conservative for liquid mega-caps)
  - Multiple concurrent positions
  - $10,000 starting capital

Usage:
  cd agents
  python3 ../research/strategy_search/scalp_alt_signals.py
  python3 ../research/strategy_search/scalp_alt_signals.py --json results.json
  python3 ../research/strategy_search/scalp_alt_signals.py --strategy gap_fade
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import pandas as pd
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from backtest_report import BacktestReport, TradeRecord


# ── Config ─────────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
DEFAULT_START = "2026-06-15"
DEFAULT_END = "2026-08-16"
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)
SLIPPAGE_BPS = 2.0  # 0.02% — conservative for liquid mega-caps


# ── Signal Dataclass ───────────────────────────────────────────────────
@dataclass
class Signal:
    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    stop_price: float
    target_price: float
    timestamp: str
    reason: str = ""


# ── Position Dataclass ─────────────────────────────────────────────────
@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    stop_price: float
    target_price: float
    qty: float
    entry_ts: str
    entry_fee: float
    bars_held: int = 0
    max_favorable: float = 0.0  # max profit pct
    max_adverse: float = 0.0    # max loss pct


# ── Strategy Base ──────────────────────────────────────────────────────
class BaseStrategy:
    """Base class: one instance per symbol, reset each trading day."""

    name = "base"

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self.session_date = None

    def reset(self, date) -> None:
        self.session_date = date

    def on_bar(self, ts: pd.Timestamp, bar: pd.Series, idx: int,
               day_bars: pd.DataFrame) -> Optional[Signal]:
        """Return a Signal or None. Called once per 1m bar."""
        raise NotImplementedError


# ── 1. Gap Fade ────────────────────────────────────────────────────────
class GapFadeStrategy(BaseStrategy):
    """Fade opening gaps. If stock gaps up >threshold, short it.
    Target = previous close (gap fill). Stop = entry + stop_pct."""

    name = "gap_fade"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.prev_close = None
        self.entered = False

    def reset(self, date) -> None:
        super().reset(date)
        self.entered = False

    def set_prev_close(self, prev_close: float) -> None:
        self.prev_close = prev_close

    def on_bar(self, ts, bar, idx, day_bars):
        if self.entered or self.prev_close is None:
            return None
        if ts.time() < MARKET_OPEN or ts.time() > dt_time(9, 45):
            return None  # only trade first 15 minutes
        open_px = float(bar["Open"])
        gap_pct = (open_px - self.prev_close) / self.prev_close * 100
        threshold = self.config.get("min_gap_pct", 1.0)
        if abs(gap_pct) < threshold:
            return None
        side = "short" if gap_pct > 0 else "long"
        entry = float(bar["Close"])
        stop_dist = entry * self.config.get("stop_pct", 0.5) / 100
        stop = entry + stop_dist if side == "short" else entry - stop_dist
        target = self.prev_close  # gap fill
        if side == "short" and target >= entry:
            return None
        if side == "long" and target <= entry:
            return None
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts), f"gap_{gap_pct:.2f}%")


# ── 2. VWAP Reversion ──────────────────────────────────────────────────
class VWAPReversionStrategy(BaseStrategy):
    """Fade price when it deviates >X% from VWAP. Target = VWAP."""

    name = "vwap_reversion"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.pv = 0.0
        self.total_vol = 0.0
        self.entered = False
        self.cooldown = 0

    def reset(self, date) -> None:
        super().reset(date)
        self.pv = 0.0
        self.total_vol = 0.0
        self.entered = False
        self.cooldown = 0

    def _vwap(self, bar) -> float:
        tp = (float(bar["High"]) + float(bar["Low"]) + float(bar["Close"])) / 3
        vol = max(0.0, float(bar.get("Volume", 0)))
        self.pv += tp * vol
        self.total_vol += vol
        return self.pv / self.total_vol if self.total_vol > 0 else float(bar["Close"])

    def on_bar(self, ts, bar, idx, day_bars):
        if self.cooldown > 0:
            self.cooldown -= 1
        if self.entered:
            return None
        if ts.time() < dt_time(9, 45) or ts.time() > dt_time(15, 30):
            return None  # skip first 15 min (VWAP not established) and last 30 min
        if self.cooldown > 0:
            return None
        vwap = self._vwap(bar)
        close = float(bar["Close"])
        dev_pct = (close - vwap) / vwap * 100
        threshold = self.config.get("min_dev_pct", 0.3)
        if abs(dev_pct) < threshold:
            return None
        side = "short" if dev_pct > 0 else "long"
        entry = close
        stop_dist = entry * self.config.get("stop_pct", 0.5) / 100
        stop = entry + stop_dist if side == "short" else entry - stop_dist
        target = vwap
        if side == "short" and target >= entry:
            return None
        if side == "long" and target <= entry:
            return None
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts),
                      f"vwap_dev_{dev_pct:.2f}%")


# ── 3. Volatility Spike Mean Reversion ─────────────────────────────────
class VolSpikeStrategy(BaseStrategy):
    """Fade bars that move >N standard deviations from recent mean.
    Pure statistical anomaly detection — no indicators."""

    name = "vol_spike"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.closes: list[float] = []
        self.entered = False
        self.cooldown = 0

    def reset(self, date) -> None:
        super().reset(date)
        self.closes = []
        self.entered = False
        self.cooldown = 0

    def on_bar(self, ts, bar, idx, day_bars):
        if self.cooldown > 0:
            self.cooldown -= 1
        if self.entered or self.cooldown > 0:
            self.closes.append(float(bar["Close"]))
            return None
        self.closes.append(float(bar["Close"]))
        lookback = self.config.get("lookback", 20)
        if len(self.closes) < lookback + 2:
            return None
        if ts.time() < dt_time(9, 35) or ts.time() > dt_time(15, 45):
            return None
        recent = self.closes[-lookback - 1:-1]
        mean = sum(recent) / len(recent)
        std = math.sqrt(sum((x - mean) ** 2 for x in recent) / len(recent))
        if std <= 0:
            return None
        close = float(bar["Close"])
        z_score = (close - mean) / std
        threshold = self.config.get("z_threshold", 2.0)
        if abs(z_score) < threshold:
            return None
        side = "short" if z_score > 0 else "long"
        entry = close
        stop_dist = entry * self.config.get("stop_pct", 0.5) / 100
        stop = entry + stop_dist if side == "short" else entry - stop_dist
        # Target: reversion to mean (or partial reversion)
        reversion_frac = self.config.get("reversion_frac", 0.5)
        target = mean + (close - mean) * (1 - reversion_frac)
        if side == "short" and target >= entry:
            target = mean
        if side == "long" and target <= entry:
            target = mean
        if side == "short" and target >= entry:
            return None
        if side == "long" and target <= entry:
            return None
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts),
                      f"z={z_score:.2f}")


# ── 4. Opening Range Breakout ──────────────────────────────────────────
class ORBStrategy(BaseStrategy):
    """Trade breakout of the opening 15-minute range.
    After 9:45, if price breaks above range high → long, below range low → short."""

    name = "orb"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.range_high = None
        self.range_low = None
        self.entered = False

    def reset(self, date) -> None:
        super().reset(date)
        self.range_high = None
        self.range_low = None
        self.entered = False

    def on_bar(self, ts, bar, idx, day_bars):
        if ts.time() < MARKET_OPEN:
            return None
        # Build opening range (9:30 - 9:45)
        if ts.time() <= dt_time(9, 45):
            high = float(bar["High"])
            low = float(bar["Low"])
            if self.range_high is None:
                self.range_high = high
                self.range_low = low
            else:
                self.range_high = max(self.range_high, high)
                self.range_low = min(self.range_low, low)
            return None
        if self.entered:
            return None
        if ts.time() > dt_time(11, 0):
            return None  # only trade breakouts in first 90 min
        if self.range_high is None or self.range_low is None:
            return None
        close = float(bar["Close"])
        range_size_pct = (self.range_high - self.range_low) / self.range_low * 100
        if range_size_pct < self.config.get("min_range_pct", 0.1):
            return None
        # Breakout: close above range high or below range low
        if close > self.range_high:
            side = "long"
            entry = close
            stop_dist = entry * self.config.get("stop_pct", 0.4) / 100
            stop = entry - stop_dist
            target = entry + entry * self.config.get("target_pct", 0.6) / 100
        elif close < self.range_low:
            side = "short"
            entry = close
            stop_dist = entry * self.config.get("stop_pct", 0.4) / 100
            stop = entry + stop_dist
            target = entry - entry * self.config.get("target_pct", 0.6) / 100
        else:
            return None
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts),
                      f"orb_range_{range_size_pct:.2f}%")


# ── 5. Momentum Burst ──────────────────────────────────────────────────
class MomentumBurstStrategy(BaseStrategy):
    """Ride sharp velocity spikes. When a bar moves >X% with volume surge,
    enter in the direction of the move for a quick continuation profit."""

    name = "momentum_burst"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.volumes: list[float] = []
        self.closes: list[float] = []
        self.entered = False
        self.cooldown = 0

    def reset(self, date) -> None:
        super().reset(date)
        self.volumes = []
        self.closes = []
        self.entered = False
        self.cooldown = 0

    def on_bar(self, ts, bar, idx, day_bars):
        if self.cooldown > 0:
            self.cooldown -= 1
        vol = max(0.0, float(bar.get("Volume", 0)))
        close = float(bar["Close"])
        open_px = float(bar["Open"])
        self.volumes.append(vol)
        self.closes.append(close)
        if self.entered or self.cooldown > 0:
            return None
        if ts.time() < dt_time(9, 35) or ts.time() > dt_time(15, 45):
            return None
        lookback = self.config.get("vol_lookback", 10)
        if len(self.volumes) < lookback + 1:
            return None
        # Bar move
        bar_move_pct = (close - open_px) / open_px * 100 if open_px > 0 else 0
        min_move = self.config.get("min_move_pct", 0.3)
        if abs(bar_move_pct) < min_move:
            return None
        # Volume surge
        avg_vol = sum(self.volumes[-lookback - 1:-1]) / lookback
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0
        min_vol_ratio = self.config.get("min_vol_ratio", 2.0)
        if vol_ratio < min_vol_ratio:
            return None
        # Enter in direction of move (momentum continuation)
        side = "long" if bar_move_pct > 0 else "short"
        entry = close
        stop_dist = entry * self.config.get("stop_pct", 0.3) / 100
        stop = entry - stop_dist if side == "long" else entry + stop_dist
        target_dist = entry * self.config.get("target_pct", 0.3) / 100
        target = entry + target_dist if side == "long" else entry - target_dist
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts),
                      f"move={bar_move_pct:.2f}%_vol={vol_ratio:.1f}x")


# ── Strategy Configs ───────────────────────────────────────────────────
STRATEGY_CONFIGS = {
    "gap_fade": {
        "class": GapFadeStrategy,
        "config": {"min_gap_pct": 1.0, "stop_pct": 0.5, "max_positions": 3},
    },
    "gap_fade_large": {
        "class": GapFadeStrategy,
        "config": {"min_gap_pct": 2.0, "stop_pct": 1.0, "max_positions": 3},
    },
    "vwap_reversion": {
        "class": VWAPReversionStrategy,
        "config": {"min_dev_pct": 0.3, "stop_pct": 0.5, "max_positions": 3},
    },
    "vwap_reversion_wide": {
        "class": VWAPReversionStrategy,
        "config": {"min_dev_pct": 0.5, "stop_pct": 0.8, "max_positions": 3},
    },
    "vol_spike": {
        "class": VolSpikeStrategy,
        "config": {"lookback": 20, "z_threshold": 2.0, "stop_pct": 0.5,
                   "reversion_frac": 0.5, "max_positions": 3},
    },
    "vol_spike_extreme": {
        "class": VolSpikeStrategy,
        "config": {"lookback": 30, "z_threshold": 3.0, "stop_pct": 0.8,
                   "reversion_frac": 0.7, "max_positions": 3},
    },
    "orb": {
        "class": ORBStrategy,
        "config": {"min_range_pct": 0.1, "stop_pct": 0.4, "target_pct": 0.6,
                   "max_positions": 3},
    },
    "orb_wide": {
        "class": ORBStrategy,
        "config": {"min_range_pct": 0.2, "stop_pct": 0.6, "target_pct": 1.0,
                   "max_positions": 3},
    },
    "momentum_burst": {
        "class": MomentumBurstStrategy,
        "config": {"min_move_pct": 0.3, "min_vol_ratio": 2.0,
                   "stop_pct": 0.3, "target_pct": 0.3, "vol_lookback": 10,
                   "max_positions": 3},
    },
    "momentum_burst_large": {
        "class": MomentumBurstStrategy,
        "config": {"min_move_pct": 0.5, "min_vol_ratio": 3.0,
                   "stop_pct": 0.5, "target_pct": 0.5, "vol_lookback": 15,
                   "max_positions": 3},
    },
}


# ── Backtester Engine ──────────────────────────────────────────────────
def fetch_1m_data(symbols: list[str], start: str, end: str, provider):
    """Fetch 1m bars for all symbols, return dict[symbol → DataFrame."""
    from datetime import timedelta
    frames = {}
    for sym in symbols:
        try:
            req_start = (datetime.fromisoformat(start) - timedelta(days=3)).strftime("%Y-%m-%d")
            req_end = (datetime.fromisoformat(end) + timedelta(days=1)).strftime("%Y-%m-%d")
            df = provider.history(sym, start=req_start, end=req_end,
                                  interval="1m", auto_adjust=False, raise_errors=False)
        except Exception as exc:
            print(f"  WARN: fetch failed for {sym}: {exc}")
            continue
        if df is None or df.empty:
            print(f"  WARN: no data for {sym}")
            continue
        df = df.reset_index()
        col = "Datetime" if "Datetime" in df.columns else "Date"
        df[col] = pd.to_datetime(df[col], utc=True)
        # Convert to ET, strip timezone for session logic
        df[col] = df[col].dt.tz_convert("America/New_York").dt.tz_localize(None)
        df = df.rename(columns={col: "Timestamp"})
        # Filter to market hours
        df = df[(df["Timestamp"].dt.time >= MARKET_OPEN) &
                (df["Timestamp"].dt.time <= MARKET_CLOSE)]
        df = df.sort_values("Timestamp").reset_index(drop=True)
        frames[sym] = df
        print(f"  {sym}: {len(df):,} bars ({df['Timestamp'].iloc[0]} → {df['Timestamp'].iloc[-1]})")
    return frames


def fetch_prev_closes(symbols: list[str], dates: list, provider):
    """Fetch daily bars to get previous day closes for each trading day."""
    from datetime import timedelta
    result = {}  # (symbol, date) → prev_close
    if not dates:
        return result
    earliest = min(dates) - timedelta(days=10)
    latest = max(dates) + timedelta(days=1)
    for sym in symbols:
        try:
            df = provider.history(sym, start=earliest.strftime("%Y-%m-%d"),
                                  end=latest.strftime("%Y-%m-%d"),
                                  interval="1d", auto_adjust=False, raise_errors=False)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.reset_index()
        col = "Datetime" if "Datetime" in df.columns else "Date"
        df[col] = pd.to_datetime(df[col]).dt.tz_localize(None)
        df = df.sort_values(col)
        closes = df[col].dt.date.tolist()
        close_vals = df["Close"].tolist()
        for i in range(1, len(closes)):
            result[(sym, closes[i])] = float(close_vals[i - 1])
    return result


def apply_slippage(price: float, side: str, is_entry: bool, slippage_bps: float) -> float:
    """Apply adverse slippage. Entry: pay more (long) / receive less (short).
    Exit: receive less (long close) / pay more (short cover)."""
    slip = slippage_bps / 10000.0
    if is_entry:
        return price * (1 + slip) if side == "long" else price * (1 - slip)
    else:
        return price * (1 - slip) if side == "long" else price * (1 + slip)


def run_backtest(
    strategy_id: str,
    strategy_class: type,
    config: dict,
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    prev_closes: dict,
    capital: float,
    slippage_bps: float,
    fee_rate: float,
) -> dict[str, Any]:
    """Run a single strategy backtest across all symbols and dates."""
    max_positions = config.get("max_positions", 3)
    position_pct = config.get("position_pct", 30.0)

    # Pre-build lookups for fast access (avoid pandas filtering in hot loop)
    # ts_to_idx[sym] = {Timestamp → row index}
    # day_groups[sym] = {date → DataFrame of that day's bars}
    ts_to_idx: dict[str, dict] = {}
    day_groups: dict[str, dict] = {}
    all_dates = set()
    for sym, df in frames.items():
        ts_to_idx[sym] = {ts: i for i, ts in enumerate(df["Timestamp"])}
        day_groups[sym] = {d: g for d, g in df.groupby(df["Timestamp"].dt.date)}
        all_dates.update(day_groups[sym].keys())
    all_dates = sorted(all_dates)

    strategies: dict[str, BaseStrategy] = {}
    cash = capital
    positions: dict[str, Position] = {}
    trades: list[TradeRecord] = []
    curve: list[dict] = []
    diagnostics = Counter()
    first_ts = None
    last_ts = None
    last_prices: dict[str, float] = {}

    # Iterate per-day, per-bar (much faster than unified timeline)
    for date in all_dates:
        # Reset strategies for new day
        for sym in frames:
            if sym not in strategies:
                strategies[sym] = strategy_class(sym, config)
            strat = strategies[sym]
            if strat.session_date != date:
                strat.reset(date)
                if hasattr(strat, "set_prev_close"):
                    pc = prev_closes.get((sym, date))
                    if pc:
                        strat.set_prev_close(pc)

        # Build per-day bar lists for this date
        day_data: dict[str, pd.DataFrame] = {}
        for sym in frames:
            day_df = day_groups.get(sym, {}).get(date)
            if day_df is not None and not day_df.empty:
                day_data[sym] = day_df

        if not day_data:
            continue

        # Iterate through bars in this day (union of all symbols' timestamps)
        day_ts = sorted(set(t for df in day_data.values() for t in df["Timestamp"]))

        for ts in day_ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            # Current bar data — use pre-built index lookups
            prices: dict[str, float] = {}
            highs: dict[str, float] = {}
            lows: dict[str, float] = {}
            bars: dict[str, pd.Series] = {}
            day_bars_map: dict[str, pd.DataFrame] = {}

            for sym, day_df in day_data.items():
                idx_map = ts_to_idx[sym]
                idx = idx_map.get(ts)
                if idx is None:
                    continue
                bar = day_df.iloc[idx - day_df.index[0]]  # local index in day df
                bars[sym] = bar
                prices[sym] = float(bar["Close"])
                highs[sym] = float(bar["High"])
                lows[sym] = float(bar["Low"])
                # Day bars up to current bar (pre-sliced, no filtering)
                local_idx = idx - day_df.index[0]
                day_bars_map[sym] = day_df.iloc[:local_idx + 1]
            last_prices.update(prices)

            # ── Exit management (before entries) ───────────────────
            for sym in list(positions.keys()):
                pos = positions[sym]
                if sym not in prices:
                    pos.bars_held += 1
                    continue
                px = prices[sym]
                hi = highs[sym]
                lo = lows[sym]
                pos.bars_held += 1

                # Track MFE/MAE
                if pos.side == "long":
                    fav = (hi - pos.entry_price) / pos.entry_price * 100
                    adv = (pos.entry_price - lo) / pos.entry_price * 100
                else:
                    fav = (pos.entry_price - lo) / pos.entry_price * 100
                    adv = (hi - pos.entry_price) / pos.entry_price * 100
                pos.max_favorable = max(pos.max_favorable, fav)
                pos.max_adverse = max(pos.max_adverse, adv)

                exit_px = None
                exit_reason = None

                if pos.side == "long":
                    if lo <= pos.stop_price:
                        exit_px, exit_reason = pos.stop_price, "stop_loss"
                    elif hi >= pos.target_price:
                        exit_px, exit_reason = pos.target_price, "take_profit"
                else:
                    if hi >= pos.stop_price:
                        exit_px, exit_reason = pos.stop_price, "stop_loss"
                    elif lo <= pos.target_price:
                        exit_px, exit_reason = pos.target_price, "take_profit"

                # Time stop: exit after N bars
                max_bars = config.get("max_bars", 0)
                if exit_px is None and max_bars > 0 and pos.bars_held >= max_bars:
                    exit_px, exit_reason = px, "time_stop"

                # Force exit at end of day
                if exit_px is None and ts.time() >= dt_time(15, 55):
                    exit_px, exit_reason = px, "eod_close"

                if exit_px is not None:
                    fill_px = apply_slippage(exit_px, pos.side, is_entry=False, slippage_bps=slippage_bps)
                    fee = fill_px * pos.qty * fee_rate
                    if pos.side == "long":
                        pnl = (fill_px - pos.entry_price) * pos.qty - fee - pos.entry_fee
                        cash += fill_px * pos.qty - fee
                    else:
                        pnl = (pos.entry_price - fill_px) * pos.qty - fee - pos.entry_fee
                        cash -= fill_px * pos.qty + fee
                    pnl_pct = pnl / (pos.entry_price * pos.qty) * 100 if pos.entry_price > 0 else 0
                    hold_hours = pos.bars_held / 60.0  # 1m bars
                    trades.append(TradeRecord(
                        symbol=sym, side=pos.side,
                        entry_date=pos.entry_ts, exit_date=str(ts),
                        entry_price=pos.entry_price, exit_price=fill_px,
                        quantity=pos.qty, pnl=pnl, pnl_pct=pnl_pct,
                        hold_days=int(hold_hours / 24), hold_hours=hold_hours,
                        reason=exit_reason,
                    ))
                    diagnostics[exit_reason] += 1
                    del positions[sym]

            # ── Equity calculation ──────────────────────────────────
            equity = cash
            for sym, pos in positions.items():
                px = prices.get(sym, pos.entry_price)
                val = pos.qty * px
                equity += val if pos.side == "long" else -val
            curve.append({"date": str(ts), "equity": round(equity, 2)})

            # ── Entry signals ───────────────────────────────────────
            if len(positions) >= max_positions:
                continue
            if ts.time() >= dt_time(15, 50):
                continue  # no new entries in last 10 min

            signals: list[Signal] = []
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
                if signal:
                    signals.append(signal)

            for sig in signals:
                if len(positions) >= max_positions:
                    break
                if sig.symbol in positions:
                    continue
                entry_px = apply_slippage(sig.entry_price, sig.side, is_entry=True,
                                          slippage_bps=slippage_bps)
                notional = equity * position_pct / 100.0
                qty = notional / entry_px
                if qty <= 0:
                    continue
                entry_fee = entry_px * qty * fee_rate
                cash -= entry_px * qty + entry_fee if sig.side == "long" else 0
                if sig.side == "short":
                    cash += entry_px * qty - entry_fee
                positions[sig.symbol] = Position(
                    symbol=sig.symbol, side=sig.side, entry_price=entry_px,
                    stop_price=sig.stop_price, target_price=sig.target_price,
                    qty=qty, entry_ts=str(ts), entry_fee=entry_fee,
                )
                diagnostics["entries"] += 1

    # Close any remaining positions at last bar
    for sym, pos in list(positions.items()):
        px = last_prices.get(sym, pos.entry_price)
        fill_px = apply_slippage(px, pos.side, is_entry=False, slippage_bps=slippage_bps)
        fee = fill_px * pos.qty * fee_rate
        if pos.side == "long":
            pnl = (fill_px - pos.entry_price) * pos.qty - fee - pos.entry_fee
            cash += fill_px * pos.qty - fee
        else:
            pnl = (pos.entry_price - fill_px) * pos.qty - fee - pos.entry_fee
            cash -= fill_px * pos.qty + fee
        pnl_pct = pnl / (pos.entry_price * pos.qty) * 100 if pos.entry_price > 0 else 0
        hold_hours = pos.bars_held / 60.0
        trades.append(TradeRecord(
            symbol=sym, side=pos.side, entry_date=pos.entry_ts, exit_date=str(last_ts),
            entry_price=pos.entry_price, exit_price=fill_px, quantity=pos.qty,
            pnl=pnl, pnl_pct=pnl_pct, hold_days=int(hold_hours / 24),
            hold_hours=hold_hours, reason="backtest_end",
        ))
        diagnostics["backtest_end"] += 1
        del positions[sym]

    # Calculate metrics
    report = BacktestReport.calculate_metrics(
        agent_name=strategy_id,
        symbols=symbols,
        start_date=str(first_ts) if first_ts else "",
        end_date=str(last_ts) if last_ts else "",
        initial_capital=capital,
        final_equity=cash,
        equity_curve=curve,
        trades=trades,
        interval="1m",
        slippage_bps=slippage_bps,
        periods_per_year=390 * 252,
    )
    return report


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Multi-strategy 1m scalping backtest")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--slippage", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--strategy", default="", help="Run single strategy by ID")
    parser.add_argument("--zero-cost", action="store_true", help="Zero slippage + fees")
    parser.add_argument("--json", default="", help="Save results to JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    slippage = 0.0 if args.zero_cost else args.slippage
    fee_rate = 0.0 if args.zero_cost else args.fee_rate

    print(f"\nMulti-Strategy 1m Scalping Backtest")
    print(f"  Symbols:    {', '.join(symbols)}")
    print(f"  Date range: {args.start} → {args.end}")
    print(f"  Capital:    ${args.capital:,.0f}")
    print(f"  Slippage:   {slippage} bps")
    print(f"  Fee rate:   {fee_rate}")
    print(f"  Mode:       {'ZERO COST' if args.zero_cost else 'realistic costs'}")

    # Provider
    alpaca = AlpacaProvider()
    if not alpaca.available:
        print("ERROR: Alpaca not configured")
        sys.exit(1)
    provider = CachedProvider(alpaca)

    # Fetch data once (shared across all strategies)
    print(f"\n  Fetching 1m data...")
    frames = fetch_1m_data(symbols, args.start, args.end, provider)
    if not frames:
        print("ERROR: No data fetched")
        sys.exit(1)

    # Fetch previous closes for gap fade
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    print(f"  Fetching daily data for prev closes...")
    prev_closes = fetch_prev_closes(symbols, all_dates, provider)

    # Select strategies
    if args.strategy:
        if args.strategy not in STRATEGY_CONFIGS:
            print(f"ERROR: Unknown strategy '{args.strategy}'")
            print(f"Available: {list(STRATEGY_CONFIGS.keys())}")
            sys.exit(1)
        configs = {args.strategy: STRATEGY_CONFIGS[args.strategy]}
    else:
        configs = STRATEGY_CONFIGS

    # Run all strategies
    results = []
    for sid, sconfig in configs.items():
        print(f"\n>>> Running {sid}...")
        t0 = time.time()
        report = run_backtest(
            strategy_id=sid,
            strategy_class=sconfig["class"],
            config=sconfig["config"],
            symbols=symbols,
            frames=frames,
            prev_closes=prev_closes,
            capital=args.capital,
            slippage_bps=slippage,
            fee_rate=fee_rate,
        )
        elapsed = time.time() - t0
        r = report.to_dict()
        result = {
            "strategy_id": sid,
            "return_pct": r["total_return_pct"],
            "profit_factor": r["profit_factor"],
            "win_rate": r["win_rate"],
            "total_trades": r["total_trades"],
            "winning_trades": r["winning_trades"],
            "losing_trades": r["losing_trades"],
            "max_drawdown_pct": r["max_drawdown_pct"],
            "sharpe_ratio": r["sharpe_ratio"],
            "avg_hold_hours": r["avg_hold_hours"],
            "final_equity": r["final_equity"],
            "per_symbol": r.get("per_symbol_stats", {}),
            "elapsed_seconds": round(elapsed, 1),
            "trades": r.get("trades", []),
        }
        results.append(result)
        _print_result(result)

    # Final ranking
    print(f"\n{'='*80}")
    print(f"  FINAL RANKING (by return)")
    print(f"{'='*80}")
    ranked = sorted(results, key=lambda r: r["return_pct"], reverse=True)
    for i, r in enumerate(ranked, 1):
        status = "PASS" if r["return_pct"] > 0 and r["profit_factor"] > 1.0 else "FAIL"
        print(f"  {i:2d}. {r['strategy_id']:25s} | "
              f"ret={r['return_pct']:+7.2f}% | "
              f"PF={r['profit_factor']:5.3f} | "
              f"WR={r['win_rate']:.0%} | "
              f"trades={r['total_trades']:5d} | "
              f"DD={r['max_drawdown_pct']:5.2f}% | "
              f"hold={r['avg_hold_hours']:.1f}h | "
              f"{status}")

    if args.json:
        output = {
            "config": {
                "symbols": symbols, "start": args.start, "end": args.end,
                "capital": args.capital, "slippage_bps": slippage,
                "fee_rate": fee_rate, "zero_cost": args.zero_cost,
            },
            "ranking": [
                {"rank": i + 1, **{k: v for k, v in r.items() if k != "trades"}}
                for i, r in enumerate(ranked)
            ],
            "results": results,
        }
        with open(args.json, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nFull results saved to: {args.json}")


def _print_result(r: dict) -> None:
    sid = r["strategy_id"]
    ret = r["return_pct"]
    pf = r["profit_factor"]
    wr = r["win_rate"]
    trades = r["total_trades"]
    dd = r["max_drawdown_pct"]
    sharpe = r["sharpe_ratio"]
    hold_h = r["avg_hold_hours"]
    status = "PASS" if ret > 0 and pf > 1.0 else "FAIL"
    print(f"\n{'='*70}")
    print(f"  {sid}  [{status}]")
    print(f"{'='*70}")
    print(f"  Return:       {ret:+.2f}%")
    print(f"  Profit Factor: {pf:.3f}")
    print(f"  Win Rate:     {wr:.1%}  ({trades} trades)")
    print(f"  Max DD:       {dd:.2f}%")
    print(f"  Sharpe:       {sharpe:.3f}")
    print(f"  Avg Hold:     {hold_h:.1f}h ({hold_h*60:.0f} min)")
    print(f"  Final Equity: ${r['final_equity']:,.2f}")
    print(f"  Runtime:      {r['elapsed_seconds']:.1f}s")
    ps = r.get("per_symbol", {})
    if ps:
        print(f"  --- Per-Symbol ---")
        for sym, stats in sorted(ps.items()):
            print(f"    {sym:6s}: {stats['trades']:3d} trades, "
                  f"WR={stats['win_rate']:.0%}, "
                  f"PnL=${stats['total_pnl']:+.2f}, "
                  f"avg={stats['avg_pnl_pct']:+.2f}%")
    # Show some sample trades
    trade_list = r.get("trades", [])
    if trade_list:
        print(f"  --- Sample Trades (first 5) ---")
        for t in trade_list[:5]:
            print(f"    {t['symbol']:6s} {t['side']:5s} "
                  f"pnl=${t['pnl']:+7.2f} ({t['pnl_pct']:+.2f}%) "
                  f"hold={t['hold_hours']:.1f}h "
                  f"reason={t['reason']}")


if __name__ == "__main__":
    main()
