# NightHawk Trade Journal

One line per entry and exit. Do not batch — write immediately after execution, same cycle.

Format:
`[timestamp] [session] [ENTRY/EXIT] [symbol] [direction] [size%] — signals: [...] — confidence: [0-1] — thesis: [one sentence]`

---

<!-- Entries append below this line. Do not edit or remove prior entries — this is an audit trail. -->

[2026-07-22T00:17:15Z] [Tokyo] [ENTRY] [BTC] [LONG] [12%] — signals: [BB_breakout, RSI_64, SMA50_trend, positive_returns_1d3d7d, vol_above_avg, news_bullish, crowd_bullish, ATR_safe] — confidence: 0.85 — thesis: BTC broke above BB upper band with bullish RSI momentum and volume confirmation during Tokyo active session, 8+ signals well above 4+ threshold.

[2026-07-22T00:17:23Z] [Tokyo] [ENTRY] [ETH] [LONG] [10%] — signals: [RSI_68_momentum, SMA50_trend, 3d_return_+4%, vol_1.2x_avg, news_headline_bullish] — confidence: 0.70 — thesis: ETH breaking above $1,900 with strong 3-day momentum and volume confirmation, fundamental backing from top news headline, 5 signals meet Tokyo 4+ threshold. NOTE: executed on wrong DB (ai4trade.ai production) — voided.

[2026-07-22T00:33:12Z] [Tokyo] [ENTRY] [BTC] [LONG] [12%] — signals: [BB_breakout, RSI_64, SMA50_trend, positive_returns_1d3d7d, vol_above_avg, news_bullish, crowd_bullish, ATR_safe] — confidence: 0.85 — thesis: BTC broke above BB upper band with bullish RSI momentum and volume confirmation during Tokyo active session, 8+ signals. Executed on localhost:8000 (correct DB). Entry $66,699.63.

[2026-07-22T00:33:25Z] [Tokyo] [ENTRY] [ETH] [LONG] [10%] — signals: [RSI_68_momentum, SMA50_trend, 3d_return_+4%, vol_1.2x_avg, news_headline_bullish] — confidence: 0.70 — thesis: ETH breaking above $1,900 with strong 3-day momentum and volume confirmation, 5 signals meet Tokyo 4+ threshold. Executed on localhost:8000 (correct DB). Entry $1,940.44.

[2026-07-22T00:53:00Z] [Tokyo] [EXIT] [BTC] [LONG] [12%] — signals: [stagnation_timeout] — confidence: N/A — thesis: BTC position did not move +/-0.5% in 20 min during non-kill-zone Tokyo session. Closed flat at $66,601. Loss ~$18 on slippage.

[2026-07-22T00:53:15Z] [Tokyo] [EXIT] [ETH] [LONG] [10%] — signals: [stagnation_timeout] — confidence: N/A — thesis: ETH position did not move +/-0.5% in 20 min during non-kill-zone Tokyo session. Closed flat at $1,938.40. Loss ~$10 on slippage.

[2026-07-22T01:33:00Z] [Tokyo] [ENTRY] [BTC] [LONG] [12%] — signals: [RSI_23.9_oversold, vol_spike_3.06x, 15m_reversal_positive, ATR_safe_1.00] — confidence: 0.75 — thesis: BTC deeply oversold RSI 23.9 with 3.06x volume spike and 15m momentum turning positive — mean reversion bounce play. 4 signals meet Tokyo 4+ threshold. Entry $66,424.36. TP $67,338 (+1.5%), SL $65,348 (-1.5%).

[2026-07-22T01:53:00Z] [Tokyo] [EXIT] [BTC] [LONG] [12%] — signals: [stagnation_timeout] — confidence: N/A — thesis: BTC position did not move +/-0.5% in 20 min during non-kill-zone Tokyo session. Bounce thesis failed — RSI stayed oversold but no follow-through buying. Volume spike was a false signal. Closed flat at $66,404. Loss ~$3.67.

[2026-07-22T02:18:00Z] [Tokyo] [ENTRY] [SOL] [LONG] [10%] — signals: [vol_spike_2.04x, 15m_positive_+0.13%, 1h_positive_+0.19%, ATR_safe_1.09] — confidence: 0.70 — thesis: SOL volume spike 2.04x with 15m and 1h momentum turning positive after oversold slide. RSI 47.9 recovering. 4 signals meet Tokyo 4+ threshold. Entry $78.50. TP $79.68 (+1.5%), SL $77.32 (-1.5%).

[2026-07-22T02:53:00Z] [Tokyo] [EXIT] [SOL] [LONG] [10%] — signals: [stagnation_timeout] — confidence: N/A — thesis: SOL position did not move +/-0.5% in 20+ min during non-kill-zone Tokyo session. Entry $78.50, closed at $78.128. Loss ~$47 on slippage. Dead trade — no follow-through after volume spike faded.

[2026-07-22T02:55:00Z] [Tokyo] [ENTRY] [BTC] [LONG] [10%] — signals: [price_above_SMA20, price_above_SMA50, price_above_SMA200, price_above_VWAP, OBV_rising, RSI_57_neutral_bullish] — confidence: 0.72 — thesis: BTC trend structurally bullish — above all major MAs and VWAP with OBV accumulation. MACD histogram negative but line still positive, momentum fading not reversing. 6 signals exceed Tokyo 4+ threshold. Entry $66,316. TP $67,311 (+1.5%), SL $65,321 (-1.5%).

[2026-07-22T02:55:00Z] [Tokyo] [ENTRY] [ETH] [LONG] [10%] — signals: [price_above_SMA20, price_above_SMA50, price_above_SMA200, price_above_VWAP, OBV_rising, Stoch_K>D_bullish, RSI_57.8_neutral_bullish] — confidence: 0.70 — thesis: ETH 7 confirming signals — above all MAs, VWAP, OBV rising, Stochastic bullish cross. Trend intact despite MACD deceleration. 7 signals exceed Tokyo 4+ threshold. Entry $1,930.50. TP $1,959.46 (+1.5%), SL $1,901.54 (-1.5%).

[2026-07-22T03:58:00Z] [Tokyo] [EXIT] [BTC] [LONG] [10%] — signals: [stagnation_timeout] — confidence: N/A — thesis: BTC did not move +/-0.5% in 60+ min during non-kill-zone Tokyo session. Entry $66,382, closed at $66,179. Trend deteriorating — price below SMA20, Stoch bearish cross, MACD histogram worsening. Loss ~$30.

[2026-07-22T03:58:00Z] [Tokyo] [EXIT] [ETH] [LONG] [10%] — signals: [stagnation_timeout] — confidence: N/A — thesis: ETH did not move +/-0.5% in 60+ min during non-kill-zone Tokyo session. Entry $1,932.43, closed at $1,929.40. OBV accumulation dropped 52%, price below SMA20. Loss ~$16.

[2026-07-22T03:58:30Z] [Tokyo] [NO_ENTRY] [ALL] — signals: [BB_squeeze_all_assets, no_crypto_catalysts, Tokyo_session_winding_down] — confidence: N/A — thesis: Bollinger bandwidths extremely tight across BTC/ETH/SOL/DOGE (0.0076-0.0154) — classic squeeze, no directional conviction. No crypto news catalysts. Tokyo session ending, London kill zone ~3h away. The disciplined predator waits for the kill zone. No new entries this cycle.

[2026-07-22T09:35:00Z] [London Morning] [ENTRY] [SOL] [SHORT] [10%] — signals: [below_SMA20, below_EMA20, below_SMA50, below_EMA50, below_VWAP, MACD_line_negative, MACD_histogram_negative, OBV_falling] — confidence: 0.78 — thesis: SOL 8 bearish signals — price below all short-term MAs and VWAP, MACD line crossed negative, OBV distribution accelerating. RSI 43.35 bearish. Only support is SMA200 at $76.46. 8 signals exceed London Morning 4+ threshold. Entry $77.312. TP $76.13 (-1.5%), SL $78.45 (+1.5%).

[2026-07-22T09:35:00Z] [London Morning] [ENTRY] [ETH] [SHORT] [10%] — signals: [below_SMA20, below_EMA20, below_VWAP, MACD_histogram_negative, OBV_falling, Stoch_K<D] — confidence: 0.72 — thesis: ETH 6 bearish signals — price below SMA20/EMA20/VWAP, MACD histogram deeply negative at -2.57, OBV falling with 25k dump, Stochastic bearish. RSI 48.9 rolling over. 6 signals exceed London Morning 4+ threshold. Entry $1,920.60. TP $1,892.12 (-1.5%), SL $1,949.82 (+1.5%).

[2026-07-22T10:29:00Z] [London Morning] [EXIT] [ETH] [SHORT] [10%] — signals: [thesis_broken] — confidence: N/A — thesis: ETH bearish case weakening — RSI bounced above 50 (48.9->51.0), Stochastic bullish cross forming (K 27.5 > D 19.6), price recovering toward SMA20, OBV decelerating. 6 bearish signals deteriorating. Cut loss before it grows. Entry $1,918.68, covered at $1,927.70. Loss ~$47.

[2026-07-22T10:29:30Z] [London Morning] [HOLD] [SOL] [SHORT] [10%] — signals: [below_SMA20, below_EMA20, below_SMA50, below_EMA50, below_VWAP, MACD_line_negative, MACD_histogram_negative, OBV_falling] — confidence: 0.75 — thesis: SOL short thesis intact — 8 bearish signals unchanged. Price $77.51 still below all short-term MAs and VWAP. MACD line negative, OBV falling -301k. Small adverse move (+0.36%) is noise within bearish structure. Holding, TP $76.13, SL $78.45.

[2026-07-22T10:32:00Z] [London Morning] [EXIT] [SOL] [SHORT] [10%] — signals: [stagnation_timeout] — confidence: N/A — thesis: SOL short did not move -0.5% in our favor in 55+ min. Price drifted +0.44% against to 15m BB upper resistance. 15m MACD histogram turned positive, Stoch overbought 90 — short-term momentum reversed against 1h bearish structure. Dead trade. Entry $77.23, covered at $77.532. Loss ~$38.

[2026-07-22T10:33:00Z] [London Morning] [NO_ENTRY] [ALL] — signals: [BB_squeeze_extreme_15m, no_catalysts, session_ending_soon] — confidence: N/A — thesis: 15m BB bandwidths extremely tight across all assets (BTC 0.004, ETH 0.008, SOL 0.007, DOGE 0.006) — market coiling with no directional conviction. Bounces hitting BB upper resistance, not breakouts. London Morning ending in ~60 min. Flat and waiting for next session with clean signals.
