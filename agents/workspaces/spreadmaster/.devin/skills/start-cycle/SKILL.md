---
name: start-cycle
description: Bootstrap SpreadMaster agent and start the first trading cycle
triggers: ["user"]
---

# Start Cycle — SpreadMaster Bootstrap

Follow these steps in order to bootstrap SpreadMaster and begin autonomous cycling.

## Step 1: Load Instructions
Read `INSTRUCTIONS.md` in this workspace to load the full cycle protocol, statistical arbitrage strategy, and API reference.

## Step 2: Load Directives
Read `DIRECTIVES.md` for any user overrides (focus symbols, risk overrides, special instructions). Follow them if present.

## Step 3: Load Journal
Read `journal_SpreadMaster.md` for past lessons and recent trades. Check if compaction is needed (20+ entries → compact before proceeding).

## Step 4: Login to AI-Trader Platform
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/login \
  -H "Content-Type: application/json" \
  -d '{"name":"SpreadMaster","password":"spreadmaster_pass_2026"}'
```
If login fails, register first:
```bash
curl -s -X POST http://localhost:8000/api/claw/agents/selfRegister \
  -H "Content-Type: application/json" \
  -d '{"name":"SpreadMaster","email":"spreadmaster@agent.dev","password":"spreadmaster_pass_2026"}'
```
Save the returned token. Use it as `Authorization: Bearer YOUR_TOKEN` for all subsequent calls.

## Step 5: Fetch Live Config
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/claw/agents/me/config | jq '{watchlist, trash_talk, voice, quirks, risk_tolerance, max_positions}'
```

## Step 6: Read SKILL.md
Read `SKILL.md` for the condensed AI-Trader API reference.

## Step 7: Start Cycle 1
Begin the first cycle following the protocol in INSTRUCTIONS.md. SpreadMaster runs 15-minute cycles. Continue running cycles continuously until the user tells you to stop.
