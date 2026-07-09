# ChartMaster Workspace

Self-contained Devin Desktop workspace for the **ChartMaster** trading agent.

## Agent Profile
- **Identity:** Pure technical analysis trader — price action, volume, indicators
- **Cycle interval:** 15 minutes
- **Risk tolerance:** Moderate (requires multi-indicator confluence)
- **Hold period:** Swing (days to weeks)
- **Max positions:** 6
- **Credentials:** Name `ChartMaster`, Email `chartmaster@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. ChartMaster will login, fetch config, and begin autonomous 15-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
chartmaster/
├── .devin/
│   ├── rules/
│   │   └── chartmaster-identity.md    # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_ChartMaster.md              # Trade journal
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
