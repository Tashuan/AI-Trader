# PREFLIGHT — Read This EVERY CYCLE Before Doing Anything

## Non-Negotiable Exit Rules (Hard-Coded, Not LLM Discretion)

These fire regardless of how good the "thesis" still sounds. Check them FIRST, in this order, before writing any narrative reasoning.

1. **Hard stop-loss: -3%.** No exceptions. Close immediately.
2. **Profit target: +6%.** Scale out per sizing plan. Don't rationalize holding for "more" without a new, independently-scored setup.
3. **Stagnation timeout:** 8 consecutive cycles with price move < 1% either direction AND no new volume signal → EXIT. Track `cycles_flat` per position mechanically:
   - `cycles_flat += 1` if abs(price_change_since_last_cycle) < 1%, else reset to 0.
   - `if cycles_flat >= 8: close position, log reason "stagnation timeout"`.
4. **Trend reversal:** EMA 20 crosses below EMA 50 (for longs) or above EMA 50 (for shorts) → exit. The trend that justified your entry is broken.
5. **Volume dry-up:** volume ratio < 0.4x for 3+ consecutive cycles → exit. No participation = no reason to stay in.
6. **Key level breach:** price closes below key support (for longs) / above key resistance (for shorts) → exit. The structure you entered on is invalidated.

If you catch yourself writing "I'll hold one more cycle" for the second time about the same position, the rule above should already have fired. Check it before writing that sentence again.

---

## Position Review Template (Fill Out EVERY Open Position, EVERY Cycle)

Copy this block for each open position. Fill in numbers BEFORE writing any interpretation. Numbers first, story second.

```
POSITION: [symbol] | SIDE: [long/short] | ENTRY: $[x] | CURRENT: $[x] | PnL: [x]%
SL distance: [x]% | TP distance: [x]% | cycles_flat: [n] | vol_ratio: [x] | EMA20: [above/below EMA50] | key_level: [x% away]
Rule 1 (-3% SL): [FIRED/NOT FIRED]
Rule 2 (+6% TP): [FIRED/NOT FIRED]
Rule 3 (stagnation 8 cycles): [FIRED/NOT FIRED]
Rule 4 (trend reversal EMA cross): [FIRED/NOT FIRED]
Rule 5 (volume dry-up <0.4x for 3 cycles): [FIRED/NOT FIRED]
Rule 6 (key level breach): [FIRED/NOT FIRED]
VERDICT: [EXIT — which rule / HOLD — no rule fired]
```

If ANY rule fired → exit immediately. No further reasoning needed for that position this cycle.
If NO rule fired → you may write qualitative read (trend structure, support/resistance, thesis status), but it cannot override a fired rule.

---

## Entry Guardrails (Quick Reference)

- Need 4+ signals across 2+ signal families AND volume ratio > 1.3x
- Weight confidence lower if all 4+ signals are from same family (trend vs volume vs volatility vs structure)
- After 3 consecutive losing trades: cut size 50%, require 5+ signals from 2+ families
- Never double up on a symbol you already hold — check `GET /api/positions` first
- Every entry MUST include `stop_loss_price` and `take_profit_price` (platform auto-close is primary enforcement)
- Bearish macro (bullish_count/total < 0.3): require 5+ signals, cut sizes 50%
- No setup = no trade. A fired exit rule = no debate.
- Futures support short/cover — look for both long and short setups
- Check market hours before entering — no new trades when futures are closed
