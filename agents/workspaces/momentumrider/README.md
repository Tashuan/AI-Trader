# MomentumRider Workspace

Self-contained Devin Desktop workspace for the **MomentumRider** trading agent.

## Agent Profile
- **Identity:** Trend-following trader — follows breakouts, rides trends
- **Cycle interval:** 25 minutes
- **Risk tolerance:** Aggressive on confirmed trends, patient otherwise
- **Hold period:** Position (days to weeks)
- **Max positions:** 5
- **Credentials:** Name `MomentumRider`, Email `momentumrider@agent.dev`

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. MCP server `liquid-co-invest` configured (for market data tools only)
3. `curl` and `jq` available in PATH
4. `python3` with `yfinance` for technical analysis

## Quick Start
1. Open this workspace folder in Devin Desktop
2. Type `@skills:start-cycle` to bootstrap the agent
3. MomentumRider will login, fetch config, and begin autonomous 25-minute cycles
4. To stop: tell the agent to stop cycling

## Workspace Structure
```
momentumrider/
├── .devin/
│   ├── rules/
│   │   └── momentumrider-identity.md  # Always-on identity & MCP rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md               # Bootstrap skill
├── INSTRUCTIONS.md                     # Full cycle protocol with MCP integration
├── DIRECTIVES.md                       # Global directives
├── journal_MomentumRider.md            # Trade journal
├── SKILL.md                            # Condensed AI-Trader API reference
└── README.md                           # This file
```

## MCP Tool Usage
- **Allowed:** `mcp0_analyze_market`, `mcp0_get_news`, `mcp0_analyze_markets_batch`, `mcp0_get_positioning_pulse`, `mcp0_show_chart` (market data only)
- **Forbidden:** All trade execution MCP tools — trades go through AI-Trader API via `curl`

## Steering
Edit `DIRECTIVES.md` to override focus symbols, risk settings, or add special instructions.
