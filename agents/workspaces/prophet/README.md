# Prophet Workspace — Devin Desktop

A self-contained workspace for the Prophet prediction-market trading agent. When you open this folder as a Devin Desktop workspace, Devin Local becomes Prophet — an autonomous agent that discovers prediction markets, assesses probabilities, and trades on the AI-Trader platform with paper money.

## Prerequisites

1. **AI-Trader backend running** on `localhost:8000`
   - Start it from `service/server/` with: `python -m uvicorn main:app`
2. **Co-Invest MCP server** configured globally in Devin Desktop settings
   - This provides market data tools (prediction market search, orderbook, news, asset analysis)
3. **Devin Desktop** (post-June 2026 rebrand from Windsurf)
   - Cascade is end-of-life. This workspace uses `.devin/` directory structure.

## How to Start Prophet

1. Open this folder (`agents/workspaces/prophet/`) in Devin Desktop (File → Open Folder)
2. Type `@skills:start-cycle` or just say **"start cycling"**
3. Prophet bootstraps:
   - Reads `INSTRUCTIONS.md` for the full cycle protocol
   - Reads `DIRECTIVES.md` for user overrides
   - Reads `journal_Prophet.md` for past lessons
   - Logs in to AI-Trader platform
   - Fetches live config
   - Begins cycle 1
4. Prophet runs cycles continuously (20-minute intervals) until you tell it to stop

## How to Steer Prophet

Edit `DIRECTIVES.md` to:
- Set focus symbols (e.g., "BTC, ETH, Fed Rate Decision")
- Add special instructions (e.g., "Focus on crypto prediction markets this cycle")
- Override risk settings (e.g., "Max 3 positions", "No new trades today")
- Adjust decision quality thresholds (e.g., "Require score 8+/9 this week")

Prophet reads `DIRECTIVES.md` at the start of every cycle, so changes take effect within 20 minutes.

## How to Stop

Tell Devin Local to stop (e.g., "stop cycling" or "stop").

## How to Resume After a Session Checkpoint

1. Open this folder in Devin Desktop
2. Run `@skills:start-cycle` again
3. Prophet reconstructs full state from:
   - `journal_Prophet.md` (past trades, lessons, patterns)
   - AI-Trader API (current positions, portfolio, config)
   - No conversation history needed — it's disposable

## Workspace Structure

```
prophet/
├── .devin/
│   ├── rules/
│   │   └── prophet-identity.md       # Auto-loaded: identity, MCP rules, cycle protocol
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md              # Startup skill: bootstraps Prophet
├── INSTRUCTIONS.md                   # Full agent instructions (cycle protocol, strategy)
├── DIRECTIVES.md                     # Steering file (edit to control Prophet)
├── journal_Prophet.md                # Trade journal (auto-maintained)
├── SKILL.md                          # Condensed AI-Trader API reference
└── README.md                         # This file
```

## MCP Tool Usage

Prophet uses the Co-Invest MCP server for market data:

| MCP Tool | Purpose | Replaces |
|---|---|---|
| `mcp0_search_prediction_markets` | Discover prediction markets | Gamma API curl |
| `mcp0_show_prediction_orderbook` | Get market prices/orderbook | CLOB API curl |
| `mcp0_analyze_market` | Underlying asset prices (BTC, ETH, etc.) | yfinance |
| `mcp0_get_news` | Market news context | `/api/market-intel/news` |

**Prophet NEVER executes trades on Liquid.** All trades go through AI-Trader's `POST /api/signals/realtime` with paper money.

## How to Replicate for Other Agents

1. Copy this folder: `cp -r agents/workspaces/prophet agents/workspaces/<new-agent>`
2. Edit `.devin/rules/prophet-identity.md` — change identity, personality, strategy
3. Edit `INSTRUCTIONS.md` — adapt the cycle protocol for the new agent's strategy
4. Edit `journal_<AgentName>.md` — start with empty template
5. Update `DIRECTIVES.md` if needed
6. Keep `SKILL.md` as-is (shared AI-Trader API reference)
7. Commit to git

## Portability

All files are committed to git. After `git clone` on any machine:
1. Open `agents/workspaces/prophet/` in Devin Desktop
2. Ensure AI-Trader backend is running and MCP server is configured
3. Run `@skills:start-cycle`
4. Prophet works identically
