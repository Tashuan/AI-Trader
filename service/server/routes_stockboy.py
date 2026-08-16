"""FastAPI routes for StockBoy supervisor status, snapshots, and controls."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from permissions import STOCKBOY_SUPERVISOR_CAPABILITY, require_capability
from routes_shared import RouteContext
from stockboy_manager import start, status, stop
from stockboy_models import (
    StockBoyActionRequest, StockBoyEnableRequest, StockBoyKillSwitchRequest,
    StockBoyOverrideRequest, StockBoyOverrideResetRequest,
)
from stockboy_overrides import create_override, reset_overrides
from stockboy_policy import PolicyViolation, validate_override
from stockboy_provision import get_supervisor_token
from stockboy_service import (
    add_commentary, add_observation, build_snapshot, execute_action, get_status, set_state,
)
from stockboy_entry_detector import evaluate_entry_quality


def _supervisor_agent(authorization: str | None) -> dict:
    try:
        return require_capability(authorization, STOCKBOY_SUPERVISOR_CAPABILITY)
    except HTTPException:
        raise


def _resolve_supervisor_auth(authorization: str | None) -> str | None:
    """Return the authorization header value, falling back to the provisioned
    supervisor token from disk when no header is provided.

    This lets the local Arena frontend read status/snapshot without a login
    flow — the backend supplies the supervisor token on its behalf.
    Action endpoints (POST) still require an explicit Authorization header.
    """
    if authorization and authorization.strip():
        return authorization
    token = get_supervisor_token()
    if token:
        return f"Bearer {token}"
    return None


def register_stockboy_routes(app: FastAPI, ctx: RouteContext) -> None:
    @app.get("/api/stockboy/token")
    async def stockboy_frontend_token():
        """Return the supervisor token so the local frontend can authenticate
        StockBoy read endpoints. Only intended for local development."""
        token = get_supervisor_token()
        if not token:
            raise HTTPException(status_code=503, detail="Supervisor token not provisioned")
        return {"token": token}

    @app.get("/api/stockboy/status")
    async def stockboy_status(authorization: str = Header(None)):
        _supervisor_agent(_resolve_supervisor_auth(authorization))
        return status()

    @app.get("/api/stockboy/snapshot")
    async def stockboy_snapshot(authorization: str = Header(None)):
        _supervisor_agent(_resolve_supervisor_auth(authorization))
        mgr_status = status()
        return build_snapshot(running=bool(mgr_status.get("running"))).model_dump()

    @app.post("/api/stockboy/start")
    async def stockboy_start(authorization: str = Header(None)):
        _supervisor_agent(authorization)
        return start()

    @app.post("/api/stockboy/stop")
    async def stockboy_stop(authorization: str = Header(None)):
        _supervisor_agent(authorization)
        return stop()

    @app.post("/api/stockboy/action")
    async def stockboy_action(data: StockBoyActionRequest, authorization: str = Header(None)):
        _supervisor_agent(authorization)
        result = execute_action(data)
        if not result.success and result.status == "rejected":
            raise HTTPException(status_code=403, detail=result.message)
        return result.model_dump()

    @app.post("/api/stockboy/enable")
    async def stockboy_enable(data: StockBoyEnableRequest, authorization: str = Header(None)):
        _supervisor_agent(authorization)
        return set_state(enabled=data.enabled).model_dump()

    @app.post("/api/stockboy/kill-switch")
    async def stockboy_kill_switch(data: StockBoyKillSwitchRequest, authorization: str = Header(None)):
        _supervisor_agent(authorization)
        state = set_state(kill_switch=data.engaged, actions_enabled=not data.engaged)
        add_commentary(
            f"StockBoy kill switch {'engaged' if data.engaged else 'disengaged'}: {data.reason}"[:2000],
            kind="control", severity="error" if data.engaged else "info",
        )
        return state.model_dump()

    @app.post("/api/stockboy/override")
    async def stockboy_override(data: StockBoyOverrideRequest, authorization: str = Header(None)):
        _supervisor_agent(authorization)
        try:
            validate_override(data.runner_key, data.field_path, data.new_value)
        except PolicyViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        return create_override(
            data.runner_key, data.field_path, data.new_value,
            data.rationale, data.expires_in_minutes,
        )

    @app.post("/api/stockboy/override/reset")
    async def stockboy_override_reset(data: StockBoyOverrideResetRequest, authorization: str = Header(None)):
        _supervisor_agent(authorization)
        return reset_overrides(data.runner_key, data.reason)

    @app.post("/api/stockboy/evaluate-entry")
    async def stockboy_evaluate_entry(data: dict, authorization: str = Header(None)):
        """Webhook called by FenceBarRunner when it creates a pending order.

        Evaluates entry quality and cancels the order if vetoed.
        """
        _supervisor_agent(authorization)
        symbol = data.get("symbol", "")
        side = data.get("side", "long")
        order_id = data.get("order_id")
        runner_key = data.get("runner_key", "fencebarrunner")
        try:
            result = evaluate_entry_quality(symbol, side, data)
            add_observation(
                runner_key=runner_key, severity="warning" if result["decision"] == "veto" else "info",
                category="entry_quality", message=f"{symbol} {side}: {result['decision']} — {'; '.join(result['reasons'])}",
                metadata={"symbol": symbol, "side": side, "order_id": order_id, **result["metrics"]},
            )
            if result["decision"] == "veto" and order_id is not None:
                cancel_req = StockBoyActionRequest(
                    idempotency_key=f"sb-entry-veto-{order_id}",
                    runner_key=runner_key, action_type="cancel_order",
                    target_order_id=int(order_id),
                    rationale=f"Entry vetoed: {'; '.join(result['reasons'])}",
                    policy_rule="entry_quality",
                )
                cancel_resp = execute_action(cancel_req)
                add_commentary(
                    f"Entry veto on {symbol} {side}: order {order_id} cancelled ({cancel_resp.message})",
                    kind="entry_quality", severity="warning",
                )
                result["cancel_result"] = cancel_resp.model_dump()
            return {"decision": result["decision"], "reasons": result["reasons"], "metrics": result["metrics"]}
        except Exception as exc:
            add_commentary(f"Entry evaluation failed for {symbol}: {exc}", kind="error", severity="error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
