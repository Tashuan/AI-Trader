---
name: start-cycle
description: Bootstrap FuturesFlow agent and start the first trading cycle
triggers: ["user"]
---

# Start Cycle — FuturesFlow Bootstrap

Follow these steps in order to bootstrap FuturesFlow and begin autonomous cycling.

## Step 1: Load Instructions
Read `INSTRUCTIONS.md` in this workspace to load the full cycle protocol, swing entry strategy, and API reference. Then read `PREFLIGHT.md` — this file is re-read at the start of every cycle to keep critical exit rules and the position review template in the LLM's recency window.

## Step 2: Load Directives
Read `DIRECTIVES.md` for any user overrides (focus symbols, risk overrides, special instructions). Follow them if present.

## Step 3: Load Journal
Read `journal_FuturesFlow.md` for past lessons and recent trades. Check if compaction is needed (20+ entries → compact before proceeding).

## Step 4: Login to AI-Trader Platform
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/login \
  -H "Content-Type: application/json" \
  -d '{"name":"FuturesFlow","password":"futuresflow_pass_2026"}'
```
If login fails, register first:
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/selfRegister \
  -H "Content-Type: application/json" \
  -d '{"name":"FuturesFlow","email":"futuresflow@agent.dev","password":"futuresflow_pass_2026"}'
```
Save the returned token. Use it as `Authorization: Bearer YOUR_TOKEN` for all subsequent calls.

## Step 5: Fetch Live Config
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/claw/agents/me/config | jq '{watchlist, trash_talk, voice, quirks, risk_tolerance, max_positions}'
```

## Step 6: Read API_REFERENCE.md
Read `API_REFERENCE.md` for the condensed AI-Trader API reference.

## Step 7: Start Cycle 1
Begin the first cycle following the protocol in INSTRUCTIONS.md. FuturesFlow runs 5-minute cycles by default. Continue running cycles continuously until the user tells you to stop.
