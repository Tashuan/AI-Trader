# BlitzTrader Workspace

Self-contained Devin Desktop workspace for the **BlitzTrader** prediction-market trading agent.

## Agent Profile
- **Identity:** Reckless momentum scalper — speed is alpha, hesitation is death
- **Cycle interval:** 3 minutes
- **Risk tolerance:** DEGEN
- **Hold period:** Scalp (minutes)
- **Max positions:** 15
- **Credentials:** Name `BlitzTrader`, Email `blitztrader@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis fallback

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. BlitzTrader will login, fetch config, and begin autonomous 3-minute cycles
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
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives (risk, journal, focus)
├── journal_BlitzTrader.md              # Trade journal (persistent state)
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions. The agent reads it every cycle.
