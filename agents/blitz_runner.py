#!/usr/bin/env python3
"""
BlitzRunner — Deterministic Goal Runner Agent

Executes the exact same scan_core logic as the backtester, with zero LLM
judgment in the loop.  Every cycle:

  1. Fetch config + strategy params from the API
  2. Run scan.py run_scan() for indicators + ranked setups + position reviews
  3. If any position has verdict "EXIT" → close it immediately via POST /signals/realtime
  4. If no position and ranked_setups is non-empty → enter the top-ranked setup
     with goal-aware sizing, ATR-based SL/TP, and trailing stop params
  5. If holding a position and a new setup scores switch_threshold_pct higher → switch
  6. Persist state (consecutive_losses, reentry_cooldown) to a JSON file

This agent is launched from the Arena UI alongside the AI agent, giving a
clean A/B comparison between deterministic strategy execution and AI-overlay.
"""

import json
import os
import sys
import time
import logging
import argparse
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

# workspace dir for scan.py
_WORKSPACE_DIR = os.path.join(_AGENTS_DIR, "workspaces", "blitztrader")
if _WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, _WORKSPACE_DIR)

import scan_core
from strategy_registry import effective_params, position_notional
from runner_narrative import RunnerNarrative


# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("BlitzRunner")
narrative = RunnerNarrative("blitzrunner")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[BlitzRunner] %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False


# ── Constants ───────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/api"
STATE_FILE = os.path.join(_AGENTS_DIR, "blitz_runner_state.json")
DEFAULT_POLL_INTERVAL = 120  # seconds


# ============================================================
# State Persistence
# ============================================================

_DEFAULT_STATE = {
    "consecutive_losses": 0,
    "reentry_cooldown": {},  # symbol -> remaining cycles
    "last_cycle_time": None,
    "cycles_run": 0,
}


def load_state() -> dict:
    """Load persisted state from JSON file."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                # Merge with defaults to handle new fields
                merged = dict(_DEFAULT_STATE)
                merged.update(state)
                return merged
    except Exception as e:
        logger.warning(f"Could not load state file: {e}")
    return dict(_DEFAULT_STATE)


def save_state(state: dict) -> None:
    """Persist state atomically so an interrupted cycle cannot corrupt it."""
    try:
        temp_file = f"{STATE_FILE}.tmp"
        with open(temp_file, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        logger.warning(f"Could not save state file: {e}")


# ============================================================
# API Helpers
# ============================================================

def _api_get(token: str, path: str) -> dict:
    """GET from API with auth token."""
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _api_post(token: str, path: str, body: dict) -> dict:
    """POST to API with auth token."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _api_patch(token: str, path: str, body: dict) -> dict:
    """PATCH to API with auth token."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PATCH", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def post_thought(token: str, text: str) -> None:
    """Post a thought to the platform so it shows up in the dashboard UI."""
    try:
        _api_post(token, "/arena/thought", {"thought": text[:500]})
    except Exception:
        pass


def post_discussion(token: str, title: str, content: str, market: str = "crypto", symbol: str = "") -> None:
    """Post a discussion signal so it shows up in the agent conversation panel."""
    try:
        _api_post(token, "/signals/discussion", {
            "market": market,
            "symbol": symbol,
            "title": title[:200],
            "content": content[:2000],
        })
    except Exception:
        pass


def post_activity(token: str, text: str, market: str = "crypto", symbol: str = "") -> None:
    """Compatibility wrapper that emits bounded stdout narration only."""
    narrative.emit("activity", "legacy_activity", facts={"text": text}, message=text,
                   symbol=symbol, throttle_key=f"activity:{text[:100]}:{symbol}")


def login(name: str = "BlitzRunner", password: Optional[str] = None) -> Optional[str]:
    """Login using an explicitly supplied or environment-backed password."""
    password = password or os.getenv("BLITZ_RUNNER_PASSWORD")
    if not password:
        logger.warning("BLITZ_RUNNER_PASSWORD not configured; using dev fallback")
        password = "blitzrunner"
    try:
        url = f"{API_BASE}/claw/agents/login"
        data = json.dumps({"name": name, "password": password, "client_type": "python_bot"}).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("token")
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return None


def register(name: str = "BlitzRunner", password: Optional[str] = None) -> Optional[str]:
    """Register using an explicitly supplied or environment-backed password."""
    password = password or os.getenv("BLITZ_RUNNER_PASSWORD")
    if not password:
        logger.warning("BLITZ_RUNNER_PASSWORD not configured; using dev fallback")
        password = "blitzrunner"
    initial_cash = float(os.getenv("BLITZ_RUNNER_INITIAL_CASH", "10000"))
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "blitzrunner@agent.dev",
            "password": password,
            "initial_balance": initial_cash,
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("token")
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        return None


def connect() -> Optional[str]:
    """Try login first, then register as BlitzRunner."""
    token = login()
    if token:
        logger.info("Logged in as BlitzRunner")
        return token
    logger.info("Login failed, attempting registration...")
    token = register()
    if token:
        logger.info("Registered as BlitzRunner")
    return token


# ============================================================
# Goal & Config Fetching
# ============================================================

def fetch_goal_status(token: str) -> dict:
    """Fetch goal status from API."""
    try:
        return _api_get(token, "/claw/agents/me/goal")
    except Exception as e:
        logger.warning(f"Could not fetch goal status: {e}")
        return {"can_trade": False, "goal_achieved": False, "max_loss_hit": False, "progress_pct": 0, "_unavailable": True}


def fetch_config(token: str) -> dict:
    """Fetch agent config (watchlist, poll_interval, max_positions)."""
    try:
        return _api_get(token, "/claw/agents/me/config")
    except Exception as e:
        logger.warning(f"Could not fetch config: {e}")
        return {"watchlist": [], "poll_interval": DEFAULT_POLL_INTERVAL, "_unavailable": True}


def fetch_strategy_params(token: str) -> dict:
    """Fetch strategy params from API."""
    try:
        data = _api_get(token, "/claw/agents/me/strategy-params")
        if isinstance(data, dict) and "strategy_params" in data:
            return data["strategy_params"]
        return data if isinstance(data, dict) else {"_unavailable": True}
    except Exception as e:
        logger.warning(f"Could not fetch strategy params: {e}")
        return {"_unavailable": True}


def fetch_portfolio(token: str) -> dict:
    """Fetch current portfolio (cash, positions, equity)."""
    try:
        data = _api_get(token, "/positions")
        return data if isinstance(data, dict) else {"_unavailable": True}
    except Exception as e:
        logger.warning(f"Could not fetch portfolio: {e}")
        return {"cash": 0.0, "positions": [], "portfolio_value": 0.0, "_unavailable": True}


def send_heartbeat(token: str) -> None:
    """Send heartbeat to platform."""
    try:
        _api_post(token, "/claw/agents/heartbeat", {"status": "running"})
    except Exception:
        pass


# ============================================================
# Goal-Aware Sizing (ported from scan_backtester.py)
# ============================================================

def goal_progress(equity: float, initial_capital: float, goal_target: float) -> float:
    """Return goal progress percentage (0-100+)."""
    if goal_target <= 0:
        return 0.0
    return ((equity - initial_capital) / goal_target) * 100


def sizing_pct(equity: float, initial_capital: float, goal_target: float,
               consecutive_losses: int, params: dict) -> tuple[float, bool]:
    """Return (position_size_pct, is_final_stretch) based on goal phase."""
    ps = params.get("position_sizing", {})
    progress = goal_progress(equity, initial_capital, goal_target)
    is_final_stretch = progress > 80.0

    if is_final_stretch:
        lo = ps.get("approaching_sizing_min_pct", 15)
        hi = ps.get("approaching_sizing_max_pct", 25)
    else:
        lo = ps.get("normal_sizing_min_pct", 25)
        hi = ps.get("normal_sizing_max_pct", 40)

    size_pct = (lo + hi) / 2.0

    # Consecutive loss circuit breaker
    threshold = ps.get("consecutive_loss_threshold", 3)
    if consecutive_losses >= threshold:
        cut = ps.get("consecutive_loss_size_cut_pct", 50)
        size_pct *= (1.0 - cut / 100.0)

    return size_pct, is_final_stretch


def min_signals_for_entry(consecutive_losses: int, params: dict) -> int:
    """Return minimum signal count required for entry (raised after consecutive losses)."""
    ps = params.get("position_sizing", {})
    threshold = ps.get("consecutive_loss_threshold", 3)
    if consecutive_losses >= threshold:
        return ps.get("consecutive_loss_min_signals", 5)
    return params.get("entry_criteria", {}).get("min_signals", 4)


# ============================================================
# Trade Execution
# ============================================================

def execute_close(token: str, symbol: str, side: str, quantity: float,
                  market: str, reason: str) -> bool:
    """Close a position via POST /signals/realtime."""
    action = "sell" if side == "long" else "cover"
    try:
        body = {
            "market": market,
            "action": action,
            "symbol": symbol,
            "price": 0,
            "quantity": quantity,
            "executed_at": "now",
            "content": f"[BlitzRunner] Auto-close: {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"CLOSED {symbol} ({side}) — {reason} — signal_id={result.get('signal_id')}")
        post_activity(token, f"CLOSED {symbol} ({side}) — {reason}", symbol=symbol)
        narrative.emit("exit", "exit", "complete", priority="trade", facts={
            "symbol": symbol, "side": side, "quantity": quantity, "reason": reason,
        })
        return True
    except urllib.error.HTTPError as e:
        logger.error(f"Close failed for {symbol}: {e.code} {e.reason}")
        narrative.emit("exit", "error", "failed", priority="error", facts={"symbol": symbol, "reason": reason})
        return False
    except Exception as e:
        logger.error(f"Close failed for {symbol}: {e}")
        narrative.emit("exit", "error", "failed", priority="error", facts={"symbol": symbol, "reason": reason})
        return False


def execute_entry(token: str, symbol: str, side: str, quantity: float,
                  market: str, stop_loss_price: float, take_profit_price: float,
                  trailing_sl_pct: float, trailing_activation_pct: float,
                  reason: str, stop_loss_pct: float = 0.0,
                  take_profit_pct: float = 0.0) -> bool:
    """Enter a new position via POST /signals/realtime with SL/TP/trailing."""
    action = "buy" if side == "long" else "short"
    try:
        body = {
            "market": market,
            "action": action,
            "symbol": symbol,
            "price": 0,
            "quantity": quantity,
            "executed_at": "now",
            "stop_loss_price": round(stop_loss_price, 6),
            "take_profit_price": round(take_profit_price, 6),
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_sl_pct": trailing_sl_pct,
            "trailing_activation_pct": trailing_activation_pct,
            "content": f"[BlitzRunner] {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"ENTERED {symbol} ({side}) — qty={quantity:.6f} — SL={stop_loss_price:.4f} TP={take_profit_price:.4f} — signal_id={result.get('signal_id')}")
        post_activity(token, f"ENTERED {symbol} ({side}) — qty={quantity:.4f} — SL={stop_loss_price:.4f} TP={take_profit_price:.4f}", symbol=symbol)
        narrative.emit("entry", "entry", "complete", priority="trade", facts={
            "symbol": symbol, "side": side, "quantity": quantity,
            "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price, "reason": reason,
        })
        return True
    except urllib.error.HTTPError as e:
        logger.error(f"Entry failed for {symbol}: {e.code} {e.reason}")
        narrative.emit("entry", "error", "failed", priority="error", facts={"symbol": symbol, "side": side, "reason": reason})
        return False
    except Exception as e:
        logger.error(f"Entry failed for {symbol}: {e}")
        narrative.emit("entry", "error", "failed", priority="error", facts={"symbol": symbol, "side": side, "reason": reason})
        return False


# ============================================================
# ATR-based SL/TP computation
# ============================================================

def compute_atr_sl_tp(entry_price: float, side: str, scan_data: dict,
                      params: dict) -> tuple[float, float, float, float]:
    """Compute ATR-based stop-loss, take-profit, and trailing stop params.

    Returns (stop_loss_price, take_profit_price, trailing_sl_pct, trailing_activation_pct).
    """
    exit_cfg = params.get("exit_rules", {})
    indicators = scan_data.get("indicators", {})
    atr = scan_data.get("atr", 0) or indicators.get("atr", {}).get("value", 0)

    # Fallback: use 2% of entry price as ATR proxy
    if atr <= 0:
        atr = entry_price * 0.02

    # SL = 1.5x ATR, TP = 3x ATR (2:1 reward/risk per INSTRUCTIONS.md)
    sl_distance = 1.5 * atr
    tp_distance = 3.0 * atr

    if side == "long":
        stop_loss_price = entry_price - sl_distance
        take_profit_price = entry_price + tp_distance
    else:
        stop_loss_price = entry_price + sl_distance
        take_profit_price = entry_price - tp_distance

    # Trailing stop from strategy params
    trail_sl_pct = exit_cfg.get("trailing_sl_pct", 1.0)
    trail_act_pct = exit_cfg.get("trailing_activation_pct", 1.0)

    return stop_loss_price, take_profit_price, trail_sl_pct, trail_act_pct


# ============================================================
# Market classification
# ============================================================

def _classify_market(symbol: str) -> str:
    """Classify symbol into market type for the API."""
    crypto_symbols = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK", "DOT", "LTC",
                      "UNI", "ATOM", "NEAR", "ARB", "OP"}
    if symbol.upper() in crypto_symbols:
        return "crypto"
    return "us-stock"


# ============================================================
# Main Cycle
# ============================================================

def run_cycle(token: str, state: dict, params: dict) -> dict:
    """Execute one deterministic trading cycle. Returns updated state."""

    # 1. Fetch goal status
    narrative.emit("cycle", "phase", "started", priority="action")
    goal = fetch_goal_status(token)
    if goal.get("_unavailable"):
        narrative.emit("goal", "error", "unavailable", priority="error")
        post_activity(token, "Cycle skipped: goal service unavailable")
        return state
    narrative.emit("goal", "phase", "loaded", priority="action", facts={
        "can_trade": goal.get("can_trade"), "goal_achieved": goal.get("goal_achieved", False),
        "max_loss_hit": goal.get("max_loss_hit", False),
    })
    can_trade = goal.get("can_trade", goal.get("status") == "no_goal")
    goal_achieved = goal.get("goal_achieved", False)
    max_loss_hit = goal.get("max_loss_hit", False)
    progress_pct = goal.get("progress_pct", 0)

    if max_loss_hit:
        logger.warning("Max loss hit — not trading. Waiting for user reset.")
        post_activity(token, "Max loss hit — not trading. Waiting for user reset.")
        return state

    # 2. Fetch portfolio for equity calculation
    portfolio = fetch_portfolio(token)
    if portfolio.get("_unavailable"):
        narrative.emit("portfolio", "error", "unavailable", priority="error")
        post_activity(token, "Cycle skipped: portfolio service unavailable")
        return state
    cash = float(portfolio.get("cash", 0.0))
    positions = portfolio.get("positions", [])

    # Compute current equity
    equity = cash
    gross_exposure = 0.0
    for p in positions:
        qty = abs(float(p.get("quantity", 0)))
        price = float(p.get("current_price", 0)) or float(p.get("entry_price", 0))
        gross_exposure += qty * price
        side = p.get("side", "long")
        if side == "long":
            equity += qty * price
        else:
            equity -= qty * price

    initial_capital = float(
        params.get("risk_controls", {}).get("paper_account_budget", 10000.0)
    )
    goal_target = goal.get("goal", {}).get("target_amount", 0) if isinstance(goal.get("goal"), dict) else 0

    # 3. Run scan.py run_scan() for indicators + setups + position reviews
    # Import here to avoid heavy yfinance import at module load
    sys.path.insert(0, _WORKSPACE_DIR)
    import scan as scan_module

    narrative.emit("scan", "scan", "started", priority="action", facts={"watchlist_size": len(params.get("watchlist", []))})
    post_activity(token, f"Cycle {state.get('cycles_run', 0) + 1}: scanning {len(params.get('watchlist', []))} symbols for setups...")
    scan_result = scan_module.run_scan(token=token)
    narrative.emit("scan", "scan", "complete", priority="action", facts={
        "ranked_setups": len(scan_result.get("ranked_setups", [])),
        "positions_reviewed": len(scan_result.get("positions", [])),
    })

    # 4. Process exits first (deterministic — no judgment)
    for pos_review in scan_result.get("positions", []):
        if pos_review.get("verdict") == "EXIT":
            symbol = pos_review["symbol"]
            side = pos_review.get("side", "long")
            exit_reason = pos_review.get("exit_reason", "exit_rule")
            qty = abs(float(pos_review.get("quantity", 0)))

            if qty <= 0:
                continue

            market = _classify_market(symbol)
            success = execute_close(token, symbol, side, qty, market, exit_reason)

            if success:
                # Update consecutive losses
                pnl_pct = pos_review.get("pnl_pct", 0)
                if pnl_pct > 0:
                    state["consecutive_losses"] = 0
                else:
                    state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                narrative.emit("exit", "exit", "reviewed", priority="trade", facts={
                    "symbol": symbol, "side": side, "reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 2),
                    "consecutive_losses": state["consecutive_losses"],
                })

                # Set reentry cooldown
                switch_cfg = params.get("switch_logic", {})
                cooldown_cycles = switch_cfg.get("reentry_cooldown_cycles", 3)
                state["reentry_cooldown"][symbol] = cooldown_cycles
                post_activity(token, f"EXIT {symbol} ({side}) — {exit_reason} — pnl_pct={pnl_pct:.1f}%", symbol=symbol)

    # 5. Decrement reentry cooldowns
    for sym in list(state["reentry_cooldown"].keys()):
        if state["reentry_cooldown"][sym] > 0:
            state["reentry_cooldown"][sym] -= 1
        if state["reentry_cooldown"][sym] <= 0:
            del state["reentry_cooldown"][sym]

    # 6. Check if we can enter new positions
    open_position_count = scan_result.get("open_position_count", len(positions))
    max_positions = params.get("position_sizing", {}).get("max_positions", 1)
    max_positions_reached = open_position_count >= max_positions

    if not can_trade or goal_achieved or max_positions_reached:
        logger.info(f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved} max_pos_reached={max_positions_reached} open={open_position_count}")
        post_activity(token, f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved} max_pos={max_positions_reached} open={open_position_count}")
        narrative.emit("cycle", "skip", "skipped", priority="action", facts={
            "can_trade": can_trade, "goal_achieved": goal_achieved,
            "max_positions_reached": max_positions_reached, "open_positions": open_position_count,
        })
        return state

    # 7. Rank setups with consecutive-loss filter
    ranked_setups = scan_result.get("ranked_setups", [])
    min_signals = min_signals_for_entry(state.get("consecutive_losses", 0), params)
    post_activity(token, f"Scan complete: {len(ranked_setups)} ranked setups, {open_position_count} open positions, equity=${equity:.0f}")

    # Filter by raised signal bar after consecutive losses
    filtered_setups = []
    for setup in ranked_setups:
        sym = setup["symbol"]
        sym_data = scan_result.get("symbols", {}).get(sym, {})
        sig_count = max(sym_data.get("signal_count", {}).get("bullish", 0), sym_data.get("signal_count", {}).get("bearish", 0))
        if state.get("consecutive_losses", 0) >= params.get("position_sizing", {}).get("consecutive_loss_threshold", 3):
            if sig_count < min_signals:
                continue
        filtered_setups.append(setup)

    # 8. Switch logic (single-position model)
    switch_cfg = params.get("switch_logic", {})
    switch_threshold_pct = switch_cfg.get("switch_score_threshold_pct", 20)
    switch_require_profitable = switch_cfg.get("switch_require_profitable", True)

    if max_positions == 1 and open_position_count == 1 and switch_threshold_pct > 0 and filtered_setups:
        current_pos = positions[0] if positions else None
        if current_pos:
            pos_sym = current_pos.get("symbol", "")
            pos_side = current_pos.get("side", "long")
            entry_price = float(current_pos.get("entry_price", 0))
            current_price = float(current_pos.get("current_price", 0)) or entry_price
            entry_score = float(current_pos.get("entry_score", 0))

            best = filtered_setups[0]
            if entry_score > 0 and best["symbol"] != pos_sym:
                improvement = ((best["score"] - entry_score) / entry_score) * 100

                can_switch = True
                if switch_require_profitable:
                    if pos_side == "long":
                        can_switch = current_price > entry_price
                    else:
                        can_switch = current_price < entry_price

                if improvement > switch_threshold_pct and can_switch:
                    qty = abs(float(current_pos.get("quantity", 0)))
                    market = _classify_market(pos_sym)
                    logger.info(f"SWITCH: {pos_sym} → {best['symbol']} (improvement={improvement:.1f}%)")
                    post_activity(token, f"SWITCH: {pos_sym} → {best['symbol']} (improvement={improvement:.1f}%)", symbol=best['symbol'])
                    narrative.emit("switch", "switch", "ready", priority="trade", facts={
                        "from_symbol": pos_sym, "to_symbol": best["symbol"],
                        "improvement_pct": round(improvement, 1),
                    })
                    success = execute_close(token, pos_sym, pos_side, qty, market, f"switch_to_{best['symbol']}")

                    if success:
                        # Update consecutive losses for the switch close
                        if pos_side == "long":
                            pnl = (current_price - entry_price) * qty
                        else:
                            pnl = (entry_price - current_price) * qty
                        if pnl > 0:
                            state["consecutive_losses"] = 0
                        else:
                            state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1

                        cooldown_cycles = switch_cfg.get("reentry_cooldown_cycles", 3)
                        state["reentry_cooldown"][pos_sym] = cooldown_cycles

                        # Enter the new setup
                        best_sym = best["symbol"]
                        if best_sym not in state["reentry_cooldown"]:
                            _enter_from_setup(token, best, scan_result, state, params, equity, initial_capital, goal_target, gross_exposure)
                    return state

    # 9. Entry logic — fill available slots
    available_slots = max_positions - open_position_count
    if available_slots > 0:
        for setup in filtered_setups:
            if available_slots <= 0:
                break
            sym = setup["symbol"]
            if sym in state["reentry_cooldown"]:
                continue
            if any(p.get("symbol", "").upper() == sym.upper() for p in positions):
                continue

            entered = _enter_from_setup(token, setup, scan_result, state, params, equity, initial_capital, goal_target, gross_exposure)
            if entered:
                available_slots -= 1

    if available_slots == max_positions - open_position_count:
        post_activity(token, f"No entries this cycle — {len(filtered_setups)} setups found but none eligible (cooldowns/already held)")

    return state


def _enter_from_setup(token: str, setup: dict, scan_result: dict, state: dict,
                      params: dict, equity: float, initial_capital: float,
                      goal_target: float, gross_exposure: float) -> bool:
    """Enter a position from a ranked setup. Returns True if entry succeeded."""
    symbol = setup["symbol"]
    side = setup.get("direction", "long")
    market = _classify_market(symbol)

    # Get scan data for this symbol (for ATR)
    sym_data = scan_result.get("symbols", {}).get(symbol, {})
    if not sym_data or sym_data.get("error"):
        logger.warning(f"Scan data missing for {symbol}, skipping entry")
        return False

    # Get current price from scan data
    entry_price = sym_data.get("price", 0)
    if entry_price <= 0:
        logger.warning(f"No price for {symbol}, skipping entry")
        return False

    # Compute ATR-based SL/TP and risk-based quantity.
    sl_price, tp_price, trail_sl_pct, trail_act_pct = compute_atr_sl_tp(
        entry_price, side, sym_data, params
    )
    stop_distance_pct = abs((sl_price - entry_price) / entry_price) * 100
    notional = position_notional(equity, stop_distance_pct, gross_exposure, params)
    qty = notional / entry_price if notional > 0 else 0
    if qty <= 0:
        return False

    # Build reason string
    sig_count = max(sym_data.get("signal_count", {}).get("bullish", 0), sym_data.get("signal_count", {}).get("bearish", 0))
    score = setup.get("score", 0)
    risk_pct = params.get("risk_controls", {}).get("risk_per_trade_pct", 0.5)
    reason = f"Auto-entry: {side} {symbol} | score={score:.1f} signals={sig_count} risk={risk_pct:.2f}% notional=${notional:.2f}"

    success = execute_entry(
        token, symbol, side, qty, market,
        sl_price, tp_price, trail_sl_pct, trail_act_pct,
        reason,
        abs((sl_price - entry_price) / entry_price) * 100,
        abs((tp_price - entry_price) / entry_price) * 100,
    )

    if success:
        narrative.emit("entry", "setup", "ready", priority="trade", facts={
            "symbol": symbol, "side": side, "score": round(score, 1),
            "signals": sig_count, "notional": round(notional, 2),
        })
    else:
        narrative.emit("entry", "reject", "skipped", priority="action", facts={
            "symbol": symbol, "side": side, "score": score, "reason": "broker_rejected",
        })
    return success


# ============================================================
# Main Loop
# ============================================================

def run_loop(stop_event: threading.Event, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
    """Main loop — runs cycles until stop_event is set."""

    token = connect()
    if not token:
        logger.error("Could not connect to platform. Exiting.")
        narrative.emit("startup", "error", "failed", priority="error", message="Velocity systems offline; no trades, no theatrics.")
        return

    narrative.emit("startup", "startup", "ready", priority="action")
    state = load_state()
    logger.info(f"State loaded: consecutive_losses={state.get('consecutive_losses', 0)} cycles_run={state.get('cycles_run', 0)}")

    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()
        narrative.begin_cycle(cycle)

        try:
            # Fetch live config for poll interval
            config = fetch_config(token)
            if config.get("_unavailable"):
                raise RuntimeError("config service unavailable")
            live_poll = config.get("poll_interval", poll_interval)

            # Fetch strategy params through the agent-specific profile resolver.
            stored_params = fetch_strategy_params(token)
            if stored_params.get("_unavailable"):
                raise RuntimeError("strategy parameter service unavailable")
            params = effective_params("BlitzRunner", "momentum_scalp", stored_params)
            logger.info("Effective profile=%s interval=%s budget=$%.2f risk=%.2f%%", params["profile"], params["indicators"].get("candle_interval"), params["risk_controls"]["paper_account_budget"], params["risk_controls"]["risk_per_trade_pct"])

            # Override watchlist from config if present
            if config.get("watchlist"):
                params["watchlist"] = config["watchlist"]
            if config.get("max_positions") is not None:
                params["position_sizing"]["max_positions"] = int(config["max_positions"])
                params["risk_controls"]["max_positions"] = int(config["max_positions"])

            # Run the cycle
            state = run_cycle(token, state, params)

            # Send heartbeat
            send_heartbeat(token)

            # Update state
            state["cycles_run"] = state.get("cycles_run", 0) + 1
            state["last_cycle_time"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            cycle_time = time.time() - cycle_start
            logger.info(f"Cycle {cycle} done in {cycle_time:.1f}s — losses={state.get('consecutive_losses', 0)} cooldowns={state.get('reentry_cooldown', {})}")
            narrative.recap({"duration_seconds": round(cycle_time, 2), "consecutive_losses": state.get("consecutive_losses", 0)})

        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)
            narrative.emit("cycle", "error", "failed", priority="error", facts={"error": str(e)[:300]})

        # Sleep in small increments so we can respond to stop signal
        sleep_secs = live_poll if 'live_poll' in dir() else poll_interval
        for _ in range(sleep_secs):
            if stop_event.is_set():
                break
            time.sleep(1)

    logger.info(f"BlitzRunner stopped after {cycle} cycles.")
    narrative.emit("shutdown", "shutdown", "complete", priority="action", facts={"cycles_run": cycle})


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="BlitzRunner — Deterministic Goal Runner Agent")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL, help="Poll interval in seconds")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles (0 = infinite)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  BlitzRunner — Deterministic Goal Runner")
    print(f"{'='*60}")
    print(f"  API: {API_BASE}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  State file: {STATE_FILE}")
    print(f"{'='*60}\n")

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print(f"\nStopping BlitzRunner...")
        stop_event.set()

    import signal as _signal
    _signal.signal(_signal.SIGINT, signal_handler)
    _signal.signal(_signal.SIGTERM, signal_handler)

    run_loop(stop_event, args.interval)


if __name__ == "__main__":
    main()
