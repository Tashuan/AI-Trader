"""Tick-level realistic fill simulator using Massive.com trade and quote data.

Replays historical tick trades and NBBO quotes against backtest signals
to measure realistic fill quality — answering the question: "Does the
+0.19R signal edge survive real spreads and tick-level fills, or is the
-4.2% portfolio backtest gap a fill realism problem?"

Usage:
    from massive_provider import MassiveProvider
    from massive_fill_simulator import FillSimulator

    provider = MassiveProvider()
    sim = FillSimulator(provider)

    result = sim.simulate_entry("AAPL", signal_time, entry_price=185.50,
                                side="long", order_type="stop_limit")
    # → {filled: True, fill_price: 185.52, fill_time: ..., slippage_bps: 1.1, spread_bps: 2.3}

    exit = sim.simulate_exit("AAPL", entry_time, stop_price=184.50,
                             target_price=187.00, side="long")
    # → {exit_price: 184.48, exit_time: ..., exit_reason: "stop_loss", slippage_bps: 1.1}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from massive_provider import MassiveProvider

logger = logging.getLogger(__name__)

# Cache tick data per (symbol, date) to avoid re-fetching within a run
_TICK_CACHE: dict[tuple[str, str], Optional[pd.DataFrame]] = {}
_QUOTE_CACHE: dict[tuple[str, str], Optional[pd.DataFrame]] = {}
_CACHE_MAX = 50  # max days cached in memory


class FillSimulator:
    """Replays tick trades + NBBO quotes to simulate realistic order fills.

    Fetches Massive tick-level trades and quotes for the relevant trading
    day, then walks through them to determine if a pending order would
    fill and at what actual price (including spread impact).
    """

    def __init__(self, provider: MassiveProvider, max_bars_forward: int = 78):
        """Args:
            provider: A configured MassiveProvider instance.
            max_bars_forward: Max bars to look forward for fill (78 = ~6.5h at 5m).
        """
        self.provider = provider
        self.max_bars_forward = max_bars_forward
        self._stats = {
            "fills_with_tick_data": 0,
            "fills_fallback_bar_close": 0,
            "total_slippage_bps": 0.0,
            "total_spread_bps": 0.0,
            "fill_count": 0,
        }

    @property
    def stats(self) -> dict:
        """Running statistics for diagnostics."""
        s = dict(self._stats)
        if s["fill_count"] > 0:
            s["avg_fill_slippage_bps"] = s["total_slippage_bps"] / s["fill_count"]
            s["avg_spread_bps"] = s["total_spread_bps"] / s["fill_count"]
        else:
            s["avg_fill_slippage_bps"] = 0.0
            s["avg_spread_bps"] = 0.0
        return s

    def _get_date_str(self, ts: pd.Timestamp) -> str:
        """Extract YYYY-MM-DD from a timestamp (handles tz-aware and naive)."""
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.strftime("%Y-%m-%d")

    def _get_trades(self, symbol: str, date_str: str) -> Optional[pd.DataFrame]:
        """Fetch and cache tick trades for a symbol on a given date."""
        key = (symbol, date_str)
        if key in _TICK_CACHE:
            return _TICK_CACHE[key]
        if len(_TICK_CACHE) > _CACHE_MAX:
            _TICK_CACHE.clear()
        df = self.provider.trades(symbol, date=date_str, limit=50000)
        _TICK_CACHE[key] = df
        return df

    def _get_quotes(self, symbol: str, date_str: str) -> Optional[pd.DataFrame]:
        """Fetch and cache NBBO quotes for a symbol on a given date."""
        key = (symbol, date_str)
        if key in _QUOTE_CACHE:
            return _QUOTE_CACHE[key]
        if len(_QUOTE_CACHE) > _CACHE_MAX:
            _QUOTE_CACHE.clear()
        df = self.provider.quotes(symbol, date=date_str, limit=50000)
        _QUOTE_CACHE[key] = df
        return df

    def _spread_at_time(self, quotes: pd.DataFrame, ts: pd.Timestamp) -> tuple[float, float, float]:
        """Find the NBBO spread at a given timestamp.

        Returns (bid, ask, mid). Returns (0, 0, 0) if no quote available.
        """
        if quotes is None or quotes.empty:
            return 0.0, 0.0, 0.0
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        # Find the last quote at or before ts
        mask = quotes["Datetime"] <= ts
        if not mask.any():
            # Use first available quote
            row = quotes.iloc[0]
        else:
            row = quotes[mask].iloc[-1]
        bid = float(row.get("BidPrice", 0))
        ask = float(row.get("AskPrice", 0))
        mid = float(row.get("Mid", (bid + ask) / 2 if bid > 0 and ask > 0 else 0))
        return bid, ask, mid

    def simulate_entry(self, symbol: str, signal_time: pd.Timestamp,
                       entry_price: float, side: str,
                       order_type: str = "stop_limit") -> dict:
        """Simulate a pending order fill using tick data.

        For stop-limit orders (the ScalpRunner default):
        - Long: fills when a trade prints at or above entry_price
        - Short: fills when a trade prints at or below entry_price

        The fill price is the actual trade price, not the theoretical
        entry level. This captures real slippage from price jumping
        past the stop level.

        Returns:
            {filled, fill_price, fill_time, slippage_bps, spread_bps, used_tick_data}
        """
        date_str = self._get_date_str(signal_time)
        trades = self._get_trades(symbol, date_str)
        quotes = self._get_quotes(symbol, date_str)

        if trades is None or trades.empty:
            self._stats["fills_fallback_bar_close"] += 1
            return {
                "filled": True,  # assume fill at bar level
                "fill_price": entry_price,
                "fill_time": signal_time,
                "slippage_bps": 0.0,
                "spread_bps": 0.0,
                "used_tick_data": False,
            }

        if signal_time.tzinfo is None:
            signal_time = signal_time.tz_localize("UTC")

        # Find trades after signal time
        future_trades = trades[trades["Datetime"] > signal_time]
        if future_trades.empty:
            self._stats["fills_fallback_bar_close"] += 1
            return {
                "filled": False,
                "fill_price": entry_price,
                "fill_time": signal_time,
                "slippage_bps": 0.0,
                "spread_bps": 0.0,
                "used_tick_data": False,
            }

        # Walk trades to find the trigger
        if side == "long":
            triggered = future_trades[future_trades["Price"] >= entry_price]
        else:
            triggered = future_trades[future_trades["Price"] <= entry_price]

        if triggered.empty:
            self._stats["fills_fallback_bar_close"] += 1
            return {
                "filled": False,
                "fill_price": entry_price,
                "fill_time": signal_time,
                "slippage_bps": 0.0,
                "spread_bps": 0.0,
                "used_tick_data": True,
            }

        fill_row = triggered.iloc[0]
        fill_price = float(fill_row["Price"])
        fill_time = fill_row["Datetime"]

        # Get spread at fill time
        bid, ask, mid = self._spread_at_time(quotes, fill_time)
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 0.0

        # Slippage = difference between actual fill and intended entry
        slippage_bps = abs(fill_price - entry_price) / entry_price * 10000 if entry_price > 0 else 0.0

        self._stats["fills_with_tick_data"] += 1
        self._stats["total_slippage_bps"] += slippage_bps
        self._stats["total_spread_bps"] += spread_bps
        self._stats["fill_count"] += 1

        return {
            "filled": True,
            "fill_price": fill_price,
            "fill_time": fill_time,
            "slippage_bps": slippage_bps,
            "spread_bps": spread_bps,
            "used_tick_data": True,
        }

    def simulate_exit(self, symbol: str, entry_time: pd.Timestamp,
                      stop_price: float, target_price: float,
                      side: str, trailing_stop: Optional[float] = None,
                      max_bars: int = 78) -> dict:
        """Simulate position exit using tick data.

        Walks tick trades forward from entry to determine which exit
        condition hits first (stop, target, or trailing stop) and at
        what actual fill price.

        Args:
            symbol: Stock ticker.
            entry_time: When the position was opened.
            stop_price: Stop loss price.
            target_price: Take profit price.
            side: "long" or "short".
            trailing_stop: Optional trailing stop level (absolute price, not pct).
            max_bars: Max bars to look forward before giving up.

        Returns:
            {exit_price, exit_time, exit_reason, slippage_bps, used_tick_data}
        """
        date_str = self._get_date_str(entry_time)
        trades = self._get_trades(symbol, date_str)
        quotes = self._get_quotes(symbol, date_str)

        if trades is None or trades.empty:
            self._stats["fills_fallback_bar_close"] += 1
            return {
                "exit_price": stop_price,  # conservative fallback
                "exit_time": entry_time,
                "exit_reason": "stop_loss",
                "slippage_bps": 0.0,
                "used_tick_data": False,
            }

        if entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize("UTC")

        # Limit to max_bars forward (approximate: use time window)
        # At 5m bars, 78 bars = 6.5 hours
        max_forward = entry_time + timedelta(minutes=max_bars * 5)
        future_trades = trades[(trades["Datetime"] > entry_time) & (trades["Datetime"] <= max_forward)]

        if future_trades.empty:
            self._stats["fills_fallback_bar_close"] += 1
            return {
                "exit_price": stop_price,
                "exit_time": entry_time,
                "exit_reason": "stop_loss",
                "slippage_bps": 0.0,
                "used_tick_data": False,
            }

        # Walk trades to find first exit trigger
        for _, row in future_trades.iterrows():
            price = float(row["Price"])
            trade_time = row["Datetime"]

            if side == "long":
                if price <= stop_price:
                    return self._build_exit(price, trade_time, "stop_loss", stop_price, quotes)
                if price >= target_price:
                    return self._build_exit(price, trade_time, "take_profit", target_price, quotes)
                if trailing_stop is not None and price <= trailing_stop:
                    return self._build_exit(price, trade_time, "trailing_stop", trailing_stop, quotes)
            else:  # short
                if price >= stop_price:
                    return self._build_exit(price, trade_time, "stop_loss", stop_price, quotes)
                if price <= target_price:
                    return self._build_exit(price, trade_time, "take_profit", target_price, quotes)
                if trailing_stop is not None and price >= trailing_stop:
                    return self._build_exit(price, trade_time, "trailing_stop", trailing_stop, quotes)

        # No exit triggered within the window — return last price
        last_row = future_trades.iloc[-1]
        self._stats["fills_fallback_bar_close"] += 1
        return {
            "exit_price": float(last_row["Price"]),
            "exit_time": last_row["Datetime"],
            "exit_reason": "timeout",
            "slippage_bps": 0.0,
            "used_tick_data": True,
        }

    def _build_exit(self, fill_price: float, fill_time: pd.Timestamp,
                    reason: str, intended_price: float,
                    quotes: Optional[pd.DataFrame]) -> dict:
        """Build an exit result dict with slippage and spread stats."""
        bid, ask, mid = self._spread_at_time(quotes, fill_time)
        spread_bps = ((ask - bid) / mid * 10000) if mid > 0 else 0.0
        slippage_bps = abs(fill_price - intended_price) / intended_price * 10000 if intended_price > 0 else 0.0

        self._stats["fills_with_tick_data"] += 1
        self._stats["total_slippage_bps"] += slippage_bps
        self._stats["total_spread_bps"] += spread_bps
        self._stats["fill_count"] += 1

        return {
            "exit_price": fill_price,
            "exit_time": fill_time,
            "exit_reason": reason,
            "slippage_bps": slippage_bps,
            "spread_bps": spread_bps,
            "used_tick_data": True,
        }

    def reset_stats(self):
        """Reset running statistics (call at the start of a new backtest)."""
        self._stats = {
            "fills_with_tick_data": 0,
            "fills_fallback_bar_close": 0,
            "total_slippage_bps": 0.0,
            "total_spread_bps": 0.0,
            "fill_count": 0,
        }

    @classmethod
    def clear_cache(cls):
        """Clear the tick data cache."""
        _TICK_CACHE.clear()
        _QUOTE_CACHE.clear()
