# CryptoRunner — Deterministic Crypto Position/Trend Runner

> Paper-trading only. No real-money execution. No LLM in the loop.

## Overview

CryptoRunner is a fully deterministic trading bot that executes a **crypto position/trend strategy** on digital assets. It uses **daily (1d) candles** with EMA trend alignment, a BTC regime filter for altcoins, and a liquidity floor gate. It supports up to 3 concurrent positions with aggressive risk sizing (3% per trade) tuned for daily-timeframe momentum.

Like BlitzRunner, there is zero AI judgment — every decision follows the exact same code path as the crypto backtester.

## Strategy Profile: `crypto_swing`

| Parameter | Value |
|---|---|
| **Candle interval** | 1d |
| **Confirmation interval** | 1d (daily trend agreement) |
| **Lookback period** | 1 year |
| **Max positions** | 3 |
| **Default watchlist** | BTC, ETH, SOL, DOGE, AVAX, XRP, ADA, LINK, DOT, LTC, UNI, ATOM, NEAR, ARB, OP, INJ, SUI, SEI, TIA, PEPE, SHIB, MATIC, APT, BCH |
| **Default poll interval** | 1800 seconds (30 min) |
| **Stop loss** | -5.0% (clamped to -3.0% / -5.0% range) |
| **Take profit** | +8.0% (clamped to +6.0% / +10.0% range) |
| **Trailing stop** | 3.0% trail, activates at +4.0% |
| **Min signals for entry** | 5 directional |
| **Min signal families** | 3 |
| **Min volume ratio** | 1.3x average |
| **Risk per trade** | 3.0% of equity |
| **Max trade notional** | 25% of equity |
| **Max open risk** | 1.50% of equity |
| **Daily loss halt** | 3.0% of equity |
| **Paper budget** | $10,000 |
| **Min avg dollar volume** | $500,000 |

## Backtested Performance (1d, 5 symbols, $10k)

| Metric | Value |
|---|---|
| **Annual return** | +22.50% ($2,250) |
| **Sharpe ratio** | 3.115 |
| **Max drawdown** | 6.60% ($660) |
| **Win rate** | 75.0% (21/28) |
| **Profit factor** | 1.982 |
| **Avg hold time** | 48 hours (2 days) |
| **Avg position size** | $5,556 (55.6% of equity) |
| **Avg loss** | $327 (3.27% of account) |
| **Avg win** | $216 (2.16% of account) |
| **Worst single loss** | $403 (4.03% of account) |
| **Best single win** | $676 (6.76% of account) |

The strategy is **win-rate dependent** (high win rate, negative R:R). It stays profitable as long as win rate remains above ~60%.

## What Makes CryptoRunner Different from BlitzRunner

| Feature | BlitzRunner | CryptoRunner |
|---|---|---|
| Market | US equities (and some crypto) | Crypto only |
| Candle interval | 1h | 1d |
| Max positions | 1 | 3 |
| Stop loss | -2.0% | -5.0% (clamped) |
| Take profit | +2.0% | +8.0% (clamped) |
| Min signals | 4 | 5 |
| Min families | 2 | 3 |
| Min volume ratio | 1.5x | 1.3x |
| OB exhaustion RSI | 75 | 80 |
| Momentum death vol | 0.7x | 0.4x |
| Stagnation timeout | 6 cycles | 3 days |
| Grace period | 3 bars | 5 days |
| Risk per trade | 0.50% | 3.0% |
| Daily trend confirmation | No | Yes |
| BTC regime filter | No | Yes (for altcoins) |
| Liquidity floor | No | Yes ($500k avg daily volume) |
| EMA trend alignment | No | Yes (9/21/55 stack) |
| Poll interval | 2 min | 30 min |
| Switch threshold | 20% | 30% |
| Reentry cooldown | 3 cycles | 8 hours |

## Entry Criteria

A crypto symbol qualifies for entry when **all** of the following are true:

1. **Directional signal count** — At least 5 indicators agree on direction.
2. **Signal family diversity** — At least 3 of the 6 indicator families are represented.
3. **Volume confirmation** — Current bar volume > 1.3x the 20-bar average.
4. **No OBV divergence** — Fake breakout detection.
5. **Daily trend agreement** — The daily candle close must be on the same side as the entry direction (close > SMA20 for longs, close < SMA20 for shorts).
6. **BTC regime filter** (for altcoins only) — If BTC's daily close is below its EMA21 (bearish regime), long entries on altcoins are blocked. BTC itself is subject to the same regime filter via `btc_self_filter`.
7. **Liquidity floor** — Average dollar volume (close x volume) over the lookback must exceed $500,000.

### Indicator Families (6 total)

| Family | Indicators |
|---|---|
| `volume` | Volume ratio, OBV divergence |
| `volatility` | ATR, Bollinger Bands state |
| `trend` | SMA alignment, EMA21, MACD histogram |
| `momentum` | RSI, stochastic, daily return |
| `timing` | VWAP, candle body ratio, consolidation breakout |
| `trend_strength` | EMA alignment (9/21/55 stack) |

## 15 Indicators

| # | Indicator | Family | Signal |
|---|---|---|---|
| 1 | Volume Ratio | volume | Bullish if > 1.3x avg |
| 2 | ATR (14) | volatility | Neutral (context only) |
| 3 | Bollinger Bands State | volatility | Bullish if expanding |
| 4 | SMA Alignment (20/50/200) | trend | Bullish if 20>50 |
| 5 | EMA21 | trend | Bullish if price > EMA21 |
| 6 | MACD Histogram | trend | Bullish if > 0 |
| 7 | RSI (14) | momentum | Bullish if > 55, bearish if < 25 |
| 8 | Stochastic (14) | momentum | Bullish if K>D and K<80 |
| 9 | OBV Divergence | volume | Bearish if price up but OBV down |
| 10 | VWAP | timing | Bullish if price > VWAP |
| 11 | Candle Body Ratio | timing | Bullish if full body (>= 0.6) |
| 12 | Consolidation Breakout | timing | Bullish if breaking out |
| 13 | Daily Return | momentum | Bullish if > 0 |
| 14 | EMA Alignment (9/21/55) | trend_strength | Bullish if 9>21>55, bearish if 9<21<55 |
| 15 | EMA21 (context) | trend | Same as #5 |

### EMA Trend Alignment

CryptoRunner adds a 15th indicator unique to the crypto profile: the **EMA stack alignment** using the 9, 21, and 55-period EMAs.

- **Bullish stack**: EMA9 > EMA21 > EMA55 → strong uptrend
- **Bearish stack**: EMA9 < EMA21 < EMA55 → strong downtrend
- **Mixed**: anything else → neutral

This indicator creates the `trend_strength` family, raising the total family count to 6 and requiring 3 families for entry (vs. 2 for BlitzRunner).

### Composite Scoring

| Component | Weight | Description |
|---|---|---|
| Signal count | 30% | `max(bullish, bearish) / 15` |
| Family diversity | 25% | `families_represented / 6` |
| Candle quality | 15% | Body ratio |
| Consolidation breakout | 15% | 1.0 if breaking out |
| Trend strength | 15% | 1.0 if EMA stack aligned |

## Exit Engine — 6 Hard Rules (Crypto-Tuned)

| Rule | Condition | Default Threshold |
|---|---|---|
| 1. Hard stop-loss | P&L% <= stop_loss_pct | -5.0% (clamped to -3.0% / -5.0%) |
| 2. Take profit | P&L% >= take_profit_pct | +8.0% (clamped to +6.0% / +10.0%) |
| 3. Stagnation timeout | Days flat >= stagnation_hours | 3 days, 1.5% threshold |
| 4. Momentum death | Vol < threshold AND days held >= grace | Vol < 0.4x, grace = 5 days |
| 5. Overbought exhaustion | RSI > threshold AND vol dropping AND price rising | RSI > 80, vol < 1.0x |
| 6. VWAP loss | Price crosses below VWAP after entering above it | — |

### Stop-Loss / Take-Profit Clamping

CryptoRunner uses clamped protective levels to prevent extreme stops on high-volatility assets:

- **Stop loss**: Computed as -5.0% of entry, but clamped to the range [-3.0%, -5.0%]. This means the stop is never tighter than -3% or wider than -5%.
- **Take profit**: Computed as +8.0% of entry, clamped to [+6.0%, +10.0%].

### ATR-Based Protective Levels

- **Stop loss** = entry_price - (1.5 x ATR) for longs
- **Take profit** = entry_price + (3.0 x ATR) for longs
- **Trailing stop** = 3.0% trail, activates at +4.0% profit

### Protective Exits (Intraday High/Low Check)

The CryptoRunner backtester also checks intraday high/low against stop and target levels — if the bar's high touches the take-profit or the bar's low touches the stop-loss, the exit fires at that level rather than waiting for the close.

## Position Sizing

| Phase | Trigger | Size Range |
|---|---|---|
| Normal | Goal progress < 80% | 12–16% of equity (midpoint: 14.0%) |
| Final stretch | Goal progress > 80% | 8–12% of equity (midpoint: 10.0%) |

### Circuit Breakers

- **Consecutive loss cut**: After 3 consecutive losing trades, position size is cut by 50% and minimum signal count is raised from 5 to 6.
- **Daily loss halt**: If daily drawdown exceeds 3.0% of equity, all new entries are blocked.
- **Reentry cooldown**: After closing a position, the symbol is blocked for 8 hours before re-entry.

### Risk-Based Sizing

At 3% risk per trade with a 5% stop distance, the notional per trade is:

```
notional = (equity × 3.0%) / (5.0% stop) = equity × 0.60
```

On a $10,000 account, this produces ~$6,000 position sizes. The actual average is ~$5,556 due to ATR-based stop variation and budget caps. See the [BlitzRunner docs](./BLITZRUNNER.md#position-sizing) for the full formula.

## Multi-Position Logic

CryptoRunner supports up to 3 concurrent positions:

1. Each cycle, available slots = `max_positions - open_position_count`
2. Setups are ranked by composite score
3. The bot fills available slots from the top-ranked setups, skipping:
   - Symbols in reentry cooldown
   - Symbols already held
4. If a sector concentration rejection occurs (guardrail), the bot retries at half size

## Daily Trend Agreement Gate

Before entering, CryptoRunner checks the **daily candle** (1d interval) for trend agreement:

- For **longs**: The prior daily close must be above the daily SMA20
- For **shorts**: The prior daily close must be below the daily SMA20
- Requires at least 21 daily bars of history

## BTC Regime Filter

For altcoins (any symbol that is not BTC):

- If BTC's daily close > BTC's daily EMA21 → **bullish regime** → alt longs allowed
- If BTC's daily close < BTC's daily EMA21 → **bearish regime** → alt longs blocked

BTC itself is also subject to regime filtering via the `btc_self_filter` flag, preventing BTC longs during bearish regimes.

This filter prevents buying altcoins during BTC downtrends, when altcoins typically bleed harder.

## Cycle Flow

```
1. Fetch goal status (can_trade, goal_achieved, max_loss_hit)
2. Fetch portfolio (cash, positions, equity)
3. Run crypto scan → 15 indicators per symbol, daily trend check, BTC regime, liquidity
4. Process exits (any position with verdict "EXIT" → close immediately)
5. Decrement reentry cooldowns (hour-based, converted to cycles)
6. Check max positions (skip entries if at 3)
7. Filter setups by consecutive-loss signal bar
8. Fill available slots from ranked setups
9. Handle guardrail rejections (retry at half size)
10. Record entry times for bars_held tracking
11. Persist state
```

## State Persistence

State is saved atomically to `agents/crypto_runner_state.json`:

```json
{
  "consecutive_losses": 0,
  "reentry_cooldown": { "SOL": 4 },
  "last_cycle_time": "2026-08-08T20:00:00Z",
  "cycles_run": 88,
  "position_entry_times": {
    "BTC": "2026-08-08T16:00:00Z",
    "ETH": "2026-08-08T18:00:00Z"
  }
}
```

The `position_entry_times` map tracks when each position was opened so the runner can compute `bars_held` for the momentum-death grace period — matching the backtester's logic exactly.

## Goal-Aware Backtesting

The backtester supports optional goal simulation via CLI flags:

```bash
python agents/run_backtest.py cryptorunner --goal-target 100 --goal-max-loss 500
```

- **`--goal-target`**: Dollar profit target. When reached, trading halts.
- **`--goal-max-loss`**: Dollar max loss. When reached, trading halts.

With a $100 target and $500 max loss on $10k, the strategy hits the goal in ~87 days (2-3 trades) with a 100% win rate on the goal-limited window.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CRYPTO_RUNNER_PASSWORD` | `cryptorunner` (dev fallback) | Login password for the bot agent |
| `AGENT_TRADE_BUDGET` | — | Override paper budget (also configurable via UI) |

### API Endpoints Used

Same as BlitzRunner — see [BlitzRunner API endpoints](./BLITZRUNNER.md#api-endpoints-used).

### Strategy Parameters (UI-Configurable)

All parameters are editable via the Arena UI Agent Editor. The `strategy_registry.effective_params()` function merges stored overrides with crypto defaults.

## Key Source Files

| File | Role |
|---|---|
| `agents/crypto_runner.py` | Live runner — cycle loop, execution, state |
| `agents/crypto_scan_core.py` | Crypto indicator math, entry qualification, exit review |
| `agents/crypto_scan.py` | Live data fetching + scan orchestration |
| `agents/strategy_registry.py` | Default params, risk controls, sizing |
| `agents/crypto_scan_backtester.py` | Historical replay engine for crypto |
| `service/server/scalp_guardrails.py` | Server-side entry validation |
| `service/server/routes_signals.py` | Realtime signal execution + position updates |

## Running

```bash
# From the project root
python agents/crypto_runner.py

# With custom poll interval
python agents/crypto_runner.py --interval 600

# Backtest (defaults to 1d interval)
python agents/run_backtest.py cryptorunner --start 2025-08-09 --end 2026-08-09

# Backtest with goal simulation
python agents/run_backtest.py cryptorunner --start 2025-08-09 --end 2026-08-09 --goal-target 100 --goal-max-loss 500

# Backtest with specific symbols
python agents/run_backtest.py cryptorunner --symbols BTC,SOL,DOGE,AVAX,LINK --start 2025-08-09 --end 2026-08-09
```
