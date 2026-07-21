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
