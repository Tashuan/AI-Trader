# PREFLIGHT — Read This EVERY CYCLE Before Doing Anything

## Goal Check (FIRST — Before Anything Else)

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/claw/agents/me/goal | jq '{status, can_trade, progress_pct, goal_achieved, max_loss_hit}'
```

- `can_trade: false` → Skip to Position Review only. No new entries.
- `goal_achieved: true` → Manage existing positions (close at profit targets). No new entries.
- `max_loss_hit: true` → Stop trading entirely. Log it. Wait for user reset.
- `status: "active"` → Proceed normally with goal-aware sizing.

---

## Non-Negotiable Exit Rules (Hard-Coded, Not LLM Discretion)

scan.py evaluates all 6 rules automatically. Check the `positions` array in scan.py output first. These fire regardless of how good the "thesis" still sounds.

1. **Hard stop-loss: -2%.** No exceptions. Close immediately.
2. **Profit target: +2%.** Scale out per sizing plan. Don't rationalize holding for "more" without a new, independently-scored setup. **Final stretch (within 20% of goal): take profit at 1.5% instead.**
3. **Stagnation timeout:** 6 consecutive cycles with price move < 0.3% either direction AND no new volume signal → EXIT. `cycles_flat` is persisted in DB via `PATCH /api/positions/{id}/state`.
4. **Momentum death:** volume ratio drops below 0.5x → exit, no debate.
5. **Overbought exhaustion:** RSI > 75 AND volume dropping while price still rising → exit.
6. **VWAP loss:** price closes below VWAP on a long entered above VWAP → exit.

If scan.py returns `verdict: "EXIT"` for any position, execute the close immediately. The `exit_reason` field tells you which rule fired. No further reasoning needed.

---

## Position Review Template (Fill Out EVERY Open Position, EVERY Cycle)

scan.py provides the values. Copy this block for each open position into your journal:

```
POSITION: [symbol] | SIDE: [long/short] | ENTRY: $[x] | CURRENT: $[x] | PnL: [x]%
SL distance: [x]% | TP distance: [x]% | cycles_flat: [n] | vol_ratio: [x] | RSI: [x] | VWAP: [above/below]
Rule 1 (-2% SL): [FIRED/NOT FIRED]
Rule 2 (+2% TP): [FIRED/NOT FIRED]
Rule 3 (stagnation 6 cycles): [FIRED/NOT FIRED]
Rule 4 (momentum death vol<0.5x): [FIRED/NOT FIRED]
Rule 5 (OB exhaustion RSI>75): [FIRED/NOT FIRED]
Rule 6 (VWAP loss): [FIRED/NOT FIRED]
VERDICT: [EXIT — which rule / HOLD — no rule fired]
```

If ANY rule fired → exit immediately. No further reasoning needed for that position this cycle.
If NO rule fired → you may write qualitative read (momentum, OBV, thesis status), but it cannot override a fired rule.

---

## Entry Guardrails (Quick Reference)

- **Run scan.py first:** `python3 scan.py --token $TOKEN` — check `ranked_setups` for qualifying entries
- Need 4+ signals across 2+ signal families AND volume ratio > 1.5x (scan.py checks this)
- After 3 consecutive losing trades: cut size 50%, require 5+ signals from 2+ families
- **Single position model:** max 1 open position at a time
- Never double up on a symbol you already hold — check `GET /api/positions` first
- Every entry MUST include `stop_loss_price` and `take_profit_price` (platform auto-close is primary enforcement)
- **Trailing stop-loss:** Include `trailing_sl_pct` and `trailing_activation_pct` on every entry to auto-ratchet SL as profit grows. Recommended: `trailing_sl_pct=1.0, trailing_activation_pct=1.0`
- Bearish macro (bullish_count/total < 0.3): require 5+ signals, cut sizes 50%
- **Goal-aware sizing:** Normal phase (0-80% progress) 25-40%, Approaching goal (80-100%) 15-25%
- No setup = no trade. A fired exit rule = no debate.
