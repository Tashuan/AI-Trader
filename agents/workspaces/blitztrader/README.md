# BlitzTrader Workspace

Self-contained Devin Desktop workspace for the **BlitzTrader** prediction-market trading agent (v2 — hard-rule exit architecture).

## Agent Profile
- **Identity:** Fast momentum scalper — reacts to velocity, but exits are rule-governed, not vibes-governed
- **Cycle interval:** dynamic via `poll_interval` (10–3600s, default fast; agent adjusts based on activity)
- **Risk tolerance:** Aggressive, sized off signal strength — not off "feeling hot" or chasing losses
- **Hold period:** Scalp (minutes) — enforced by a stagnation timeout, not just a stated intention
- **Max positions:** 15
- **Credentials:** Name `BlitzTrader`, Email `blitztrader@agent.dev`

## What Changed in v2
The original identity/instructions let exit decisions live entirely in the agent's per-cycle reasoning ("I'll hold one more cycle..."), with no mechanism forcing it to actually follow through. v2 adds:
- **Non-Negotiable Exit Rules** (hard stop-loss, profit target, stagnation timeout, momentum death, overbought exhaustion, VWAP loss) — checked in a fixed order, numbers-before-narrative, every cycle, before any qualitative reasoning about a position.
- **Position Review Checklist** run first each cycle, ahead of scanning for new entries.
- **Price-source reconciliation** — platform price is authoritative over MCP price when they disagree.
- **Signal-family weighting** — entry signals are checked for redundancy (e.g. RSI/MACD/1h-return all measuring the same trend) so "4+ signals" isn't false-confirmed by correlated indicators.
- **Circuit breaker and pattern-learning sample-size floor** are now hard rules, not suggestions, in `DIRECTIVES.md` / `INSTRUCTIONS.md`.

See `INSTRUCTIONS.md` for the full cycle protocol.

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis fallback

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. BlitzTrader will login, fetch config, run the Position Review Checklist on any open positions, then begin autonomous cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
blitztrader/
├── .devin/
│   ├── rules/
│   │   └── blitztrader-identity.md    # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol: entry logic, hard exit rules, journaling
├── DIRECTIVES.md                       # Global directives (risk, journal, focus, circuit-breaker overrides)
├── journal_BlitzTrader.md              # Trade journal (persistent state; numbers-first entries)
├── API_REFERENCE.md                    # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through the AI-Trader API via `curl`, never through an MCP execution tool

## Exit Rules at a Glance
These fire regardless of how the agent's qualitative read of a position looks. Full detail in `INSTRUCTIONS.md`.

| Rule | Trigger |
|---|---|
| Hard stop-loss | -2% |
| Profit target | +2% |
| Stagnation timeout | 6 consecutive cycles with <0.3% move |
| Momentum death | Volume ratio < 0.5x |
| Overbought exhaustion | RSI > 75 with volume dropping while price rises |
| VWAP loss | Price closes below VWAP on a long entered above it |

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions — the agent reads it every cycle. Note: directives can adjust entry criteria and sizing, but should not be used to disable the Non-Negotiable Exit Rules above; if a directive conflicts with a hard exit rule, the hard rule still applies.