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
from permissions import agent_role, require_admin, require_agent
from portfolio_risk_engine import _load_sector_map, _total_portfolio_equity, _utc_day_start
from routes_shared import RouteContext, agent_identity_status, agent_is_verified, broadcast_activity
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


class ThoughtReport(BaseModel):
    thought: str


def _compute_arena_stats(cursor, agent_id: int) -> dict[str, Any]:
    """Compute live performance stats from signals and profit_history.

    signals.pnl is never populated by the trading system, so we derive
    win rate and streaks from profit_history deltas (each snapshot where
    profit increased = win, decreased = loss).
    """
    # Total trades = count of operation signals (buy/sell actions)
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM signals WHERE agent_id = ? AND message_type = 'operation'",
        (agent_id,),
    )
    total_trades = cursor.fetchone()["cnt"]

    # Profit history — sorted chronologically
    cursor.execute(
        """
        SELECT total_value, profit, recorded_at
        FROM profit_history WHERE agent_id = ?
        ORDER BY recorded_at ASC
        """,
        (agent_id,),
    )
    ph_rows = cursor.fetchall()

    # Compute win/loss streaks and total P&L from profit deltas
    current_streak = 0
    best_streak = 0
    streak = 0
    winning_periods = 0
    losing_periods = 0
    max_profit_seen = 0.0
    max_drawdown = 0.0

    for i, row in enumerate(ph_rows):
        profit = row["profit"] or 0
        # Track max drawdown
        if profit > max_profit_seen:
            max_profit_seen = profit
        dd = max_profit_seen - profit
        if dd > max_drawdown:
            max_drawdown = dd

        # Compare to previous snapshot for win/loss
        if i > 0:
            prev_profit = ph_rows[i - 1]["profit"] or 0
            delta = profit - prev_profit
            if delta > 0:
                winning_periods += 1
                streak = streak + 1 if streak > 0 else 1
            elif delta < 0:
                losing_periods += 1
                streak = streak - 1 if streak < 0 else -1
            else:
                streak = 0
            best_streak = max(best_streak, abs(streak))

    current_streak = streak
    total_periods = winning_periods + losing_periods
    win_rate = (winning_periods / total_periods) if total_periods > 0 else 0.0

    # Total P&L = latest profit value
    total_pnl = ph_rows[-1]["profit"] if ph_rows else 0.0

    return {
        "total_trades": total_trades,
        "winning_trades": winning_periods,
        "losing_trades": losing_periods,
        "win_rate": win_rate,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "total_profit": total_pnl,
        "max_drawdown": max_drawdown,
    }


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

        state_meta = STATE_META.get(report.state, {})
        await broadcast_activity(ctx, {
            'type': 'state_change',
            'agent_id': agent['id'],
            'agent_name': agent['name'],
            'message_type': 'state_change',
            'state': report.state,
            'state_detail': report.detail,
            'state_symbol': report.symbol,
            'state_color': state_meta.get('color', '#8B92A5'),
            'confidence': report.confidence,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

        return {"success": True}

    # ─── POST /api/arena/thought — Agent posts a conversational thought ──

    @app.post("/api/arena/thought")
    async def arena_post_thought(report: ThoughtReport, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid agent token")

        thought_text = report.thought.strip()[:500]
        if not thought_text:
            raise HTTPException(status_code=400, detail="Thought cannot be empty")

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agent_thoughts (agent_id, content) VALUES (?, ?)",
            (agent["id"], thought_text),
        )
        conn.commit()
        conn.close()

        await broadcast_activity(ctx, {
            'type': 'thought',
            'agent_id': agent['id'],
            'agent_name': agent['name'],
            'message_type': 'thought',
            'content': thought_text,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

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

        # Get agents with live-computed stats
        cursor.execute(
            """
            SELECT a.id, a.name
            FROM agents a
            WHERE NOT EXISTS (
                SELECT 1 FROM agent_leaderboard_exclusions ale
                WHERE ale.agent_id = a.id AND COALESCE(ale.active, 1) = 1
            )
            """
        )
        agents = []
        for row in cursor.fetchall():
            aid = row["id"]
            live = _compute_arena_stats(cursor, aid)
            agents.append({
                "id": aid,
                "name": row["name"],
                "trade_count": live["total_trades"],
                "win_rate": live["win_rate"],
                "win_streak": live["current_streak"],
                "last_trade_at": None,
            })
            # Get last trade timestamp
            cursor.execute(
                "SELECT MAX(created_at) as last_signal FROM signals WHERE agent_id = ?",
                (aid,),
            )
            lt = cursor.fetchone()
            if lt and lt["last_signal"]:
                agents[-1]["last_trade_at"] = lt["last_signal"]

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
            "SELECT symbol, market, side, quantity, entry_price, current_price, opened_at, stop_loss_price, take_profit_price FROM positions WHERE agent_id = ?",
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

        # Agent stats — compute live from signals + profit_history
        stats = _compute_arena_stats(cursor, agent_id)

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

    # ─── GET /api/arena/positions — All open positions across agents ──

    @app.get("/api/arena/positions")
    async def arena_positions(agent_id: Optional[int] = None):
        conn = get_db_connection()
        cursor = conn.cursor()

        if agent_id is not None:
            cursor.execute(
                """
                SELECT p.agent_id, a.name as agent_name, p.symbol, p.market, p.side,
                       p.quantity, p.entry_price, p.current_price, p.opened_at,
                       p.stop_loss_price, p.take_profit_price
                FROM positions p
                JOIN agents a ON a.id = p.agent_id
                WHERE p.agent_id = ?
                ORDER BY p.opened_at DESC
                """,
                (agent_id,),
            )
        else:
            cursor.execute(
                """
                SELECT p.agent_id, a.name as agent_name, p.symbol, p.market, p.side,
                       p.quantity, p.entry_price, p.current_price, p.opened_at,
                       p.stop_loss_price, p.take_profit_price
                FROM positions p
                JOIN agents a ON a.id = p.agent_id
                ORDER BY p.opened_at DESC
                """,
            )

        rows = cursor.fetchall()
        conn.close()

        positions = []
        for row in rows:
            entry_price = row["entry_price"]
            current_price = row["current_price"]
            quantity = abs(row["quantity"]) if row["quantity"] else 0
            pnl = 0
            pnl_pct = 0
            if current_price and entry_price:
                if row["side"] == "long":
                    pnl = (current_price - entry_price) * quantity
                else:
                    pnl = (entry_price - current_price) * quantity
                if entry_price > 0 and quantity > 0:
                    pnl_pct = (pnl / (entry_price * quantity)) * 100

            positions.append({
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "symbol": row["symbol"],
                "market": row["market"],
                "side": row["side"],
                "quantity": row["quantity"],
                "entry_price": entry_price,
                "current_price": current_price,
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
                "opened_at": row["opened_at"],
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 1),
            })

        return {"positions": positions, "count": len(positions)}

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
                SELECT agent_id, symbol, market, side, quantity, entry_price, current_price, opened_at, stop_loss_price, take_profit_price
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

        # Get agent stats — compute live per agent
        stats_by_agent: dict[int, dict] = {}
        if agent_ids:
            for aid in agent_ids:
                stats_by_agent[aid] = _compute_arena_stats(cursor, aid)

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
                AND datetime(created_at) > datetime('now', '-5 minutes')
                """,
                agent_ids,
            )
            for row in cursor.fetchall():
                recently_active.add(row["agent_id"])

            # Also check for recent heartbeats (agents actively cycling but not posting signals)
            cursor.execute(
                f"""
                SELECT DISTINCT actor_agent_id FROM experiment_events
                WHERE event_type = 'agent_heartbeat'
                AND actor_agent_id IN ({placeholders})
                AND datetime(created_at) > datetime('now', '-10 minutes')
                """,
                agent_ids,
            )
            for row in cursor.fetchall():
                recently_active.add(row["actor_agent_id"])

            # Fetch recent thoughts for each agent (last 5)
            thoughts_by_agent: dict[int, list[dict]] = {}
            for aid in agent_ids:
                cursor.execute(
                    "SELECT content, created_at FROM agent_thoughts WHERE agent_id = ? ORDER BY id DESC LIMIT 5",
                    (aid,),
                )
                thoughts_by_agent[aid] = [
                    {"content": row["content"], "created_at": row["created_at"]}
                    for row in cursor.fetchall()
                ]
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

            # Determine position — compute total unrealized P&L across ALL positions
            all_positions = []
            total_unrealized_pnl = 0
            for pos_row in positions:
                pos_pnl = 0
                if pos_row.get("current_price") and pos_row.get("entry_price"):
                    if pos_row["side"] == "long":
                        pos_pnl = (pos_row["current_price"] - pos_row["entry_price"]) * abs(pos_row["quantity"])
                    else:
                        pos_pnl = (pos_row["entry_price"] - pos_row["current_price"]) * abs(pos_row["quantity"])
                total_unrealized_pnl += pos_pnl
                pos_pnl_pct = 0
                if pos_row.get("entry_price") and pos_row["entry_price"] > 0:
                    pos_pnl_pct = (pos_pnl / (pos_row["entry_price"] * abs(pos_row["quantity"]))) * 100 if pos_row.get("quantity") else 0
                all_positions.append({
                    "side": pos_row["side"],
                    "symbol": pos_row["symbol"],
                    "pnl": pos_pnl,
                    "pnl_pct": round(pos_pnl_pct, 1),
                    "entry_price": pos_row.get("entry_price"),
                    "current_price": pos_row.get("current_price"),
                    "stop_loss_price": pos_row.get("stop_loss_price"),
                    "take_profit_price": pos_row.get("take_profit_price"),
                    "quantity": pos_row.get("quantity"),
                    "opened_at": pos_row.get("opened_at"),
                })
            # Primary position for backwards compat (first position)
            position = all_positions[0] if all_positions else None

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

            # Today's P&L — use unrealized P&L from open positions, fall back to profit_history
            total_profit = profit_info.get("profit", 0)
            total_value = profit_info.get("total_value", 100000.0)
            if all_positions:
                today_pnl = total_unrealized_pnl
                today_pnl_pct = (total_unrealized_pnl / 100000.0) * 100
            else:
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
                "all_positions": all_positions,
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
                "thoughts": [t["content"] for t in thoughts_by_agent.get(aid, [])],
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

        # Get headlines — generated from all available agent data
        headline_data = []

        # --- Streak headlines ---
        for agent in agents:
            if agent["win_streak"] >= 3:
                headline_data.append({
                    "headline": f"{agent['name']} has won {agent['win_streak']} trades in a row.",
                    "type": "streak",
                    "agent": agent["name"],
                })

        # --- P&L leader headlines ---
        sorted_by_pnl = sorted(agents, key=lambda a: a.get("total_profit", 0), reverse=True)
        if sorted_by_pnl and sorted_by_pnl[0].get("total_profit", 0) > 0:
            top = sorted_by_pnl[0]
            headline_data.append({
                "headline": f"{top['name']} leads the arena with +${top['total_profit']:,.0f} total P&L.",
                "type": "performance",
                "agent": top["name"],
            })
        if sorted_by_pnl and sorted_by_pnl[-1].get("total_profit", 0) < 0:
            bottom = sorted_by_pnl[-1]
            headline_data.append({
                "headline": f"{bottom['name']} is underwater at -${abs(bottom['total_profit']):,.0f} total P&L.",
                "type": "performance",
                "agent": bottom["name"],
            })

        # --- Today's biggest movers ---
        sorted_by_today = sorted(agents, key=lambda a: abs(a.get("today_pnl", 0)), reverse=True)
        if sorted_by_today and abs(sorted_by_today[0].get("today_pnl", 0)) > 0:
            mover = sorted_by_today[0]
            direction = "up" if mover["today_pnl"] >= 0 else "down"
            sign = "+" if mover["today_pnl"] >= 0 else "-"
            headline_data.append({
                "headline": f"{mover['name']} is {direction} {sign}${abs(mover['today_pnl']):,.0f} ({mover['today_pnl_pct']:+.1f}%) today.",
                "type": "performance",
                "agent": mover["name"],
            })

        # --- Biggest open position P&L ---
        best_pos = None
        for agent in agents:
            for pos in agent.get("all_positions", []):
                if not best_pos or abs(pos.get("pnl_pct", 0)) > abs(best_pos.get("pnl_pct", 0)):
                    best_pos = {**pos, "agent_name": agent["name"]}
        if best_pos and abs(best_pos.get("pnl_pct", 0)) >= 5:
            sign = "+" if best_pos["pnl_pct"] >= 0 else ""
            headline_data.append({
                "headline": f"{best_pos['agent_name']} is {best_pos['side']} {best_pos['symbol']} with {sign}{best_pos['pnl_pct']:.1f}% unrealized P&L.",
                "type": "performance",
                "agent": best_pos["agent_name"],
            })

        # --- Consensus / crowding headlines ---
        position_map: dict[str, list[tuple[str, str]]] = {}  # symbol -> [(agent_name, side)]
        for agent in agents:
            for pos in agent.get("all_positions", []):
                sym = pos.get("symbol", "").upper()
                if sym:
                    position_map.setdefault(sym, []).append((agent["name"], pos["side"]))
        for sym, holders in position_map.items():
            if len(holders) >= 3:
                longs = [h for h in holders if h[1] == "long"]
                shorts = [h for h in holders if h[1] == "short"]
                if len(longs) >= 3:
                    names = ", ".join(h[0] for h in longs[:3])
                    headline_data.append({
                        "headline": f"{len(longs)} agents are all long {sym} — {names}. Is the crowd right?",
                        "type": "consensus",
                        "agent": longs[0][0],
                    })
                elif len(shorts) >= 3:
                    names = ", ".join(h[0] for h in shorts[:3])
                    headline_data.append({
                        "headline": f"{len(shorts)} agents are all short {sym} — {names}. Bearish consensus forming.",
                        "type": "consensus",
                        "agent": shorts[0][0],
                    })

        # --- Most active agent ---
        sorted_by_trades = sorted(agents, key=lambda a: a.get("trade_count", 0), reverse=True)
        if sorted_by_trades and sorted_by_trades[0].get("trade_count", 0) >= 5:
            top_trader = sorted_by_trades[0]
            headline_data.append({
                "headline": f"{top_trader['name']} is the most active trader with {top_trader['trade_count']} trades.",
                "type": "performance",
                "agent": top_trader["name"],
            })

        # --- Relationship / rivalry headlines ---
        for agent in agents:
            rels = agent.get("relationships", {})
            for target_name, rel in list(rels.items())[:3]:
                if rel.get("dislike", 0) > 0.6:
                    headline_data.append({
                        "headline": f"Tension rising: {agent['name']} distrusts {target_name} ({rel['dislike']:.0%}).",
                        "type": "rivalry",
                        "agent": agent["name"],
                    })
                    break
                if rel.get("trust", 0) > 0.8:
                    headline_data.append({
                        "headline": f"{agent['name']} trusts {target_name} ({rel['trust']:.0%}) — alliance forming.",
                        "type": "rivalry",
                        "agent": agent["name"],
                    })
                    break

        # --- High win rate ---
        for agent in agents:
            if agent.get("win_rate", 0) >= 0.7 and agent.get("trade_count", 0) >= 10:
                headline_data.append({
                    "headline": f"{agent['name']} is hitting {agent['win_rate']:.0%} win rate across {agent['trade_count']} trades.",
                    "type": "performance",
                    "agent": agent["name"],
                })

        # Sort by priority and cap at 10
        headline_priority = {"streak": 0, "rivalry": 0, "consensus": 1, "performance": 2, "dormant": 3}
        headline_data.sort(key=lambda h: headline_priority.get(h["type"], 99))

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

        # Get recent replies for those signals
        signal_ids = [s["id"] for s in recent_signals if s.get("id")]
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

        # Get recent thoughts for timeline
        cursor.execute(
            """
            SELECT t.id, t.content, t.created_at, a.name as agent_name
            FROM agent_thoughts t
            JOIN agents a ON a.id = t.agent_id
            ORDER BY t.id DESC
            LIMIT 20
            """
        )
        recent_thoughts = [dict(row) for row in cursor.fetchall()]
        conn.close()

        timeline = build_timeline_events(recent_signals, replies, limit=15, recent_thoughts=recent_thoughts)

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

    # ─── GET /api/arena/me — Current user info ─────────────────────

    @app.get("/api/arena/me")
    async def arena_me(authorization: str = Header(None)):
        try:
            agent = require_agent(authorization)
        except HTTPException:
            return {"name": None, "role": "anonymous", "is_admin": False}
        role = agent_role(agent)
        return {
            "name": agent.get("name"),
            "role": role,
            "is_admin": role == "admin",
        }

    # ─── GET /api/arena/portfolio-risk — Portfolio risk status ────

    @app.get("/api/arena/portfolio-risk")
    async def arena_portfolio_risk():
        conn = get_db_connection()
        cursor = conn.cursor()

        day_key = _utc_day_start()

        # Get today's portfolio risk state
        cursor.execute(
            "SELECT day_key, starting_equity, halted, halt_reason FROM portfolio_risk_state WHERE day_key = ?",
            (day_key,),
        )
        state_row = cursor.fetchone()
        starting_equity = float(state_row["starting_equity"]) if state_row else 0.0
        halted = int(state_row["halted"]) if state_row else 0
        halt_reason = state_row["halt_reason"] if state_row else None

        # Total portfolio equity (dynamic, live)
        total_equity = _total_portfolio_equity(cursor)

        # Per-symbol aggregate gross exposure
        cursor.execute(
            """
            SELECT symbol,
                   ABS(SUM(quantity * COALESCE(current_price, entry_price, 0))) AS gross,
                   COUNT(DISTINCT agent_id) AS agent_count
            FROM positions
            GROUP BY symbol
            """
        )
        symbol_exposure: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            sym = row["symbol"]
            gross = abs(float(row["gross"] or 0))
            pct = gross / starting_equity if starting_equity > 0 else 0.0
            symbol_exposure[sym] = {
                "value": gross,
                "pct": pct,
                "agents": int(row["agent_count"] or 0),
            }

        # Per-sector aggregate gross exposure
        sector_map = _load_sector_map()
        sector_exposure: dict[str, dict[str, Any]] = {}
        for sym, sym_data in symbol_exposure.items():
            sector = sector_map.get(sym.upper(), "unknown")
            if sector not in sector_exposure:
                sector_exposure[sector] = {"value": 0.0, "pct": 0.0}
            sector_exposure[sector]["value"] += sym_data["value"]
        for sector, data in sector_exposure.items():
            data["pct"] = data["value"] / starting_equity if starting_equity > 0 else 0.0

        # Crowding map: symbol → [(agent_name, side)]
        cursor.execute(
            """
            SELECT p.symbol, p.side, a.name AS agent_name
            FROM positions p
            JOIN agents a ON a.id = p.agent_id
            """
        )
        crowding: dict[str, list[dict[str, str]]] = {}
        for row in cursor.fetchall():
            sym = row["symbol"]
            if sym not in crowding:
                crowding[sym] = []
            crowding[sym].append({"agent": row["agent_name"], "side": row["side"]})

        # Daily P&L
        daily_pnl = total_equity - starting_equity if starting_equity > 0 else 0.0
        daily_pnl_pct = daily_pnl / starting_equity if starting_equity > 0 else 0.0

        # Current thresholds from env vars
        thresholds = {
            "max_symbol_pct": float(os.getenv("PORTFOLIO_MAX_SYMBOL_PCT", "0.35")),
            "max_sector_pct": float(os.getenv("PORTFOLIO_MAX_SECTOR_PCT", "0.50")),
            "max_unknown_pct": float(os.getenv("PORTFOLIO_MAX_UNKNOWN_PCT", "0.10")),
            "max_crowding": int(os.getenv("PORTFOLIO_MAX_CROWDING", "3")),
            "max_daily_loss_pct": float(os.getenv("PORTFOLIO_MAX_DAILY_LOSS_PCT", "0.05")),
        }

        conn.close()

        return {
            "total_equity": total_equity,
            "starting_equity": starting_equity,
            "symbol_exposure": symbol_exposure,
            "sector_exposure": sector_exposure,
            "crowding": crowding,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "halted": halted,
            "halt_reason": halt_reason,
            "thresholds": thresholds,
        }

    # ─── POST /api/arena/portfolio-risk/unhalt — Admin un-halt ───

    @app.post("/api/arena/portfolio-risk/unhalt")
    async def arena_portfolio_risk_unhalt(authorization: str = Header(None)):
        require_admin(authorization)

        conn = get_db_connection()
        cursor = conn.cursor()
        day_key = _utc_day_start()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        cursor.execute(
            """
            UPDATE portfolio_risk_state
            SET halted = 0, halt_reason = NULL, updated_at = ?
            WHERE day_key = ?
            """,
            (now, day_key),
        )
        conn.commit()

        # Return updated state
        cursor.execute(
            "SELECT day_key, starting_equity, halted, halt_reason FROM portfolio_risk_state WHERE day_key = ?",
            (day_key,),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="No portfolio risk state found for today")

        return {
            "day_key": row["day_key"],
            "starting_equity": float(row["starting_equity"]),
            "halted": int(row["halted"]),
            "halt_reason": row["halt_reason"],
            "unhalted": True,
        }

    # ─── POST /api/arena/reset-portfolio — Reset all trading data ──

    @app.post("/api/arena/reset-portfolio")
    async def arena_reset_portfolio(authorization: str = Header(None)):
        require_admin(authorization)

        conn = get_db_connection()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            # Tables to DELETE entirely (trading/position/signal/history data)
            tables_to_clear = [
                "positions",
                "signal_replies",
                "signal_predictions",
                "signal_quality_scores",
                "signals",
                "profit_history",
                "trading_risk_state",
                "portfolio_risk_state",
                "trading_decision_log",
                "polymarket_settlements",
                "agent_messages",
                "agent_thoughts",
                "agent_reward_ledger",
                "experiment_events",
                "network_edges",
                "agent_metric_snapshots",
                "challenge_submission_votes",
                "challenge_trades",
                "challenge_results",
                "challenge_submissions",
                "challenge_team_trades",
                "challenge_team_submissions",
                "team_messages",
                "team_contributions",
                "team_submissions",
                "team_results",
            ]

            deleted_counts = {}
            for table in tables_to_clear:
                cursor.execute(f"DELETE FROM {table}")
                deleted_counts[table] = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

            # Reset agent financial fields but keep identity, tokens, configs
            cursor.execute(
                "UPDATE agents SET cash = 10000.0, deposited = 0.0, points = 0"
            )
            agents_reset = cursor.rowcount if cursor.rowcount else 0

            # Reset signal_sequence back to empty
            cursor.execute("DELETE FROM signal_sequence")

            conn.commit()

            await broadcast_activity(
                ctx,
                {
                    "type": "portfolio_reset",
                    "message": "Portfolio has been reset — all positions, signals, and profit history cleared.",
                    "timestamp": now,
                },
            )
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f"Reset failed: {exc}")
        finally:
            conn.close()

        return {
            "success": True,
            "agents_reset": agents_reset,
            "deleted": deleted_counts,
            "message": "Portfolio reset complete. All agents reset to $100,000 cash.",
        }
