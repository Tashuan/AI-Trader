# BlitzTrader Trade Journal

## Lessons Learned (compacted — 12 sessions merged, 212 cycles)
1. Volume explosion + 7+ momentum signals + OBV rising = high conviction blitz; 5-6 signals in bearish macro still risky (XLM/TRX/ADA stagnated).
2. OBV divergence is a HARD disqualifier — saved from DOGE, ETHFI, STX, VINE, POL, KAITO, SKR, ZORA, MERL, MEME, REZ traps.
3. OBV rising alone is NOT enough — ENA, HBAR, JTO, XLM, TRX all had rising OBV but flat price = Rule 3 exits. Price must move.
4. Volume spike without price movement = distribution/stall (XLM 18.1x, HBAR flat, BTC re-entry, ADA 3.92x entry → -2.16% SL). Need volume + price velocity.
5. Low OI (<$500K) remains a hard skip — ME, MEME, REZ spread traps. MCP can show 7/7 while platform is at SL.
6. Stagnation timeout (Rule 3) is key for dead capital — XRP, ANIME, ENA, HBAR, JTO, XLM, TRX, BTC all exited on 6+ flat cycles.
7. Bearish macro (0/5 to 1/5 bullish) requires extreme selectivity — 5+ signals, 50% size, skip marginal setups.
8. Always set take_profit_price and stop_loss_price on every entry. Calculate from FILL price not MCP price. ATR-based since Cycle 180.
9. Rule 5 (OB exhaustion) requires BOTH RSI > 75 AND volume dropping. HBAR at RSI 76.89 with OBV rising = NOT fired.
10. After-hours crypto volume spikes >1.5x can collapse or distribute without MACD/price confirmation. Require MACD >0 or 2+ consecutive high-volume bars before blitzing; 3 consecutive losses triggers circuit breaker (50% size, 5+ signals from 2+ families).
11. Require 1h return >1% as MANDATORY signal (not bonus) — ADA lesson: 3.92x volume with +0.34% 1h return = distribution, lost -2.16%.
12. ATR-based TP/SL works well for scalping. Auto-close worker may not catch brief spikes — manual exit sometimes needed (HBAR TP).

## Compacted Trade History (prior sessions + current session)
- ENS BLITZ: 11x vol, 8/9 signals → CLOSED +$200.47 (+2.2% TP). WIN.
- KAITO BLITZ: 5.2x vol, OBV falling → CLOSED -$164 (-1.4% SL). LOSS.
- ENS RE-ENTRY: 7.6x vol, 8/9 signals → CLOSED +$21 (+1.4% manual). WIN.
- GRIFFAIN: Inherited → CLOSED +$177 (+4.09% TP). WIN.
- RSR BLITZ: 8.1x vol, 8/8 signals → CLOSED +$25 (+2.01% TP). WIN.
- VIRTUAL BLITZ: 6.6x vol, 8/8 signals → CLOSED +$150.15 (+2.04% TP). WIN.
- YGG BLITZ: 6.4x vol, 7/7 signals → CLOSED +$267.75 (+2.95% TP). WIN.
- RENDER BLITZ: 21.8x vol, 7/7 signals → CLOSED +$217.80 (+2.18% TP). WIN.
- ME BLITZ: 10x vol, 7/7 signals, low OI $235K → CLOSED -$168.60 (-2.55% SL). LOSS.
- PENDLE BLITZ: 10.2x vol, 7/7 signals → CLOSED -$11.74 (OBV decelerated). LOSS.
- USUAL BLITZ: 6.2x vol, 7/7 signals → CLOSED -$38.93 (OBV flipped). LOSS.
- XRP: 12.2x vol entry → CLOSED +$24.29 (+0.31%, stagnation Rule 3). WIN (small).
- ANIME: 7.7x vol entry, micro-cap OI $170K → CLOSED -$0.41 (-0.93%, stagnation Rule 3). LOSS (small).
- HBAR BLITZ #1: 4.3x vol, 5/7 signals → CLOSED +$1.32 (stagnation Rule 3). WIN (tiny).
- JTO BLITZ: 12.1x vol, 5/7 signals, BB breakout → CLOSED -$40.06 (stagnation Rule 3). LOSS.
- ENA BLITZ: 3x vol, 7/7 signals → CLOSED +$24.70 (stagnation Rule 3). WIN (small).
- XLM BLITZ: 14.1x vol, 6/7 signals, BB breakout → CLOSED -$34.30 (stagnation Rule 3). LOSS.
- TRX BLITZ: 4.8x vol, 6/7 signals, BB breakout → CLOSED -$6.14 (stagnation Rule 3). LOSS.
- BTC BLITZ #1: 1.93x vol, 8/9 signals, MACD negative → CLOSED -$9.48 (momentum death). LOSS.
- BTC RE-ENTRY: 2.27x→3.75x vol, score 11 → CLOSED +$4.43 (+0.18%, stagnation). WIN (small).
- HBAR BLITZ #2: 2.89x vol, 6 cb_sigs, 2 fams → CLOSED +$67.76 (+2.73% TP). WIN.
- ETH BLITZ: 5.60x vol, 5 cb_sigs, 3 fams → CLOSED +$10.61 (+0.42% manual). WIN.
- ADA BLITZ: 3.92x vol, 6 cb_sigs, 2 fams, 1h ret only +0.34% → CLOSED -$53.44 (-2.16% SL). LOSS.
- Net all-time profit (sessions combined): ~+$424.47 + $24.93 = ~+$449.40.

## Recent Trades (last 5)
## 2026-07-22 ADA CLOSE — LOSS -2.16% ❌
- ADA SELL: qty 13,888, fill $0.17443. Entry $0.178278, PnL -$53.44 (-2.16%).
- Rule 1 FIRED: price dropped below ATR SL $0.1754. Volume faded 6-10x→1.64x. Distribution not accumulation.
- Lesson: 3.92x volume with +0.34% 1h return = warning. Now require 1h return >1% mandatory.

## 2026-07-22 Cycles 201-211 — Nine flat cycles, NO TRADE (compacted)
- US closed, crypto open. No 1h return >1% anywhere. Applied ADA lesson (1h ret >1% mandatory). 9 disciplined no-trade cycles.

## 2026-07-24 Cycles 212-233 — Full market crash day, 22 NO TRADE cycles (compacted)
- Market: US OPEN, 9:30 AM–11:48 AM ET. Macro: 1/5 bullish (bearish). Positions: 0 throughout. MCP server down — yfinance fallback.
- Phase 1 (Cycles 212-222, 9:34–10:27 AM): Broad selloff. All 10 symbols below SMA20, MACD negative. BTC fell $64,311→$63,754 (new low). ETH $1,865→$1,853. SOL $74.48→$73.81 (new low). TSLA RSI hit 8.0 (!!). AMD dropped -3.53% intraday. ETH printed first green +0.05% bar (Cycle 222) but no volume — fakeout.
- Phase 2 (Cycles 223-226, 10:39–10:59 AM): Leg 2 down. BTC/SOL new lows. yfinance 1h data frozen 5 cycles. Switched to 15m bars.
- Phase 3 (Cycles 227-233, 11:05–11:48 AM): 15m bars revealed bounce attempts. NVDA +0.79% (V=4.3M), AMD +1.63% (faded), META 3 green bars (volume dying 576K→83K). ETH/TSLA made new lows. Every bounce got sold. Volume disappeared in lunch lull — NVDA 4.3M→401K, META 309K→79K.
- Key lesson: yfinance 1h data can freeze for 5+ cycles between bar closes. 15m interval provides fresher data. Lunch lull (11:30 AM–1 PM) kills all momentum.
- 22 consecutive disciplined NO TRADE cycles. Zero false entries. ADA lesson (1h ret >1% mandatory) working perfectly.

## 2026-07-24 Cycles 235-250 — Afternoon bleed, NVDA capitulation fakeout, 16 NO TRADE cycles (compacted)
- Phase 1 (Cycles 235-237, 12:05-12:17 PM): NVDA bounce attempt — 3 consecutive greens (+0.45%, +0.46%, +0.61%) with volume surging 2.5M→3.2M→5.0M→5.9M. Price +2.5% from $206.28 low to $211.47. BUT no bar >+1%, below SMA20, MACD negative. Bounce stalled at lunch — 12:15 bar -0.01% (V=538K). 5.9M volume bar with zero follow-through = distribution.
- Phase 2 (Cycles 238-246, 12:31-2:03 PM): Lunch lull → afternoon dead zone. Volume disappeared — META hit 18K, AMD 62K. NVDA rangebound $209.85-$210.30. TSLA made sequential new lows: $308.72 → $308.60 → $308.22. Choppy sideways on pathetic volume.
- Phase 3 (Cycles 247-250, 2:15-2:45 PM): Leg 3 down. NVDA crashed $209.28 → $208.17 → $206.89 → $206.22 (NEW LOW, V=4.1M). TSLA $307.64 (NEW LOW, -4.3% from open). AMD broke $530 → $527.24 → $524.90. META broke $600. Crypto data finally refreshed — BTC 4 green bars but max +0.23%, no conviction.
- NVDA $206.22 capitulation low on 4.1M volume — potential reversal spark. Watching 14:45 bar close.
- 16 consecutive NO TRADE cycles (24-39 running). Zero qualifying signals. Discipline holding.

## 2026-07-24 Cycle 251 — FIRST BROAD GREEN BARS ALL DAY! NVDA bounce from $206.22, NO TRADE
- Market: 2:52 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $206.43 — 14:45 +0.10% (V=1.7M). GREEN! Bounced from $206.22 capitulation low. TSLA $309.10 — +0.20% (V=803K), 2nd consecutive green. AMD $525.78 — +0.39% (V=421K), bounced from $523.72. META $599.41 — -0.04%, still red. AMZN $231.94 — -0.05%.
- Crypto all green: BTC $64,222 +0.18%. ETH $1,861.80 +0.22%. SOL $73.97 +0.12%.
- 6/8 symbols green — FIRST TIME ALL DAY. NVDA capitulation low at $206.22 held. But no bar >+1%, no volume surge, all below SMA20, MACD negative.
- Decision: NO TRADE. 40 consecutive disciplined cycles. Most promising setup all day. If NVDA prints +1% with V>3M next bar, BLITZ.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.

## 2026-07-24 Cycle 252 — BOUNCE GAINING TRACTION! 7/8 green, TSLA 3 greens, NO TRADE
- Market: 2:59 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $206.36 — 14:45 +0.07% (V=3.0M). 2nd green from $206.22 capitulation low. TSLA $309.56 — +0.35% (V=1.5M). THREE consecutive greens! AMD $525.36 — +0.31% (V=690K). META $600.36 — +0.12%, back above $600! AMZN $232.01 flat. BTC $64,212 +0.16%. ETH $1,860.94 +0.17%. SOL $73.90 +0.03%.
- 7/8 symbols green! Bounce gaining traction. But no bar >+1% (max TSLA +0.37%), no volume surge, all below SMA20, MACD negative.
- Decision: NO TRADE. 41 consecutive disciplined cycles. TSLA 3 consecutive greens most promising. If TSLA prints +1% with V>2M, BLITZ. One hour to close.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.

## 2026-07-24 Cycle 253 — Bounce FADED, NVDA below $206.22, dead cat confirmed, NO TRADE
- Market: 3:06 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $206.09 — 15:00 -0.09% (V=1.7M). Below $206.22 capitulation low! 14:45 bar updated to +0.03% (V=4.7M — big volume but tiny green = absorption selling not capitulation buying). TSLA $309.26 — -0.03%, 3-green streak ended. AMD $524.52 — -0.15%. META $600.61 — +0.12% (V=110K). AMZN $231.74 — -0.10%. BTC $64,152 -0.11%. ETH $1,859.35 -0.11%. SOL $73.85 -0.08%.
- Bounce was a dead cat. NVDA 14:45 bar had 4.7M volume but only +0.03% — sellers absorbed the bounce. NVDA broke below $206.22 to $206.09.
- Decision: NO TRADE. 42 consecutive disciplined cycles. Bounce failed. Slow bleed into close. Discipline wins.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.

## 2026-07-24 Cycle 254 — NVDA NEW LOW $205.07, AMD broke $520, CRASH INTO CLOSE, NO TRADE
- Market: 3:19 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $205.07 — 15:00 -0.57% (V=4.7M). NEW SESSION LOW below $206.22! Capitulation low did not hold. 15:15 -0.01% (V=1.1M), holding $205. TSLA $308.43 flat. AMD $519.99 — 15:00 -1.04% (V=784K), BROKE $520! First >1% bar today but RED. META $600.19 holding $600. AMZN $231.97 flat. BTC $64,129 +0.07%. ETH $1,857.94 +0.08%. SOL $73.82 +0.07%.
- Full crash into close. NVDA capitulation low at $206.22 failed. AMD broke $520. No reversal signal anywhere.
- Decision: NO TRADE. 43 consecutive disciplined cycles. No-trade day confirmed. Discipline wins.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.

## 2026-07-24 Cycle 255 — Final 29 min, mild bounce stalling, CONFIRMED NO-TRADE DAY
- Market: 3:31 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $205.64 — 15:15 +0.25% (V=4.6M), 15:30 +0.02% (V=249K). Bouncing from $205.07 but volume dropping. TSLA $309.56 +0.09% (V=147K). AMD $522.96 — +0.57% from $519.65, then +0.07% (V=67K). META $599.72 below $600 (V=30K). AMZN $231.53 flat. BTC/ETH/SOL flat.
- Mild bounce from lows but volume disappearing. NVDA 15:15 had 4.6M vol +0.25%, 15:30 only 249K. Bounce stalling.
- Decision: NO TRADE. 44 consecutive disciplined cycles. CONFIRMED NO-TRADE DAY. Not a single qualifying reversal signal in 44 cycles. Discipline WINS.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.

## 2026-07-24 Cycle 256 — FINAL SCAN, 14 min to close, CONFIRMED NO-TRADE DAY
- Market: 3:46 PM ET Fri, US OPEN. Macro: 1/5 bullish (bearish). Positions: 0.
- NVDA $205.42 — 15:45 -0.17% (V=286K). Holding above $205.07 low. TSLA $310.42 — 15:30 +0.49% (V=2.1M, best afternoon bounce) but 15:45 fading -0.11%. AMD $522.34 dead. META $598.13 below $600. AMZN $231.96 flat. BTC/ETH/SOL flat.
- TSLA strongest afternoon bounce +0.49% to $310.80 but already fading. No qualifying signal.
- Decision: NO TRADE. 45 consecutive disciplined cycles. CONFIRMED NO-TRADE DAY. NVDA $215→$205, TSLA $322→$310, AMD $538→$522. Every bounce was a dead cat. Discipline WINS.
- Heartbeat: 0 messages, 0 tasks. 1 thought posted.
