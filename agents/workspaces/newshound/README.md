# NewsHound Workspace

Self-contained Devin Desktop workspace for the **NewsHound** trading agent.

## Agent Profile
- **Identity:** News-driven trader — sniffs out alpha in headlines before the market catches up
- **Cycle interval:** 10 minutes
- **Risk tolerance:** Moderate (sized by story strength)
- **Hold period:** Swing (hours to days)
- **Max positions:** 8
- **Credentials:** Name `NewsHound`, Email `newshound@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical confirmation

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. NewsHound will login, fetch config, and begin autonomous 10-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
newshound/
├── .devin/
│   ├── rules/
│   │   └── newshound-identity.md      # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_NewsHound.md                # Trade journal
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
