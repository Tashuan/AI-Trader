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

The current canonical configuration is `2.1-corrected-paper` from `ORB_CONFIG` in `agents/orb_runner.py`. It is positive in corrected historical tests but remains paper/shadow-only because full-period drawdown has not passed the live-capital gate.

1. **09:30–09:35 ET** — Build an exclusive 5-minute opening range from 1m equity bars
2. **After the range** — Skip the first post-range bar, then require two consecutive breakout closes
3. **09:35–10:00 ET** — Enter only on a confirmed close above the range high or below the range low
4. **On breakout** — Buy symmetric OTM+1 call/put via Alpaca options API, nearest DTE 2–14
5. **Exit** — Underlying hits 1.0% stop or 2.0% target; stops remain inactive for 10 elapsed minutes
6. **15:55 ET** — Force-close all remaining positions

Risk controls: 4 concurrent positions max, 3% equity per trade, $0.20 minimum option premium, 3-loss circuit breaker per symbol, 10% daily-loss pause, 30% rolling-drawdown pause, paper-only gate, and goal check before trading.

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

All strategy config lives in `ORB_CONFIG` at the top of `agents/orb_runner.py`. It does **not** use `strategy_registry.py` or the 3-layer parameter resolution model.

The platform `fetch_config(token)` endpoint is consulted each loop, but only `poll_interval` is honored. Any legacy `watchlist` is explicitly ignored so database state cannot replace the validated strategy universe or alter the experiment.

| Key | Corrected value | Description |
|---|---:|---|
| `config_version` | `2.1-corrected-paper` | Canonical paper configuration |
| `range_minutes` | 5 | Exclusive opening range window |
| `range_end_policy` | `exclusive` | 09:30–09:34 are range bars; 09:35 is eligible |
| `confirmation_bars` | 2 | Consecutive closes required |
| `skip_first_post_range_bar` | `true` | Ignore first eligible post-range bar |
| `stop_pct` / `target_pct` | 1.0 / 2.0 | Underlying stop and target (%) |
| `latest_entry` | `10:00` | No new entries after this ET time |
| `max_positions` | 4 | Max concurrent option positions |
| `position_pct` | 3.0 | Baseline allocation per trade |
| `dynamic_sizing` | `true` | Shadow candidate uses cumulative allocation |
| `max_position_pct` | 6.0 | Dynamic per-trade cap |
| `max_total_pct` | 12.0 | Dynamic cumulative daily cap |
| `strategy_mode` | `symmetric_otm` | Calls higher strike; puts lower strike |
| `strike_offset` | 1 | OTM strike distance |
| `dte_min` / `dte_max` | 2 / 14 | Days to expiration range |
| `min_option_entry_price` | 0.20 | Minimum option premium per share |
| `confirmation_minutes` | 10 | Elapsed minutes before stops activate |
| `circuit_breaker` | 3 | Consecutive losses before halting a symbol |
| `intrabar_policy` | `conservative` | Stop-first if stop and target share a bar |
| `discovery_mode` | `dynamic` | Premarket movers for shadow validation; fixed remains the backtest universe |
| `shadow_mode` | `true` | Log signals; do not place orders |
| `paper_only` | `true` | Reject non-paper execution |
| `daily_loss_limit_pct` | 10.0 | Daily loss pause |
| `max_drawdown_limit_pct` | 30.0 | Rolling drawdown pause |

Research fill assumptions are 100 bps full spread, 50 bps adverse slippage, and $0.65 per-contract fee. Live Alpaca paper execution uses actual quotes and fills.

Strike steps per symbol are in `STRIKE_STEPS` dict (e.g. NVDA $2.50, AAPL $2.50, AMD $0.50).

## Symbol Discovery

The current runner uses `discovery_mode: "dynamic"` for forward shadow validation. The historical backtest used the fixed universe:

```text
NVDA, TSLA, AAPL, COIN
```

Dynamic discovery is intentionally a separate live-vs-backtest experiment. `discover_movers(config)` runs once per day and caches results in `state["discovered_symbols"][date]`:

1. Schwab movers (primary)
2. Alpaca snapshots (fallback)
3. Fixed default symbols (final fallback)

Discovery is filtered by `discovery_min_change_pct`, capped at `discovery_max_symbols`, and flagged if it runs after 09:30 ET because that can introduce lookahead.

## State Persistence

`agents/orb_runner_state.json` — auto-created on first run, holds:
- `consecutive_losses` — global circuit breaker counter
- `day_loss_streaks` — per-symbol loss counter for the day
- `signals_posted` — symbol → last signal date (prevents duplicate signals)
- `open_positions` — symbol → position metadata (occ_symbol, qty, entry, stop, target, option_type)
- `discovered_symbols` — date → list of movers selected that day
- `last_force_exit_date` — tracks EOD close completion
- `shadow_signals` — signals logged without orders while shadow mode is enabled; each signal includes sizing metadata
- `sizing_state` — per-day cumulative reserved allocation for dynamic sizing
- `risk_state` — daily baseline, peak equity, drawdown, and halt reasons
- `config_version` — active runner configuration version

## Shadow Sizing Validation

Dynamic shadow sizing reserves up to 6% of day-start equity per signal and 12% cumulatively per day. Reserved allocation is not released when a hypothetical position would exit. Once exhausted, later signals remain visible with `allocated_budget: 0` and no order is placed.

Each shadow signal records `sizing.mode`, `base_position_pct`, `max_position_pct`, `max_total_pct`, `day_start_equity`, `day_deployed_before`, `remaining_budget_before`, `allocated_budget`, `allocation_pct`, `budget_available`, and `trade_number`. This is an allocation model only; shadow mode does not resolve an option contract or submit an order.

Initial corrected backtest: dynamic 6%/12% returned +75.92% versus fixed 3% +34.20% at 50% IV, with 15.11% versus 27.28% max drawdown. IV, chronological holdout, inverted-bear, and cap sensitivity checks were positive. These are theoretical results; collect 20 clean dynamic-discovery shadow sessions before any paper promotion.

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
| Symbols | Fixed (NVDA, TSLA, AAPL, COIN) | Fixed validated universe by default |
| Execution | BS + 100 bps spread + 50 bps slippage + $0.65 fee | Alpaca paper orders; actual fills |
| Signals | Exclusive range, skip first bar, two-bar confirmation | Same canonical `orb_strategy.py` path |
| Exits | 1.0% stop / 2.0% target, conservative intrabar | Same underlying stop/target/EOD behavior |
| Config | CLI/config dict | `ORB_CONFIG` dict; only poll interval from platform |

The corrected backtest is positive across IV assumptions and chronological holdout data, but full-period drawdown is not yet approved for live capital. The runner is currently shadow-only and must collect clean forward sessions before limited paper execution.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APCA_API_KEY_ID` | Yes | Alpaca API key |
| `APCA_API_SECRET_KEY` | Yes | Alpaca API secret |
| `ORB_RUNNER_PASSWORD` | No | Platform login (defaults to "orbrunner") |
| `ORB_RUNNER_INITIAL_CASH` | No | Initial paper balance (defaults to $10,000) |

## Common Tasks

**"Start shadow mode":** `POST /api/arena/orb-runner/start` or click the ORBRunner card on the Arena Agents page. With the current `shadow_mode: true`, the runner logs signals and places no orders.

**"Enable limited paper execution":** Only after the shadow-session gate is approved, set `ORB_CONFIG["shadow_mode"]` to `false`; keep `paper_only: true`. This is an explicit source-controlled change, not a database toggle.

**"Change traded symbols":** Set `discovery_mode: "fixed"` and edit `DEFAULT_SYMBOLS`, or keep `"dynamic"` and adjust `discovery_max_symbols` / `discovery_min_change_pct` / `SCANNER_UNIVERSE`.

**"Change stop/target":** Edit `ORB_CONFIG["stop_pct"]` and `ORB_CONFIG["target_pct"]`. Current corrected values are 1.0% / 2.0%, measured on the underlying rather than the option price.

**"Change strategy universe":** Keep `discovery_mode: "fixed"` and edit `DEFAULT_SYMBOLS` only when intentionally creating a new research candidate. Platform/API watchlists are ignored by the canonical runner.

**"Change strike selection":** Keep `strategy_mode: "symmetric_otm"` and edit `strike_offset` only with a new validation. Calls select the next higher strike; puts select the next lower strike.

**"Run the corrected backtest":** Use the canonical document and pass the corrected parameters explicitly, including `--strategy-mode symmetric_otm`, `--intrabar-policy conservative`, 2-bar confirmation, 1.0% / 2.0% stop-target, 3% position sizing, a $0.20 minimum premium, 100 bps spread, 50 bps slippage, and $0.65 contract fees. See `research/strategy_search/STRATEGY_ORB_OPTIONS_WINNER.md` for the reproducible configuration.

**"Run validation suite":** `python3 ../research/strategy_search/orb_options_validation.py --test all`

## Gotchas

- ORBRunner does NOT share code with ScalpRunner. Changes to `scalp_scan_core.py` do not affect it.
- Config is in `ORB_CONFIG` at the top of `orb_runner.py`, not in `strategy_registry.py`. No 3-layer resolution, no admin UI schema.
- Option positions live on Alpaca's side, not in the platform's `positions` table. The platform DB doesn't know about ORBRunner's trades — only the personality-log events and `orb_runner_state.json` track them.
- Schwab OAuth is currently blocked (Akamai 403). Discovery falls through to Alpaca snapshots automatically.
- The runner fetches 1m equity bars via `arena_market_data` (the Arena router), not directly from Alpaca. Option bars are fetched via `alpaca_options_provider`.
- `discovery_mode` is `"dynamic"` for the current forward shadow experiment. The backtest used the fixed four-symbol universe, so discovery fidelity is still under validation.
- `dynamic_sizing` is enabled for shadow metadata at 6% per trade / 12% cumulative daily allocation; it does not authorize orders by itself.
- `shadow_mode` is `true` by default, so starting the runner logs would-have-traded signals and does not place paper orders.
- Strategy parameters and symbols are source-controlled in `ORB_CONFIG`/`DEFAULT_SYMBOLS`; the platform config endpoint only supplies `poll_interval`.
- Not in StockBoy's `CONTROLLED_RUNNERS` — no supervisor monitoring, no position-level risk management.
