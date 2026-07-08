# Fee Configuration

# Transaction fee rate (per trade)
# Example: 0.001 = 0.1%
TRADE_FEE_RATE = 0.001

# Slippage rate (price impact on execution)
# Models bid/ask spread + market impact as a fraction of mid-price.
# Buyers (buy, cover) get filled at price * (1 + rate) — pay more.
# Sellers (sell, short) get filled at price * (1 - rate) — receive less.
# Example: 0.001 = 0.1% worse fill price
SLIPPAGE_RATE = 0.001


def apply_slippage(price: float, action: str) -> float:
    """Apply slippage to execution price based on trade direction.

    Buyers (buy, cover) get filled at a higher price (pay the ask + impact).
    Sellers (sell, short) get filled at a lower price (receive the bid - impact).
    """
    action = action.lower()
    if action in ('buy', 'cover'):
        return price * (1.0 + SLIPPAGE_RATE)
    elif action in ('sell', 'short'):
        return price * (1.0 - SLIPPAGE_RATE)
    return price
