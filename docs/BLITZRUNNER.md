# BlitzRunner — Deterministic Equity Momentum Runner

> Paper-trading only. No real-money execution. No LLM in the loop.

## Overview

BlitzRunner is a fully deterministic trading bot that executes an **equity momentum scalp strategy** on US stocks. It scans a configurable watchlist of equities using 15 technical indicators, enters the highest-scoring setup, and manages the position through a 6-rule exit engine with ATR-based stops, trailing stops, and goal-aware position sizing.

There is zero AI judgment in the loop. Every decision — entry, exit, sizing, switching — follows the exact same code path as the backtester, ensuring live behavior matches historical replay.

## Strategy Profile: `equity_momentum`

| Parameter | Value |
|---|---|
| **Candle interval** | 1h |
| **Lookback period** | 1 month |
| **Max positions** | 1 (single-position model) |
| **Default watchlist** | BTC, ETH, SOL, AVAX, NVDA, TSLA, META, AMZN |
| **Default poll interval** | 120 seconds (2 min) |
| **Stop loss** | -2.0% (hard), 1.5x ATR (computed) |
| **Take profit** | +2.0% (hard), 3.0x ATR (computed) |
| **Trailing stop** | 1.5% trail, activates at +2.5% |
| **Min signals for entry** | 4 bullish (or bearish) |
| **Min signal families** | 2 |
| **Min volume ratio** | 1.5x average |
| **Risk per trade** | 0.50% of equity |
| **Max trade notional** | 25% of equity |
| **Max open risk** | 1.50% of equity |
| **Daily loss halt** | 3.0% of equity |
| **Paper budget** | $10,000 |

## Entry Criteria

A symbol qualifies for entry when **all** of the following are true:

1. **Directional signal count** — At least 4 indicators agree on direction (bullish for long, bearish for short). Only directional (bullish/bearish) signals count — neutral indicators do not satisfy this threshold.
2. **Signal family diversity** — At least 2 of the 5 indicator families are represented among the directional signals:
   - `volume` — volume ratio, OBV divergence
   - `volatility` — ATR, Bollinger Bands state
   - `trend` — SMA alignment, EMA20, MACD histogram
   - `momentum` — RSI, stochastic, 1h return
   - `timing` — VWAP, candle body ratio, consolidation breakout
3. **Volume confirmation** — Current bar volume > 1.5x the 20-bar average volume.
4. **No OBV divergence** — Price rising but OBV falling (fake breakout) disqualifies entry.

### Composite Scoring

Each qualifying setup receives a composite score (0–10) weighted as:

| Component | Weight | Description |
|---|---|---|
| Signal count | 35% | `max(bullish, bearish) / 13` |
| Family diversity | 25% | `families_represented / 5` |
| Candle quality | 20% | Body ratio (full body = 1.0, doji = low) |
| Consolidation breakout | 20% | 1.0 if breaking out of tight range, else 0.0 |

Setups are ranked by composite score. The top-ranked setup is entered first.

## 15 Indicators

| # | Indicator | Family | Signal |
|---|---|---|---|
| 1 | Volume Ratio | volume | Bullish if > 1.5x avg |
| 2 | ATR (14) | volatility | Neutral (context only) |
| 3 | Bollinger Bands State | volatility | Bullish if expanding |
| 4 | SMA Alignment (20/50/200) | trend | Bullish if 20>50, bearish if 20<50 |
| 5 | EMA20 | trend | Bullish if price > EMA20 |
| 6 | MACD Histogram | trend | Bullish if > 0 |
| 7 | RSI (14) | momentum | Bullish if > 55, bearish if < 30 |
| 8 | Stochastic (14) | momentum | Bullish if K>D and K<80 |
| 9 | OBV Divergence | volume | Bearish if price up but OBV down |
| 10 | VWAP | timing | Bullish if price > VWAP |
| 11 | Candle Body Ratio | timing | Bullish if full body (>= 0.6) |
| 12 | Consolidation Breakout | timing | Bullish if breaking out of tight range |
| 13 | 1h Return | momentum | Bullish if > 0 |
| 14 | SMA Alignment (full) | trend | Same as #4, full stack check |
| 15 | EMA20 (duplicate check) | trend | Same as #5 |

## Exit Engine — 6 Hard Rules

Positions are reviewed every cycle. If any rule fires, the position is closed immediately.

| Rule | Condition | Default Threshold |
|---|---|---|
| 1. Hard stop-loss | P&L% <= stop_loss_pct | -2.0% |
| 2. Take profit | P&L% >= take_profit_pct | +2.0% |
| 3. Stagnation timeout | Cycles flat (price within threshold) >= stagnation_cycles | 6 cycles, 0.3% threshold |
| 4. Momentum death | Volume ratio < threshold AND bars held >= grace period | Vol < 0.7x, grace = 3 bars |
| 5. Overbought exhaustion | RSI > threshold AND volume dropping AND price rising | RSI > 75, vol < 1.0x |
| 6. VWAP loss | Price crosses below VWAP after entering above it | — |

### ATR-Based Protective Levels

In addition to the hard percentage stops, BlitzRunner computes ATR-based levels at entry:

- **Stop loss** = entry_price - (1.5 x ATR) for longs
- **Take profit** = entry_price + (3.0 x ATR) for longs
- **Trailing stop** = 1.5% trail, activates at +2.5% profit

Both the percentage-based and ATR-based levels are sent to the server. The server stores them on the position for protective monitoring.

## Position Sizing

BlitzRunner uses **goal-aware sizing** with two phases:

| Phase | Trigger | Size Range |
|---|---|---|
| Normal | Goal progress < 80% | 25–40% of equity (midpoint: 32.5%) |
| Final stretch | Goal progress > 80% | 15–25% of equity (midpoint: 20.0%) |

### Circuit Breakers

- **Consecutive loss cut**: After 3 consecutive losing trades, position size is cut by 50% and minimum signal count is raised from 4 to 5.
- **Daily loss halt**: If daily drawdown exceeds 3.0% of equity, all new entries are blocked. Existing positions may still exit.
- **Reentry cooldown**: After closing a position, the symbol is blocked for 3 cycles before re-entry is allowed.

### Risk-Based Sizing (Server-Side)

When `sizing_mode = "risk_based"` (default), the server computes notional as:

```
risk_dollars = equity * (risk_per_trade_pct / 100)
notional = risk_dollars / (stop_distance_pct / 100)
```

This is capped by:
- `max_trade_notional_pct` (25% of equity)
- `max_gross_exposure_pct` (100% of equity)
- `paper_account_budget` ($10,000)
- `max_position_dollar_cap` (optional hard dollar cap)

## Switch Logic

BlitzRunner operates a **single-position model** with switch capability:

- If holding a position and a new setup scores **20% higher** than the current position's entry score, the bot may switch.
- Switch requires the current position to be **profitable** (configurable via `switch_require_profitable`).
- The old position is closed, cooldown is set, and the new setup is entered immediately.

## Cycle Flow

```
1. Fetch goal status (can_trade, goal_achieved, max_loss_hit)
2. Fetch portfolio (cash, positions, equity)
3. Run scan → 15 indicators per symbol, rank setups, review positions
4. Process exits (any position with verdict "EXIT" → close immediately)
5. Decrement reentry cooldowns
6. Check max positions (skip entries if at limit)
7. Filter setups by consecutive-loss signal bar
8. Switch logic (if single position + better setup available)
9. Enter top-ranked setup in available slot(s)
10. Persist state (consecutive_losses, reentry_cooldown, cycles_run)
```

## State Persistence

State is saved atomically (write to temp file, then `os.replace`) to `agents/blitz_runner_state.json`:

```json
{
  "consecutive_losses": 0,
  "reentry_cooldown": { "NVDA": 2 },
  "last_cycle_time": "2026-08-08T20:00:00Z",
  "cycles_run": 142
}
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BLITZ_RUNNER_PASSWORD` | `blitzrunner` (dev fallback) | Login password for the bot agent |
| `AGENT_TRADE_BUDGET` | — | Override paper budget (also configurable via UI) |

### API Endpoints Used

| Endpoint | Purpose |
|---|---|
| `POST /api/claw/agents/login` | Authenticate |
| `POST /api/claw/agents/selfRegister` | Register on first run |
| `GET /api/claw/agents/me/goal` | Fetch goal status |
| `GET /api/claw/agents/me/config` | Fetch watchlist + poll interval |
| `GET /api/claw/agents/me/strategy-params` | Fetch strategy parameters |
| `GET /api/positions` | Fetch current portfolio |
| `POST /api/signals/realtime` | Execute entries and exits |
| `POST /api/signals/strategy` | Publish trade reasoning |
| `POST /api/arena/thought` | Post cycle activity to UI |
| `POST /api/signals/discussion` | Post cycle activity to conversation panel |
| `POST /api/claw/agents/heartbeat` | Heartbeat |

### Strategy Parameters (UI-Configurable)

All parameters are editable via the Arena UI Agent Editor and stored in `agent_configs.config_json.strategy_params`. The `strategy_registry.effective_params()` function merges stored overrides with defaults.

## Key Source Files

| File | Role |
|---|---|
| `agents/blitz_runner.py` | Live runner — cycle loop, execution, state |
| `agents/scan_core.py` | Indicator math, entry qualification, exit review |
| `agents/strategy_registry.py` | Default params, risk controls, sizing |
| `agents/scan_backtester.py` | Historical replay engine |
| `agents/workspaces/blitztrader/scan.py` | Live data fetching + scan orchestration |
| `service/server/scalp_guardrails.py` | Server-side entry validation |
| `service/server/routes_signals.py` | Realtime signal execution + position updates |

## Running

```bash
# From the project root
python agents/blitz_runner.py

# With custom poll interval
python agents/blitz_runner.py --interval 60

# Backtest
python agents/run_backtest.py blitzrunner --start 2025-06-01 --end 2025-08-01
```
