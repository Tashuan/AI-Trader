---
name: stockboy-platform-api
description: Least-privilege API reference for StockBoy's paper-only supervisory cycles
---

# StockBoy API Reference

Base URL:

```bash
API="http://localhost:8000/api"
```

All authenticated calls require:

```bash
-H "Authorization: Bearer $TOKEN"
```

Use `curl -sf` and guard every response. Prefer `jq` field extraction. Never put the token in a journal, thought, discussion, or error message.

## Authentication

Use the platform's configured StockBoy supervisor credentials. Do not print or persist the password. If authentication fails, stop and report; do not use another agent's token.

## Read-only supervisor endpoints

```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$API/stockboy/status" \
  | jq '{enabled,actions_enabled,mode,kill_switch,running,last_cycle_at,last_heartbeat_at,last_error,cycles_run,controlled_runners}'

curl -sf -H "Authorization: Bearer $TOKEN" "$API/stockboy/snapshot" \
  | jq '{timestamp,supervisor,portfolio,runners,positions,pending_orders,overrides,recent_actions,risk_anomalies}'
```

The snapshot is the primary source of truth for an overwatch cycle. A successful HTTP response with stale, partial, empty, or contradictory data is not a safe snapshot.

## Adjustment endpoint

Only these action types are allowed:

- `close_position`
- `partial_close`
- `set_stop`
- `set_target`
- `set_trailing`
- `cancel_order`

Example protection adjustment:

```bash
curl -sf -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key":"stockboy-cycle-12-position-123-set-stop-a1b2",
    "runner_key":"blitztrader",
    "action_type":"set_stop",
    "target_position_id":123,
    "stop_loss_price":99.50,
    "rationale":"Position has no protection; setting the configured protective stop.",
    "policy_rule":"missing_protection"
  }' \
  "$API/stockboy/action" | jq
```

Rules:

- `runner_key` must be `blitztrader`, `cryptorunner`, or `scalprunner`.
- Position/order IDs must belong to the named runner.
- `close_position` uses the whole current quantity; `partial_close` requires a smaller positive quantity.
- Use a unique key for a new intent. If a request times out, inspect recent actions before retrying; never create a second key for the same uncertain intent.
- Always fetch a fresh snapshot after an accepted action and verify the postcondition.

## Supervisor controls

```bash
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":false}' "$API/stockboy/enable"

curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"engaged":true,"reason":"Data integrity incident"}' "$API/stockboy/kill-switch"
```

Use the kill switch when action safety cannot be established. Stopping StockBoy does not stop runners or close positions.

## Forbidden endpoints

Never call these from StockBoy:

- `/api/signals/realtime`
- `/api/signals/pending` to create entries
- broker/live/MCP execution tools
- direct database connections
- runner source/config file mutation

Thoughts/discussions are commentary only; they do not authorize an action.
