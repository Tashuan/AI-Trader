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
| Slippage | 0 bps (configurable) |
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

### CryptoScanBacktester (CryptoRunner / Crypto)

**File**: `agents/crypto_scan_backtester.py`

| Setting | Default |
|---|---|
| Symbols | BTC, ETH, SOL, DOGE, AVAX, XRP, LINK |
| Interval | 4h |
| Initial capital | $10,000 |
| Slippage | 5 bps |
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

## Slippage and Fees

### Slippage

Slippage is modeled in **basis points** (1 bp = 0.01%). It is applied adversely:

- **Entry**: fill_price = entry_price * (1 + slippage_bps / 10000) for longs
- **Exit**: fill_price = exit_price * (1 - slippage_bps / 10000) for longs

Default slippage:
- BlitzRunner: 0 bps (configurable)
- CryptoRunner: 5 bps (0.05%)

### Fees

CryptoRunner models a **0.1% fee per trade** (entry and exit). This matches typical crypto exchange taker fees. BlitzRunner does not model fees by default (equity commissions vary by broker).

## Market Data Provider

Both backtesters use the `MarketDataProvider` interface with `YFinanceProvider` as the default implementation:

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

1. **Slippage**: Live fills will have real slippage. Backtest slippage is a configurable constant. Set `slippage_bps` to a realistic value (5–20 bps) for trustworthy results.
2. **Fill timing**: Backtests fill at the close of the signal bar. Live fills happen at the next available price after the signal is sent. This can cause small discrepancies on fast-moving symbols.
3. **Data quality**: yfinance has gaps, especially for crypto volume (zero-volume bars are forward-filled). The live runner may use a different data provider with better coverage.
4. **Fee accuracy**: Crypto fees vary by exchange and tier. The 0.1% default is a conservative estimate.

## Key Source Files

| File | Role |
|---|---|
| `agents/scan_backtester.py` | BlitzRunner historical replay engine |
| `agents/crypto_scan_backtester.py` | CryptoRunner historical replay engine |
| `agents/backtest_report.py` | Report dataclass, metrics calculation, activation gate |
| `agents/run_backtest.py` | CLI entry point for running backtests |
| `agents/scan_core.py` | Shared equity indicator math + exit review |
| `agents/crypto_scan_core.py` | Shared crypto indicator math + exit review |
| `agents/strategy_registry.py` | Default params, risk controls, position sizing |
| `agents/market_data.py` | Market data provider abstraction |
| `service/server/routes_backtest.py` | API endpoints for running backtests |
| `service/arena/src/pages/BacktestPage.tsx` | Arena UI backtest page |

## Tips for Meaningful Backtests

1. **Use enough data**: 100+ trades is the minimum for statistical significance. With 1h candles, that may require 3–6 months of data. With 4h candles, 6–12 months.
2. **Set realistic slippage**: 0 bps is optimistic. Use 5–20 bps for liquid stocks, 10–30 bps for crypto.
3. **Test out-of-sample**: Run the backtest on a period the strategy was NOT designed around. If it only works on the design period, it's overfit.
4. **Check the activation gate**: If any gate check fails, the strategy is not ready for live deployment. Don't cherry-pick which checks matter.
5. **Compare to buy-and-hold**: If the strategy underperforms buying and holding the same symbols, it's adding risk without reward.
6. **Watch max drawdown**: A strategy with +20% return but -15% drawdown is worse than one with +10% return and -3% drawdown for most traders.
7. **Per-symbol analysis**: A strategy that makes all its money on one symbol while losing on the others is not diversified — it's a single-symbol strategy in disguise.
