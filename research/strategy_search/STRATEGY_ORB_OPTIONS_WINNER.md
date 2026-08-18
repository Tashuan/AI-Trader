# ORB Options — Corrected Paper Strategy

> **Status:** PROMISING — paper/shadow validation only; not approved for live capital
> **Validation:** Positive full-period and chronological holdout results under corrected assumptions
> **Full period:** +112.76% at 50% IV, PF 1.567, 38.92% max drawdown, 167 trades
> **Holdout:** +103.86% at 50% IV, PF 2.176, 24.95% max drawdown, 84 trades
> **Universe:** NVDA, TSLA, AAPL, COIN for the fixed-universe backtest
> **Execution model:** Black-Scholes with spread, adverse slippage, contract fees, and minimum premium filter
> **Live mode:** Explicitly paper-only; shadow mode is enabled by default
> **Runner alignment:** `agents/orb_runner.py` uses this configuration as `ORB_CONFIG` version `2.1-corrected-paper`

This is the canonical strategy record for the corrected ORB options configuration. It supersedes the earlier optimistic ORB results that omitted important execution and data-timing constraints. The strategy has positive expectancy in the tested periods, but its full-period drawdown is too high for live promotion.

## 1. Strategy Rules

1. Build the exclusive 5-minute opening range from 09:30–09:35 ET.
2. Begin evaluating breakouts after the range is complete; do not reuse the final range bar as a signal bar.
3. Require the canonical two-bar confirmation from `agents/orb_strategy.py`.
4. Enter long on a confirmed close above the range high and buy an OTM call.
5. Enter short on a confirmed close below the range low and buy a symmetric OTM put.
6. Do not open entries after 10:00 ET.
7. Permit at most four concurrent positions and one signal per symbol per session.
8. Size each position at 3% of equity based on option premium.
9. Require an option entry premium of at least $0.20 per share.
10. Apply a 1.0% underlying stop and 2.0% underlying target.
11. Force-close all positions at the end of the session.

The live runner must use the same canonical range and breakout path as the backtester. Dynamic discovery remains a separate live-vs-backtest risk and must be measured during paper trading.

## 2. Locked Configuration

```json
{
  "range_minutes": 5,
  "confirmation_bars": 2,
  "stop_pct": 1.0,
  "target_pct": 2.0,
  "latest_entry": "10:00",
  "max_positions": 4,
  "position_pct": 3.0,
  "strike_offset": 1,
  "dte_min": 2,
  "dte_max": 14,
  "min_option_entry_price": 0.20,
  "option_spread_bps": 100,
  "option_slippage_bps": 50,
  "contract_fee": 0.65,
  "risk_free_rate": 0.05,
  "iv_assumption": 0.50,
  "skip_first_post_range_bar": true
}
```

The backtester also uses conservative intrabar ordering for bars that touch both a stop and a target. Option puts use the same OTM distance from ATM as calls; the old asymmetric/ITM put selection is not valid.

## 3. Corrected Validation

### Full period — 2026-06-15 through 2026-08-17

| IV | Return | Profit factor | Max drawdown | Trades |
|---:|---:|---:|---:|---:|
| 25% | +220.75% | 2.033 | 32.92% | 149 |
| 50% | +112.76% | 1.567 | 38.92% | 167 |
| 75% | +67.79% | 1.314 | 50.55% | 182 |

### Chronological holdout — 2026-07-16 through 2026-08-17

| IV | Return | Profit factor | Max drawdown | Trades |
|---:|---:|---:|---:|---:|
| 25% | +150.30% | 2.623 | 22.03% | 77 |
| 50% | +103.86% | 2.176 | 24.95% | 84 |
| 75% | +75.12% | 1.776 | 28.84% | 91 |

The result remains positive across the IV sensitivity range and in the later holdout. It is not yet promotable because the full-period drawdown exceeds the aggressive risk gate at the middle and high IV assumptions, and Black-Scholes remains an approximation of real option execution.

## 4. Execution and Data Assumptions

The corrected pass addresses the failure modes found in the earlier audit:

- Discovery inputs are frozen to data available before the session instead of using 09:30 prices at 09:20.
- The date range excludes the accidental extra session from the first sweep.
- Calls and puts use symmetric OTM strike selection.
- Options below $0.20 are rejected as economically unreliable.
- Entry and exit prices include a 100 bps spread assumption, 50 bps adverse slippage, and $0.65 per-contract fees.
- Exits are priced conservatively rather than at an unadjusted theoretical midpoint.
- Backtest signal generation uses the canonical `OpeningRangeBuilder` and `BreakoutChecker` path.

These assumptions make the result less attractive than the earlier +196.84% sweep, but materially more credible.

## 5. Risk Controls

ORBRunner is paper-only and must retain these mechanical controls:

- Daily loss pause at 10% of starting daily equity.
- Rolling drawdown pause at 30% from peak equity.
- No new entries after a risk halt; existing positions continue to be monitored for exits.
- Maximum four concurrent positions.
- Minimum option premium filter of $0.20.
- Persistent risk state and structured discovery, entry, exit, and halt events.

A risk halt is a safety boundary, not a signal to increase size or resume live trading.

## 6. Promotion Decision

| Stage | Decision |
|---|---|
| Historical research | Passes corrected expectancy and holdout checks |
| Shadow mode | Ready |
| Limited paper | Pending clean shadow sessions and execution review |
| Live capital | Rejected for now |

Required next evidence is at least 10 clean shadow sessions, including actual option quotes/fills, rejected signals, discovery selections, spread/slippage observations, daily loss state, and realized drawdown. Re-run validation after the paper sample; do not promote from the backtest alone.

## 7. Source References

- `agents/orb_strategy.py` — canonical range and breakout signal logic
- `agents/orb_runner.py` — paper-only live runner and risk guardrails
- `research/strategy_search/orb_options_bs_backtester.py` — corrected BS backtester
- `research/strategy_search/orb_sweep.py` — experiment harness
- `docs/ORB_OPTIONS_STRATEGY.md` — operational overview
- `research/strategy_search/journal.md` — chronological research record
