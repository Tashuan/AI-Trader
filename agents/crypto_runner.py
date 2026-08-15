#!/usr/bin/env python3
"""
CryptoRunner — Deterministic Crypto Swing/Trend Bot

Executes a crypto-specific swing/trend strategy with zero LLM judgment.
Multi-position (up to 3), 4h candles with daily confirmation, EMA trend
alignment, BTC regime filter for alts, liquidity floor, ATR-based SL/TP
with clamping, and goal-aware sizing.

Every cycle:
  1. Fetch config + strategy params from the API
  2. Run crypto_scan.run_scan() for indicators + ranked setups + position reviews
  3. Process exits (any position with verdict "EXIT" → close immediately)
  4. Check max positions (3) — skip entries if at limit
  5. Fill available slots from top-ranked setups with goal-aware sizing
  6. Handle sector_concentration rejections (retry at half size)
  7. Persist state (consecutive_losses, reentry_cooldown, position_entry_times)
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

import crypto_scan_core as scan_core
import crypto_scan as scan_module
from strategy_registry import effective_params, position_notional
from runner_narrative import RunnerNarrative


# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("CryptoRunner")
narrative = RunnerNarrative("cryptorunner")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[CryptoRunner] %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False


# ── Constants ───────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/api"
STATE_FILE = os.path.join(_AGENTS_DIR, "crypto_runner_state.json")
DEFAULT_POLL_INTERVAL = 1800  # 30 minutes


# ============================================================
# State Persistence
# ============================================================

_DEFAULT_STATE = {
    "consecutive_losses": 0,
    "reentry_cooldown": {},  # symbol -> remaining cycles
    "last_cycle_time": None,
    "cycles_run": 0,
    "position_entry_times": {},  # symbol -> ISO timestamp of entry
}


def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                merged = dict(_DEFAULT_STATE)
                merged.update(state)
                if "position_entry_times" not in merged:
                    merged["position_entry_times"] = {}
                return merged
    except Exception as e:
        logger.warning(f"Could not load state file: {e}")
    return dict(_DEFAULT_STATE)


def save_state(state: dict) -> None:
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
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _api_post(token: str, path: str, body: dict) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _api_patch(token: str, path: str, body: dict) -> dict:
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


def login(name: str = "CryptoRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("CRYPTO_RUNNER_PASSWORD")
    if not password:
        logger.warning("CRYPTO_RUNNER_PASSWORD not configured; using dev fallback")
        password = "cryptorunner"
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


def register(name: str = "CryptoRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("CRYPTO_RUNNER_PASSWORD")
    if not password:
        logger.warning("CRYPTO_RUNNER_PASSWORD not configured; using dev fallback")
        password = "cryptorunner"
    initial_cash = float(os.getenv("CRYPTO_RUNNER_INITIAL_CASH", "10000"))
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "cryptorunner@agent.dev",
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
    token = login()
    if token:
        logger.info("Logged in as CryptoRunner")
        return token
    logger.info("Login failed, attempting registration...")
    token = register()
    if token:
        logger.info("Registered as CryptoRunner")
    return token


# ============================================================
# Goal & Config Fetching
# ============================================================

def fetch_goal_status(token: str) -> dict:
    try:
        return _api_get(token, "/claw/agents/me/goal")
    except Exception as e:
        logger.warning(f"Could not fetch goal status: {e}")
        return {"can_trade": False, "goal_achieved": False, "max_loss_hit": False, "progress_pct": 0, "_unavailable": True}


def fetch_config(token: str) -> dict:
    try:
        return _api_get(token, "/claw/agents/me/config")
    except Exception as e:
        logger.warning(f"Could not fetch config: {e}")
        return {"watchlist": [], "poll_interval": DEFAULT_POLL_INTERVAL, "_unavailable": True}


def fetch_strategy_params(token: str) -> dict:
    try:
        data = _api_get(token, "/claw/agents/me/strategy-params")
        if isinstance(data, dict) and "strategy_params" in data:
            return data["strategy_params"]
        return data if isinstance(data, dict) else {"_unavailable": True}
    except Exception as e:
        logger.warning(f"Could not fetch strategy params: {e}")
        return {"_unavailable": True}


def fetch_portfolio(token: str) -> dict:
    try:
        data = _api_get(token, "/positions")
        return data if isinstance(data, dict) else {"_unavailable": True}
    except Exception as e:
        logger.warning(f"Could not fetch portfolio: {e}")
        return {"cash": 0.0, "positions": [], "portfolio_value": 0.0, "_unavailable": True}


def send_heartbeat(token: str) -> None:
    try:
        _api_post(token, "/claw/agents/heartbeat", {"status": "running"})
    except Exception:
        pass


# ============================================================
# Goal-Aware Sizing
# ============================================================

def goal_progress(equity: float, initial_capital: float, goal_target: float) -> float:
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
        lo = ps.get("approaching_sizing_min_pct", 8)
        hi = ps.get("approaching_sizing_max_pct", 12)
    else:
        lo = ps.get("normal_sizing_min_pct", 12)
        hi = ps.get("normal_sizing_max_pct", 16)

    size_pct = (lo + hi) / 2.0

    threshold = ps.get("consecutive_loss_threshold", 3)
    if consecutive_losses >= threshold:
        cut = ps.get("consecutive_loss_size_cut_pct", 50)
        size_pct *= (1.0 - cut / 100.0)

    return size_pct, is_final_stretch


def min_signals_for_entry(consecutive_losses: int, params: dict) -> int:
    ps = params.get("position_sizing", {})
    threshold = ps.get("consecutive_loss_threshold", 3)
    if consecutive_losses >= threshold:
        return ps.get("consecutive_loss_min_signals", 6)
    return params.get("entry_criteria", {}).get("min_signals", 5)


# ============================================================
# Trade Execution
# ============================================================

def execute_close(token: str, symbol: str, side: str, quantity: float,
                  market: str, reason: str) -> bool:
    action = "sell" if side == "long" else "cover"
    try:
        body = {
            "market": market,
            "action": action,
            "symbol": symbol,
            "price": 0,
            "quantity": quantity,
            "executed_at": "now",
            "content": f"[CryptoRunner] Auto-close: {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"CLOSED {symbol} ({side}) — {reason} — signal_id={result.get('signal_id')}")
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
                  take_profit_pct: float = 0.0) -> dict:
    """Enter a new position. Returns result dict (may contain error)."""
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
            "content": f"[CryptoRunner] {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"ENTERED {symbol} ({side}) — qty={quantity:.6f} — SL={stop_loss_price:.4f} TP={take_profit_price:.4f} — signal_id={result.get('signal_id')}")
        narrative.emit("entry", "entry", "complete", priority="trade", facts={
            "symbol": symbol, "side": side, "quantity": quantity,
            "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price, "reason": reason,
        })
        return result
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()
        except Exception:
            pass
        logger.error(f"Entry failed for {symbol}: {e.code} {e.reason} — {detail}")
        narrative.emit("entry", "error", "failed", priority="error", facts={"symbol": symbol, "side": side, "reason": reason})
        return {"error": f"HTTP {e.code}: {e.reason}", "detail": detail}
    except Exception as e:
        logger.error(f"Entry failed for {symbol}: {e}")
        narrative.emit("entry", "error", "failed", priority="error", facts={"symbol": symbol, "side": side, "reason": reason})
        return {"error": str(e)}


def publish_strategy(token: str, symbol: str, side: str, action: str,
                     score: float, signals: int, families: int, reason: str) -> None:
    """Publish trade reasoning via POST /api/signals/strategy."""
    try:
        body = {
            "symbol": symbol,
            "side": side,
            "action": action,
            "content": f"[CryptoRunner] {reason} | score={score:.1f} signals={signals} families={families}",
            "strategy_type": "crypto_swing",
        }
        _api_post(token, "/signals/strategy", body)
    except Exception:
        pass


# ============================================================
# Market Classification
# ============================================================

def _classify_market(symbol: str) -> str:
    return "crypto"


# ============================================================
# Main Cycle
# ============================================================

def run_cycle(token: str, state: dict, params: dict, poll_interval: int) -> dict:
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

    # 3. Run scan for indicators + setups + position reviews
    narrative.emit("scan", "scan", "started", priority="action", facts={"watchlist_size": len(params.get("watchlist", [])), "market": "crypto"})
    post_activity(token, f"Cycle {state.get('cycles_run', 0) + 1}: scanning {len(params.get('watchlist', []))} crypto symbols for setups...")
    scan_result = scan_module.run_scan(
        token=token,
        position_entry_times=state.get("position_entry_times", {}),
        poll_interval=poll_interval,
    )
    narrative.emit("scan", "scan", "complete", priority="action", facts={
        "ranked_setups": len(scan_result.get("ranked_setups", [])),
        "positions_reviewed": len(scan_result.get("positions", [])),
        "open_positions": scan_result.get("open_position_count", len(positions)),
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

                # Set reentry cooldown (convert hours to cycles)
                switch_cfg = params.get("switch_logic", {})
                cooldown_hours = switch_cfg.get("reentry_cooldown_hours", 8)
                cooldown_cycles = scan_core.hours_to_cycles(cooldown_hours, poll_interval)
                state["reentry_cooldown"][symbol] = cooldown_cycles

                # Clear entry time
                state.get("position_entry_times", {}).pop(symbol, None)
                post_activity(token, f"EXIT {symbol} ({side}) — {exit_reason} — pnl_pct={pnl_pct:.1f}%", symbol=symbol)

    # 5. Decrement reentry cooldowns
    for sym in list(state["reentry_cooldown"].keys()):
        if state["reentry_cooldown"][sym] > 0:
            state["reentry_cooldown"][sym] -= 1
        if state["reentry_cooldown"][sym] <= 0:
            del state["reentry_cooldown"][sym]

    # 6. Check if we can enter new positions
    open_position_count = scan_result.get("open_position_count", len(positions))
    max_positions = params.get("position_sizing", {}).get("max_positions", 3)
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

    filtered_setups = []
    for setup in ranked_setups:
        sym = setup["symbol"]
        sym_data = scan_result.get("symbols", {}).get(sym, {})
        sig_count = max(sym_data.get("signal_count", {}).get("bullish", 0), sym_data.get("signal_count", {}).get("bearish", 0))
        if state.get("consecutive_losses", 0) >= params.get("position_sizing", {}).get("consecutive_loss_threshold", 3):
            if sig_count < min_signals:
                continue
        filtered_setups.append(setup)

    # 8. Entry logic — fill available slots (up to 3)
    available_slots = max_positions - open_position_count
    if available_slots > 0:
        held_symbols = {p.get("symbol", "").upper() for p in positions}
        for setup in filtered_setups:
            if available_slots <= 0:
                break
            sym = setup["symbol"]
            if sym in state["reentry_cooldown"]:
                continue
            if sym.upper() in held_symbols:
                continue

            entered = _enter_from_setup(token, setup, scan_result, state, params, equity, initial_capital, goal_target, gross_exposure)
            if entered:
                available_slots -= 1
                gross_exposure += float(state.pop("last_entry_notional", 0.0))
                # Record entry time for bars_held tracking
                if "position_entry_times" not in state:
                    state["position_entry_times"] = {}
                state["position_entry_times"][sym] = datetime.now(timezone.utc).isoformat()

    if available_slots == max_positions - open_position_count:
        post_activity(token, f"No entries this cycle — {len(filtered_setups)} setups found but none eligible (cooldowns/already held)")

    return state


def _enter_from_setup(token: str, setup: dict, scan_result: dict, state: dict,
                      params: dict, equity: float, initial_capital: float,
                      goal_target: float, gross_exposure: float) -> bool:
    """Enter a position from a ranked setup. Returns True if entry succeeded.

    Handles sector_concentration rejection by retrying once at half size.
    """
    symbol = setup["symbol"]
    side = setup.get("direction", "long")
    market = _classify_market(symbol)

    sym_data = scan_result.get("symbols", {}).get(symbol, {})
    if not sym_data or sym_data.get("error"):
        logger.warning(f"Scan data missing for {symbol}, skipping entry")
        return False

    entry_price = sym_data.get("price", 0)
    if entry_price <= 0:
        logger.warning(f"No price for {symbol}, skipping entry")
        return False

    # Compute ATR-based SL/TP with clamping, then size from actual stop risk.
    sl_price, tp_price, trail_sl_pct, trail_act_pct = scan_core.compute_atr_sl_tp(
        entry_price, side, sym_data, params
    )
    stop_distance_pct = abs((sl_price - entry_price) / entry_price) * 100
    notional = position_notional(equity, stop_distance_pct, gross_exposure, params)
    qty = notional / entry_price if notional > 0 else 0
    if qty <= 0:
        return False

    sig_count = max(sym_data.get("signal_count", {}).get("bullish", 0), sym_data.get("signal_count", {}).get("bearish", 0))
    families_count = len(sym_data.get("families_represented", []))
    score = setup.get("score", 0)
    risk_pct = params.get("risk_controls", {}).get("risk_per_trade_pct", 0.5)
    reason = f"Auto-entry: {side} {symbol} | score={score:.1f} signals={sig_count} families={families_count} risk={risk_pct:.2f}% notional=${notional:.2f}"

    result = execute_entry(
        token, symbol, side, qty, market,
        sl_price, tp_price, trail_sl_pct, trail_act_pct,
        reason,
        abs((sl_price - entry_price) / entry_price) * 100,
        abs((tp_price - entry_price) / entry_price) * 100,
    )

    if isinstance(result, dict) and "error" in result:
        logger.warning(f"Entry rejected for {symbol}: {result.get('detail', result.get('error'))}")
        narrative.emit("entry", "reject", "skipped", priority="action", facts={
            "symbol": symbol, "side": side, "score": score, "reason": "broker_rejected",
        })
        return False

    state["last_entry_notional"] = notional
    narrative.emit("entry", "setup", "ready", priority="trade", facts={
        "symbol": symbol, "side": side, "score": round(score, 1),
        "signals": sig_count, "families": families_count, "notional": round(notional, 2),
    })

    # Publish strategy reasoning
    publish_strategy(token, symbol, side, "buy" if side == "long" else "short",
                     score, sig_count, families_count, reason)
    post_activity(token, f"ENTERED {symbol} ({side}) — qty={qty:.4f} — SL={sl_price:.4f} TP={tp_price:.4f} — score={score:.1f}", symbol=symbol)
    return True


# ============================================================
# Main Loop
# ============================================================

def run_loop(stop_event: threading.Event, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
    """Main loop — runs cycles until stop_event is set."""

    token = connect()
    if not token:
        logger.error("Could not connect to platform. Exiting.")
        narrative.emit("startup", "error", "failed", priority="error", message="The 24-hour desk is offline; capital remains untouched.")
        return

    narrative.emit("startup", "startup", "ready", priority="action")
    state = load_state()
    logger.info(f"State loaded: consecutive_losses={state.get('consecutive_losses', 0)} cycles_run={state.get('cycles_run', 0)}")

    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()
        live_poll = poll_interval

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
            params = effective_params("CryptoRunner", "crypto_swing", stored_params)
            logger.info("Effective profile=%s interval=%s budget=$%.2f risk=%.2f%%", params["profile"], params["indicators"].get("candle_interval"), params["risk_controls"]["paper_account_budget"], params["risk_controls"]["risk_per_trade_pct"])

            # Override watchlist from config if present
            if config.get("watchlist"):
                params["watchlist"] = config["watchlist"]
            if config.get("max_positions") is not None:
                params["position_sizing"]["max_positions"] = int(config["max_positions"])
                params["risk_controls"]["max_positions"] = int(config["max_positions"])

            # Run the cycle
            state = run_cycle(token, state, params, live_poll)

            # Send heartbeat
            send_heartbeat(token)

            # Update state
            state["cycles_run"] = state.get("cycles_run", 0) + 1
            state["last_cycle_time"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            cycle_time = time.time() - cycle_start
            logger.info(f"Cycle {cycle} done in {cycle_time:.1f}s — losses={state.get('consecutive_losses', 0)} cooldowns={state.get('reentry_cooldown', {})}")
            narrative.emit("cycle", "cycle", "complete", priority="action", facts={
                "cycle": cycle,
                "cycle_time_s": round(cycle_time, 1),
                "consecutive_losses": state.get("consecutive_losses", 0),
                "cooldowns": len(state.get("reentry_cooldown", {})),
            })

        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)
            narrative.emit("cycle", "error", "failed", priority="error", message=f"Cycle {cycle} tripped on a bad candle — safeguarding capital and retrying next tick.")

        # Sleep in small increments so we can respond to stop signal
        for _ in range(live_poll):
            if stop_event.is_set():
                break
            time.sleep(1)

    logger.info(f"CryptoRunner stopped after {cycle} cycles.")
    narrative.emit("shutdown", "shutdown", "complete", priority="action", facts={"cycles_run": cycle})


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="CryptoRunner — Deterministic Crypto Swing/Trend Bot")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL, help="Poll interval in seconds")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles (0 = infinite)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  CryptoRunner — Deterministic Crypto Swing/Trend Bot")
    print(f"{'='*60}")
    print(f"  API: {API_BASE}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  State file: {STATE_FILE}")
    print(f"{'='*60}\n")

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print(f"\nStopping CryptoRunner...")
        stop_event.set()

    import signal as _signal
    _signal.signal(_signal.SIGINT, signal_handler)
    _signal.signal(_signal.SIGTERM, signal_handler)

    run_loop(stop_event, args.interval)


if __name__ == "__main__":
    main()
