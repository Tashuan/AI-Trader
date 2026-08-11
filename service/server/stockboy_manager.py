"""Lifecycle manager for the StockBoy supervisor loop."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from stockboy_overrides import expire_overrides
from stockboy_service import add_commentary, add_journal, build_snapshot, get_status, set_state

logger = logging.getLogger("StockBoy")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[StockBoy] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

_DEFAULT_INTERVAL = 60
_MIN_INTERVAL = 10
_MAX_INTERVAL = 3600
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None


def _interval() -> int:
    try:
        return max(_MIN_INTERVAL, min(_MAX_INTERVAL, int(os.getenv("STOCKBOY_POLL_INTERVAL", str(_DEFAULT_INTERVAL)))))
    except ValueError:
        return _DEFAULT_INTERVAL


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
