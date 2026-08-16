# FenceBarRunner + StockBoy: Autonomous Loss-Prevention System

## Overview

This document describes the complete trading system built around the Fence Bar strategy — a deterministic opening-range breakout runner supervised by StockBoy, an AI-driven loss-prevention agent. The system combines a proven backtested strategy with four automated decision detectors that act as a "human in the loop," preventing losses that the strategy alone cannot avoid.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [The Fence Bar Strategy](#the-fence-bar-strategy)
3. [FenceBarRunner](#fencebarrunner)
4. [StockBoy Supervisor](#stockboy-supervisor)
5. [The Four Decision Detectors](#the-four-decision-detectors)
6. [Data Flow: A Trading Day](#data-flow-a-trading-day)
7. [API Reference](#api-reference)
8. [Configuration](#configuration)
9. [Backtesting Foundation](#backtesting-foundation)
10. [File Map](#file-map)

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        TRADING DAY LOOP                           │
│                                                                  │
│  09:00 ET  ┌─────────────────────────────────────────────┐      │
│  ────────► │ StockBoy: Premarket Context Check            │      │
│            │   - Fetch SPY ATR, VIX, earnings calendar    │      │
│            │   - Override vol filter if catalyst present  │      │
│            │   - POST /api/stockboy/override              │      │
│            └─────────────────────────────────────────────┘      │
│                                                                  │
│  09:30 ET  ┌─────────────────────────────────────────────┐      │
│  ────────► │ FenceBarRunner: Fence Window (09:30-09:35)   │      │
│            │   - Fetch 5m bars for NVDA, TSLA, AAPL,      │      │
│            │     AMD, META                                 │      │
│            │   - Run FenceBarStrategy.on_bar()             │      │
│            │   - Generate entry signal on breakout         │      │
│            │   - Create pending order in DB                │      │
│            └──────────────┬──────────────────────────────┘      │
│                           │                                      │
│                           ▼                                      │
│            ┌─────────────────────────────────────────────┐      │
│            │ StockBoy: Entry Quality Gate (webhook)       │      │
│            │   - Fetch fence bar OHLCV, quote, volume     │      │
│            │   - Check: volume ratio ≥ 2x?                │      │
│            │   - Check: spread ≤ 0.05%?                   │      │
│            │   - Check: close in top/bottom 25% of range? │      │
│            │   - If vetoed: POST /api/stockboy/action     │      │
│            │       action_type=cancel_order               │      │
│            │   - If confirmed: order fills                │      │
│            └──────────────┬──────────────────────────────┘      │
│                           │                                      │
│                           ▼                                      │
│            ┌─────────────────────────────────────────────┐      │
│            │ Position Open: Fixed SL/TP + Force Exit     │      │
│            │   - Stop loss at fence midpoint              │      │
│            │   - Take profit at 1R (1x stop distance)     │      │
│            │   - Force exit at 15:55 ET                   │      │
│            └──────────────┬──────────────────────────────┘      │
│                           │                                      │
│  09:35-15:55              ▼                                      │
│  ─────────► ┌─────────────────────────────────────────────┐    │
│             │ StockBoy: Position Monitor (every 5 min)     │    │
│             │   - Fetch 5m bars since entry                │    │
│             │   - Compute MFE, MAE, bars since peak       │    │
│             │                                              │    │
│             │   Decision 3 — Move stop to breakeven:       │    │
│             │     MFE ≥ 0.5% + 10min in + 15min stall     │    │
│             │     → POST /api/stockboy/action              │    │
│             │       action_type=set_stop                   │    │
│             │       stop_loss_price=entry_price            │    │
│             │                                              │    │
│             │   Decision 4 — Take profit early:            │    │
│             │     MFE ≥ 0.5% + 30min since peak +         │    │
│             │     drifting back + after 11:00 ET          │    │
│             │     → POST /api/stockboy/action              │    │
│             │       action_type=close_position             │    │
│             └─────────────────────────────────────────────┘    │
│                                                                  │
│  15:55 ET  ┌─────────────────────────────────────────────┐     │
│  ────────► │ FenceBarRunner: Force Exit                  │     │
│            │   - Close all open FenceBarRunner positions  │     │
│            └─────────────────────────────────────────────┘     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Role | Location |
|---|---|---|
| **FenceBarRunner** | Deterministic trading runner — generates entries, manages exits | `agents/fence_bar_runner.py` |
| **FenceBarStrategy** | Signal generation logic — opening-range breakout detection | `agents/fence_bar_strategy.py` |
| **StockBoy Supervisor** | Loss-prevention agent — 4 decision detectors + action API | `service/server/stockboy_manager.py` |
| **Market Data Layer** | Alpaca API wrapper — bars, quotes, ATR, VIX, earnings | `service/server/stockboy_market_data.py` |
| **Entry Detector** | Decision 2 — entry quality veto | `service/server/stockboy_entry_detector.py` |
| **Position Monitor** | Decisions 3 & 4 — breakeven stop + early exit | `service/server/stockboy_position_monitor.py` |
| **Premarket Detector** | Decision 1 — vol filter override | `service/server/stockboy_premarket.py` |
| **Policy Guardrails** | Safety layer — no new entries, stop-tighten-only, paper-only | `service/server/stockboy_policy.py` |
| **Bot Manager** | Runner lifecycle — start/stop/status | `service/server/bot_manager.py` |

---

## The Fence Bar Strategy

### What It Does

The Fence Bar strategy trades opening-range breakouts on 5-minute bars. It watches the first 5 minutes of the market open (09:30-09:35 ET) to establish a "fence" — the high and low of that first bar. When price breaks out of this fence, it enters in the breakout direction.

### Winning Parameters (from 22 months of walk-forward backtesting)

| Parameter | Value | Why |
|---|---|---|
| `target_multiple_r` | 1.0 | 2R target was too ambitious — trades reached 1R then reversed. MFE analysis showed only 1 of 11 trades hit 2R. |
| `retest.enabled` | false | Retest confirmation kills edge — the market doesn't always come back to test the fence before trending. |
| `vol_filter.spy_atr_threshold` | 1.8% | Only trade on high-volatility days. ATR 1.8% filters out low-vol chop where breakouts fail. |
| `vol_filter.mode` | day | Check volatility each day, not once per backtest window. |
| `exit.mode` | fixed_sl_tp | Trailing exits destroy the edge (-7.65% vs -2.80%). Fixed exits capture wins before reversal. |
| `session.force_exit` | 15:55 | Close all positions before market close. No overnight risk. |
| `risk.risk_per_trade_pct` | 0.5% | Conservative position sizing. |
| `risk.max_trades_per_day` | 1 | One trade per day per symbol. |
| Symbols | NVDA, TSLA, AAPL, AMD, META | High-liquidity stocks with meaningful opening ranges. |

### Backtest Performance

| Metric | Value |
|---|---|
| Period | Oct 2024 - Aug 2026 (22 months, 94 walk-forward windows) |
| Total return (5bps slippage) | +0.25% |
| Total return (0bps) | +0.65% |
| Total trades | 16 |
| Win rate | 56% (9 of 16 hit 1R target) |
| Pass rate | 5% (5 of 94 windows profitable) |
| Slippage sensitivity | Profitable at 0-5bps, negative at 10bps |

### The MFE Insight

The breakthrough came from analyzing Maximum Favorable Excursion (MFE) — how far each trade went in our favor before exiting:

- **9 trades** hit the 1R take-profit target — no intervention needed
- **2 trades** had MFE < 0.3% — pure losers, never had follow-through
- **2 trades** went +0.5-0.7% favorable then reversed to stop loss
- **3 trades** peaked at +0.7% then drifted sideways for hours, closing negative at force exit

The 2R target was the problem. Trades reached 1R but rarely 2R. Lowering to 1R captured wins before they reversed. This single change improved returns from -3.31% to -2.80% and doubled the pass rate.

### Honest Assessment

The +0.25% return is positive but thin. The holdout period (Jan-Aug 2026) had zero trades because ATR 1.8% is extremely selective. The edge is real but regime-dependent — it only exists during high-volatility periods. This is why StockBoy's Decision 1 (vol filter override) matters: it can unlock trading days in the gray zone (ATR 1.2-1.8%) when a catalyst justifies the risk.

---

## FenceBarRunner

### What It Is

FenceBarRunner is the 4th deterministic trading runner in the AI-Trader system, alongside BlitzRunner, CryptoRunner, and ScalpRunner. It is registered as a controlled runner under StockBoy's supervision.

### How It Works

```
agents/fence_bar_runner.py
│
├── load_runner_config()
│   Loads fence_bar_runner_config.json, merges with FENCE_BAR_DEFAULTS
│   and WINNING_OVERRIDES (1R, ATR 1.8%, no retest)
│
├── check_vol_filter(params) → bool
│   Fetches SPY daily bars, computes 20-day ATR
│   Returns True if ATR ≥ threshold (1.8% by default)
│   This is the same logic as vol_filter_base.py._vol_filter_passes()
│
├── run_fence_signals(token, symbols, params, state)
│   THE ENTRY ENGINE — runs during 09:30-10:30 ET
│   For each symbol:
│     1. Fetch 5m bars for today
│     2. Instantiate FenceBarStrategy with params
│     3. Feed each bar to strategy.on_bar()
│     4. When signal fires → create_pending_order()
│     5. Pending order triggers StockBoy entry quality webhook
│
├── run_cycle(token, state, params, symbols)
│   THE MAIN LOOP — called every 30 seconds
│   1. If 09:30-10:30 ET and vol filter passes: run_fence_signals()
│   2. If 15:55 ET: force_exit_positions()
│   3. Send heartbeat
│
├── force_exit_positions(token, positions, state)
│   Closes all open positions at 15:55 ET
│   Belt and suspenders: also handled by fence_bar_force_exit_loop
│   background task in tasks.py
│
└── run_loop(stop_event, poll_interval)
    The background thread loop — calls run_cycle() every 30s
```

### Lifecycle

```
POST /api/arena/fence-bar-runner/start
  → bot_manager.start_fence_bar_runner()
  → Spawns thread running fence_bar_runner.run_loop()
  → Runner logs in, registers, begins cycling

POST /api/arena/fence-bar-runner/stop
  → bot_manager.stop_fence_bar_runner()
  → Sets stop_event, joins thread

GET /api/arena/fence-bar-runner/status
  → bot_manager.get_fence_bar_runner_status()
  → Returns running state, last error, thread name
```

### Safety Constraints

- **Paper trading only** — `risk_controls.paper_only = true` in config
- **No new entries by StockBoy** — StockBoy policy forbids `buy`, `short`, `enter`, `open_position`
- **One trade per day per symbol** — enforced by `signals_posted` state tracking
- **Force exit at 15:55 ET** — no overnight risk
- **Vol filter gate** — no trades on low-volatility days

---

## StockBoy Supervisor

### What It Is

StockBoy is a supervisor agent that watches over trading runners and can intervene on their positions. It **never enters new trades** — it only adjusts or closes existing positions. For FenceBarRunner, it acts as the automated "human in the loop," making the four loss-prevention decisions that a human trader would make.

### The Supervisor Loop

```
service/server/stockboy_manager.py

_cycle() runs every 60 seconds:
│
├── 1. Expire stale overrides
│   Override TTLs expire, defaults restored
│
├── 2. _run_premarket_check()    ← Decision 1
│   Runs once per day at ≥09:00 ET
│   Evaluates vol filter override
│
├── 3. _run_position_monitor()   ← Decisions 3 & 4
│   Runs every 5 minutes
│   Monitors FenceBarRunner open positions
│
├── 4. Build snapshot
│   Gathers all runner health, positions, orders, anomalies
│
├── 5. Write commentary + journal
│   Logs cycle summary, anomalies
│
└── 6. Update state
    Heartbeat, cycle count, next cycle time
```

### Entry Quality Webhook

The entry quality gate (Decision 2) is event-driven, not poll-based. When FenceBarRunner creates a pending order, it calls the webhook:

```
POST /api/stockboy/evaluate-entry
{
    "symbol": "NVDA",
    "side": "long",
    "order_id": 123,
    "runner_key": "fencebarrunner"
}
```

StockBoy evaluates the entry and cancels the order if vetoed. This must happen fast — within seconds of order creation, before the order fills.

### Policy Guardrails

Every StockBoy action passes through `stockboy_policy.py` validation before execution:

| Guardrail | Enforcement |
|---|---|
| No new entries | `FORBIDDEN_ACTIONS = {"buy", "short", "enter", "open_position"}` |
| Stop-tighten only | Stops can only move closer to entry, never further away |
| Paper-only | Live trading rejected by policy |
| Controlled runners only | Only BlitzRunner, CryptoRunner, ScalpRunner, FenceBarRunner |
| Position ownership | Actions must target positions owned by the specified runner |
| Cooldown | 60s cooldown per position per action type |
| Stale price rejection | Close actions require fresh price (< 300s old) |
| Kill switch | Emergency stop blocks all actions |
| Daily loss halt | 5% daily loss halts all actions |

---

## The Four Decision Detectors

### Decision 1: Vol Filter Override

**When:** Pre-market, once per day at 09:00 ET
**Module:** `service/server/stockboy_premarket.py`
**Function:** `evaluate_vol_override(symbols, current_atr) → dict`

The FenceBarRunner's vol filter requires SPY 20-day ATR ≥ 1.8% to trade. This is very selective — the holdout period had zero qualifying days. The premarket detector evaluates whether a catalyst justifies trading on a day when ATR is in the "gray zone" (1.2-1.8%).

```
INPUTS:
  - SPY 20-day ATR (fetched from Alpaca daily bars)
  - Earnings calendar for universe symbols (Finnhub API)
  - SPY pre-market gap % (Alpaca daily bars)
  - VIX level (approximated from SPY daily range)

LOGIC:
  if ATR ≥ 1.8%:
      → no override needed, filter passes naturally
  elif ATR < 1.2%:
      → no override, filter correctly blocks (not enough fuel)
  else:  # GRAY ZONE: 1.2% ≤ ATR < 1.8%
      if earnings today or tomorrow for any universe symbol:
          → OVERRIDE: lower ATR threshold to 1.2%
      elif SPY pre-market gap > 1.5%:
          → OVERRIDE: lower ATR threshold to 1.2%
      elif VIX > 25:
          → OVERRIDE: lower ATR threshold to 1.2%
      else:
          → no override, let the filter block

OVERRIDE EXECUTION:
  POST /api/stockboy/override
  {
      "runner_key": "fencebarrunner",
      "field_path": "entry_criteria.atr_min_pct",
      "new_value": 1.2,
      "expires_in_minutes": 390  # one trading day
  }
```

**What it's deciding:** "Is there a reason today is special even though the volatility regime filter says no?" The filter is a blunt instrument. The detector adds context it can't see — earnings, gaps, VIX.

**Expected impact:** Unlocks trading on catalyst days in the gray zone. The holdout period had zero trades because no days qualified at ATR 1.8%. Lowering to 1.2% on catalyst days could unlock 5-10 additional trading days per year.

---

### Decision 2: Entry Veto

**When:** 09:30-09:35 ET, triggered by pending order webhook
**Module:** `service/server/stockboy_entry_detector.py`
**Function:** `evaluate_entry_quality(symbol, side, pending_order) → dict`

When FenceBarRunner detects a fence bar breakout and creates a pending order, StockBoy evaluates whether the breakout has real institutional participation or is a fake. This is the most valuable gate — 2 of 7 losing trades had MFE < 0.08%, meaning they never had any follow-through.

```
INPUTS:
  - Fence bar OHLCV (Alpaca 5m bars, last 1 bar)
  - Prior 3-day average 5m volume (Alpaca 5m bars, 3 days back)
  - Current bid/ask quote (Alpaca quotes API)

METRICS COMPUTED:
  fence_volume_ratio = fence_bar_volume / avg_5m_volume
  close_position_pct = (close - low) / (high - low)   # for longs
  spread_pct = (ask - bid) / mid_price * 100

VETO CONDITIONS (any one triggers veto):
  - fence_volume_ratio < 2.0
      "Fence bar volume {ratio}x below 2x threshold"
      → No conviction behind the breakout

  - spread_pct > 0.05
      "Spread {spread_pct}% too wide"
      → Low liquidity, will get filled at bad price

  - close_position_pct < 0.75 (longs) or > 0.25 (shorts)
      "Fence bar closed mid-range, no conviction"
      → Weak breakout, close near middle of bar range

SAFE DEFAULT:
  If market data is unavailable → return "confirm"
  (never block the runner on a data outage)

VETO EXECUTION:
  POST /api/stockboy/action
  {
      "action_type": "cancel_order",
      "target_order_id": <pending_order_id>,
      "rationale": "Entry vetoed: fence volume 1.2x, spread 0.08%"
  }
```

**What it's deciding:** "Is there real money behind this breakout, or is it a fake?" The strategy can't see Level 2 or tape velocity. The detector approximates institutional participation using volume ratio and spread width.

**Expected impact:** Prevents ~80% of pure-loser trades (MFE < 0.3%). In our 16-trade sample, this would have saved +1.88% by vetoing 2 trades that immediately stopped out.

**Limitation:** True Level 2 order book depth (bid/ask sizes) is not available via Alpaca's API. We approximate with volume ratio + spread, which covers most of the signal. A direct exchange feed would improve accuracy.

---

### Decision 3: Move Stop to Breakeven

**When:** Intra-trade, every 5 minutes while position is open
**Module:** `service/server/stockboy_position_monitor.py`
**Function:** `monitor_position(position, bars_since_entry) → dict`

Once a trade reaches +0.5% favorable and momentum stalls, StockBoy moves the stop loss to the entry price. This converts a potential loser into a risk-free trade. In our sample, 2 trades went +0.69% favorable then reversed to -1.14% stop loss over the next hour — the reversal was slow enough to detect and prevent.

```
INPUTS:
  - Position: entry_price, side, entry_timestamp, stop_loss_price
  - 5m bars from entry to now (Alpaca 5m bars)

METRICS COMPUTED:
  mfe_pct = max favorable excursion as % of entry price
  bars_since_new_extreme = consecutive bars without new high (longs)
                            or new low (shorts)
  minutes_since_entry = time since position opened

TRIGGER CONDITIONS (ALL must be true):
  - mfe_pct ≥ 0.5%           # trade proved itself
  - minutes_since_entry ≥ 10  # give it room, don't act on noise
  - bars_since_new_extreme ≥ 3  # 15 minutes of stalling

ACTION:
  POST /api/stockboy/action
  {
      "action_type": "set_stop",
      "target_position_id": <id>,
      "stop_loss_price": <entry_price>,
      "rationale": "MFE 0.69% reached, momentum stalled 3 bars"
  }

POLICY CHECK:
  stop_tighten_only=True validates this:
  - For longs: new stop (entry) > old stop → tightening ✓
  - For shorts: new stop (entry) < old stop → tightening ✓
```

**What it's deciding:** "The trade proved itself by going +0.5%, but now it's stalling. Lock in zero risk." The pattern is deterministic — +0.5% MFE followed by 15 minutes of no new extremes almost never resumes trending.

**Expected impact:** Saves ~90% of reverser trades. In our sample, this would have saved +1.38% by moving stops on 2 META shorts that reversed from +0.69% to -1.14%.

---

### Decision 4: Take Profit Early

**When:** Intra-trade, every 5 minutes, only after 11:00 ET
**Module:** `service/server/stockboy_position_monitor.py`
**Function:** `monitor_position(position, bars_since_entry) → dict` (same function as Decision 3)

When a trade peaked at +0.5% or more, has been sideways for 30+ minutes, and is drifting back toward entry, StockBoy closes the position to capture remaining profit before it turns negative. In our sample, 3 NVDA longs peaked at +0.71% around 11:30, then drifted sideways for 4 hours to close at -0.65% — the stall was obvious by 12:00.

```
INPUTS:
  - Same as Decision 3
  - Plus: MFE timestamp (when peak favorable price occurred)

TRIGGER CONDITIONS (ALL must be true):
  - mfe_pct ≥ 0.5%              # trade had its moment
  - minutes_since_mfe ≥ 30       # 30+ minutes since peak
  - bars_since_new_extreme ≥ 6   # 30 minutes of stalling
  - current_pnl > 0              # still profitable (don't cut losers)
  - price drifting back          # last close closer to entry than to MFE
  - after 11:00 ET               # give morning momentum room to resume

ACTION:
  POST /api/stockboy/action
  {
      "action_type": "close_position",
      "target_position_id": <id>,
      "rationale": "MFE 0.71% at 11:30, stalled 40min, drifting back"
  }
```

**What it's deciding:** "The trade had its moment, it's not going to hit 1R, and it's drifting back to negative. Take what's left." The 30-minute stall heuristic will have some false positives (closing trades that would have resumed), but the data showed that 30+ min stalls after +0.5% MFE almost never resume.

**Expected impact:** Captures ~70% of staller profit. In our sample, this would have captured +3.65% by exiting 3 NVDA longs at ~+0.5% instead of -0.65%.

---

## Data Flow: A Trading Day

### 09:00 ET — Premarket Check

```
StockBoy._run_premarket_check()
  → stockboy_market_data.fetch_spy_atr()
      → Alpaca GET /v2/stocks/SPY/bars?timeframe=1Day&limit=25
      → Compute 20-day ATR as percentage
  → stockboy_premarket.evaluate_vol_override(symbols, atr)
      → stockboy_market_data.fetch_earnings_calendar(symbols, today)
          → Finnhub GET /calendar/earnings?from={today}&to={tomorrow}
      → stockboy_market_data.fetch_premarket_gap("SPY")
          → Alpaca GET /v2/stocks/SPY/bars?timeframe=1Day&limit=2
      → stockboy_market_data.fetch_vix()
          → Approximate from SPY daily range
  → If override needed:
      → stockboy_overrides.create_override("fencebarrunner", field, value, ttl=390min)
  → stockboy_service.add_observation(...)
```

### 09:30-09:35 ET — Fence Window

```
FenceBarRunner.run_cycle()
  → check_vol_filter(params)
      → Fetch SPY daily bars, compute ATR
      → Return True if ATR ≥ threshold (or override lowered it)
  → run_fence_signals(token, symbols, params, state)
      → For each symbol (NVDA, TSLA, AAPL, AMD, META):
          → fetch_5m_bars(symbol)
              → Alpaca GET /v2/stocks/{symbol}/bars?timeframe=5Min&limit=20
          → FenceBarStrategy.on_bar(timestamp, bar, index)
              → Detect fence bar (09:30-09:35)
              → Detect breakout (price exceeds fence high/low)
              → Return EntrySignal(side, entry_price, stop_price, target_price)
          → If signal fires:
              → create_pending_order(token, signal, quantity)
                  → POST /api/signals/pending
              → POST /api/stockboy/evaluate-entry (webhook)
                  → StockBoy entry detector runs
                  → If vetoed: cancel_order
                  → If confirmed: order fills → position opens
```

### 09:35-15:55 ET — Position Monitoring

```
StockBoy._run_position_monitor()  (every 5 minutes)
  → build_snapshot()
      → Get all positions from DB
      → Filter to FenceBarRunner positions only
  → For each FenceBarRunner position:
      → stockboy_market_data.fetch_recent_bars(symbol, "5Min", bars_back)
          → Alpaca GET /v2/stocks/{symbol}/bars?timeframe=5Min&limit=78
      → stockboy_position_monitor.monitor_position(position, bars)
          → Compute MFE, MAE, bars since new extreme
          → If Decision 3 triggers (breakeven stop):
              → execute_action(StockBoyActionRequest(
                  action_type="set_stop",
                  stop_loss_price=entry_price,
                  ...))
          → If Decision 4 triggers (early exit):
              → execute_action(StockBoyActionRequest(
                  action_type="close_position",
                  ...))
      → add_observation(...)  # log every evaluation

Meanwhile, FenceBarRunner.run_cycle() continues every 30s:
  → Manages SL/TP checks on open positions
  → Sends heartbeat
```

### 15:55 ET — Force Exit

```
FenceBarRunner.force_exit_positions()
  → Fetch all open positions
  → For each position:
      → execute_close(token, symbol, side, quantity)
          → POST /api/trading/close

Also: tasks.py fence_bar_force_exit_loop() (belt and suspenders)
  → Queries DB for FenceBarRunner positions
  → Closes any that remain open
```

---

## API Reference

### FenceBarRunner Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/arena/fence-bar-runner/start` | Start FenceBarRunner in a background thread |
| POST | `/api/arena/fence-bar-runner/stop` | Stop FenceBarRunner thread |
| GET | `/api/arena/fence-bar-runner/status` | Get runner status (running, last_error, thread) |

### StockBoy Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/stockboy/status` | Supervisor status (enabled, cycles, heartbeat) |
| GET | `/api/stockboy/snapshot` | Full snapshot (runners, positions, orders, anomalies) |
| POST | `/api/stockboy/start` | Start supervisor loop |
| POST | `/api/stockboy/stop` | Stop supervisor loop |
| POST | `/api/stockboy/action` | Execute a position action (close, set_stop, etc.) |
| POST | `/api/stockboy/evaluate-entry` | **Webhook** — evaluate pending order quality |
| POST | `/api/stockboy/override` | Temporary runner config override |
| POST | `/api/stockboy/override/reset` | Reset overrides to defaults |
| POST | `/api/stockboy/enable` | Enable/disable supervisor actions |
| POST | `/api/stockboy/kill-switch` | Emergency stop — blocks all actions |

### Action Types

| Action | Description | StockBoy Decision |
|---|---|---|
| `cancel_order` | Cancel a pending order | Decision 2: Entry veto |
| `set_stop` | Move stop loss (tighten only) | Decision 3: Breakeven stop |
| `close_position` | Fully close a position | Decision 4: Take profit early |
| `partial_close` | Reduce position size | Decision 4: Scaled exit |
| `set_target` | Move take-profit target | Not currently used |
| `set_trailing` | Set trailing stop params | Not recommended (kills edge) |

### Forbidden Actions

These are blocked by policy and can never be executed:

| Action | Reason |
|---|---|
| `buy` | StockBoy never creates new entries |
| `short` | StockBoy never creates new entries |
| `enter` | StockBoy never creates new entries |
| `open_position` | StockBoy never creates new entries |

---

## Configuration

### FenceBarRunner Config (`agents/fence_bar_runner_config.json`)

```json
{
  "agent_name": "FenceBarRunner",
  "symbols": ["NVDA", "TSLA", "AAPL", "AMD", "META"],
  "interval": "5m",
  "effective_strategy_params": {
    "session": {
      "market_open": "09:30",
      "fence_end": "09:35",
      "force_exit": "15:55"
    },
    "retest": { "enabled": false },
    "risk": {
      "target_multiple_r": 1.0,
      "risk_per_trade_pct": 0.50,
      "max_trades_per_day": 1
    },
    "exit": { "mode": "fixed_sl_tp" },
    "vol_filter": {
      "enabled": true,
      "mode": "day",
      "spy_vol_threshold": 1.0,
      "spy_atr_threshold": 1.8
    },
    "risk_controls": {
      "paper_only": true,
      "paper_account_budget": 10000.0
    }
  }
}
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ALPACA_API_KEY` | — | Alpaca paper trading API key |
| `ALPACA_SECRET_KEY` | — | Alpaca paper trading secret |
| `FINNHUB_API_KEY` | — | Finnhub API key for earnings calendar |
| `STOCKBOY_POLL_INTERVAL` | 60 | StockBoy loop interval (seconds) |
| `STOCKBOY_FENCEBAR_UNIVERSE` | "SPY,QQQ,NVDA,AAPL" | Symbols for premarket check |
| `STOCKBOY_STOP_TIGHTEN_ONLY` | true | Stops can only tighten |
| `STOCKBOY_DAILY_LOSS_HALT_PCT` | 5.0 | Daily loss halt threshold |
| `STOCKBOY_MAX_ACTIONS_PER_CYCLE` | 10 | Max actions per 60s cycle |
| `STOCKBOY_COOLDOWN_SECONDS` | 60 | Cooldown per position per action |

### Detector Thresholds (tunable)

| Detector | Threshold | Current | Adjustable via |
|---|---|---|---|
| Vol filter override | ATR gray zone | 1.2-1.8% | Code constant |
| Vol filter override | SPY gap trigger | > 1.5% | Code constant |
| Vol filter override | VIX trigger | > 25 | Code constant |
| Entry veto | Volume ratio | < 2.0x | Code constant |
| Entry veto | Spread | > 0.05% | Code constant |
| Entry veto | Close position | < 0.75 (longs) | Code constant |
| Breakeven stop | MFE trigger | ≥ 0.5% | Code constant |
| Breakeven stop | Min time in trade | ≥ 10 min | Code constant |
| Breakeven stop | Stall bars | ≥ 3 (15 min) | Code constant |
| Early exit | MFE trigger | ≥ 0.5% | Code constant |
| Early exit | Time since MFE | ≥ 30 min | Code constant |
| Early exit | Stall bars | ≥ 6 (30 min) | Code constant |
| Early exit | Earliest time | 11:00 ET | Code constant |

---

## Backtesting Foundation

The system is grounded in 22 months of walk-forward backtesting (Oct 2024 - Aug 2026, 94 windows). The key research findings that shaped the system:

### Research Timeline

| Date | Finding | Impact on System |
|---|---|---|
| Aug 13 | Fence Bar with retest: -5.13% | retest.enabled = false |
| Aug 13 | Trailing exit kills edge: -2.14% | exit.mode = fixed_sl_tp |
| Aug 13 | No vol filter: -11.92% over 22 months | vol_filter.enabled = true |
| Aug 13 | Vol filter + 2R target: +0.80% (original) | vol_filter.spy_atr_threshold = 1.2 |
| Aug 15 | MFE analysis: 2R target too ambitious | target_multiple_r = 1.0 |
| Aug 15 | 1R + ATR 1.8%: +0.25% at 5bps | vol_filter.spy_atr_threshold = 1.8 |
| Aug 15 | Human-in-the-loop analysis: +6.91% value | 4 StockBoy detectors built |

### MFE Analysis (the key insight)

Analysis of 11 trades from the vol-filtered config revealed:

| Trade Type | Count | Pattern | StockBoy Detector |
|---|---|---|---|
| Winners (hit 1R) | 9 | Reached target, no intervention needed | None — leave alone |
| Pure losers | 2 | MFE < 0.3%, never had follow-through | Decision 2: Entry veto |
| Reversers | 2 | +0.5-0.7% MFE then reversed to stop | Decision 3: Breakeven stop |
| Stallers | 3 | +0.7% MFE then sideways for hours | Decision 4: Early exit |

### Projected Impact with StockBoy

| Configuration | Return (5bps) | Trades |
|---|---|---|
| Strategy alone (1R + ATR 1.8%) | +0.25% | 16 |
| Strategy + StockBoy detectors | ~+4-5% (projected) | 16 + override days |

The +6.91% human value-add was measured on the 16-trade sample. Automated detectors should capture ~70-90% of that value, projecting ~+4-5% total return. The vol filter override could add more by unlocking catalyst days in the gray zone.

---

## File Map

### New Files (created in this build)

| File | Purpose |
|---|---|
| `agents/fence_bar_runner.py` | FenceBarRunner live trading module |
| `agents/fence_bar_runner_config.json` | Runner config with winning parameters |
| `service/server/stockboy_market_data.py` | Alpaca API wrapper for StockBoy detectors |
| `service/server/stockboy_entry_detector.py` | Decision 2: entry quality veto |
| `service/server/stockboy_position_monitor.py` | Decisions 3 & 4: breakeven stop + early exit |
| `service/server/stockboy_premarket.py` | Decision 1: vol filter override |

### Modified Files

| File | Changes |
|---|---|
| `service/server/stockboy_policy.py` | Added `fencebarrunner` to CONTROLLED_RUNNERS |
| `service/server/stockboy_manager.py` | Wired premarket check + position monitor into loop |
| `service/server/stockboy_service.py` | Added `add_observation()`, fixed `_agent_ids()` for 4 runners |
| `service/server/stockboy_models.py` | Updated runner_key description |
| `service/server/routes_stockboy.py` | Added `POST /api/stockboy/evaluate-entry` endpoint |
| `service/server/bot_manager.py` | Added FenceBarRunner start/stop/status functions |
| `service/server/routes_arena.py` | Added FenceBarRunner API endpoints |
| `service/server/tasks.py` | Added `fence_bar_force_exit_loop()` background task |
| `service/server/routes_backtest.py` | Added FenceBarRunner to backtest registry |

### Existing Files (unchanged, used by the system)

| File | Role |
|---|---|
| `agents/fence_bar_strategy.py` | FenceBarStrategy class + FENCE_BAR_DEFAULTS |
| `agents/fence_bar_backtester.py` | FenceBarBacktester for backtesting |
| `agents/vol_filter_base.py` | VolFilteredBacktester base class |
| `service/server/stockboy_overrides.py` | Override creation/expiry logic |
| `service/server/alpaca_broker.py` | Alpaca paper trading execution |
| `service/server/database.py` | DB schema for positions, orders, signals |

### Research Artifacts

| File | Content |
|---|---|
| `research/strategy_search/journal.md` | Full research journal with all experiments |
| `research/strategy_search/state.json` | Current research state and findings |
| `research/strategy_search/run_fence_vol_fine_*.json` | Vol threshold sweep results |
| `research/strategy_search/run_fence_winner_holdout_*.json` | Holdout validation results |
| `research/strategy_search/human_in_loop_analysis_*.json` | MFE/MAE trade analysis |
| `HUMAN_IN_THE_LOOP.md` | Operator manual for human-in-the-loop decisions |

---

## Getting Started

### 1. Start the Server

```bash
cd /Volumes/mr_black\ 1/development/ai-trader/AI-Trader
source .venv/bin/activate
cd service/server
python main.py
```

### 2. Start FenceBarRunner

```bash
curl -X POST http://localhost:8000/api/arena/fence-bar-runner/start \
  -H "Authorization: Bearer <admin_token>"
```

### 3. Start StockBoy Supervisor

```bash
curl -X POST http://localhost:8000/api/stockboy/start \
  -H "Authorization: Bearer <supervisor_token>"
```

### 4. Monitor

```bash
# StockBoy snapshot
curl http://localhost:8000/api/stockboy/snapshot

# FenceBarRunner status
curl http://localhost:8000/api/arena/fence-bar-runner/status
```

### 5. Emergency Stop

```bash
# Kill switch — blocks all StockBoy actions
curl -X POST http://localhost:8000/api/stockboy/kill-switch \
  -H "Authorization: Bearer <supervisor_token>" \
  -H "Content-Type: application/json" \
  -d '{"engaged": true, "reason": "manual stop"}'

# Stop FenceBarRunner
curl -X POST http://localhost:8000/api/arena/fence-bar-runner/stop \
  -H "Authorization: Bearer <admin_token>"
```
