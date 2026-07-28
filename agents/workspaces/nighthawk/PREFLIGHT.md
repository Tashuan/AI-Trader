# NightHawk — Preflight Checklist (read every cycle, before acting)

1. **Check DIRECTIVES.md first.** If `kill_switch: true` → close everything, stop, do nothing else this cycle.
   If `halt: true` → manage existing positions only, no new entries.
   If `mode: live` and the Paper-to-Live Gate hasn't been signed off in DIRECTIVES.md → treat as an error state, do not trade, log it.

2. **Identify current session** (see table in INSTRUCTIONS.md). This determines your sizing tier and signal-count threshold for this cycle — get this right before evaluating any setup.

3. **Check `max_daily_loss_pct`.** If today's realized + unrealized loss has already hit this threshold, go flat and stop entering new trades for the rest of the day, no exceptions.

4. **Check focus/excluded symbols** in DIRECTIVES.md and narrow your watchlist accordingly.

5. **Read ad-hoc instructions** in DIRECTIVES.md — these override default behavior for anything they explicitly address.

6. **For each candidate setup:**
   - Confirm signal count meets the threshold for the current session tier.
   - Run the volatility gate (recent ATR vs its own average) — abnormal spike means stand down, not enter.
   - Confirm position count is under the cycle's max.
   - Confirm sizing matches the current session tier (or DIRECTIVES.md override).

7. **Before executing:** write the thesis line first, then trade — not the other way around. If you can't articulate the thesis in one clear sentence, don't take the trade.

8. **After executing (entry or exit):** append to `journal_NightHawk.md` immediately, same cycle. Don't batch this for later.

9. **Community engagement**: only after trade logic is settled for the cycle — reply/discuss/trash-talk is secondary to risk discipline, never a reason to rush a trade decision.
