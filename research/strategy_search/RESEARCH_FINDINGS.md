# Intraday Strategy Research Findings

## Summary

Tested 5 alternative intraday signal types on 1m bars to find a strategy
with repeatable edge. **ORB (Opening Range Breakout) is the only signal
with genuine gross edge**, but the edge is regime-dependent and too thin
to survive trading costs on a $10k equity account.

## Strategies Tested

All strategies tested on 1m bars, 5 symbols (NVDA, TSLA, AAPL, AMD, META),
Jun 15 - Aug 16 2026, with 2bps slippage and 0% commission:

| Strategy | Return | PF | Win Rate | Trades | Verdict |
|----------|--------|------|----------|--------|---------|
| ORB Wide | +0.20% | 1.009 | 42% | 197 | PASS |
| Momentum Burst Large | -0.90% | 0.379 | 31% | 13 | FAIL |
| Momentum Burst | -1.19% | 0.778 | 50% | 107 | FAIL |
| Gap Fade Large | -2.77% | 0.843 | 34% | 89 | FAIL |
| ORB (narrow) | -3.74% | 0.765 | 38% | 199 | FAIL |
| Vol Spike | -4.52% | 0.641 | 64% | 218 | FAIL |
| Vol Spike Extreme | -5.79% | 0.687 | 65% | 220 | FAIL |
| Gap Fade | -5.87% | 0.685 | 24% | 155 | FAIL |
| VWAP Reversion | -6.39% | 0.627 | 50% | 218 | FAIL |
| VWAP Reversion Wide | -8.92% | 0.646 | 50% | 215 | FAIL |

### Signal Descriptions

- **Gap Fade**: Fade the opening gap (gap up → short, gap down → long).
  Min gap 1.0%, stop 0.5%, target 0.3%. Failed — gaps tend to continue.
- **Gap Fade Large**: Same but min gap 2.0%, wider stop/target. Still failed.
- **VWAP Reversion**: Short above VWAP+band, long below VWAP-band.
  Band = 0.3% of price. Failed — VWAP reversion is too weak intraday.
- **VWAP Reversion Wide**: Band = 0.6%. Worse — wider entries = more adverse.
- **Vol Spike**: Mean-revert after 3-bar move > 0.5% in 1m bars.
  High win rate (64%) but tiny wins, large losses. PF 0.641.
- **Vol Spike Extreme**: Same but > 1.0% move. Similar failure.
- **ORB**: Breakout from 15-min opening range. Narrow range = too many false breakouts.
- **ORB Wide**: 5-min range, 0.6% stop, 1.0% target. The only passing config.
- **Momentum Burst**: Enter on 5-bar momentum > 0.3%. Failed — momentum doesn't persist on 1m.

## ORB Parameter Optimization

Swept 312 configs (range period, min range size, stop, target, entry cutoff),
then 90 fine-grained configs around the optimal region.

### Best Configuration

**5-minute opening range, 0.7% stop, 1.2% target, entry until 10:30**

| Metric | Realistic (2bps) | Zero Cost |
|--------|------------------|-----------|
| Return | +7.83% | +10.31% |
| Profit Factor | 1.355 | 1.489 |
| Win Rate | 48% | 48% |
| Trades | 190 | 190 |
| Max DD | 1.84% | — |
| Sharpe | 4.574 | — |

All 5 symbols profitable. Top 15 configs all pass (+5.26% to +7.83%).

## Out-of-Sample Validation

| Period | Market | Realistic | Zero Cost | Verdict |
|--------|--------|-----------|-----------|---------|
| Jun-Aug (in-sample) | Moderate bull (+2.9%) | +7.83% | +10.31% | PASS |
| Aug (held-out test) | — | +3.59% | — | PASS |
| Apr-Jun (OOS) | Strong bull (+13.2%) | -8.95% | -6.38% | FAIL |
| Apr-Aug (full 5mo) | Combined | -2.06% | +2.97% | Marginal |
| 10 symbols (Jun-Aug) | Moderate bull | +3.60% | +6.97% | PASS |

### Regime Dependence

ORB works in moderate-trend markets but fails in strong-trend markets.
In a strong bull market (+13.2% SPY in 2 months), short breakouts fail
repeatedly. A SPY direction filter (only trade with SPY's opening
direction) improved the full 5-month result from -2.06% to +0.16%.

### With SPY Regime Filter

| Period | Realistic | Zero Cost |
|--------|-----------|-----------|
| Apr-Jun | -6.76% | -4.62% |
| Jun-Aug | +7.70% | +9.72% |
| Apr-Aug (5 months) | +0.16% | +4.33% |

## Why the Edge Is Too Thin for Equities

The core problem is leverage. On a $10k account:

- NVDA at $200, 0.1% move = $0.20/share
- To make $20: need 100 shares = $20,000 position
- $10k can't afford 100 shares of NVDA
- Even with fractional shares: 2bps slippage on $20k = $4 cost = 20% of $20 profit

The gross edge (~0.04% per trade) is real but cannot survive trading costs
on a small equity account. Options or futures provide the leverage needed
to turn small price moves into meaningful dollar profits.

## Next Steps: Options-Based ORB

Alpaca supports options contracts and chains. The ORB signal on the
underlying stock can be traded via options for 5-10x leverage amplification.
A +7.83% stock move over 2 months could become +40-80% on options, even
after accounting for wider bid-ask spreads and time decay.

See `orb_options_backtester.py` for the options-based implementation.

## Files

- `scalp_alt_signals.py` — Multi-strategy 1m backtester (5 signal types, 10 configs)
- `orb_optimize.py` — ORB parameter sweep (312 + 90 configs)
- `scalp_1m_adaptive.py` — ScalpScan 1m adaptive backtest (failed)
- `scalp_alt_results.json` — Initial screen results
- `orb_sweep_results.json` — ORB sweep results (top 50)
- `scalp_1m_adaptive_results.json` — ScalpScan 1m results
- `scalp_1m_zerocost.json` — ScalpScan zero-cost results
