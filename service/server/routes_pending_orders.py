"""
routes_pending_orders.py — Pending stop-limit order API endpoints.

POST   /api/signals/pending          — Create a pending stop-limit order
GET    /api/signals/pending          — List pending orders for agent
GET    /api/signals/pending/{id}     — Get single pending order
DELETE /api/signals/pending/{id}     — Cancel a pending order
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from database import get_db_connection
from routes_shared import RouteContext, utc_now_iso_z
from services import _get_agent_by_token
from utils import _extract_token


class PendingOrderRequest(BaseModel):
    symbol: str
    market: str = "us-stock"
    side: str = "long"  # "long" or "short"
    order_type: str = "stop_limit"  # "stop_limit" or "stop_market"
    stop_price: float
    limit_price: Optional[float] = None
    quantity: float
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_sl_pct: Optional[float] = None
    trailing_activation_pct: Optional[float] = None
    expires_at_minutes: int = 30
    entry_score: Optional[float] = None
    scan_data: Optional[dict] = None


def register_pending_order_routes(app: FastAPI, ctx: RouteContext) -> None:

    @app.post("/api/signals/pending")
    async def create_pending_order(
        data: PendingOrderRequest, authorization: str = Header(None),
    ):
        """Create a pending stop-limit order."""
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")

        agent_id = agent["id"]

        # Validate
        if data.side not in ("long", "short"):
            raise HTTPException(status_code=400, detail="side must be 'long' or 'short'")
        if data.order_type not in ("stop_limit", "stop_market"):
            raise HTTPException(status_code=400, detail="order_type must be 'stop_limit' or 'stop_market'")
        if data.stop_price <= 0:
            raise HTTPException(status_code=400, detail="stop_price must be positive")
        if data.quantity <= 0:
            raise HTTPException(status_code=400, detail="quantity must be positive")
        if data.order_type == "stop_limit" and data.limit_price is None:
            raise HTTPException(status_code=400, detail="limit_price required for stop_limit orders")

        # Compute expiry
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=data.expires_at_minutes)).isoformat()

        scan_json = json.dumps(data.scan_data) if data.scan_data else None

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO pending_orders
                    (agent_id, symbol, market, side, order_type, stop_price, limit_price,
                     quantity, stop_loss_price, take_profit_price, trailing_sl_pct,
                     trailing_activation_pct, status, created_at, expires_at, entry_score, scan_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)""",
                (agent_id, data.symbol, data.market, data.side, data.order_type,
                 data.stop_price, data.limit_price, data.quantity,
                 data.stop_loss_price, data.take_profit_price,
                 data.trailing_sl_pct, data.trailing_activation_pct,
                 utc_now_iso_z(), expires_at, data.entry_score, scan_json),
            )
            order_id = cursor.lastrowid
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create order: {e}")
        finally:
            conn.close()

        return {"pending_order_id": order_id, "status": "PENDING", "expires_at": expires_at}

    @app.get("/api/signals/pending")
    async def list_pending_orders(
        status: str = "PENDING", authorization: str = Header(None),
    ):
        """List pending orders for the authenticated agent."""
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")

        agent_id = agent["id"]
        valid_statuses = ("PENDING", "FILLED", "CANCELLED", "EXPIRED", "ALL")
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"status must be one of {valid_statuses}")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            if status == "ALL":
                cursor.execute(
                    """SELECT * FROM pending_orders WHERE agent_id = ?
                       ORDER BY created_at DESC LIMIT 100""",
                    (agent_id,),
                )
            else:
                cursor.execute(
                    """SELECT * FROM pending_orders WHERE agent_id = ? AND status = ?
                       ORDER BY created_at DESC LIMIT 100""",
                    (agent_id, status),
                )
            rows = cursor.fetchall()
        finally:
            conn.close()

        orders = []
        for row in rows:
            order = dict(row)
            if order.get("scan_data"):
                try:
                    order["scan_data"] = json.loads(order["scan_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
            orders.append(order)

        return {"orders": orders, "count": len(orders)}

    @app.get("/api/signals/pending/{order_id}")
    async def get_pending_order(order_id: int, authorization: str = Header(None)):
        """Get a single pending order by ID."""
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM pending_orders WHERE id = ? AND agent_id = ?",
                (order_id, agent["id"]),
            )
            row = cursor.fetchone()
        finally:
            conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Order not found")

        order = dict(row)
        if order.get("scan_data"):
            try:
                order["scan_data"] = json.loads(order["scan_data"])
            except (json.JSONDecodeError, TypeError):
                pass
        return order

    @app.delete("/api/signals/pending/{order_id}")
    async def cancel_pending_order(order_id: int, authorization: str = Header(None)):
        """Cancel a pending order."""
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE pending_orders SET status = 'CANCELLED'
                   WHERE id = ? AND agent_id = ? AND status = 'PENDING'""",
                (order_id, agent["id"]),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                raise HTTPException(status_code=404, detail="Order not found or not pending")
            conn.commit()
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to cancel: {e}")
        finally:
            conn.close()

        return {"order_id": order_id, "status": "CANCELLED"}
