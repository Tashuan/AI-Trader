"""execution_simulator.py — Shared fill model for backtesting and live paper trading.

Provides a deterministic, configurable execution simulator that models:
  - Adverse slippage (bps-based, with optional volatility widening)
  - Size-dependent price impact (order value vs ADV)
  - Partial fills (liquidity-constrained fill quantity)
  - Transaction fees
  - Tick-size rounding

Unlike service/server/fees.py (which has random price drift and env-var rates),
this module is fully deterministic and config-driven, making it suitable for
backtesting where reproducibility is essential.

All backtesters import from here instead of implementing their own inline
slippage logic, ensuring backtest results reflect realistic trading costs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Tick sizes per market ─────────────────────────────────────────────
_TICK_SIZES = {
    "us-stock": lambda p: 0.01 if p >= 1.0 else 0.0001,
    "crypto": lambda p: 0.01 if p >= 1.0 else 0.0001,
    "forex": lambda p: 0.001 if p >= 50.0 else 0.00001,
    "futures": lambda p: 0.25 if p >= 1000.0 else 0.01,
    "polymarket": lambda p: 0.001,
}


def _tick_size(price: float, market: str) -> float:
    fn = _TICK_SIZES.get(market, _TICK_SIZES["us-stock"])
    return fn(price)


def _round_to_tick(price: float, market: str, is_buyer: bool) -> float:
    """Round in the adverse direction so tick rounding cannot improve a fill."""
    tick = _tick_size(price, market)
    if tick <= 0:
        return price
    units = price / tick
    rounded = math.ceil(units) if is_buyer else math.floor(units)
    return rounded * tick


# ── Volatility multiplier (deterministic, from bar range) ─────────────
def _vol_multiplier(bar_range_pct: float) -> float:
    """Map bar high/low range to a slippage widening multiplier."""
    r = abs(bar_range_pct)
    if r <= 0.01:
        return 1.0
    if r <= 0.03:
        return 1.5
    if r <= 0.05:
        return 2.0
    return 3.0


# ── ADV estimation from bar data ──────────────────────────────────────
_BARS_PER_DAY = {
    "1d": 1, "4h": 6, "1h": 24, "60m": 24, "30m": 48,
    "15m": 96, "5m": 78, "2m": 195, "1m": 390,
}


def _bar_value(bar: Any, name: str, default: float = 0.0) -> float:
    """Read a bar field from dicts, pandas rows, or provider-shaped objects."""
    aliases = (name, name.capitalize(), name.upper())
    for key in aliases:
        try:
            if hasattr(bar, "__contains__") and key not in bar:
                continue
            value = bar.get(key) if hasattr(bar, "get") else getattr(bar, key)
        except (AttributeError, KeyError, TypeError):
            continue
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return default


def _estimate_adv(bar: Any, interval: str = "1d") -> float:
    """Estimate daily dollar volume from the current OHLCV bar."""
    vol = _bar_value(bar, "volume")
    close = _bar_value(bar, "close")
    if vol <= 0 or close <= 0:
        return 0.0
    scaling = _BARS_PER_DAY.get(interval, 1)
    return vol * close * scaling


# ── Config ────────────────────────────────────────────────────────────
@dataclass
class FillConfig:
    """Configuration for the execution simulator."""
    slippage_bps: float = 10.0
    fee_rate: float = 0.001
    enable_size_impact: bool = True
    enable_vol_widening: bool = True
    enable_partial_fills: bool = True
    enable_tick_rounding: bool = True
    max_fill_pct_of_adv: float = 0.10
    impact_factor: float = 0.5
    market: str = "us-stock"
    interval: str = "1d"
    # When True, simulate_entry/simulate_exit use the quote's bid/ask as the
    # starting price (long entries at ask, long exits at bid, etc.) before
    # applying slippage, impact, and tick rounding. When False or no quote is
    # supplied, the reference price is used directly (legacy behavior).
    enable_quote_side_pricing: bool = True

    @classmethod
    def from_legacy(cls, slippage_bps: float, fee_rate: float = 0.0,
                    market: str = "us-stock", interval: str = "1d") -> "FillConfig":
        """Build config from legacy slippage_bps/fee_rate params."""
        return cls(slippage_bps=slippage_bps, fee_rate=fee_rate,
                   market=market, interval=interval)


@dataclass
class FillResult:
    """Result of a simulated fill."""
    fill_price: float
    fill_qty: float
    fee: float
    slippage_cost: float
    partial_fill: bool


# ── Core fill logic ───────────────────────────────────────────────────
def _apply_slippage(price: float, is_buyer: bool, config: FillConfig,
                    bar: Optional[dict]) -> float:
    """Apply adverse slippage with optional volatility widening."""
    base_rate = config.slippage_bps / 10000.0
    if config.enable_vol_widening and bar is not None:
        hi = _bar_value(bar, "high", price)
        lo = _bar_value(bar, "low", price)
        close = _bar_value(bar, "close", price)
        if close > 0:
            bar_range = (hi - lo) / close
            base_rate *= _vol_multiplier(bar_range)
    if is_buyer:
        return price * (1.0 + base_rate)
    return price * (1.0 - base_rate)


def _apply_size_impact(price: float, is_buyer: bool, order_value: float,
                       adv: float, config: FillConfig) -> float:
    """Apply size-dependent price impact."""
    if not config.enable_size_impact or adv <= 0:
        return price
    impact_pct = (order_value / adv) * config.impact_factor
    if is_buyer:
        return price * (1.0 + impact_pct)
    return price * (1.0 - impact_pct)


def _compute_fill_qty(requested_qty: float, order_value: float,
                      adv: float, config: FillConfig) -> tuple[float, bool]:
    """Determine fillable quantity. Returns (qty, is_partial)."""
    if not config.enable_partial_fills or adv <= 0:
        return requested_qty, False
    max_fill_value = adv * config.max_fill_pct_of_adv
    if order_value <= max_fill_value:
        return requested_qty, False
    price_per_unit = order_value / requested_qty if requested_qty > 0 else 0
    max_fill_qty = max_fill_value / price_per_unit if price_per_unit > 0 else 0
    if max_fill_qty < requested_qty * 0.10:
        return 0.0, True
    return max_fill_qty, True


# ── Quote-side pricing ───────────────────────────────────────────────
def _quote_reference_price(price: float, is_buyer: bool,
                           quote: Optional[dict]) -> float:
    """Return the quote-side reference price for an order.

    Buyers (long entries, short exits) cross the spread and pay the ask.
    Sellers (short entries, long exits) receive the bid.

    Falls back to the supplied price when no quote is available.
    """
    if quote is None:
        return price
    if is_buyer:
        ask = float(quote.get("ask", 0))
        return ask if ask > 0 else price
    bid = float(quote.get("bid", 0))
    return bid if bid > 0 else price


# ── Public API ────────────────────────────────────────────────────────
def simulate_entry(price: float, side: str, qty: float, symbol: str,
                   config: FillConfig, bar: Optional[dict] = None,
                   quote: Optional[dict] = None) -> FillResult:
    """Simulate an entry fill (opening a long or short position).

    When ``quote`` is supplied and ``config.enable_quote_side_pricing`` is
    True, the fill starts from the quote-side price (ask for longs, bid for
    shorts) before slippage, impact, and tick rounding are applied. This
    models the cost of crossing the spread in realistic execution.
    """
    if price <= 0 or qty <= 0:
        return FillResult(0.0, 0.0, 0.0, 0.0, False)
    is_buyer = side.lower() == "long"
    ref = _quote_reference_price(price, is_buyer, quote) if config.enable_quote_side_pricing else price
    slipped = _apply_slippage(ref, is_buyer, config, bar)
    order_value = slipped * qty
    adv = _estimate_adv(bar, config.interval) if bar is not None else 0.0
    impacted = _apply_size_impact(slipped, is_buyer, order_value, adv, config)
    fill_qty, partial = _compute_fill_qty(qty, order_value, adv, config)
    if config.enable_tick_rounding:
        impacted = _round_to_tick(impacted, config.market, is_buyer)
    fee = impacted * fill_qty * config.fee_rate
    slippage_cost = abs(impacted - price) * fill_qty
    return FillResult(impacted, fill_qty, fee, slippage_cost, partial)


def simulate_exit(price: float, side: str, qty: float, symbol: str,
                  config: FillConfig, bar: Optional[dict] = None,
                  quote: Optional[dict] = None) -> FillResult:
    """Simulate an exit fill (closing a long or short position).

    When ``quote`` is supplied and ``config.enable_quote_side_pricing`` is
    True, the fill starts from the quote-side price (bid for long exits,
    ask for short exits) before slippage, impact, and tick rounding.
    """
    if price <= 0 or qty <= 0:
        return FillResult(0.0, 0.0, 0.0, 0.0, False)
    is_buyer = side.lower() == "short"  # covering a short = buying back
    ref = _quote_reference_price(price, is_buyer, quote) if config.enable_quote_side_pricing else price
    slipped = _apply_slippage(ref, is_buyer, config, bar)
    order_value = slipped * qty
    adv = _estimate_adv(bar, config.interval) if bar is not None else 0.0
    impacted = _apply_size_impact(slipped, is_buyer, order_value, adv, config)
    fill_qty, partial = _compute_fill_qty(qty, order_value, adv, config)
    if config.enable_tick_rounding:
        impacted = _round_to_tick(impacted, config.market, is_buyer)
    fee = impacted * fill_qty * config.fee_rate
    slippage_cost = abs(impacted - price) * fill_qty
    return FillResult(impacted, fill_qty, fee, slippage_cost, partial)
