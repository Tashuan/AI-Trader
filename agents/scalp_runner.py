#!/usr/bin/env python3
"""
ScalpRunner — Deterministic 4-Step Scalp Agent

Executes the human trader's 4-step scalp process with zero LLM judgment:
  1. Discover: Schwab movers + news + volume scanner → shortlist
  2. Filter: liquidity scoring (spread, depth, dollar volume)
  3. Analyze: multi-TF (1m/5m/15m) + Fib + S/R + breakout → ranked setups
  4. Pre-position: stop-limit pending orders at breakout/entry levels

Every cycle (default 15s):
  - Fetch config + strategy params + goal status + portfolio
  - Run scalp_scan run_scan() for the full 4-step pipeline
  - Cancel stale/expired pending orders
  - If active exit mode: review open positions and exit if rules trigger
  - If slots available and qualifying setups found: create pending stop-limit orders
  - Persist state (consecutive_losses, reentry_cooldown, pending_order_ids)

The pending_order_filler_loop in tasks.py handles the actual fill when price
crosses the stop level — this agent only places and cancels orders.
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
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

# ── Path setup ──────────────────────────────────────────────────────────
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

# workspace dir for scalp scan.py
_WORKSPACE_DIR = os.path.join(_AGENTS_DIR, "workspaces", "scalprunner")
if _WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, _WORKSPACE_DIR)

import scalp_scan_core
from strategy_registry import effective_params, position_notional


# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("ScalpRunner")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[ScalpRunner] %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False


# ── Constants ───────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/api"
STATE_FILE = os.path.join(_AGENTS_DIR, "scalp_runner_state.json")
DEFAULT_POLL_INTERVAL = 15  # seconds — fast scalp cycle


# ============================================================
# State Persistence
# ============================================================

_DEFAULT_STATE = {
    "consecutive_losses": 0,
    "reentry_cooldown": {},        # symbol -> remaining cycles
    "pending_order_ids": {},       # symbol -> order_id (for cancellation)
    "last_cycle_time": None,
    "cycles_run": 0,
}


def load_state() -> dict:
    """Load persisted state from JSON file."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
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


def _api_delete(token: str, path: str) -> dict:
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def post_thought(token: str, text: str) -> None:
    try:
        _api_post(token, "/arena/thought", {"thought": text[:500]})
    except Exception:
        pass


def post_discussion(token: str, title: str, content: str,
                    market: str = "us-stock", symbol: str = "") -> None:
    try:
        _api_post(token, "/signals/discussion", {
            "market": market,
            "symbol": symbol,
            "title": title[:200],
            "content": content[:2000],
        })
    except Exception:
        pass


def post_activity(token: str, text: str, symbol: str = "") -> None:
    """Post cycle activity as both a thought and a discussion for full UI visibility."""
    post_thought(token, text)
    post_discussion(token, text[:200], text, market="us-stock", symbol=symbol)


# ============================================================
# Auth
# ============================================================

def login(name: str = "ScalpRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("SCALP_RUNNER_PASSWORD")
    if not password:
        logger.warning("SCALP_RUNNER_PASSWORD not configured; using dev fallback")
        password = "scalprunner"
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


def register(name: str = "ScalpRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("SCALP_RUNNER_PASSWORD")
    if not password:
        password = "scalprunner"
    initial_cash = float(os.getenv("SCALP_RUNNER_INITIAL_CASH", "10000"))
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "scalprunner@agent.dev",
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
    """Try login first, then register as ScalpRunner."""
    token = login()
    if token:
        logger.info("Logged in as ScalpRunner")
        return token
    logger.info("Login failed, attempting registration...")
    token = register()
    if token:
        logger.info("Registered as ScalpRunner")
    return token


# ============================================================
# Goal & Config Fetching
# ============================================================

def fetch_goal_status(token: str) -> dict:
    try:
        return _api_get(token, "/claw/agents/me/goal")
    except Exception as e:
        logger.warning(f"Could not fetch goal status: {e}")
        return {"can_trade": False, "goal_achieved": False, "max_loss_hit": False,
                "progress_pct": 0, "_unavailable": True}


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
# Pending Order Management
# ============================================================

def fetch_pending_orders(token: str) -> list[dict]:
    """Fetch all PENDING orders for this agent."""
    try:
        result = _api_get(token, "/signals/pending?status=PENDING")
        return result.get("orders", [])
    except Exception as e:
        logger.warning(f"Could not fetch pending orders: {e}")
        return []


def cancel_pending_order(token: str, order_id: int) -> bool:
    """Cancel a pending order by ID."""
    try:
        _api_delete(token, f"/signals/pending/{order_id}")
        logger.info(f"Cancelled pending order {order_id}")
        return True
    except Exception as e:
        logger.warning(f"Could not cancel pending order {order_id}: {e}")
        return False


def create_pending_order(token: str, setup: dict, scan_data: dict,
                         quantity: float, params: dict) -> Optional[int]:
    """Create a pending stop-limit order from a qualifying setup.

    Returns the order_id on success, None on failure.
    """
    symbol = setup["symbol"]
    side = setup.get("direction", "long")
    entry_level = setup.get("entry_level", 0)
    sl_level = setup.get("sl_level", 0)
    tp_level = setup.get("tp_level", 0)
    score = setup.get("score", 0)

    if entry_level <= 0 or quantity <= 0:
        return None

    order_cfg = params.get("order", {})
    offset_pct = order_cfg.get("stop_limit_offset_pct", 0.02)
    expiry_min = order_cfg.get("order_expiry_minutes", 30)

    # Stop price = entry level; limit price = entry + small offset for slippage
    if side == "long":
        limit_price = entry_level * (1 + offset_pct / 100.0)
    else:
        limit_price = entry_level * (1 - offset_pct / 100.0)

    exit_cfg = params.get("exit_rules", {})
    trail_sl = exit_cfg.get("trailing_sl_pct", 0.5)
    trail_act = exit_cfg.get("trailing_activation_pct", 0.8)

    body = {
        "symbol": symbol,
        "market": "us-stock",
        "side": side,
        "order_type": "stop_limit",
        "stop_price": round(entry_level, 6),
        "limit_price": round(limit_price, 6),
        "quantity": quantity,
        "stop_loss_price": round(sl_level, 6) if sl_level > 0 else None,
        "take_profit_price": round(tp_level, 6) if tp_level > 0 else None,
        "trailing_sl_pct": trail_sl,
        "trailing_activation_pct": trail_act,
        "expires_at_minutes": expiry_min,
        "entry_score": score,
        "scan_data": {
            "pattern_type": setup.get("pattern_type", "none"),
            "breakout_level": setup.get("breakout_level", 0),
            "reason": setup.get("reason", ""),
            "atr": setup.get("atr", 0),
        },
    }

    try:
        result = _api_post(token, "/signals/pending", body)
        order_id = result.get("pending_order_id")
        logger.info(f"PENDING ORDER {symbol} ({side}) — stop={entry_level:.4f} "
                     f"limit={limit_price:.4f} qty={quantity:.4f} SL={sl_level:.4f} "
                     f"TP={tp_level:.4f} — order_id={order_id}")
        return order_id
    except urllib.error.HTTPError as e:
        logger.error(f"Pending order failed for {symbol}: {e.code} {e.reason}")
        return None
    except Exception as e:
        logger.error(f"Pending order failed for {symbol}: {e}")
        return None


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
        lo = ps.get("approaching_sizing_min_pct", 5)
        hi = ps.get("approaching_sizing_max_pct", 10)
    else:
        lo = ps.get("normal_sizing_min_pct", 5)
        hi = ps.get("normal_sizing_max_pct", 10)

    size_pct = (lo + hi) / 2.0

    threshold = ps.get("consecutive_loss_threshold", 3)
    if consecutive_losses >= threshold:
        cut = ps.get("consecutive_loss_size_cut_pct", 50)
        size_pct *= (1.0 - cut / 100.0)

    return size_pct, is_final_stretch


# ============================================================
# Active Exit Management
# ============================================================

def execute_close(token: str, symbol: str, side: str, quantity: float,
                  reason: str) -> bool:
    """Close a position via POST /signals/realtime."""
    action = "sell" if side == "long" else "cover"
    try:
        body = {
            "market": "us-stock",
            "action": action,
            "symbol": symbol,
            "price": 0,
            "quantity": quantity,
            "executed_at": "now",
            "content": f"[ScalpRunner] Auto-close: {reason}",
        }
        result = _api_post(token, "/signals/realtime", body)
        logger.info(f"CLOSED {symbol} ({side}) — {reason} — signal_id={result.get('signal_id')}")
        post_activity(token, f"CLOSED {symbol} ({side}) — {reason}", symbol=symbol)
        return True
    except urllib.error.HTTPError as e:
        logger.error(f"Close failed for {symbol}: {e.code} {e.reason}")
        return False
    except Exception as e:
        logger.error(f"Close failed for {symbol}: {e}")
        return False


def review_active_exits(token: str, positions: list[dict], params: dict,
                        state: dict) -> dict:
    """In active exit mode, review open positions and close if exit rules trigger.

    Returns updated state.
    """
    exit_cfg = params.get("exit_rules", {})
    mode = exit_cfg.get("exit_mode", "set_and_forget")
    if mode == "set_and_forget":
        return state  # Server handles SL/TP automatically

    for pos in positions:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "long")
        qty = abs(float(pos.get("quantity", 0)))
        if qty <= 0:
            continue

        pnl_pct = float(pos.get("pnl_pct", 0))
        entry_price = float(pos.get("entry_price", 0))
        current_price = float(pos.get("current_price", 0)) or entry_price
        minutes_held = int(pos.get("minutes_held", 0))

        # Build indicator data for review
        ind_data = {
            "vol_ratio": float(pos.get("vol_ratio", 1.0)),
            "rsi": float(pos.get("rsi", 50)),
        }

        review = scalp_scan_core.review_scalp_position(
            {"pnl_pct": pnl_pct, "side": side}, params, minutes_held, ind_data,
        )

        if review.get("verdict") == "EXIT":
            exit_reason = review.get("exit_reason", "active_exit")
            success = execute_close(token, symbol, side, qty, exit_reason)
            if success:
                if pnl_pct > 0:
                    state["consecutive_losses"] = 0
                else:
                    state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                state["reentry_cooldown"][symbol] = 3
                post_activity(token, f"EXIT {symbol} ({side}) — {exit_reason} — pnl={pnl_pct:.1f}%",
                              symbol=symbol)

    return state


# ============================================================
# Main Cycle
# ============================================================

def run_cycle(token: str, state: dict, params: dict) -> dict:
    """Execute one 4-step scalp trading cycle. Returns updated state."""

    # 1. Fetch goal status
    goal = fetch_goal_status(token)
    if goal.get("_unavailable"):
        post_activity(token, "Cycle skipped: goal service unavailable")
        return state
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

    # 3. Active exit management (if not set-and-forget)
    state = review_active_exits(token, positions, params, state)

    # 4. Manage existing pending orders — cancel stale ones
    pending_orders = fetch_pending_orders(token)
    active_pending_symbols = set()
    pending_gross_exposure = 0.0
    for po in pending_orders:
        po_id = po.get("id")
        po_symbol = po.get("symbol", "")
        po_status = po.get("status", "PENDING")
        if po_status == "PENDING":
            active_pending_symbols.add(po_symbol)
            qty = abs(float(po.get("quantity", 0)))
            trigger = float(po.get("stop_price", 0)) or float(po.get("limit_price", 0))
            pending_gross_exposure += qty * trigger

    # Clean up state's pending_order_ids for orders no longer pending
    state_pending = dict(state.get("pending_order_ids", {}))
    for sym, oid in list(state_pending.items()):
        if sym not in active_pending_symbols:
            del state_pending[sym]
    state["pending_order_ids"] = state_pending

    # 5. Decrement reentry cooldowns
    for sym in list(state.get("reentry_cooldown", {}).keys()):
        if state["reentry_cooldown"][sym] > 0:
            state["reentry_cooldown"][sym] -= 1
        if state["reentry_cooldown"][sym] <= 0:
            del state["reentry_cooldown"][sym]

    # 6. Check if we can place new orders
    open_position_count = len(positions)
    ps_cfg = params.get("position_sizing", {})
    max_positions = ps_cfg.get("max_positions", 3)
    max_pending = ps_cfg.get("max_pending_orders", 5)
    total_active = open_position_count + len(active_pending_symbols)

    if not can_trade or goal_achieved:
        logger.info(f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved}")
        post_activity(token, f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved}")
        return state

    if total_active >= max_positions + max_pending:
        logger.info(f"Capacity full: {open_position_count} positions + "
                     f"{len(active_pending_symbols)} pending = {total_active}")
        return state

    # 7. Run the 4-step scalp scan
    post_activity(token, f"Cycle {state.get('cycles_run', 0) + 1}: running 4-step scalp scan...")

    sys.path.insert(0, _WORKSPACE_DIR)
    import scan as scalp_scan_module
    scan_result = scalp_scan_module.run_scan(token=token)

    ranked_setups = scan_result.get("ranked_setups", [])
    shortlist = scan_result.get("shortlist", [])
    liquid_count = len(scan_result.get("liquid_candidates", []))

    post_activity(token, f"Scan done: {len(shortlist)} shortlist → {liquid_count} liquid → "
                         f"{len(ranked_setups)} qualifying setups | equity=${equity:.0f}")

    if not ranked_setups:
        return state

    # 8. Place pending orders for top setups (up to capacity)
    available_slots = (max_positions + max_pending) - total_active
    placed = 0

    for setup in ranked_setups:
        if placed >= available_slots:
            break

        symbol = setup["symbol"]
        side = setup.get("direction", "long")

        # Skip if already holding or have pending order for this symbol
        if symbol in active_pending_symbols:
            continue
        if any(p.get("symbol", "").upper() == symbol.upper() for p in positions):
            continue
        if symbol in state.get("reentry_cooldown", {}):
            continue

        # Compute quantity from risk-based sizing
        sym_data = scan_result.get("symbols", {}).get(symbol, {})
        entry_price = setup.get("entry_level", 0) or sym_data.get("price", 0)
        sl_level = setup.get("sl_level", 0)

        if entry_price <= 0:
            continue

        stop_distance_pct = abs((sl_level - entry_price) / entry_price) * 100 if sl_level > 0 else 1.0
        notional = position_notional(
            equity, stop_distance_pct, gross_exposure + pending_gross_exposure, params,
        )
        qty = notional / entry_price if notional > 0 else 0

        if qty <= 0:
            continue

        order_id = create_pending_order(token, setup, sym_data, qty, params)
        if order_id:
            state["pending_order_ids"][symbol] = order_id
            active_pending_symbols.add(symbol)
            placed += 1
            score = setup.get("score", 0)
            post_activity(token,
                          f"PRE-POSITION {symbol} ({side}) — score={score:.1f} "
                          f"stop={entry_price:.4f} qty={qty:.4f} notional=${notional:.2f}",
                          symbol=symbol)

    if placed == 0:
        post_activity(token, f"No new orders placed — {len(ranked_setups)} setups but "
                             f"all filtered (cooldowns/already held/pending)")

    return state


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
    logger.info(f"State loaded: consecutive_losses={state.get('consecutive_losses', 0)} "
                f"cycles_run={state.get('cycles_run', 0)} "
                f"pending={len(state.get('pending_order_ids', {}))}")

    cycle = 0
    live_poll = poll_interval
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()

        try:
            # Fetch live config for poll interval
            config = fetch_config(token)
            if config.get("_unavailable"):
                raise RuntimeError("config service unavailable")
            live_poll = config.get("poll_interval", poll_interval)

            # Fetch strategy params through the agent-specific profile resolver
            stored_params = fetch_strategy_params(token)
            if stored_params.get("_unavailable"):
                raise RuntimeError("strategy parameter service unavailable")
            params = effective_params("ScalpRunner", "scalp_4step", stored_params)
            logger.info("Effective profile=%s interval=%ss budget=$%.2f risk=%.2f%%",
                        params["profile"], live_poll,
                        params["risk_controls"]["paper_account_budget"],
                        params["risk_controls"]["risk_per_trade_pct"])

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
            logger.info(f"Cycle {cycle} done in {cycle_time:.1f}s — "
                        f"losses={state.get('consecutive_losses', 0)} "
                        f"pending={len(state.get('pending_order_ids', {}))}")

        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)

        # Sleep in small increments so we can respond to stop signal
        sleep_secs = live_poll
        for _ in range(sleep_secs):
            if stop_event.is_set():
                break
            time.sleep(1)

    logger.info(f"ScalpRunner stopped after {cycle} cycles.")


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="ScalpRunner — 4-Step Scalp Agent")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL,
                        help="Poll interval in seconds (default: 15)")
    parser.add_argument("--cycles", type=int, default=0,
                        help="Max cycles (0 = infinite)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  ScalpRunner — 4-Step Scalp Agent")
    print(f"{'='*60}")
    print(f"  API: {API_BASE}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  State file: {STATE_FILE}")
    print(f"{'='*60}\n")

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print(f"\nStopping ScalpRunner...")
        stop_event.set()

    import signal as _signal
    _signal.signal(_signal.SIGINT, signal_handler)
    _signal.signal(_signal.SIGTERM, signal_handler)

    run_loop(stop_event, args.interval)


if __name__ == "__main__":
    main()
