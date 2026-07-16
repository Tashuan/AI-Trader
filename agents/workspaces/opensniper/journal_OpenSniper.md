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

### Trade 4 — LIT Short (LOSS)
- **Entry:** 2026-07-10 21:26 ET | **Exit:** 2026-07-10 21:49 ET
- **Hold time:** ~23 minutes (VIOLATION — server outage prevented management)
- **Entry price:** $2.6571 | **Exit price:** $2.6591 (cover) + $2.6605 (accidental long sell)
- **PnL:** -$36.58 (-0.04%)
- **Opening range:** $2.682-$2.711 (60-min consolidation, 1.08% width)
- **Breakout direction:** Short below range low $2.682
- **Volume surge:** 5.87x sustained over 3 cycles
- **Entry thesis:** LIT broke below 60-min range low with sustained volume, score 8/12
- **Exit reason:** TIME EXIT — server went down at ~8 min hold, position sat unmanaged for 30+ min. Used "buy" instead of "cover" to close short, accidentally opening a long. Fixed by covering short then selling long.
- **Breakout score:** 8/12 (range tightness 2, volume surge 3, speed 2, gap alignment 1)
- **Phase:** Opportunistic crypto (21:26 ET Friday)
- **What worked:** Breakout identification correct — LIT did drop to $2.6364 (-0.79% from entry) before bouncing
- **What went wrong:** Server outage prevented exit at profit. Used wrong action ("buy" instead of "cover") to close short — opened accidental long. Need to remember: cover closes shorts, sell closes longs.
- **Lesson:** Use "cover" to close shorts, "sell" to close longs. Server outages happen — auto SL/TP is critical. Position was profitable at $2.6364 but I couldn't exit.

### Trade 5 — MON Long (WIN)
- **Entry:** 2026-07-10 21:51 ET | **Exit:** 2026-07-10 21:57 ET
- **Hold time:** ~6 minutes (within 10-min limit ✓)
- **Entry price:** $0.02390 | **Exit price:** $0.023968 | **PnL:** +$14.25 (+0.28%)
- **Opening range:** $0.02294-$0.02383 (60-min consolidation, 3.88% width)
- **Breakout direction:** Long above range high $0.02383
- **Volume surge:** 5.04x sustained over 2 cycles
- **Entry thesis:** MON broke range high with 5x volume, score 9/12. Clean breakout with sustained volume.
- **Exit reason:** End of session — user requested close before shutdown. Position was in profit.
- **Breakout score:** 9/12 (range tightness 2, volume surge 3, speed 3, gap alignment 1)
- **Phase:** Opportunistic crypto (21:51 ET Friday)
- **What worked:** Patience — waited for breakout confirmation across 2 cycles before entering. Volume sustained. Range width >1% produced a meaningful move.
- **What was wrong:** Exit was forced by session end, not by TP hit. Position was only +0.28% vs +2% target. Would have held longer if session continued.
- **Lesson:** Wider ranges (3.88%) DO produce better breakouts. Patience waiting for confirmation pays off. First win after 4 losses — circuit breaker lifted.

### Trade 6 — ZEC Long (WIN)
- **Entry:** 2026-07-12 12:01 ET | **Exit:** 2026-07-12 12:09 ET
- **Hold time:** ~8 minutes (within 10-min limit ✓)
- **Entry price:** $543.05 | **Exit price:** $545.42 | **PnL:** +$42.66 (+0.44%)
- **Opening range:** $530-$540.23 (60-min consolidation, 1.92% width)
- **Breakout direction:** Long above range high $540.23
- **Volume surge:** 36.5x at breakout, sustained 2x+ across 4 cycles
- **Entry thesis:** ZEC broke range high with explosive 36x volume, score 10/12. 24h +5.24% momentum alignment.
- **Exit reason:** Momentum stalling — price peaked at $549.61 then pulled back to $545. Time stop approaching. Locked in profit rather than letting winner turn loser.
- **Breakout score:** 10/12 (range tightness 2, volume surge 3, speed 3, gap alignment 2)
- **Phase:** Opportunistic crypto (12:01 PM ET Sunday)
- **What worked:** Patience waiting for breakout confirmation across 2 cycles. Volume was genuinely explosive (36x). Exit discipline — locked in profit when momentum stalled.
- **What was wrong:** Didn't ride to TP $553.93. Price peaked at $549.61 then faded. Could have exited at the peak for +1.2% instead of +0.44%.
- **Lesson:** When price peaks and starts pulling back after a strong run, consider exiting at the first sign of stall rather than waiting for time stop. Two wins in a row — momentum is building.

## Circuit Breaker Status
- **LIFTED** — Two consecutive wins (Trades 5 and 6). Full confidence restored.
- Normal size: $10,000 notional max
- Normal score threshold: 6+/12
- Range width >1% still recommended based on Trade 3 lesson
