"""Persistence helpers for StockBoy temporary runner overrides."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from database import get_db_connection
from routes_shared import utc_now_iso_z
from stockboy_policy import CONTROLLED_RUNNERS, validate_override


def _encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def create_override(runner_key: str, field_path: str, new_value: Any, rationale: str, expires_in_minutes: int) -> dict:
    validate_override(runner_key, field_path, new_value)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=expires_in_minutes)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT new_value_json, baseline_version FROM stockboy_overrides
           WHERE runner_key = ? AND field_path = ? AND status = 'active'
           ORDER BY created_at DESC LIMIT 1""",
        (runner_key, field_path),
    )
    previous = cursor.fetchone()
    previous = dict(previous) if previous else None
    old_value = json.loads(previous["new_value_json"]) if previous and previous.get("new_value_json") else None
    baseline = previous.get("baseline_version") if previous else None
    if not baseline:
        baseline = "default-unversioned"
    cursor.execute(
        """INSERT INTO stockboy_overrides
           (runner_key, field_path, old_value_json, new_value_json, baseline_version,
            rationale, author, status, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'stockboy', 'active', ?, ?)""",
        (runner_key, field_path, _encode(old_value), _encode(new_value), baseline,
         rationale[:2000], expires.isoformat().replace("+00:00", "Z"), utc_now_iso_z()),
    )
    override_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"success": True, "override_id": override_id, "expires_at": expires.isoformat().replace("+00:00", "Z")}


def reset_overrides(runner_key: Optional[str], reason: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    if runner_key:
        cursor.execute(
            """UPDATE stockboy_overrides SET status = 'rolled_back', rolled_back_at = ?, rollback_reason = ?
               WHERE runner_key = ? AND status = 'active'""",
            (utc_now_iso_z(), reason[:500], runner_key),
        )
    else:
        cursor.execute(
            """UPDATE stockboy_overrides SET status = 'rolled_back', rolled_back_at = ?, rollback_reason = ?
               WHERE status = 'active'""",
            (utc_now_iso_z(), reason[:500]),
        )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return {"success": True, "rolled_back": count, "reason": reason}


def expire_overrides() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE stockboy_overrides SET status = 'expired', rolled_back_at = ?, rollback_reason = 'expired'
           WHERE status = 'active' AND expires_at <= ?""",
        (utc_now_iso_z(), utc_now_iso_z()),
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def apply_active_overrides(agent_name: str, effective: dict[str, Any]) -> dict[str, Any]:
    """Apply active temporary overrides to an in-memory effective config.

    Source defaults remain untouched. Expired overrides are ignored; the
    manager marks them expired during its next cycle.
    """
    runner_key = next((key for key, name in CONTROLLED_RUNNERS.items() if name == agent_name), None)
    if not runner_key:
        return effective

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT field_path, new_value_json FROM stockboy_overrides
           WHERE runner_key = ? AND status = 'active'
             AND (expires_at IS NULL OR expires_at > ?)
           ORDER BY created_at ASC""",
        (runner_key, utc_now_iso_z()),
    )
    rows = cursor.fetchall()
    conn.close()

    result = json.loads(json.dumps(effective, default=str))
    for row in rows:
        data = dict(row)
        try:
            value = json.loads(data["new_value_json"])
        except (TypeError, ValueError):
            continue
        target = result
        parts = str(data["field_path"]).split(".")
        for part in parts[:-1]:
            current = target.get(part)
            if not isinstance(current, dict):
                current = {}
                target[part] = current
            target = current
        target[parts[-1]] = value
    return result
