---
name: trading-systems
description: How the backtester, realtime scanner, forward walker, and strategy lab fit together — entry points, flows, and where to look
triggers:
  - user
  - model
allowed-tools:
  - read
  - grep
  - glob
---

# AI-Trader Trading Systems — Operational Context

Four systems form the research-to-live pipeline. Here's how they fit together and where to jump in.

## The Pipeline (top to bottom)

```
Strategy Lab  →  Forward Walker  →  Backtester  →  Promotion  →  Live Agent + Scanner
(define params)  (validate OOS)     (replay history) (gates)       (trade for real)
```

Strategy Lab defines candidate parameter sets. Forward Walker validates them out-of-sample across rolling time windows using the Backtester. If a candidate passes promotion gates, it gets promoted to the live agent config (with explicit human confirmation). The live agent then uses the Realtime Scanner to discover and trade stocks every cycle.

The shared brain is `agents/scalp_scan_core.py` — pure strategy logic that both the live scanner and backtester call into. This is what keeps backtests honest: they run the same code the live agent runs. `strategy_registry.deep_merge()` layers candidate overrides on top of `SCALP_DEFAULT_PARAMS` everywhere. `execution_simulator.FillConfig` is the shared fill model (slippage, fees, partial fills, tick rounding).

---

## Strategy Lab — Define & Run Experiments

**What it is:** Candidate param definitions + experiment runners. Not a UI — it's JSON configs and Python scripts.

**Entry points:**
- `research/strategy_search/candidate_*.json` — individual candidate param overrides. Load with `load_candidate(config_id)`.
- `agents/scalp_experiments.py` — experiment matrix with named profiles (`baseline`, `strict`, `short_only`, `favorable_rr`, `asymmetric`, etc.). Run standalone.
- `research/strategy_search/state.json` — resumable experiment progress. Check this before starting fresh.
- `agents/strategy_lab.py` — shared helpers: `deep_merge()`, `load_json_config()`, `require_range()`.
- `agents/strategy_registry.py` — canonical `deep_merge()`, `effective_params()`, `position_notional()`.

**To add a new candidate:** Create `research/strategy_search/candidate_<name>.json` with param overrides. They get merged over `SCALP_DEFAULT_PARAMS` from `scalp_scan_core.py`.

**Known results:** `cap2_spy10` is the current winner (short-only 30m, +1.22% return, 1.96 profit factor — edge from pre-move cap + SPY EMA-10 regime filter). VolFence is rejected (-0.15%). Primary loss factor across all strategies: late entry (72% of move already happened before fill).

---

## Forward Walker — Out-of-Sample Validation

**What it is:** Splits history into sequential train/test windows, runs backtests on each test window, scores candidates by out-of-sample performance. No look-ahead bias.

**Entry points:**
- `agents/walk_forward.py` — generic engine. `run_walk_forward(symbols, base_params, candidates, start, end, ...)`. Uses `CryptoScanBacktester`. Returns `dict[candidate_id → WalkForwardSummary]`.
- `research/strategy_search/walk_forward_harness.py` — **the one to use for ScalpRunner**. Exposes pre-move cap and SPY regime filters that the generic engine doesn't. Run: `python3 research/strategy_search/walk_forward_harness.py --candidate cap2_spy10`. Modes: `--candidate`, `--ablation`, `--sweep`, `--sensitivity`.
- `research/strategy_search/discovery_walk_forward.py` — dynamic symbol selection per window (ranks 29-symbol universe by gap/volume/proximity using daily bars). This is the bridge between static backtest symbols and live scanner discovery. Run: `python3 research/strategy_search/discovery_walk_forward.py --candidate cap2_spy10`.
- `service/server/routes_backtest.py` — API: `POST /api/backtest/walk-forward` with `WalkForwardRequest`.

**Default gates** (`min_windows_passed: 0.6`, `min_return_pct: 0.0`, `max_drawdown_pct: 25.0`, `min_sharpe: -0.5`, `min_trades: 5`). A candidate must pass 60% of windows to be promotion-eligible.

**Output:** `WalkForwardSummary` per candidate — windows run/passed, avg return, avg Sharpe, max drawdown, win rate, overall pass/fail.

---

## Backtester — Historical Replay

**What it is:** Replays OHLCV bars through real strategy logic with realistic fills. The simulation engine everything else builds on.

**Entry points:**
- `agents/scalp_scan_backtester.py` — ScalpRunner replay. `ScalpScanBacktester(symbols, params, start_date, end_date, ...).run()` → `BacktestReport`. Models stop-limit pre-positioning, ATR exits, trailing stops. Supports 1m/5m/15m/30m intervals.
- `agents/backtester.py` — generic `BacktestAgent` mock for any agent's `analyze()` method.
- `agents/crypto_scan_backtester.py` — crypto variant (used by generic walk-forward).
- `agents/execution_simulator.py` — `FillConfig` + `simulate_entry()` / `simulate_exit()`. Slippage in bps, fees, size impact, vol widening, partial fills, tick rounding.
- `service/server/routes_backtest.py` — API: `POST /api/backtest/run` with `BacktestRequest`.

**To run a backtest:** Either call `ScalpScanBacktester(...).run()` directly in a script, or POST to `/api/backtest/run`. You need: symbols, params (or params_override), date range, interval, capital, slippage_bps.

**Output:** `BacktestReport` — total return, Sharpe, max drawdown, win rate, profit factor, per-trade `TradeRecord`s.

**The gap:** Backtests use fixed symbol lists (usually `["NVDA", "TSLA", "AAPL", "AMD", "META"]`). The live scanner discovers dynamically. `discovery_walk_forward.py` is the only attempt to bridge this, and it's research-only.

---

## Realtime Scanner — Live Discovery

**What it is:** The live trading stock selection pipeline. Runs every cycle inside `scalp_runner.py`.

**Entry points:**
- `agents/workspaces/scalprunner/scan.py` — `run_scan()` is the entry point. Handles all network I/O (Schwab, Alpaca, platform news).
- `agents/scalp_scan_core.py` — pure logic (no I/O). Shared with backtester.
- `agents/scalp_runner.py` — the live agent. Calls `run_scan()` each cycle, creates pending orders from setups, manages positions.

**The 4 steps:** Discover (Schwab movers + news tickers + volume scanner over 32-symbol universe) → Filter (liquidity: quotes, L2 depth) → Analyze (multi-TF patterns, Fib, S/R) → Pre-position (pending stop-limit orders).

**Config:** All under `params.discovery.*` in `scalp_scan_core.py` defaults. Universe and fallback shortlist defined in `scan.py` (`_SCANNER_UNIVERSE`, `_FALLBACK_SHORTLIST`), overridable via params.

**This system is NOT used by the backtester.** It's live-only. The backtester trades fixed symbol lists. This is the biggest fidelity gap — real traders scan 32+ symbols; backtests trade the same 5.

---

## Promotion — Test to Live

**Entry point:** `agents/promotion.py`

`evaluate_promotion(summaries, gates)` → `dict[candidate_id → PromotionEvaluation]`. `select_best_candidate()` picks the winner by avg_score. `promotion_preview()` generates a JSON summary for human review.

**Promotion is NEVER automatic.** It requires explicit user confirmation, then patches the live agent DB via the existing PATCH endpoint. `rollback_params()` can restore from local JSON backup (`config_backup.py`).

---

## Common Tasks

**"Validate a new strategy idea":** Create `candidate_<name>.json` → run `walk_forward_harness.py --candidate <name>` → check if it passes gates → if yes, `promotion.py` can evaluate for promotion.

**"Run a quick backtest":** `ScalpScanBacktester(symbols, params, start, end, ...).run()` or POST `/api/backtest/run`.

**"Change what the live scanner discovers":** Edit `params.discovery.*` in the agent's strategy params, or modify `_SCANNER_UNIVERSE` / `_FALLBACK_SHORTLIST` in `agents/workspaces/scalprunner/scan.py`.

**"Change strategy logic":** Edit `agents/scalp_scan_core.py` — this is the shared brain. Changes here affect both live trading and backtests.

**"Change fill realism":** Edit `agents/execution_simulator.py` or pass different `FillConfig` params.

**"Check experiment history":** Read `research/strategy_search/state.json` and any result JSONs in that directory.

---

## Gotchas

- Backtests use fixed symbols; live scanner is dynamic. Don't assume backtest results transfer to live discovery without validation.
- `walk_forward.py` (generic) uses `CryptoScanBacktester`. For ScalpRunner, use `walk_forward_harness.py` instead — it has the pre-move cap and SPY regime filters.
- `scalp_scan_core.py` is shared between live and backtest. Changes affect both. Test accordingly.
- Promotion always requires human confirmation. Never auto-promote.
- Default gates are lenient (`min_return_pct: 0.0`). Tighten them if you want stricter validation.
- Late entry is the dominant loss factor (72% of move done before fill). Bar-based fills are inherently late — this is a structural limitation, not a bug.
