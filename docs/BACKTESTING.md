# Backtesting — Historical Replay Engine

> Backtests are paper-only simulations. No real-money execution. Results do not guarantee future performance.

## Overview

The AI-Trader backtesting system replays historical OHLCV data through the **exact same strategy logic** that the live runners use. This is the core design principle: **zero drift between backtest and live execution**.

Both BlitzRunner and CryptoRunner have dedicated backtesters that share indicator math, entry qualification, exit rules, and position sizing with their live counterparts. The only difference is the data source — historical bars instead of live feeds.

## Architecture

```
Live Runner                    Backtester
──────────                     ──────────
scan.py (fetches live data)    _fetch_historical() (fetches past data)
        │                              │
        ▼                              ▼
scan_core.deep_scan_symbol_from_df()  ← same function
crypto_scan_core.deep_scan_symbol_from_df()  ← same function
        │                              │
        ▼                              ▼
review_position_from_indicators()  ← same function
        │                              │
        ▼                              ▼
execute_entry / execute_close     simulated fill (with slippage)
```

The scan core modules (`scan_core.py`, `crypto_scan_core.py`) are **side-effect-free** — they take a DataFrame and return indicator results. This is what makes parity possible: the live runner passes a live-fetched DataFrame, the backtester passes a historical window. Same function, same math, same output.

## Backtesters

### ScanBacktester (BlitzRunner / Equities)

**File**: `agents/scan_backtester.py`

| Setting | Default |
|---|---|
| Symbols | NVDA, TSLA, META, AMZN |
| Interval | 1h |
| Initial capital | $100,000 |
| Base slippage | 10 bps (configurable) |
| Goal target | 10% of initial capital |

**Features**:
- Single-position state machine (matches live BlitzRunner)
- Goal-aware sizing with normal/final-stretch phases
- 6-rule exit engine with `bars_held` tracking
- Switch logic (close current + enter better setup)
- Consecutive-loss circuit breaker
- Reentry cooldown
- Precomputed indicators (O(n) per symbol, not O(n^2))
- Unified timeline across all symbols
- Per-symbol statistics

### ScalpScanBacktester (ScalpRunner / Multi-TF Scalp)

**File**: `agents/scalp_scan_backtester.py`

| Setting | Default |
|---|---|
| Symbols | 42-symbol universe (see `backtest_discovery.py`) |
| Interval | 1m (configurable) |
| Initial capital | $100,000 |
| Base slippage | 2 bps (configurable) |
| Max positions | 3 |

**Features**:
- Multi-timeframe scan (1m entry, 5m pattern, 15m trend) with confluence scoring
- **Dynamic symbol discovery** — per-day symbol selection via `discovery_fn` callback (static/daily/intraday modes)
- **Adaptive direction** — SPY regime drives long/short/both selection (`direction_mode: "adaptive"`)
- **Tape reading signals** — bar velocity + volume acceleration scoring (opt-in via `indicators.tape_reading`)
- **Adaptive exit logic** — phase-based stops (Phase 1: wide 1.5×ATR, Phase 2: tight 1.0×ATR + trailing, Phase 3: very tight 0.5×ATR + stagnation exit)
- **Catalyst scoring** — news headline classification via `catalyst_fn` callback; bullish/bearish bias boosts or penalizes setup scores
- ATR-based SL/TP with side-specific multiples
- SPY market regime filter (block shorts in bull, longs in bear)
- Liquidity-constrained fill simulation with partial fills
- Goal-aware sizing with consecutive-loss circuit breaker
- Reentry cooldown per symbol
- Precomputed indicators (O(n) per symbol)

### CryptoScanBacktester (CryptoRunner / Crypto)

**File**: `agents/crypto_scan_backtester.py`

| Setting | Default |
|---|---|
| Symbols | BTC, ETH, SOL, DOGE, AVAX, XRP, LINK |
| Interval | 4h |
| Initial capital | $10,000 |
| Base slippage | 10 bps (configurable) |
| Fee rate | 0.1% per trade |

**Features**:
- Multi-position state machine (up to 3 concurrent, matches live CryptoRunner)
- Daily trend agreement gate (fetches 1d candles alongside 4h)
- BTC regime filter for altcoins
- Liquidity floor check ($500k avg daily volume)
- ATR-based protective exits with intraday high/low checking
- Stop-loss / take-profit clamping (-3%/-5% and +6%/+10%)
- Trailing stop logic
- Hour-based stagnation and grace periods (converted to bar counts)
- Risk-based sizing using `strategy_registry.position_notional()`
- Fee modeling (0.1% per entry/exit)
- Per-symbol statistics

### ORB Options Backtester (Black-Scholes)

**File**: `research/strategy_search/orb_options_bs_backtester.py`

|| Setting | Default |
|---|---|
| Symbols | NVDA, TSLA, AAPL, COIN |
| Interval | 1m (equity bars) |
| Initial capital | $10,000 |
| Option slippage | 10 bps |
| Pricing model | Black-Scholes (constant IV) |

**Features**:
- Opening range breakout signal on 1m equity bars (5-min range, breakout entry)
- Options priced via Black-Scholes (no historical option bars needed)
- OTM call/put selection with configurable strike offset
- DTE range filter (2–14 days default)
- Confirmation period (no stop checks for first N minutes after entry)
- Per-symbol circuit breaker (halt after N consecutive losses in a day)
- SPY regime filter (optional — block shorts in bull market, longs in bear)
- Mark-to-market equity curve via BS pricing at each bar
- EOD force-close at 15:55 ET

**Validation**: Passed IV sensitivity (robust across 25%–75% IV), walk-forward (3/3 OOS windows positive, +68% compounded), and bear market simulation (profitable in both regimes). See `docs/ORB_OPTIONS_STRATEGY.md` for full details.

**Validation suite**: `research/strategy_search/orb_options_validation.py` — runs IV sensitivity, walk-forward, and bear market tests.

### Legacy Backtester (AI Agents)

**File**: `agents/backtester.py` (not covered in depth here)

The legacy backtester supports the AI personality agents (NewsHound, ChartMaster, FadeMaster, BlitzTrader). It uses a different strategy interface and is not parity-guaranteed with the live runners. Use the runner backtesters for accurate runner validation.

## Running a Backtest

### CLI

```bash
# BlitzRunner backtest
python agents/run_backtest.py blitzrunner \
  --start 2025-06-01 --end 2025-08-01 \
  --symbols NVDA,TSLA,META,AMZN \
  --capital 10000 \
  --slippage 5

# CryptoRunner backtest
python agents/run_backtest.py cryptorunner \
  --start 2025-06-01 --end 2025-08-01 \
  --symbols BTC,ETH,SOL \
  --capital 10000 \
  --slippage 5

# List available strategies
python agents/run_backtest.py --list

# Save full report as JSON
python agents/run_backtest.py cryptorunner --json report.json
```

### API

```bash
# Run via API
curl -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "agent_key": "cryptorunner",
    "symbols": ["BTC", "ETH", "SOL"],
    "start_date": "2025-06-01",
    "end_date": "2025-08-01",
    "initial_capital": 10000,
    "interval": "4h",
    "slippage_bps": 5
  }'

# List available strategies
curl http://localhost:8000/api/backtest/strategies
```

### Arena UI

Navigate to the **Backtest** page in the Arena frontend. Select a strategy (BlitzRunner or CryptoRunner), set the date range, symbols, and capital, then run. Results display with an equity curve, trade list, per-symbol breakdown, and activation gate status.

## Report Metrics

Every backtest produces a `BacktestReport` with the following metrics:

| Metric | Description |
|---|---|
| `total_return_pct` | (final_equity - initial_capital) / initial_capital * 100 |
| `sharpe_ratio` | Annualized Sharpe ratio (per-bar returns, scaled by `periods_per_year`) |
| `max_drawdown_pct` | Largest peak-to-trough decline in the equity curve |
| `win_rate` | Winning trades / total trades |
| `profit_factor` | Gross profit / gross loss |
| `total_trades` | Number of completed round-trip trades |
| `winning_trades` | Trades with positive P&L |
| `losing_trades` | Trades with zero or negative P&L |
| `avg_hold_days` | Average holding period in days |
| `avg_hold_hours` | Average holding period in hours |
| `equity_curve` | Time series of portfolio equity values |
| `trades` | List of individual trade records |
| `per_symbol_stats` | Per-symbol breakdown of trades, wins, P&L |
| `interval` | Candle interval used |
| `slippage_bps` | Slippage in basis points applied to fills |
| `out_of_sample` | Whether the backtest used out-of-sample data |

### Sharpe Ratio Annualization

The Sharpe ratio is annualized using `periods_per_year`, which scales with the bar interval:

| Interval | periods_per_year |
|---|---|
| 1d | 252 |
| 4h | 252 * 6 = 1,512 |
| 1h | 252 * 24 = 6,048 |

This ensures intraday backtests produce comparable Sharpe values to daily backtests.

## Activation Gate

The activation gate is a **strict quantitative pass/fail check** that determines whether a strategy is eligible to move from paper-only to consideration for live deployment. All five checks must pass:

| Check | Threshold | Description |
|---|---|---|
| `positive_return` | > 0% | Total return must be positive |
| `profit_factor` | > 1.15 | Gross profit must exceed gross loss by 15%+ |
| `max_drawdown` | < 8.0% | Maximum drawdown must stay under 8% |
| `trade_coverage` | >= 100 trades | Enough trades for statistical significance |
| `out_of_sample` | true | Must pass on out-of-sample data |

```python
report.activation_gate()
# → {"eligible": False, "checks": {"positive_return": True, "profit_factor": False, ...}}
```

The gate is intentionally hard to pass. Most strategies will fail at least one check. This is by design — it prevents activating strategies that look good but are statistically fragile.

## Slippage, Fees, and Fill Simulation

All backtesters now use `agents/execution_simulator.py`, a deterministic execution model shared by the runner backtests. It keeps runs reproducible while matching the cost categories used by live paper execution:

1. Adverse base slippage in basis points.
2. Volatility widening from the current bar's high/low range.
3. Size-dependent impact relative to estimated daily dollar volume.
4. Liquidity-constrained partial fills when an order exceeds the configured ADV share.
5. Transaction fees on both entry and exit.
6. Adverse tick-size rounding (buyers round up; sellers round down).

The API defaults to `realistic_fills=true`, `fee_rate=0.001` (0.1%), and 10 bps base slippage. Set `realistic_fills=false` only for a deliberately idealized bps-only comparison. The selected assumptions are returned in `report.diagnostics` so results are not ambiguous.

The model is deterministic: it does not use the live service's random latency drift. Alpaca paper trading remains the forward-test reference for real NBBO fills, partial-fill behavior, and actual account constraints.

## Market Data Provider

The equity backtesters use the `MarketDataProvider` interface with Alpaca historical bars as the default when `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` are configured, and yfinance as the fallback. Crypto backtesting keeps its existing crypto-capable provider path:

```python
class MarketDataProvider(Protocol):
    def history(self, symbol: str, *, period: str, interval: str, **kwargs): ...
    def quote(self, symbol: str): ...
```

The provider abstraction allows swapping yfinance for a different data source (e.g., a paid API) without changing the backtester logic. yfinance is treated as a **fallback**, not a primary source — in production, a more reliable provider would be injected.

### Data Limitations (yfinance)

| Interval | Max lookback |
|---|---|
| 1m | 7 days |
| 5m / 15m / 30m | 60 days |
| 1h / 60m | 730 days |
| 4h | 730 days |
| 1d | 2+ years |

For 1h backtests, the maximum useful range is approximately 730 days. For 4h, the same limit applies. Plan your date ranges accordingly.

## Backtest / Live Parity Guarantees

The following are guaranteed to be identical between backtest and live execution:

| Component | Guarantee |
|---|---|
| Indicator math | Same `deep_scan_symbol_from_df()` function |
| Entry qualification | Same signal count, family diversity, volume ratio checks |
| Exit rules | Same 6-rule `review_position_from_indicators()` function |
| ATR computation | Same ATR period, same fallback (2% of price if ATR unavailable) |
| Position sizing | Same goal-aware sizing with consecutive-loss circuit breaker |
| Switch logic | Same score threshold and profitability requirement |
| Reentry cooldown | Same cycle/hour-based cooldown after exits |
| Strategy params | Same `effective_params()` resolution from `strategy_registry` |

### Known Parity Considerations

1. **Fill timing**: Backtests fill from the signal bar while live orders execute after network and broker latency. Alpaca paper fills against current NBBO, so fast-moving symbols can still diverge.
2. **Data quality**: Historical bars may have gaps or stale volume. Backtest size impact and partial fills are only as good as the bar's OHLCV data.
3. **Broker assumptions**: The shared simulator models cost categories but not Alpaca's exact queue-position and order-matching behavior. Use Alpaca paper as the forward-test validation layer.
4. **Short constraints**: Backtests do not know Alpaca's current easy-to-borrow inventory. A paper short can be rejected even when the backtest accepts it.

## Key Source Files

| File | Role |
|---|---|
| `agents/scan_backtester.py` | BlitzRunner historical replay engine |
| `agents/scalp_scan_backtester.py` | ScalpRunner multi-TF historical replay engine |
| `agents/crypto_scan_backtester.py` | CryptoRunner historical replay engine |
| `agents/backtest_discovery.py` | Shared symbol discovery module (daily-bar + intraday scanner) |
| `agents/catalyst_tagger.py` | News headline catalyst classification (8 categories, bias detection) |
| `agents/backtest_report.py` | Report dataclass, metrics calculation, activation gate |
| `agents/run_backtest.py` | CLI entry point for running backtests |
| `agents/scan_core.py` | Shared equity indicator math + exit review |
| `agents/scalp_scan_core.py` | ScalpRunner indicator math, tape reading, adaptive exit, scoring |
| `agents/crypto_scan_core.py` | Shared crypto indicator math + exit review |
| `research/strategy_search/orb_options_bs_backtester.py` | ORB options backtester (Black-Scholes pricing) |
| `research/strategy_search/orb_options_backtester.py` | ORB options backtester (historical option bars) |
| `research/strategy_search/orb_options_validation.py` | ORB options validation suite (IV, walk-forward, bear) |
| `agents/strategy_registry.py` | Default params, risk controls, position sizing |
| `agents/market_data.py` | Market data provider abstraction |
| `research/strategy_search/walk_forward_harness.py` | Walk-forward validation harness |
| `research/strategy_search/fence_walk_forward.py` | Fence walk-forward with discovery integration |
| `service/server/routes_backtest.py` | API endpoints for running backtests |
| `service/arena/src/pages/BacktestPage.tsx` | Arena UI backtest page |

## Walk-Forward Validation

The walk-forward harness (`research/strategy_search/walk_forward_harness.py`) validates strategies across multiple time windows to detect overfitting:

```bash
# Run walk-forward with daily symbol discovery
python research/strategy_search/walk_forward_harness.py \
  --discovery daily --max-symbols 10

# Run with static symbol list (legacy mode)
python research/strategy_search/walk_forward_harness.py \
  --discovery static
```

**Discovery modes**:
- `static` — Use the full symbol list for all windows (default, backward-compatible)
- `daily` — Discover symbols per trading day using daily-bar scanning (volume/price movers)
- `intraday` — Discover symbols using intraday bar scanning (more responsive)

The harness passes `discovery_fn` and `catalyst_fn` callbacks to the backtester, enabling per-day symbol selection and catalyst-aware scoring during walk-forward validation.

## Tips for Meaningful Backtests

1. **Use enough data**: 100+ trades is the minimum for statistical significance. With 1h candles, that may require 3–6 months of data. With 4h candles, 6–12 months.
2. **Set realistic slippage**: The 10 bps default is a starting point for liquid stocks. Calibrate it with Alpaca paper results by symbol and market regime; do not treat one constant as universal.
3. **Test out-of-sample**: Run the backtest on a period the strategy was NOT designed around. If it only works on the design period, it's overfit.
4. **Check the activation gate**: If any gate check fails, the strategy is not ready for live deployment. Don't cherry-pick which checks matter.
5. **Compare to buy-and-hold**: If the strategy underperforms buying and holding the same symbols, it's adding risk without reward.
6. **Watch max drawdown**: A strategy with +20% return but -15% drawdown is worse than one with +10% return and -3% drawdown for most traders.
7. **Per-symbol analysis**: A strategy that makes all its money on one symbol while losing on the others is not diversified — it's a single-symbol strategy in disguise.
