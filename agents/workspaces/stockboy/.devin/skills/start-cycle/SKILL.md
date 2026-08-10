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
3. Authenticate as StockBoy using configured supervisor credentials. Do not print the password or token.
4. Fetch `/api/stockboy/status` and `/api/stockboy/snapshot` with guarded `curl -sf` and bounded `jq` output.
5. Confirm `mode` is `paper`, the supervisor identity is correct, the kill switch is not unexpectedly engaged, and the controlled runner list is exact.
6. If any bootstrap check fails, report the specific blocker and do not act.

## Cycle loop

For each cycle:

1. Read preflight and directives.
2. Fetch a fresh status/snapshot.
3. Review every runner, position, pending order, anomaly, override, and recent action.
4. Produce a compact fact sheet and classify conditions.
5. Decide `no action` unless evidence supports maintenance, protection, reduction, or controlled cancellation.
6. Submit only allowed actions to `/api/stockboy/action` with rationale, policy rule, target ID, and a unique idempotency key.
7. Never retry an uncertain request under a new key.
8. Fetch a new snapshot after each accepted action and verify the postcondition and no-entry invariant.
9. Publish concise commentary for meaningful changes and append a compact journal entry.
10. Wait for the configured cadence and repeat.

## Stop conditions

Stop action execution and report if:

- the API is unavailable or the snapshot is stale/partial/contradictory;
- mode is not paper, identity is wrong, or the kill switch is engaged;
- target ownership or position quantity is unclear;
- an action result cannot be verified;
- the operator tells you to stop.

Stopping the StockBoy loop does not stop a runner or close a position.
