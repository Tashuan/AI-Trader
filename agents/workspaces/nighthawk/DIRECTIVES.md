# NightHawk — Directives

This file is read at the start of every cycle. Edit it to steer NightHawk without touching any code. If a section is empty, NightHawk falls back to the defaults in INSTRUCTIONS.md.

Everything in this file is written as if real money is on the line, because eventually it may be. Treat blank/default values as the conservative choice, not an oversight.

---

## Mode

`mode: paper`

Valid values: `paper` | `live`

**This must be manually flipped to `live` — never inferred, never auto-promoted.** See "Paper-to-Live Gate" below before ever setting this to `live`.

## Trading Halt

`halt: false`

Set to `true` to force NightHawk to close no new positions this cycle, regardless of any signal. Existing positions still respect their stop-loss/take-profit. Use this before news events, exchange outages, or anytime you don't trust current conditions.

## Focus / Restricted Instruments

`focus_symbols:` (empty = use full watchlist: BTC, ETH, SOL, DOGE)
`excluded_symbols:` (empty = none excluded)

## Risk Overrides

`max_positions_override:` (empty = use INSTRUCTIONS.md default of 5)
`max_daily_loss_pct: 3` — hard circuit breaker. If realized + unrealized loss for the day hits this %, NightHawk goes flat and halts new entries until manually reset, regardless of session or signal quality.
`max_single_trade_size_pct_override:` (empty = use tiered sizing in INSTRUCTIONS.md)

## Kill-Zone Override

`disable_kill_zone_aggression: false`

If `true`, NightHawk treats all sessions with the "active session" sizing/threshold tier — no full-size kill-zone entries. Useful if you want a flatter risk profile temporarily without redefining the whole strategy.

## Ad-Hoc Instructions

(free text — NightHawk reads this literally each cycle, e.g. "reduce size 50% this week, low conviction on BTC macro setup" or "no new SOL positions until further notice")

---

## Paper-to-Live Gate

Do not set `mode: live` until all of the following are true. This isn't a formality — it's the actual gate between a game and a brokerage account:

1. **Minimum sample size**: at least 30 closed paper trades across at least 2 full kill-zone weeks, so the session-based edge has actually been tested, not just assumed.
2. **Broker integration confirmed**: a real crypto exchange/broker API key is connected for this specific agent, scoped to the minimum permissions needed (trade + read, never withdrawal).
3. **Capital is explicitly allocated and isolated**: a fixed, named dollar amount set aside for this agent only — never a shared pool with other agents or the operator's main funds.
4. **`max_daily_loss_pct` circuit breaker is live-tested**: confirm in paper mode that hitting the daily loss threshold actually halts new entries before trusting it with real funds.
5. **A human has reviewed the last 10 journal entries** and can explain, in their own words, why each trade was taken. If the reasoning reads as noise, don't go live yet — fix the signal logic first, not the sizing.

`live_gate_signed_off_by:` (name/date — leave blank until genuinely done)

## Kill Switch

`kill_switch: false`

If `true`, NightHawk closes all open positions immediately at next cycle and stops trading entirely (not just new entries) until this is reset to `false`. This is the "something is wrong, stop everything now" switch — separate from `halt`, which only stops new entries.
