# ScalpRunner Strategy Research

This directory is the persistent workspace for the Devin `strategy-researcher` agent and the `/strategy-research` skill.

## Scope

ScalpRunner is the primary research target. The default search surface is the Strategy Lab and its realistic execution stack:

- `agents/scalp_experiments.py` — scenario matrix, holdout, and walk-forward workflows
- `agents/scalp_scan_backtester.py` — scanner-integrated backtests
- `agents/scalp_scan_core.py` — deterministic signal and parameter behavior
- `agents/strategy_lab.py` — deep parameter merge/config helpers
- `agents/execution_simulator.py` — fees, slippage, spread/quote-side pricing, partial fills, volatility widening, and tick rounding
- `agents/backtest_liquidity.py` — conservative liquidity assumptions
- `agents/sweep_params_crypto_scalp.py` and related experiment tools where applicable

Other strategy families are comparison or fallback work only unless the journal explicitly promotes them to the active question.

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
