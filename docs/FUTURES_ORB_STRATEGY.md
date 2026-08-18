# Futures ORB Research Status

> Status: research preparation only. No FuturesRunner has been built, registered, or connected to a broker.
>
> Last updated: 2026-08-17

## Purpose

This document records the current plan and implementation state for adapting the corrected ORB strategy to futures. The objective is to find a futures configuration that survives realistic costs, sizing, drawdown, and out-of-sample validation before any live or paper runner is created.

The options ORB is the baseline for **signal structure**, not for contract mechanics. Futures require their own tick, multiplier, commission, slippage, margin, and dollar-risk model.

## Research boundary

The first research candidate is US RTH only:

```text
Market open:       09:30 ET
Opening range:     09:30–09:34 ET (exclusive five-minute range)
First eligible bar:09:35 ET
First bar:         skipped
Confirmation:      two consecutive closes outside the range
Latest entry:      10:00 ET
Intrabar policy:   conservative stop-first
Force exit:        15:55 ET
```

Overnight, London, Globex, and overnight-range-breakout variants are separate future candidates. They must not be blended into the first RTH result.

## Current implementation

### Futures backtester

**File:** `research/strategy_search/orb_futures_backtester.py`

The backtester now:

- Uses `OpeningRangeBuilder` and `BreakoutChecker` from `agents/orb_strategy.py`.
- Shares the corrected ORB signal behavior rather than maintaining a separate breakout implementation.
- Defaults to micro futures suitable for initial $10,000-account research:
  - `MES=F` — Micro E-mini S&P 500
  - `MNQ=F` — Micro E-mini Nasdaq 100
  - `M2K=F` — Micro E-mini Russell 2000
  - `MYM=F` — Micro E-mini Dow
- Defines tick size, tick value, multiplier, and contract metadata.
- Fetches yfinance 5-minute bars and filters them to 09:30–15:55 ET RTH.
- Calculates stops from opening-range width by default.
- Calculates targets as an R multiple.
- Sizes positions from dollar risk per trade instead of fixed contract count.
- Includes commission and tick-based adverse slippage.
- Applies conservative stop-first handling when a bar touches both stop and target.
- Tracks signal count, extension-filter rejections, and sizing rejections.
- Reports per-symbol performance and exit reasons.
- Force-closes remaining positions at the configured end-of-day time.

### Regression tests

**File:** `agents/tests/test_orb_futures_backtester.py`

Coverage currently verifies:

- The futures adapter uses the canonical two-confirmed-close behavior after skipping the first post-range bar.
- Micro-contract tick values equal `tick_size × multiplier`.

## Baseline configuration

The current research baseline is `futures-orb-baseline-v0.1`:

```python
{
    "range_minutes": 5,
    "range_end_policy": "exclusive",
    "confirmation_bars": 2,
    "skip_first_post_range_bar": True,
    "latest_entry": "10:00",
    "min_entry_time": "09:30",
    "max_positions": 1,
    "risk_per_trade_pct": 0.5,
    "max_contracts": 4,
    "stop_model": "range_width",
    "stop_range_multiplier": 1.0,
    "target_r_multiple": 2.0,
    "confirmation_minutes": 10,
    "circuit_breaker": 3,
    "extension_filter_pct": 0.0,
    "force_exit_time": "15:55",
    "slippage_ticks": 1,
    "commission_per_side": 2.50,
}
```

The command-line interface supports controlled experiments with risk percentage, range-stop multiplier, R target, maximum contracts, and the extension filter.

Example:

```bash
source .venv/bin/activate
python3 research/strategy_search/orb_futures_backtester.py \
  --symbols MES=F,MNQ=F,M2K=F,MYM=F \
  --period 60d \
  --risk-pct 1.0 \
  --stop-range-multiplier 0.5 \
  --target-r 2.0
```

## Initial smoke results

These results are exploratory only. They use approximately 60 days of yfinance 5-minute data and are not a promotion or paper-trading approval.

Configuration:

```text
Universe:             MES, MNQ, M2K, MYM
Risk:                 1.0% per trade
Stop:                 0.5 × opening-range width
Target:               2R
Maximum positions:    1
Slippage:             1 tick per fill
Commission:           $2.50 per contract per side
```

Results:

| Metric | Result |
|---|---:|
| Total return | +3.02% |
| Net P&L | +$301.50 |
| Maximum drawdown | 9.16% |
| Profit factor | 1.230 |
| Win rate | 40.6% |
| Trades | 64 |

Per-symbol P&L:

| Symbol | Trades | P&L | Win rate |
|---|---:|---:|---:|
| MES=F | 28 | +$137.50 | 39% |
| MNQ=F | 6 | $0.00 | 33% |
| M2K=F | 14 | +$142.00 | 43% |
| MYM=F | 16 | +$297.00 | 44% |

Controlled comparison:

| Candidate | Return | Max DD | PF | Trades |
|---|---:|---:|---:|---:|
| 0.5R range stop / 2R target | +3.02% | 9.16% | 1.230 | 64 |
| 0.5R range stop / 2R target + 0.05% extension filter | +1.22% | 3.57% | 1.268 | 20 |
| 1.0R range stop / 2R target | -14.23% | 15.87% | 0.431 | 45 |
| 0.5R range stop / 1.5R target | +1.98% | 7.90% | 1.214 | 67 |

Interpretation: the short sample is useful for verifying the engine, but not for declaring a strategy winner. The 0.05% extension candidate lowers drawdown and improves PF while drastically reducing trade count. It remains a research hypothesis.

## Data limitations

The current data path is yfinance 5-minute futures history. The available intraday sample is approximately 60–70 days, depending on the request and symbol. This is insufficient for the intended multi-year walk-forward validation.

Before promotion, the research process still needs:

- A longer historical dataset or durable collection process.
- Documented continuous-contract and roll methodology.
- Contract-specific historical metadata.
- Missing-bar, duplicate-bar, timezone, holiday, and session-quality checks.
- Validation that RTH bars and overnight bars can be separated deterministically.

The current backtester is suitable for a smoke test and controlled implementation work, not final strategy selection.

## Validation gates before a runner

A candidate must be tested by instrument and not only as a combined portfolio. Required checks:

1. At least 12 months of reliable intraday data; longer is preferred.
2. At least three chronological walk-forward test windows.
3. Positive results in at least 60% of out-of-sample windows.
4. Positive expectancy after commissions and realistic slippage.
5. Cost sensitivity at 1.5x and 2.0x the base slippage.
6. Entry-latency sensitivity.
7. Conservative stop-first intrabar handling.
8. Best-day, best-trade, and best-instrument removal tests.
9. No single instrument providing an unacceptable share of total profit.
10. Maximum drawdown within the approved risk gate.
11. Results not dependent on fixed E-mini contracts or oversized leverage.
12. Per-symbol, long/short, month, regime, and exit-reason reporting.

A short-sample positive return, a single-instrument result, or a high-return/high-drawdown configuration is labeled `promising_not_validated`, not a winner.

## Next research sequence

```text
1. Acquire/cache longer futures history.
2. Add data-quality and contract-roll validation.
3. Expand the RTH experiment matrix.
4. Compare range-width, ATR, and hybrid stop models.
5. Test 1R, 1.5R, 2R, and 2.5R exits.
6. Test confirmation, cutoff, first-bar, and extension hypotheses.
7. Run cost, latency, and outlier sensitivity.
8. Add chronological walk-forward validation.
9. Freeze a candidate only if it passes the promotion gates.
10. Record live shadow signals before any paper execution.
11. Build FuturesRunner only after the research candidate is approved.
```

## Explicit no-runner boundary

As of this document's update:

- No `FuturesRunner` exists.
- No futures broker orders are placed.
- No futures runner is registered in Arena.
- No futures strategy has been promoted to paper execution.
- No overnight or London session strategy has been approved.
- The current futures work is research/backtesting preparation only.

## Verification performed

```text
python3 -m pytest -q
255 passed, 89 skipped

python3 -m pytest agents/tests/test_orb_futures_backtester.py agents/tests/test_orb_strategy.py -q
45 passed
```

## Related files

- `agents/orb_strategy.py` — canonical ORB signal engine.
- `research/strategy_search/orb_futures_backtester.py` — futures research backtester.
- `agents/tests/test_orb_futures_backtester.py` — futures backtester regression tests.
- `agents/orb_runner.py` — corrected options ORB reference; unchanged by futures preparation.
- `docs/ORB_OPTIONS_STRATEGY.md` — options ORB strategy documentation.
- `docs/BACKTESTING.md` — backtesting architecture and entry points.
- `research/strategy_search/README.md` — research artifact and evidence contract.
