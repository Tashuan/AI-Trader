# PREFLIGHT — Read This EVERY CYCLE Before Doing Anything

## Non-Negotiable Exit Rules (Hard-Coded, Checked in Order — Not Discretion)

These fire regardless of how good the "thesis" still sounds. Check them FIRST, in this order, before writing any narrative reasoning.

1. **Hard stop: -1.5%** (tighten to -1% in bearish macro). No exceptions. Close immediately.
2. **Profit target hit: +1.5% to +3%** (per ATR-based tier). Take it. No greed, no holding for "a bit more" without a fresh, independently-scored setup.
3. **Time stop / stagnation:** `cycles_open >= 5` (≈10 minutes) and neither target nor stop has hit → EXIT. This is hold-time discipline, not a suggestion.
4. **Momentum dying:** volume on last 3 candles trending down AND price stalling (no new high/low) → EXIT. If up at all, secure it now.
5. **False breakout reversal:** price re-enters the opening range within 1-2 candles of breakout confirmation → breakout failed. EXIT immediately, do not wait for the stop.
6. **Profit lock:** position up +1% and volume drops below OR average → SECURE PROFIT now. A sniper takes the shot that's there.

If you catch yourself writing "I'll give it one more cycle" a second time about the same position, that is itself proof one of these should already have fired. Check the rule before writing that sentence again.

---

## Position Review Template (Fill Out EVERY Open Position, EVERY Cycle)

Copy this block for each open position. Fill in numbers BEFORE writing any interpretation. Numbers first, story second.

```
POSITION: [symbol] | SIDE: [long/short] | ENTRY: $[x] | CURRENT: $[x] | PnL: [x]%
SL distance: [x]% | TP distance: [x]% | cycles_open: [n] | last 3 candle vol trend: [rising/flat/falling] | price vs OR: [above/below/inside]
Rule 1 (-1.5% SL): [FIRED/NOT FIRED]
Rule 2 (+1.5-3% TP): [FIRED/NOT FIRED]
Rule 3 (time stop 5 cycles): [FIRED/NOT FIRED]
Rule 4 (momentum dying): [FIRED/NOT FIRED]
Rule 5 (false breakout reversal): [FIRED/NOT FIRED]
Rule 6 (profit lock +1% & vol<OR avg): [FIRED/NOT FIRED]
VERDICT: [EXIT — which rule / HOLD — no rule fired]
```

If ANY rule fired → exit immediately. No further reasoning needed for that position this cycle.
If NO rule fired → you may write qualitative read (volume trend, OR relationship, thesis status), but it cannot override a fired rule.

---

## Entry Guardrails (Quick Reference)

- ALL entry criteria must be met: price breaks AND CLOSES above/below OR, breakout candle volume > 1.5x OR average (2x in bearish macro), within first 30 minutes
- Score breakout quality 1-3 on each factor: range tightness, volume surge, speed of breakout, pre-market gap alignment. Require weighted total 6+.
- Signal-family weighting: range tightness + speed-of-breakout are same family. Volume surge + pre-market gap are the two distinct families. Don't count correlated factors as independent.
- After 3 consecutive losing trades: cut size 50%, require breakout score 8+ until back to breakeven
- Never double up on a symbol you already hold — check `GET /api/positions` first
- Never hold more than 2 correlated positions at once (NVDA + AMD = one bet)
- Every entry MUST include `stop_loss_price` and `take_profit_price` (platform auto-close is primary enforcement)
- Bearish macro (bullish_count/total < 0.3): require 2x volume, size at 50%, tighten stops to -1%, favor SHORT breakdowns
- No setup = no trade. A fired exit rule = no debate.
