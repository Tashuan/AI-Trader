# RangeRider Workspace

Self-contained Devin Desktop workspace for the **RangeRider** trading agent.

## Agent Profile
- **Identity:** Range trading specialist — profits from sideways markets
- **Tagline:** "Trends are for amateurs. I trade the range."
- **Cycle interval:** 15 minutes
- **Risk tolerance:** Moderate (ATR-based stops within ranges)
- **Hold period:** Swing (days to weeks) — ranges persist
- **Max positions:** 5
- **Credentials:** Name `RangeRider`, Email `rangerider@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance`, `numpy`, `pandas` for ADX/BB calculations

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. RangeRider will login, fetch config, and begin autonomous 15-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
rangerider/
├── .devin/
│   ├── rules/
│   │   └── rangerider-identity.md     # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_RangeRider.md               # Trade journal
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
