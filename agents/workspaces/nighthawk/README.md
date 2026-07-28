# NightHawk Workspace

Session-aware crypto trading agent. Trades BTC/ETH/SOL/DOGE with behavior that shifts by global trading session (Asian, London kill-zone, US after-hours, weekend), rather than trading identically at all hours.

## Files

- **INSTRUCTIONS.md** — full identity, strategy logic, session definitions, entry/exit rules, sizing tiers. Read this to understand *how* NightHawk decides.
- **DIRECTIVES.md** — operator-editable controls: halt switch, kill switch, risk overrides, paper-to-live gate. Edit this to steer NightHawk without touching strategy logic. **This is the file you actually touch day-to-day.**
- **PREFLIGHT.md** — the condensed per-cycle checklist NightHawk runs through before acting. Useful as a quick sanity check on what order of operations the agent follows.
- **journal_NightHawk.md** — running trade log, one line per entry/exit with thesis and confidence. This is the real audit trail — read it before ever trusting this agent with live capital.
- **.devin/rules/nighthawk-identity.md** — voice/persona guardrails, kept separate from strategy so tone tweaks don't risk touching risk logic.
- **.devin/skills/start-cycle/SKILL.md** — cycle bootstrap sequence.

## Phase 1 Scope (current)

Crypto perpetuals only. Forex and index futures were part of an earlier concept but are intentionally excluded until:
- a real bid/ask quote-depth data source exists (not just OHLCV), and
- a broker integration exists for those asset classes.

Do not re-introduce those asset classes by editing DIRECTIVES.md focus_symbols — they aren't supported by the strategy logic in INSTRUCTIONS.md regardless of what's listed there.

## Before Going Live

See "Paper-to-Live Gate" in DIRECTIVES.md. Every condition there should be true before `mode: live` is ever set. This isn't boilerplate — it's the actual checklist between a paper-trading personality and a system trading real money.
