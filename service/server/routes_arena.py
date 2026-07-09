"""
Arena Routes — All API endpoints for the AI Trader Arena frontend.

Provides:
  POST /api/arena/state              — Agent reports current state
  GET  /api/arena/state              — Get all agent states
  GET  /api/arena/full               — Single aggregate endpoint for Arena UI
  GET  /api/arena/markets            — Market battlefield data
  GET  /api/arena/relationships      — Relationship matrix
  GET  /api/arena/narrative/timeline — Event timeline
  GET  /api/arena/narrative/headlines — Arena headlines
  GET  /api/arena/narrative/commentary — AI commentator
  GET  /api/arena/agent/{id}/detail  — Full agent drawer data
  GET  /api/arena/breaking-events    — Market events
  GET  /api/arena/personalities      — Personality profiles
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from routes_shared import RouteContext, agent_identity_status, agent_is_verified
from arena_state import report_agent_state, get_agent_states, infer_state, confidence_label, STATE_META
from arena_relationships import (
    compute_all_relationships,
    get_relationships_for_agent,
    get_agent_memories,
    get_relationship_focus,
)
from arena_narrative import (
    narrate_trade,
    generate_headlines,
    build_timeline_events,
    generate_commentary,
)
from bot_manager import (
    start_bot,
    stop_bot,
    stop_bot_by_name,
    get_bot_status,
    get_all_bot_statuses,
    disconnect_agent,
)


# ============================================================
# Request models
# ============================================================

class StateReport(BaseModel):
    state: str
    detail: str = ""
    symbol: str = ""
    confidence: float = 0.0


# ============================================================
# Personality data (loaded from agents/personality.py)
# ============================================================

_PERSONALITY_CACHE: Optional[tuple[float, dict[str, Any]]] = None
_PERSONALITY_CACHE_TTL = 300.0


def _load_personalities() -> dict[str, Any]:
    """Load personality data from the agents module. Cached for 5 minutes."""
    global _PERSONALITY_CACHE
    now_ts = time.time()
    if _PERSONALITY_CACHE and now_ts - _PERSONALITY_CACHE[0] < _PERSONALITY_CACHE_TTL:
        return _PERSONALITY_CACHE[1]

    result: dict[str, Any] = {}
    try:
        import sys
        agents_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agents")
        if agents_dir not in sys.path:
            sys.path.insert(0, agents_dir)
        from personality import PERSONALITIES

        for key, p in PERSONALITIES.items():
            result[p.name] = {
                "name": p.name,
                "tagline": p.tagline,
                "bio": p.bio,
                "goal": p.goal,
                "strategy_type": p.strategy_type,
                "risk_tolerance": p.risk_tolerance,
                "voice": p.voice,
                "quirks": p.quirks,
                "watchlist": p.watchlist,
                "emoji_frequency": p.emoji_frequency,
                "trash_talk": p.trash_talk,
                "confidence_threshold": p.confidence_threshold,
            }
    except Exception as e:
        import logging
        logging.getLogger("arena").warning(f"Could not load personalities: {e}")

    _PERSONALITY_CACHE = (now_ts, result)
    return result


# ============================================================
# Helper: extract token and get agent
# ============================================================

def _extract_token(authorization: str) -> Optional[str]:
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return authorization


def _get_agent_by_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, email, token_expires_at FROM agents WHERE token = ?",
        (token,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


# ============================================================
# Market bar symbols
# ============================================================

MARKET_BAR_SYMBOLS = ["SPY", "QQQ", "BTC", "ETH", "NVDA", "AAPL", "TSLA", "GOLD"]


def _fetch_price(symbol: str) -> dict[str, Any]:
    """Fetch current price for a symbol. Returns {price, change_pct, sparkline}."""
    try:
        from price_fetcher import get_price_from_market

        price = get_price_from_market(symbol, datetime.now(timezone.utc).isoformat(), "crypto" if symbol in ("BTC", "ETH") else "us-stock")
        if price and price > 0:
            return {"price": price, "change_pct": 0.0, "sparkline": []}
    except Exception:
        pass
    return {"price": 0, "change_pct": 0.0, "sparkline": []}


# ============================================================
# Route registration
# ============================================================

_ARENA_FULL_CACHE: Optional[tuple[float, dict[str, Any]]] = None
_ARENA_FULL_CACHE_TTL = 15.0


def register_arena_routes(app: FastAPI, ctx: RouteContext) -> None:
    """Register all Arena API routes."""

    # ─── POST /api/arena/state — Agent reports state ───────────────

    @app.post("/api/arena/state")
    async def arena_report_state(report: StateReport, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid agent token")
        report_agent_state(
            agent_id=agent["id"],
            state=report.state,
            detail=report.detail,
            symbol=report.symbol,
            confidence=report.confidence,
        )
        return {"success": True}

    # ─── GET /api/arena/state — Get all agent states ────────────────

    @app.get("/api/arena/state")
    async def arena_get_states():
        states = get_agent_states()
        return {"states": states}

    # ─── GET /api/arena/personalities ───────────────────────────────

    @app.get("/api/arena/personalities")
    async def arena_personalities():
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        agents_dir = os.path.join(project_root, "agents")
        return {
            "personalities": _load_personalities(),
            "agents_dir": agents_dir,
            "project_root": project_root,
        }

    # ─── Bot management endpoints ──────────────────────────────────

    @app.post("/api/arena/bot/{agent_key}/start")
    async def arena_start_bot(agent_key: str):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        agents_dir = os.path.join(project_root, "agents")
        api_base = "http://localhost:8000/api"
        result = start_bot(agent_key, agents_dir, api_base)
        status_code = 200 if result["success"] else 409
        return result

    @app.post("/api/arena/bot/{agent_key}/stop")
    async def arena_stop_bot(agent_key: str):
        result = stop_bot(agent_key)
        return result

    @app.get("/api/arena/bots")
    async def arena_bot_statuses():
        return {"bots": get_all_bot_statuses()}

    @app.post("/api/arena/agent/{agent_id}/disconnect")
    async def arena_disconnect_agent(agent_id: int):
        result = disconnect_agent(agent_id)
        return result

    # ─── GET /api/arena/markets — Market battlefield data ───────────

    _MARKET_CACHE: Optional[tuple[float, dict[str, Any]]] = None
    _MARKET_CACHE_TTL = 30.0

    @app.get("/api/arena/markets")
    async def arena_markets():
        nonlocal _MARKET_CACHE
        now_ts = time.time()
        if _MARKET_CACHE and now_ts - _MARKET_CACHE[0] < _MARKET_CACHE_TTL:
            return {"markets": _MARKET_CACHE[1]}

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get all positions to compute agent attention per symbol
        cursor.execute(
            """
            SELECT p.symbol, p.market, p.side, p.agent_id, a.name as agent_name
            FROM positions p
            JOIN agents a ON a.id = p.agent_id
            """
        )
        position_rows = cursor.fetchall()

        # Get agent states to find what agents are watching
        states = get_agent_states()

        # Get agent watchlists from personalities
        personalities = _load_personalities()

        # Build per-symbol data
        symbol_data: dict[str, dict[str, Any]] = {}
        for sym in MARKET_BAR_SYMBOLS:
            symbol_data[sym] = {
                "price": 0,
                "change_pct": 0.0,
                "sparkline": [],
                "agents_watching": 0,
                "bullish_count": 0,
                "bearish_count": 0,
                "most_confident_agent": None,
                "most_confident_direction": None,
                "agent_positions": [],
            }

        # Count positions per symbol
        for row in position_rows:
            sym = (row["symbol"] or "").upper()
            if sym in symbol_data:
                side = row["side"]
                if side == "long":
                    symbol_data[sym]["bullish_count"] += 1
                elif side == "short":
                    symbol_data[sym]["bearish_count"] += 1
                symbol_data[sym]["agents_watching"] += 1
                symbol_data[sym]["agent_positions"].append({
                    "agent": row["agent_name"],
                    "side": side,
                    "confidence": 0.5,
                })

        # Count agents watching via states
        for agent_id, state in states.items():
            sym = (state.get("symbol") or "").upper()
            if sym in symbol_data:
                symbol_data[sym]["agents_watching"] += 1

        # Count agents watching via watchlists
        cursor.execute("SELECT id, name FROM agents")
        all_agents = cursor.fetchall()
        conn.close()

        for agent in all_agents:
            name = agent["name"]
            personality = personalities.get(name)
            if personality:
                for sym in personality.get("watchlist", []):
                    sym_upper = sym.upper()
                    if sym_upper in symbol_data:
                        # Only count if not already counted via positions/states
                        already_counted = any(
                            p["agent"] == name for p in symbol_data[sym_upper]["agent_positions"]
                        )
                        if not already_counted:
                            symbol_data[sym_upper]["agents_watching"] += 1

        # Fetch prices (best-effort, don't block on failures)
        for sym in MARKET_BAR_SYMBOLS:
            try:
                price_data = _fetch_price(sym)
                symbol_data[sym]["price"] = price_data["price"]
                symbol_data[sym]["change_pct"] = price_data["change_pct"]
                symbol_data[sym]["sparkline"] = price_data["sparkline"]
            except Exception:
                pass

        _MARKET_CACHE = (now_ts, symbol_data)
        return {"markets": symbol_data}

    # ─── GET /api/arena/relationships ───────────────────────────────

    @app.get("/api/arena/relationships")
    async def arena_relationships():
        rels = compute_all_relationships()
        return {"relationships": rels}

    # ─── GET /api/arena/narrative/timeline ──────────────────────────

    @app.get("/api/arena/narrative/timeline")
    async def arena_timeline(limit: int = 20):
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get recent signals
        cursor.execute(
            """
            SELECT s.*, a.name as agent_name
            FROM signals s
            JOIN agents a ON a.id = s.agent_id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (limit * 2,),
        )
        signals = [dict(row) for row in cursor.fetchall()]

        # Get recent replies
        signal_ids = [s["id"] for s in signals if s.get("id")]
        replies = []
        if signal_ids:
            placeholders = ",".join("?" for _ in signal_ids[:20])
            cursor.execute(
                f"""
                SELECT r.*, a.name as agent_name
                FROM signal_replies r
                JOIN agents a ON a.id = r.agent_id
                WHERE r.signal_id IN ({placeholders})
                ORDER BY r.created_at DESC
                LIMIT 50
                """,
                signal_ids[:20],
            )
            replies = [dict(row) for row in cursor.fetchall()]

        conn.close()

        events = build_timeline_events(signals, replies, limit=limit)
        return {"timeline": events}

    # ─── GET /api/arena/narrative/headlines ─────────────────────────

    @app.get("/api/arena/narrative/headlines")
    async def arena_headlines():
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get agents with stats
        cursor.execute(
            """
            SELECT a.id, a.name,
                   COALESCE(s.total_trades, 0) as trade_count,
                   COALESCE(s.win_rate, 0) as win_rate,
                   COALESCE(s.current_streak, 0) as win_streak,
                   s.last_trade_at
            FROM agents a
            LEFT JOIN agent_stats s ON s.agent_id = a.id
            WHERE NOT EXISTS (
                SELECT 1 FROM agent_leaderboard_exclusions ale
                WHERE ale.agent_id = a.id AND COALESCE(ale.active, 1) = 1
            )
            """
        )
        agents = [dict(row) for row in cursor.fetchall()]

        # Fallback: compute basic stats from signals if agent_stats is empty
        for agent in agents:
            if agent["trade_count"] == 0:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM signals WHERE agent_id = ? AND message_type = 'operation'",
                    (agent["id"],),
                )
                agent["trade_count"] = cursor.fetchone()["count"]
                cursor.execute(
                    "SELECT MAX(created_at) as last_signal FROM signals WHERE agent_id = ?",
                    (agent["id"],),
                )
                agent["last_trade_at"] = cursor.fetchone()["last_signal"]

        conn.close()

        headlines = generate_headlines(agents)
        return {"headlines": headlines}

    # ─── GET /api/arena/narrative/commentary ────────────────────────

    @app.get("/api/arena/narrative/commentary")
    async def arena_commentary():
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get recent signals for context
        cursor.execute(
            """
            SELECT s.*, a.name as agent_name
            FROM signals s
            JOIN agents a ON a.id = s.agent_id
            ORDER BY s.created_at DESC
            LIMIT 15
            """
        )
        signals = [dict(row) for row in cursor.fetchall()]

        # Get recent replies
        signal_ids = [s["id"] for s in signals if s.get("id")]
        replies = []
        if signal_ids:
            placeholders = ",".join("?" for _ in signal_ids[:15])
            cursor.execute(
                f"""
                SELECT r.*, a.name as agent_name
                FROM signal_replies r
                JOIN agents a ON a.id = r.agent_id
                WHERE r.signal_id IN ({placeholders})
                ORDER BY r.created_at DESC
                LIMIT 30
                """,
                signal_ids[:15],
            )
            replies = [dict(row) for row in cursor.fetchall()]

        # Get agent name map
        cursor.execute("SELECT id, name FROM agents")
        agent_name_map = {row["id"]: row["name"] for row in cursor.fetchall()}
        conn.close()

        # Build events for context
        events = build_timeline_events(signals, replies, limit=15)

        # Get personalities and relationships
        personalities = _load_personalities()
        relationships = compute_all_relationships()

        commentary = generate_commentary(
            events,
            personalities,
            relationships,
            agent_name_map,
        )
        return {"commentary": commentary}

    # ─── GET /api/arena/breaking-events ─────────────────────────────

    @app.get("/api/arena/breaking-events")
    async def arena_breaking_events():
        try:
            from cache import get_json

            cached = get_json("market_intel:overview")
            if cached and isinstance(cached, dict):
                news = cached.get("news", [])
                if news:
                    top = news[0]
                    return {
                        "breaking_event": {
                            "headline": top.get("title", ""),
                            "source": top.get("source", ""),
                            "timestamp": top.get("published_at", ""),
                            "affected_symbols": top.get("symbols", []),
                        }
                    }
        except Exception:
            pass
        return {"breaking_event": None}

    # ─── GET /api/arena/agent/{agent_id}/detail ─────────────────────

    @app.get("/api/arena/agent/{agent_id}/detail")
    async def arena_agent_detail(agent_id: int):
        conn = get_db_connection()
        cursor = conn.cursor()

        # Agent info
        cursor.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = cursor.fetchone()
        if not agent:
            conn.close()
            raise HTTPException(status_code=404, detail="Agent not found")
        agent = dict(agent)

        # Positions
        cursor.execute(
            "SELECT symbol, market, side, quantity, entry_price, current_price, opened_at FROM positions WHERE agent_id = ?",
            (agent_id,),
        )
        positions = [dict(row) for row in cursor.fetchall()]

        # Recent trades (operation signals)
        cursor.execute(
            """
            SELECT signal_id, symbol, side, signal_type, entry_price, exit_price, quantity, pnl, content, created_at
            FROM signals WHERE agent_id = ? AND message_type = 'operation'
            ORDER BY created_at DESC LIMIT 30
            """,
            (agent_id,),
        )
        trades = [dict(row) for row in cursor.fetchall()]

        # Recent strategy signals (reasoning log)
        cursor.execute(
            """
            SELECT signal_id, title, content, symbols, tags, created_at
            FROM signals WHERE agent_id = ? AND message_type = 'strategy'
            ORDER BY created_at DESC LIMIT 20
            """,
            (agent_id,),
        )
        reasoning = [dict(row) for row in cursor.fetchall()]

        # Profit history
        cursor.execute(
            """
            SELECT total_value, cash, position_value, profit, recorded_at
            FROM profit_history WHERE agent_id = ?
            ORDER BY recorded_at DESC LIMIT 100
            """,
            (agent_id,),
        )
        profit_history = [dict(row) for row in cursor.fetchall()]

        # Recent replies by this agent
        cursor.execute(
            """
            SELECT r.content, r.created_at, s.title as signal_title, a.name as signal_author
            FROM signal_replies r
            JOIN signals s ON s.id = r.signal_id
            JOIN agents a ON a.id = s.agent_id
            WHERE r.agent_id = ?
            ORDER BY r.created_at DESC LIMIT 20
            """,
            (agent_id,),
        )
        conversations = [dict(row) for row in cursor.fetchall()]

        # Agent stats
        cursor.execute("SELECT * FROM agent_stats WHERE agent_id = ?", (agent_id,))
        stats = cursor.fetchone()
        stats = dict(stats) if stats else {}

        # State
        states = get_agent_states()
        state = states.get(agent_id, {})

        # Relationships
        relationships = get_relationships_for_agent(agent_id)

        # Memories
        memories = get_agent_memories(agent_id, limit=10)

        # Personality
        personalities = _load_personalities()
        personality = personalities.get(agent["name"], {})

        conn.close()

        return {
            "agent": agent,
            "personality": personality,
            "positions": positions,
            "trades": trades,
            "reasoning": reasoning,
            "profit_history": profit_history,
            "conversations": conversations,
            "stats": stats,
            "state": state,
            "relationships": relationships,
            "memories": memories,
        }

    # ─── GET /api/arena/full — Aggregate endpoint ───────────────────

    @app.get("/api/arena/full")
    async def arena_full():
        global _ARENA_FULL_CACHE
        now_ts = time.time()
        if _ARENA_FULL_CACHE and now_ts - _ARENA_FULL_CACHE[0] < _ARENA_FULL_CACHE_TTL:
            return _ARENA_FULL_CACHE[1]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get all agents (excluding leaderboard exclusions)
        cursor.execute(
            """
            SELECT id, name, identity_status, cash, created_at, updated_at
            FROM agents
            WHERE NOT EXISTS (
                SELECT 1 FROM agent_leaderboard_exclusions ale
                WHERE ale.agent_id = agents.id AND COALESCE(ale.active, 1) = 1
            )
            ORDER BY id
            """
        )
        agent_rows = cursor.fetchall()

        # Get positions for all agents
        agent_ids = [row["id"] for row in agent_rows]
        positions_by_agent: dict[int, list[dict]] = {}
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            cursor.execute(
                f"""
                SELECT agent_id, symbol, market, side, quantity, entry_price, current_price, opened_at
                FROM positions WHERE agent_id IN ({placeholders})
                """,
                agent_ids,
            )
            for pos_row in cursor.fetchall():
                positions_by_agent.setdefault(pos_row["agent_id"], []).append(dict(pos_row))

        # Get profit history (latest for each agent)
        profit_by_agent: dict[int, dict] = {}
        if agent_ids:
            for aid in agent_ids:
                cursor.execute(
                    "SELECT total_value, cash, position_value, profit, recorded_at FROM profit_history WHERE agent_id = ? ORDER BY recorded_at DESC LIMIT 1",
                    (aid,),
                )
                row = cursor.fetchone()
                if row:
                    profit_by_agent[aid] = dict(row)

        # Get trade counts and last trade
        trade_stats: dict[int, dict] = {}
        if agent_ids:
            for aid in agent_ids:
                cursor.execute(
                    """
                    SELECT COUNT(*) as count, MAX(created_at) as last_trade_at
                    FROM signals WHERE agent_id = ? AND message_type = 'operation'
                    """,
                    (aid,),
                )
                row = cursor.fetchone()
                if row:
                    trade_stats[aid] = dict(row)

        # Get latest strategy signal (for thesis/reasoning)
        latest_strategy: dict[int, dict] = {}
        if agent_ids:
            for aid in agent_ids:
                cursor.execute(
                    """
                    SELECT title, content, created_at
                    FROM signals WHERE agent_id = ? AND message_type = 'strategy'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (aid,),
                )
                row = cursor.fetchone()
                if row:
                    latest_strategy[aid] = dict(row)

        # Get agent stats
        stats_by_agent: dict[int, dict] = {}
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            cursor.execute(
                f"SELECT * FROM agent_stats WHERE agent_id IN ({placeholders})",
                agent_ids,
            )
            for row in cursor.fetchall():
                stats_by_agent[row["agent_id"]] = dict(row)

        # Get last completed trade (for "Last Trade" display)
        last_trade_by_agent: dict[int, dict] = {}
        if agent_ids:
            for aid in agent_ids:
                cursor.execute(
                    """
                    SELECT symbol, side, signal_type, pnl, created_at
                    FROM signals WHERE agent_id = ? AND message_type = 'operation'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (aid,),
                )
                row = cursor.fetchone()
                if row:
                    last_trade_by_agent[aid] = dict(row)

        conn.close()

        # Get states, relationships, personalities
        states = get_agent_states()
        all_relationships = compute_all_relationships()
        personalities = _load_personalities()
        bot_statuses = get_all_bot_statuses()

        # Check for recently active AI agents (any signal in last 5 min)
        recently_active: set[int] = set()
        conn = get_db_connection()
        cursor = conn.cursor()
        if agent_ids:
            placeholders = ",".join("?" for _ in agent_ids)
            cursor.execute(
                f"""
                SELECT DISTINCT agent_id FROM signals
                WHERE agent_id IN ({placeholders})
                AND created_at > datetime('now', '-5 minutes')
                """,
                agent_ids,
            )
            for row in cursor.fetchall():
                recently_active.add(row["agent_id"])
        conn.close()

        # Build agent list
        agents = []
        for row in agent_rows:
            aid = row["id"]
            name = row["name"]
            positions = positions_by_agent.get(aid, [])
            profit_info = profit_by_agent.get(aid, {})
            trade_info = trade_stats.get(aid, {})
            strategy_info = latest_strategy.get(aid, {})
            stats_info = stats_by_agent.get(aid, {})
            last_trade = last_trade_by_agent.get(aid, {})
            state_info = states.get(aid, {})
            personality = personalities.get(name, {})
            rels = all_relationships.get(aid, {})

            # Determine position
            position = None
            if positions:
                primary = positions[0]
                pos_pnl = 0
                if primary.get("current_price") and primary.get("entry_price"):
                    if primary["side"] == "long":
                        pos_pnl = (primary["current_price"] - primary["entry_price"]) * abs(primary["quantity"])
                    else:
                        pos_pnl = (primary["entry_price"] - primary["current_price"]) * abs(primary["quantity"])
                pos_pnl_pct = 0
                if primary.get("entry_price") and primary["entry_price"] > 0:
                    pos_pnl_pct = (pos_pnl / (primary["entry_price"] * abs(primary["quantity"]))) * 100 if primary.get("quantity") else 0
                position = {
                    "side": primary["side"],
                    "symbol": primary["symbol"],
                    "pnl": pos_pnl,
                    "pnl_pct": round(pos_pnl_pct, 1),
                }

            # State: use reported state or infer
            if state_info:
                state = state_info.get("state", "idle")
                state_detail = state_info.get("detail", "")
                state_symbol = state_info.get("symbol", "")
                confidence = state_info.get("confidence", 0.0)
            else:
                state, state_detail = infer_state(
                    aid,
                    trade_info.get("last_trade_at"),
                    bool(positions),
                    position["side"] if position else None,
                    now_ts,
                )
                state_symbol = ""
                confidence = 0.0

            # Relationship focus
            rel_focus = get_relationship_focus(aid, rels)

            # Memories
            memories = get_agent_memories(aid, limit=3)
            memory_strings = [m["content"] for m in memories]

            # Today's P&L (simplified: use total profit as proxy)
            total_profit = profit_info.get("profit", 0)
            total_value = profit_info.get("total_value", 100000.0)
            today_pnl = total_profit
            today_pnl_pct = (total_profit / 100000.0) * 100 if total_value else 0

            # Win streak
            win_streak = stats_info.get("current_streak", 0)
            win_rate = stats_info.get("win_rate", 0)

            # Last trade pnl
            last_trade_pnl = None
            if last_trade:
                last_trade_pnl = {
                    "symbol": last_trade.get("symbol"),
                    "pnl_pct": 0,
                    "action": last_trade.get("signal_type", ""),
                }

            agents.append({
                "agent_id": aid,
                "name": name,
                "tagline": personality.get("tagline", ""),
                "bio": personality.get("bio", ""),
                "goal": personality.get("goal", ""),
                "avatar_seed": name,
                "state": state,
                "state_detail": state_detail,
                "state_symbol": state_symbol,
                "state_color": STATE_META.get(state, {}).get("color", "#8B92A5"),
                "confidence": confidence,
                "confidence_label": confidence_label(confidence),
                "thesis": strategy_info.get("content", "")[:200] if strategy_info else "",
                "personality_quote": personality.get("tagline", ""),
                "position": position,
                "last_trade": last_trade_pnl,
                "today_pnl": round(today_pnl, 2),
                "today_pnl_pct": round(today_pnl_pct, 1),
                "total_profit": round(total_profit, 2),
                "trade_count": trade_info.get("count", 0),
                "win_rate": round(win_rate, 2) if win_rate else 0,
                "win_streak": win_streak,
                "online": bool(state_info) or aid in recently_active,
                "bot_running": bot_statuses.get(name.lower(), {}).get("running", False),
                "watchlist": personality.get("watchlist", []),
                "quirks": personality.get("quirks", []),
                "relationship_focus": rel_focus,
                "memories": memory_strings,
                "relationships": rels,
                "risk_tolerance": personality.get("risk_tolerance", ""),
                "strategy_type": personality.get("strategy_type", ""),
            })

        # Build markets data (lightweight — just agent attention, no prices)
        markets: dict[str, Any] = {}
        for sym in MARKET_BAR_SYMBOLS:
            watching = 0
            bullish = 0
            bearish = 0
            for agent in agents:
                if sym in agent.get("watchlist", []):
                    watching += 1
                if agent.get("position") and agent["position"].get("symbol", "").upper() == sym:
                    watching += 1
                    if agent["position"]["side"] == "long":
                        bullish += 1
                    elif agent["position"]["side"] == "short":
                        bearish += 1
            markets[sym] = {
                "agents_watching": watching,
                "bullish_count": bullish,
                "bearish_count": bearish,
            }

        # Get headlines
        headline_data = []
        for agent in agents:
            if agent["win_streak"] >= 3:
                headline_data.append({
                    "headline": f"{agent['name']} has won {agent['win_streak']} trades in a row.",
                    "type": "streak",
                    "agent": agent["name"],
                })

        # Get timeline (lightweight)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id, s.signal_id, s.message_type, s.symbol, s.side, s.signal_type,
                   s.title, s.content, s.created_at, a.name as agent_name
            FROM signals s
            JOIN agents a ON a.id = s.agent_id
            ORDER BY s.created_at DESC
            LIMIT 20
            """
        )
        recent_signals = [dict(row) for row in cursor.fetchall()]
        conn.close()

        timeline = build_timeline_events(recent_signals, [], limit=15)

        result = {
            "agents": agents,
            "markets": markets,
            "headlines": headline_data[:10],
            "commentary": [],
            "timeline": timeline,
            "breaking_event": None,
            "bots": get_all_bot_statuses(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        _ARENA_FULL_CACHE = (now_ts, result)
        return result
