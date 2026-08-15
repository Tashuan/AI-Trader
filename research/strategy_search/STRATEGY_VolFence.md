# VolFence — Volatility-Filtered Opening-Range Breakout

> **Status:** REJECTED pending reproduction — current code does not meet the 60% gate
> **Validated:** Oct 2024 – Aug 2026 (94 windows, 22 months, 34 trades)
> **Current result:** -0.15% at 5bps, 40% of 15 volatility-eligible windows passed

---

## 1. What It Is

VolFence is a day-trading strategy that buys or sells the first breakout of the morning opening-range "fence" on high-volatility days. It only trades when SPY 20-day historical volatility is above 1.0% and SPY 20-day ATR is above 1.2% — filtering out the low-volatility chop where opening-range breakouts fail.

**One trade per day. One symbol per day. Fixed 2R stop and target. No trailing. No retest confirmation. In and out.**

---

## 2. Why It Works

The edge comes from three layers of selectivity:

### Layer 1: The Fence (opening-range filter)
The first 5-minute bar of the regular session (09:30–09:35 ET) forms the "fence." If that bar's range is between 0.35% and 1.05% of its close, it's wide enough to matter but not so wide that the move already happened. Stocks with tiny opening bars (<0.35%) are low-energy and breakouts fizzle. Stocks with huge opening bars (>1.05%) are already extended and the edge is gone.

### Layer 2: The Breakout (directional trigger)
When a subsequent 5m bar closes outside the fence high (long) or fence low (short) with its body fully outside the rail, that's the breakout. No retest confirmation — the strategy enters immediately on the breakout bar's close. Retesting kills the edge because the market doesn't always come back to test the fence before trending.

### Layer 3: The Volatility Regime (when to trade at all)
This is the key unlock. The strategy only trades on days where:
- SPY 20-day annualized volatility > 1.0%
- SPY 20-day ATR (as % of close) > 1.2%

In low-volatility regimes (SPY vol < 0.7%), opening-range breakouts are fakeouts — price breaks out, reverses, and stops you out. In high-volatility regimes, the breakout has momentum behind it and follows through to the 2R target.

**The historical research artifact reported +0.80% with this filter, but the current implementation reproduces -0.15% at 5bps. The historical result must not be treated as validated until the discrepancy is resolved.**

---

## 3. Full Configuration

### Strategy Parameters (override on top of `FENCE_BAR_DEFAULTS`)

```json
{
  "session": {
    "timezone": "America/New_York",
    "market_open": "09:30",
    "fence_end": "09:35",
    "latest_breakout": "10:30",
    "force_exit": "15:55"
  },
  "fence": {
    "min_range_pct": 0.35,
    "max_range_pct": 1.05
  },
  "breakout": {
    "require_body_outside": true,
    "min_close_distance_pct": 0.0,
    "max_bars_after_fence": 12
  },
  "retest": {
    "enabled": false
  },
  "anchor": {
    "enabled": true,
    "period": 20,
    "require_trend_alignment": false,
    "max_distance_pct": 2.0,
    "extended_action": "reject"
  },
  "risk": {
    "stop_mode": "fence_midpoint",
    "target_multiple_r": 2.0,
    "risk_per_trade_pct": 0.50,
    "max_trades_per_day": 1
  },
  "exit": {
    "mode": "fixed_sl_tp",
    "trailing_pct": 0.3,
    "trailing_activation_pct": 0.3,
    "max_bars": 0
  },
  "premarket": {
    "enabled": false
  }
}
```

### Volatility Filter (applied before each trading day)

| Filter | Threshold | Source |
|--------|-----------|--------|
| SPY 20-day historical volatility | > 1.0% | `SPY daily close pct_change rolling(20).std() * 100` |
| SPY 20-day ATR% | > 1.2% | `(High - Low) / Close * 100`, rolling(20).mean() |

If either filter fails on a given day, **skip trading that day entirely.** No entries, no positions.

### Symbol Discovery (per walk-forward window)

**Universe (29 symbols):**
```
NVDA, TSLA, AAPL, AMD, META, AMZN, MSFT, GOOGL, NFLX, INTC, MU,
QQQ, SPY, IWM, BA, DIS, BABA, COIN, MARA, RIOT, SOFI, AAL, UAL,
F, GM, NIO, XPEV, PLUG, DKNG
```

**Selection (top 15 by score, recomputed per 2-week test window):**
- **Gap score** (0–25 pts): `abs(gap_pct) * 5`, capped at 25, only if `|gap| >= 1.0%`
- **Volume score** (0–20 pts): `vol_ratio * 6`, capped at 20, only if `vol_ratio >= 1.25x`
- **ADV score** (0–20 pts): +20 if `price * avg_volume >= $25M`
- **Proximity score** (0–15 pts): +15 if close is within 1.0% of prior-day high or low

Sort by total score descending, take top 15. Fallback to `["NVDA", "TSLA", "AAPL", "AMD", "META"]` if all fail.

### Daily Symbol Selection

On each trading day, one symbol is chosen from the discovered set by **highest first-bar dollar volume** (close × volume of the 09:30 bar). This picks the most liquid, most traded symbol for that specific session.

---

## 4. Entry Rules (step by step)

1. **Check volatility filter.** At the start of each trading day, check SPY's 20-day vol and 20-day ATR%. If either is below threshold, skip the day.

2. **Capture the fence.** The 09:30–09:35 5-minute bar defines the fence:
   - Fence High = bar High
   - Fence Low = bar Low
   - Fence Midpoint = (High + Low) / 2
   - Range % = (High - Low) / Close × 100

3. **Validate the fence.** If range % is not between 0.35% and 1.05%, done for the day. No trade.

4. **Wait for breakout.** Watch subsequent 5m bars (up to 12 bars after fence, or until 10:30 ET — whichever comes first). A breakout occurs when:
   - **Long:** bar Close > Fence High AND bar Open >= Fence High (body fully outside)
   - **Short:** bar Close < Fence Low AND bar Open <= Fence Low (body fully outside)

5. **Check anchor filter.** If the entry price is more than 2.0% away from the 20-bar SMA of closes, reject the trade (price is too extended).

6. **Enter immediately** on the breakout bar's close price (no retest needed).

7. **Set stops:**
   - Stop loss = Fence Midpoint
   - Take profit = Entry ± (2 × risk_per_share), where risk = |entry - fence midpoint|
   - Risk per trade = 0.50% of equity

---

## 5. Exit Rules

| Exit Type | Trigger | Price |
|-----------|---------|-------|
| Stop loss | bar Low ≤ stop (long) / bar High ≥ stop (short) | Fence Midpoint |
| Take profit | bar High ≥ target (long) / bar Low ≤ target (short) | Entry ± 2R |
| Force exit | timestamp ≥ 15:55 ET | bar Close |

**No trailing stop. No time-based exit (max_bars=0).** The position runs until it hits the fixed stop, the fixed target, or the 15:55 force-exit.

---

## 6. Execution Assumptions

| Parameter | Value |
|-----------|-------|
| Slippage | 5 bps (0.05%) per fill |
| Fee rate | 0.1% (10 bps) per fill |
| Fill model | Price × (1 ± slippage) — buys fill higher, sells fill lower |
| Position sizing | `risk_budget / risk_per_share`, capped at 25% of equity |
| Risk per trade | 0.50% of current equity |
| Max trades per day | 1 |
| Max position size | 25% of equity |

---

## 7. Backtest Results

### Walk-Forward (94 windows, Oct 2024 – Aug 2026)

| Slippage | Return | Pass Rate | Trades | Max DD |
|----------|--------|-----------|--------|--------|
| 0 bps | not freshly reproduced | not freshly reproduced | 34 | not freshly reproduced |
| 2 bps | not freshly reproduced | not freshly reproduced | 34 | not freshly reproduced |
| **5 bps** | **-0.15%** | **40%** | 34 | **0.88%** |
| 10 bps | not freshly reproduced | not freshly reproduced | 34 | not freshly reproduced |

- **Pass rate** = fraction of active windows (windows with ≥1 trade) where return > 0 AND profit factor > 1.0
- **15 of 94 windows are volatility-eligible**; 13 of those produced trades in the current reproduction.
- Current pass rate is 6/15 = 40%, below the 60% promotion gate.

### Holdout Validation

The historical holdout numbers below belong to the stale research artifact and are not considered current validation. A fresh holdout should be rerun after the implementation/data discrepancy is resolved.

| Set | Windows | Return | Pass Rate | Trades |
|-----|---------|--------|-----------|--------|
| Historical train artifact | 65 | +0.83% | 58% | 25 |
| Historical holdout artifact | 29 | -0.03% | 67% | 10 |

---

## 8. What Kills This Strategy

| Variation | Result | Lesson |
|-----------|--------|--------|
| Remove vol filter | -11.92% | Low-vol chop destroys opening-range breakouts |
| Enable retest confirmation | -5.13% | The market doesn't always retest before trending |
| Use trailing stop | -2.14% | Trailing cuts winners before they reach 2R |
| Wide fence (>1.05%) | -7.38% | Too many low-quality setups |
| Narrow fence (<0.35%) | -1.12% | Too few trades, misses valid breakouts |
| 3R target instead of 2R | -1.88% | Winners don't reach 3R often enough |
| Early breakout (4 bars max) | -1.80% | Misses late-morning breakouts |
| Long-only | not tested | Strategy trades both directions; filtering by direction not explored |

---

## 9. How to Reproduce

### Data Requirements
- Alpaca market data (IEX feed sufficient)
- 5-minute bars for all 29 universe symbols
- Daily bars for SPY (for vol/ATR filter) and all universe symbols (for discovery)
- Period: at least Oct 2024 to Aug 2026 for full validation

### Code Path
```
agents/fence_bar_strategy.py          # Strategy logic (FenceBarStrategy)
agents/fence_bar_backtester.py        # Backtester (FenceBarBacktester)
research/strategy_search/fence_walk_forward.py  # Walk-forward harness + discovery
```

### Reproduction Script
```python
import sys, os
sys.path.insert(0, 'agents')
sys.path.insert(0, 'research/strategy_search')
from dotenv import load_dotenv
load_dotenv('.env')

from fence_walk_forward import discover_symbols, generate_windows
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge
from fence_bar_backtester import FenceBarBacktester
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider

provider = CachedProvider(AlpacaProvider())

# Strategy config (the VolFence overrides)
override = {
    'retest': {'enabled': False},
    'fence': {'min_range_pct': 0.35, 'max_range_pct': 1.05},
}
params = deep_merge(FENCE_BAR_DEFAULTS, override)

# Walk-forward
for w in generate_windows('2024-10-01', '2026-08-11'):
    symbols = discover_symbols(w['test_start'], provider, max_symbols=15)
    bt = FenceBarBacktester(
        symbols=symbols, params=params,
        start_date=w['test_start'], end_date=w['test_end'],
        initial_capital=100_000.0, slippage_bps=5.0,
        fee_rate=0.001, provider=provider,
    )
    report = bt.run()
    # Apply vol filter: check SPY vol/ATR for each trading day
    # (see VolFilteredFenceBarBacktester in research notes)
```

> **Note:** The volatility filter is implemented as a custom `VolFilteredFenceBarBacktester` subclass that wraps `FenceBarBacktester` and skips trading days where SPY vol/ATR is below threshold. This is not yet integrated into the main backtester — it lives in the research scripts. For production, the vol filter should be checked before each session open.

---

## 10. Caveats & Known Limitations

1. **Not currently validated.** The historical +0.80% result does not reproduce from the current committed implementation. Current reproduction is -0.15% at 5bps and 40% pass rate.

2. **Low trade count.** 34 trades in 22 months = ~1.5 trades per month. Statistically thin — the current 40% pass rate is based on 6/15 eligible windows passing.

3. **84% idle.** Only 15 of 94 windows are active. Capital sits unused most of the time. This is acceptable for a supplemental strategy but not a standalone portfolio.

4. **Vol filter parameters are in-sample.** The thresholds (vol > 1.0%, ATR > 1.2%) were selected on the same 22-month data. The holdout validates generalization but the filter itself was not selected out-of-sample.

5. **Daily-bar discovery proxy.** Symbol selection uses daily bars (gap/volume/proximity) rather than the real premarket scanner. The premarket scanner (yfinance-backed) is limited to 60 days of intraday data, making it unusable for the full backtest period.

6. **Single-symbol-per-day.** The backtester picks one symbol per day by first-bar dollar volume. In live trading, you'd want to monitor all qualifying symbols and pick the best setup in real-time.

7. **No live execution tested.** This is purely a backtest. Paper trading is needed to validate fill assumptions, slippage, and the vol filter in real-time.

---

## 11. File References

| File | Purpose |
|------|---------|
| `agents/fence_bar_strategy.py` | Core strategy logic — `FenceBarStrategy.on_bar()` |
| `agents/fence_bar_backtester.py` | Backtester with fill/exit logic |
| `research/strategy_search/fence_walk_forward.py` | Walk-forward harness + symbol discovery |
| `research/strategy_search/run_fence_final.json` | Historical backtest artifact; not reproducible from current code |
| `research/strategy_search/run_fence_current_code_reproduction.json` | Current 94-window reproduction (-0.15%, 40% eligible-window pass rate) |
| `research/strategy_search/run_fence_extended_5bps.json` | Historical extended backtest without vol filter (-11.92%) |
| `research/strategy_search/state.json` | Full research state with all experiments |
| `research/strategy_search/journal.md` | Research journal with all batches and lessons |
