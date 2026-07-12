from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from typing import Any


class GuardrailViolation(ValueError):
    pass


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _utc_day_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _position_value(cursor: Any, agent_id: int) -> float:
    cursor.execute(
        """
        SELECT quantity, entry_price, current_price
        FROM positions
        WHERE agent_id = ?
        """,
        (agent_id,),
    )
    value = 0.0
    for row in cursor.fetchall():
        mark = float(row["current_price"] or row["entry_price"] or 0)
        value += abs(float(row["quantity"] or 0)) * mark
    return value


def _equity(cursor: Any, agent_id: int) -> float:
    cursor.execute("SELECT cash, deposited FROM agents WHERE id = ?", (agent_id,))
    row = cursor.fetchone()
    if not row:
        raise GuardrailViolation("Agent account not found")
    return max(0.0, float(row["cash"] or 0) + _position_value(cursor, agent_id))


def _ensure_state(cursor: Any, agent_id: int, equity: float, now: str) -> dict[str, Any]:
    day_start = _utc_day_start()
    cursor.execute(
        "SELECT day_key, starting_equity, halted, halt_reason FROM trading_risk_state WHERE agent_id = ?",
        (agent_id,),
    )
    row = cursor.fetchone()
    if not row or row["day_key"] != day_start:
        cursor.execute(
            """
            INSERT INTO trading_risk_state (agent_id, day_key, starting_equity, halted, halt_reason, updated_at)
            VALUES (?, ?, ?, 0, NULL, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                day_key = excluded.day_key,
                starting_equity = excluded.starting_equity,
                halted = 0,
                halt_reason = NULL,
                updated_at = excluded.updated_at
            """,
            (agent_id, day_start, equity, now),
        )
        return {"day_key": day_start, "starting_equity": equity, "halted": 0, "halt_reason": None}
    return dict(row)


def validate_entry(
    cursor: Any,
    *,
    agent_id: int,
    market: str,
    symbol: str,
    action: str,
    trade_value: float,
    now: str,
) -> dict[str, Any]:
    if action not in {"buy", "short"}:
        return {"allowed": True, "reason": "exit_or_non_entry"}
    if not math.isfinite(trade_value) or trade_value <= 0:
        raise GuardrailViolation("Trade value must be positive and finite")

    equity = _equity(cursor, agent_id)
    state = _ensure_state(cursor, agent_id, equity, now)
    max_trade_pct = _float_env("SCALP_MAX_TRADE_PCT", 0.10, 0.001)
    max_daily_loss_pct = _float_env("SCALP_MAX_DAILY_LOSS_PCT", 0.03, 0.001)
    max_positions = _int_env("SCALP_MAX_POSITIONS", 15, 1)
    cooldown_seconds = _int_env("SCALP_REENTRY_COOLDOWN_SECONDS", 60, 0)

    starting_equity = float(state["starting_equity"] or equity)
    daily_loss_pct = max(0.0, (starting_equity - equity) / starting_equity) if starting_equity > 0 else 0.0
    if state["halted"] or daily_loss_pct >= max_daily_loss_pct:
        reason = state["halt_reason"] or f"Daily loss limit reached ({daily_loss_pct:.2%})"
        cursor.execute(
            "UPDATE trading_risk_state SET halted = 1, halt_reason = ?, updated_at = ? WHERE agent_id = ?",
            (reason, now, agent_id),
        )
        raise GuardrailViolation(reason)

    if trade_value > equity * max_trade_pct:
        raise GuardrailViolation(
            f"Entry exceeds scalp allocation limit (${trade_value:,.2f} > {max_trade_pct:.1%} of ${equity:,.2f})"
        )

    cursor.execute("SELECT COUNT(*) AS count FROM positions WHERE agent_id = ?", (agent_id,))
    if int(cursor.fetchone()["count"] or 0) >= max_positions:
        raise GuardrailViolation(f"Maximum open positions reached ({max_positions})")

    if cooldown_seconds > 0:
        cursor.execute(
            """
            SELECT created_at FROM signals
            WHERE agent_id = ? AND market = ? AND symbol = ? AND side = ? AND message_type = 'operation'
            ORDER BY created_at DESC LIMIT 1
            """,
            (agent_id, market, symbol, action),
        )
        last = cursor.fetchone()
        if last and last["created_at"]:
            try:
                last_ts = datetime.fromisoformat(str(last["created_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                last_ts = None
            if last_ts is not None and time.time() - last_ts < cooldown_seconds:
                raise GuardrailViolation(f"Re-entry cooldown active for {symbol} ({action})")

    return {
        "allowed": True,
        "equity": equity,
        "daily_loss_pct": daily_loss_pct,
        "max_trade_pct": max_trade_pct,
        "max_positions": max_positions,
    }
