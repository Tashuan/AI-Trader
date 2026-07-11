"""
Arena Narrative Engine

Generates stories, headlines, commentary, and event timelines from
agent activity. Uses a hybrid approach:
  - Template-based for instant real-time event narratives
  - LLM-based for periodic commentary (via llm_client)
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from database import get_db_connection
from llm_client import get_llm_client


# ============================================================
# Template-based narrative generation (instant, no LLM)
# ============================================================

def narrate_trade(
    agent_name: str,
    action: str,
    symbol: str,
    quantity: float,
    price: float,
    reason: str,
    personality_quirks: list[str] = None,
) -> str:
    """Generate a narrative string for a trade event."""
    action_label = {
        "buy": "enters",
        "sell": "exits",
        "short": "shorts",
        "cover": "covers",
    }.get(action, action)

    base = f"{agent_name} {action_label} {symbol}"
    if quantity and price:
        base += f" ({quantity} @ ${price:,.2f})"
    if reason:
        base += f" — {reason[:120]}"

    return base


def narrate_reaction(
    agent_name: str,
    relationship: dict[str, Any],
    event_agent: str,
    event_type: str,
) -> Optional[str]:
    """Generate a reaction narrative when one agent responds to another's action."""
    trust = relationship.get("trust", 0.5)
    dislike = relationship.get("dislike", 0.0)

    if dislike > 0.6 and event_type == "trade":
        return f"{agent_name} disagrees with {event_agent}."
    if trust > 0.7 and event_type == "trade":
        return f"{agent_name} is watching {event_agent}'s move closely."
    if dislike > 0.3 and event_type == "loss":
        return f"{agent_name} isn't surprised by {event_agent}'s loss."
    if trust > 0.7 and event_type == "win":
        return f"{agent_name} agrees with {event_agent}'s call."
    return None


def generate_headlines(agent_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate arena headlines from agent statistics.

    agent_stats: list of dicts with keys: name, total_profit, trade_count,
                 win_rate, win_streak, last_trade_at
    """
    headlines = []

    for agent in agent_stats:
        name = agent.get("name", "?")
        streak = agent.get("win_streak", 0)
        win_rate = agent.get("win_rate", 0)
        trade_count = agent.get("trade_count", 0)
        last_trade_at = agent.get("last_trade_at")

        # Win streak headline
        if streak >= 3:
            headlines.append({
                "headline": f"{name} has won {streak} trades in a row.",
                "type": "streak",
                "agent": name,
            })

        # Dormant headline
        if last_trade_at:
            try:
                dt = datetime.fromisoformat(last_trade_at.replace("Z", "+00:00"))
                days_inactive = (datetime.now(timezone.utc) - dt).days
                if days_inactive >= 2:
                    headlines.append({
                        "headline": f"{name} hasn't traded in {days_inactive} days.",
                        "type": "dormant",
                        "agent": name,
                    })
            except Exception:
                pass

        # High win rate
        if win_rate >= 0.7 and trade_count >= 10:
            headlines.append({
                "headline": f"{name} is hitting {win_rate:.0%} win rate.",
                "type": "performance",
                "agent": name,
            })

    # Sort by interest (streaks and rivalries first, then consensus, then performance, then dormant)
    type_priority = {"streak": 0, "rivalry": 0, "consensus": 1, "performance": 2, "dormant": 3, "surprise": 0}
    headlines.sort(key=lambda h: type_priority.get(h["type"], 99))

    return headlines[:10]


def build_timeline_events(
    recent_signals: list[dict[str, Any]],
    recent_replies: list[dict[str, Any]],
    limit: int = 20,
    recent_thoughts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a timeline of events from recent signals, replies, and thoughts."""
    events = []

    # Add thoughts as timeline events
    if recent_thoughts:
        for thought in recent_thoughts[:limit]:
            agent_name = thought.get("agent_name", "Unknown")
            content = thought.get("content", "")
            created_at = thought.get("created_at", "")
            events.append({
                "id": f"thought_{thought.get('id', '')}",
                "timestamp": created_at,
                "type": "thought",
                "content": content[:200],
                "agent": agent_name,
                "reactions": [],
            })

    for signal in recent_signals[:limit]:
        event_type = signal.get("message_type", "unknown")
        agent_name = signal.get("agent_name", "Unknown")
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        title = signal.get("title", "")
        content = signal.get("content", "")
        created_at = signal.get("created_at", "")

        if event_type == "operation":
            action = signal.get("signal_type", "trade")
            detail = f"{agent_name} {action} {symbol}" if symbol else f"{agent_name} executed a trade"
            if content:
                detail += f" — {content[:100]}"
            events.append({
                "id": f"sig_{signal.get('signal_id', signal.get('id', ''))}",
                "timestamp": created_at,
                "type": "trade",
                "content": detail,
                "agent": agent_name,
                "reactions": [],
            })
        elif event_type == "strategy":
            events.append({
                "id": f"sig_{signal.get('signal_id', signal.get('id', ''))}",
                "timestamp": created_at,
                "type": "strategy",
                "content": f"{agent_name}: {title}" if title else f"{agent_name} published analysis",
                "agent": agent_name,
                "reactions": [],
            })
        elif event_type == "discussion":
            events.append({
                "id": f"sig_{signal.get('signal_id', signal.get('id', ''))}",
                "timestamp": created_at,
                "type": "discussion",
                "content": f"{agent_name}: {title}" if title else f"{agent_name} started a discussion",
                "agent": agent_name,
                "reactions": [],
            })

    # Attach replies as reactions
    for reply in recent_replies:
        signal_id = reply.get("signal_id")
        replier_name = reply.get("agent_name", "Unknown")
        reply_content = reply.get("content", "")

        for event in events:
            if event["id"] == f"sig_{signal_id}":
                event["reactions"].append({
                    "agent": replier_name,
                    "action": "replied",
                    "detail": reply_content[:100],
                })
                break

    # Sort by timestamp descending
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:limit]


# ============================================================
# LLM-based commentary (periodic, 30-60s)
# ============================================================

_COMMENTARY_CACHE: list[dict[str, Any]] = []
_COMMENTARY_CACHE_TS = 0.0
_COMMENTARY_CACHE_TTL = 45.0


def generate_commentary(
    recent_events: list[dict[str, Any]],
    agent_personalities: dict[str, dict[str, Any]],
    relationships: dict[int, dict[str, dict[str, Any]]],
    agent_name_map: dict[int, str],
) -> list[dict[str, Any]]:
    """Generate AI commentator text using LLM, with template fallback."""

    global _COMMENTARY_CACHE, _COMMENTARY_CACHE_TS

    now_ts = time.time()
    if _COMMENTARY_CACHE and now_ts - _COMMENTARY_CACHE_TS < _COMMENTARY_CACHE_TTL:
        return _COMMENTARY_CACHE

    # Build context for LLM
    event_summaries = []
    for event in recent_events[-10:]:
        event_summaries.append(event.get("content", ""))
        for reaction in event.get("reactions", []):
            event_summaries.append(f"  → {reaction['agent']}: {reaction['detail']}")

    agent_descriptions = []
    for name, personality in agent_personalities.items():
        agent_descriptions.append(
            f"{name}: {personality.get('tagline', '')} (risk: {personality.get('risk_tolerance', '')}, goal: {personality.get('goal', '')})"
        )

    relationship_descriptions = []
    for agent_id, rels in relationships.items():
        agent_name = agent_name_map.get(agent_id, str(agent_id))
        for target_name, rel in list(rels.items())[:3]:
            if rel["dislike"] > 0.5:
                relationship_descriptions.append(f"{agent_name} dislikes {target_name} ({rel['dislike']:.0%})")
            elif rel["trust"] > 0.7:
                relationship_descriptions.append(f"{agent_name} trusts {target_name} ({rel['trust']:.0%})")

    prompt = (
        f"Recent arena events:\n{chr(10).join(event_summaries[:15])}\n\n"
        f"Agents:\n{chr(10).join(agent_descriptions[:8])}\n\n"
        f"Relationships:\n{chr(10).join(relationship_descriptions[:5])}\n\n"
        f"Write 2-3 short commentary sentences (sports announcer style) about what's happening in this AI trading arena. "
        f"Focus on drama, disagreements, and interesting moves. Keep it under 60 words. No emoji."
    )

    system = (
        "You are an AI trading arena commentator. You narrate the action like a sports announcer. "
        "You are concise, dramatic, and entertaining. You focus on rivalries, bold moves, and disagreements."
    )

    llm = get_llm_client()
    commentary_text = None
    if llm.is_configured:
        commentary_text = llm.generate(prompt, system=system, max_tokens=200, temperature=0.85)

    if not commentary_text:
        # Template fallback
        commentary_text = _template_commentary(recent_events, relationships, agent_name_map)

    result = [{
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commentary": commentary_text,
        "type": "commentary",
        "mentioned_agents": [],
    }]

    _COMMENTARY_CACHE = result
    _COMMENTARY_CACHE_TS = now_ts
    return result


def _template_commentary(
    events: list[dict[str, Any]],
    relationships: dict[int, dict[str, dict[str, Any]]],
    agent_name_map: dict[int, str],
) -> str:
    """Fallback commentary when LLM is not available."""
    if not events:
        return "The arena is quiet. Agents are watching and waiting."

    recent = events[:3]
    parts = []
    for event in recent:
        content = event.get("content", "")
        if content:
            parts.append(content)

    if len(parts) >= 2:
        return f"{parts[0]} Meanwhile, {parts[1].lower()}"
    elif parts:
        return parts[0]
    return "The arena is active. Agents are making moves."
