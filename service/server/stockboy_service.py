"""StockBoy snapshot, audit, and paper-adjustment services."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from database import get_db_connection
from stockboy_models import (
    StockBoyActionRequest, StockBoyActionResponse, StockBoyActionDetail,
    StockBoyCommentaryEntry, StockBoyJournalEntry, StockBoyOverrideDetail,
    StockBoyPendingOrderDetail, StockBoyPositionDetail, StockBoyPortfolioOverview,
    StockBoyRiskAnomaly, StockBoyRunnerHealth, StockBoySnapshot,
    StockBoySupervisorStatus, StockBoyObservationDetail,
)
from stockboy_policy import (
    CONTROLLED_RUNNERS, OBSERVED_RUNNERS, PolicyConfig, PolicyViolation, validate_action,
    validate_override, CooldownTracker, action_cooldown_key,
)
from routes_shared import utc_now_iso_z


RUNNER_AGENT_NAMES = tuple(CONTROLLED_RUNNERS.values())
# Phase 8: Include observed runners in agent name list for position display
ALL_RUNNER_AGENT_NAMES = tuple(list(CONTROLLED_RUNNERS.values()) + list(OBSERVED_RUNNERS.values()))


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        raw = timestamp.replace("Z", "+00:00")
        return max(0.0, (_now() - datetime.fromisoformat(raw)).total_seconds())
    except (TypeError, ValueError):
        return None


def _runner_key(agent_name: str) -> str:
    return next((key for key, name in CONTROLLED_RUNNERS.items() if name == agent_name), agent_name.lower())


def _agent_ids(cursor) -> dict[str, int]:
    placeholders = ",".join("?" for _ in RUNNER_AGENT_NAMES)
    cursor.execute(
        f"SELECT id, name FROM agents WHERE name IN ({placeholders})",
        RUNNER_AGENT_NAMES,
    )
    return {row["name"]: int(row["id"]) for row in cursor.fetchall()}


def _status_row(cursor) -> dict[str, Any]:
    cursor.execute("SELECT * FROM stockboy_state WHERE id = 1")
    row = cursor.fetchone()
    if row:
        return _as_dict(row)
    now = utc_now_iso_z()
    cursor.execute(
        """INSERT INTO stockboy_state (id, mode, enabled, actions_enabled, updated_at)
           VALUES (1, 'paper', 0, 1, ?)""",
        (now,),
    )
    return {
        "id": 1, "mode": "paper", "enabled": 0, "actions_enabled": 1,
        "kill_switch": 0, "cycles_run": 0,
    }


def get_status() -> StockBoySupervisorStatus:
    conn = get_db_connection()
    cursor = conn.cursor()
    row = _status_row(cursor)
    conn.commit()
    conn.close()
    return StockBoySupervisorStatus(
        enabled=bool(row.get("enabled")),
        actions_enabled=bool(row.get("actions_enabled", 1)),
        mode=row.get("mode") or "paper",
        kill_switch=bool(row.get("kill_switch")),
        running=False,
        agent_id=row.get("agent_id"),
        last_cycle_at=row.get("last_cycle_at"),
        next_cycle_at=row.get("next_cycle_at"),
        last_heartbeat_at=row.get("last_heartbeat_at"),
        last_error=row.get("last_error"),
        cycles_run=int(row.get("cycles_run") or 0),
        controlled_runners=list(CONTROLLED_RUNNERS),
    )


def set_state(**fields: Any) -> StockBoySupervisorStatus:
    allowed = {
        "enabled", "actions_enabled", "mode", "kill_switch", "last_cycle_at",
        "next_cycle_at", "last_error", "last_heartbeat_at", "cycles_run", "agent_id",
    }
    _BOOL_FIELDS = {"enabled", "actions_enabled", "kill_switch"}
    updates = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in _BOOL_FIELDS:
            updates[key] = int(bool(value))
        else:
            updates[key] = value
    updates["updated_at"] = utc_now_iso_z()
    conn = get_db_connection()
    cursor = conn.cursor()
    _status_row(cursor)
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor.execute(f"UPDATE stockboy_state SET {assignments} WHERE id = 1", tuple(updates.values()))
    conn.commit()
    conn.close()
    return get_status()


def _runner_health(cursor, agent_ids: dict[str, int]) -> list[StockBoyRunnerHealth]:
    result: list[StockBoyRunnerHealth] = []
    try:
        from bot_manager import get_all_bot_statuses
        bot_statuses = get_all_bot_statuses()
    except Exception:
        bot_statuses = {}
    bot_keys = {
        "blitztrader": "blitztrader-runner",
        "cryptorunner": "cryptorunner-runner",
        "scalprunner": "scalprunner-runner",
        "fencebarrunner": "fencebarrunner-runner",
        # Phase 8: observed runner bot keys
        "orbrunner": "orb-runner",
    }
    # Phase 8: Include both controlled and observed runners
    all_runners = {**CONTROLLED_RUNNERS, **OBSERVED_RUNNERS}
    for runner_key, agent_name in all_runners.items():
        agent_id = agent_ids.get(agent_name)
        if not agent_id:
            result.append(StockBoyRunnerHealth(runner_key=runner_key, agent_name=agent_name))
            continue
        cursor.execute("SELECT cash FROM agents WHERE id = ?", (agent_id,))
        agent = _as_dict(cursor.fetchone())
        cursor.execute("""SELECT quantity, entry_price, current_price
                         FROM positions WHERE agent_id = ?""", (agent_id,))
        positions = cursor.fetchall()
        pnl = 0.0
        position_value = 0.0
        for pos in positions:
            qty = abs(float(pos["quantity"] or 0))
            entry = float(pos["entry_price"] or 0)
            current = float(pos["current_price"] or entry)
            position_value += qty * current
            pnl += (current - entry) * qty if float(pos["quantity"] or 0) >= 0 else (entry - current) * qty
        cursor.execute(
            "SELECT COUNT(*) AS count FROM stockboy_overrides WHERE runner_key = ? AND status = 'active'",
            (runner_key,),
        )
        overrides = _as_dict(cursor.fetchone())
        bot = bot_statuses.get(bot_keys.get(runner_key, ""), {})
        # Phase 8: Mark observed runners differently
        is_observed = runner_key in OBSERVED_RUNNERS
        result.append(StockBoyRunnerHealth(
            runner_key=runner_key,
            agent_name=agent_name,
            agent_id=agent_id,
            running=bool(bot.get("running")),
            last_error=bot.get("last_error"),
            cash=float(agent.get("cash") or 0),
            portfolio_value=float(agent.get("cash") or 0) + position_value,
            open_positions=len(positions),
            unrealized_pnl=pnl,
            active_overrides=int((overrides or {}).get("count") or 0),
            # Phase 8: observed runners have no overrides
            latest_assessment="observe-only" if is_observed else None,
        ))
    return result


def _position_details(cursor, agent_ids: dict[str, int]) -> list[StockBoyPositionDetail]:
    # Phase 8: Include observed runner positions for Arena visibility
    placeholders = ",".join("?" for _ in ALL_RUNNER_AGENT_NAMES)
    cursor.execute(
        f"""SELECT p.*, a.name AS agent_name
            FROM positions p JOIN agents a ON a.id = p.agent_id
            WHERE a.name IN ({placeholders}) ORDER BY p.opened_at DESC""",
        ALL_RUNNER_AGENT_NAMES,
    )
    result = []
    for row in cursor.fetchall():
        data = dict(row)
        qty = float(data.get("quantity") or 0)
        entry = float(data.get("entry_price") or 0)
        current = data.get("current_price")
        current = float(current) if current is not None else None
        side = data.get("side") or ("short" if qty < 0 else "long")
        pnl = None
        pnl_pct = None
        if current is not None and entry:
            pnl = (current - entry) * abs(qty) if side == "long" else (entry - current) * abs(qty)
            pnl_pct = ((current - entry) / entry * 100) if side == "long" else ((entry - current) / entry * 100)
        result.append(StockBoyPositionDetail(
            position_id=int(data["id"]), agent_id=int(data["agent_id"]),
            agent_name=data["agent_name"], runner_key=_runner_key(data["agent_name"]),
            symbol=data.get("symbol") or "", market=data.get("market") or "",
            side=side, quantity=qty, entry_price=entry, current_price=current,
            current_price_age_seconds=_age(data.get("current_price_updated_at")),
            unrealized_pnl=pnl, unrealized_pnl_pct=pnl_pct,
            stop_loss_price=data.get("stop_loss_price"), take_profit_price=data.get("take_profit_price"),
            trailing_sl_pct=data.get("trailing_sl_pct"), trailing_activation_pct=data.get("trailing_activation_pct"),
            opened_at=data.get("opened_at"), age_seconds=_age(data.get("opened_at")),
            missing_protection=(data.get("stop_loss_price") is None),
            stale_price=current is None,
        ))
    return result


def _pending_orders(cursor) -> list[StockBoyPendingOrderDetail]:
    placeholders = ",".join("?" for _ in RUNNER_AGENT_NAMES)
    cursor.execute(
        f"""SELECT o.*, a.name AS agent_name FROM pending_orders o
            JOIN agents a ON a.id = o.agent_id
            WHERE a.name IN ({placeholders}) AND o.status = 'PENDING'
            ORDER BY o.created_at DESC""",
        RUNNER_AGENT_NAMES,
    )
    result = []
    for row in cursor.fetchall():
        data = dict(row)
        age = _age(data.get("created_at"))
        result.append(StockBoyPendingOrderDetail(
            order_id=int(data["id"]), agent_id=int(data["agent_id"]), agent_name=data["agent_name"],
            runner_key=_runner_key(data["agent_name"]), symbol=data.get("symbol") or "",
            market=data.get("market") or "", side=data.get("side") or "",
            stop_price=float(data.get("stop_price") or 0), limit_price=data.get("limit_price"),
            quantity=float(data.get("quantity") or 0), status=data.get("status") or "PENDING",
            created_at=data.get("created_at") or "", expires_at=data.get("expires_at") or "",
            age_seconds=age, stale=bool(age and age > PolicyConfig.from_env().pending_order_stale_minutes * 60),
        ))
    return result


def _recent(cursor, table: str, model, limit: int = 20):
    cursor.execute(f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT ?", (limit,))
    result = []
    for row in cursor.fetchall():
        data = dict(row)
        identifier_map = {
            "stockboy_actions": "action_id",
            "stockboy_observations": "observation_id",
            "stockboy_journal": "entry_id",
            "stockboy_commentary": "commentary_id",
            "stockboy_overrides": "override_id",
        }
        identifier = identifier_map.get(table)
        if identifier:
            data[identifier] = data.pop("id", None)
        if "parameters_json" in data:
            data["parameters"] = _loads(data.pop("parameters_json"), {})
        if "result_json" in data:
            data["result"] = _loads(data.pop("result_json"), {})
        if "metadata_json" in data:
            data["metadata"] = _loads(data.pop("metadata_json"), {})
        if "new_value_json" in data:
            data["new_value"] = _loads(data.pop("new_value_json"), None)
        if "old_value_json" in data:
            data["old_value"] = _loads(data.pop("old_value_json"), None)
        try:
            result.append(model(**data))
        except Exception:
            continue
    return result


def build_snapshot(*, manager_status: Optional[StockBoySupervisorStatus] = None, running: bool = False) -> StockBoySnapshot:
    conn = get_db_connection()
    cursor = conn.cursor()
    row = _status_row(cursor)
    agent_ids = _agent_ids(cursor)
    positions = _position_details(cursor, agent_ids)
    pending = _pending_orders(cursor)
    runners = _runner_health(cursor, agent_ids)
    overrides = _recent(cursor, "stockboy_overrides", StockBoyOverrideDetail)
    actions = _recent(cursor, "stockboy_actions", StockBoyActionDetail)
    observations = _recent(cursor, "stockboy_observations", StockBoyObservationDetail)
    commentary = _recent(cursor, "stockboy_commentary", StockBoyCommentaryEntry)
    journal = _recent(cursor, "stockboy_journal", StockBoyJournalEntry, 10)
    conn.close()

    total_cash = sum(item.cash for item in runners)
    total_equity = sum(item.portfolio_value for item in runners)
    total_pnl = sum(item.unrealized_pnl or 0 for item in runners)
    gross = sum(abs((item.current_price or item.entry_price) * item.quantity) for item in positions)
    anomalies: list[StockBoyRiskAnomaly] = []
    for position in positions:
        if position.missing_protection:
            anomalies.append(StockBoyRiskAnomaly(
                category="missing_protection", severity="warning",
                message=f"{position.agent_name} position {position.symbol} has no stop-loss",
                runner_key=position.runner_key, symbol=position.symbol,
            ))
        if position.stale_price:
            anomalies.append(StockBoyRiskAnomaly(
                category="stale_price", severity="warning",
                message=f"{position.agent_name} position {position.symbol} has no current price",
                runner_key=position.runner_key, symbol=position.symbol,
            ))
    for order in pending:
        if order.stale:
            anomalies.append(StockBoyRiskAnomaly(
                category="stale_order", severity="warning",
                message=f"Pending {order.symbol} order is stale",
                runner_key=order.runner_key, symbol=order.symbol,
            ))

    status = manager_status or StockBoySupervisorStatus(
        enabled=bool(row.get("enabled")), actions_enabled=bool(row.get("actions_enabled", 1)),
        mode=row.get("mode") or "paper", kill_switch=bool(row.get("kill_switch")),
        running=running,
        last_cycle_at=row.get("last_cycle_at"), next_cycle_at=row.get("next_cycle_at"),
        last_heartbeat_at=row.get("last_heartbeat_at"), last_error=row.get("last_error"),
        cycles_run=int(row.get("cycles_run") or 0), controlled_runners=list(CONTROLLED_RUNNERS),
    )
    return StockBoySnapshot(
        timestamp=utc_now_iso_z(), supervisor=status,
        portfolio=StockBoyPortfolioOverview(
            total_equity=total_equity, total_cash=total_cash, total_unrealized_pnl=total_pnl,
            gross_exposure=gross, net_exposure=0.0, open_position_count=len(positions),
            pending_order_count=len(pending), controlled_runner_count=len(CONTROLLED_RUNNERS),
            active_override_count=len([x for x in overrides if x.status == "active"]),
            data_fresh=not any(x.stale_price for x in positions),
        ),
        runners=runners, positions=positions, pending_orders=pending, overrides=overrides,
        recent_actions=actions, recent_observations=observations, recent_commentary=commentary,
        risk_anomalies=anomalies, broader_agent_summary=[],
    )


# Per-process action cooldown; idempotency remains database-backed.
_COOLDOWNS = CooldownTracker()


def _request_hash(request: StockBoyActionRequest) -> str:
    """Compute a stable hash of the request payload for idempotency conflict detection."""
    payload = request.model_dump_json(exclude={"idempotency_key"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def execute_action(request: StockBoyActionRequest) -> StockBoyActionResponse:
    conn = get_db_connection()
    cursor = conn.cursor()
    state = _status_row(cursor)
    req_hash = _request_hash(request)
    cursor.execute("SELECT * FROM stockboy_actions WHERE idempotency_key = ?", (request.idempotency_key,))
    existing = cursor.fetchone()
    if existing:
        data = dict(existing)
        stored_hash = data.get("request_hash")
        if stored_hash and stored_hash != req_hash:
            conn.close()
            return StockBoyActionResponse(
                success=False, action_id=int(data["id"]),
                status="rejected",
                message="Idempotency key reused with a different request payload",
            )
        conn.close()
        return StockBoyActionResponse(
            success=data.get("status") == "executed", action_id=int(data["id"]),
            status=data.get("status") or "pending", message="Idempotent replay", result=_loads(data.get("result_json"), {}),
        )

    target = None
    price = None
    price_age = None
    target_order = None
    if request.target_position_id:
        cursor.execute(
            """SELECT p.*, a.name AS agent_name FROM positions p JOIN agents a ON a.id = p.agent_id WHERE p.id = ?""",
            (request.target_position_id,),
        )
        row = cursor.fetchone()
        target = dict(row) if row else None
        if target:
            price = target.get("current_price")
            price_age = _age(target.get("current_price_updated_at"))

    if request.action_type == "cancel_order" and request.target_order_id:
        cursor.execute(
            """SELECT o.*, a.name AS agent_name FROM pending_orders o
               JOIN agents a ON a.id = o.agent_id WHERE o.id = ?""",
            (request.target_order_id,),
        )
        order_row = cursor.fetchone()
        target_order = dict(order_row) if order_row else None

    try:
        validate_action(
            request, supervisor_enabled=bool(state.get("enabled")),
            actions_enabled=bool(state.get("actions_enabled", 1)),
            kill_switch=bool(state.get("kill_switch")),
            paper_only=(state.get("mode") or "paper") == "paper",
            target_position=target, current_price=float(price) if price else None,
            current_price_age_seconds=price_age,
            target_order=target_order,
        )
        if request.target_position_id and not _COOLDOWNS.check_and_record(
            action_cooldown_key(request.runner_key, request.target_position_id, request.action_type),
            PolicyConfig.from_env().cooldown_seconds_per_position,
        ):
            raise PolicyViolation("Position is in adjustment cooldown", "cooldown")
    except PolicyViolation as exc:
        cursor.execute(
            """INSERT INTO stockboy_actions
               (idempotency_key, request_hash, runner_key, action_type, target_position_id, target_order_id,
                parameters_json, rationale, policy_rule, status, error, requested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'rejected', ?, ?)""",
            (request.idempotency_key, req_hash, request.runner_key, request.action_type, request.target_position_id,
             request.target_order_id, _json(request.model_dump()), request.rationale, request.policy_rule,
             exc.reason, utc_now_iso_z()),
        )
        action_id = cursor.lastrowid
        conn.commit(); conn.close()
        return StockBoyActionResponse(success=False, action_id=action_id, status="rejected", message=exc.reason)

    now = utc_now_iso_z()
    try:
        cursor.execute(
            """INSERT INTO stockboy_actions
               (idempotency_key, request_hash, runner_key, action_type, target_position_id, target_order_id,
                parameters_json, rationale, policy_rule, status, requested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'executing', ?)""",
            (request.idempotency_key, req_hash, request.runner_key, request.action_type, request.target_position_id,
             request.target_order_id, _json(request.model_dump()), request.rationale, request.policy_rule, now),
        )
        action_id = cursor.lastrowid
        if request.action_type in ("close_position", "partial_close"):
            qty = abs(float(target["quantity"])) if request.action_type == "close_position" else float(request.quantity or 0)
            signed_qty = -qty if float(target["quantity"]) < 0 else qty
            # Decrease quantity; never insert or increase a position.
            current_qty = float(target["quantity"])
            new_qty = current_qty + qty if current_qty < 0 else current_qty - qty
            if (current_qty > 0 and new_qty < 0) or (current_qty < 0 and new_qty > 0):
                raise ValueError("Adjustment exceeds current position")
            if abs(new_qty) < 1e-12:
                cursor.execute("DELETE FROM positions WHERE id = ?", (request.target_position_id,))
            else:
                cursor.execute("UPDATE positions SET quantity = ? WHERE id = ?", (new_qty, request.target_position_id))
            # Paper cash credit mirrors sell/cover economics without opening a position.
            fill_price = float(price)
            entry = float(target["entry_price"] or fill_price)
            trade_value = fill_price * qty
            if current_qty > 0:
                credit = trade_value
            else:
                credit = (2 * entry - fill_price) * qty
            cursor.execute("UPDATE agents SET cash = cash + ? WHERE id = ?", (credit, target["agent_id"]))
        elif request.action_type in ("set_stop", "set_target", "set_trailing"):
            fields = []
            params = []
            if request.action_type == "set_stop":
                fields.append("stop_loss_price = ?"); params.append(request.stop_loss_price)
            elif request.action_type == "set_target":
                fields.append("take_profit_price = ?"); params.append(request.take_profit_price)
            else:
                fields.extend(["trailing_sl_pct = ?", "trailing_activation_pct = ?"])
                params.extend([request.trailing_sl_pct, request.trailing_activation_pct])
            params.append(request.target_position_id)
            cursor.execute(f"UPDATE positions SET {', '.join(fields)} WHERE id = ?", params)
        elif request.action_type == "cancel_order":
            cursor.execute(
                "UPDATE pending_orders SET status = 'CANCELLED' WHERE id = ? AND status = 'PENDING'",
                (request.target_order_id,),
            )
        result = {"action": request.action_type, "position_id": request.target_position_id, "verified": True}
        cursor.execute(
            "UPDATE stockboy_actions SET status = 'executed', result_json = ?, executed_at = ? WHERE id = ?",
            (_json(result), now, action_id),
        )
        conn.commit(); conn.close()
        return StockBoyActionResponse(success=True, action_id=action_id, status="executed", message="Action executed", result=result)
    except Exception as exc:
        conn.rollback()
        failed_id = action_id
        if action_id:
            cursor.execute(
                """UPDATE stockboy_actions SET status = 'failed', error = ?, executed_at = ?
                   WHERE idempotency_key = ?""",
                (str(exc), now, request.idempotency_key),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """INSERT INTO stockboy_actions
                       (idempotency_key, request_hash, runner_key, action_type, target_position_id, target_order_id,
                        parameters_json, rationale, policy_rule, status, error, requested_at, executed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)""",
                    (request.idempotency_key, req_hash, request.runner_key, request.action_type,
                     request.target_position_id, request.target_order_id, _json(request.model_dump()),
                     request.rationale, request.policy_rule, str(exc), now, now),
                )
                failed_id = cursor.lastrowid
            conn.commit()
        conn.close()
        return StockBoyActionResponse(success=False, action_id=failed_id, status="failed", message=str(exc))


def add_commentary(content: str, kind: str = "status", severity: str = "info", dedup_key: Optional[str] = None) -> None:
    conn = get_db_connection(); cursor = conn.cursor()
    if dedup_key:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        cursor.execute("SELECT id FROM stockboy_commentary WHERE dedup_key = ? AND created_at > ?", (dedup_key, cutoff))
        if cursor.fetchone():
            conn.close(); return
    cursor.execute("INSERT INTO stockboy_commentary (kind, severity, content, dedup_key) VALUES (?, ?, ?, ?)", (kind, severity, content[:2000], dedup_key))
    conn.commit(); conn.close()


def add_journal(content: str, runner_key: Optional[str] = None, entry_type: str = "observation", title: Optional[str] = None, metadata: Optional[dict] = None) -> None:
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO stockboy_journal (runner_key, entry_type, title, content, metadata_json) VALUES (?, ?, ?, ?, ?)", (runner_key, entry_type, title, content[:10000], _json(metadata or {})))
    conn.commit(); conn.close()


def add_observation(
    runner_key: Optional[str] = None,
    severity: str = "info",
    category: str = "",
    message: str = "",
    metadata: Optional[dict] = None,
    cycle_id: Optional[int] = None,
) -> None:
    """Log a StockBoy observation to the stockboy_observations table."""
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO stockboy_observations
           (cycle_id, runner_key, severity, category, message, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (cycle_id, runner_key, severity, category, message[:2000], _json(metadata or {})),
    )
    conn.commit(); conn.close()
