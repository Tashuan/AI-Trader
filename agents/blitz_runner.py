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


# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("BlitzRunner")
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
    """Persist state to JSON file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
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


def login(name: str = "BlitzRunner", password: str = "blitzrunner_pass_2026") -> Optional[str]:
    """Login to the platform and return auth token."""
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


def register(name: str = "BlitzRunner", password: str = "blitzrunner_pass_2026") -> Optional[str]:
    """Register on the platform and return auth token."""
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "blitzrunner@agent.dev",
            "password": password,
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
        return {"can_trade": True, "goal_achieved": False, "max_loss_hit": False, "progress_pct": 0}


def fetch_config(token: str) -> dict:
    """Fetch agent config (watchlist, poll_interval, max_positions)."""
    try:
        return _api_get(token, "/claw/agents/me/config")
    except Exception as e:
        logger.warning(f"Could not fetch config: {e}")
        return {"watchlist": ["BTC", "ETH", "SOL"], "max_positions": 1, "poll_interval": DEFAULT_POLL_INTERVAL}


def fetch_strategy_params(token: str) -> dict:
    """Fetch strategy params from API."""
    try:
        data = _api_get(token, "/claw/agents/me/strategy-params")
        if isinstance(data, dict) and "strategy_params" in data:
            return data["strategy_params"]
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Could not fetch strategy params: {e}")
        return {}


def fetch_portfolio(token: str) -> dict:
    """Fetch current portfolio (cash, positions, equity)."""
    try:
        return _api_get(token, "/positions")
    except Exception as e:
        logger.warning(f"Could not fetch portfolio: {e}")
        return {"cash": 100000.0, "positions": [], "portfolio_value": 100000.0}


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
        return True
    except urllib.error.HTTPError as e:
        logger.error(f"Close failed for {symbol}: {e.code} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Close failed for {symbol}: {e}")
        return False


def execute_entry(token: str, symbol: str, side: str, quantity: float,
                  market: str, stop_loss_price: float, take_profit_price: float,
                  trailing_sl_pct: float, trailing_activation_pct: float,
                  reason: str) -> bool:
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
            "trailing_sl_pct": trailing_sl_pct,
            "trailing_activation_pct": trailing_activation_pct,
            "content": f"[BlitzRunner] {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"ENTERED {symbol} ({side}) — qty={quantity:.6f} — SL={stop_loss_price:.4f} TP={take_profit_price:.4f} — signal_id={result.get('signal_id')}")
        return True
    except urllib.error.HTTPError as e:
        logger.error(f"Entry failed for {symbol}: {e.code} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Entry failed for {symbol}: {e}")
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
    atr = indicators.get("atr_14", 0)

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
    goal = fetch_goal_status(token)
    can_trade = goal.get("can_trade", True)
    goal_achieved = goal.get("goal_achieved", False)
    max_loss_hit = goal.get("max_loss_hit", False)
    progress_pct = goal.get("progress_pct", 0)

    if max_loss_hit:
        logger.warning("Max loss hit — not trading. Waiting for user reset.")
        return state

    # 2. Fetch portfolio for equity calculation
    portfolio = fetch_portfolio(token)
    cash = float(portfolio.get("cash", 100000.0))
    positions = portfolio.get("positions", [])

    # Compute current equity
    equity = cash
    for p in positions:
        qty = abs(float(p.get("quantity", 0)))
        price = float(p.get("current_price", 0)) or float(p.get("entry_price", 0))
        side = p.get("side", "long")
        if side == "long":
            equity += qty * price
        else:
            equity -= qty * price

    initial_capital = 100000.0
    goal_target = 1000.0  # default; could fetch from goal config

    # 3. Run scan.py run_scan() for indicators + setups + position reviews
    # Import here to avoid heavy yfinance import at module load
    sys.path.insert(0, _WORKSPACE_DIR)
    import scan as scan_module

    scan_result = scan_module.run_scan(token=token)

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

                # Set reentry cooldown
                switch_cfg = params.get("switch_logic", {})
                cooldown_cycles = switch_cfg.get("reentry_cooldown_cycles", 3)
                state["reentry_cooldown"][symbol] = cooldown_cycles

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
        return state

    # 7. Rank setups with consecutive-loss filter
    ranked_setups = scan_result.get("ranked_setups", [])
    min_signals = min_signals_for_entry(state.get("consecutive_losses", 0), params)

    # Filter by raised signal bar after consecutive losses
    filtered_setups = []
    for setup in ranked_setups:
        sym = setup["symbol"]
        sym_data = scan_result.get("symbols", {}).get(sym, {})
        sig_count = sym_data.get("signal_count", {}).get("bullish", 0)
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
                            _enter_from_setup(token, best, scan_result, state, params, equity, initial_capital, goal_target)
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

            entered = _enter_from_setup(token, setup, scan_result, state, params, equity, initial_capital, goal_target)
            if entered:
                available_slots -= 1

    return state


def _enter_from_setup(token: str, setup: dict, scan_result: dict, state: dict,
                      params: dict, equity: float, initial_capital: float,
                      goal_target: float) -> bool:
    """Enter a position from a ranked setup. Returns True if entry succeeded."""
    symbol = setup["symbol"]
    side = setup.get("direction", "long")
    market = _classify_market(symbol)

    # Get scan data for this symbol (for ATR)
    sym_data = scan_result.get("symbols", {}).get(symbol, {})
    if not sym_data or sym_data.get("error"):
        logger.warning(f"Scan data missing for {symbol}, skipping entry")
        return False

    # Goal-aware sizing
    size_pct, is_final_stretch = sizing_pct(
        equity, initial_capital, goal_target,
        state.get("consecutive_losses", 0), params
    )

    ps_cfg = params.get("position_sizing", {})
    notional = equity * (size_pct / 100.0)

    # Apply dollar cap if configured
    max_dollar = ps_cfg.get("max_position_dollar_cap")
    if max_dollar is not None and notional > max_dollar:
        notional = max_dollar

    # Get current price from scan data
    entry_price = sym_data.get("price", 0)
    if entry_price <= 0:
        logger.warning(f"No price for {symbol}, skipping entry")
        return False

    # Compute quantity
    qty = notional / entry_price
    if qty <= 0:
        return False

    # Compute ATR-based SL/TP and trailing params
    sl_price, tp_price, trail_sl_pct, trail_act_pct = compute_atr_sl_tp(
        entry_price, side, sym_data, params
    )

    # Build reason string
    sig_count = sym_data.get("signal_count", {}).get("bullish", 0)
    score = setup.get("score", 0)
    reason = f"Auto-entry: {side} {symbol} | score={score:.1f} signals={sig_count} size={size_pct:.1f}%"

    success = execute_entry(
        token, symbol, side, qty, market,
        sl_price, tp_price, trail_sl_pct, trail_act_pct,
        reason
    )

    return success


# ============================================================
# Main Loop
# ============================================================

def run_loop(stop_event: threading.Event, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
    """Main loop — runs cycles until stop_event is set."""

    token = connect()
    if not token:
        logger.error("Could not connect to platform. Exiting.")
        return

    state = load_state()
    logger.info(f"State loaded: consecutive_losses={state.get('consecutive_losses', 0)} cycles_run={state.get('cycles_run', 0)}")

    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()

        try:
            # Fetch live config for poll interval
            config = fetch_config(token)
            live_poll = config.get("poll_interval", poll_interval)

            # Fetch strategy params
            params = fetch_strategy_params(token)
            # Merge with defaults
            params = scan_core.deep_merge(dict(scan_core.DEFAULT_PARAMS), params)

            # Override watchlist from config if present
            if config.get("watchlist"):
                params["watchlist"] = config["watchlist"]

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

        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)

        # Sleep in small increments so we can respond to stop signal
        sleep_secs = live_poll if 'live_poll' in dir() else poll_interval
        for _ in range(sleep_secs):
            if stop_event.is_set():
                break
            time.sleep(1)

    logger.info(f"BlitzRunner stopped after {cycle} cycles.")


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
