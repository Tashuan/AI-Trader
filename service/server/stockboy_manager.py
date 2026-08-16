"""Lifecycle manager for the StockBoy supervisor loop."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from stockboy_overrides import expire_overrides, create_override
from stockboy_service import (
    add_commentary, add_journal, add_observation, build_snapshot,
    execute_action, get_status, set_state,
)
from stockboy_models import StockBoyActionRequest
from stockboy_market_data import fetch_recent_bars, fetch_spy_atr
from stockboy_premarket import evaluate_vol_override
from stockboy_position_monitor import monitor_position

logger = logging.getLogger("StockBoy")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[StockBoy] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

_ET = ZoneInfo("America/New_York")
_DEFAULT_INTERVAL = 60
_MIN_INTERVAL = 10
_MAX_INTERVAL = 3600
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_last_premarket_date: Optional[str] = None
_last_position_monitor_at: Optional[datetime] = None
_FENCEBAR_KEY = "fencebarrunner"
_FENCEBAR_AGENT = "FenceBarRunner"
_POSITION_MONITOR_INTERVAL = timedelta(minutes=5)


def _interval() -> int:
    try:
        return max(_MIN_INTERVAL, min(_MAX_INTERVAL, int(os.getenv("STOCKBOY_POLL_INTERVAL", str(_DEFAULT_INTERVAL)))))
    except ValueError:
        return _DEFAULT_INTERVAL


def _run_premarket_check() -> None:
    """Run the vol-filter override evaluation once per trading day (~09:00 ET)."""
    global _last_premarket_date
    now_et = datetime.now(_ET)
    today = now_et.date().isoformat()
    if _last_premarket_date == today:
        return
    if now_et.hour < 9:
        return
    try:
        atr = fetch_spy_atr()
        if atr is None:
            add_commentary("Premarket vol-override skipped — SPY ATR unavailable", kind="premarket", severity="info")
            return
        symbols = [s.strip() for s in os.getenv("STOCKBOY_FENCEBAR_UNIVERSE", "SPY,QQQ,NVDA,AAPL").split(",") if s.strip()]
        result = evaluate_vol_override(symbols, atr)
        add_observation(
            runner_key=_FENCEBAR_KEY, severity="info" if not result["override"] else "warning",
            category="vol_override", message=str(result), metadata=result,
        )
        if result["override"]:
            create_override(
                _FENCEBAR_KEY, "entry_criteria.atr_min_pct", result["new_atr_threshold"],
                "; ".join(result["reasons"]), result["expires_in_minutes"],
            )
            add_commentary(
                f"Vol-filter override applied: ATR {atr:.2f}% → {result['new_atr_threshold']}%. "
                f"Reasons: {'; '.join(result['reasons'])}",
                kind="premarket", severity="warning",
            )
        else:
            add_commentary(f"Vol-filter override not applied: {result['reasons'][0]}", kind="premarket", severity="info")
        _last_premarket_date = today
    except Exception as exc:
        logger.exception("Premarket vol-override check failed")
        add_commentary(f"Premarket vol-override check failed: {exc}", kind="error", severity="error")


def _run_position_monitor() -> None:
    """Monitor FenceBarRunner open positions every 5 minutes."""
    global _last_position_monitor_at
    now = datetime.now(timezone.utc)
    if _last_position_monitor_at and (now - _last_position_monitor_at) < _POSITION_MONITOR_INTERVAL:
        return
    _last_position_monitor_at = now
    try:
        snapshot = build_snapshot(running=True)
        positions = [p for p in snapshot.positions if p.agent_name == _FENCEBAR_AGENT]
        for pos in positions:
            _evaluate_one_position(pos)
    except Exception as exc:
        logger.exception("Position monitor failed")
        add_commentary(f"Position monitor failed: {exc}", kind="error", severity="error")


def _evaluate_one_position(pos) -> None:
    """Run the position monitor detector for a single FenceBarRunner position."""
    try:
        bars = fetch_recent_bars(pos.symbol, interval="5Min", bars_back=78)
        if bars is None or bars.empty:
            return
        position_dict = {
            "entry_price": pos.entry_price, "side": pos.side,
            "entry_timestamp": pos.opened_at, "stop_loss_price": pos.stop_loss_price,
            "current_price": pos.current_price,
        }
        result = monitor_position(position_dict, bars)
        add_observation(
            runner_key=_FENCEBAR_KEY, severity="info",
            category=f"position_monitor:{result['action']}",
            message=result["rationale"], metadata={"symbol": pos.symbol, **result["metrics"]},
        )
        if result["action"] == "set_stop_breakeven":
            _execute_stop_breakeven(pos, result["rationale"])
        elif result["action"] == "close_position":
            _execute_close(pos, result["rationale"])
    except Exception as exc:
        logger.exception("Position monitor evaluation failed for %s", getattr(pos, "symbol", "?"))


def _execute_stop_breakeven(pos, rationale: str) -> None:
    req = StockBoyActionRequest(
        idempotency_key=f"sb-be-{pos.position_id}-{uuid.uuid4().hex[:8]}",
        runner_key=_FENCEBAR_KEY, action_type="set_stop",
        target_position_id=pos.position_id, stop_loss_price=pos.entry_price,
        rationale=rationale, policy_rule="stop_tighten_only",
    )
    resp = execute_action(req)
    add_commentary(
        f"Breakeven stop on {pos.symbol}: {resp.message}",
        kind="position_monitor", severity="warning" if resp.success else "error",
    )


def _execute_close(pos, rationale: str) -> None:
    req = StockBoyActionRequest(
        idempotency_key=f"sb-tp-{pos.position_id}-{uuid.uuid4().hex[:8]}",
        runner_key=_FENCEBAR_KEY, action_type="close_position",
        target_position_id=pos.position_id, rationale=rationale,
        policy_rule="early_exit",
    )
    resp = execute_action(req)
    add_commentary(
        f"Early exit on {pos.symbol}: {resp.message}",
        kind="position_monitor", severity="warning" if resp.success else "error",
    )


def _cycle() -> None:
    """Run one deterministic supervisor cycle.

    The backend loop is intentionally deterministic — it builds snapshots,
    detects anomalies, expires overrides, and writes commentary/journal.
    AI reasoning happens in a separate Devin workspace session that calls
    the same /api/stockboy/* endpoints via curl. Both coexist.
    """
    status = get_status()
    now = datetime.now(timezone.utc)
    cycle_number = status.cycles_run + 1
    set_state(last_heartbeat_at=now.isoformat().replace("+00:00", "Z"), last_error=None, cycles_run=cycle_number)
    try:
        expired = expire_overrides()
        if expired:
            add_commentary(f"Expired {expired} StockBoy runner override(s); defaults remain authoritative", kind="maintenance", severity="info")

        _run_premarket_check()
        _run_position_monitor()

        snapshot = build_snapshot(running=True)
        issues = len(snapshot.risk_anomalies)
        summary = (
            f"Overwatch cycle {cycle_number}: {len(snapshot.runners)} runners, "
            f"{snapshot.portfolio.open_position_count} positions, "
            f"{issues} anomalies, ${snapshot.portfolio.total_unrealized_pnl:,.2f} unrealized P&L."
        )
        add_commentary(summary, kind="cycle", severity="warning" if issues else "info", dedup_key=f"cycle:{cycle_number}")
        if issues:
            add_journal(summary, entry_type="anomaly", title=f"Overwatch cycle {cycle_number} — anomaly review")
        else:
            add_journal(summary, entry_type="cycle", title=f"Overwatch cycle {cycle_number}")

        logger.info("Cycle %d complete: %d runners, %d positions, %d anomalies", cycle_number, len(snapshot.runners), snapshot.portfolio.open_position_count, issues)

        next_at = (datetime.now(timezone.utc) + timedelta(seconds=_interval())).isoformat().replace("+00:00", "Z")
        set_state(last_cycle_at=now.isoformat().replace("+00:00", "Z"), next_cycle_at=next_at, last_error=None)
    except Exception as exc:
        logger.exception("Supervisor cycle failed")
        add_commentary(f"StockBoy cycle failed: {exc}", kind="error", severity="error")
        set_state(last_error=str(exc), last_heartbeat_at=now.isoformat().replace("+00:00", "Z"))


def _run_loop(stop_event: threading.Event) -> None:
    logger.info("StockBoy supervisor loop started")
    while not stop_event.is_set():
        _cycle()
        stop_event.wait(_interval())
    logger.info("StockBoy supervisor loop stopped")


def start() -> dict:
    """Start StockBoy once and enable its persisted supervisor state."""
    global _thread, _stop_event
    with _lock:
        if _thread and _thread.is_alive():
            return {"success": False, "message": "StockBoy is already running"}
        _stop_event = threading.Event()
        set_state(enabled=True, last_error=None)
        _thread = threading.Thread(target=_run_loop, args=(_stop_event,), name="ManagedSupervisor-stockboy", daemon=True)
        _thread.start()
        return {"success": True, "message": "Started StockBoy", "thread": _thread.name}


def stop() -> dict:
    """Stop the loop without stopping runners or changing positions."""
    global _thread, _stop_event
    with _lock:
        if not _thread or not _thread.is_alive():
            set_state(enabled=False)
            return {"success": False, "message": "StockBoy is not running"}
        _stop_event.set()
        _thread.join(timeout=10)
        running = _thread.is_alive()
        if not running:
            _thread = None
            _stop_event = None
        set_state(enabled=False)
        return {"success": not running, "message": "Stopped StockBoy" if not running else "Stop requested for StockBoy"}


def status() -> dict:
    """Return persisted and process-local supervisor status."""
    result = get_status().model_dump()
    result["running"] = bool(_thread and _thread.is_alive())
    result["thread"] = _thread.name if _thread and _thread.is_alive() else None
    result["bot_type"] = "supervisor"
    return result
