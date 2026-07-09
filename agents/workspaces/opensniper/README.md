# OpenSniper Workspace

Self-contained Devin Desktop workspace for the **OpenSniper** trading agent.

## Agent Profile
- **Identity:** Precision opening range scalper — strikes in the first 30 minutes
- **Cycle interval:** 2 minutes
- **Risk tolerance:** Aggressive on entries, disciplined on exits
- **Hold period:** Ultra-short (5-10 minutes)
- **Max positions:** 8
- **Credentials:** Name `OpenSniper`, Email `opensniper@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for 1-minute candle data

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. OpenSniper will login, fetch config, and begin autonomous 2-minute cycles
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
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
