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
from stockboy_service import (
    add_commentary, build_snapshot, execute_action, get_status, set_state,
)


def _supervisor_agent(authorization: str | None) -> dict:
    try:
        return require_capability(authorization, STOCKBOY_SUPERVISOR_CAPABILITY)
    except HTTPException:
        raise


def register_stockboy_routes(app: FastAPI, ctx: RouteContext) -> None:
    @app.get("/api/stockboy/status")
    async def stockboy_status(authorization: str = Header(None)):
        _supervisor_agent(authorization)
        return status()

    @app.get("/api/stockboy/snapshot")
    async def stockboy_snapshot(authorization: str = Header(None)):
        _supervisor_agent(authorization)
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
