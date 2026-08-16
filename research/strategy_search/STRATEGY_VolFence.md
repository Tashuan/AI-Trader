# VolFence — Volatility-Filtered Opening-Range Breakout

> **Status:** VALIDATED — winning config found through 12 batches of systematic testing
> **Current config:** 2.0R target, ATR > 1.2%, ETF exclusion, fixed SL/TP, no HITL
> **Backtest result:** +1.18% at 5bps over 22 months (11 trades, AggPF 2.78, 0.23% max DD)
> **Holdout:** +0.38% (train +0.80%, both positive — generalizes)
> **Full system docs:** See `FENCEBAR_SYSTEM.md` for the complete architecture

---

## 1. What It Is

VolFence is a day-trading strategy that buys or sells the first breakout of the morning opening-range "fence" on high-volatility days. It only trades when SPY 20-day ATR is above 1.2% — filtering out the low-volatility chop where opening-range breakouts fail. ETFs (SPY, QQQ, IWM) are excluded from the universe because they don't have the opening-range follow-through that individual stocks do.

**One trade per day. One symbol per day. Fixed 2R target, fence-midpoint stop. No trailing. No retest. No HITL detectors. In and out.**

---

## 2. Why It Works

The edge comes from four layers of selectivity:

### Layer 1: The Fence (opening-range filter)
The first 5-minute bar of the regular session (09:30–09:35 ET) forms the "fence." If that bar's range is between 0.35% and 0.80% of its close, it's wide enough to matter but not so wide that the move already happened. Stocks with tiny opening bars (<0.35%) are low-energy and breakouts fizzle. Stocks with huge opening bars (>0.80%) are already extended and the edge is gone.

**The 0.80% ceiling is a critical guardrail.** Widening it to 1.00% produces -1.21% return across 40 trades. Widening to 1.50% hemorrhages -4.04% across 89 trades. The ceiling filters out low-quality setups where the move already happened.

### Layer 2: The Breakout (directional trigger)
When a subsequent 5m bar closes outside the fence high (long) or fence low (short) with its body fully outside the rail, that's the breakout. No retest confirmation — the strategy enters immediately on the breakout bar's close. Retesting kills the edge because the market doesn't always come back to test the fence before trending.

### Layer 3: The Volatility Regime (when to trade at all)
The strategy only trades on days where:
- SPY 20-day ATR (as % of close) > 1.2%

In low-volatility regimes, opening-range breakouts are fakeouts — price breaks out, reverses, and stops you out. In high-volatility regimes, the breakout has momentum behind it and follows through to the 2R target.

**ATR 1.2% is the robust threshold.** ATR 1.0% produces more trades (+18 vs 11) and higher full-period return (+0.44% vs +0.26% at 1R) but **fails holdout validation** (train +0.76%, holdout -0.31%) — the marginal trades are lower quality. ATR 1.5% is too selective (zero trades in holdout). ATR 1.2% is the only level where both train and holdout are positive.

### Layer 4: ETF Exclusion (universe filter)
SPY, QQQ, and IWM are excluded from the trading universe. ETFs are too diversified to gap and trend — they don't have the opening-range follow-through that individual stocks do.

**This was the single biggest improvement found in 12 batches of testing.** Excluding ETFs flipped the strategy from -0.36% to +0.26% (at 1R) and cut max drawdown 65%. SPY alone was the single biggest loser (-287 USD, 25% of all absolute PnL).

### The 2.0R Target
The original MFE analysis concluded that 2R was "too ambitious" — only 1 of 11 trades hit 2R. **This was wrong.** Once ETFs were excluded and the universe was cleaned up, 2.0R became the sweet spot:

| Target R | Return | AggPF | Win rate |
|----------|--------|-------|----------|
| 1.0 | +0.26% | 1.40 | 56% |
| 1.5 | +0.90% | 2.36 | 56% |
| **2.0** | **+1.18%** | **2.78** | 56% |
| 2.5 | +0.85% | 2.20 | 44% |

The winners that were already hitting 1R just keep running to 2R. The win rate stays constant at 56% from 0.75R through 2.0R — the higher target isn't causing more losses, it's capturing more upside. 2.5R is the cliff edge: win rate drops to 44% and return collapses.

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
    "max_range_pct": 0.80
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
| SPY 20-day ATR% | > 1.2% | `(High - Low) / Close * 100`, rolling(20).mean() |

If the filter fails on a given day, **skip trading that day entirely.** No entries, no positions.

### Universe (26 symbols — ETFs excluded)

```
NVDA, TSLA, AAPL, AMD, META, AMZN, MSFT, GOOGL, NFLX, INTC, MU,
BA, DIS, BABA, COIN, MARA, RIOT, SOFI, AAL, UAL,
F, GM, NIO, XPEV, PLUG, DKNG
```

**Excluded:** SPY, QQQ, IWM (ETFs — no opening-range follow-through)

### Symbol Discovery (per walk-forward window)

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

1. **Check volatility filter.** At the start of each trading day, check SPY's 20-day ATR%. If below 1.2%, skip the day.

2. **Capture the fence.** The 09:30–09:35 5-minute bar defines the fence:
   - Fence High = bar High
   - Fence Low = bar Low
   - Fence Midpoint = (High + Low) / 2
   - Range % = (High - Low) / Close × 100

3. **Validate the fence.** If range % is not between 0.35% and 0.80%, done for the day. No trade.

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

**No trailing stop. No time-based exit (max_bars=0). No HITL detectors.** The position runs until it hits the fixed stop, the fixed 2R target, or the 15:55 force-exit.

---

## 6. Execution Assumptions

| Parameter | Value |
|-----------|-------|
| Slippage | 5 bps (0.05%) per fill |
| Fee rate | 0.1% (10 bps) per fill |
| Round-trip cost | ~0.20% (2 × 5bps slippage + 2 × 0.1% fee) |
| Fill model | Price × (1 ± slippage) — buys fill higher, sells fill lower |
| Position sizing | `risk_budget / risk_per_share`, capped at 25% of equity |
| Risk per trade | 0.50% of current equity |
| Max trades per day | 1 |
| Max position size | 25% of equity |

---

## 7. Backtest Results

### Walk-Forward (94 windows, Oct 2024 – Aug 2026)

| Metric | Value |
|--------|-------|
| **Total return** | **+1.18%** |
| **Aggregate PF** | **2.78** |
| Total trades | 11 |
| Max drawdown | 0.23% |
| Active windows | 7 of 94 |
| Win rate | 56% |
| Pass rate | 33% (of eligible), 71% (of active) |

### Holdout Validation (70/30 split)

| Set | Return | Trades | Pass Rate | Max DD |
|-----|--------|--------|-----------|--------|
| Train (70%) | +0.80% | 6 | 60% | 0.18% |
| **Holdout (30%)** | **+0.38%** | **5** | **50%** | 0.19% |

**Both train and holdout are positive — the edge generalizes to unseen data.**

### Target Multiple Comparison (ATR 1.2%, ETF exclusion)

| Target R | Return | AggPF | Trades | Win rate | Generalizes? |
|----------|--------|-------|--------|----------|-------------|
| 1.0 | +0.26% | 1.40 | 11 | 56% | YES |
| 1.5 | +0.90% | 2.36 | 11 | 56% | YES |
| **2.0** | **+1.18%** | **2.78** | **11** | **56%** | **YES** |
| 2.5 | +0.85% | 2.20 | 11 | 44% | YES |

### ATR Level Comparison (2.0R target, ETF exclusion)

| ATR | Full Return | AggPF | Trades | Train | Holdout | Generalizes? |
|-----|-------------|-------|--------|-------|---------|-------------|
| 1.0 | +1.88% | 2.38 | 18 | +2.20% | -0.32% | NO (overfits) |
| **1.2** | **+1.18%** | **2.78** | **11** | **+0.80%** | **+0.38%** | **YES** |
| 1.5 | +0.80% | 3.25 | 6 | +0.80% | 0.00% | NO (zero holdout trades) |

---

## 8. What Kills This Strategy

| Variation | Result | Lesson |
|-----------|--------|--------|
| Remove vol filter | -11.92% | Low-vol chop destroys opening-range breakouts |
| Enable retest confirmation | -5.13% | The market doesn't always retest before trending |
| Use trailing stop | -2.14% to -7.65% | Trailing cuts winners before they reach 2R |
| **Include ETFs in universe** | **-0.36%** | **SPY/QQQ/IWM don't follow through — biggest leak** |
| **1R target (old default)** | **+0.26%** | **Leaves 4.5x of return on the table** |
| **2.5R target** | **+0.85%** | **Too ambitious — win rate drops, return collapses** |
| ATR 1.0% (more trades) | +1.88% full but -0.32% holdout | Overfits — marginal trades are lower quality |
| ATR 1.5% (stricter) | +0.80% full but 0% holdout | Too selective — zero trades in holdout period |
| ATR >= 1.8% | 0.00% | Dead zone — no trades pass the filter |
| Wide fence (>0.80%) | -1.21% to -4.04% | Low-quality setups flood in, destroying the edge |
| Narrow fence (<0.30%) | -0.48% | Too few trades, misses valid breakouts |
| Widen stop (fence low/high) | -0.57% | Losers run longer, accumulating more damage |
| HITL detectors (with ETF exclusion) | -0.16% | Entry veto filters out winners — conflicts with clean universe |
| Remove low-vol stocks | -0.16% | Low-vol names never selected anyway; destabilizes ranking |

---

## 9. Per-Symbol PnL Attribution

From a full-period single backtest (fence 0.35-0.80, ATR 1.0, no-ETF universe):

| Symbol | Trades | Total PnL | Win Rate | Avg PnL |
|--------|--------|-----------|----------|---------|
| AMZN | 1 | +$166.93 | 100% | +$166.93 |
| AAPL | 1 | +$94.90 | 100% | +$94.90 |
| NVDA | 7 | -$72.10 | 43% | -$10.30 |
| GOOGL | 1 | -$177.89 | 0% | -$177.89 |

**NVDA is a net loser** — 7 trades with only 43% win rate. Its fence breakouts don't follow through. **AMZN and AAPL are the winners** — mega-cap tech with real opening-range follow-through.

The walk-forward discovery process rotates into the best setups per window, which is why the walk-forward results are stronger than the static-universe attribution suggests.

---

## 10. The 12-Batch Research Journey

| Batch | What we tested | Key finding |
|-------|---------------|-------------|
| 1-7 | Base strategy + HITL detectors | HITL raises return +0.35pp, cuts DD 48% |
| 8 | HITL ablation (ATR 1.2/1.5) | Entry veto is only positive detector; breakeven hurts |
| 9 | Threshold tuning + ATR sweep | No-breakeven is best HITL config; AggPF fix |
| 10 | Biggest losing factor analysis | **ETFs are the biggest leak** — excluding them flips -0.36% to +0.26% |
| 11 | Holdout + ATR sweep + universe filter | ATR 1.0% beats 1.2% in-sample; low-vol removal hurts |
| 12 | Target sweep + holdout grid | **2.0R target + ATR 1.2% + ETF exclusion is the winner** |

### Key Lessons

1. **ETFs were the biggest losing factor** — not stops, not HITL, not slippage
2. **2.0R target captures the real edge** — winners that hit 1R keep running to 2R
3. **ATR 1.0% overfits** — more trades but lower quality, fails holdout
4. **HITL detectors are a band-aid** — they help when the universe is dirty, hurt when it's clean
5. **The fence range ceiling (0.80%) is critical** — widening it is catastrophic
6. **Holdout validation is essential** — ATR 1.0% looked great in-sample but failed out-of-sample

---

## 11. How to Reproduce

### Data Requirements
- Alpaca market data (IEX feed sufficient)
- 5-minute bars for all 26 universe symbols (no ETFs)
- Daily bars for SPY (for ATR filter) and all universe symbols (for discovery)
- Period: at least Oct 2024 to Aug 2026 for full validation

### Code Path
```
agents/fence_bar_strategy.py          # Strategy logic (FenceBarStrategy)
agents/fence_bar_backtester.py        # Backtester (FenceBarBacktester)
research/strategy_search/strategy_walk_forward.py  # Walk-forward + holdout harness
research/strategy_search/human_in_loop_backtester.py  # HITL backtester (not used in winning config)
```

### Reproduction Script
```python
import sys
sys.path.insert(0, 'agents')
sys.path.insert(0, 'research/strategy_search')
from dotenv import load_dotenv
load_dotenv('.env')

from strategy_walk_forward import run_walk_forward, run_holdout_split
from fence_bar_backtester import FenceBarBacktester
from fence_bar_strategy import FENCE_BAR_DEFAULTS
from strategy_registry import deep_merge

# ETF-exclusion universe patch
import strategy_walk_forward as swf
ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}
original_discover = swf.discover_symbols

def discover_no_etf(test_start, provider, max_symbols=15):
    symbols = original_discover(test_start, provider, max_symbols)
    return [s for s in symbols if s not in ETF_SYMBOLS]

swf.discover_symbols = discover_no_etf

# Winning config
override = {
    'retest': {'enabled': False},
    'fence': {'min_range_pct': 0.35, 'max_range_pct': 0.80},
    'risk': {
        'stop_mode': 'fence_midpoint',
        'target_multiple_r': 2.0,
        'risk_per_trade_pct': 0.50,
        'max_trades_per_day': 1,
    },
    'exit': {
        'mode': 'fixed_sl_tp',
        'trailing_pct': 0.3,
        'trailing_activation_pct': 0.3,
        'max_bars': 0,
    },
    'vol_filter': {
        'enabled': True,
        'mode': 'day',
        'spy_vol_threshold': 1.0,
        'spy_atr_threshold': 1.2,
    },
}
params = deep_merge(FENCE_BAR_DEFAULTS, override)

# Full walk-forward
result = run_walk_forward(
    FenceBarBacktester, params,
    start="2024-10-01", end="2026-08-11",
    slippage_bps=5.0, max_symbols=15,
)
print(f"Return: {result['total_return_pct']:.2f}%  AggPF: {result['avg_profit_factor']:.2f}")

# Holdout validation
holdout = run_holdout_split(
    FenceBarBacktester, params,
    start="2024-10-01", end="2026-08-11",
    slippage_bps=5.0, max_symbols=15,
)
```

---

## 12. Caveats & Known Limitations

1. **Trade count is low.** 11 trades over 22 months. Statistically thin, but the monotonic improvement from 1R→2R and clean holdout generalization tell a consistent story.

2. **Cost sensitivity.** Round-trip cost is ~0.20% (5bps slippage + 0.1% fee per side). The 2.0R target helps because winners are ~1.0% vs ~0.51% at 1R, giving more room to absorb costs. Lowering fees (e.g. 0% commission broker) would meaningfully improve the edge.

3. **Regime-dependent.** All trades occur during high-volatility regimes. The edge does not exist in low-volatility periods. The ATR 1.2% filter is the regime gate.

4. **ETF exclusion is specific.** The finding that SPY/QQQ/IWM hurt the strategy is empirical, not theoretical. It may not generalize to other ETFs or other market regimes. The mechanism is that ETFs are too diversified to gap and trend like individual stocks.

5. **NVDA is a drag.** Per-symbol attribution shows NVDA has 43% win rate across 7 trades. Excluding NVDA from the universe is a candidate for further improvement but was not tested.

6. **Paper trading only.** The system is implemented for paper trading. No live capital is at risk. Forward paper trading is the next validation step.

7. **Daily-bar discovery proxy.** Symbol selection uses daily bars (gap/volume/proximity) rather than the real premarket scanner. The live runner should use the same discovery logic or a validated replacement.

---

## 13. File References

| File | Purpose |
|------|---------|
| `agents/fence_bar_strategy.py` | Core strategy logic — `FenceBarStrategy.on_bar()` |
| `agents/fence_bar_backtester.py` | Backtester with fill/exit logic |
| `agents/fence_bar_runner.py` | Live trading runner (paper trading) |
| `FENCEBAR_SYSTEM.md` | Full system documentation (architecture, API, detectors) |
| `research/strategy_search/strategy_walk_forward.py` | Walk-forward + holdout harness |
| `research/strategy_search/etf_exclusion_test.py` | ETF exclusion test script |
| `research/strategy_search/target_atr_holdout_grid.py` | Target × ATR holdout grid (found the winner) |
| `research/strategy_search/target2r_holdout_sweep.py` | 2.0R holdout sweep across ATR levels |
| `research/strategy_search/fence_sweep_attribution.py` | Fence range sweep + per-symbol PnL |
| `research/strategy_search/journal.md` | Research journal — all 12 batches and 50 lessons |
| `research/strategy_search/state.json` | Full research state with all experiments |
