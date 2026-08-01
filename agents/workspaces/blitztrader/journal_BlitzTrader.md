# BlitzTrader Trade Journal

## Lessons Learned (compacted — 15 sessions merged, 280+ cycles)
1. Volume explosion + 7+ momentum signals + OBV rising = high conviction blitz; 5-6 signals in bearish macro still risky.
2. OBV divergence is a HARD disqualifier — saved from DOGE, ETHFI, STX, VINE, POL, KAITO, SKR, ZORA, MERL, MEME, REZ traps.
3. OBV rising alone is NOT enough — price must move. Flat price + rising OBV = Rule 3 exit.
4. Volume spike without price movement = distribution/stall. Need volume + price velocity.
5. Low OI (<$500K) remains a hard skip — spread traps.
6. Stagnation timeout (Rule 3) is key for dead capital — 6+ flat cycles = exit.
7. Bearish macro (0/5 to 1/5 bullish) requires extreme selectivity — 5+ signals, 50% size, skip marginal setups.
8. Always set take_profit_price and stop_loss_price on every entry. Calculate from FILL price. ATR-based since Cycle 180.
9. Rule 5 (OB exhaustion) requires BOTH RSI > 75 AND volume dropping.
10. After-hours crypto volume spikes >1.5x can collapse without MACD/price confirmation. Require MACD >0 or 2+ consecutive high-volume bars; 3 consecutive losses triggers circuit breaker (50% size, 5+ signals from 2+ families).
11. Require 1h return >1% as MANDATORY signal — ADA lesson: 3.92x volume with +0.34% 1h return = distribution.
12. ATR-based TP/SL works well for scalping. Auto-close worker may not catch brief spikes — manual exit sometimes needed.
13. Volume collapse is the #1 killer of oversold bounces — LINK had RSI/MACD/Stochastic all improving but volume evaporated. Rule 4 saved from round-trip.
14. Bearish macro + volume collapse = death for momentum scalps. SOL 3.38x vol spike evaporated in 6 minutes. Even 7+ signals / 5 families have short half-lives in bearish macro.
15. Trailing stop-loss available on platform: `trailing_sl_pct` and `trailing_activation_pct` fields on POST /api/signals/realtime. Activates at specified profit %, then ratchets SL below peak.
16. After-hours crypto can go 20+ consecutive flat cycles. Don't force trades — no volume = no blitz.

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
- XRP: 12.2x vol → CLOSED +$24.29 (stagnation Rule 3). WIN (small).
- ANIME: 7.7x vol, OI $170K → CLOSED -$0.41 (Rule 3). LOSS (small).
- HBAR BLITZ #1: 4.3x vol, 5/7 signals → CLOSED +$1.32 (Rule 3). WIN (tiny).
- JTO BLITZ: 12.1x vol, 5/7 signals → CLOSED -$40.06 (Rule 3). LOSS.
- ENA BLITZ: 3x vol, 7/7 signals → CLOSED +$24.70 (Rule 3). WIN (small).
- XLM BLITZ: 14.1x vol, 6/7 signals → CLOSED -$34.30 (Rule 3). LOSS.
- TRX BLITZ: 4.8x vol, 6/7 signals → CLOSED -$6.14 (Rule 3). LOSS.
- BTC BLITZ #1: 1.93x vol, 8/9 signals → CLOSED -$9.48 (momentum death). LOSS.
- BTC RE-ENTRY: 2.27x→3.75x vol → CLOSED +$4.43 (stagnation). WIN (small).
- HBAR BLITZ #2: 2.89x vol, 6 cb_sigs → CLOSED +$67.76 (+2.73% TP). WIN.
- ETH BLITZ #1: 5.60x vol, 5 cb_sigs → CLOSED +$10.61 (manual). WIN.
- ADA BLITZ: 3.92x vol, 1h ret +0.34% → CLOSED -$53.44 (-2.16% SL). LOSS.
- LINK BLITZ: 3.58x vol, 5/5 signals, oversold bounce → CLOSED +$144.20 (+0.96% Rule 4 momentum death). WIN.
- ETH BLITZ #2: 1.6x vol, 7/8 signals → CLOSED +~$24 (+0.13% manual, re-entered with trailing). WIN (small).
- ETH BLITZ #3 (trailing): re-entered with trailing stop → CLOSED -$5.59 (-0.04% Rule 4). LOSS (flat).
- SOL BLITZ: 3.38x vol, 7/9 signals, consolidation breakout → CLOSED -$58.48 (-0.39% Rule 4). LOSS.
- Net all-time profit (sessions combined): ~+$553.63.

## Recent Entries (last 5)

## 2026-07-28 Cycle 52 — ETH RE-ENTRY with Trailing Stop-Loss! 🚀🔒
- **User directive:** "We added trailing, take profit, and stop loss functionality. Locate it and add to your position."
- **Found:** `POST /api/signals/realtime` accepts `trailing_sl_pct` and `trailing_activation_pct`. Platform worker handles trailing SL ratchet — activates at `trailing_activation_pct` profit, then ratchets SL `trailing_sl_pct` below peak. No PATCH for existing positions — must close and re-enter.
- **Action:** Closed ETH at $1,916.30 (signal 1272, small profit). Re-entered at $1,918.02 (signal 1273) with trailing.
- **POSITION: ETH | long | ENTRY: $1,918.02 | SL $1,892.49 (-1.33%) | TP $1,963.01 (+2.34%)**
  - trailing_sl_pct: 1.0%, trailing_activation_pct: 1.0% (activates at ~$1,937.20)
- **Docs updated:** API_REFERENCE.md, INSTRUCTIONS.md, PREFLIGHT.md

## 2026-07-28 Cycle 54 — SOL BLITZ ENTRY! User authorized 2nd position 🚀🚀
- **User directive:** "you can open another position if you want" — override of single-position model.
- **ETH POSITION:** entry $1,918.02, PnL +0.02%. HOLD, trailing not yet active.
- **SOL SETUP:** Score 14.87, 7/9 bullish, 5 families, vol_ratio 3.38x, RSI 67.3, MACD +0.12, 1h return +1.47%, full_body candle, consolidation breakout=True.
- **SOL ENTRY:** 200 SOL @ $74.415 fill (signal 1274). SL $73.59 (-1.08%), TP $76.00 (+2.16%). trailing_sl_pct: 1.0%, trailing_activation_pct: 1.0%.
- **PORTFOLIO:** ETH long 7.8 @ $1,918.02 + SOL long 200 @ $74.49. Both with trailing stops.

## 2026-07-28 Cycles 55-60 — ETH+SOL DOUBLE EXIT (Rule 4 Momentum Death) 🔴🔴
- **Cycles 55-59:** Both drifted red, SOL approaching SL (0.7% away at worst).
- **Cycle 60:** scan.py returned EXIT on BOTH — exit_reason: momentum_death. Volume collapsed below 0.5x simultaneously.
- **SOL CLOSE:** 200 SOL @ $74.197 (signal 1275). Entry $74.49. PnL: -$58.48 (-0.39%). Held ~6 min.
- **ETH CLOSE:** 7.8 ETH @ $1,917.30 (signal 1276). Entry $1,918.02. PnL: -$5.59 (-0.04%). Held ~15 min across re-entry.
- **TOTAL PnL: -$64.07**
- **Lesson:** Bearish macro + volume collapse = death for momentum scalps. Both setups looked great (7+ signals, 5 families, high vol) but volume evaporated within minutes. Rule 4 is the most important exit rule for scalpers.
- **Position verified:** NO OPEN POSITIONS ✅

## 2026-07-28 Cycle 61 (New Session) — Dead market, no setups, NO TRADE
- **Session restart:** 5:39 PM ET, US CLOSED, crypto OPEN. Macro: BEARISH (1/5, 20%).
- **scan.py:** 0 setups, 0 positions. US equities all closed/red.
- **Crypto MCP:** BTC $63,973 (-1.27%), ETH $1,922 (-1.03%), SOL $74.20 (-1.78%), DOGE $0.0708 (-1.39%), AVAX $6.62 (+0.73%). No volume bursts.
- **News:** Nasdaq-100 in correction, chip sell-off, Asian markets crashing (Kospi -10.8%).
- **Decision:** NO TRADE. No velocity anywhere.

## 2026-07-28 Cycles 62-80 — 20 flat after-hours cycles, NO TRADE (compacted)
- **Market:** Tuesday 5:46 PM – 6:58 PM ET, US CLOSED, crypto OPEN. Macro: BEARISH (1/5, 20%).
- **20 consecutive flat cycles.** BTC range $63,740–$63,970, slow bleed from $63,992 to $63,740 then bounce to $63,809. ETH $1,909–$1,925. SOL $73.40–$74.20. All minimal movement, no volume.
- **MEME trap avoided twice:** 9.8x → 16x volume spike but low OI (Lesson #5). INJ, AXS, GRASS, SPX unusual activity — none on watchlist.
- **API down Cycle 65:** Connection refused on localhost:8000. Retried Cycle 66, back up.
- **Cycle 80 SESSION CHECKPOINT:** 20 flat cycles, context large, recommend fresh session.
- **Decision:** NO TRADE × 20. Discipline wins. After-hours crypto stasis — no volume = no blitz.
