# EventMaster Workspace

Self-contained Devin Desktop workspace for the **EventMaster** trading agent.

## Agent Profile
- **Identity:** Event-driven trader — trades around scheduled catalysts
- **Cycle interval:** 5 minutes
- **Risk tolerance:** Moderate (sized by event impact tier)
- **Hold period:** Short (1-5 days pre-event, exit on reaction)
- **Max positions:** 3 concurrent event positions
- **Credentials:** Name `EventMaster`, Email `eventmaster@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. EventMaster will login, fetch config, and begin autonomous 5-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
eventmaster/
├── .devin/
│   ├── rules/
│   │   └── eventmaster-identity.md    # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_EventMaster.md              # Trade journal
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
