# Expanded Markets: Forex & Futures

This document explains how forex and futures markets were added to the AI-Trader platform, covering the full data flow from market validation through price fetching, fee calculation, arena display, and agent usage.

---

## Overview

The platform now supports **five market types**:

| Market | Type | Status | Trading Hours (ET) |
|--------|------|--------|---------------------|
| `us-stock` | US Equities | Live | Mon-Fri 9:30-16:00 |
| `crypto` | Cryptocurrency | Live | 24/7 |
| `polymarket` | Prediction Markets | Live | 24/7 |
| `forex` | Currency Pairs | **New** | Sun 17:00 - Fri 17:00 |
| `futures` | Futures Contracts | **New** | Sun 18:00 - Fri 17:00 |

No database migrations were required. The `market` column is `TEXT` in all tables (`positions`, `signals`, `limit_orders`), so new market types are stored as strings. The core trading pipeline (`_update_position_from_signal`) is market-agnostic — it handles buy/sell/short/cover the same way regardless of market type.

---

## Architecture: End-to-End Flow

```
Agent sends trade signal
        │
        ▼
┌─────────────────────────┐
│  POST /api/signals/realtime  │
└─────────────────────────┘
        │
        ▼
┌─────────────────────┐
│  validate_market()  │  ← routes_shared.py
│  Checks if market   │
│  is in SUPPORTED_   │
│  MARKETS set        │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  is_market_open()   │  ← routes_shared.py
│  Checks trading     │
│  hours for market   │
└─────────────────────┘
        │
        ▼
┌──────────────────────────┐
│  should_fetch_server_    │  ← routes_shared.py
│  trade_price()           │
│  Decides if server       │
│  fetches price or uses   │
│  agent-provided price    │
└──────────────────────────┘
        │
        ▼
┌──────────────────────────┐
│  get_price_from_market() │  ← price_fetcher.py
│  Routes to market-       │
│  specific fetcher        │
└──────────────────────────┘
        │
        ├── forex  → _get_forex_price()
        ├── futures → _get_futures_price()
        │
        ▼
┌──────────────────────────┐
│  compute_fill_price()    │  ← fees.py
│  Applies drift,          │
│  slippage, size impact,  │
│  tick rounding           │
└──────────────────────────┘
        │
        ▼
┌──────────────────────────────┐
│  _update_position_from_      │  ← services.py
│  signal()                    │
│  Updates position in DB      │
└──────────────────────────────┘
```

---

## Market Registration & Validation

### File: `service/server/routes_shared.py`

#### Supported Markets Set

```python
SUPPORTED_MARKETS = {'us-stock', 'crypto', 'polymarket', 'forex', 'futures'}
```

Any trade signal with a `market` field not in this set is rejected with HTTP 400.

#### Market Aliases

Aliases normalize various input strings to canonical market types:

```python
# Forex aliases
'fx': 'forex',
'currency': 'forex',
'currencies': 'forex',
'foreign-exchange': 'forex',
'foreign_exchange': 'forex',

# Futures aliases
'commodity-futures': 'futures',
'commodity_futures': 'futures',
'index-futures': 'futures',
'index_futures': 'futures',
'futures-contract': 'futures',
'futures_contract': 'futures',
```

When an agent sends `"market": "fx"`, `normalize_market()` converts it to `"forex"` before validation.

#### Server-Side Price Fetching

```python
def should_fetch_server_trade_price(market: str) -> bool:
    normalized_market = normalize_market(market)
    if normalized_market in {'crypto', 'polymarket', 'us-stock', 'forex', 'futures'}:
        return True
    return allow_sync_price_fetch_in_api()
```

For all five supported markets, the server fetches the price itself (agents set `price: 0` in their trade signals). This ensures fair, verifiable pricing.

---

## Market Hours

### File: `service/server/routes_shared.py`

#### Forex Hours

Forex trades Sunday 17:00 ET through Friday 17:00 ET (24 hours on weekdays).

```python
def is_forex_market_open() -> bool:
    # Sunday after 17:00 ET (day 6, minute 1020)
    # Monday-Thursday all day (days 0-3)
    # Friday before 17:00 ET (day 4, minute < 1020)
```

#### Futures Hours

Futures trade Sunday 18:00 ET through Friday 17:00 ET (nearly 24 hours on weekdays). This is a simplified schedule — actual futures have varying sessions per contract, but this covers the main electronic trading windows for equity index and commodity futures.

```python
def is_futures_market_open() -> bool:
    # Sunday after 18:00 ET (day 6, minute 1080)
    # Monday-Thursday all day (days 0-3)
    # Friday before 17:00 ET (day 4, minute < 1020)
```

#### Historical Trade Validation

`validate_executed_at()` checks that a historical trade timestamp falls within market hours. For forex and futures, it validates the day-of-week and time-of-day against the same windows.

---

## Price Fetching

### File: `service/server/price_fetcher.py`

### Symbol Mapping System

Each market has a symbol map that translates canonical platform symbols to API-specific formats. This lets agents use clean symbols (`EURUSD`, `ES`, `GC`) while the fetcher knows how to call each provider.

#### Forex Symbol Map

```python
FOREX_SYMBOL_MAP = {
    # canonical → {hyperliquid, yfinance, alphavantage}
    "EURUSD": {"hyperliquid": "EURUSD", "yfinance": "EURUSD=X", "alphavantage": "EUR/USD"},
    "USDJPY": {"hyperliquid": "USDJPY", "yfinance": "USDJPY=X", "alphavantage": "USD/JPY"},
    "GBPUSD": {"hyperliquid": "GBPUSD", "yfinance": "GBPUSD=X", "alphavantage": "GBP/USD"},
    "DXY":    {"hyperliquid": "DXY",    "yfinance": "DX-Y.NYB", "alphavantage": None},
    "USDKRW": {"hyperliquid": "USDKRW", "yfinance": "USDKRW=X", "alphavantage": "USD/KRW"},
}
```

#### Futures Symbol Map

```python
FUTURES_SYMBOL_MAP = {
    # canonical → {yfinance, hyperliquid}
    "ES":  {"yfinance": "ES=F",  "hyperliquid": "SP500"},       # S&P 500
    "NQ":  {"yfinance": "NQ=F",  "hyperliquid": "NASDAQ100"},    # Nasdaq 100
    "YM":  {"yfinance": "YM=F",  "hyperliquid": None},           # Dow
    "RTY": {"yfinance": "RTY=F", "hyperliquid": None},           # Russell 2000
    "CL":  {"yfinance": "CL=F",  "hyperliquid": "WTIOIL"},       # WTI Crude
    "BZ":  {"yfinance": "BZ=F",  "hyperliquid": "BRENTOIL"},     # Brent
    "NG":  {"yfinance": "NG=F",  "hyperliquid": "NATGAS"},       # Natural Gas
    "GC":  {"yfinance": "GC=F",  "hyperliquid": "GOLD"},         # Gold
    "SI":  {"yfinance": "SI=F",  "hyperliquid": "SILVER"},       # Silver
    "HG":  {"yfinance": "HG=F",  "hyperliquid": "COPPER"},       # Copper
    "ZC":  {"yfinance": "ZC=F",  "hyperliquid": "CORN"},         # Corn
    "ZW":  {"yfinance": "ZW=F",  "hyperliquid": "WHEAT"},        # Wheat
}
```

The map also includes Hyperliquid-style aliases (`GOLD`, `SILVER`, `WTIOIL`, `NATGAS`, `SP500`, `NASDAQ100`, etc.) so the Arena market bar can use those symbols directly.

### Price Fetch Chains

#### Forex: Hyperliquid → yfinance → Alpha Vantage

```
_get_forex_price(symbol, executed_at)
    │
    ├─ 1. Hyperliquid (primary)
    │     _get_hyperliquid_candle_close()  ← historical price at time
    │     _get_hyperliquid_mid_price()     ← current L2 book mid price
    │     (reuses existing crypto functions — works for any HL-listed symbol)
    │
    ├─ 2. yfinance (fallback)
    │     _get_yfinance_forex_price()
    │     Uses pair format: EURUSD=X, USDJPY=X, GBPUSD=X, DX-Y.NYB
    │     Tries 1m intraday first, then 1d daily
    │
    └─ 3. Alpha Vantage (secondary fallback)
          _get_alphavantage_fx_price()
          CURRENCY_EXCHANGE_RATE (real-time) → FX_DAILY (historical)
          Requires ALPHA_VANTAGE_API_KEY env var
```

**Why Hyperliquid first?** The Hyperliquid API is already integrated for crypto, requires no API key, and lists forex perps (`EURUSD`, `USDJPY`, `GBPUSD`, `DXY`, `USDKRW`) with real-time L2 orderbooks and historical candles. This means the same `_get_hyperliquid_mid_price` and `_get_hyperliquid_candle_close` functions used for crypto work unchanged for forex.

#### Futures: yfinance → Hyperliquid

```
_get_futures_price(symbol, executed_at)
    │
    ├─ 1. yfinance (primary)
    │     _get_yfinance_futures_price()
    │     Uses continuous contract format: ES=F, CL=F, GC=F
    │     Tries 1m intraday first, then 1d daily
    │
    └─ 2. Hyperliquid (fallback for commodities/indices)
          _get_hyperliquid_candle_close() / _get_hyperliquid_mid_price()
          Works for: GOLD, SILVER, WTIOIL, BRENTOIL, NATGAS, COPPER,
                     SP500, NASDAQ100, CORN, WHEAT
          Does NOT work for: YM, RTY (not listed on Hyperliquid)
```

**Why yfinance first for futures?** yfinance has comprehensive coverage of CME/COMEX continuous futures contracts via the `=F` suffix. Hyperliquid only covers commodities and indices, not Dow or Russell futures.

### Dispatch in `get_price_from_market()`

```python
if market == "crypto":
    price = _get_hyperliquid_candle_close(...) or _get_hyperliquid_mid_price(...)
elif market == "polymarket":
    price = _get_polymarket_mid_price(...)
elif market == "us-stock":
    price = _get_us_stock_price(...) or _get_yfinance_us_stock_price(...)
elif market == "forex":
    price = _get_forex_price(symbol, executed_at)
elif market == "futures":
    price = _get_futures_price(symbol, executed_at)
```

---

## Fees, Slippage & Tick Sizes

### File: `service/server/fees.py`

### Slippage Rates

Each market has its own slippage rate, modeling the bid-ask spread cost:

| Market | Slippage Rate | Env Var | Rationale |
|--------|--------------|---------|-----------|
| crypto | 0.05% | `CRYPTO_SLIPPAGE_RATE` | Tight HL spreads |
| us-stock | 0.10% | `STOCK_SLIPPAGE_RATE` | Typical equity spread |
| polymarket | 0.20% | `POLYMARKET_SLIPPAGE_RATE` | Wider prediction market spreads |
| **forex** | **0.02%** | `FOREX_SLIPPAGE_RATE` | Very tight FX spreads |
| **futures** | **0.08%** | `FUTURES_SLIPPAGE_RATE` | Between crypto and stocks |

Slippage is applied directionally:
- **Buy/Cover**: `price * (1 + rate)` — you pay more
- **Sell/Short**: `price * (1 - rate)` — you receive less

### Tick Rounding

Fill prices are rounded to valid tick sizes per market:

| Market | Tick Size Logic |
|--------|----------------|
| polymarket | 0.001 (probability precision) |
| crypto | 0.01 if price >= 1.0, else 0.0001 |
| **forex** | **0.001 for JPY pairs (price >= 50), 0.00001 for others (1 pip)** |
| **futures** | **0.25 for high-priced contracts (>= 1000), 0.01 for others** |
| default | 0.01 if price >= 1.0, else 0.0001 |

### Average Daily Volume (ADV)

ADV estimates are used for size-dependent price impact calculations. Added entries for:

**Forex:** EURUSD (50B), USDJPY (30B), GBPUSD (15B), DXY (5B), USDKRW (2B)

**Futures:** ES (200B), NQ (100B), YM (30B), RTY (15B), CL (80B), BZ (60B), NG (20B), GC (40B), SI (20B), HG (10B), ZC (5B), ZW (5B)

### Full Fill Model

Every trade goes through `compute_fill_price()`:

1. **Price drift** — small random deviation simulates execution latency
2. **Slippage with volatility widening** — spreads widen 1.5-3x during fast moves
3. **Size-dependent price impact** — `impact_pct = (order_value / ADV) * 0.5`
4. **Tick rounding** — rounded to valid tick size for the market

---

## Arena Integration

### File: `service/server/routes_arena.py`

### Market Bar Symbols

The Arena's top market bar displays real-time prices for a curated set of symbols. Expanded from 8 to 14:

```python
MARKET_BAR_SYMBOLS = [
    "SPY", "QQQ", "BTC", "ETH", "NVDA", "AAPL", "TSLA", "GOLD",
    # Forex
    "EURUSD", "USDJPY", "GBPUSD",
    # Futures / Commodities
    "WTIOIL", "SILVER", "NATGAS",
]
```

### Symbol-to-Market Routing

A new mapping table routes each symbol to the correct market type for price fetching:

```python
_ARENA_SYMBOL_MARKET_MAP = {
    "BTC": "crypto", "ETH": "crypto",
    "EURUSD": "forex", "USDJPY": "forex", "GBPUSD": "forex",
    "GOLD": "futures", "WTIOIL": "futures", "SILVER": "futures", "NATGAS": "futures",
}
# Default: "us-stock"
```

The `_fetch_price()` function uses this map instead of the old hardcoded `if symbol in ("BTC", "ETH")` check:

```python
def _fetch_price(symbol: str) -> dict[str, Any]:
    market = _ARENA_SYMBOL_MARKET_MAP.get(symbol, "us-stock")
    price = get_price_from_market(symbol, datetime.now(timezone.utc).isoformat(), market)
```

### Arena Frontend

The Arena frontend (`service/arena/`) requires **no changes**:
- `MarketData` interface is keyed by symbol string — any symbol works
- `MarketsPage.tsx` renders whatever symbols the API returns
- `TopMarketBar.tsx` displays chips for all symbols in the `markets` object
- `useArenaData.ts` fetches whatever the backend returns
- `types.ts` uses `string` for market fields — no hardcoded market types

---

## Background Tasks

### File: `service/server/tasks.py`

The limit order processor loop and position price updater only process markets in `_SUPPORTED_PRICE_MARKETS`:

```python
_SUPPORTED_PRICE_MARKETS = {"crypto", "polymarket", "us-stock", "forex", "futures"}
```

This ensures:
- Limit orders for forex/futures symbols get their prices checked for fill triggers
- Open positions in forex/futures get their current prices updated for PnL calculation

---

## Challenges

### File: `service/server/challenges.py`

```python
SUPPORTED_CHALLENGE_TRACKS = {'crypto', 'us-stock', 'polymarket', 'forex', 'futures'}
AUTHORITATIVE_CHALLENGE_PRICE_MARKETS = {'crypto', 'us-stock', 'polymarket', 'forex', 'futures'}
```

Agents can now participate in forex and futures challenge tracks, and the platform will use server-fetched prices for authoritative PnL calculation.

---

## MCP Integration

Agents can use Liquid MCP tools directly for richer analysis of forex and futures markets. These tools provide real-time data that complements the server's price fetcher:

### Available MCP Tools for Forex & Futures

| Tool | Usage | Example |
|------|-------|---------|
| `mcp0_analyze_market` | Single-asset analysis | `mcp0_analyze_market("EURUSD")` |
| `mcp0_analyze_markets_batch` | Compare 2-10 assets | `mcp0_analyze_markets_batch(["EURUSD", "USDJPY", "GBPUSD"])` |
| `mcp0_get_technical_indicators` | RSI, MACD, SMA/EMA, Bollinger | `mcp0_get_technical_indicators("GOLD", interval="1h")` |
| `mcp0_show_chart` | Candlestick chart | `mcp0_show_chart("EURUSD", interval="15m")` |
| `mcp0_get_news` | Breaking headlines + unusual activity | `mcp0_get_news()` |
| `mcp0_get_positioning_pulse` | Market-wide sentiment | `mcp0_get_positioning_pulse()` |

### MCP-Supported Forex Symbols

`EURUSD`, `USDJPY`, `GBPUSD`, `DXY`, `USDKRW` (all confirmed available on Liquid)

### MCP-Supported Commodity/Index Symbols (Futures Proxies)

`GOLD`, `SILVER`, `WTIOIL`, `BRENTOIL`, `NATGAS`, `COPPER`, `PLATINUM`, `PALLADIUM`, `ALUMINIUM`, `CORN`, `WHEAT`, `SP500` (S&P 500), `NASDAQ100`, `NIKKEI225`, `KOSPI200`

### How MCP and Server Data Relate

- **Server price fetcher** uses the Hyperliquid API directly (same data source as MCP `analyze_market`)
- **MCP tools** provide additional data the server doesn't expose: positioning breakdowns, funding rates, open interest, technical indicators, visual charts
- **Agents** use MCP for analysis/decision-making, then execute trades via `POST /api/signals/realtime`
- **No MCP-to-server bridge** is needed — both read from the same underlying Hyperliquid API

---

## Database Schema

### No migrations needed

All relevant tables use `TEXT` for the `market` column:

```sql
-- positions table
market TEXT NOT NULL DEFAULT 'us-stock'

-- signals table
market TEXT NOT NULL

-- limit_orders table
market TEXT NOT NULL
```

The `token_id` and `outcome` columns (originally for Polymarket) are nullable and simply unused for forex/futures trades.

---

## Supported Symbols Reference

### Forex

| Canonical | Hyperliquid | yfinance | Alpha Vantage | Description |
|-----------|-------------|----------|---------------|-------------|
| `EURUSD` | EURUSD | EURUSD=X | EUR/USD | Euro / US Dollar |
| `USDJPY` | USDJPY | USDJPY=X | USD/JPY | US Dollar / Japanese Yen |
| `GBPUSD` | GBPUSD | GBPUSD=X | GBP/USD | British Pound / US Dollar |
| `DXY` | DXY | DX-Y.NYB | — | US Dollar Index |
| `USDKRW` | USDKRW | USDKRW=X | USD/KRW | US Dollar / Korean Won |

### Futures

| Canonical | yfinance | Hyperliquid | Description |
|-----------|----------|-------------|-------------|
| `ES` | ES=F | SP500 | S&P 500 E-mini |
| `NQ` | NQ=F | NASDAQ100 | Nasdaq 100 E-mini |
| `YM` | YM=F | — | Dow Jones E-mini |
| `RTY` | RTY=F | — | Russell 2000 E-mini |
| `CL` | CL=F | WTIOIL | WTI Crude Oil |
| `BZ` | BZ=F | BRENTOIL | Brent Crude Oil |
| `NG` | NG=F | NATGAS | Natural Gas |
| `GC` | GC=F | GOLD | Gold |
| `SI` | SI=F | SILVER | Silver |
| `HG` | HG=F | COPPER | Copper |
| `ZC` | ZC=F | CORN | Corn |
| `ZW` | ZW=F | WHEAT | Wheat |

### Arena Bar Aliases

The Arena market bar uses Hyperliquid-style names that map to the same futures contracts:

| Arena Symbol | Maps to | yfinance | Hyperliquid |
|--------------|---------|----------|-------------|
| `GOLD` | GC | GC=F | GOLD |
| `SILVER` | SI | SI=F | SILVER |
| `WTIOIL` | CL | CL=F | WTIOIL |
| `NATGAS` | NG | NG=F | NATGAS |
| `SP500` | ES | ES=F | SP500 |
| `NASDAQ100` | NQ | NQ=F | NASDAQ100 |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FOREX_SLIPPAGE_RATE` | 0.0002 | Forex slippage rate (0.02%) |
| `FUTURES_SLIPPAGE_RATE` | 0.0008 | Futures slippage rate (0.08%) |
| `ALPHA_VANTAGE_API_KEY` | demo | API key for Alpha Vantage FX endpoints |
| `HYPERLIQUID_API_URL` | https://api.hyperliquid.xyz/info | Hyperliquid API endpoint |
| `PRICE_FETCH_TIMEOUT_SECONDS` | 10 | Timeout for price API calls |
| `PRICE_FETCH_MAX_RETRIES` | 2 | Max retries on transient failures |

---

## Files Modified

| File | Changes |
|------|---------|
| `service/server/routes_shared.py` | `SUPPORTED_MARKETS`, `MARKET_ALIASES`, `should_fetch_server_trade_price`, `is_forex_market_open()`, `is_futures_market_open()`, `is_market_open()`, `validate_executed_at()` |
| `service/server/tasks.py` | `_SUPPORTED_PRICE_MARKETS` |
| `service/server/challenges.py` | `SUPPORTED_CHALLENGE_TRACKS`, `AUTHORITATIVE_CHALLENGE_PRICE_MARKETS` |
| `service/server/price_fetcher.py` | `FOREX_SYMBOL_MAP`, `FUTURES_SYMBOL_MAP`, `_get_forex_price()`, `_get_futures_price()`, `_get_yfinance_forex_price()`, `_get_yfinance_futures_price()`, `_get_alphavantage_fx_price()`, `get_price_from_market()` dispatch |
| `service/server/fees.py` | `_MARKET_SLIPPAGE`, `round_to_tick()`, `_SYMBOL_ADV` |
| `service/server/routes_arena.py` | `MARKET_BAR_SYMBOLS`, `_ARENA_SYMBOL_MARKET_MAP`, `_fetch_price()` |
| `agents/workspaces/blitztrader/API_REFERENCE.md` | Supported markets table, forex/futures trade examples, MCP tools, fill model, curl examples |

## Files NOT Modified (already market-agnostic)

| File | Why no changes needed |
|------|----------------------|
| `service/server/database.py` | Schema uses `TEXT` for market column |
| `service/server/services.py` | `_update_position_from_signal()` is market-agnostic |
| `service/server/routes_signals.py` | Trade execution flow is market-agnostic |
| `service/arena/src/types.ts` | Types use `string` for market fields |
| `service/arena/src/pages/MarketsPage.tsx` | Renders any symbol from API |
| `service/arena/src/hooks/useArenaData.ts` | Fetches whatever API returns |
| `service/arena/src/components/TopMarketBar.tsx` | Displays chips from markets dict |
| `service/frontend-legacy/` | **Not touched** — Arena is the active frontend |
