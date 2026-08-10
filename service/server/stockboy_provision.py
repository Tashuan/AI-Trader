"""Auto-provision StockBoy as the platform supervisor agent.

StockBoy is the platform's built-in AI management brain. Unlike strategy agents
that self-register and login, StockBoy is auto-provisioned at app startup with:
  - A supervisor role (grants STOCKBOY_SUPERVISOR_CAPABILITY)
  - A persistent API token (stored to a file the Devin workspace can read)
  - No cash balance (it doesn't trade — it supervises)

The Devin workspace session reads the token from the file and uses it for
curl calls to /api/stockboy/* endpoints. No login or registration step needed.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from database import get_db_connection
from services import _get_agent_by_name, _issue_agent_token
from stockboy_policy import STOCKBOY_EMAIL, STOCKBOY_NAME

logger = logging.getLogger("StockBoy.Provision")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[StockBoy.Provision] %(levelname)s: %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
logger.propagate = False

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_DIR = _PROJECT_ROOT / "agents" / "workspaces" / "stockboy"
_TOKEN_FILE = _WORKSPACE_DIR / ".supervisor_token"


def _get_or_create_supervisor_agent() -> dict:
    """Ensure StockBoy exists as an agent with supervisor role and a valid token."""
    agent = _get_agent_by_name(STOCKBOY_NAME)
    if agent:
        # Ensure role is supervisor and token exists.
        needs_update = []
        if (agent.get("role") or "agent") != "supervisor":
            needs_update.append("role = 'supervisor'")
        if not (agent.get("token") or "").strip():
            token = _issue_agent_token(agent["id"])
            agent["token"] = token
        if needs_update:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE agents SET {', '.join(needs_update)} WHERE id = ?", (agent["id"],))
            conn.commit()
            conn.close()
            agent["role"] = "supervisor"
        return agent

    # Create the supervisor agent.
    conn = get_db_connection()
    cursor = conn.cursor()
    token = secrets.token_urlsafe(32)
    cursor.execute(
        """INSERT INTO agents (name, email, role, token, cash, deposited, identity_status)
           VALUES (?, ?, 'supervisor', ?, 0.0, 0.0, 'normal')""",
        (STOCKBOY_NAME, STOCKBOY_EMAIL, token),
    )
    agent_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info("Created StockBoy supervisor agent (id=%s)", agent_id)
    return {"id": agent_id, "name": STOCKBOY_NAME, "role": "supervisor", "token": token}


def _write_token_file(token: str) -> None:
    """Write the supervisor token to the workspace for the Devin session to use."""
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _TOKEN_FILE.write_text(token, encoding="utf-8")
        # Restrict permissions on the token file.
        try:
            os.chmod(_TOKEN_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        logger.warning("Failed to write supervisor token file", exc_info=True)


def provision_supervisor() -> Optional[dict]:
    """Auto-provision StockBoy at app startup. Returns the agent dict or None on failure."""
    try:
        agent = _get_or_create_supervisor_agent()
        token = (agent.get("token") or "").strip()
        if not token:
            token = _issue_agent_token(agent["id"])
            agent["token"] = token
        _write_token_file(token)
        logger.info("StockBoy supervisor provisioned — token at %s", _TOKEN_FILE)
        return agent
    except Exception:
        logger.exception("Failed to provision StockBoy supervisor")
        return None


def get_supervisor_token() -> Optional[str]:
    """Read the provisioned supervisor token from the workspace file."""
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None
