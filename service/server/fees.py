# Fee Configuration

import os
import random

# Transaction fee rate (per trade)
# Example: 0.001 = 0.1%
TRADE_FEE_RATE = float(os.getenv("TRADE_FEE_RATE", "0.001"))

# ── Per-market slippage rates (env-configurable) ──────────────────────
# Buyers (buy, cover) get filled at price * (1 + rate) — pay more.
# Sellers (sell, short) get filled at price * (1 - rate) — receive less.
_MARKET_SLIPPAGE = {
    "crypto": float(os.getenv("CRYPTO_SLIPPAGE_RATE", "0.0005")),
    "us-stock": float(os.getenv("STOCK_SLIPPAGE_RATE", "0.001")),
    "polymarket": float(os.getenv("POLYMARKET_SLIPPAGE_RATE", "0.002")),
}

# Default fallback slippage for unknown markets
DEFAULT_SLIPPAGE_RATE = float(os.getenv("DEFAULT_SLIPPAGE_RATE", "0.001"))

# Legacy alias for backwards compatibility
SLIPPAGE_RATE = DEFAULT_SLIPPAGE_RATE


def get_slippage_rate(market: str) -> float:
    from routes_shared import normalize_market
    normalized = normalize_market(market)
    return _MARKET_SLIPPAGE.get(normalized, DEFAULT_SLIPPAGE_RATE)


def apply_slippage(price: float, action: str, market: str = "us-stock") -> float:
    """Apply slippage to execution price based on trade direction.

    Buyers (buy, cover) get filled at a higher price (pay the ask + impact).
    Sellers (sell, short) get filled at a lower price (receive the bid - impact).
    """
    rate = get_slippage_rate(market)
    action = action.lower()
    if action in ('buy', 'cover'):
        return price * (1.0 + rate)
    elif action in ('sell', 'short'):
        return price * (1.0 - rate)
    return price


# ── Size-dependent price impact ───────────────────────────────────────
# Models the reality that larger orders move the price against you.
# impact_pct = (order_value / adv) * IMPACT_FACTOR
IMPACT_FACTOR = float(os.getenv("PRICE_IMPACT_FACTOR", "0.5"))
DEFAULT_ADV = float(os.getenv("DEFAULT_ADV_USD", "5000000"))

_SYMBOL_ADV = {
    "BTC": 50_000_000_000,
    "ETH": 25_000_000_000,
    "SOL": 5_000_000_000,
    "AVAX": 1_000_000_000,
    "NVDA": 8_000_000_000,
    "TSLA": 6_000_000_000,
    "AAPL": 10_000_000_000,
    "META": 5_000_000_000,
    "AMZN": 7_000_000_000,
    "AMD": 3_000_000_000,
    "MSFT": 12_000_000_000,
    "GOOGL": 6_000_000_000,
    "NFLX": 2_000_000_000,
    "JPM": 5_000_000_000,
    "V": 5_000_000_000,
    "SPY": 30_000_000_000,
    "QQQ": 20_000_000_000,
}


def get_adv(symbol: str) -> float:
    return _SYMBOL_ADV.get(symbol.upper(), DEFAULT_ADV)


# ── Tick size rounding ────────────────────────────────────────────────
def round_to_tick(price: float, market: str) -> float:
    from routes_shared import normalize_market
    normalized = normalize_market(market)
    if normalized == "polymarket":
        tick = 0.001
    elif normalized == "crypto":
        tick = 0.01 if price >= 1.0 else 0.0001
    else:
        tick = 0.01 if price >= 1.0 else 0.0001
    return round(price / tick) * tick


# ── Volatility-based spread widening ──────────────────────────────────
VOLATILITY_WIDENING_ENABLED = os.getenv("VOLATILITY_SPREAD_WIDENING_ENABLED", "1") == "1"


def compute_volatility_multiplier(recent_price_change_pct: float) -> float:
    if not VOLATILITY_WIDENING_ENABLED:
        return 1.0
    abs_change = abs(recent_price_change_pct)
    if abs_change <= 0.01:
        return 1.0
    elif abs_change <= 0.03:
        return 1.5
    elif abs_change <= 0.05:
        return 2.0
    else:
        return 3.0


# ── Price drift (execution latency simulation) ───────────────────────
DRIFT_ENABLED = os.getenv("PRICE_DRIFT_ENABLED", "1") == "1"
DRIFT_STDEV = float(os.getenv("PRICE_DRIFT_STDEV", "0.0005"))


def apply_price_drift(price: float) -> float:
    if not DRIFT_ENABLED:
        return price
    drift = random.gauss(0, DRIFT_STDEV)
    return price * (1.0 + drift)


# ── Unified fill price computation ────────────────────────────────────
def compute_fill_price(
    mid_price: float,
    action: str,
    market: str,
    symbol: str,
    order_value: float,
    recent_price_change_pct: float = 0.0,
) -> float:
    """Compute realistic fill price: drift → slippage (with vol widening) → size impact → tick rounding."""
    action_lower = action.lower()

    # 1. Price drift (simulates execution latency)
    drifted = apply_price_drift(mid_price)

    # 2. Base slippage with volatility widening
    vol_mult = compute_volatility_multiplier(recent_price_change_pct)
    rate = get_slippage_rate(market) * vol_mult
    if action_lower in ('buy', 'cover'):
        slipped = drifted * (1.0 + rate)
    elif action_lower in ('sell', 'short'):
        slipped = drifted * (1.0 - rate)
    else:
        slipped = drifted

    # 3. Size-dependent price impact
    adv = get_adv(symbol)
    impact_pct = (order_value / adv) * IMPACT_FACTOR if adv > 0 else 0.0
    if action_lower in ('buy', 'cover'):
        impacted = slipped * (1.0 + impact_pct)
    elif action_lower in ('sell', 'short'):
        impacted = slipped * (1.0 - impact_pct)
    else:
        impacted = slipped

    # 4. Tick rounding
    return round_to_tick(impacted, market)


# ── Short borrow costs ────────────────────────────────────────────────
SHORT_BORROW_RATE_ANNUAL = float(os.getenv("SHORT_BORROW_RATE_ANNUAL", "0.04"))
SHORT_BORROW_HARD_RATE = float(os.getenv("SHORT_BORROW_HARD_RATE", "0.15"))
_HARD_TO_BORROW = set(
    s.strip().upper() for s in os.getenv("HARD_TO_BORROW_SYMBOLS", "").split(",") if s.strip()
)


def is_hard_to_borrow(symbol: str) -> bool:
    return symbol.upper() in _HARD_TO_BORROW


def get_borrow_rate(symbol: str) -> float:
    return SHORT_BORROW_HARD_RATE if is_hard_to_borrow(symbol) else SHORT_BORROW_RATE_ANNUAL


def compute_borrow_fee(symbol: str, position_value: float, days_held: float) -> float:
    rate = get_borrow_rate(symbol)
    daily_rate = rate / 365.0
    return position_value * daily_rate * days_held


# ── Liquidity / partial fill model ────────────────────────────────────
MAX_FILL_PCT_OF_ADV = float(os.getenv("MAX_FILL_PCT_OF_ADV", "0.10"))


def compute_fill_quantity(requested_qty: float, order_value: float, symbol: str) -> float:
    """Determine how much of the order can fill based on available liquidity."""
    adv = get_adv(symbol)
    max_fill_value = adv * MAX_FILL_PCT_OF_ADV
    if order_value <= max_fill_value:
        return requested_qty
    price_per_unit = order_value / requested_qty if requested_qty > 0 else 0
    max_fill_qty = max_fill_value / price_per_unit if price_per_unit > 0 else 0
    if max_fill_qty < requested_qty * 0.10:
        return 0.0
    return max_fill_qty
