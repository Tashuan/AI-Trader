# Futures ORB Research Status

> Status: research preparation only. No FuturesRunner has been built, registered, or connected to a broker.
>
> Last updated: 2026-08-18

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

- Supports yfinance for smoke tests and Massive REST futures aggregates for longer history.
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
- Includes entry commissions inside each trade's net P&L so total equity, trade records, and per-symbol attribution reconcile.
- Supports a separate 1-minute tick-target scalp mode; this is a new strategy family, not the slower range-width ORB.

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

## Controlled validation update

**Harness:** `research/strategy_search/orb_futures_validation.py`

The first controlled 8-window replay used 10-day windows over the same short yfinance sample. It is a diagnostic walk-forward-style split, not a valid multi-year walk-forward promotion test.

| Candidate | Full return | Profitable windows | Full PF | P&L without best day |
|---|---:|---:|---:|---:|
| baseline | +3.02% | 6/8 | 1.230 | +$4.00 |
| extension_005 | +2.06% | 5/8 | 1.406 | +$64.25 |
| extension_010 | +1.12% | 5/8 | 1.202 | -$135.75 |
| target_150 | +1.98% | 6/8 | 1.214 | -$96.25 |
| stop_100 | -14.23% | 2/8 | 0.431 | -$1,561.00 |
| range_10m | -6.81% | 2/8 | — | -$894.00 |
| range_15m | -5.71% | 2/8 | — | -$706.75 |
| confirm_1 | +3.01% | 5/8 | — | -$96.75 |

The baseline's +$301.50 is almost entirely one best day (+$297.50). The 0.05% extension candidate is less dependent on that day but has only 19 full-period trades. **No candidate is validated or approved.** The current decision is to obtain longer data rather than continue optimizing this short sample.

### Massive REST multi-year result: MES

Massive REST minute aggregates are now accessible with the updated `MASSIVE_API_KEY`. The provider fetched and stitched MES quarterly contracts from 2024-08-19 through 2026-08-17, then filtered and evaluated RTH 5-minute bars across 13 60-day diagnostic windows.

| Candidate | Return | PF | Max DD | Positive windows | Trades |
|---|---:|---:|---:|---:|---:|
| baseline | -22.05% | 0.983 | 33.89% | 5/13 | 338 |
| extension 0.05% | -13.46% | 0.817 | 18.34% | 4/13 | 95 |
| extension 0.10% | -10.86% | 0.997 | 19.68% | 5/13 | 162 |
| target 1.5R | -26.82% | 0.925 | 34.60% | 7/13 | 335 |
| stop 1.0x range | -10.60% | 0.994 | 22.82% | 6/13 | 282 |
| range 10m | -2.14% | 1.097 | 15.82% | 6/13 | 271 |
| range 15m | -6.01% | 1.034 | 24.06% | 7/13 | 201 |
| confirmation 1 bar | -38.02% | 0.890 | 45.21% | 3/13 | 408 |

**Decision:** MES does not currently demonstrate a validated ORB edge over the longer sample. The 10-minute range is the least-bad candidate but remains negative, below the PF and walk-forward gates.

### Massive REST multi-year result: MNQ

The same 2024-08-19 through 2026-08-17 test completed for MNQ. MNQ is materially stronger than MES in this sample, but this is still a single-instrument result and requires the same out-of-sample, cost, roll, and cross-regime scrutiny.

| Candidate | Return | PF | Max DD | Positive windows | Trades | P&L without best day |
|---|---:|---:|---:|---:|---:|---:|
| baseline | +5.97% | 1.112 | 19.28% | 6/13 | 273 | +$388.00 |
| extension 0.05% | +1.13% | 1.138 | 5.97% | 5/13 | 52 | -$56.00 |
| extension 0.10% | -3.21% | 1.001 | 9.08% | 4/13 | 92 | -$489.50 |
| target 1.5R | +6.02% | 1.130 | 12.81% | 6/13 | 269 | +$455.00 |
| stop 1.0x range | +21.44% | 1.617 | 5.43% | 8/13 | 89 | +$1,931.50 |
| range 10m | +9.08% | 1.180 | 7.98% | 8/13 | 161 | +$718.00 |
| range 15m | +8.60% | 1.253 | 6.01% | 7/13 | 93 | +$665.50 |
| confirmation 1 bar | +13.68% | 1.162 | 14.65% | 7/13 | 322 | +$1,161.00 |

**Interpretation:** MNQ is the first genuinely promising product in the longer sample. The strongest candidates are not the original corrected baseline: 1.0x range stop, one-bar confirmation, and 10–15-minute ranges performed better. These are research candidates only; selecting the best from this same sample would introduce optimization bias. MNQ must be tested against a frozen holdout and realistic quote/slippage data before any promotion.

### Frozen MNQ holdout result

The strongest in-sample MNQ candidate was frozen at `1.0x range stop / 2R target / two-bar confirmation` and tested on 2026-01-01 through 2026-08-17 without changing parameters.

| Slippage | Return | PF | Max DD | Trades | P&L without best day |
|---:|---:|---:|---:|---:|---:|
| 1 tick | +0.30% | 1.091 | 2.50% | 7 | -$143.50 |
| 2 ticks | +0.22% | 1.066 | 2.53% | 7 | -$150.50 |
| 3 ticks | -1.57% | 0.545 | 2.56% | 6 | -$321.50 |

The 1-tick result was driven by one +$176.50 day. The frozen candidate therefore **fails the holdout robustness gate** and is not promotion-ready. Do not tune parameters against this holdout; use a new research period or product for subsequent hypotheses.

### Massive REST multi-year result: M2K and MYM

M2K's 0.05% extension-filter candidate was the strongest in-sample product/candidate combination. MYM did not produce a positive candidate.

| Product | Best candidate | Return | PF | Max DD | Positive windows | Trades |
|---|---|---:|---:|---:|---:|---:|
| M2K | 0.05% extension | +6.11% | 1.552 | 3.93% | 8/13 | 31 |
| M2K | 0.10% extension | +5.62% | 1.418 | 5.00% | 8/13 | 71 |
| MYM | 15m range | -0.30% | 1.099 | 12.21% | 5/13 | 166 |

The frozen M2K 0.05% extension candidate was evaluated on 2026-01-01 through 2026-08-17:

| Slippage | Return | PF | Max DD | Trades | P&L without best day |
|---:|---:|---:|---:|---:|---:|
| 1 tick | +2.40% | 1.884 | 2.10% | 8 | +$83.50 |
| 2 ticks | +2.21% | 1.788 | 2.15% | 8 | +$66.50 |
| 3 ticks | +1.32% | 1.551 | 1.74% | 8 | +$27.00 |

**Decision:** M2K is the leading candidate, but the holdout has only eight trades and the candidate was identified after reviewing the full sample. It is `promising_not_validated`, not runner-approved. The next evidence should be a genuinely untouched future paper/shadow period or a separately reserved historical period, not more tuning on these dates.

### M2K separate 2025 period

As an additional date-range check, the frozen 0.05% extension candidate was run over 2025-01-01 through 2025-12-31. After correcting entry-commission accounting, results were:

| Slippage | Return | PF | Max DD | Trades | P&L without best day |
|---:|---:|---:|---:|---:|---:|
| 1 tick | +1.40% | 1.279 | 4.05% | 14 | +$12.00 |
| 2 ticks | +1.31% | 1.269 | 3.89% | 14 | +$5.00 |
| 3 ticks | +1.14% | 1.238 | 3.81% | 14 | -$9.50 |

This is directionally positive but still only 14 trades and nearly all profit comes from the best day. It strengthens the M2K hypothesis slightly but does not meet the evidence threshold for a runner.

## 1-minute futures scalp experiment

The slower ORB was converted into a separate scalp research mode using 1-minute Massive REST bars, a 5-minute opening range, two 1-minute confirmation closes, and fixed tick stops/targets. All four micro index contracts were tested: MES, MNQ, M2K, and MYM.

The tight and balanced profiles were rejected:

| Profile | Stop | Target | 2025 H1 return | 2026 Jan–Feb return |
|---|---:|---:|---:|---:|
| Tight | 4 ticks | 8 ticks | -31.32% | -10.42% |
| Balanced | 8 ticks | 12 ticks | -10.90% | -2.30% |

### MNQ momentum scalp candidate

The high-R:R profile was tested on MNQ alone with a 10-tick stop and 40-tick target. A 10-minute confirmation grace period initially produced very strong results, but that was inherited from the slower ORB and is not valid scalp behavior.

After testing stop activation timing over the full 2024-08-19 through 2026-08-14 sample:

| Stop activation | Return | PF | Max DD | Win rate | Trades |
|---:|---:|---:|---:|---:|---:|
| Immediate | -85.51% | 0.095 | 85.51% | 10.9% | 359 |
| 2 minutes | -24.48% | 0.813 | 30.94% | 46.8% | 474 |
| 5 minutes | +81.52% | 2.074 | 6.34% | 69.2% | 474 |
| 10 minutes | +128.52% | 3.497 | 2.03% | 79.1% | 474 |

The 5-minute version remains positive at 3-tick slippage across all tested periods:

| Period | Return | PF | Max DD | Trades |
|---|---:|---:|---:|---:|
| 2024 H2 | +2.24% | 1.108 | 5.06% | 88 |
| 2025 H1 | +27.12% | 2.738 | 1.40% | 119 |
| 2025 H2 | +20.16% | 2.077 | 2.26% | 117 |
| 2026 Jan–Feb | +8.68% | 2.517 | 1.54% | 41 |
| 2026 Mar–Aug | +18.80% | 2.063 | 2.72% | 110 |

Full-period trade duration for the 5-minute version averaged 2.8 minutes, with a median of 2 minutes and a 90th percentile of 5 minutes. That meets the intended scalp behavior, although the stop grace period is a material strategy parameter.

The same frozen 10/40 profile at 3 ticks over the full available period was strongly product-specific:

| Product | Return | PF | Max DD | Trades | Avg hold |
|---|---:|---:|---:|---:|---:|
| MES | -60.88% | 0.519 | 61.50% | 458 | 7.0m |
| MNQ | +81.52% | 2.074 | 6.34% | 474 | 2.8m |
| M2K | -61.00% | 0.494 | 61.36% | 446 | 5.1m |
| MYM | -54.73% | 0.562 | 55.29% | 442 | 4.4m |

MNQ sensitivity also remained positive across entry cutoffs 09:45, 10:00, and 10:15, and with two or three confirmation bars. This reduces dependence on one exact cutoff, but the sensitivity was still evaluated on the same historical sample.

**Decision:** The MNQ 10/40 profile with a 5-minute stop grace is the leading high-R:R scalp hypothesis. Immediate and 2-minute stop activation fail. The candidate is promising but not runner-approved until the grace-period rule survives quote-aware forward shadow testing.

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

## MNQ shadow monitor

A paperless shadow monitor now exists at `agents/mnq_scalp_shadow.py` and is exposed through the local backend:

```text
POST /api/arena/mnq-scalp-shadow/start
POST /api/arena/mnq-scalp-shadow/stop
GET  /api/arena/mnq-scalp-shadow/status
```

The monitor observes yfinance 1-minute MNQ bars during US RTH and records the frozen candidate:

```text
5-minute range
2 confirmation closes
10-tick stop
40-tick target
5-minute stop grace
3-tick modeled slippage
```

State is stored in `agents/mnq_scalp_shadow_state.json`. The monitor explicitly records `shadow_only`, `paper_orders: false`, `live_orders: false`, and `orders_submitted: false` on hypothetical trades. It has no broker client or order route.

The monitor is intended to collect forward behavior and timing evidence; it does not validate historical bid/ask execution because the current Massive plan lacks futures quote access.

### Shadow monitoring infrastructure

Three background processes support the shadow monitor during live RTH sessions:

```text
1. Backend (uvicorn, PID varies)
   - FastAPI server on :8000
   - Hosts the MNQ shadow monitor thread and ORBRunner thread
   - Entry point: service/server/main.py

2. MNQ shadow watcher (/tmp/mnq_shadow_watcher.sh)
   - Polls the shadow state file every 30 seconds
   - Logs only when the state file changes (range set, confirmation, fills, stops, targets)
   - Log: agents/mnq_shadow_watcher.log
   - All I/O is local (localhost curl + file read); no external network traffic

3. Backend watchdog (/tmp/backend_watchdog.sh)
   - Health-checks GET /health every 60 seconds
   - After 3 consecutive failures, kills the stale process and restarts python main.py
   - After restart, re-arms both runners via API:
     POST /api/arena/orb-runner/start
     POST /api/arena/mnq-scalp-shadow/start
   - Log: agents/backend_watchdog.log
```

Monitoring commands:

```bash
# Live MNQ shadow state changes
tail -f agents/mnq_shadow_watcher.log

# Backend watchdog events
tail -f agents/backend_watchdog.log

# Quick status (both runners)
curl -sS http://127.0.0.1:8000/api/arena/mnq-scalp-shadow/status
curl -sS http://127.0.0.1:8000/api/arena/orb-runner/status

# Full shadow state
cat agents/mnq_scalp_shadow_state.json | python3 -m json.tool
```

The watcher and watchdog are shell scripts launched with `nohup` and are not managed by the backend. They persist across backend restarts (the watchdog triggers those restarts). If the host machine reboots, all three processes must be relaunched manually.

## Explicit no-runner boundary

As of this document's update:

- No `FuturesRunner` exists; only the paperless MNQ shadow monitor is running.
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
- `research/strategy_search/orb_futures_backtester.py` — futures research backtester and provider CLI.
- `research/strategy_search/massive_futures_data.py` — Massive REST quarterly-contract loader, stitching, normalization, rate-limit backoff, and parquet cache.
- `research/strategy_search/orb_futures_validation.py` — controlled candidate and window validation harness.
- `agents/mnq_scalp_shadow.py` — paperless MNQ forward shadow monitor.
- `agents/tests/test_orb_futures_backtester.py` — futures backtester regression tests and shadow safety tests.
- `agents/orb_runner.py` — corrected options ORB reference; unchanged by futures preparation.
- `docs/ORB_OPTIONS_STRATEGY.md` — options ORB strategy documentation.
- `docs/BACKTESTING.md` — backtesting architecture and entry points.
- `research/strategy_search/README.md` — research artifact and evidence contract.
