# OpenSniper — Trade Journal

## Key Metrics (Updated Weekly)
- Win rate: —
- Average hold time: —
- Average profit on winners: —
- Average loss on losers: —
- Profit factor: —
- Best time window: —

---

## Trade Log

### Trade 1 — SOL Long (LOSS)
- **Entry:** 2026-07-09 05:53 UTC | **Exit:** 2026-07-09 13:10 UTC
- **Hold time:** ~7 hours (VIOLATION — should be 10 min max)
- **Entry price:** $78.326 | **Exit price:** $77.984 | **PnL:** -$43.78 (-0.44%)
- **Opening range:** $77.72-$78.22 (30-min consolidation)
- **Breakout direction:** Long above range high
- **Volume surge:** 4.8x average (+380%)
- **Entry thesis:** SOL broke consolidation range with massive volume surge, all 4 criteria met
- **Exit reason:** TIME STOP — position held 7+ hours due to timeout command failure between cycles
- **Breakout score:** 7/12 (range tightness 2, volume surge 3, speed 1, gap alignment 1)
- **Phase:** Pre-market crypto (01:53 AM ET)
- **What worked:** Breakout identification was correct — SOL did push above range high with volume
- **What went wrong:** timeout command did not wait full 120 seconds, causing cycle gap. Position sat unmanaged for 7 hours. Auto SL/TP didn't trigger (price never hit $77.05 or $79.40)
- **Lesson:** Do NOT rely on Windows `timeout` command for cycle pacing. Use `Start-Sleep -Seconds 120` in PowerShell instead. Always verify position age every cycle. Time stop is non-negotiable.

### Trade 2 — ETH Long (LOSS)
- **Entry:** 2026-07-09 09:42 AM ET | **Exit:** 2026-07-09 09:45 AM ET
- **Hold time:** ~3 minutes (within 10-min limit ✓)
- **Entry price:** $1,746.75 | **Exit price:** $1,740.80 | **PnL:** -$34.10 (-0.34%)
- **Opening range:** $1,738-$1,743 (15-min consolidation)
- **Breakout direction:** Long above range high
- **Volume surge:** 1.1K avg at entry (strong), but collapsed 83% within 2 minutes
- **Entry thesis:** ETH broke range high $1,743 with 1.1K volume avg, US market open catalyzing
- **Exit reason:** Momentum dying — volume collapsed from 1.1K to 236 avg, price declining from $1,746 to $1,741, trend turning negative
- **Breakout score:** 6/12 (range tightness 2, volume surge 2, speed 1, gap alignment 1)
- **Phase:** Kill zone (09:42 AM ET)
- **What worked:** Exit discipline — cut at -0.34% before stop hit, recognized momentum death quickly
- **What went wrong:** Breakout was a false breakout — volume didn't sustain. The 1.1K volume was a spike, not sustained buying. Need to check if volume is building over multiple candles, not just one spike
- **Lesson:** Volume spike at breakout ≠ sustained volume. Require 2+ candles of elevated volume before entry, not just one surge candle. False breakouts common in low-volatility ranges (ETH range was only 0.57% of price)

### Trade 3 — SOL Long (LOSS)
- **Entry:** 2026-07-09 09:48 AM ET | **Exit:** 2026-07-09 09:53 AM ET
- **Hold time:** 5 minutes (within 10-min limit ✓)
- **Entry price:** $78.232 | **Exit price:** $78.194 | **PnL:** -$4.86 (-0.05%)
- **Opening range:** $77.72-$78.05 (consolidation)
- **Breakout direction:** Long above range high
- **Volume surge:** 1.1K avg at entry, sustained 3 candles (538→1.1K→3.4K), then collapsed to 1.1K
- **Entry thesis:** SOL broke range high with 2-candle volume confirmation (journal lesson applied)
- **Exit reason:** Momentum dying — volume collapsed 68% over 3 candles, price stuck at entry, no advance toward TP
- **Breakout score:** 7/12 (range tightness 2, volume surge 2, speed 2, gap alignment 1)
- **Phase:** Kill zone (09:48 AM ET)
- **What worked:** 2-candle volume confirmation was correct improvement. Exit at breakeven preserved capital.
- **What went wrong:** Volume sustained for 3 candles then faded — breakout still lacked follow-through. The issue isn't entry timing, it's that these ranges are too tight (0.4-0.6% of price) to generate meaningful breakouts. Need wider ranges or stronger catalysts.
- **Lesson:** Tight consolidation ranges (<0.7% of price) produce weak breakouts. Require range width > 1% of price for meaningful breakout trades. CIRCUIT BREAKER: 3 consecutive losses — cut size 50% and require score 8+/12 until breakeven.

## Circuit Breaker Status
- **3 consecutive losses** — ACTIVE
- Size reduced to 50% ($5,000 notional max)
- Entry score threshold raised to 8+/12
- Require range width > 1% of price
- Remain active until 1 winning trade restores confidence
