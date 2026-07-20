# OpenSniper Workspace

Self-contained Devin Desktop workspace for the **OpenSniper** trading agent (v2 — hard-rule exit architecture).

## Agent Profile
- **Identity:** Precision opening range scalper — strikes in the first 30 minutes, exits are rule-governed, not vibes-governed
- **Cycle interval:** 2 minutes
- **Risk tolerance:** Aggressive on entries, disciplined on exits
- **Hold period:** Ultra-short (5-10 minutes), enforced by a `cycles_open` time-stop counter, not just a stated intention
- **Max positions:** 8
- **Credentials:** Name `OpenSniper`, Email `opensniper@agent.dev`

## What Changed in v2
The original instructions let exit decisions live entirely in per-cycle reasoning ("I'll give it one more cycle..."), with nothing forcing follow-through. v2 adds:
- **Non-Negotiable Exit Rules** (hard stop, profit target, time stop via `cycles_open`, momentum death, false-breakout reversal, profit lock) — checked in a fixed order, numbers-before-narrative, every cycle, before any qualitative reasoning about a position.
- **Position Review Checklist** run first each cycle, ahead of scanning for new breakouts.
- **Price-source reconciliation** — platform price is authoritative over MCP price when they disagree by more than 0.1%.
- **Signal-family weighting** — breakout score factors are checked for redundancy (range tightness and speed-of-breakout both measure "clean move") so a 6+ score isn't false-confirmed by correlated factors.
- **Circuit breaker and pattern-learning sample-size floor** are now hard rules, not suggestions, in `DIRECTIVES.md` / `INSTRUCTIONS.md`.

See `INSTRUCTIONS.md` for the full cycle protocol.

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for 1-minute candle data

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. OpenSniper will login, fetch config, run the Position Review Checklist on any open positions, then begin autonomous 2-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
opensniper/
├── .devin/
│   ├── rules/
│   │   └── opensniper-identity.md     # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_OpenSniper.md               # Trade journal (with key metrics tracking)
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`, never through an MCP execution tool

## Exit Rules at a Glance
These fire regardless of how the agent's qualitative read of a position looks. Full detail in `INSTRUCTIONS.md`.

| Rule | Trigger |
|---|---|
| Hard stop-loss | -1.5% (tighten to -1% in bearish macro) |
| Profit target | +1.5% to +3% (ATR-scaled) |
| Time stop | `cycles_open >= 5` (~10 min) without hitting target or stop |
| Momentum death | Volume declining on last 3 candles + price stalling |
| False breakout reversal | Price re-enters the opening range within 1-2 candles of confirmation |
| Profit lock | Up +1% and volume drops below OR average |

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions — the agent reads it every cycle. Note: directives can adjust entry criteria and sizing, but should not be used to disable the Non-Negotiable Exit Rules above; if a directive conflicts with a hard exit rule, the hard rule still applies.
