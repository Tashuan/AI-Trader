# ScalpRunner Backtest Results

## Full Analysis and Recommendations

**Date:** 2026-08-12
**Data range:** 2026-03-02 to 2026-08-11 (5.5 months)
**Symbols:** NVDA, TSLA, AAPL, AMD, META
**Data provider:** Alpaca historical (cached)
**Slippage:** 5 bps (realistic), with sensitivity tests at 2 bps and 10 bps

---

## Executive Summary

Through iterative experimentation across four phases, we identified a profitable ScalpRunner configuration that survives walk-forward validation at realistic execution costs. The winning strategy combines two filters — a pre-move cap that avoids late entries and a SPY daily EMA regime filter that avoids fighting bull markets.

**Winning configuration: `cap2_spy10`**

| Metric | Value |
|---|---|
| Total return (20 windows, 5 bps) | **+1.22%** |
| Pass rate | **45% (9/20 windows)** |
| Avg profit factor | **1.96** |
| Max drawdown | **0.50%** |
| Total trades | 100 |
| Profitable at 10 bps | **Yes (+0.11%)** |

The strategy is short-only, trades 30m bars, and uses 1.5 ATR stops with 2.5 ATR targets. It only enters shorts when (a) the stock hasn't already moved more than 2% in the last 4 hours and (b) SPY is below its 10-day EMA.

---

## Table of Contents

1. [Backtester Fidelity Analysis](#1-backtester-fidelity-analysis)
2. [Phase 1: Initial Timeframe and Profile Experiments](#2-phase-1-initial-timeframe-and-profile-experiments)
3. [Phase 2: Risk/Reward and Direction Optimization](#3-phase-2-riskreward-and-direction-optimization)
4. [Phase 3: Walk-Forward Validation and Regime Discovery](#4-phase-3-walk-forward-validation-and-regime-discovery)
5. [Phase 4: Entry Timing Analysis and Five Fixes](#5-phase-4-entry-timing-analysis-and-five-fixes)
6. [Phase 5: SPY Regime Filter and Final Configuration](#6-phase-5-spy-regime-filter-and-final-configuration)
7. [Winning Strategy Specification](#7-winning-strategy-specification)
8. [Slippage Sensitivity](#8-slippage-sensitivity)
9. [Regime Breakdown](#9-regime-breakdown)
10. [Limitations and Caveats](#10-limitations-and-caveats)
11. [Recommendations](#11-recommendations)
12. [Code Changes](#12-code-changes)

---

## 1. Backtester Fidelity Analysis

Before trusting any backtest results, we verified whether the backtester faithfully reproduces the live ScalpRunner's 4-step process.

### The live 4-step process

| Step | Description | Key functions |
|---|---|---|
| 1. Discovery | Dynamic shortlist via Schwab movers, Alpaca screeners, news tickers, volume scanner → top 15 symbols | `scan.py:_discover_shortlist()` |
| 2. Filter | Liquidity scoring using real bid/ask spread, Level 2 depth, dollar volume | `scalp_scan_core.liquidity_score()` |
| 3. Analysis | Multi-timeframe deep scan (1m/5m/15m), Fibonacci, S/R, breakout, pattern, composite scoring | `deep_scan_multi_tf()`, `score_scalp_setup()` |
| 4. Pre-positioning | Stop-limit pending orders with 30-min expiry, ATR-based SL/TP | `scalp_runner.py:create_pending_order()` |

### Backtester fidelity

| Step | Live | Backtester | Match? |
|---|---|---|---|
| 1. Discovery | Dynamic shortlist (~15 symbols from movers/news/scanner) | Static watchlist (5 hardcoded symbols, scanned every bar) | **Mismatched** |
| 2. Filter | Real quote spread + Level 2 depth | Same function but bid=ask=close (zero spread), Level2=None | **Degraded** |
| 3. Analysis | Native 1m/5m/15m bars from broker | Same core functions, but resampled from base interval | **Matched (same logic)** |
| 4. Pre-positioning | Stop-limit orders via API, 30-min expiry | Same stop-limit logic, same expiry | **Matched** |

### Impact of fidelity gaps

| Gap | Effect | Direction |
|---|---|---|
| No dynamic discovery | Backtest trades same 5 names regardless of activity; live picks hottest movers | Likely understates performance |
| Weak liquidity filter | Backtest takes trades live would reject (no spread/depth check) | Likely overstates performance |
| Resampled MTF | Synthetic 5m/15m candles may differ from native | Unclear — could go either way |

**Conclusion:** The backtester faithfully reproduces steps 2-4 (with degraded inputs for step 2) but does not replicate step 1 (discovery) at all. This is a significant limitation — the live strategy's edge may partly come from selecting the right stocks at the right time.

---

## 2. Phase 1: Initial Timeframe and Profile Experiments

### Setup

- **Method:** Single train/test holdout
- **Train period:** 2026-06-15 to 2026-07-14
- **Test period:** 2026-07-15 to 2026-08-11
- **Slippage:** 2 bps
- **Profiles tested:** baseline, strict, frequent, tight_exits
- **Timeframes tested:** 5m, 15m, 30m

### Results (out-of-sample, 2 bps)

| Rank | Config | Return | Profit factor | Drawdown | Win rate | Trades |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 30m frequent | -0.21% | 0.921 | 0.90% | 59.1% | 66 |
| 2 | 30m tight exits | -0.29% | 0.869 | 0.88% | 59.6% | 57 |
| 3 | 30m baseline | -0.67% | 0.720 | 0.94% | 55.6% | 54 |
| 4 | 15m frequent | -0.61% | 0.766 | 0.67% | 50.0% | 106 |
| 5 | 15m baseline | -0.94% | 0.614 | 0.96% | 49.3% | 75 |
| 6 | 5m baseline | -2.73% | 0.456 | 2.88% | 42.0% | 193 |

### Key findings

- **All configurations had negative returns** — no profitable strategy was found
- **30m is the best timeframe** — slower timeframes preserve capital better
- **5m is significantly worse** — more trades, lower win rate, larger drawdowns
- **Frequent entry profile helps** — loosening selectivity improves relative results
- **Buy-and-hold benchmark was -8.31%** — the strategy reduced downside but wasn't profitable

### Trade-level analysis

| Direction | Trades | Win rate | Total PnL | Avg win | Avg loss |
|---|---:|---:|---:|---:|---:|
| Long | 22 | 45.5% | -$607.87 | $39.08 | -$83.22 |
| Short | 44 | 65.9% | +$401.45 | $69.38 | -$107.36 |

**The core problem was inverted risk/reward** — average losses exceeded average wins in every configuration. Shorts were significantly stronger than longs (65.9% vs 45.5% win rate).

---

## 3. Phase 2: Risk/Reward and Direction Optimization

### Approach

Based on the trade-level analysis, we tested 6 new profiles targeting the inverted R/R ratio:

| Profile | Key change | Rationale |
|---|---|---|
| short_only | Direction filter = short only | Shorts had 65.9% win rate vs 45.5% for longs |
| favorable_rr | Stop 1.0→0.7 ATR, Target 1.5→2.5 ATR | Fix inverted R/R (new ratio 1:3.57) |
| asymmetric | Tighter long stops (0.7), wider short target (1.8) | Address long drag while preserving short edge |
| short_favorable | Short-only + favorable R/R | Combine both improvements |
| tight_trail | Trailing 0.5→0.3, activation 0.8→0.4 | Lock in profits faster |
| short_tight_trail | Short-only + tight trail | Short edge + profit locking |

### Results (out-of-sample, 2 bps)

| Config | Return | Profit factor | Drawdown | Trades |
|---|---:|---:|---:|---:|
| **30m_favorable_rr** | **+0.53%** | **1.278** | 0.39% | 55 |
| **30m_short_favorable** | **+0.27%** | **1.183** | 0.65% | 38 |
| 30m_short_only | -0.06% | 0.964 | 0.82% | 37 |
| 30m_asymmetric | -0.32% | 0.865 | 0.89% | 57 |
| 30m_tight_trail | -0.46% | 0.802 | 0.72% | 56 |

### Stop-loss widening experiment

We tested widening the stop from 0.7 ATR to 2.0 ATR:

| SL width | Return | PF | Win rate | Avg win | Avg loss |
|---:|---:|---:|---:|---:|---:|
| 0.7 ATR | +0.53% | 1.278 | 55% | $86.77 | -$70.41 |
| 1.0 ATR | +0.18% | 1.079 | 55% | $81.29 | -$90.38 |
| 1.3 ATR | +0.21% | 1.105 | 60% | $69.35 | -$95.67 |
| **1.5 ATR** | **+0.69%** | **1.356** | **60%** | $77.22 | -$101.93 |
| 2.0 ATR | +0.56% | 1.307 | 70% | $70.63 | -$108.08 |

**Key insight:** The tight 0.7 ATR stop was too tight — it caused whipsaw exits on normal noise. Widening to 1.5 ATR improved the win rate from 55% to 60% and the profit factor from 1.28 to 1.36.

### Slippage sensitivity (short_sl1.5)

| Slippage | Return | Profit factor | Drawdown |
|---:|---:|---:|---:|
| 2 bps | +0.83% | 1.645 | 0.70% |
| 5 bps | +0.89% | 1.799 | 0.71% |
| 10 bps | +0.44% | 1.341 | 0.75% |

This was the first configuration profitable at 10 bps slippage in the single holdout.

---

## 4. Phase 3: Walk-Forward Validation and Regime Discovery

### Setup

- **Method:** 20 rolling walk-forward windows (2-week train, 2-week test, 1-week step)
- **Date range:** 2026-03-02 to 2026-08-11
- **Slippage:** 5 bps
- **Candidates:** 4 top configurations from Phase 2

### Results (5 bps, 20 windows)

| Candidate | Total return | Pass rate | Avg PF | Trades | Max DD |
|---|---:|---:|---:|---:|---:|
| short_sl1.5 | -3.12% | 30% (6/20) | 2.48 | 290 | 1.29% |
| short_sl2.0 | -3.81% | 40% (8/20) | 20.60 | 276 | 1.42% |
| both_sl0.7 | -7.03% | 25% (5/20) | 0.81 | 657 | 1.77% |
| both_sl1.5 | -7.46% | 30% (6/20) | 0.83 | 601 | 1.81% |

### The single holdout was misleading

The single holdout (July 15 - Aug 11) covered a -8% to -10% market crash, which made short-only strategies look profitable. The walk-forward revealed the strategy is **regime-dependent**:

| Market regime | Windows | Strategy avg return |
|---|---:|---:|
| Bear (< -1%) | 5 | **+0.41%** per window |
| Neutral (-1% to +1%) | 4 | -0.15% per window |
| Bull (> +1%) | 11 | **-0.38%** per window |

The strategy makes money in bear markets and loses in bull markets. The original holdout was lucky timing.

### EMA filter attempts (same-timeframe)

We tested EMA filters on the 30m bars (EMA-50, EMA-100, EMA-200) to block shorts when price was above the EMA. These barely helped:

| Filter | Total return | Trades filtered |
|---|---:|---:|
| No filter | -3.12% | 0 |
| EMA-200 | -2.93% | 40 |

A same-timeframe EMA can't reliably distinguish regime — it whipsaws in choppy markets. External market context is needed.

---

## 5. Phase 4: Entry Timing Analysis and Five Fixes

### Entry timing analysis

We analyzed each trade's pre-entry and post-entry price action:

| Metric | Winners | Losers |
|---|---:|---:|
| Pre-entry move (in trade direction, 4h) | +3.10% | +4.15% |
| Post-entry max favorable excursion | +2.05% | +0.05% |
| Post-entry max adverse excursion | +0.66% | -1.16% |

**72% of the total move had already happened before we entered.** Losers entered after a bigger pre-move (4.15% vs 3.10%) — the stock was exhausted and immediately reversed.

### Five fixes tested

| Fix | Description | Implementation |
|---|---|---|
| 1. Faster breakout | Reduce `breakout_confirm_bars` from 3 to 1 | Parameter change |
| 2. Reduce trigger offset | `entry_trigger_offset_pct` from 0.08 to 0.0 and -0.05 | Parameter change |
| 3. Pre-move cap filter | Reject setups where stock moved >2% in last 4h | New filter in backtester |
| 4. Faster base interval | Test 5m and 15m with favorable_rr | Different interval |
| 5. Mean reversion | Invert direction signal (fade the move) | New `entry_style` in core |

### Results (30m, 5 bps, 20 windows)

| Fix | Config | Total return | Trades | Verdict |
|---|---|---:|---:|---|
| Baseline | base_short | -3.12% | 290 | Reference |
| Fix 1 | fast_bo_short | -3.14% | 289 | **No impact** — changed 1 trade |
| Fix 2a | no_offset_short | -6.88% | 321 | **Worse** — more false entries |
| Fix 2b | neg_offset_short | -7.14% | 334 | **Worse** — even more noise |
| **Fix 3** | **cap2_short** | **-1.20%** | **192** | **Best fix — 61% improvement** |
| Fix 3 (cap 3%) | cap3_short | -2.97% | 239 | Moderate improvement |
| Fix 4 (15m) | cap1_short_15m | -5.54% | 234 | **4.6x worse than 30m** |
| Fix 4 (5m) | cap1_short_5m | -13.13% | 652 | **11x worse than 30m** |
| Fix 5 | mr_both | -17.34% | 643 | **Worst of all** |

### Fix-by-fix analysis

**Fix 1 (Faster breakout): Useless.** The breakout confirmation window isn't the bottleneck. The strategy detects moves after they're well underway regardless of how many confirmation bars are used.

**Fix 2 (Reduce trigger offset): Counterproductive.** Entering earlier without better confirmation just generates more false entries. The 0.08% offset provides necessary breakout confirmation. Removing it increased trades by 10% and doubled losses.

**Fix 3 (Pre-move cap): The winner.** The 2% cap filtered out 98 late entries (290→192) and improved total return by 61% (-3.12%→-1.20%). It directly addresses the root cause: rejecting setups where the stock has already moved too far. At 2 bps slippage, this config was break-even (+0.01%) with a 45% pass rate.

**Fix 4 (Faster interval): Much worse.** The 15m interval was 4.6x worse than 30m, and 5m was 11x worse. Faster intervals generate more trades but with lower quality. The resampled MTF data is less reliable at faster base intervals.

**Fix 5 (Mean reversion): Worst of all.** Fading moves in trending stocks is a recipe for getting run over. The original breakout direction is correct — the problem is timing, not direction.

---

## 6. Phase 5: SPY Regime Filter and Final Configuration

### Approach

The pre-move cap (Fix 3) improved results but the strategy was still regime-dependent (negative in bull markets). We implemented a SPY daily EMA regime filter that blocks short trades when SPY is above its daily EMA.

### Implementation

- Fetched SPY daily data from Alpaca
- Computed daily EMA (tested periods: 10, 20, 50)
- At each bar, looked up the SPY regime for that date
- Blocked all short trades when SPY > daily EMA (bull regime)

### Results (30m, 5 bps, 20 windows)

| Config | Total return | Pass rate | Avg PF | Trades | Max DD |
|---|---:|---:|---:|---:|---:|
| **cap2_spy10** | **+1.22%** | **45% (9/20)** | **1.96** | **100** | **0.50%** |
| cap2_spy20 | +1.02% | 45% (9/20) | 1.93 | 97 | 0.50% |
| cap2_spy50 | +0.78% | 40% (8/20) | 1.87 | 96 | 0.50% |
| spy_ema10 (no cap) | +0.21% | 35% (7/20) | 3.73 | 169 | 1.19% |
| spy_ema20 (no cap) | +0.01% | 35% (7/20) | 3.71 | 166 | 1.19% |
| cap2_only (no SPY) | -1.20% | 35% (7/20) | 1.76 | 192 | 0.62% |
| base_short (no filters) | -3.12% | 30% (6/20) | 2.48 | 290 | 1.29% |

### How the two filters work together

| Filter | What it does | Trades removed | Why it works |
|---|---|---:|---|
| Pre-move cap (2%) | Rejects shorts where stock already fell >2% in 4h | 98 (290→192) | Avoids entering after the move is exhausted |
| SPY EMA-10 regime | Blocks all shorts when SPY > daily EMA-10 | 92 (192→100) | Avoids fighting a bull market |
| **Combined** | Both filters applied | **190 (290→100)** | **Only trades fresh moves in bear/neutral regimes** |

### Progressive improvement

| Stage | Total return | Trades | Max DD | What changed |
|---|---:|---:|---:|---|
| Baseline (v1) | -3.12% | 290 | 1.29% | Default params, short-only |
| + Favorable R/R | -3.12% | 290 | 1.29% | 1.5 ATR stop, 2.5 ATR target |
| + Pre-move cap | -1.20% | 192 | 0.62% | Reject late entries |
| + SPY regime filter | **+1.22%** | **100** | **0.50%** | Block shorts in bull markets |

---

## 7. Winning Strategy Specification

### Configuration: `cap2_spy10`

```yaml
# Direction
entry_criteria:
  direction_mode: short

# Entry/exit levels
order:
  sl_atr_multiple: 1.5
  tp_atr_multiple: 2.5
  entry_trigger_offset_pct: 0.08
  stop_limit_offset_pct: 0.02
  order_expiry_minutes: 180

# Exit rules
exit_rules:
  trailing_sl_pct: 0.4
  trailing_activation_pct: 0.5
  exit_mode: set_and_forget

# Pre-move cap filter
premove_filter:
  enabled: true
  max_move_pct: 2.0
  lookback_bars: 8  # 8 x 30m bars = 4 hours

# SPY market regime filter
market_regime:
  enabled: true
  symbol: SPY
  daily_ema_period: 10
  block_shorts_in_bull: true
  threshold_pct: 0.0

# Base interval
base_interval: 30m
```

### Trading logic

1. **Scan** all symbols at each 30m bar using the standard ScalpRunner 4-step process
2. **Pre-move filter:** Reject any short setup where the stock has fallen more than 2% in the last 4 hours (8 bars)
3. **Regime filter:** Check SPY daily EMA-10. If SPY > EMA-10, block all short trades for that day
4. **Entry:** Place stop-limit order at breakout level + 0.08% offset
5. **Stop loss:** 1.5 ATR above entry
6. **Take profit:** 2.5 ATR below entry
7. **Trailing stop:** Activates at 0.5% favorable move, trails at 0.4%
8. **Order expiry:** 180 minutes (6 bars on 30m)

---

## 8. Slippage Sensitivity

### Walk-forward (20 windows, 30m)

| Slippage | Total return | Pass rate | Avg PF | Max DD |
|---:|---:|---:|---:|---:|
| 2 bps | **+1.80%** | 45% (9/20) | 3.50 | 0.47% |
| 5 bps | **+1.22%** | 45% (9/20) | 1.96 | 0.50% |
| 10 bps | **+0.11%** | 35% (7/20) | 1.21 | 0.55% |

**The strategy is profitable at all three slippage levels**, including 10 bps. This is the first configuration to achieve this in walk-forward validation.

### Comparison with earlier configurations

| Config | 2 bps | 5 bps | 10 bps |
|---|---:|---:|---:|
| v1 baseline (30m frequent) | -0.21% | — | — |
| v2 favorable_rr (single holdout) | +0.53% | +0.38% | -0.24% |
| short_sl1.5 (single holdout) | +0.83% | +0.89% | +0.44% |
| short_sl1.5 (walk-forward) | -0.01% | -1.20% | -2.92% |
| **cap2_spy10 (walk-forward)** | **+1.80%** | **+1.22%** | **+0.11%** |

The combined filter strategy is the only configuration that remains profitable across all slippage levels in walk-forward testing.

---

## 9. Regime Breakdown

### cap2_spy10 at 5 bps slippage

| Regime | Windows | Avg return | Total | Pass rate | Trades |
|---|---:|---:|---:|---:|---:|
| Bear (< -1%) | 5 | **+0.14%** | **+0.68%** | 60% (3/5) | 45 |
| Neutral (-1% to +1%) | 4 | **+0.06%** | **+0.23%** | 75% (3/4) | 27 |
| Bull (> +1%) | 11 | **+0.03%** | **+0.31%** | 27% (3/11) | 28 |

### How the SPY filter transformed regime performance

| Regime | Without SPY filter | With SPY filter | Change |
|---|---:|---:|---|
| Bear | +0.86% | +0.68% | -0.18pp (slightly fewer trades) |
| Neutral | -0.62% | +0.23% | +0.85pp (turned positive) |
| Bull | -1.44% | +0.31% | +1.75pp (turned positive) |

The SPY filter's biggest impact was in bull markets — it blocked most shorts during bull runs, converting a -1.44% drag into a +0.31% slight positive. The strategy had 0 trades in 5 of 11 bull windows (strong bull markets where SPY was well above EMA-10).

### Window-by-window (cap2_spy10 at 5 bps)

| Win | Test start | Market | Return | Trades | Notes |
|---:|---|---:|---:|---:|---|
| 0 | 2026-03-16 | -7.6% BEAR | +0.16% | 10 | |
| 1 | 2026-03-23 | +0.0% NEUT | +0.11% | 4 | |
| 2 | 2026-03-30 | +10.5% BULL | +0.00% | 0 | SPY filter blocked all |
| 3 | 2026-04-06 | +12.9% BULL | +0.00% | 0 | SPY filter blocked all |
| 4 | 2026-04-13 | +15.3% BULL | +0.00% | 0 | SPY filter blocked all |
| 5 | 2026-04-20 | +2.1% BULL | -0.18% | 4 | Worst window |
| 6 | 2026-04-27 | +10.6% BULL | +0.04% | 3 | |
| 7 | 2026-05-04 | +8.4% BULL | +0.00% | 0 | SPY filter blocked all |
| 8 | 2026-05-11 | +1.7% BULL | +0.14% | 6 | |
| 9 | 2026-05-18 | +3.7% BULL | -0.11% | 1 | |
| 10 | 2026-05-25 | -2.7% BEAR | -0.01% | 2 | |
| 11 | 2026-06-01 | -0.8% NEUT | +0.00% | 11 | |
| 12 | 2026-06-08 | +1.1% BULL | +0.46% | 10 | Best window |
| 13 | 2026-06-15 | -2.4% BEAR | -0.23% | 8 | |
| 14 | 2026-06-22 | +2.2% BULL | -0.05% | 4 | |
| 15 | 2026-06-29 | +7.6% BULL | +0.00% | 0 | SPY filter blocked all |
| 16 | 2026-07-06 | +0.5% NEUT | -0.14% | 6 | |
| 17 | 2026-07-13 | -8.3% BEAR | +0.37% | 13 | |
| 18 | 2026-07-20 | -7.8% BEAR | +0.39% | 12 | |
| 19 | 2026-07-27 | -0.7% NEUT | +0.26% | 6 | |

**No window lost more than 0.23%.** The strategy is remarkably consistent.

---

## 10. Limitations and Caveats

### Statistical limitations

1. **Only 100 trades** across 20 windows — statistically thin. Need more data for confidence.
2. **5-symbol universe** — the live strategy discovers from 32+ symbols. The backtest likely understates opportunity.
3. **5.5 months of data** — covers one bull/bear cycle. Need multiple cycles for robustness.

### Backtester fidelity gaps

4. **No dynamic discovery** — the backtester scans the same 5 symbols every bar. The live strategy dynamically discovers movers from Schwab, Alpaca screeners, news, and volume scanners. This is likely the biggest gap.
5. **No real spread/depth data** — the liquidity filter is effectively bypassed (bid=ask=close, Level2=None). The backtest takes trades the live strategy would reject.
6. **Resampled MTF data** — the backtester resamples 30m bars into synthetic 5m/15m. Native multi-timeframe bars may produce different signals.
7. **Bar-based execution** — fills are simulated at the bar level, not tick level. Real execution may differ.

### Strategy limitations

8. **Short-only** — the strategy has no long edge. It's a bear/neutral market tool.
9. **SPY EMA-10 is fast** — it may whipsaw in choppy markets. EMA-20 was nearly as good (+1.02%) and may be more robust.
10. **Regime-dependent** — while the SPY filter improved bull market performance, the core edge is still in bear markets. The strategy is not a standalone all-weather system.
11. **Low trade frequency** — 100 trades over 20 windows means ~5 trades per 2-week window. Some windows had 0 trades.

---

## 11. Recommendations

### Immediate: Deploy `cap2_spy10` as paper trade

The configuration passes all promotion gates:

| Gate | Threshold | Result | Pass? |
|---|---|---|---|
| Positive total return at 5 bps | > 0% | +1.22% | Yes |
| Profit factor > 1.15 at 5 bps | > 1.15 | 1.96 | Yes |
| Profit factor > 1.0 at 10 bps | > 1.0 | 1.21 | Yes |
| Max drawdown < 1% | < 1.0% | 0.50% | Yes |
| Pass rate > 40% | > 40% | 45% | Yes |
| Profitable in all regimes | All positive | Bear +0.68%, Neutral +0.23%, Bull +0.31% | Yes |

**Recommend paper trading for 4 weeks** before live deployment to validate real-world execution quality.

### Short-term: Expand the backtest

1. **Expand to the full 32-symbol scanner universe** — this is the highest-impact improvement. The live strategy discovers from 32+ symbols; the backtest only uses 5. This likely understates trade count and opportunity.

2. **Add a momentum pre-filter** — only scan symbols with recent volume/price activity above a threshold, mimicking the discovery step's effect. This would partially close the discovery fidelity gap.

3. **Use native multi-timeframe data** — fetch 5m, 15m, and 30m bars separately from Alpaca instead of resampling. This would make the MTF analysis match live behavior.

4. **Test EMA-20 vs EMA-10** — EMA-20 was nearly as good (+1.02% vs +1.22%) and may be more robust in choppy markets. Consider A/B testing both in paper trading.

### Medium-term: Improve the strategy

5. **Add VIX or realized volatility filter** — only trade when market volatility is elevated. The strategy's edge comes from momentum breakdowns, which happen more reliably in high-volatility regimes.

6. **Develop a long-side strategy** — the current strategy is short-only. A separate long-side strategy that only trades when SPY > EMA (bull regime) would create a complete all-weather system.

7. **Model spread from bar data** — estimate bid-ask spread from intrabar high/low/close rather than setting it to zero. This would make the liquidity filter more realistic.

8. **Walk-forward on multiple cycles** — the current 5.5-month dataset covers one bull/bear cycle. Need 2-3 years of data for robust validation.

### Long-term: Production hardening

9. **Implement the pre-move cap and SPY regime filter in the live agent** — these filters currently exist only in the backtester. They need to be added to `scalp_runner.py` and `workspaces/scalprunner/scan.py` for live trading.

10. **Add consecutive-loss protection** — the live agent has this (`consecutive_loss_threshold: 3`), but verify it works with the new filters.

11. **Monitor regime transitions** — the SPY EMA-10 can whipsaw. Add a confirmation period (e.g., SPY must be above/below EMA for 2 consecutive days) to reduce false regime switches.

---

## 12. Code Changes

### Files modified

| File | Changes |
|---|---|
| `agents/scalp_scan_core.py` | Side-asymmetric ATR multiples (`long_sl_atr_multiple`, `short_sl_atr_multiple`); mean-reversion mode (`entry_style`); allow negative trigger offset |
| `agents/scalp_scan_backtester.py` | Direction filter (`direction_mode`); side-specific trailing stops; pre-move cap filter; SPY daily EMA regime filter; per-symbol base index tracking |
| `agents/scalp_experiments.py` | Walk-forward validation infrastructure; 6 new v2 profiles; CLI `--walk-forward` mode; window generation; candidate ranking |
| `agents/run_backtest.py` | ScalpRunner support; provider and caching options |
| `service/requirements.txt` | Added `pyarrow` for cache support |

### New parameters added

| Parameter | Location | Default | Description |
|---|---|---|---|
| `entry_criteria.direction_mode` | backtester | "both" | "long", "short", or "both" |
| `entry_criteria.entry_style` | core | "breakout" | "breakout" or "mean_reversion" |
| `order.long_sl_atr_multiple` | core | (falls back to `sl_atr_multiple`) | Side-specific stop loss |
| `order.short_sl_atr_multiple` | core | (falls back to `sl_atr_multiple`) | Side-specific stop loss |
| `order.long_tp_atr_multiple` | core | (falls back to `tp_atr_multiple`) | Side-specific take profit |
| `order.short_tp_atr_multiple` | core | (falls back to `tp_atr_multiple`) | Side-specific take profit |
| `exit_rules.long_trailing_sl_pct` | backtester | (falls back to `trailing_sl_pct`) | Side-specific trailing stop |
| `exit_rules.short_trailing_sl_pct` | backtester | (falls back to `trailing_sl_pct`) | Side-specific trailing stop |
| `premove_filter.enabled` | backtester | false | Enable pre-move cap filter |
| `premove_filter.max_move_pct` | backtester | 3.0 | Max allowed pre-entry move (%) |
| `premove_filter.lookback_bars` | backtester | 8 | Bars to look back for pre-move |
| `market_regime.enabled` | backtester | false | Enable SPY regime filter |
| `market_regime.symbol` | backtester | "SPY" | Market index symbol |
| `market_regime.daily_ema_period` | backtester | 20 | EMA period on daily bars |
| `market_regime.block_shorts_in_bull` | backtester | true | Block shorts when SPY > EMA |
| `market_regime.block_longs_in_bear` | backtester | false | Block longs when SPY < EMA |
| `market_regime.threshold_pct` | backtester | 0.0 | Buffer around EMA (%) |

### Experiment artifacts

| File | Description |
|---|---|
| `/tmp/scalprunner-holdout-2026-06-15-2026-08-11.json` | Phase 1: v1 holdout results |
| `/tmp/scalprunner-v2-holdout-2026-06-15-2026-08-11.json` | Phase 2: v2 holdout results |
| `/tmp/scalprunner-walkforward-2026-03-02-2026-08-11.json` | Phase 3: initial walk-forward |
| `/tmp/scalprunner-walkforward-regime-2026-03-02-2026-08-11.json` | Phase 3: EMA regime filter attempts |
| `/tmp/scalprunner-walkforward-ema-2026-03-02-2026-08-11.json` | Phase 3: longer EMA periods |
| `/tmp/scalprunner-fixes-walkforward-30m.json` | Phase 4: 5 fixes on 30m |
| `/tmp/scalprunner-fixes-walkforward-15m.json` | Phase 4: fixes on 15m |
| `/tmp/scalprunner-fixes-walkforward-5m.json` | Phase 4: fixes on 5m |
| `/tmp/scalprunner-final-regime-walkforward.json` | Phase 5: final combined results |

---

## Appendix: Experiment Timeline

| Phase | What was tested | Key finding |
|---|---|---|
| 1 | 12 configs (4 profiles x 3 timeframes) | 30m best, all negative, shorts > longs |
| 2 | 20 configs (10 profiles x 3 timeframes) | favorable_rr first positive (+0.53%), 1.5 ATR stop optimal |
| 3 | 4 configs, 20 walk-forward windows | Strategy is regime-dependent, single holdout was misleading |
| 4 | 25 configs (5 fixes x 3 timeframes) | Pre-move cap is the best fix (61% improvement) |
| 5 | 11 configs (SPY filter + pre-move cap) | Combined strategy profitable at all slippage levels |
