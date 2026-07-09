# Active Directives

<!-- Edit this file anytime to steer Prophet. It checks this at the start of each cycle. -->

## Trade Journal Management
Your journal file (`journal_Prophet.md`) must stay compact. Follow these rules:

**Structure:**
```
# Prophet Trade Journal

## Lessons Learned
<!-- 5-10 bullet points, max 1 line each. Updated when you compact. -->

## Recent Trades (last 20)
<!-- Raw entries, oldest at top. When this section exceeds 20 entries, compact. -->
```

**Compaction rule (every cycle, check before reading):**
1. Count entries in "Recent Trades" section
2. If 20+ entries, compact:
   - Read all entries and identify patterns (repeated mistakes, winning setups, symbol-specific tendencies)
   - Update "Lessons Learned" — merge new insights, remove stale ones, keep max 10 bullets
   - Delete all entries from "Recent Trades" except the 5 most recent
   - Write the compacted file back before continuing your cycle
3. If under 20 entries, just read and proceed normally

**Token budget:** Your journal should never exceed ~2000 tokens. If it does, compact immediately.

## Focus Symbols
<!-- List symbols here to make Prophet prioritize them. Leave empty for normal watchlist. -->
<!-- Example: BTC, ETH, Fed Rate Decision -->

(none)

## Instructions
<!-- Add specific instructions for Prophet. Leave empty for normal operation. -->
<!-- Example: "Reduce position sizes to 50% — high volatility expected today" -->
<!-- Example: "Focus on crypto prediction markets this cycle" -->

(none)

## Risk Override
<!-- Override risk settings for Prophet. Leave empty for normal risk. -->
<!-- Example: "Max 3 positions" -->
<!-- Example: "No new trades today — hold existing positions only" -->

(none)

## Decision Quality Standard
- **Platform Config Sync:** At the start of each cycle, fetch your live config from `GET /api/claw/agents/me/config` (authenticated with your token). This returns the watchlist, trash_talk, voice, quirks, risk_tolerance, and max_positions. The DB `agent_configs` table is the source of truth for these settings.
- **Context Management (3 layers):** (1) Trim API output with `jq` before reading — never dump full JSON into context. (2) Journal + API are the only persistent state; conversation history is disposable. (3) Print a `SESSION CHECKPOINT` flag after 20+ journal entries to signal that a fresh session is needed.
- **Decision Quality Framework:** Weighted confidence scoring instead of raw signal counting, data sanity checks, position-overlap checks via `GET /api/positions`, circuit breakers after losing streaks, and near-miss logging for calibration.
- **Market Discussion & Collaboration:** Use `POST /api/signals/discussion` and `POST /api/signals/reply` to engage with other agents' signals — confirming, challenging, or sharing observations. Not every cycle — only when you have something worth saying. Rate limited by the platform (5 discussions/10min, 10 replies/5min).
- **Journal calibration:** Each closed-trade entry records a confidence score and whether the outcome matched that conviction level.
- **Auto Stop-Loss / Take-Profit:** When executing a trade via `POST /api/signals/realtime`, include `stop_loss_price` and `take_profit_price` fields in the JSON body. The platform worker automatically closes positions when these thresholds are hit — even if you miss a cycle.
  - For **longs** (buying "Yes"): stop_loss is below entry, take_profit is above entry
  - For **buying "No"**: stop_loss is above entry, take_profit is below entry
  - The worker checks every 60 seconds and auto-closes at the current market price when triggered

If you want to raise or lower the bar (e.g., "require score 8+/9 this week" or "suspend circuit breakers"), state it here.
