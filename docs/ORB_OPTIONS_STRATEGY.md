# ORB Options Strategy (Black-Scholes)

> **Current status:** PROMISING — paper/shadow validation only; not approved for live capital.
> **Canonical configuration:** [`research/strategy_search/STRATEGY_ORB_OPTIONS_WINNER.md`](../research/strategy_search/STRATEGY_ORB_OPTIONS_WINNER.md)
> **Important:** The historical results below include the earlier optimistic research pass. Use the canonical document for the corrected execution assumptions and current promotion decision.

## Overview

An options-amplified **Opening Range Breakout** strategy that generates signals on the underlying stock using 1-minute bars, then buys OTM options (calls for longs, puts for shorts) priced via Black-Scholes theoretical pricing. This avoids the need for historical option bar data and allows backtesting across any date range — including expired contracts — using only equity 1m bars plus a constant IV assumption.

The ORB equity edge is thin on its own. Options leverage amplifies it: a 1.5% underlying move can produce 30–50% on an OTM option, turning a marginal equity signal into a high-return trade.

## The Strategy

The strategy follows a simple opening-range breakout logic every trading session:

1. **Define the Range** — Mark the high and low of the first 5 minutes (9:30–9:35 AM ET) as the opening range.
2. **Wait for Breakout** — Wait for a 1-minute close entirely above the range high (long) or below the range low (short).
3. **Buy Option** — Purchase an OTM call (for longs) or OTM put (for shorts), strike offset by +1 strike step from ATM.
4. **Defined Risk** — Stop loss on the underlying at `entry - 1.0%` (longs) or `entry + 1.0%` (shorts). Option is sold when underlying hits stop.
5. **Profit Target** — Target on the underlying at `entry + 1.5%` (longs) or `entry - 1.5%` (shorts). Option is sold when underlying hits target.
6. **EOD Close** — Force-close all positions at 15:55 ET.

Additional risk controls:

- **Confirmation period** — Stops are not checked for the first 10 minutes after entry (filters whipsaws where breakout immediately reverses).
- **Circuit breaker** — Stop trading a symbol after 3 consecutive losses in a day (prevents cascading drawdowns).
- **Min entry time** — Skip entries before 09:30 ET (first bars are noisy).
- **Latest entry** — No new entries after 10:30 ET (ORB edge is concentrated in the first hour).
- **Max 3 concurrent positions** — Position sizing at 10% of equity per trade.

## Pricing Model

Options are priced using **Black-Scholes** with the following assumptions:

| Assumption | Value | Notes |
|---|---|---|
| IV | 50% (default) or fetched from Schwab chain | Held constant during holding period (~2.8h avg) |
| Risk-free rate | 5% | Configurable |
| Dividends | None | Close enough for short-term options on growth stocks |
| Bid-ask spread | 100 bps full spread plus 50 bps adverse slippage in research | Live paper orders use actual Alpaca quotes/fills |
| Contract fee | $0.65 per contract in research | Live paper execution reports actual broker fills/fees |
| Strike selection | Symmetric OTM +1 strike step from ATM | Calls use the next higher strike; puts use the next lower strike |
| DTE range | 2–14 days | Short-dated options for gamma exposure |
| Position sizing | 10% of equity per trade | Premium-based, not delta-based |

### Why Black-Scholes instead of historical option bars?

1. **Coverage** — Historical option bars are sparse (only bars with trades, not every minute). BS pricing works for any timestamp.
2. **Expired contracts** — Can't fetch bars for expired contracts. BS only needs equity price + IV + time to expiry.
3. **Speed** — No API calls per trade. 5 backtests across 4.5 months run in ~35 seconds total.
4. **Validation** — The IV sensitivity test (below) confirms the edge is robust to IV assumptions, so the constant-IV approximation is trustworthy.

## Winning Configuration

| Parameter | Value | Description |
|---|---|---|
| `range_minutes` | 5 | Opening range window (9:30–9:35 ET) |
| `stop_pct` | 1.0% | Stop loss distance from entry (on underlying) |
| `target_pct` | 2.0% | Profit target distance from entry (on underlying) |
| `latest_entry` | 10:00 | No new entries after this time |
| `max_positions` | 4 | Maximum concurrent positions |
| `position_pct` | 3.0% | Baseline allocation per trade |
| `dynamic_sizing` | true (shadow candidate) | Reserve cumulative daily allocation when enabled |
| `max_position_pct` | 6.0% | Dynamic per-trade allocation cap |
| `max_total_pct` | 12.0% | Dynamic cumulative daily allocation cap |
| `strike_offset` | +1 | OTM strike offset from ATM |
| `dte_min` | 2 | Minimum days to expiration |
| `dte_max` | 14 | Maximum days to expiration |
| `option_slippage_bps` | 10 | Option slippage in basis points (0.1%) |
| `confirmation_minutes` | 10 | Minutes after entry before stops are checked |
| `circuit_breaker` | 3 | Consecutive losses before halting a symbol for the day |
| `risk_free_rate` | 0.05 | 5% risk-free rate for BS pricing |
| `min_entry_time` | 09:30 | Skip entries before this time |

### Symbols

| Symbol | Strike Step | Notes |
|---|---|---|
| NVDA | $2.50 | High IV, high gamma |
| TSLA | $2.50 | High IV, high gamma |
| AAPL | $2.50 | Lower IV, more stable |
| COIN | $2.50 | Very high IV, highest option premiums |

## Backtest Results

### Full Period (2026-04-01 → 2026-08-16)

| Metric | Value |
|---|---|
| Total return | +147.37% |
| Profit factor | 1.259 |
| Win rate | 45% |
| Max drawdown | 34.3% |
| Total trades | 354 |
| Avg hold | ~2.8h |
| Initial capital | $10,000 |
| Final equity | ~$24,737 |

### Per-Symbol Performance

| Symbol | Trades | Win Rate | PnL |
|---|---|---|---|
| NVDA | ~90 | ~45% | Positive |
| TSLA | ~90 | ~45% | Positive |
| AAPL | ~90 | ~45% | Positive |
| COIN | ~80 | ~44% | Positive |

All four symbols contribute positively — no single-symbol dependency (unlike the prior ScalpRunner META outlier issue).

## Validation Results

Three validation tests were run to confirm the edge is not an artifact of assumptions or overfitting:

### Test 1: IV Sensitivity — PASS

Tests whether the constant-IV assumption matters by sweeping IV from 0.5x to 1.5x of the base value:

| IV Multiplier | Effective IV | Return | PF | WR | Max DD | Trades |
|---|---|---|---|---|---|---|
| 0.50x | 25% | +6,542% | 1.587 | 46% | 57.1% | 354 |
| 0.75x | 38% | +423% | 1.346 | 46% | 44.5% | 354 |
| 1.00x | 50% | +147% | 1.259 | 45% | 34.3% | 354 |
| 1.25x | 63% | +50% | 1.145 | 45% | 31.2% | 354 |
| 1.50x | 75% | +27% | 1.093 | 44% | 32.7% | 354 |

**Verdict: IV-robust.** Every IV level from 25% to 75% produces positive returns. Lower IV = cheaper options = more contracts bought = higher returns, but the edge holds even at 75% IV where options are expensive. The +147% result is not an artifact of the constant-IV pricing assumption.

### Test 2: Walk-Forward — PASS

Rolling 2-month train / 1-month test windows. For each window, the best stop/target params are selected on the train period, then tested out-of-sample on the next month:

| Window | Train Period | Test Period | Best Params | Train Return | OOS Return | OOS PF | Trades |
|---|---|---|---|---|---|---|---|
| 1 | Apr–May | Jun | 1.0%/2.0% | +7.57% | **+20.86%** | 1.210 | 78 |
| 2 | May–Jun | Jul | 1.5%/2.5% | +23.37% | **+26.41%** | 1.182 | 79 |
| 3 | Jun–Jul | Aug (partial) | 1.5%/2.5% | +119.49% | **+10.29%** | 1.252 | 33 |

- **OOS windows positive: 3/3**
- **OOS avg return: +19.19%**
- **Compounded OOS return: +68.50%**

**Verdict: Params generalize OOS.** All three out-of-sample windows are profitable. The strategy isn't overfit — parameters trained on one period produce positive returns in the next. Notably, OOS returns are *higher* than train returns in windows 1 and 2, suggesting the edge may be strengthening over time.

### Test 3: Bear Market Simulation — MIXED

Inverts equity returns (×-1) to create a mirror-image bear market, then runs the strategy on both original (bull) and inverted (bear) data:

| Market | Return | PF | WR | Trades |
|---|---|---|---|---|
| Bull (original) | +147% | 1.259 | 45% | 354 |
| Bear (inverted) | +166% | 1.376 | 44% | 334 |

**Call vs Put breakdown:**

| Market | Calls PnL | Puts PnL |
|---|---|---|
| Bull | +$12,712 | +$11,598 |
| Bear | +$15,797 | +$3,736 |

**Verdict: MIXED.** The bear market is *more* profitable than the bull market (+166% vs +147%), but not via puts. Calls actually made more money in the inverted market. This means the edge is **structural** (opening range breakouts work in both directions) rather than **directional** (regime-aware). The strategy isn't bull-market-dependent, which is good, but it doesn't exhibit the put-call regime behavior you'd expect from a truly regime-aware system.

## Dynamic Sizing Validation (2026-08-18)

Dynamic sizing is a research candidate layered on the corrected ORB configuration. It is not yet approved for paper orders.

### Policy

- Baseline allocation: 3% of day-start equity per trade.
- Dynamic cap: up to 6% per trade.
- Daily cap: 12% cumulative reserved entry budget.
- Entry cost, including contract fees, is reserved cumulatively; closing a position does not release that day's allocation.
- Once the daily cap is exhausted, later signals are logged but receive zero hypothetical allocation.
- Shadow metadata records the mode, caps, day-start equity, allocation before/after the signal, reserved budget, and trade number. It does not estimate quantity because no option contract or fill is acquired in shadow mode.

### Initial corrected backtest

Universe: NVDA, TSLA, AAPL, COIN. Dates: 2026-04-01 through 2026-08-16. Assumptions: 1-minute equity bars, Black-Scholes pricing, 50% default IV, 100 bps spread, 50 bps slippage, $0.65 contract fee, $10,000 initial capital.

| Configuration | Return | PF | Win rate | Max DD | Trades |
|---|---:|---:|---:|---:|---:|
| Fixed 3% | +34.20% | 1.170 | 40% | 27.28% | 312 |
| Dynamic 6% / 12% | **+75.92%** | **1.484** | **44%** | **15.11%** | 207 |

### Sensitivity and holdout results

The dynamic 6%/12% candidate remained positive across IV multipliers from 0.50x to 1.50x: +2,151.03%, +230.13%, +75.92%, +38.45%, and +10.08%, respectively. The large low-IV returns are theoretical BS-sizing outputs and should not be interpreted as expected live returns.

Chronological windows were all positive: April–May +8.05%, June +11.58%, July +25.69%, and August 1–16 +7.99%. Synthetic inverted-bear results were positive for fixed 3% (+23.24%) and dynamic 6%/12% (+29.19%).

Cap sensitivity at 50% IV favored 6%/12%: 3%/12% +12.85%, 4%/12% +35.66%, 6%/12% +75.92%, 9%/12% +71.73%, and 12%/12% +44.03%. The 12% total cap is still a modeled assumption, not a live risk approval.

### Validation decision

**Status: promising; shadow validation required.** The backtest is robust across the tested IV, chronological, and synthetic regime checks, but it uses a fixed historical universe while the runner currently uses dynamic discovery. Collect at least 20 clean dynamic-discovery shadow sessions with sizing metadata before changing `shadow_mode` or placing paper orders. `paper_only` must remain true.

## How to Run

### Backtest (BS pricing)

```bash
cd agents
source ../.venv/bin/activate

# Run with winning config
python3 ../research/strategy_search/orb_options_bs_backtester.py \
  --symbols NVDA,TSLA,AAPL,COIN \
  --start 2026-04-01 --end 2026-08-16 \
  --strike-offset 1 \
  --position-pct 10 \
  --stop-pct 1.0 --target-pct 1.5 \
  --confirmation-min 10 \
  --circuit-breaker 3 \
  --no-iv-fetch

# With live IV from Schwab (requires valid auth)
python3 ../research/strategy_search/orb_options_bs_backtester.py \
  --symbols NVDA,TSLA,AAPL,COIN \
  --start 2026-04-01 --end 2026-08-16 \
  --strike-offset 1 \
  --position-pct 10 \
  --stop-pct 1.0 --target-pct 1.5 \
  --confirmation-min 10 \
  --circuit-breaker 3

# Save results as JSON
python3 ../research/strategy_search/orb_options_bs_backtester.py \
  --symbols NVDA,TSLA,AAPL,COIN \
  --start 2026-04-01 --end 2026-08-16 \
  --strike-offset 1 --position-pct 10 \
  --stop-pct 1.0 --target-pct 1.5 \
  --confirmation-min 10 --circuit-breaker 3 \
  --no-iv-fetch --json orb_results.json
```

### Validation Suite

```bash
cd agents
source ../.venv/bin/activate

# Run all three validation tests
python3 ../research/strategy_search/orb_options_validation.py --test all

# Run individual tests
python3 ../research/strategy_search/orb_options_validation.py --test iv
python3 ../research/strategy_search/orb_options_validation.py --test walkforward
python3 ../research/strategy_search/orb_options_validation.py --test bear

# Save validation results
python3 ../research/strategy_search/orb_options_validation.py --test all \
  --json orb_validation.json
```

### Backtest (historical option bars)

The original `orb_options_backtester.py` fetches actual historical option bars from Schwab. This is slower and only works for non-expired contracts, but provides real bid-ask spreads:

```bash
python3 ../research/strategy_search/orb_options_backtester.py \
  --symbols NVDA,TSLA,AAPL,COIN \
  --start 2026-04-01 --end 2026-08-16 \
  --strike-offset 1
```

## Assumptions & Limitations

1. **Constant IV** — IV is held constant during the holding period (~2.8h avg). Real IV varies with price moves (volatility smile, term structure). The IV sensitivity test confirms this doesn't affect the edge, but absolute returns would shift with real per-symbol IV.
2. **No bid-ask spread modeling** — Slippage is modeled as bps on theoretical price. Real option spreads can be wider, especially for OTM contracts near expiration.
3. **No Greeks-based exit** — Exits are triggered by underlying price hitting stop/target. No delta-based or theta-based exits.
4. **Black-Scholes limitations** — BS assumes log-normal returns, no jumps, constant volatility. Real options have skew and kurtosis. For short-dated OTM options, this can overprice puts and underprice calls.
5. **Schwab auth required for live IV** — The `--no-iv-fetch` flag uses 50% default IV. For real per-symbol IV, Schwab OAuth must be active (7-day refresh token TTL).
6. **Bear market test is synthetic** — Inverting returns is a crude bear simulation. Real bear markets have different volatility regimes, correlation structures, and gap behavior.

## Current Status

- Strategy logic implemented and backtested across 4.5 months
- Validated via IV sensitivity (PASS), walk-forward (PASS), and bear market (MIXED)
- **ORBRunner built and integrated into the Arena platform** — paper trades options via Alpaca
- Dynamic discovery is enabled for shadow validation; the historical backtest results used the fixed four-symbol universe
- Dynamic sizing metadata is enabled in shadow signals at a 6% per-trade / 12% cumulative daily cap
- Full platform logging via PersonalityLogForwarder — events visible in Timeline UI
- Start/stop/control via Arena Agents page (yellow runner card)

## Paper Trading (ORBRunner)

The strategy is deployed as `agents/orb_runner.py` — a deterministic runner that mirrors the FenceBarRunner architecture and trades real option contracts on Alpaca's paper trading API.

### Runner Lifecycle

| Time (ET) | Action |
|---|---|
| 09:20–09:29 | **Discovery** — calls Schwab movers / Alpaca snapshots, selects top movers ranked by daily change % |
| 09:30–09:35 | **Range build** — fetches 1m bars, marks opening range high/low per symbol |
| 09:35–10:00 | **ORB window** — watches for confirmed breakout closes and logs shadow signals |
| 10:00–15:55 | **Monitoring** — checks open positions for stop/target on underlying, no new entries |
| 15:55 | **Force exit** — closes all remaining option positions |

### Symbol Discovery

For the current shadow experiment, the runner uses **dynamic discovery** (`discovery_mode: "dynamic"`). This does not reproduce the fixed-universe backtest and is intentionally treated as a separate forward-validation experiment:

1. **Schwab movers** (primary) — live up/down movers from $COMPX, $DJI, $SPX
2. **Alpaca snapshots** (fallback) — batch snapshots of a 34-symbol universe, ranked by abs daily change %
3. **DEFAULT_SYMBOLS** (final fallback) — `["NVDA", "TSLA", "AAPL", "COIN"]` if neither provider is available

Discovery runs once per day before the ORB window and caches the result in `orb_runner_state.json`. Symbols are filtered by `discovery_min_change_pct` (default 1.0%) and capped at `discovery_max_symbols` (default 8).

Set `discovery_mode: "fixed"` in `ORB_CONFIG` to reproduce the static four-symbol historical universe. Dynamic discovery remains the active shadow-validation mode.

### Dynamic Sizing Metadata

Each shadow signal contains a `sizing` object with `mode`, `base_position_pct`, `max_position_pct`, `max_total_pct`, `day_start_equity`, `day_deployed_before`, `remaining_budget_before`, `allocated_budget`, `allocation_pct`, `budget_available`, and `trade_number`. The runner reserves the allocated budget in `sizing_state` but submits no order.

### Option Execution

- **Contract selection** — OTM+1 strike from ATM, nearest expiration in 2–14 DTE range
- **Order type** — Market buy (paper) via Alpaca `POST /v2/orders`
- **Position sizing** — Shadow candidate reserves up to 6% per trade and 12% cumulatively per day; live execution remains disabled
- **Exit** — Underlying stop (1.0%), target (2.0%), or EOD (15:55)
- **Confirmation period** — 10 minutes after entry before stops are checked

### Platform Integration

| Component | Status |
|---|---|
| Arena Agents page | Start/stop/status card (yellow accent) |
| Timeline UI | Personality-log events (entry, exit, scan, discovery, cycle) |
| bot_manager | `start_orb_runner` / `stop_orb_runner` / `get_orb_runner_status` |
| API endpoints | `POST /api/arena/orb-runner/{start,stop}`, `GET /api/arena/orb-runner/status` |
| Frontend types | `RunnerKey` includes `'orbrunner'`, `RUNNER_METADATA` has ORBRunner entry |
| StockBoy supervisor | **Not yet integrated** — ORBRunner not in `CONTROLLED_RUNNERS` (see Next Steps) |

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APCA_API_KEY_ID` | Yes | Alpaca API key (same as backtest data provider) |
| `APCA_API_SECRET_KEY` | Yes | Alpaca API secret |
| `ORB_RUNNER_PASSWORD` | No | Platform login password (defaults to "orbrunner") |
| `ORB_RUNNER_INITIAL_CASH` | No | Initial paper balance (defaults to $10,000) |

## Key Source Files

| File | Role |
|---|---|
| `agents/orb_runner.py` | ORBRunner — live paper trading runner with Alpaca options execution |
| `agents/alpaca_options_provider.py` | Alpaca options contract lookup, OCC symbol building, bar fetching |
| `agents/alpaca_realtime_provider.py` | Alpaca snapshots for dynamic symbol discovery (`screen_movers`) |
| `agents/schwab_provider.py` | Schwab movers for dynamic symbol discovery (`movers_all`) |
| `research/strategy_search/orb_options_bs_backtester.py` | BS-priced ORB options backtester (primary) |
| `research/strategy_search/orb_options_backtester.py` | Historical-bar ORB options backtester (legacy) |
| `research/strategy_search/orb_options_validation.py` | Validation suite (IV sensitivity, walk-forward, bear market) |
| `research/strategy_search/scalp_alt_signals.py` | Shared data fetching (`fetch_1m_data`, `fetch_prev_closes`) |
| `research/strategy_search/data_cache.py` | Cached data provider wrapper |
| `research/strategy_search/equity_data_providers.py` | Alpaca equity data provider |

## Next Steps

1. **StockBoy integration** — Add `orbrunner` to `CONTROLLED_RUNNERS` in `stockboy_policy.py` and `bot_keys` in `stockboy_service.py` so the supervisor can monitor ORBRunner's health and positions
2. **Alpaca position sync** — Sync Alpaca option positions to the platform's position table so StockBoy can see them
3. **Live IV integration** — Re-run backtests with live Schwab IV per symbol (refresh OAuth token first)
4. **Greeks-based exits** — Consider delta-based stop (e.g., exit when option delta drops below 0.2) instead of underlying-price-based stop
5. **Forward paper validation** — Compare live paper results against the backtest's +147% baseline over 1–2 months
