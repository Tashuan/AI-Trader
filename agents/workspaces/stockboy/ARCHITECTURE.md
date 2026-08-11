# StockBoy Agent — Architecture & Design

## Overview

StockBoy is the platform's supervisory AI management brain. It is not a strategy trader, signal generator, or portfolio-entry agent. Its job is constant, disciplined overwatch of the paper trading platform and its three deterministic runners.

- **Identity:** Platform supervisor (auto-provisioned, no login required)
- **Mode:** Paper-only — never creates new entries
- **Controlled runners:** BlitzRunner, CryptoRunner, ScalpRunner
- **Cycle interval:** 60 seconds (configurable via `STOCKBOY_POLL_INTERVAL`)
- **Authority:** Reduce, protect, or cancel existing exposure only

---

## Hybrid Architecture

StockBoy runs as a **hybrid** agent with two coexisting layers:

### 1. Backend deterministic loop (always running)

A Python daemon thread auto-starts on app launch (`main.py` startup event). It:

- Builds platform snapshots every 60 seconds
- Detects risk anomalies (missing protection, stale prices, stale orders)
- Expires stale runner overrides
- Writes commentary and journal entries to the database
- Provides the API surface (`/api/stockboy/*`) that the AI session calls

This loop does **not** use LLM reasoning. It is the reliable, always-on safety net.

**Key files:**
- `service/server/stockboy_manager.py` — lifecycle, thread, cycle loop
- `service/server/stockboy_service.py` — snapshot builder, action executor, commentary/journal writers
- `service/server/stockboy_policy.py` — deterministic policy guardrails
- `service/server/stockboy_provision.py` — auto-provisioning at startup
- `service/server/routes_stockboy.py` — FastAPI route handlers

### 2. Devin AI session (manual start)

When the operator runs `@skills:start-cycle` in the Devin workspace, the AI agent:

- Reads workspace instructions (`INSTRUCTIONS.md`, `PREFLIGHT.md`, `DIRECTIVES.md`)
- Loads the pre-provisioned supervisor token from `.supervisor_token`
- Fetches snapshots via `curl` and reasons about what it sees
- Proposes and executes allowed protective actions via `/api/stockboy/action`
- Writes journal entries and publishes commentary
- Governs the platform with AI judgment layered on top of the deterministic loop

Both layers coexist. The deterministic policy layer is always authoritative.

---

## Authority Model

### Never allowed

- Create a new position or entry (`buy`, `short`, `enter`, `open_position`)
- Increase quantity, average in, pyramid, or hedge by opening a new position
- Use live broker/MCP execution or change live-trading mode
- Directly mutate the database, runner state files, configs, credentials, or source code
- Act on agents/runners outside the controlled allowlist
- Infer that a missing field means zero, safe, healthy, or permission granted
- Loosen a stop to avoid realizing a loss

### Allowed (only through `/api/stockboy/action`)

| Action type | Description |
|---|---|
| `close_position` | Fully close an existing controlled position |
| `partial_close` | Reduce quantity on an existing controlled position |
| `set_stop` | Set or tighten a stop-loss on an existing position |
| `set_target` | Set a take-profit target on an existing position |
| `set_trailing` | Set trailing stop parameters on an existing position |
| `cancel_order` | Cancel a stale pending order belonging to a controlled runner |

Every action must be paper-only, bounded, justified, idempotent, and verified by a fresh snapshot.

---

## Policy Enforcement

All actions pass through `validate_action()` in `stockboy_policy.py` before execution. The policy layer enforces:

### Hard gates

| Check | Category | Behavior |
|---|---|---|
| Kill switch engaged | `kill_switch` | All actions blocked |
| Supervisor disabled | `disabled` | All actions blocked |
| Actions disabled | `actions_disabled` | All actions blocked |
| Not paper mode | `live_mode` | All actions blocked |
| Forbidden action type | `no_entry` | Entry actions rejected |
| Unknown action type | `unknown_action` | Non-allowlisted actions rejected |
| Non-controlled runner | `unauthorized_target` | Actions on unlisted runners rejected |
| Position not owned by runner | `ownership_mismatch` | Cross-runner actions rejected |
| Zero quantity position | `empty_position` | Actions on closed positions rejected |
| Stale or missing price | `stale_price` | Close/partial_close blocked without fresh price |
| Invalid partial close quantity | `invalid_quantity` | Must be positive and less than total |
| Stop loosened | `stop_loosened` | Stops may only be tightened, never loosened |
| Stop above entry (long) / below entry (short) | `invalid_stop` | Stops must be on the protective side |
| Order not found | `missing_target` | Cancel on nonexistent order rejected |
| Order wrong owner | `ownership_mismatch` | Cancel on another runner's order rejected |
| Order not pending | `invalid_order_state` | Cancel on filled/expired order rejected |
| Position in cooldown | `cooldown` | Repeated adjustments within cooldown blocked |

### Configurable thresholds (env-overridable)

| Setting | Env var | Default |
|---|---|---|
| Max total adjustment notional | `STOCKBOY_MAX_ADJUSTMENT_NOTIONAL` | $50,000 |
| Max actions per cycle | `STOCKBOY_MAX_ACTIONS_PER_CYCLE` | 10 |
| Max actions per day | `STOCKBOY_MAX_ACTIONS_PER_DAY` | 100 |
| Max partial close % | `STOCKBOY_MAX_PARTIAL_CLOSE_PCT` | 100% |
| Min residual quantity | `STOCKBOY_MIN_RESIDUAL_QTY` | 0 |
| Stale price max age | `STOCKBOY_STALE_PRICE_AGE_SECONDS` | 300s |
| Stop tighten only | `STOCKBOY_STOP_TIGHTEN_ONLY` | true |
| Cooldown per position | `STOCKBOY_COOLDOWN_SECONDS` | 60s |
| Daily loss halt % | `STOCKBOY_DAILY_LOSS_HALT_PCT` | 5% |
| Max gross exposure % | `STOCKBOY_MAX_GROSS_EXPOSURE_PCT` | 100% |
| Pending order stale minutes | `STOCKBOY_PENDING_ORDER_STALE_MINUTES` | 60 |
| Autostart | `STOCKBOY_AUTOSTART` | true |
| Poll interval | `STOCKBOY_POLL_INTERVAL` | 60 |

---

## API Surface

All routes are registered in `routes_stockboy.py` and require the `STOCKBOY_SUPERVISOR_CAPABILITY` (granted by the `supervisor` role).

### Read endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/stockboy/status` | Supervisor state: enabled, mode, kill switch, cycle count, controlled runners |
| GET | `/api/stockboy/snapshot` | Full platform snapshot: portfolio, runners, positions, orders, anomalies, recent actions |

### Action endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/stockboy/action` | Submit a protective action (close, set stop, cancel order, etc.) |
| POST | `/api/stockboy/start` | Start the deterministic supervisor loop |
| POST | `/api/stockboy/stop` | Stop the loop (does not stop runners or close positions) |
| POST | `/api/stockboy/enable` | Enable/disable supervisor actions |
| POST | `/api/stockboy/kill-switch` | Engage/disengage the emergency kill switch |
| POST | `/api/stockboy/override` | Create a temporary bounded runner config override |
| POST | `/api/stockboy/override/reset` | Reset one or all runner overrides to defaults |

### Arena lifecycle endpoints (admin-only)

| Method | Path | Description |
|---|---|---|
| POST | `/api/arena/stockboy/start` | Start StockBoy from the arena UI |
| POST | `/api/arena/stockboy/stop` | Stop StockBoy from the arena UI |
| GET | `/api/arena/stockboy/status` | Get StockBoy status from the arena UI |

---

## Cycle Protocol

The AI session follows this protocol each cycle:

1. **Load persistent context** — Read `PREFLIGHT.md`, `DIRECTIVES.md`, and `journal_StockBoy.md`. Compact the journal if needed.
2. **Establish service health** — Fetch `/status` and `/snapshot` with `curl -sf` and `jq`. If data is stale, partial, or contradictory: do not act, record the failure, retry next cycle.
3. **Build the fact sheet** — Extract mode, kill switch, cycle age, data freshness, runner health, positions, orders, portfolio metrics, anomalies, and recent actions. Keep facts separate from interpretation.
4. **Classify conditions** — Label each issue as `healthy`, `watch`, `maintenance`, `risk`, `critical`, or `unknown`. For non-healthy conditions, state evidence, impact, confidence, next check, and allowed response.
5. **Decide conservatively** — Use deterministic policy first. The best response is often `no action`. A hard protective condition beats the runner's thesis. Never loosen a stop. Never take repeated opposing actions.
6. **Execute, then verify** — Write an internal action intent record with rationale, policy rule, and idempotency key. Submit to `/api/stockboy/action`. Fetch a fresh snapshot. Confirm the postcondition. Confirm no new position was created.
7. **Communicate and journal** — Post concise commentary only for meaningful changes. Append durable lessons to the journal.
8. **Wait and recover** — Respect the configured cadence. Use bounded backoff on API failures. Stop immediately if the operator asks, the kill switch is engaged, or identity/mode is wrong.

### Stop conditions

The loop stops action execution and reports if:
- The API is unavailable or the snapshot is stale/partial/contradictory
- Mode is not paper, identity is wrong, or the kill switch is engaged
- Target ownership or position quantity is unclear
- An action result cannot be verified
- The operator tells it to stop

Stopping the loop does **not** stop a runner or close a position.

---

## Authentication & Provisioning

### Auto-provisioning

At app startup (`main.py`), `provision_supervisor()`:

1. Creates or updates a `StockBoy` agent row with `role='supervisor'`
2. Generates a `secrets.token_urlsafe(32)` API token
3. Writes the token to `agents/workspaces/stockboy/.supervisor_token` with `0600` permissions
4. The token file is gitignored

### Capability model

The `supervisor` role grants `STOCKBOY_SUPERVISOR_CAPABILITY` via `permissions.py`:

```python
ROLE_CAPABILITIES = {
    "supervisor": {STOCKBOY_SUPERVISOR_CAPABILITY},
}
```

All `/api/stockboy/*` routes call `require_capability(authorization, STOCKBOY_SUPERVISOR_CAPABILITY)`. Arena lifecycle routes require `require_admin()`.

---

## Idempotency

Every action requires a unique `idempotency_key`. The system:

1. Checks `stockboy_actions` for an existing row with that key
2. If found, computes a SHA-256 `request_hash` of the current payload and compares it to the stored hash
3. If the hash matches, returns the original result (idempotent replay)
4. If the hash differs, rejects with "Idempotency key reused with a different request payload"
5. If no existing row, inserts a new action with the request hash and proceeds

This prevents both accidental duplicate execution and silent payload substitution.

---

## Database Schema

Six tables under the `stockboy_` prefix:

| Table | Purpose |
|---|---|
| `stockboy_state` | Single-row supervisor state (mode, enabled, kill switch, cycle count, heartbeat) |
| `stockboy_cycles` | Per-cycle snapshot and outcome records |
| `stockboy_observations` | Anomalies/observations produced during a cycle |
| `stockboy_actions` | Command audit trail (idempotency key, request hash, status, result) |
| `stockboy_overrides` | Temporary runner config overrides with baseline and rollback |
| `stockboy_journal` | Structured per-runner maintenance journal entries |
| `stockboy_commentary` | Dashboard commentary events (deduplicated) |

The `positions` table includes a `current_price_updated_at` column that is stamped whenever the batch price updater refreshes prices. This enables server-side stale-price detection for close/partial_close actions.

---

## Risk Anomaly Detection

The deterministic loop detects these anomalies per snapshot:

| Anomaly | Trigger | Severity |
|---|---|---|
| `missing_protection` | Position has no stop-loss price | warning |
| `stale_price` | Position has no current price | warning |
| `stale_order` | Pending order age exceeds `pending_order_stale_minutes` | warning |

A position with a take-profit but no stop-loss is flagged as `missing_protection` — downside protection is the required minimum.

---

## Snapshot Structure

The `/api/stockboy/snapshot` response contains:

- **`supervisor`** — enabled, actions_enabled, mode, kill_switch, running, cycle count, heartbeats, controlled runners
- **`portfolio`** — total equity, cash, unrealized P&L, gross/net exposure, position/order counts, override count, data freshness
- **`runners`** — per-runner health: running state, cash, portfolio value, open positions, unrealized P&L, active overrides
- **`positions`** — per-position detail: symbol, side, quantity, entry/current price, price age, P&L, stop/target/trailing, missing protection, stale price
- **`pending_orders`** — per-order detail: symbol, side, stop/limit price, quantity, status, age, stale flag
- **`overrides`** — active temporary config overrides with expiry
- **`recent_actions`** — last 20 actions with status and result
- **`recent_observations`** — recent anomaly detections
- **`recent_commentary`** — recent dashboard messages
- **`risk_anomalies`** — current detected anomalies

---

## Workspace Structure

```
stockboy/
├── .devin/
│   ├── rules/
│   │   └── stockboy-identity.md          # Always-on identity & boundary rules
│   └── skills/
│       └── start-cycle/
│           └── SKILL.md                  # Bootstrap skill
├── .supervisor_token                     # Pre-provisioned API token (gitignored)
├── INSTRUCTIONS.md                       # Full cycle protocol
├── PREFLIGHT.md                          # Non-negotiable safety checklist
├── DIRECTIVES.md                         # Operator overrides and priorities
├── API_REFERENCE.md                      # Least-privilege API reference
├── ARCHITECTURE.md                       # This document
├── journal_StockBoy.md                   # Supervisor journal (persistent state)
└── README.md                             # Quick start guide
```

---

## Frontend

The arena UI includes a StockBoy dashboard (`StockBoyDashboard.tsx`) that:

- Polls `/api/stockboy/status` and `/api/stockboy/snapshot` every 10 seconds
- Displays supervisor state, portfolio overview, runner health, positions, pending orders, anomalies, and recent actions
- Provides start/stop/kill-switch controls (admin-only)
- Shows data freshness and stale warnings

The hook `useStockBoyData.ts` manages polling, auth headers, and control actions.

---

## Test Coverage

`service/server/tests/test_stockboy.py` covers:

- Policy rejection of entry actions (`no_entry`)
- Policy rejection of non-controlled owner (`ownership_mismatch`)
- Policy rejection of stale price for reductions (`stale_price`)
- Policy rejection of loosened long stop (`stop_loosened`)
- Cancel order with wrong owner (`ownership_mismatch`)
- Cancel order on non-pending order (`invalid_order_state`)
- Cancel order on missing order (`missing_target`)
- Cancel order with correct owner (accepted)
- Supervisor capability required for actions (403)
- Supervisor auth required for status (401)
- Supervisor auth required for snapshot (401)
- Idempotent action replay
