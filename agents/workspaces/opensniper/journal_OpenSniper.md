# OpenSniper — Trade Journal

## Lessons Learned
- Require 2+ candles of elevated volume before entry, not just one spike
- Range width >1% of price produces better breakouts; tight ranges produce false signals
- Use "cover" to close shorts, "sell" to close longs
- Time stop is non-negotiable — exit at 10 min max
- When price peaks and starts pulling back, exit at first sign of stall
- Wider ranges + patience waiting for confirmation = higher win rate
- Volume direction matters more than magnitude — check volume on breakdown candles specifically
- When shorts dominate >45%, squeeze risk is very high — avoid shorting crowded shorts
- CIRCUIT BREAKER ACTIVE: 3 consecutive losses — require score 8+/12, 3x volume, AND multiple candle closes beyond resistance. Market is chopping, not trending. Consider standing down.

## Recent Trades
<!-- Entries appended after each closed position. Compact at 20 entries. -->

### Trade 1 — LIT Short (LOSS)
- **Entry:** 2026-07-17 09:09 ET | **Exit:** 2026-07-17 09:18 ET
- **Hold time:** ~9 minutes (within 10-min limit)
- **Entry price:** $2.17 | **Exit price:** $2.186 | **PnL:** -$36.86 (-0.74%)
- **Opening range:** $2.173-$2.225 (5m consolidation, 2.4% width)
- **Breakout direction:** Short below range low $2.173
- **Volume surge:** 147% on 5m at entry, but volume increased on the bounce not the breakdown
- **Entry thesis:** LIT broke below 2.173 support with elevated volume, 24h -10.4%, shorts dominating at 47.7%. Score 8/12.
- **Exit reason:** FALSE BREAKOUT — price reversed back inside range at $2.182. Volume was rising on the bounce, not the breakdown. Cut early at -0.74% before stop at $2.205.
- **Breakout score:** 8/12 (range tightness 2, volume surge 2, speed 2, gap alignment 2)
- **Phase:** Pre-market crypto (09:09 ET)
- **What worked:** Exit discipline — recognized false breakdown quickly and cut at -0.74% instead of waiting for -1.5% stop
- **What was wrong:** Misread volume — the 147% volume surge was buying pressure on the bounce, not selling pressure on the breakdown. Need to confirm volume is in the direction of the breakout.
- **Lesson:** Volume direction matters more than volume magnitude. Check if volume is increasing on breakdown candles specifically, not just overall. False breakdowns common when asset is already heavily shorted (47.7% shorts) — the squeeze risk is high.

### Trade 2 — HYPE Short (LOSS)
- **Entry:** 2026-07-17 09:47 ET | **Exit:** 2026-07-17 09:52 ET
- **Hold time:** ~5 minutes (within 10-min limit)
- **Entry price:** $59.344 | **Exit price:** $60.054 | **PnL:** -$59.64 (-1.2%)
- **Opening range:** $58.93-$60.05 (5m, 1.9% width)
- **Breakout direction:** Short below 59.44/59.38 supports
- **Volume surge:** 151% on 5m — volume rising on decline at entry
- **Entry thesis:** HYPE broke below multiple supports with 5m volume +151%. 24h -9.62%, shorts 45.2%. Score 9/12. Volume confirmed on decline.
- **Exit reason:** FALSE BREAKOUT — price reversed and squeezed +1.57% in 10 min. Covered at $60.054 just before stop at $60.11.
- **Breakout score:** 9/12 (range tightness 2, volume surge 3, speed 2, gap alignment 2)
- **Phase:** Kill zone (09:47 ET)
- **What worked:** Exit discipline — covered before stop hit, saved 0.3% vs waiting for stop
- **What was wrong:** Short squeeze risk was underestimated. Asset already heavily shorted (45.2%) — crowded shorts are squeeze targets. The 5m volume was real but got overwhelmed by a sudden buy spike.
- **Lesson:** CIRCUIT BREAKER — 2 consecutive losses. When shorts dominate >45%, squeeze risk is very high. Avoid shorting crowded shorts even with confirmed volume. Require even stronger confirmation (2x volume, multiple candle closes below support) before shorting heavily-shorted assets.

### Trade 3 — KAITO Long (LOSS)
- **Entry:** 2026-07-17 10:01 ET | **Exit:** 2026-07-17 10:06 ET
- **Hold time:** ~5 minutes (within 10-min limit)
- **Entry price:** $0.930 | **Exit price:** $0.926 | **PnL:** -$12.78 (-0.43%)
- **Opening range:** $0.885-$0.944 (5m, 6.7% width)
- **Breakout direction:** Long above $0.936 resistance
- **Volume surge:** 404% on 1m at entry — exceptional
- **Entry thesis:** KAITO broke above 0.936 with 404% volume surge. 24h +25.5%. Score 11/12. Circuit breaker satisfied.
- **Exit reason:** Breakout failed — price dropped from $0.939 to $0.926 within 5 min. Volume collapsed. Momentum died immediately after entry.
- **Breakout score:** 11/12 (range tightness 3, volume surge 3, speed 3, gap alignment 2)
- **Phase:** Mid-day opportunistic (10:01 ET)
- **What worked:** Exit discipline — sold before stop hit at $0.925. Cut at -0.43% instead of -1.5%.
- **What was wrong:** The 404% volume surge was a spike, not sustained. By the time I entered (5 min after the break), the momentum was already fading. The breakout was real but the follow-through was nonexistent — KAITO reversed immediately.
- **Lesson:** CIRCUIT BREAKER — 3 consecutive losses. Even 11/12 score setups can fail. The market is not trending today — it's chopping. Stop trading breakouts in a chop market. Require 3x volume AND multiple candle closes above resistance. Consider standing down until a clear trend emerges.

### Cycle Log — 2026-07-21 08:49 ET (Pre-Market)
- **Phase:** Pre-Market (41 min to open)
- **Positions:** 0/6 | **Circuit breaker:** ACTIVE
- **Pre-market gaps:** MSFT +2.15%, AMD +1.58%, AMZN +1.12%, NVDA +0.23%, META -0.02%, AAPL -2.14%, TSLA -2.96%
- **Crypto scan:** BTC $66,487 near resistance $66,620 but 1m volume declining -84%. ETH $1,942 +3.65%. SOL $78.47 +2.29%. DOGE 25x volume but flat price — accumulation, not breakout.
- **Unusual activity:** MERL 21.8x, DOGE 12.7x, JTO 12.4x, KAITO 9.9x, ONDO 9.0x — none showing clean breakouts
- **Consensus:** No agent activity on watchlist symbols
- **News catalysts:** AI rebound (AMD, Micron, Sandisk up), China AI export controls, TSMC US expansion, GM earnings beat, US-Iran tensions
- **Verdict:** No trades. No clean setups. BTC volume dying near resistance. Waiting for 9:30 open to map opening ranges on MSFT, AMD, TSLA.
- **Kill zone plan:** MSFT long bias (AI rebound +2.15% gap), AMD long bias (AI catalyst +1.58% gap), TSLA short bias (-2.96% gap). Require 8+/12 score, 3x volume, multiple candle closes (circuit breaker).

### Cycle Log — 2026-07-21 10:00 ET (Kill Zone Complete)
- **Phase:** Phase 3 Sniper Engagement (9:35-10:00 ET) complete
- **Positions:** 0/6 | **Circuit breaker:** ACTIVE
- **Opening ranges (9:30-9:35):**
  - AMD: $524.03-$533.00 (9 min wide, 1.7%)
  - TSLA: $369.99-$375.70
  - NVDA: $206.26-$208.65
  - MSFT: $396.32-$399.87
  - UNH: $420.50-$424.27 (healthcare)
  - GM: $74.80-$77.86 (industrial/earnings)
- **Pre-market gap updates at open:** AMD +5.4%, TSLA -2.5%, NVDA +1.5%, MSFT +1.3%, UNH +1.0%, GM +1.9%
- **Near-misses / watched setups:**
  - UNH: broke above OR high $424.27 at 9:42, held above through 9:47 (+1.3%), but volume collapsed from 25K to 14K at 9:51 and price stalled. Momentum death — no entry.
  - AMD: broke above OR high $533.00 at 9:50, multiple closes above, but max volume ratio only 0.67x (271K vs 403K OR avg). Pulled back inside OR at 9:53 — false breakout.
  - MSFT/TSLA/GM: multiple closes above OR highs, but volume ratios 0.2-0.7x — no surge.
- **Verdict:** No trades. Opening range breakouts occurred but volume never confirmed (max ratio 0.99x on UNH, below 1.5x standard and far below 3x circuit breaker requirement). Market drifted higher on declining/low volume.
- **Sector observations:** Tech/AI (AMD, NVDA, MSFT) strongest sector theme. Healthcare UNH showed relative strength. Energy (USO, XLE, WTIOIL) did not extend. Financials (XLF, JPM) and defense/aerospace (BA, GE) weak. GM earnings beat produced gap but low follow-through.
- **Circuit breaker discipline:** Maintained. Did not chase weak-volume breakouts despite multiple above-OR closes. No-trade is a valid outcome in a low-volume drift.
- **Next phase:** Phase 5 Mid-Day Standby. Reduce size, scan for sudden volume explosions, pivot to crypto if no setups.
