---
name: orb-runner
description: ORBRunner — opening range breakout options strategy, live paper trading via Alpaca, BS-priced backtesting, and Arena integration
triggers:
  - user
  - model
allowed-tools:
  - read
  - grep
  - glob
---

# ORBRunner — Operational Context

ORBRunner trades OTM options on opening range breakouts via Alpaca's paper trading API. It is a **separate pipeline** from ScalpRunner — own backtester, own config, own execution path. Nothing is shared with `scalp_scan_core.py`.

## Strategy Summary

1. **09:20 ET** — Discover top movers (Schwab movers → Alpaca snapshots → fallback to fixed 4 symbols)
2. **09:30–09:35** — Build 5-min opening range (high/low) per symbol using 1m equity bars
3. **09:35–10:30** — Watch for 1m close above range high (long) or below range low (short)
4. **On breakout** — Buy OTM+1 call (long) or put (short) via Alpaca options API, nearest DTE 2–14
5. **Exit** — Underlying hits 1.0% stop or 1.5% target (10-min confirmation before stops active)
6. **15:55 ET** — Force-close all remaining positions

Risk controls: 3 concurrent positions max, 10% equity per trade, 3-loss circuit breaker per symbol, goal check before trading.

## Key Files

| File | Role |
|---|---|
| `agents/orb_runner.py` | Live runner — the main file. Config, discovery, signals, execution, exits, state, loop |
| `research/strategy_search/orb_options_bs_backtester.py` | BS-priced backtester (historical validation) |
| `research/strategy_search/orb_options_validation.py` | Validation suite (IV sensitivity, walk-forward, bear market) |
| `agents/alpaca_options_provider.py` | Option contract lookup, OCC symbol building, option bar fetching |
| `agents/alpaca_realtime_provider.py` | Alpaca snapshots for dynamic discovery (`screen_movers`) |
| `agents/schwab_provider.py` | Schwab movers for dynamic discovery (`movers_all`) |
| `agents/runner_narrative.py` | Shared structured event emitter (all runners use this) |
| `agents/personality_log_forwarder.py` | Shared HTTP forwarder to Arena personality-log endpoint |
| `docs/ORB_OPTIONS_STRATEGY.md` | Full strategy doc with backtest results and validation |

## Config

All config lives in `ORB_CONFIG` at the top of `agents/orb_runner.py`. It does **not** use `strategy_registry.py` or the 3-layer parameter resolution model. To change behavior, edit `ORB_CONFIG` directly or override via the platform's `fetch_config(token)` watchlist field.

| Key | Default | Description |
|---|---|---|
| `range_minutes` | 5 | Opening range window length |
| `stop_pct` | 1.0 | Stop loss distance on underlying (%) |
| `target_pct` | 1.5 | Profit target distance on underlying (%) |
| `latest_entry` | "10:30" | No new entries after this ET time |
| `max_positions` | 3 | Max concurrent option positions |
| `position_pct` | 10.0 | % of equity per trade (option premium) |
| `strike_offset` | 1 | OTM strike offset from ATM |
| `dte_min` / `dte_max` | 2 / 14 | Days to expiration range |
| `confirmation_minutes` | 10 | Minutes after entry before stops checked |
| `circuit_breaker` | 3 | Consecutive losses before halting a symbol |
| `discovery_mode` | "dynamic" | "dynamic" (movers) or "fixed" (DEFAULT_SYMBOLS) |
| `discovery_max_symbols` | 8 | Max symbols after discovery |
| `discovery_min_change_pct` | 1.0 | Min abs daily change % to qualify |

Strike steps per symbol are in `STRIKE_STEPS` dict (e.g. NVDA $2.50, AAPL $2.50, AMD $0.50).

## Symbol Discovery

`discover_movers(config)` in `orb_runner.py` — runs once per day at ~09:20 ET, cached in `state["discovered_symbols"][date]`:

1. **Schwab movers** (primary) — `schwab_provider.movers_all()` fetches up/down movers from $COMPX, $DJI, $SPX
2. **Alpaca snapshots** (fallback) — `alpaca_realtime_provider.screen_movers(SCANNER_UNIVERSE, top_n)` ranks 34 symbols by abs daily change %
3. **DEFAULT_SYMBOLS** (final fallback) — `["NVDA", "TSLA", "AAPL", "COIN"]` if neither provider available

Filtered by `discovery_min_change_pct`, capped at `discovery_max_symbols`. Set `discovery_mode: "fixed"` to skip discovery and use DEFAULT_SYMBOLS (matches backtest).

## State Persistence

`agents/orb_runner_state.json` — auto-created on first run, holds:
- `consecutive_losses` — global circuit breaker counter
- `day_loss_streaks` — per-symbol loss counter for the day
- `signals_posted` — symbol → last signal date (prevents duplicate signals)
- `open_positions` — symbol → position metadata (occ_symbol, qty, entry, stop, target, option_type)
- `discovered_symbols` — date → list of movers selected that day
- `last_force_exit_date` — tracks EOD close completion

## Arena Integration

| Layer | Detail |
|---|---|
| `bot_manager.py` | `start_orb_runner()`, `stop_orb_runner()`, `get_orb_runner_status()` |
| `routes_arena.py` | `POST /api/arena/orb-runner/start`, `POST /api/arena/orb-runner/stop`, `GET /api/arena/orb-runner/status` |
| Frontend types | `RunnerKey` includes `'orbrunner'`, `RUNNER_METADATA` has ORBRunner entry (yellow accent) |
| AgentsPage.tsx | Start/stop/status card with crash error display |
| Timeline UI | Personality-log events (entry, exit, scan, discovery, cycle, portfolio) via `useTimelineData.ts` |
| StockBoy | **Not integrated** — not in `CONTROLLED_RUNNERS` in `stockboy_policy.py` |

## Logging

ORBRunner uses the same logging stack as all other runners:
- `PersonalityLogForwarder(runner="orbrunner")` — background thread POSTs JSON events to `/api/arena/personality-log/batch` every 2s
- `RunnerNarrative("orbrunner", printer=_forwarder.printer)` — structured events with phase/kind/outcome/priority/facts
- `post_activity(token, text, symbol)` — convenience wrapper for activity events with throttling
- `logger` — standard Python logging with `[ORBRunner]` prefix for local debugging

Events emitted: `startup:ready`, `cycle:observed`, `portfolio:measured`, `discovery:complete`, `scan:started`, `scan:complete`, `entry:complete`, `exit:complete`, `goal:halted`, `cycle:error`, `cycle_recap`.

## Backtest vs Live

| Aspect | Backtester | Live Runner |
|---|---|---|
| File | `orb_options_bs_backtester.py` | `orb_runner.py` |
| Pricing | Black-Scholes (constant IV) | Real Alpaca option prices |
| Symbols | Fixed (NVDA, TSLA, AAPL, COIN) | Dynamic discovery (default) |
| Execution | Simulated (10 bps slippage) | Real Alpaca market orders |
| Exits | Underlying stop/target/EOD | Same (checks underlying price) |
| Config | CLI flags | `ORB_CONFIG` dict |

The backtest validated the edge (+147% return, 1.259 profit factor, 45% win rate across 4.5 months). The live runner is the forward paper test to validate real fill behavior and slippage.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APCA_API_KEY_ID` | Yes | Alpaca API key |
| `APCA_API_SECRET_KEY` | Yes | Alpaca API secret |
| `ORB_RUNNER_PASSWORD` | No | Platform login (defaults to "orbrunner") |
| `ORB_RUNNER_INITIAL_CASH` | No | Initial paper balance (defaults to $10,000) |

## Common Tasks

**"Start paper trading":** `POST /api/arena/orb-runner/start` or click the ORBRunner card on the Arena Agents page.

**"Change traded symbols":** Set `discovery_mode: "fixed"` and edit `DEFAULT_SYMBOLS`, or keep `"dynamic"` and adjust `discovery_max_symbols` / `discovery_min_change_pct` / `SCANNER_UNIVERSE`.

**"Change stop/target":** Edit `ORB_CONFIG["stop_pct"]` and `ORB_CONFIG["target_pct"]`. These are on the underlying price, not the option price.

**"Change strike selection":** Edit `ORB_CONFIG["strike_offset"]` (currently +1 = OTM by 1 strike step). Add new symbols to `STRIKE_STEPS` dict.

**"Run the backtest":** `cd agents && python3 ../research/strategy_search/orb_options_bs_backtester.py --symbols NVDA,TSLA,AAPL,COIN --start 2026-04-01 --end 2026-08-16 --strike-offset 1 --no-iv-fetch`

**"Run validation suite":** `python3 ../research/strategy_search/orb_options_validation.py --test all`

## Gotchas

- ORBRunner does NOT share code with ScalpRunner. Changes to `scalp_scan_core.py` do not affect it.
- Config is in `ORB_CONFIG` at the top of `orb_runner.py`, not in `strategy_registry.py`. No 3-layer resolution, no admin UI schema.
- Option positions live on Alpaca's side, not in the platform's `positions` table. The platform DB doesn't know about ORBRunner's trades — only the personality-log events and `orb_runner_state.json` track them.
- Schwab OAuth is currently blocked (Akamai 403). Discovery falls through to Alpaca snapshots automatically.
- The runner fetches 1m equity bars via `arena_market_data` (the Arena router), not directly from Alpaca. Option bars are fetched via `alpaca_options_provider`.
- `discovery_mode` is `"dynamic"` by default. The backtest used a fixed 4-symbol universe, so live results may differ from backtest if different symbols are discovered.
- Not in StockBoy's `CONTROLLED_RUNNERS` — no supervisor monitoring, no position-level risk management.
