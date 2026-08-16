Here's the concrete instruction set, grounded in the actual trade data we just analyzed.

> **Status: AUTOMATED (2026-08-15).** All four decisions below have been implemented as StockBoy supervisor detectors. See `FENCEBAR_SYSTEM.md` for the full system architecture. This manual remains as the design specification for the automated detectors.
> - Decision 1 → `service/server/stockboy_premarket.py` (`evaluate_vol_override`)
> - Decision 2 → `service/server/stockboy_entry_detector.py` (`evaluate_entry_quality`)
> - Decision 3 & 4 → `service/server/stockboy_position_monitor.py` (`monitor_position`)

---

## Human-in-the-Loop Operator Manual
### Fence Bar Strategy — 1R Target, ATR 1.8% Filter

You are the loss prevention layer. The strategy picks its own entries and exits. Your job is to veto bad entries and manage losing trades. **Do not touch winning trades.** The strategy hit 1R on 9 of 16 trades — leave those alone.

---

### Decision 1: Vol Filter Override
**When:** Pre-market, before 09:30 ET
**Action:** The system will block trading on any day where SPY 20-day ATR is below 1.8%. You can override this block.

**Override YES if any of these are true:**
- A symbol in today's universe has earnings within ±1 day
- A symbol has a major catalyst (FDA approval, product launch, analyst upgrade/downgrade before open)
- SPY gapped > 1.5% from prior close (pre-market)
- VIX is above 25 and rising into the open
- A symbol in the universe gapped > 3% on the daily chart

**Do NOT override if:**
- ATR is below 1.2% (even with a catalyst, the breakout won't have fuel)
- It's a low-vol chop period (SPY daily range < 0.8% for 5+ consecutive days)
- No specific catalyst — just "feels like a volatile day"

**What you're deciding:** "Is there a reason today is special even though the volatility regime filter says no?" The filter is a blunt instrument. You're adding context it can't see.

---

### Decision 2: Entry Confirmation
**When:** 09:30-09:35 ET, during the 5-minute fence bar
**Action:** The system will generate an entry signal when the fence bar breaks out. You must confirm or veto before the order fills.

**VETO (skip the trade) if:**
- **Order book is thin:** Fewer than 5 round lots (500 shares) on the side you're trading. For a long, look at the ask side. For a short, look at the bid side. Thin book = no institutional participation = breakout will fail.
- **Volume on the fence bar is below average:** The 09:30-09:35 bar should have at least 2x the average 5m bar volume from the prior 3 days. If it's 1x or less, there's no conviction behind the move.
- **Spread is wide:** Bid-ask spread is more than 0.05% of price (e.g., >$0.10 on a $200 stock). Wide spread = low liquidity = you'll get filled at a bad price and the breakout lacks participation.
- **The breakout bar is weak:** The close of the fence bar is near the middle of its range, not near the high (for longs) or low (for shorts). A strong breakout closes in the top/bottom 25% of the bar.
- **No follow-through in the first 1-2 bars after breakout:** If price stalls or reverses within 10 minutes of the breakout, the move has no legs. This is the pattern that produced our 2 pure-loser trades (MFE +0.08%, stopped out in 3 bars).

**CONFIRM if:**
- Order book has visible size on your side (5+ round lots)
- Fence bar volume is 2x+ average
- Spread is tight (< 0.03% of price)
- Fence bar closed in the top/bottom 20% of its range
- Price is pushing through the breakout level, not stalling at it

**What you're deciding:** "Is there real money behind this breakout, or is it a fake?" The strategy can't see Level 2 or tape velocity. You can.

---

### Decision 3: Move Stop to Breakeven
**When:** Intra-trade, after entry is filled, monitoring until exit
**Action:** Once the trade reaches +0.5% favorable, move your stop loss to your entry price. This converts a potential loser into a risk-free trade.

**Move stop to breakeven when ALL of these are true:**
- Trade is +0.5% or more in your favor (check the high for longs, low for shorts)
- AND at least 10 minutes have passed since entry (don't do this instantly — give the trade room)
- AND momentum is stalling: the last 3-5 bars (15-25 minutes) have not made a new favorable extreme (no new high for longs, no new low for shorts)

**Do NOT move to breakeven if:**
- Trade is +0.5% but still making new extremes every few bars — it's trending, let it run
- Less than 10 minutes since entry — you'll get stopped on noise
- Trade is only +0.2-0.4% — too close to entry, normal noise will stop you out

**What you're deciding:** "The trade proved itself by going +0.5%, but now it's stalling. Lock in zero risk." This is the pattern that produced our 2 reverser trades (META shorts went +0.69% in 2 bars, then reversed to -1.14% stop over the next 12 bars). The reversal took over an hour — you had plenty of time to see it stall and move your stop.

---

### Decision 4: Take Profit Early
**When:** Intra-trade, when momentum has clearly died
**Action:** Exit the trade at the current price before the 15:55 force exit. The system will hold until 1R or close — you're cutting a stalled trade that's drifting back.

**Exit early if ALL of these are true:**
- Trade is currently profitable (even slightly — +0.1% counts)
- AND trade peaked at +0.5% or more at some point
- AND it's been 30+ minutes since the peak favorable price (high for longs, low for shorts)
- AND the last 6 bars (30 minutes) have not made a new favorable extreme
- AND price is drifting back toward entry (last bar's close is closer to entry than to the MFE price)

**Do NOT exit early if:**
- Trade is still making new extremes — it may be consolidating before continuation
- Trade is at or near the 1R target — let the system take the profit
- Trade is losing money — that's what the stop is for, don't panic exit
- It's before 11:00 — morning momentum can resume after a mid-morning consolidation

**What you're deciding:** "The trade had its moment, it's not going to hit 1R, and it's drifting back to negative. Take what's left." This is the pattern that produced our 3 staller trades (NVDA longs peaked at +0.71% around 11:30, then drifted sideways for 4 hours to close at -0.65%). The stall was obvious by 12:00 — you'd have exited at +0.5% instead of -0.65%.

---

### What NOT to Do

- **Do not add to winners.** The strategy sizes positions based on risk. Adding changes the risk profile.
- **Do not move the target higher.** 1R is the target. Greed is how +0.7% becomes -0.65%.
- **Do not override the stop loss on losing trades.** If the trade is going against you from the start (never went +0.3% favorable), let the stop take you out. Hope is not a strategy.
- **Do not trade without the fence bar signal.** You only act on system-generated signals. No discretionary entries.
- **Do not skip Decision 2.** This is the most valuable gate. 2 of 7 losers had MFE < 0.08% — they never had a chance. Vetoing at entry saves the full stop loss.

---

### Time-Based Quick Reference

| Time | Action | What You're Watching |
|---|---|---|
| Pre-market | Decision 1: Vol filter override | Earnings calendar, SPY gap, VIX, catalysts |
| 09:30-09:35 | Decision 2: Entry confirmation | Level 2 depth, fence bar volume, spread, close position in bar |
| 09:35-11:00 | Monitor only | Let the trade work. Don't touch unless +0.5% and stalling (Decision 3) |
| 11:00-15:00 | Decision 3 & 4: Stop management, profit taking | Is it making new extremes? Has it stalled for 30+ min? |
| 15:00-15:55 | Final decision: hold or cut | If stalled and barely positive, cut it. Don't wait for force exit. |
| 15:55 | System force exit | Hands off — system handles this |

---

### Expected Impact (from our 16-trade sample)

| Decision | Trades Affected | Value Added | Success Signal |
|---|---|---|---|
| Skip entry | 2 of 16 | +1.88% | Thin book, low volume, weak close |
| Move stop to BE | 2 of 16 | +1.38% | +0.5% then stall, no new extremes |
| Take profit early | 3 of 16 | +3.65% | Peaked +0.5%+, 30+ min sideways, drifting back |
| Override vol filter | untested | unknown | Catalyst + moderate ATR |
| **No action needed** | **9 of 16** | **+0.00%** | **Trade hit 1R target — leave it alone** |

The strategy does the heavy lifting on 56% of trades. You're the insurance policy on the other 44%.