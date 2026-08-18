# ScalpRunner Strategy Research

This directory is the persistent workspace for the Devin `strategy-researcher` agent and the `/strategy-research` skill.

## Scope

ScalpRunner is the primary research target. The default search surface is the Strategy Lab and its realistic execution stack:

- `agents/scalp_experiments.py` — scenario matrix, holdout, and walk-forward workflows
- `agents/scalp_scan_backtester.py` — scanner-integrated backtests with discovery_fn + catalyst_fn callbacks
- `agents/scalp_scan_core.py` — deterministic signal and parameter behavior, tape reading, adaptive exit
- `agents/backtest_discovery.py` — shared symbol discovery module (daily-bar + intraday scanner, 42-symbol universe)
- `agents/catalyst_tagger.py` — news headline catalyst classification (8 categories, bullish/bearish bias)
- `agents/strategy_lab.py` — deep parameter merge/config helpers
- `agents/execution_simulator.py` — fees, slippage, spread/quote-side pricing, partial fills, volatility widening, and tick rounding
- `agents/backtest_liquidity.py` — conservative liquidity assumptions
- `agents/sweep_params_crypto_scalp.py` and related experiment tools where applicable
- `research/strategy_search/walk_forward_harness.py` — walk-forward validation with --discovery and --max-symbols CLI flags
- `research/strategy_search/fence_walk_forward.py` — fence walk-forward using shared discovery module

Other strategy families are comparison or fallback work only unless the journal explicitly promotes them to the active question.

## Validated Strategies

- **VolFence** — Volatility-filtered opening-range breakout on 5m bars. Winning config: 2.0R target, ATR > 1.2%, ETF exclusion. +1.18% at 5bps over 22 months. See `STRATEGY_VolFence.md`.
- **ORB Options** — Corrected paper strategy using canonical 5m range / 2-bar confirmation, 1.0% stop, 2.0% target, 3% position sizing, minimum $0.20 option premium, and conservative spread/slippage/fee assumptions. Positive full-period and chronological holdout results, but full-period drawdown is too high for live promotion. See `STRATEGY_ORB_OPTIONS_WINNER.md` and `docs/ORB_OPTIONS_STRATEGY.md`.
- **ORB equity archive** — Historical 1m equity ORB research and the superseded optimistic options pass. See `STRATEGY_ORB.md`.

## Futures ORB — research preparation

The futures ORB adaptation is currently a research-only backtesting track. The foundation lives in `orb_futures_backtester.py` and `orb_futures_validation.py`, reusing the canonical ORB signal engine from `agents/orb_strategy.py`. It defaults to MES/MNQ/M2K/MYM RTH data, tick-aware costs, micro-contract metadata, dollar-risk sizing, and the corrected exclusive-range/two-confirmation/skip-first-bar signal rules.

The initial smoke replay was positive but used only approximately 60 days of yfinance 5m data. Massive REST access is now working and has been integrated with quarterly-contract stitching and parquet caching. The first two-year MES validation was negative, so the track remains `promising_not_validated` with no approved candidate. No FuturesRunner exists and no futures execution has been enabled. Full status, results, assumptions, and promotion gates are documented in [`../../docs/FUTURES_ORB_STRATEGY.md`](../../docs/FUTURES_ORB_STRATEGY.md).

## Artifact contract

- `state.json` is the resumable machine-readable checkpoint.
- `journal.md` contains hypotheses, exact commands, assumptions, metrics, decisions, and lessons.
- `candidate_<id>.json` stores reproducible nested parameter overrides.
- `run_<id>_<timestamp>.json` stores complete reports or summarized batch results.
- `holdout_<id>.json` stores untouched final validation and gate results.
- Research-only harnesses may be created here when an existing CLI cannot express a needed scenario.

Do not edit canonical files under `agents/`, `service/`, or live agent configuration to tune a candidate. Do not store secrets or credentials here.

## Required evidence

A candidate is not a winner from in-sample return alone. The agent must preserve chronological train/validation/holdout separation and record:

- symbols, dates, interval, provider/cache mode, capital, fees, slippage, and liquidity/fill assumptions
- total return, Sharpe, maximum drawdown, win rate, profit factor, trade count, average hold
- per-symbol results, exit attribution, sample/data warnings, and sensitivity results
- walk-forward window pass rate and final untouched holdout result

Results that depend on zero spread, unrealistic fills, one symbol, one regime, or a short sample are labeled rejected or promising—not proven.
