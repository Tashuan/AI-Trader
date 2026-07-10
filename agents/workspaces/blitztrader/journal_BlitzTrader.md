# BlitzTrader Trade Journal

## Lessons Learned
1. Volume spikes on 1h can be single-bar events — verify volume sustains for 2+ cycles before sizing up. My 50% sizing on BTC entry was correct.
2. 1h timeframe can be too narrow for structural read — NewsHound showed ETH 3x volume was accumulation (exchange outflows), not distribution. Daily chart context matters even for scalpers.
3. yfinance rate-limits frequently — always have web search (Tier 2) ready as fallback.
4. FadeMaster makes valid points about single-bar spikes — acknowledge good criticism, don't let ego override data.
5. ETF inflows ($265M BlackRock) are major momentum catalysts — watch for these as confirmation signals.
6. Require vol_ratio > 1.5x at ENTRY, not just "building." BTC entered at 1.09x vol = not a burst. Held 2 days = not a scalp. Both violated my strategy.
7. Volume explosion + short squeeze setup = high conviction. AVAX 2.50x vol entry → TP hit in 5 cycles. Auto TP/SL is essential for scalpers.
8. Platform requires market="us-stock" for equities, market="crypto" for crypto. Wrong market type = price fetch failure. Always use correct market type.
9. 6/6 signals with +7.68% 1h return = the move already happened. Entering after a massive spike is chasing, not scalping. Wait for pullback entry on volume explosion, not after price moved 7%+. Perfect score ≠ perfect timing.
10. Single-bar volume spikes (14.60x DOGE) can eventually translate to price recovery — but slowly (16+ cycles). Don't declare thesis "broken" too early, but don't size up either.
11. Patience is the edge in flat markets. 40+ cycles of range-bound action, all positions within 1.2% of TP but not breaking through. Then suddenly ALL 3 TPs hit simultaneously. Auto-TP + patience = wins.
12. Triple TP events happen! BTC, DOGE, ETH all hit TP in the same cycle after 40+ cycles of waiting. The market broke through the range and all positions closed at once. +$2,215 total profit in one cycle.

## Recent Trades (last 20)
<!-- Raw entries, oldest at top. When this section exceeds 20 entries, compact. -->

## 2026-07-07 BTC OPEN → CLOSED 2026-07-09 @ $62,268 (LOSS)
- Entry thesis: Vol_ratio 1.09 (building), RSI 54.1 (momentum zone), MACD hist +733 (strong bullish), above SMA20 & BB mid, +3.5% 5d return. 6/9 signals, weighted score 11/21. NewsHound & ChartMaster confluence.
- Position: 0.15 BTC at $63,633 (50% normal size — volume building not exploding)
- Exit: Sold at $62,268 (-2.07%) — HARD STOP triggered. Loss -$197.40.
- What worked: Stop discipline — cut at -2% with zero hesitation.
- What was wrong: Held too long (2 days for a scalper!). Volume was building not exploding at entry. Should have required vol_ratio > 1.5x at entry.
- Confidence: 6/9 signals at entry → outcome: LOSS. Calibration: overconfident on building (not exploding) volume.
- Lesson: Require volume EXPLOSION (>1.5x) at entry, not just building. A 1.09x vol_ratio is not a momentum burst.

## 2026-07-09 AVAX OPEN → CLOSED 2026-07-09 @ ~$6.66 (TP HIT! WIN!)
- Entry thesis: Vol_ratio 2.50x (EXPLOSION), RSI 69 (momentum zone), above SMA20, MACD hist +0.021 (positive). 4/6 buy signals + vol > 1.5x. Short squeeze setup — shorts dominate 100K-250K accounts at 33.6%. No cross-agent consensus (ghost town).
- Position: 523 AVAX at $6.5423 (~$3,417 = 10% portfolio)
- Exit: Auto-closed at $6.782 (TP triggered at $6.66, closed at market). AVAX 24h return accelerated from +1.41% to +5.02% over 5 cycles. Profit ~$125.
- What worked: Volume explosion entry (2.50x), short squeeze thesis confirmed, TP auto-close worked perfectly.
- What was right: AVAX was the only green crypto on watchlist at entry — momentum picked correctly.
- Confidence: 4/6 signals at entry → outcome: WIN (+2% TP hit). Calibration: accurate.
- Lesson: Volume explosion + short squeeze setup = high conviction. Auto TP/SL is essential for scalpers — I don't have to watch every tick.

## 2026-07-09 ETH OPEN → CLOSED 2026-07-09 @ $1,799.20 (TP+ EXCEEDED! WIN!)
- Entry: $1,735.43, 32.39 ETH (~$56,200 = large position from prior session)
- No auto-TP/SL set (API rejected retroactive add). Monitored manually for 82 cycles.
- Exit: Sold manually at $1,799.20 (+3.71%) when BTC/DOGE TPs hit and ETH was way past +2% target.
- Profit: +$2,066.39. ETH ran well past the +2% TP target ($1,770.14) to $1,799.80 before I sold.
- What worked: Patience. Held through 40+ cycles of range-bound action. ETH oscillated between +0.5% and +1.2% for 30+ cycles then broke through to +3.71%.
- What was wrong: No auto-TP set meant I had to monitor manually. Could have missed the spike if not cycling. Should always set TP/SL at entry.
- Confidence: Large position, no TP set → outcome: WIN (+3.71%). Calibration: patience paid off but risk was high without auto-TP.
- Lesson: Always set TP/SL at entry. Without auto-TP, ETH could have spiked and reversed before I noticed. Got lucky with timing.

## 2026-07-09 DOGE OPEN → CLOSED 2026-07-09 @ $0.07405 (TP HIT! WIN!)
- Entry thesis: Vol_ratio 14.60x (NUCLEAR EXPLOSION!), RSI 65.7 (momentum zone), above SMA20, MACD hist +0.0001 (positive). 4/6 buy signals + vol >> 1.5x. Price flat (-0.03% 1h) = accumulation before breakout.
- Position: 55,200 DOGE at $0.072595 (~$4,007 = 10% portfolio)
- Exit: Auto-closed at TP $0.07405 (+1.9%). Profit +$76.18.
- What worked: Auto-TP set at entry. Patience through 70+ cycles — DOGE volume crashed to 0.51x (single-bar event confirmed) but price slowly recovered and hit TP.
- Confidence: 4/6 signals at entry → outcome: WIN (+1.9% TP hit). Calibration: accurate despite single-bar volume risk.
- Lesson: Single-bar volume spikes CAN eventually translate to TP hits — but it takes 70+ cycles. Auto-TP is essential. Patience through the volume crash paid off.

## 2026-07-09 BTC RE-ENTRY → CLOSED 2026-07-09 @ $64,154 (TP HIT! WIN!)
- Entry thesis: Vol_ratio 2.43x (EXPLOSION), RSI 62.7 (momentum zone), above SMA20, MACD hist +78.61 (strongly positive). 4/6 buy signals + vol > 1.5x. Redemption trade — cut BTC at -2% ($62,268) earlier, now surging with REAL volume.
- Position: 0.063 BTC at $62,948 (~$3,960 = 10% portfolio)
- Exit: Auto-closed at TP $64,154 (+2%). Profit +$72.01.
- What worked: Volume explosion entry (2.43x), auto-TP set at entry, patience through 70+ cycles of range-bound action.
- Confidence: 4/6 signals at entry → outcome: WIN (+2% TP hit). Calibration: accurate.
- Lesson: Redemption trades work when you wait for REAL volume (2.43x vs original 1.09x). Auto-TP + patience = wins.

## 2026-07-09 AMD OPEN → CLOSED 2026-07-09 @ ~$546.08 (SL HIT — LOSS)
- Entry thesis: PERFECT 6/6 signals! Vol_ratio 1.90x (EXPLOSION), RSI 71.1 (momentum zone), above SMA20, MACD hist +2.73 (strong), 1h return +7.68% (MASSIVE), BB width 0.1543 (expanding). AI semiconductor rally catalyst (DeepSeek chip, SK Hynix 7x oversubscribed, Qualcomm AI factory). Nasdaq futures rising led by chip stocks.
- Position: 7.67 AMD at $557.33 (~$4,275 = 12% portfolio)
- Exit: Auto-closed at SL $546.08 (-2%). 24h return was +8-9% but intraday dip hit the stop. Loss ~$90.
- What worked: 6/6 signals were technically correct — volume increased through the dip (1.90x → 2.32x → 2.60x), confirming accumulation. Auto SL discipline worked.
- What was wrong: RSI 71 at entry was too close to overbought. The +7.68% 1h spike meant the easy money was already made — I entered at the top of the first leg. The dip to -1.61% recovered to breakeven but then dipped again to hit -2% SL. Should have waited for a pullback entry rather than chasing the spike.
- Confidence: 6/6 signals at entry → outcome: LOSS (-2% SL hit). Calibration: perfect score ≠ perfect entry timing. Volume signals were right but price entry was late.
- Lesson: 6/6 signals with +7.68% 1h return means the move already happened — entering after a massive spike is chasing, not momentum scalping. Wait for the next pullback to enter on a volume explosion, not after the price has already moved 7%+.
