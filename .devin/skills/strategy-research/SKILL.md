---
name: strategy-research
description: Run or resume the autonomous ScalpRunner Strategy Lab research loop
argument-hint: "[research objective or constraint]"
agent: strategy-researcher
triggers:
  - user
---

Run the autonomous strategy-research protocol for this repository, with **ScalpRunner as the primary and default target**. Treat the existing Strategy Lab scenario work in `agents/scalp_experiments.py`, `agents/scalp_scan_backtester.py`, `agents/strategy_lab.py`, `agents/execution_simulator.py`, and the related sweep/holdout tooling as the main research surface.

If the user supplies an objective or constraint, incorporate it into the experiment contract. Otherwise resume from `research/strategy_search/state.json` and the journal. Do not restart from scratch when prior state exists.

Complete a bounded but meaningful batch, checkpoint all findings in `research/strategy_search/`, and continue into the next batch when the environment supports it. The search is not complete until a candidate is either independently validated on untouched chronological holdout data or the evidence shows that the current search space has no robust edge. Return a clear status of `winner`, `promising`, `inconclusive`, or `rejected`.

Use realistic ScalpRunner execution assumptions by default: estimated liquidity/spread, quote-side pricing where supported, fees, slippage, volatility widening, partial fills, and tick rounding. Never use optimistic zero-spread results as the primary conclusion. Never modify live agent configuration or execute any trade; promotion is always a separate, explicit human decision.
