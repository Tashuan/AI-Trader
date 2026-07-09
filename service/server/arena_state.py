"""
Arena State Engine — Decision Engine

Tracks agent states reported via POST /api/arena/state and provides
inference fallback for agents that don't report states.
"""

import time
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_db_connection


# State ordering for the decision state machine
STATE_MACHINE = [
    "idle",
    "scanning",
    "researching",
    "reading_news",
    "comparing_technicals",
    "building_thesis",
    "waiting",
    "entering",
    "managing",
    "exiting",
    "reviewing",
]

# Map states to display labels and accent colors
STATE_META = {
    "idle": {"label": "Idle", "color": "#8B92A5"},
    "scanning": {"label": "Scanning", "color": "#3B82F6"},
    "researching": {"label": "Researching", "color": "#3B82F6"},
    "reading_news": {"label": "Reading News", "color": "#3B82F6"},
    "comparing_technicals": {"label": "Comparing Technicals", "color": "#3B82F6"},
    "building_thesis": {"label": "Building Thesis", "color": "#8B5CF6"},
    "waiting": {"label": "Waiting", "color": "#F59E0B"},
    "entering": {"label": "Entering", "color": "#10B981"},
    "managing": {"label": "Managing", "color": "#10B981"},
    "exiting": {"label": "Exiting", "color": "#EF4444"},
    "reviewing": {"label": "Reviewing", "color": "#8B5CF6"},
}

# Confidence labels
CONFIDENCE_LABELS = [
    (0.0, "Unsure"),
    (0.3, "Interested"),
    (0.5, "Confident"),
    (0.75, "High Conviction"),
    (0.9, "All In"),
]


def confidence_label(confidence: float) -> str:
    for threshold, label in reversed(CONFIDENCE_LABELS):
        if confidence >= threshold:
            return label
    return "Unsure"


def report_agent_state(
    agent_id: int,
    state: str,
    detail: str = "",
    symbol: str = "",
    confidence: float = 0.0,
) -> None:
    """Upsert the latest state for an agent (called by agents via POST /api/arena/state)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        """
        INSERT INTO agent_states (agent_id, state, detail, symbol, confidence, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent_id, state, detail, symbol, confidence, now),
    )
    conn.commit()
    conn.close()


def get_agent_states() -> dict[int, dict[str, Any]]:
    """Get the latest state for each agent that has reported."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT agent_id, state, detail, symbol, confidence, updated_at
        FROM agent_states
        WHERE id IN (SELECT MAX(id) FROM agent_states GROUP BY agent_id)
        """
    )
    states = {}
    for row in cursor.fetchall():
        states[row["agent_id"]] = dict(row)
    conn.close()
    return states


def infer_state(
    agent_id: int,
    last_signal_at: Optional[str],
    has_open_position: bool,
    position_side: Optional[str],
    now_ts: float,
) -> tuple[str, str]:
    """Infer a state for agents that haven't reported one.

    Returns (state, detail).
    """
    if has_open_position:
        if position_side == "long":
            return "managing", "Managing long position"
        elif position_side == "short":
            return "managing", "Managing short position"

    if last_signal_at:
        try:
            from datetime import datetime as _dt

            dt = _dt.fromisoformat(last_signal_at.replace("Z", "+00:00"))
            signal_age = now_ts - dt.timestamp()
            if signal_age < 300:
                return "thinking", "Recently posted analysis"
            if signal_age < 1800:
                return "watching", "Monitoring markets"
            if signal_age < 86400:
                return "idle", "Waiting for next cycle"
        except Exception:
            pass

    return "idle", "Waiting"
