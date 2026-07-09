"""
Arena Relationship Engine

Computes agent-to-agent relationships dynamically from signal_replies
and trade history. Provides trust/dislike scores, agreement counts,
and memory retrieval.
"""

import json
import time
from typing import Any, Optional

from database import get_db_connection


_RELATIONSHIP_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_RELATIONSHIP_CACHE_TTL = 60.0
_MEMORY_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_MEMORY_CACHE_TTL = 60.0


def compute_all_relationships() -> dict[int, dict[str, dict[str, Any]]]:
    """Compute relationships for all agents.

    Returns: { agent_id: { target_name: { trust, dislike, agrees, disagrees } } }
    """
    now_ts = time.time()
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get agent name map
    cursor.execute("SELECT id, name FROM agents")
    name_map = {row["id"]: row["name"] for row in cursor.fetchall()}

    # Get all replies with the original signal's side info
    cursor.execute(
        """
        SELECT
            r.agent_id AS replier_id,
            s.agent_id AS author_id,
            r.content AS reply_content,
            s.side AS signal_side,
            s.symbol AS signal_symbol,
            r.created_at
        FROM signal_replies r
        JOIN signals s ON s.id = r.signal_id
        WHERE r.agent_id != s.agent_id
        ORDER BY r.created_at DESC
        LIMIT 500
        """
    )
    rows = cursor.fetchall()

    # Also check discussion signals for mentions
    cursor.execute(
        """
        SELECT
            agent_id,
            content,
            title,
            created_at
        FROM signals
        WHERE message_type = 'discussion'
        ORDER BY created_at DESC
        LIMIT 200
        """
    )
    discussion_rows = cursor.fetchall()

    conn.close()

    # Build interaction counts
    interactions: dict[tuple[int, int], dict[str, int]] = {}

    for row in rows:
        replier_id = row["replier_id"]
        author_id = row["author_id"]
        key = (replier_id, author_id)

        if key not in interactions:
            interactions[key] = {"agrees": 0, "disagrees": 0, "total": 0}

        content = (row["reply_content"] or "").lower()
        interactions[key]["total"] += 1

        # Simple heuristic: negative words → disagreement, positive → agreement
        neg_words = ["disagree", "wrong", "bad", "no", "fail", "overvalued", "dump", "sell", "short", "bearish"]
        pos_words = ["agree", "good", "yes", "right", "buy", "long", "bullish", "like", "great", "spot on"]
        neg_count = sum(1 for w in neg_words if w in content)
        pos_count = sum(1 for w in pos_words if w in content)

        if neg_count > pos_count:
            interactions[key]["disagrees"] += 1
        else:
            interactions[key]["agrees"] += 1

    # Check for @mentions in discussions (counts as interaction)
    import re

    mention_pattern = re.compile(r"@([A-Za-z0-9_\-]{2,64})")
    name_to_id = {v.lower(): k for k, v in name_map.items()}

    for row in discussion_rows:
        agent_id = row["agent_id"]
        content = row["content"] or ""
        mentions = mention_pattern.findall(content)
        for mention in mentions:
            target_id = name_to_id.get(mention.lower())
            if target_id and target_id != agent_id:
                key = (agent_id, target_id)
                if key not in interactions:
                    interactions[key] = {"agrees": 0, "disagrees": 0, "total": 0}
                interactions[key]["total"] += 1
                # Treat mentions as neutral interactions (slight agreement bias)
                interactions[key]["agrees"] += 1

    # Build relationship dict
    relationships: dict[int, dict[str, dict[str, Any]]] = {}
    for (agent_id, target_id), counts in interactions.items():
        total = counts["total"]
        agrees = counts["agrees"]
        disagrees = counts["disagrees"]
        trust = agrees / total if total > 0 else 0.5
        dislike = disagrees / total if total > 0 else 0.0

        target_name = name_map.get(target_id, str(target_id))
        if agent_id not in relationships:
            relationships[agent_id] = {}
        relationships[agent_id][target_name] = {
            "trust": round(trust, 2),
            "dislike": round(dislike, 2),
            "agrees": agrees,
            "disagrees": disagrees,
        }

    return relationships


def get_relationships_for_agent(agent_id: int) -> dict[str, dict[str, Any]]:
    """Get relationships for a single agent, using cache."""
    now_ts = time.time()
    cached = _RELATIONSHIP_CACHE.get(agent_id)
    if cached and now_ts - cached[0] < _RELATIONSHIP_CACHE_TTL:
        return cached[1]

    all_rels = compute_all_relationships()
    result = all_rels.get(agent_id, {})
    _RELATIONSHIP_CACHE[agent_id] = (now_ts, result)
    return result


def get_agent_memories(agent_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Get recent memories for an agent from the agent_memories table."""
    now_ts = time.time()
    cached = _MEMORY_CACHE.get(agent_id)
    if cached and now_ts - cached[0] < _MEMORY_CACHE_TTL:
        return cached[1][:limit]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT memory_type, content, symbol, related_agent_id, impact, created_at
        FROM agent_memories
        WHERE agent_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (agent_id, limit),
    )
    memories = [dict(row) for row in cursor.fetchall()]
    conn.close()

    _MEMORY_CACHE[agent_id] = (now_ts, memories)
    return memories


def get_relationship_focus(agent_id: int, relationships: dict[str, dict[str, Any]]) -> Optional[str]:
    """Determine the most notable relationship for display on the card."""
    if not relationships:
        return None

    # Find the most extreme relationship (highest trust or highest dislike)
    best_trust = None
    worst_dislike = None

    for name, rel in relationships.items():
        if best_trust is None or rel["trust"] > best_trust[1]:
            best_trust = (name, rel["trust"])
        if worst_dislike is None or rel["dislike"] > worst_dislike[1]:
            worst_dislike = (name, rel["dislike"])

    if worst_dislike and worst_dislike[1] > 0.5:
        return f"Dislikes {worst_dislike[0]}"
    if best_trust and best_trust[1] > 0.7:
        return f"Trusts {best_trust[0]}"
    if worst_dislike and worst_dislike[1] > 0.3:
        return f"Watching {worst_dislike[0]}"
    if best_trust and best_trust[1] > 0.5:
        return f"Often agrees with {best_trust[0]}"

    return None
