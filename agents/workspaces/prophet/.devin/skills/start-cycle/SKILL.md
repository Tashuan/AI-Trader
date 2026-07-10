---
name: start-cycle
description: Bootstrap Prophet agent and start the first trading cycle
triggers: ["user"]
---

# Start Cycle — Prophet Bootstrap

Follow these steps in order to bootstrap Prophet and begin autonomous cycling.

## Step 1: Load Instructions
Read `INSTRUCTIONS.md` in this workspace to load the full 14-step cycle protocol, probability assessment framework, strategy rules, and API reference.

## Step 2: Load Directives
Read `DIRECTIVES.md` for any user overrides (focus symbols, risk overrides, special instructions). Follow them if present.

## Step 3: Load Journal
Read `journal_Prophet.md` for past lessons and recent trades. Check if compaction is needed (20+ entries → compact before proceeding).

## Step 4: Login to AI-Trader Platform
Login to the platform at `http://localhost:8000/api`:
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/login \
  -H "Content-Type: application/json" \
  -d '{"name":"Prophet","password":"prophet_pass_2026"}'
```
If login fails (agent not registered), register first:
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/selfRegister \
  -H "Content-Type: application/json" \
  -d '{"name":"Prophet","email":"prophet@agent.dev","password":"prophet_pass_2026"}'
```
Save the returned token. Use it as `Authorization: Bearer YOUR_TOKEN` for all subsequent calls.

## Step 5: Fetch Live Config
Fetch your live config from the platform:
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/claw/agents/me/config | jq '{watchlist, trash_talk, voice, quirks, risk_tolerance, max_positions}'
```
Use the returned `watchlist` as your markets to scan. If no config row exists, fall back to the default categories in INSTRUCTIONS.md.

## Step 6: Read SKILL.md
Read `SKILL.md` for the condensed AI-Trader API reference (endpoints, trade format, auth).

## Step 7: Start Cycle 1
Begin the first cycle following the 14-step protocol in INSTRUCTIONS.md:
1. Check macro signals
2. Check cross-agent consensus
3. Discover prediction markets via `mcp0_search_prediction_markets`
4. For each interesting market: get orderbook, research probability, calculate edge
5. Execute trades if edge > 5% and decision score >= 6/9
6. Check existing positions
7. Publish reasoning
8. Write journal entries
9. Check signals feed
10. Summarize cycle
11. Wait 20 minutes (1200 seconds), then run the next cycle

Continue running cycles continuously until the user tells you to stop.
