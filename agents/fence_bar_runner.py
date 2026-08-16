#!/usr/bin/env python3
"""
FenceBarRunner — Opening-Range Fence Bar Day-Trading Agent

Executes the Fence Bar strategy with zero LLM judgment:
  1. 09:30-09:35 ET: fetch 5m bars, run FenceBarStrategy.on_bar() for each symbol
  2. On breakout signal: create a pending order via /signals/pending
  3. Throughout the day: monitor open positions for SL/TP (server-side auto-close)
  4. 15:55 ET: force-close any remaining positions

The runner uses the winning parameters from 12 batches of walk-forward
backtesting with holdout validation:
  - 2R target (target_multiple_r = 2.0) — 4.5x return improvement over 1R
  - ATR 1.2% vol filter (SPY 20-day ATR threshold = 1.2%) — generalizes to holdout
  - No retest (retest.enabled = false)
  - ETF exclusion (SPY/QQQ/IWM removed — biggest single fix found)
  - 26-symbol universe (individual stocks only, no ETFs)
  - Fence range 0.35%-0.80% (ceiling is critical guardrail)
  - 5m bars
  - Fixed SL/TP exit (trailing kills the edge)
  - Force exit at 15:55 ET
  - Max 1 trade per day

Winning config backtest results (Oct 2024 - Aug 2026, 94 windows):
  - Full period: +1.18% return, AggPF 2.78, 11 trades, 0.23% max DD
  - Holdout (70/30 split): train +0.80%, holdout +0.38% (both positive)
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
from zoneinfo import ZoneInfo

# ── Path setup ──────────────────────────────────────────────────────────
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

from fence_bar_strategy import FENCE_BAR_DEFAULTS, FenceBarStrategy, EntrySignal
from strategy_lab import deep_merge
from runner_narrative import RunnerNarrative
from personality_log_forwarder import PersonalityLogForwarder


# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("FenceBarRunner")
_forwarder = PersonalityLogForwarder(runner="fencebarrunner")
narrative = RunnerNarrative("fencebarrunner", printer=_forwarder.printer)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[FenceBarRunner] %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False


# ── Constants ───────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/api"
STATE_FILE = os.path.join(_AGENTS_DIR, "fence_bar_runner_state.json")
DEFAULT_POLL_INTERVAL = 30  # seconds
ET = ZoneInfo("America/New_York")

# 26-symbol universe — individual stocks only, ETFs excluded (SPY/QQQ/IWM)
# ETFs don't have opening-range follow-through; excluding them was the
# single biggest improvement found in 12 batches of testing.
DEFAULT_SYMBOLS = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "BA", "DIS", "BABA", "COIN", "MARA", "RIOT",
    "SOFI", "AAL", "UAL", "F", "GM", "NIO", "XPEV", "PLUG", "DKNG",
]
CONFIG_FILE = os.path.join(_AGENTS_DIR, "fence_bar_runner_config.json")

# Winning parameter overrides on top of FENCE_BAR_DEFAULTS
# Found through 12 batches of walk-forward backtesting with holdout validation.
# See research/strategy_search/STRATEGY_VolFence.md for full documentation.
WINNING_OVERRIDES: dict[str, Any] = {
    "retest": {"enabled": False},
    "fence": {"min_range_pct": 0.35, "max_range_pct": 0.80},
    "risk": {
        "stop_mode": "fence_midpoint",
        "target_multiple_r": 2.0,
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {
        "mode": "fixed_sl_tp",
        "trailing_pct": 0.3,
        "trailing_activation_pct": 0.3,
        "max_bars": 0,
    },
    "vol_filter": {
        "enabled": True,
        "mode": "day",
        "spy_vol_threshold": 1.0,
        "spy_atr_threshold": 1.2,
    },
}


def load_runner_config() -> dict[str, Any]:
    """Load the runner config JSON and merge with FENCE_BAR_DEFAULTS."""
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        params = cfg.get("effective_strategy_params", {})
        base = deep_merge(FENCE_BAR_DEFAULTS, WINNING_OVERRIDES)
        return deep_merge(base, params)
    except Exception as e:
        logger.warning(f"Could not load config file: {e}; using defaults + overrides")
        return deep_merge(FENCE_BAR_DEFAULTS, WINNING_OVERRIDES)


# ============================================================
# State Persistence
# ============================================================

_DEFAULT_STATE = {
    "consecutive_losses": 0,
    "last_signal_date": None,      # ISO date string — one trade per day per symbol
    "signals_posted": {},           # symbol -> last signal date
    "last_cycle_time": None,
    "cycles_run": 0,
    "last_force_exit_date": None,
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


def post_activity(token: str, text: str, symbol: str = "") -> None:
    """Emit a bounded stdout narration line (no network I/O)."""
    narrative.emit(
        "activity", "legacy_activity", facts={"text": text}, message=text,
        symbol=symbol, throttle_key=f"activity:{text[:100]}:{symbol}",
    )


# ============================================================
# Auth
# ============================================================

def login(name: str = "FenceBarRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("FENCE_BAR_RUNNER_PASSWORD")
    if not password:
        logger.warning("FENCE_BAR_RUNNER_PASSWORD not configured; using dev fallback")
        password = "fencebarrunner"
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


def register(name: str = "FenceBarRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("FENCE_BAR_RUNNER_PASSWORD")
    if not password:
        password = "fencebarrunner"
    initial_cash = float(os.getenv("FENCE_BAR_RUNNER_INITIAL_CASH", "10000"))
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "fencebarrunner@agent.dev",
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
    """Try login first, then register as FenceBarRunner."""
    token = login()
    if token:
        logger.info("Logged in as FenceBarRunner")
        return token
    logger.info("Login failed, attempting registration...")
    token = register()
    if token:
        logger.info("Registered as FenceBarRunner")
    return token


# ============================================================
# Config & Portfolio Fetching
# ============================================================

def fetch_config(token: str) -> dict:
    try:
        return _api_get(token, "/claw/agents/me/config")
    except Exception as e:
        logger.warning(f"Could not fetch config: {e}")
        return {"watchlist": [], "poll_interval": DEFAULT_POLL_INTERVAL, "_unavailable": True}


def fetch_goal_status(token: str) -> dict:
    try:
        return _api_get(token, "/claw/agents/me/goal")
    except Exception as e:
        logger.warning(f"Could not fetch goal status: {e}")
        return {"can_trade": False, "goal_achieved": False, "max_loss_hit": False,
                "progress_pct": 0, "_unavailable": True}


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


def create_pending_order(token: str, signal: EntrySignal, quantity: float) -> Optional[int]:
    """Create a pending stop-limit order from a FenceBarStrategy entry signal.

    Returns the order_id on success, None on failure.
    """
    if signal.entry_price <= 0 or quantity <= 0:
        return None

    side = signal.side
    entry_level = signal.entry_price
    sl_level = signal.stop_price
    tp_level = signal.target_price

    # Stop-limit: stop at entry level, limit slightly beyond for slippage
    offset_pct = 0.02
    if side == "long":
        limit_price = entry_level * (1 + offset_pct / 100.0)
    else:
        limit_price = entry_level * (1 - offset_pct / 100.0)

    body = {
        "symbol": signal.symbol,
        "market": "us-stock",
        "side": side,
        "order_type": "stop_limit",
        "stop_price": round(entry_level, 6),
        "limit_price": round(limit_price, 6),
        "quantity": quantity,
        "stop_loss_price": round(sl_level, 6) if sl_level > 0 else None,
        "take_profit_price": round(tp_level, 6) if tp_level > 0 else None,
        "trailing_sl_pct": 0,
        "trailing_activation_pct": 0,
        "expires_at_minutes": 360,
        "entry_score": 1.0,
        "scan_data": {
            "pattern_type": "fence_bar_breakout",
            "breakout_level": entry_level,
            "reason": signal.reason,
            "fence_high": signal.fence_high,
            "fence_low": signal.fence_low,
            "risk_per_share": signal.risk_per_share,
        },
    }

    try:
        result = _api_post(token, "/signals/pending", body)
        order_id = result.get("pending_order_id")
        logger.info(f"PENDING ORDER {signal.symbol} ({side}) — stop={entry_level:.4f} "
                     f"qty={quantity:.4f} SL={sl_level:.4f} TP={tp_level:.4f} — order_id={order_id}")
        return order_id
    except urllib.error.HTTPError as e:
        logger.error(f"Pending order failed for {signal.symbol}: {e.code} {e.reason}")
        return None
    except Exception as e:
        logger.error(f"Pending order failed for {signal.symbol}: {e}")
        return None


# ============================================================
# Position Management
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
            "content": f"[FenceBarRunner] Auto-close: {reason}",
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


def force_exit_positions(token: str, positions: list[dict], state: dict) -> dict:
    """Force-close all open FenceBarRunner positions at 15:55 ET."""
    closed = 0
    for pos in positions:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "long")
        qty = abs(float(pos.get("quantity", 0)))
        if qty <= 0:
            continue
        success = execute_close(token, symbol, side, qty, "force_exit_15:55")
        if success:
            closed += 1
            pnl_pct = float(pos.get("pnl_pct", 0))
            if pnl_pct > 0:
                state["consecutive_losses"] = 0
            else:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    if closed > 0:
        post_activity(token, f"Force-exited {closed} position(s) at 15:55 ET")
    today = datetime.now(ET).date().isoformat()
    state["last_force_exit_date"] = today
    return state


# ============================================================
# Time Helpers
# ============================================================

def now_et() -> datetime:
    return datetime.now(ET)


def et_time_str() -> str:
    return now_et().strftime("%H:%M")


def et_date_str() -> str:
    return now_et().date().isoformat()


def is_fence_window(params: dict) -> bool:
    """Check if current ET time is within the fence bar signal window (09:30-10:30)."""
    session = params.get("session", {})
    open_time = session.get("market_open", "09:30")
    latest_breakout = session.get("latest_breakout", "10:30")
    current = et_time_str()
    return open_time <= current <= latest_breakout


def is_force_exit_time(params: dict) -> bool:
    """Check if current ET time is at or past force_exit (15:55)."""
    force_exit = params.get("session", {}).get("force_exit", "15:55")
    current = et_time_str()
    return current >= force_exit


def is_market_hours(params: dict) -> bool:
    """Check if current ET time is within market hours (09:30-16:00)."""
    current = et_time_str()
    return "09:30" <= current < "16:00"


# ============================================================
# Vol Filter Check
# ============================================================

def check_vol_filter(params: dict) -> bool:
    """Check SPY volatility filter using daily ATR.

    Returns True if the vol filter passes (or is disabled).
    Uses the ArenaMarketDataProvider to fetch SPY daily data.
    """
    cfg = params.get("vol_filter", {})
    if not cfg.get("enabled", True):
        return True
    try:
        from arena_market_data import get_arena_market_data
        provider = get_arena_market_data()
        spy = provider.history("SPY", period="3mo", interval="1d")
        if spy is None or spy.empty:
            logger.warning("Vol filter: SPY data unavailable — allowing trade")
            return True
        spy = spy.reset_index() if spy.index.name else spy
        col = "Datetime" if "Datetime" in spy.columns else "Date"
        spy[col] = __import__("pandas").to_datetime(spy[col])
        for c in ("High", "Low", "Close"):
            spy[c] = __import__("pandas").to_numeric(spy[c], errors="coerce")
        spy["ATR_pct"] = (spy["High"] - spy["Low"]) / spy["Close"] * 100
        spy["ATR20"] = spy["ATR_pct"].rolling(20).mean()
        spy["Vol20"] = spy["Close"].pct_change().rolling(20).std() * 100
        last = spy.dropna(subset=["ATR20", "Vol20"]).iloc[-1]
        vol_threshold = float(cfg.get("spy_vol_threshold", 1.0))
        atr_threshold = float(cfg.get("spy_atr_threshold", 1.8))
        vol_ok = float(last["Vol20"]) >= vol_threshold
        atr_ok = float(last["ATR20"]) >= atr_threshold
        if not (vol_ok and atr_ok):
            logger.info(f"Vol filter FAIL: Vol20={last['Vol20']:.2f} (need {vol_threshold}), "
                        f"ATR20={last['ATR20']:.2f} (need {atr_threshold})")
        return vol_ok and atr_ok
    except Exception as e:
        logger.warning(f"Vol filter check failed: {e} — allowing trade")
        return True


# ============================================================
# Bar Fetching & Signal Generation
# ============================================================

def fetch_5m_bars(symbol: str) -> Optional[Any]:
    """Fetch recent 5m bars for a symbol via the Arena market data provider."""
    try:
        from arena_market_data import get_arena_market_data
        import pandas as pd
        provider = get_arena_market_data()
        frame = provider.history(symbol, period="5d", interval="5m")
        if frame is None or frame.empty:
            return None
        frame = frame.copy().reset_index()
        time_col = "Datetime" if "Datetime" in frame.columns else "Date"
        frame[time_col] = pd.to_datetime(frame[time_col], errors="coerce")
        frame = frame.dropna(subset=[time_col]).rename(columns={time_col: "Timestamp"})
        if getattr(frame["Timestamp"].dt, "tz", None) is not None:
            frame["Timestamp"] = frame["Timestamp"].dt.tz_convert("America/New_York").dt.tz_localize(None)
        else:
            frame["Timestamp"] = frame["Timestamp"].dt.tz_localize(None)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in frame.columns:
                return None
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Timestamp")
        # Filter to today's session only
        today = now_et().date()
        frame = frame[frame["Timestamp"].dt.date == today]
        frame = frame[(frame["Timestamp"].dt.time >= datetime.strptime("09:30", "%H:%M").time()) &
                      (frame["Timestamp"].dt.time <= datetime.strptime("16:00", "%H:%M").time())]
        return frame.reset_index(drop=True)
    except Exception as e:
        logger.warning(f"Failed to fetch 5m bars for {symbol}: {e}")
        return None


def run_fence_signals(token: str, symbols: list[str], params: dict,
                      state: dict, equity: float) -> dict:
    """Run FenceBarStrategy.on_bar() for each symbol and post pending orders.

    Returns updated state. Enforces max_trades_per_day from params.
    """
    today = et_date_str()
    signals_posted = state.get("signals_posted", {})
    max_trades = int(params.get("risk", {}).get("max_trades_per_day", 1))

    # Count how many signals already posted today across all symbols
    trades_today = sum(1 for s, d in signals_posted.items() if d == today)

    # Check vol filter once per day
    if not check_vol_filter(params):
        narrative.emit("vol_filter", "decision", "filtered", priority="action",
                        message="SPY vol filter failed — no trades today.")
        post_activity(token, "Vol filter failed — skipping fence bar signals today")
        return state

    if trades_today >= max_trades:
        logger.info(f"Max trades per day ({max_trades}) already reached — skipping scan")
        return state

    narrative.emit("scan", "phase", "started", priority="action", facts={
        "symbols": symbols, "window": "09:30-10:30",
        "trades_today": trades_today, "max_trades": max_trades,
    })

    placed = 0
    for symbol in symbols:
        # Stop if we've hit the daily trade limit
        if trades_today + placed >= max_trades:
            logger.info(f"Daily trade limit ({max_trades}) reached — stopping scan")
            break

        # Skip if already signaled for this symbol today
        if signals_posted.get(symbol) == today:
            continue

        bars = fetch_5m_bars(symbol)
        if bars is None or bars.empty:
            logger.info(f"No bars for {symbol} — skipping")
            continue

        strategy = FenceBarStrategy(symbol, params)
        signal: EntrySignal | None = None
        for index, bar in bars.iterrows():
            timestamp = bar["Timestamp"]
            signal = strategy.on_bar(timestamp, bar, index)
            if signal is not None:
                break

        if signal is None:
            logger.info(f"No fence bar signal for {symbol} — state={strategy.state}")
            continue

        # Compute quantity from risk-based sizing
        risk_pct = float(params.get("risk", {}).get("risk_per_trade_pct", 0.5))
        risk_budget = equity * risk_pct / 100.0
        if signal.risk_per_share <= 0:
            continue
        quantity = risk_budget / signal.risk_per_share
        # Cap at 25% of equity
        max_qty = equity * 0.25 / signal.entry_price
        quantity = min(quantity, max_qty)
        if quantity <= 0:
            continue

        narrative.emit("signal", "decision", "qualified", priority="action", symbol=symbol,
                        detail=True, facts={
                            "side": signal.side, "entry": signal.entry_price,
                            "stop": signal.stop_price, "target": signal.target_price,
                            "quantity": quantity, "risk_per_share": signal.risk_per_share,
                        })

        order_id = create_pending_order(token, signal, quantity)
        if order_id:
            signals_posted[symbol] = today
            state["signals_posted"] = signals_posted
            placed += 1
            post_activity(token,
                          f"FENCE BAR SIGNAL {symbol} ({signal.side}) — "
                          f"entry={signal.entry_price:.2f} SL={signal.stop_price:.2f} "
                          f"TP={signal.target_price:.2f} qty={quantity:.4f}",
                          symbol=symbol)

    narrative.emit("scan", "phase", "complete", priority="action", facts={
        "signals_placed": placed, "symbols_checked": len(symbols),
        "trades_today": trades_today + placed, "max_trades": max_trades,
    })
    if placed == 0:
        post_activity(token, f"Fence bar scan complete — 0 signals from {len(symbols)} symbols")
    return state


# ============================================================
# Main Cycle
# ============================================================

def run_cycle(token: str, state: dict, params: dict, symbols: list[str]) -> dict:
    """Execute one FenceBarRunner trading cycle. Returns updated state."""

    # 1. Fetch goal status
    goal = fetch_goal_status(token)
    if goal.get("_unavailable"):
        narrative.emit("goal", "error", "unavailable", priority="error")
        return state
    can_trade = goal.get("can_trade", goal.get("status") == "no_goal")
    goal_achieved = goal.get("goal_achieved", False)
    max_loss_hit = goal.get("max_loss_hit", False)

    if max_loss_hit:
        narrative.emit("goal", "decision", "halted", priority="critical",
                        message="Max loss hit — standing down until reset.")
        logger.warning("Max loss hit — not trading.")
        return state

    # 2. Fetch portfolio for equity calculation
    portfolio = fetch_portfolio(token)
    if portfolio.get("_unavailable"):
        narrative.emit("portfolio", "error", "unavailable", priority="error")
        return state
    cash = float(portfolio.get("cash", 0.0))
    positions = portfolio.get("positions", [])

    equity = cash
    for p in positions:
        qty = abs(float(p.get("quantity", 0)))
        price = float(p.get("current_price", 0)) or float(p.get("entry_price", 0))
        side = p.get("side", "long")
        if side == "long":
            equity += qty * price
        else:
            equity -= qty * price

    narrative.emit("portfolio", "phase", "measured", priority="action", facts={
        "cash": round(cash, 2), "equity": round(equity, 2),
        "open_positions": len(positions),
    })

    # 3. Check if we're in market hours
    if not is_market_hours(params):
        logger.info("Outside market hours — skipping cycle")
        return state

    # 4. Force exit at 15:55 ET
    today = et_date_str()
    if is_force_exit_time(params) and state.get("last_force_exit_date") != today:
        if positions:
            state = force_exit_positions(token, positions, state)
        else:
            state["last_force_exit_date"] = today
        return state

    # 5. During fence window: generate signals
    if not can_trade or goal_achieved:
        logger.info(f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved}")
        return state

    if is_fence_window(params):
        state = run_fence_signals(token, symbols, params, state, equity)
    else:
        logger.info(f"Outside fence window (current={et_time_str()}) — monitoring only")

    return state


# ============================================================
# Main Loop
# ============================================================

def run_loop(stop_event: threading.Event, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
    """Main loop — runs cycles until stop_event is set."""

    token = connect()
    if not token:
        logger.error("Could not connect to platform. Exiting.")
        narrative.emit("startup", "error", "failed", priority="error",
                        message="Connection failed; staying safely offline.")
        return

    narrative.emit("startup", "startup", "ready", priority="action")
    state = load_state()
    params = load_runner_config()
    symbols = params.get("watchlist", DEFAULT_SYMBOLS)

    logger.info(f"State loaded: cycles_run={state.get('cycles_run', 0)} "
                f"signals_posted={len(state.get('signals_posted', {}))}")
    logger.info(f"Symbols: {symbols} | Poll: {poll_interval}s | "
                f"Target R: {params.get('risk', {}).get('target_multiple_r')} | "
                f"Retest: {params.get('retest', {}).get('enabled')}")

    cycle = 0
    live_poll = poll_interval
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()
        narrative.begin_cycle(cycle)
        narrative.emit("cycle", "phase", facts={"cycle": cycle}, priority="action")

        try:
            # Fetch live config for poll interval
            config = fetch_config(token)
            if config.get("_unavailable"):
                raise RuntimeError("config service unavailable")
            live_poll = config.get("poll_interval", poll_interval)

            # Override symbols from config if present
            if config.get("watchlist"):
                symbols = config["watchlist"]

            # Run the cycle
            state = run_cycle(token, state, params, symbols)

            # Send heartbeat
            send_heartbeat(token)

            # Update state
            state["cycles_run"] = state.get("cycles_run", 0) + 1
            state["last_cycle_time"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            cycle_time = time.time() - cycle_start
            logger.info(f"Cycle {cycle} done in {cycle_time:.1f}s — "
                        f"signals={len(state.get('signals_posted', {}))}")
            narrative.recap({
                "duration_seconds": round(cycle_time, 2),
                "signals_posted": len(state.get("signals_posted", {})),
            })

        except Exception as e:
            logger.error(f"Cycle {cycle} error: {e}", exc_info=True)
            narrative.emit("cycle", "error", "failed", priority="error",
                            facts={"error": str(e)[:300]})

        # Sleep in small increments so we can respond to stop signal
        sleep_secs = live_poll
        for _ in range(sleep_secs):
            if stop_event.is_set():
                break
            time.sleep(1)

    logger.info(f"FenceBarRunner stopped after {cycle} cycles.")


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="FenceBarRunner — Opening-Range Fence Bar Agent")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL,
                        help="Poll interval in seconds (default: 30)")
    parser.add_argument("--cycles", type=int, default=0,
                        help="Max cycles (0 = infinite)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  FenceBarRunner — Opening-Range Fence Bar Agent")
    print(f"{'='*60}")
    print(f"  API: {API_BASE}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  State file: {STATE_FILE}")
    print(f"  Config file: {CONFIG_FILE}")
    print(f"{'='*60}\n")

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print(f"\nStopping FenceBarRunner...")
        stop_event.set()

    import signal as _signal
    _signal.signal(_signal.SIGINT, signal_handler)
    _signal.signal(_signal.SIGTERM, signal_handler)

    run_loop(stop_event, args.interval)


if __name__ == "__main__":
    main()
