# Market Data Provider Policy

This document describes every market-data source used by the AI-Trader project and the consolidated routing policy for the Arena runtime.

## Status of this document

The Arena mode is the primary application. All deterministic runners, the Arena backtest UI, the paper-trading server, and the StockBoy supervisor should consume data through the canonical Arena provider boundary (`agents/arena_market_data.py`). The original `agents/market_data.py` module is now explicitly legacy and remains only for backward compatibility with original-platform agent scripts.

## Canonical Arena routing

Source: `agents/arena_market_data.py`

```
US equity history:    Alpaca → Schwab → yfinance
US equity live quote: Schwab → Alpaca → yfinance
Crypto:               Hyperliquid → Binance.US → Coinbase → yfinance
Futures/FX:           yfinance (explicit research fallback only)
```

The Arena router is a small boundary that centralizes every market-data decision. It is the only provider that Arena runners and backtests should call directly.

## Provider inventory

### Alpaca

**Role in Arena:** Primary historical US equity bars and paper-trading/execution platform.

**Provides:**
- Historical stock bars (1m, 5m, 15m, 1h, 1d, weekly, monthly)
- Real-time stock quotes
- Latest trades
- Stock snapshots
- Crypto bars, quotes, trades, L2 orderbooks
- Market clock and calendar
- Options contracts and chains
- News
- Paper trading account/positions/orders

**Files:**
- `agents/equity_data_providers.py` — legacy historical-bars adapter
- `agents/alpaca_realtime_provider.py` — full real-time, account, and market-data adapter

**Configuration:**
- `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` or `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`

**Notes:**
- Alpaca data is now the canonical source for Arena backtests when keys are configured.
- The `AlpacaProvider` and `AlpacaRealtimeProvider` implementations are still separate; future consolidation may merge them.
- The external Alpaca MCP exposes order placement, account, and asset reference functions. Those are operator-level tools and must not bypass the application execution layer.

### Schwab

**Role in Arena:** Primary live US equity quotes, spreads, movers, and Level 2.

**Provides:**
- Real-time quotes and batch quotes
- Historical price history
- Movers by index and direction
- Equity Level 2 order book (requires subscription)
- Account positions (future live-trading use)

**Files:**
- `agents/schwab_provider.py`
- `agents/schwab_oauth_flow.py` (one-time OAuth setup)

**Configuration:**
- `SCHWAB_CLIENT_ID`
- `SCHWAB_CLIENT_SECRET`
- Refresh token persisted to `~/.config/devin/schwab_tokens.json`

**Notes:**
- Schwab is now the first live-equity source for the ScalpRunner pipeline.
- All Schwab returns are normalized to UTC inside the provider; the current implementation historically converted to `US/Eastern`, which caused cross-provider cache bugs.

### yfinance

**Role in Arena:** Research and emergency fallback only.

**Provides:**
- Yahoo Finance OHLCV data for equities, crypto, forex, futures, and indices
- No auth required

**File:**
- `agents/market_data.py` (legacy `YFinanceProvider`)

**Notes:**
- yfinance is intentionally no longer the default for any Arena runner or backtest.
- It remains as the terminal fallback for futures/FX and for offline research when other providers are not configured.
- Intraday lookback is limited by Yahoo's terms.

### Hyperliquid

**Role in Arena:** Crypto perpetual/spot market data and L2 order book.

**Provides:**
- Crypto OHLCV history
- L2 book
- Symbol universe

**File:**
- `agents/crypto_data_providers.py`

**Notes:**
- Hyperliquid represents perpetual-style crypto markets. Do not mix Hyperliquid bars with Alpaca/Coinbase spot bars in the same canonical dataset.

### Binance.US

**Role in Arena:** Secondary crypto historical fallback.

**Provides:**
- Crypto kline/candle data

**File:**
- `agents/crypto_data_providers.py`

**Notes:**
- Optional. Keep only if region/coverage requires it.

### Coinbase

**Role in Arena:** Tertiary crypto spot fallback.

**Provides:**
- Public US-friendly spot candles

**File:**
- `agents/crypto_data_providers.py`

### Massive

**Role in Arena:** Tick-level fill simulation and deep research only.

**Provides:**
- Tick trades/quotes
- OHLC aggregates
- Real-time snapshots
- Movers
- Built-in technical indicators

**Files:**
- `agents/massive_provider.py`
- `agents/massive_fill_simulator.py`
- `agents/massive_cache.py`

**Configuration:**
- `MASSIVE_API_KEY`

**Notes:**
- Massive is not a default live feed for Arena runners.
- Use it for optional realistic-fill backtests or for research requiring tick/quote history.
- The Massive MCP is configured in `.devin/mcp_config.json` for external research queries.

### Alpha Vantage

**Role in Arena:** Legacy FX fallback only.

**Provides:**
- US equity intraday (legacy)
- FX real-time exchange rates
- FX daily history

**File:**
- `service/server/price_fetcher.py`

**Configuration:**
- `ALPHA_VANTAGE_API_KEY`

**Notes:**
- Alpha Vantage is no longer used for US equity prices in the Arena path.
- It remains as a temporary FX fallback until Schwab FX coverage is validated.
- Remove the `demo` default before production use.

### Finnhub

**Role in Arena:** None.

**File:**
- `agents/market_data.py`

**Notes:**
- Only used as a legacy technical-analysis fallback after yfinance.
- Should be removed once all technical analysis is computed from canonical bars.

### Liquid (MCP)

**Role in Arena:** Positioning and sentiment enrichment only.

**Provides:**
- Per-asset positioning and funding
- Long/short cohort breakdowns
- Market-wide positioning pulse
- News-driven picks

**Notes:**
- Liquid is not a canonical pricing or execution source.
- It is useful for context, contrarian signals, and funding/open-interest data.

## Files touched by the consolidation

| File | Status | Purpose |
|------|--------|---------|
| `agents/arena_market_data.py` | New | Canonical Arena provider router |
| `agents/market_data.py` | Legacy (marked) | Original platform compatibility |
| `agents/equity_data_providers.py` | Active | Alpaca historical equity adapter |
| `agents/alpaca_realtime_provider.py` | Active | Alpaca real-time, account, and market data |
| `agents/schwab_provider.py` | Active | Schwab live equity data |
| `agents/crypto_data_providers.py` | Active | Crypto fallback chain |
| `agents/massive_provider.py` | Active (specialized) | Tick/quote and fill simulation |
| `service/server/price_fetcher.py` | Active | Server-side mark pricing |
| `agents/workspaces/blitztrader/scan.py` | Active | BlitzRunner scan wrapper now uses Arena router |
| `agents/workspaces/scalprunner/scan.py` | Active | ScalpRunner 4-step wrapper now uses Arena router |
| `agents/crypto_scan.py` | Active | CryptoRunner scan wrapper now uses Arena router |
| `agents/scan_backtester.py` | Active | BlitzTrader backtest uses Arena router |
| `agents/scalp_scan_backtester.py` | Active | ScalpRunner backtest uses Arena router |
| `agents/crypto_scan_backtester.py` | Active | CryptoRunner backtest uses Arena router |
| `agents/orb_runner.py` | Active | ORBRunner uses Arena router for 1m equity bars + Alpaca for options execution |
| `agents/fence_bar_runner.py` | Active | FenceBarRunner uses Arena router for 5m equity bars |

## Environment variables

| Variable | Provider | Required for |
|----------|----------|--------------|
| `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` | Alpaca | Historical equity, paper trading, crypto |
| `SCHWAB_CLIENT_ID` / `SCHWAB_CLIENT_SECRET` | Schwab | Live US equity quotes, movers, L2 |
| `MASSIVE_API_KEY` | Massive | Tick fill simulation, deep research |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage | Temporary FX fallback |
| `FINNHUB_API_KEY` | Finnhub | None (legacy, to be removed) |

## Migration notes

- Any new runner or backtest should import from `arena_market_data` instead of `market_data`.
- Do not add new provider-specific fallback logic inside scan modules or route handlers; route it through `arena_market_data`.
- Cache and backtest datasets should record provider/venue/feed in their metadata to avoid mixing spot, perpetual, and delayed data.
