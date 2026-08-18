"""Paperless MNQ 1-minute high-R:R scalp shadow monitor.

This monitor observes yfinance 1-minute MNQ futures bars during US RTH and
records hypothetical trades for the frozen research candidate. It has no
broker imports and no order path by design.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from runner_narrative import RunnerNarrative

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger("MNQScalpShadow")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())

ET = ZoneInfo("America/New_York")
STATE_FILE = Path(__file__).with_name("mnq_scalp_shadow_state.json")
SYMBOL = "MNQ=F"
TICK_SIZE = 0.25
TICK_VALUE = 0.50
MULTIPLIER = 2.0
STOP_TICKS = 10
TARGET_TICKS = 40
SLIPPAGE_TICKS = 3
STOP_GRACE_MINUTES = 5
RISK_PER_TRADE_PCT = 1.0
MAX_CONTRACTS = 4
MAX_ENTRY = dt_time(10, 0)
FORCE_EXIT = dt_time(15, 55)
POLL_SECONDS = 30
narrative = RunnerNarrative("mnqscalpshadow")


def _default_state() -> dict:
    return {
        "mode": "shadow_only",
        "paper_orders": False,
        "live_orders": False,
        "symbol": SYMBOL,
        "config_version": "mnq-scalp-shadow-v0.1",
        "session_date": None,
        "cycles_run": 0,
        "last_cycle_time": None,
        "last_bar_time": None,
        "range_high": None,
        "range_low": None,
        "first_post_range_seen": False,
        "confirmation_side": None,
        "confirmation_count": 0,
        "open_trade": None,
        "shadow_trades": [],
        "daily_pnl": 0.0,
        "status": "stopped",
        "last_error": None,
    }


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    merged = _default_state()
    merged.update(state)
    return merged


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n")
    tmp.replace(STATE_FILE)


def _bars() -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    frame = yf.Ticker(SYMBOL).history(period="1d", interval="1m", prepost=False)
    if frame.empty:
        return frame
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(ET)
    else:
        frame.index = frame.index.tz_convert(ET)
    return frame[(frame.index.time >= dt_time(9, 30)) & (frame.index.time <= FORCE_EXIT)]


def _reset_session(state: dict, session_date: str) -> None:
    keep = {key: state.get(key) for key in (
        "mode", "paper_orders", "live_orders", "symbol", "config_version",
        "cycles_run", "last_cycle_time",
    )}
    state.clear()
    state.update(_default_state())
    state.update(keep)
    state["session_date"] = session_date
    state["status"] = "observing"


def _contracts(equity: float) -> int:
    risk_budget = equity * RISK_PER_TRADE_PCT / 100
    risk_per_contract = (STOP_TICKS + 2 * SLIPPAGE_TICKS) * TICK_VALUE
    return min(MAX_CONTRACTS, max(0, int(risk_budget / risk_per_contract)))


def _fill_price(price: float, side: str, entry: bool) -> float:
    slip = SLIPPAGE_TICKS * TICK_SIZE
    if entry:
        return price + slip if side == "long" else price - slip
    return price - slip if side == "long" else price + slip


def _exit_for_trade(trade: dict, row: pd.Series, ts: datetime) -> tuple[str, float] | None:
    if ts.time() >= FORCE_EXIT:
        return "force_exit", float(row["Close"])
    elapsed = (ts - datetime.fromisoformat(trade["entry_time"])).total_seconds() / 60
    in_grace = elapsed < STOP_GRACE_MINUTES
    if trade["side"] == "long":
        target = float(row["High"]) >= trade["target_price"]
        stop = float(row["Low"]) <= trade["stop_price"]
    else:
        target = float(row["Low"]) <= trade["target_price"]
        stop = float(row["High"]) >= trade["stop_price"]
    if target and stop:
        return ("take_profit", trade["target_price"]) if in_grace else ("stop_loss", trade["stop_price"])
    if target:
        return "take_profit", trade["target_price"]
    if stop and not in_grace:
        return "stop_loss", trade["stop_price"]
    return None


def process_bar(state: dict, ts: datetime, row: pd.Series, equity: float = 10000.0) -> None:
    if state["open_trade"]:
        trade = state["open_trade"]
        result = _exit_for_trade(trade, row, ts)
        if result:
            reason, raw_exit = result
            exit_price = _fill_price(raw_exit, trade["side"], False)
            price_diff = exit_price - trade["entry_price"] if trade["side"] == "long" else trade["entry_price"] - exit_price
            pnl = price_diff * MULTIPLIER * trade["qty"] - 2.50 * trade["qty"]
            trade.update({"exit_time": ts.isoformat(), "exit_price": exit_price, "pnl": pnl, "reason": reason})
            state["shadow_trades"].append(trade)
            state["daily_pnl"] += pnl
            state["open_trade"] = None
            narrative.emit("shadow", "exit", "measured", priority="action", symbol=SYMBOL, facts=trade)
        return

    if ts.time() <= dt_time(9, 34):
        state["range_high"] = max(state["range_high"] or float(row["High"]), float(row["High"]))
        state["range_low"] = min(state["range_low"] or float(row["Low"]), float(row["Low"]))
        return
    if state["range_high"] is None or ts.time() > MAX_ENTRY:
        return
    if not state["first_post_range_seen"]:
        state["first_post_range_seen"] = True
        return
    close = float(row["Close"])
    side = "long" if close > state["range_high"] else "short" if close < state["range_low"] else None
    if side is None:
        state["confirmation_side"] = None
        state["confirmation_count"] = 0
        return
    if side == state["confirmation_side"]:
        state["confirmation_count"] += 1
    else:
        state["confirmation_side"] = side
        state["confirmation_count"] = 1
    if state["confirmation_count"] < 2:
        return
    qty = _contracts(equity)
    if qty < 1:
        return
    entry = _fill_price(close, side, True)
    stop = entry - STOP_TICKS * TICK_SIZE if side == "long" else entry + STOP_TICKS * TICK_SIZE
    target = entry + TARGET_TICKS * TICK_SIZE if side == "long" else entry - TARGET_TICKS * TICK_SIZE
    state["open_trade"] = {
        "symbol": SYMBOL, "side": side, "entry_time": ts.isoformat(),
        "signal_price": close, "entry_price": entry,
        "stop_price": stop, "target_price": target, "qty": qty,
        "mode": "shadow_only", "orders_submitted": False,
    }
    narrative.emit("shadow", "entry", "observed", priority="action", symbol=SYMBOL, facts=state["open_trade"])


def run_cycle(state: dict) -> dict:
    frame = _bars()
    now = datetime.now(ET)
    state["cycles_run"] += 1
    state["last_cycle_time"] = now.isoformat()
    state["status"] = "observing" if dt_time(9, 30) <= now.time() <= FORCE_EXIT else "outside_rth"
    if frame.empty:
        save_state(state)
        return state
    session_date = str(frame.index[-1].date())
    if state.get("session_date") != session_date:
        _reset_session(state, session_date)
    for ts, row in frame.iterrows():
        ts = ts.to_pydatetime()
        if state.get("last_bar_time") and ts.isoformat() <= state["last_bar_time"]:
            continue
        process_bar(state, ts, row)
        state["last_bar_time"] = ts.isoformat()
    save_state(state)
    return state


def run_loop(stop_event: threading.Event, poll_interval: int = POLL_SECONDS) -> None:
    state = load_state()
    state["status"] = "running_shadow_only"
    save_state(state)
    narrative.emit("startup", "ready", "started", priority="action", message="MNQ shadow monitor started; no orders enabled.")
    while not stop_event.is_set():
        try:
            run_cycle(state)
        except Exception as exc:
            state["last_error"] = str(exc)
            state["status"] = "error_shadow_only"
            save_state(state)
            logger.exception("MNQ shadow cycle failed")
        stop_event.wait(poll_interval)
    state["status"] = "stopped"
    save_state(state)
    narrative.emit("shutdown", "stopped", "complete", priority="action", message="MNQ shadow monitor stopped.")
