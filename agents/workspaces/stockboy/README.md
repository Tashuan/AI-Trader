# StockBoy Workspace

Self-contained Devin Desktop workspace for the **StockBoy** platform supervisor agent.

## Agent Profile
- **Identity:** Platform supervisor / AI management brain — not a strategy trader
- **Role:** Supervisor (auto-provisioned, no login required)
- **Mode:** Paper-only — never creates new entries, only adjusts existing exposure
- **Controlled runners:** BlitzRunner, CryptoRunner, ScalpRunner
- **Action policy:** Close, partial close, set stop/target/trailing, cancel stale orders
- **Cycle interval:** 60 seconds (configurable via `STOCKBOY_POLL_INTERVAL` env var)

## Architecture — Hybrid Supervisor

StockBoy runs as a **hybrid** agent with two coexisting layers:

### 1. Backend deterministic loop (always running)
The Python backend auto-starts a deterministic supervisor loop on app launch. This loop:
- Builds platform snapshots every 60 seconds
- Detects risk anomalies (missing protection, stale prices, stale orders)
- Expires stale runner overrides
- Writes commentary and journal entries
- Provides the API surface (`/api/stockboy/*`) that the AI session calls

This loop does NOT use LLM reasoning. It's the reliable, always-on safety net.

### 2. Devin AI session (manual start)
When you open this workspace in Devin and run `@skills:start-cycle`, the AI agent:
- Reads the workspace instructions (INSTRUCTIONS, PREFLIGHT, DIRECTIVES)
- Loads the pre-provisioned supervisor token from `.supervisor_token`
- Fetches snapshots via `curl` and reasons about what it sees
- Proposes and executes allowed protective actions via `/api/stockboy/action`
- Writes journal entries and publishes commentary
- Governs the platform with AI judgment, layered on top of the deterministic loop

Both layers coexist — the backend loop keeps running even when the AI session is active. The AI session adds reasoning, judgment, and proactive governance. The deterministic policy layer is always authoritative.

## Prerequisites
1. AI-Trader platform running at `http://localhost:8000`
2. `curl` and `jq` available in PATH
3. StockBoy auto-provisioned at startup (happens automatically — token in `.supervisor_token`)

## Quick Start
1. Ensure the AI-Trader backend is running (StockBoy auto-provisions on startup)
2. Open this workspace folder in Devin Desktop
3. Type `@skills:start-cycle` to bootstrap the AI supervisor
4. StockBoy will load context, fetch a snapshot, and begin autonomous overwatch cycles
5. To stop: tell the agent to stop cycling

## Workspace Structure
```
stockboy/
├── .devin/
│   ├── rules/
│   │   └── stockboy-identity.md          # Always-on identity & boundary rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md                  # Bootstrap skill
├── .supervisor_token                     # Pre-provisioned API token (auto-generated, do NOT commit)
├── INSTRUCTIONS.md                       # Full cycle protocol: overwatch, classification, action policy
├── PREFLIGHT.md                          # Non-negotiable safety checklist (read every cycle)
├── DIRECTIVES.md                         # Operator overrides and priorities
├── API_REFERENCE.md                      # Least-privilege StockBoy API reference
├── journal_StockBoy.md                   # Supervisor journal (persistent state, lessons, cycle entries)
└── README.md                             # This file
```

## Action Authority

StockBoy can ONLY:
- Close or partially close an existing controlled position
- Set or tighten stop loss, take profit, and trailing parameters
- Cancel stale pending orders belonging to controlled runners
- Apply bounded temporary runner configuration overrides

StockBoy can NEVER:
- Create a new `buy` or `short` entry
- Increase position quantity or average in
- Use live broker/MCP execution
- Modify default runner config files
- Act on agents outside the controlled allowlist

## Steering
Edit `DIRECTIVES.md` to add operator priorities — the AI session reads it every cycle. Directives can prioritize attention but cannot override server policy or the no-entry boundary.
