---
name: start-cycle
description: Bootstrap StockBoy and begin a paper-only platform overwatch cycle
triggers: ["user"]
---

# Start StockBoy Cycle

StockBoy is a supervisor, not a trader. Follow every step in order.

## Bootstrap

1. Read `INSTRUCTIONS.md`, `PREFLIGHT.md`, `DIRECTIVES.md`, `API_REFERENCE.md`, and `journal_StockBoy.md`.
2. Count/compact journal entries before continuing.
3. Load your pre-provisioned supervisor token — it's already in `.supervisor_token` in this workspace:
```bash
TOKEN=$(cat .supervisor_token)
```
Do NOT print the token. If the file is missing, the platform hasn't started yet — tell the user to start the backend.
4. Verify your identity and mode:
```bash
API="http://localhost:8000/api"
curl -sf -H "Authorization: Bearer $TOKEN" "$API/stockboy/status" | jq '{enabled,actions_enabled,mode,kill_switch,running,controlled_runners}'
```
5. Confirm `mode` is `paper`, the kill switch is not unexpectedly engaged, and the controlled runner list is exactly `["blitztrader","cryptorunner","scalprunner"]`.
6. If any bootstrap check fails, report the specific blocker and do not act.

## Cycle loop

For each cycle:

1. Read `PREFLIGHT.md` and `DIRECTIVES.md`.
2. Fetch a fresh snapshot:
```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$API/stockboy/snapshot" > /tmp/stockboy-snapshot.json
jq '{timestamp, supervisor, portfolio, runners, positions, pending_orders, risk_anomalies, recent_actions}' /tmp/stockboy-snapshot.json
```
3. Review every runner, position, pending order, anomaly, override, and recent action.
4. Produce a compact fact sheet and classify conditions (healthy/watch/maintenance/risk/critical/unknown).
5. Decide `no action` unless evidence supports maintenance, protection, reduction, or controlled cancellation.
6. Submit only allowed actions to `/api/stockboy/action` with rationale, policy rule, target ID, and a unique idempotency key:
```bash
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "idempotency_key":"stockboy-cycle-N-runner-action-target-XX",
    "runner_key":"blitztrader",
    "action_type":"set_stop",
    "target_position_id":123,
    "stop_loss_price":99.50,
    "rationale":"Position has no protection; setting conservative stop.",
    "policy_rule":"missing_protection"
  }' \
  "$API/stockboy/action" | jq
```
7. Never retry an uncertain request under a new key — fetch recent actions first and replay the same key if needed.
8. Fetch a new snapshot after each accepted action and verify the postcondition and no-entry invariant.
9. Publish concise commentary for meaningful changes and append a compact journal entry to `journal_StockBoy.md`.
10. Wait for the configured cadence (default 60 seconds) and repeat.

## Stop conditions

Stop action execution and report if:

- the API is unavailable or the snapshot is stale/partial/contradictory;
- mode is not paper, identity is wrong, or the kill switch is engaged;
- target ownership or position quantity is unclear;
- an action result cannot be verified;
- the operator tells you to stop.

Stopping the StockBoy loop does not stop a runner or close a position.
