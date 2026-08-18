#!/usr/bin/env python3
"""
ORBRunner — Opening Range Breakout Options Agent

Executes the corrected ORB Options strategy with zero LLM judgment:
  1. 09:30-09:35 ET: fetch 1m bars and build the exclusive opening range
  2. 09:35-10:00 ET: require two confirmed breakout closes before entry
  3. Throughout the day: monitor underlying for stop/target, exit options
  4. 15:55 ET: force-close any remaining option positions

The canonical paper configuration is maintained in ORB_CONFIG below and mirrors
STRATEGY_ORB_OPTIONS_WINNER.md. It remains shadow/paper-only because the
corrected backtest has not yet passed the live-capital drawdown gate.

See docs/ORB_OPTIONS_STRATEGY.md for full documentation.
"""

import json
import os
import sys
import time
import logging
import argparse
import threading
import urllib.request

# Load .env from project root so Alpaca API keys are available
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_path = _Path(__file__).resolve().parent.parent / ".env"
    _load_dotenv(_env_path)
except ImportError:
    pass
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

# ── Path setup ──────────────────────────────────────────────────────────
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

from runner_narrative import RunnerNarrative
from personality_log_forwarder import PersonalityLogForwarder

# Phase 2: import canonical strategy core and centralized execution
from orb_strategy import (
    ORBStrategyConfig, StrategyMode, ExecutionMode, IntrabarPolicy, RangeEndPolicy,
    select_strike as canonical_select_strike,
    check_exit as canonical_check_exit,
    OpeningRangeBuilder, BreakoutChecker,
)

# ── Logging ─────────────────────────────────────────────────────────────
logger = logging.getLogger("ORBRunner")
_forwarder = PersonalityLogForwarder(runner="orbrunner")
narrative = RunnerNarrative("orbrunner", printer=_forwarder.printer)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("[ORBRunner] %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False

# ── Constants ───────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000/api"
STATE_FILE = os.path.join(_AGENTS_DIR, "orb_runner_state.json")
DEFAULT_POLL_INTERVAL = 30  # seconds
ET = ZoneInfo("America/New_York")

# Winning config from validated backtest
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "COIN"]

# Broader universe for dynamic discovery — large-caps with liquid options
SCANNER_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "BA", "DIS", "BABA", "COIN", "MARA", "RIOT",
    "SOFI", "AAL", "UAL", "F", "GM", "NIO", "XPEV", "PLUG", "DKNG",
    "SPOT", "SNAP", "PINS", "ROKU", "ZM", "SQ", "SHOP", "COIN",
]

STRIKE_STEPS = {
    "NVDA": 2.5, "TSLA": 2.5, "AMD": 0.5, "AAPL": 2.5, "META": 2.5,
    "AMZN": 2.5, "MSFT": 2.5, "GOOGL": 2.5, "NFLX": 5.0, "INTC": 0.5,
    "COIN": 2.5, "MU": 0.5, "BA": 5.0, "DIS": 0.5, "BABA": 2.5,
    "MARA": 0.5, "RIOT": 0.5, "SOFI": 0.5, "AAL": 0.5, "UAL": 0.5,
    "F": 0.5, "GM": 0.5, "NIO": 0.5, "XPEV": 0.5, "PLUG": 0.5,
    "DKNG": 1.0, "SPOT": 2.5, "SNAP": 0.5, "PINS": 0.5, "ROKU": 1.0,
    "ZM": 2.5, "SQ": 2.5, "SHOP": 2.5,
}

ORB_CONFIG = {
    # Canonical corrected strategy configuration.
    "config_version": "2.1-corrected-paper",
    "range_minutes": 5,
    "range_end_policy": "exclusive",
    "confirmation_bars": 2,
    "skip_first_post_range_bar": True,
    "stop_pct": 1.0,
    "target_pct": 2.0,
    "latest_entry": "10:00",
    "max_positions": 4,
    "position_pct": 3.0,
    "strike_offset": 1,
    "strategy_mode": "symmetric_otm",
    "dte_min": 2,
    "dte_max": 14,
    "option_slippage_bps": 50.0,
    "option_spread_bps": 100.0,
    "contract_fee": 0.65,
    "confirmation_minutes": 10,
    "circuit_breaker": 3,
    "risk_free_rate": 0.05,
    "min_entry_time": "09:30",
    # Dynamic discovery selects premarket movers before the ORB window.
    "discovery_mode": "dynamic",
    "discovery_max_symbols": 4,
    "discovery_min_change_pct": 1.0,
    "discovery_universe": SCANNER_UNIVERSE,
    "max_signal_age_seconds": 300,
    "intrabar_policy": "conservative",
    # Candidate sizing is measured in shadow mode; execution remains disabled.
    "dynamic_sizing": True,
    "max_position_pct": 6.0,
    "max_total_pct": 12.0,
    # Shadow mode remains enabled until the shadow gate is explicitly cleared.
    "shadow_mode": True,
    "paper_only": True,
    "min_option_entry_price": 0.20,
    "daily_loss_limit_pct": 10.0,
    "max_drawdown_limit_pct": 30.0,
}


# ============================================================
# State Persistence
# ============================================================

_DEFAULT_STATE = {
    "consecutive_losses": 0,
    "day_loss_streaks": {},       # symbol -> consecutive losses today
    "signals_posted": {},          # symbol -> last signal date
    "last_cycle_time": None,
    "cycles_run": 0,
    "last_force_exit_date": None,
    "open_positions": {},          # symbol -> position metadata
    "discovered_symbols": {},      # date -> list[str] of movers for that day
    "discovery_meta": {},          # date -> {timestamp, et_time, late, count}
    "order_history": {},           # symbol -> {client_order_id, alpaca_order_id, status, ts}
    "last_reconcile_date": None,  # YYYY-MM-DD of last Alpaca reconciliation
    "config_version": "2.1-corrected-paper",  # tracked config version
    "shadow_signals": {},          # date -> list of shadow signals
    "sizing_state": {},            # date -> cumulative reserved allocation
    "risk_state": {},              # daily equity baseline, peak, and halt reason
}


def load_state() -> dict:
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
# State Reconciliation & Order Lifecycle (Phase 3)
# ============================================================

def reconcile_state_with_alpaca(state: dict) -> dict:
    """Reconcile internal state against Alpaca paper positions.

    On startup or once per day, check if Alpaca has positions that
    we don't know about (or vice versa).  This handles:
    - Positions opened by a previous run that crashed before saving state
    - Positions closed by Alpaca SL/TP that we haven't recorded
    - Stale state entries for positions that no longer exist

    Returns updated state.
    """
    today = et_date_str()
    if state.get("last_reconcile_date") == today:
        return state  # already reconciled today

    logger.info("Reconciling state with Alpaca paper positions...")
    alpaca_positions = get_alpaca_positions()
    if alpaca_positions is None:
        alpaca_positions = []

    # Build set of Alpaca option position symbols (OCC symbols)
    alpaca_symbols = {p.get("symbol", "") for p in alpaca_positions}

    # Check for positions in state but not on Alpaca (already closed)
    internal_positions = state.get("open_positions", {})
    stale = []
    for symbol, pos in list(internal_positions.items()):
        occ = pos.get("occ_symbol", "")
        if occ and occ not in alpaca_symbols:
            stale.append(symbol)
            logger.info(f"Reconcile: {symbol} ({occ}) not on Alpaca — marking closed")
            # Record in order history as closed
            order_hist = state.get("order_history", {})
            order_hist[symbol] = {
                "client_order_id": f"orb:reconcile:{symbol}:{today}",
                "status": "closed_externally",
                "closed_ts": datetime.now(timezone.utc).isoformat(),
            }
            state["order_history"] = order_hist
            del internal_positions[symbol]

    if stale:
        post_activity(None, f"Reconciled {len(stale)} stale position(s): {', '.join(stale)}")

    # Check for positions on Alpaca but not in state (orphaned)
    internal_occs = {p.get("occ_symbol", "") for p in internal_positions.values()}
    orphaned = []
    for ap in alpaca_positions:
        occ = ap.get("symbol", "")
        if occ and occ not in internal_occs:
            # This is an orphaned position — log it but don't auto-adopt
            # (we don't know the stop/target/entry context)
            orphaned.append(occ)
            logger.warning(f"Reconcile: orphaned Alpaca position {occ} (qty={ap.get('qty', '?')}) — not auto-adopting")

    if orphaned:
        post_activity(None, f"WARNING: {len(orphaned)} orphaned Alpaca position(s): {', '.join(orphaned)}")

    state["last_reconcile_date"] = today
    logger.info(f"Reconcile complete: {len(stale)} stale, {len(orphaned)} orphaned")
    return state


def record_order_lifecycle(
    state: dict,
    symbol: str,
    client_order_id: str,
    alpaca_order_id: str | None,
    status: str,
) -> None:
    """Record an order lifecycle event in state.

    This provides an audit trail of entries and exits.
    """
    order_hist = state.get("order_history", {})
    order_hist[symbol] = {
        "client_order_id": client_order_id,
        "alpaca_order_id": alpaca_order_id,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    state["order_history"] = order_hist


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


def post_activity(token: str, text: str, symbol: str = "") -> None:
    narrative.emit(
        "activity", "legacy_activity", facts={"text": text}, message=text,
        symbol=symbol, throttle_key=f"activity:{text[:100]}:{symbol}",
    )


# ============================================================
# Auth
# ============================================================

def login(name: str = "ORBRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("ORB_RUNNER_PASSWORD")
    if not password:
        logger.warning("ORB_RUNNER_PASSWORD not configured; using dev fallback")
        password = "orbrunner"
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


def register(name: str = "ORBRunner", password: Optional[str] = None) -> Optional[str]:
    password = password or os.getenv("ORB_RUNNER_PASSWORD")
    if not password:
        password = "orbrunner"
    initial_cash = float(os.getenv("ORB_RUNNER_INITIAL_CASH", "10000"))
    try:
        url = f"{API_BASE}/claw/agents/selfRegister"
        data = json.dumps({
            "name": name,
            "email": "orbrunner@agent.dev",
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
        logger.info("Logged in as ORBRunner")
        return token
    logger.info("Login failed, attempting registration...")
    token = register()
    if token:
        logger.info("Registered as ORBRunner")
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


def apply_platform_runtime_config(live_config: dict, symbols: list[str]) -> list[str]:
    """Apply only non-strategy platform settings to the runner.

    The canonical strategy universe is source-controlled; legacy watchlists
    are logged and ignored so database state cannot alter the experiment.
    """
    if live_config.get("watchlist"):
        logger.warning(
            "Ignoring platform watchlist for canonical ORB strategy: %s",
            live_config["watchlist"],
        )
    return symbols


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
# Time Helpers
# ============================================================

def now_et() -> datetime:
    return datetime.now(ET)


def et_time_str() -> str:
    return now_et().strftime("%H:%M")


def et_date_str() -> str:
    return now_et().date().isoformat()


def is_orb_window(config: dict) -> bool:
    """Check if current ET time is within the ORB signal window (09:30-10:00)."""
    min_entry = config.get("min_entry_time", "09:30")
    latest = config.get("latest_entry", "10:00")
    current = et_time_str()
    return min_entry <= current <= latest


def is_force_exit_time() -> bool:
    return et_time_str() >= "15:55"


def is_market_hours() -> bool:
    current = et_time_str()
    return "09:30" <= current < "16:00"


# ============================================================
# Dynamic Symbol Discovery
# ============================================================

def discover_movers(config: dict) -> list[str]:
    """Discover top movers for today's ORB trading universe.

    Tries Schwab movers first (live indices), falls back to Alpaca
    snapshots on the scanner universe. Filters by min change % and
    returns top N symbols ranked by abs(change_pct).

    If neither provider is available, falls back to DEFAULT_SYMBOLS.

    Phase 6: Lookahead guard — discovery data must represent pre-market
    movement only.  If discovery runs after 09:30 ET, the change_pct
    may include opening range movement, which would bias selection
    toward symbols that already broke out.  We cap the change_pct
    and log a warning when discovery is late.
    """
    max_symbols = config.get("discovery_max_symbols", 8)
    min_change = config.get("discovery_min_change_pct", 1.0)
    universe = config.get("discovery_universe", SCANNER_UNIVERSE)
    candidates: dict[str, dict] = {}

    # Phase 6: Lookahead guard — check if discovery is running late
    current_time = et_time_str()
    is_late_discovery = current_time >= "09:30"
    if is_late_discovery:
        logger.warning(
            f"Discovery running at {current_time} — post-open data may "
            f"include opening range movement (lookahead risk). "
            f"Results will be flagged in state."
        )

    # 1. Try Schwab movers (live up/down from $COMPX, $DJI, $SPX)
    try:
        from schwab_provider import get_schwab_provider
        provider = get_schwab_provider()
        if provider.is_configured:
            movers = provider.movers_all()
            for m in movers:
                sym = m.get("symbol", "").upper()
                if sym and sym not in candidates:
                    candidates[sym] = {
                        "symbol": sym,
                        "change_pct": abs(m.get("change_pct", 0)),
                        "source": "schwab_movers",
                    }
            logger.info(f"Schwab movers: {len(candidates)} candidates")
    except Exception as e:
        logger.warning(f"Schwab movers unavailable: {e}")

    # 2. Fall back to Alpaca snapshots on the scanner universe
    if not candidates:
        try:
            from alpaca_realtime_provider import get_alpaca_provider
            alpaca = get_alpaca_provider()
            if alpaca.is_configured:
                movers = alpaca.screen_movers(universe, top_n=max_symbols * 2)
                for m in movers:
                    sym = m.get("symbol", "").upper()
                    if sym and sym not in candidates:
                        candidates[sym] = {
                            "symbol": sym,
                            "change_pct": abs(m.get("change_pct", 0)),
                            "source": "alpaca_snapshots",
                        }
                logger.info(f"Alpaca snapshots: {len(candidates)} candidates")
        except Exception as e:
            logger.warning(f"Alpaca snapshots unavailable: {e}")

    # 3. Fall back to fixed default symbols
    if not candidates:
        logger.info("No movers found — using DEFAULT_SYMBOLS")
        return list(DEFAULT_SYMBOLS)

    # Filter by min change % and rank by momentum
    filtered = [c for c in candidates.values() if c["change_pct"] >= min_change]
    if not filtered:
        logger.info(f"No movers above {min_change}% change — using DEFAULT_SYMBOLS")
        return list(DEFAULT_SYMBOLS)

    ranked = sorted(filtered, key=lambda c: c["change_pct"], reverse=True)
    result = [c["symbol"] for c in ranked[:max_symbols]]
    logger.info(f"Discovery: {len(result)} symbols from {len(candidates)} candidates "
                f"(min {min_change}% change) — {result}")
    return result


# ============================================================
# ORB Signal Generation
# ============================================================

@dataclass
class ORBRange:
    """Opening range for a symbol."""
    symbol: str
    range_high: float
    range_low: float
    range_end_time: str  # "09:35"


@dataclass
class ORBSignal:
    """ORB breakout signal."""
    symbol: str
    side: str           # "long" or "short"
    entry_price: float   # underlying close at breakout
    stop_price: float    # underlying stop
    target_price: float  # underlying target
    timestamp: str
    option_type: str     # "call" or "put"


def fetch_1m_bars(symbol: str) -> Optional[list[dict]]:
    """Fetch today's 1m bars for a symbol via the Arena market data provider."""
    try:
        from arena_market_data import get_arena_market_data
        import pandas as pd
        provider = get_arena_market_data()
        frame = provider.history(symbol, period="1d", interval="1m")
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
        today = now_et().date()
        frame = frame[frame["Timestamp"].dt.date == today]
        frame = frame[(frame["Timestamp"].dt.time >= datetime.strptime("09:30", "%H:%M").time()) &
                      (frame["Timestamp"].dt.time <= datetime.strptime("16:00", "%H:%M").time())]
        return frame.to_dict("records")
    except Exception as e:
        logger.warning(f"Failed to fetch 1m bars for {symbol}: {e}")
        return None


def build_opening_range(symbol: str, bars: list[dict], config: dict) -> Optional[ORBRange]:
    """Build the opening range from the first N minutes of bars.

    Phase 4: Now uses canonical OpeningRangeBuilder from orb_strategy.py
    for deterministic range construction with proper boundary handling.
    """
    range_end_policy = config.get("range_end_policy", "inclusive")
    if range_end_policy == "exclusive":
        cfg = ORBStrategyConfig(range_end_policy=RangeEndPolicy.EXCLUSIVE,
                                range_minutes=config.get("range_minutes", 5))
    else:
        cfg = ORBStrategyConfig(range_end_policy=RangeEndPolicy.INCLUSIVE,
                                range_minutes=config.get("range_minutes", 5))
    builder = OpeningRangeBuilder(cfg)
    canonical_range = builder.build(symbol, bars)
    if canonical_range is None:
        return None
    # Convert to legacy ORBRange format for backward compatibility
    range_minutes = config.get("range_minutes", 5)
    if range_end_policy == "exclusive":
        range_end = f"09:{30 + range_minutes - 1:02d}"
    else:
        range_end = f"09:{30 + range_minutes:02d}"
    return ORBRange(
        symbol=symbol,
        range_high=canonical_range.range_high,
        range_low=canonical_range.range_low,
        range_end_time=range_end,
    )


def check_breakout(symbol: str, bars: list[dict], orb_range: ORBRange,
                   config: dict) -> Optional[ORBSignal]:
    """Check if any bar after the range has a close outside the range.

    Phase 4: Now uses canonical BreakoutChecker from orb_strategy.py
    with signal freshness enforcement, duplicate bar guard, and
    one-signal-per-symbol-per-session policy.
    """
    stop_pct = config.get("stop_pct", 1.0)
    target_pct = config.get("target_pct", 1.5)
    latest_entry = config.get("latest_entry", "10:00")
    max_signal_age = config.get("max_signal_age_seconds", 120)
    confirmation_bars = config.get("confirmation_bars", 1)

    # Build canonical config for BreakoutChecker
    cfg = ORBStrategyConfig(
        stop_pct=stop_pct,
        target_pct=target_pct,
        latest_entry=latest_entry,
        confirmation_bars=confirmation_bars,
        skip_first_post_range_bar=config.get("skip_first_post_range_bar", False),
        intrabar_policy=IntrabarPolicy(config.get("intrabar_policy", "conservative")),
        strategy_mode=StrategyMode(config.get("strategy_mode", "symmetric_otm")),
        max_signal_age_seconds=max_signal_age,
    )

    # Use a module-level checker per symbol to maintain state
    if not hasattr(check_breakout, "_checkers"):
        check_breakout._checkers = {}
    if symbol not in check_breakout._checkers:
        check_breakout._checkers[symbol] = BreakoutChecker(cfg)
    checker = check_breakout._checkers[symbol]

    # Convert legacy ORBRange to canonical ORBRange
    from orb_strategy import ORBRange as CanonicalORBRange
    from datetime import datetime as _dt
    # Parse range_end_time (e.g. "09:35") into today's datetime
    range_end_ts = _dt.now()
    try:
        parts = orb_range.range_end_time.split(":")
        range_end_ts = _dt.now().replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0)
    except Exception:
        pass
    canonical_range = CanonicalORBRange(
        symbol=orb_range.symbol,
        range_high=orb_range.range_high,
        range_low=orb_range.range_low,
        range_start_ts=range_end_ts,  # not used by checker
        range_end_ts=range_end_ts,
        bar_count=0,
    )

    # Check the last bar (most recent) for breakout
    if not bars:
        return None
    last_bar = bars[-1]
    current_ts = datetime.now()
    sig = checker.check(symbol, last_bar, canonical_range, current_ts=current_ts)
    if sig is None:
        return None

    # Convert canonical ORBSignal to legacy ORBSignal format
    return ORBSignal(
        symbol=sig.symbol,
        side=sig.side,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        timestamp=sig.signal_ts.isoformat() if hasattr(sig.signal_ts, 'isoformat') else str(sig.signal_ts),
        option_type=sig.option_type,
    )


# ============================================================
# Alpaca Options Execution
# ============================================================

def _alpaca_headers() -> dict:
    """Build headers for Alpaca API calls."""
    api_key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY", "")
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret,
    }


def _alpaca_paper_url() -> str:
    """Alpaca paper trading base URL."""
    return "https://paper-api.alpaca.markets/v2"


def find_option_contract(symbol: str, signal: ORBSignal, config: dict) -> Optional[dict]:
    """Find an option contract for the ORB signal via AlpacaOptionsProvider.

    Uses canonical select_strike from orb_strategy.py for strike selection.
    Falls back to legacy +offset behavior if strategy_mode is legacy.
    """
    try:
        from alpaca_options_provider import AlpacaOptionsProvider, build_occ_symbol
        provider = AlpacaOptionsProvider()
        if not provider.available:
            logger.error("Alpaca options provider not available — check API keys")
            return None

        spot = signal.entry_price
        # Use canonical strike selection
        strategy_mode = config.get("strategy_mode", "symmetric_otm")
        if strategy_mode == "symmetric_otm":
            cfg = ORBStrategyConfig(strategy_mode=StrategyMode.SYMMETRIC_OTM,
                                    strike_offset=config.get("strike_offset", 1))
        else:
            cfg = ORBStrategyConfig.legacy()
            cfg = ORBStrategyConfig(
                strategy_mode=StrategyMode.LEGACY_PLUS_STRIKE,
                strike_offset=config.get("strike_offset", 1),
            )
        target_strike = canonical_select_strike(spot, signal.option_type, symbol, cfg)

        # Find expiration within DTE range
        today = now_et().date()
        min_exp = (today + timedelta(days=config.get("dte_min", 2))).isoformat()
        max_exp = (today + timedelta(days=config.get("dte_max", 14))).isoformat()
        expirations = provider.get_expirations(symbol, min_date=min_exp, max_date=max_exp)
        if not expirations:
            logger.warning(f"No expirations found for {symbol} in DTE range {config.get('dte_min')}-{config.get('dte_max')}")
            return None
        expiry = expirations[0]  # nearest expiration

        occ_symbol = build_occ_symbol(symbol, expiry, signal.option_type, target_strike)
        return {
            "occ_symbol": occ_symbol,
            "underlying": symbol,
            "option_type": signal.option_type,
            "strike": target_strike,
            "expiration": expiry,
        }
    except Exception as e:
        logger.error(f"Failed to find option contract for {symbol}: {e}")
        return None


# ── Execution service singleton ─────────────────────────────────────────
_execution_service = None


def _get_execution_service():
    """Lazy-init the centralized ORB execution service (paper-only)."""
    global _execution_service
    if _execution_service is None:
        try:
            import sys as _sys
            _server_dir = os.path.join(os.path.dirname(_AGENTS_DIR), "service", "server")
            if _server_dir not in _sys.path:
                _sys.path.insert(0, _server_dir)
            from orb_execution_service import ORBExecutionService
            _execution_service = ORBExecutionService()
        except Exception as e:
            logger.warning(f"Could not init ORBExecutionService, falling back to raw API: {e}")
            _execution_service = False  # sentinel: fallback to legacy
    return _execution_service


def place_option_order(contract: dict, qty: int) -> Optional[dict]:
    """Place a market buy order for an option contract via centralized execution service."""
    svc = _get_execution_service()
    if svc:
        session_date = et_date_str()
        result = svc.execute_entry(
            symbol=contract["underlying"],
            occ_symbol=contract["occ_symbol"],
            qty=qty,
            session_date=session_date,
        )
        if result.is_filled:
            logger.info(f"OPTION ORDER {contract['occ_symbol']} — qty={qty} — filled={result.filled_qty}@{result.filled_price}")
            return {"id": result.alpaca_order_id, "status": "filled",
                    "filled_qty": str(result.filled_qty), "filled_avg_price": str(result.filled_price)}
        elif result.status == "pending":
            logger.info(f"OPTION ORDER {contract['occ_symbol']} — qty={qty} — pending (order_id={result.alpaca_order_id})")
            return {"id": result.alpaca_order_id, "status": "pending"}
        else:
            logger.error(f"Option order failed: {result.status} — {result.error}")
            return None
    # Fallback: legacy raw urllib path
    return _place_option_order_legacy(contract, qty)


def _place_option_order_legacy(contract: dict, qty: int) -> Optional[dict]:
    """Legacy raw urllib option order (fallback when service unavailable)."""
    try:
        url = f"{_alpaca_paper_url()}/orders"
        body = {
            "symbol": contract["occ_symbol"],
            "qty": str(qty),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            **_alpaca_headers(),
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            logger.info(f"OPTION ORDER {contract['occ_symbol']} — qty={qty} — order_id={result.get('id')}")
            return result
    except urllib.error.HTTPError as e:
        logger.error(f"Option order failed: {e.code} {e.reason} — {e.read()[:200]}")
        return None
    except Exception as e:
        logger.error(f"Option order failed: {e}")
        return None


def close_option_order(contract_symbol: str, qty: int) -> Optional[dict]:
    """Place a market sell order to close an option position via centralized execution service."""
    svc = _get_execution_service()
    if svc:
        session_date = et_date_str()
        result = svc.execute_exit(
            symbol=contract_symbol,
            occ_symbol=contract_symbol,
            qty=qty,
            session_date=session_date,
        )
        if result.is_filled:
            logger.info(f"OPTION CLOSE {contract_symbol} — qty={qty} — filled={result.filled_qty}@{result.filled_price}")
            return {"id": result.alpaca_order_id, "status": "filled",
                    "filled_qty": str(result.filled_qty), "filled_avg_price": str(result.filled_price)}
        elif result.status == "pending":
            logger.info(f"OPTION CLOSE {contract_symbol} — qty={qty} — pending")
            return {"id": result.alpaca_order_id, "status": "pending"}
        else:
            logger.error(f"Option close failed: {result.status} — {result.error}")
            return None
    # Fallback: legacy raw urllib path
    return _close_option_order_legacy(contract_symbol, qty)


def _close_option_order_legacy(contract_symbol: str, qty: int) -> Optional[dict]:
    """Legacy raw urllib option close (fallback when service unavailable)."""
    try:
        url = f"{_alpaca_paper_url()}/orders"
        body = {
            "symbol": contract_symbol,
            "qty": str(qty),
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST", headers={
            **_alpaca_headers(),
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            logger.info(f"OPTION CLOSE {contract_symbol} — qty={qty} — order_id={result.get('id')}")
            return result
    except urllib.error.HTTPError as e:
        logger.error(f"Option close failed: {e.code} {e.reason} — {e.read()[:200]}")
        return None
    except Exception as e:
        logger.error(f"Option close failed: {e}")
        return None


def get_alpaca_positions() -> list[dict]:
    """Fetch open option positions from Alpaca paper API."""
    try:
        url = f"{_alpaca_paper_url()}/positions"
        req = urllib.request.Request(url, headers=_alpaca_headers())
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"Could not fetch Alpaca positions: {e}")
        return []


# ============================================================
# Position Management
# ============================================================

def _entry_sizing(config: dict, equity: float, state: dict, today: str) -> dict:
    dynamic = config.get("dynamic_sizing", False)
    risk = state.get("risk_state", {})
    day_start = float(risk.get("day_start_equity") or equity)
    sizing = state.setdefault("sizing_state", {})
    record = sizing.setdefault(today, {"day_start_equity": day_start, "deployed": 0.0})
    deployed = float(record.get("deployed", 0.0))
    base_pct = float(config.get("position_pct", 3.0))
    max_pct = float(config.get("max_position_pct", base_pct))
    total_pct = float(config.get("max_total_pct", base_pct))
    total_budget = day_start * total_pct / 100.0
    cap = day_start * max_pct / 100.0
    allocated = min(cap, max(0.0, total_budget - deployed)) if dynamic else equity * base_pct / 100.0
    return {
        "mode": "dynamic" if dynamic else "fixed",
        "base_position_pct": base_pct,
        "max_position_pct": max_pct,
        "max_total_pct": total_pct,
        "day_start_equity": day_start,
        "day_deployed_before": deployed,
        "remaining_budget_before": max(0.0, total_budget - deployed),
        "allocated_budget": allocated,
        "trade_number": len(state.get("shadow_signals", {}).get(today, [])) + 1,
    }


def _reserve_entry_budget(state: dict, today: str, amount: float) -> None:
    record = state.setdefault("sizing_state", {}).setdefault(today, {"deployed": 0.0})
    record["deployed"] = round(float(record.get("deployed", 0.0)) + amount, 2)


def execute_entry(token: str, signal: ORBSignal, config: dict, equity: float,
                  state: dict) -> bool:
    """Execute an ORB options entry: find contract, place order, record position."""
    symbol = signal.symbol

    if not config.get("paper_only", True):
        logger.error("ORBRunner paper-only gate rejected non-paper execution")
        narrative.emit("risk", "decision", "halted", priority="critical",
                        message="Paper-only policy rejected execution.")
        return False

    # Phase 10: Shadow mode — log signal but don't execute
    if config.get("shadow_mode", False):
        today = et_date_str()
        sizing = _entry_sizing(config, equity, state, today)
        sizing["allocation_pct"] = round(
            sizing["allocated_budget"] / sizing["day_start_equity"] * 100, 4
        ) if sizing["day_start_equity"] else 0.0
        sizing["budget_available"] = sizing["allocated_budget"] > 0
        shadow = state.get("shadow_signals", {})
        shadow.setdefault(today, []).append({
            "symbol": symbol,
            "side": signal.side,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "target_price": signal.target_price,
            "option_type": signal.option_type,
            "timestamp": signal.timestamp,
            "equity": equity,
            "sizing": sizing,
        })
        if sizing["budget_available"] and config.get("dynamic_sizing", False):
            _reserve_entry_budget(state, today, sizing["allocated_budget"])
        state["shadow_signals"] = shadow
        logger.info(f"SHADOW MODE: would enter {symbol} {signal.side} "
                    f"entry={signal.entry_price} stop={signal.stop_price} "
                    f"target={signal.target_price} "
                    f"budget=${sizing['allocated_budget']:.2f}")
        narrative.emit("shadow", "signal", "logged", priority="action", facts={
            "symbol": symbol, "side": signal.side,
            "entry": signal.entry_price, "stop": signal.stop_price,
            "target": signal.target_price,
        })
        return False  # don't update state as a real position

    # Find option contract
    contract = find_option_contract(symbol, signal, config)
    if contract is None:
        post_activity(token, f"ENTRY FAILED {symbol} — no option contract found", symbol=symbol)
        return False

    # Position sizing: reserve cumulative daily allocation.
    today = et_date_str()
    sizing = _entry_sizing(config, equity, state, today)
    budget = sizing["allocated_budget"]
    if budget <= 0:
        logger.info(f"Skipping {symbol}: dynamic sizing budget exhausted")
        return False

    # Phase 4: Improved option price estimation
    # Use a simple OTM premium estimate based on underlying price and moneyness
    spot = signal.entry_price
    strike_step = STRIKE_STEPS.get(symbol, 2.5)
    atm = round(spot / strike_step) * strike_step
    otm_distance = abs(atm - spot) / spot  # how far OTM we are
    # Rough premium: ~2% of underlying for near-ATM, scaling down for further OTM
    est_option_price = max(0.50, spot * 0.02 * (1.0 - otm_distance * 0.5))
    min_option_price = config.get("min_option_entry_price", 0.20)
    if est_option_price < min_option_price:
        logger.info(f"Skipping {symbol}: estimated option premium ${est_option_price:.2f} below minimum ${min_option_price:.2f}")
        return False
    qty = max(1, int(budget / (est_option_price * 100)))
    if qty < 1:
        logger.warning(f"Insufficient budget for {symbol} option: budget=${budget:.2f}")
        return False

    # Place the order
    result = place_option_order(contract, qty)
    if result is None:
        return False
    if config.get("dynamic_sizing", False):
        _reserve_entry_budget(state, today, sizing["allocated_budget"])

    # Record position in state
    state["open_positions"][symbol] = {
        "occ_symbol": contract["occ_symbol"],
        "option_type": contract["option_type"],
        "strike": contract["strike"],
        "expiration": contract["expiration"],
        "side": signal.side,
        "entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "qty": qty,
        "entry_ts": signal.timestamp,
        "bars_held": 0,
        "order_id": result.get("id"),
    }
    # Record order lifecycle
    record_order_lifecycle(state, symbol, "orb:entry", result.get("id"), "entered")

    state["signals_posted"][symbol] = et_date_str()

    narrative.emit("entry", "entry", "complete", priority="trade", facts={
        "symbol": symbol, "side": signal.side, "option_type": signal.option_type,
        "strike": contract["strike"], "expiration": contract["expiration"],
        "qty": qty, "entry_underlying": signal.entry_price,
        "stop": signal.stop_price, "target": signal.target_price,
        "occ_symbol": contract["occ_symbol"],
    })
    post_activity(token,
                  f"ORB ENTRY {symbol} ({signal.side}) — {contract['option_type']} "
                  f"strike={contract['strike']} exp={contract['expiration']} "
                  f"qty={qty} — underlying={signal.entry_price:.2f} "
                  f"SL={signal.stop_price:.2f} TP={signal.target_price:.2f}",
                  symbol=symbol)
    return True


def execute_exit(token: str, symbol: str, position: dict, reason: str,
                 state: dict) -> bool:
    """Close an option position and update state."""
    occ_symbol = position["occ_symbol"]
    qty = position["qty"]
    result = close_option_order(occ_symbol, qty)
    if result is None:
        return False

    # Update loss tracking
    if reason in ("stop_loss", "force_close"):
        streaks = state.get("day_loss_streaks", {})
        streaks[symbol] = streaks.get(symbol, 0) + 1
        state["day_loss_streaks"] = streaks
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    elif reason == "take_profit":
        state["day_loss_streaks"][symbol] = 0
        state["consecutive_losses"] = 0

    record_order_lifecycle(state, symbol, "orb:exit", result.get("id"), "exited")

    del state["open_positions"][symbol]

    narrative.emit("exit", "exit", "complete", priority="trade", facts={
        "symbol": symbol, "reason": reason, "occ_symbol": occ_symbol, "qty": qty,
    })
    post_activity(token, f"ORB EXIT {symbol} — {reason} — {occ_symbol} qty={qty}", symbol=symbol)
    return True


def _elapsed_minutes(entry_ts: str, current_ts: Any) -> float:
    """Return elapsed minutes between stored and market-bar timestamps."""
    try:
        entry = datetime.fromisoformat(str(entry_ts).replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(current_ts).replace("Z", "+00:00"))
        if entry.tzinfo is not None:
            entry = entry.astimezone(ET).replace(tzinfo=None)
        if current.tzinfo is not None:
            current = current.astimezone(ET).replace(tzinfo=None)
        return max(0.0, (current - entry).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return 0.0


def check_exits(token: str, state: dict, config: dict) -> dict:
    """Check all open positions for stop/target/EOD exits."""
    positions = state.get("open_positions", {})
    if not positions:
        return state

    confirmation_minutes = config.get("confirmation_minutes", 10)

    for symbol in list(positions.keys()):
        pos = positions[symbol]

        # Fetch current underlying price
        bars = fetch_1m_bars(symbol)
        if not bars:
            continue
        latest_bar = bars[-1]
        current_price = float(latest_bar["Close"])
        current_high = float(latest_bar["High"])
        current_low = float(latest_bar["Low"])

        exit_reason = None
        elapsed = _elapsed_minutes(pos.get("entry_ts"), latest_bar.get("Timestamp"))
        in_confirmation = elapsed < confirmation_minutes

        # Use canonical check_exit from orb_strategy.py
        intrabar_policy_str = config.get("intrabar_policy", "legacy")
        intrabar_policy = (IntrabarPolicy.CONSERVATIVE
                           if intrabar_policy_str == "conservative"
                           else IntrabarPolicy.LEGACY)
        exit_reason = canonical_check_exit(
            side=pos["side"],
            current_high=current_high,
            current_low=current_low,
            stop_price=pos["stop_price"],
            target_price=pos["target_price"],
            in_confirmation=in_confirmation,
            intrabar_policy=intrabar_policy,
        )

        if exit_reason is None and is_force_exit_time():
            exit_reason = "eod_close"

        if exit_reason:
            execute_exit(token, symbol, pos, exit_reason, state)

    return state


def force_exit_all(token: str, state: dict) -> dict:
    """Force-close all open positions at 15:55 ET."""
    positions = state.get("open_positions", {})
    closed = 0
    for symbol in list(positions.keys()):
        pos = positions[symbol]
        success = execute_exit(token, symbol, pos, "force_exit_15:55", state)
        if success:
            closed += 1
    if closed > 0:
        post_activity(token, f"Force-exited {closed} option position(s) at 15:55 ET")
    state["last_force_exit_date"] = et_date_str()
    return state


# ============================================================
# ORB Signal Scanning
# ============================================================

def run_orb_signals(token: str, symbols: list[str], config: dict,
                    state: dict, equity: float) -> dict:
    """Scan symbols for ORB breakout signals and enter options on signals.

    Returns updated state. Enforces max_positions and circuit breaker.
    """
    today = et_date_str()
    signals_posted = state.get("signals_posted", {})
    day_loss_streaks = state.get("day_loss_streaks", {})
    open_positions = state.get("open_positions", {})
    max_positions = config.get("max_positions", 3)
    circuit_breaker = config.get("circuit_breaker", 3)

    # Reset day loss streaks on new day
    if any(d != today for d in signals_posted.values()):
        state["day_loss_streaks"] = {}
        day_loss_streaks = {}

    if len(open_positions) >= max_positions:
        logger.info(f"Max positions ({max_positions}) reached — skipping scan")
        return state

    narrative.emit("scan", "phase", "started", priority="action", facts={
        "symbols": symbols, "window": "09:30-10:00",
        "open_positions": len(open_positions), "max_positions": max_positions,
    })

    placed = 0

    # Pre-fetch all bars in parallel to minimize latency
    from concurrent.futures import ThreadPoolExecutor, as_completed
    bars_cache: dict[str, Optional[list[dict]]] = {}
    symbols_to_fetch = [
        s for s in symbols
        if signals_posted.get(s) != today
        and not (circuit_breaker > 0 and day_loss_streaks.get(s, 0) >= circuit_breaker)
    ]
    if symbols_to_fetch:
        with ThreadPoolExecutor(max_workers=min(8, len(symbols_to_fetch))) as pool:
            futures = {pool.submit(fetch_1m_bars, s): s for s in symbols_to_fetch}
            for fut in as_completed(futures, timeout=30):
                sym = futures[fut]
                try:
                    bars_cache[sym] = fut.result()
                except Exception as e:
                    logger.warning(f"Bar fetch failed for {sym}: {e}")
                    bars_cache[sym] = None

    for symbol in symbols:
        if len(open_positions) + placed >= max_positions:
            break

        # Skip if already signaled today
        if signals_posted.get(symbol) == today:
            continue

        # Circuit breaker check
        if circuit_breaker > 0 and day_loss_streaks.get(symbol, 0) >= circuit_breaker:
            logger.info(f"Circuit breaker active for {symbol} — skipping")
            continue

        # Use pre-fetched bars
        bars = bars_cache.get(symbol)
        if not bars:
            continue

        orb_range = build_opening_range(symbol, bars, config)
        if orb_range is None:
            continue

        # Check for breakout
        signal = check_breakout(symbol, bars, orb_range, config)
        if signal is None:
            continue

        # Execute entry
        success = execute_entry(token, signal, config, equity, state)
        if success:
            placed += 1
            open_positions = state.get("open_positions", {})

    narrative.emit("scan", "phase", "complete", priority="action", facts={
        "signals_placed": placed, "symbols_checked": len(symbols),
        "open_positions": len(open_positions),
    })
    if placed == 0:
        post_activity(token, f"ORB scan complete — 0 signals from {len(symbols)} symbols")
    return state


def update_risk_state(state: dict, equity: float, today: str, config: dict) -> dict:
    """Track equity risk limits without changing strategy signals."""
    risk = state.setdefault("risk_state", {})
    if risk.get("date") != today:
        risk["date"] = today
        risk["day_start_equity"] = equity
        risk["daily_halt_reason"] = None
    risk["peak_equity"] = max(float(risk.get("peak_equity", equity)), equity)
    day_start = max(float(risk.get("day_start_equity", equity)), 0.01)
    peak = max(float(risk.get("peak_equity", equity)), 0.01)
    daily_loss_pct = max(0.0, (day_start - equity) / day_start * 100)
    drawdown_pct = max(0.0, (peak - equity) / peak * 100)
    risk["daily_loss_pct"] = round(daily_loss_pct, 4)
    risk["drawdown_pct"] = round(drawdown_pct, 4)
    daily_limit = config.get("daily_loss_limit_pct", 10.0)
    drawdown_limit = config.get("max_drawdown_limit_pct", 30.0)
    if daily_loss_pct >= daily_limit:
        risk["daily_halt_reason"] = f"daily_loss_limit_{daily_limit:g}%"
    if drawdown_pct >= drawdown_limit:
        risk["rolling_halt_reason"] = f"drawdown_limit_{drawdown_limit:g}%"
    return state


# ============================================================
# Main Cycle
# ============================================================

def run_cycle(token: str, state: dict, config: dict, symbols: list[str]) -> dict:
    """Execute one ORBRunner trading cycle. Returns updated state."""

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
        equity += qty * price

    state = update_risk_state(state, equity, et_date_str(), config)
    risk = state.get("risk_state", {})
    narrative.emit("portfolio", "phase", "measured", priority="action", facts={
        "cash": round(cash, 2), "equity": round(equity, 2),
        "open_positions": len(state.get("open_positions", {})),
        "daily_loss_pct": risk.get("daily_loss_pct", 0),
        "drawdown_pct": risk.get("drawdown_pct", 0),
    })

    # 3. Dynamic symbol discovery (once per day, before ORB window)
    # Run BEFORE market hours check — discovery should happen at 09:20, pre-open
    today = et_date_str()
    discovery_mode = config.get("discovery_mode", "fixed")
    discovered = state.get("discovered_symbols", {})
    discovery_meta = state.get("discovery_meta", {})
    if discovery_mode == "dynamic" and today not in discovered:
        if et_time_str() >= "09:20" and et_time_str() < config.get("min_entry_time", "09:30"):
            movers = discover_movers(config)
            discovered[today] = movers
            discovery_meta[today] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "et_time": et_time_str(),
                "late": False,
                "count": len(movers),
            }
            state["discovered_symbols"] = discovered
            state["discovery_meta"] = discovery_meta
            symbols = movers
            narrative.emit("discovery", "phase", "complete", priority="action", facts={
                "mode": "dynamic", "symbols": movers, "count": len(movers),
            })
            post_activity(token, f"Discovery: {len(movers)} movers selected — {', '.join(movers)}")
        elif et_time_str() >= config.get("min_entry_time", "09:30") and today not in discovered:
            # ORB window already started without discovery — do it now
            # Phase 6: Flag as late discovery (lookahead risk)
            movers = discover_movers(config)
            discovered[today] = movers
            discovery_meta[today] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "et_time": et_time_str(),
                "late": True,
                "count": len(movers),
                "warning": "Discovery after 09:30 — may include opening range data (lookahead risk)",
            }
            state["discovered_symbols"] = discovered
            state["discovery_meta"] = discovery_meta
            symbols = movers
            narrative.emit("discovery", "phase", "late", priority="action", facts={
                "mode": "dynamic", "symbols": movers, "count": len(movers),
                "late": True,
            })
            post_activity(token, f"Late discovery: {len(movers)} movers — {', '.join(movers)} (LOOKAHEAD RISK)")
    elif discovery_mode == "dynamic" and today in discovered:
        symbols = discovered[today]

    # 4. Check if we're in market hours (gates trading, not discovery)
    if not is_market_hours():
        logger.info("Outside market hours — skipping cycle")
        return state

    # Phase 3: Daily reconciliation with Alpaca (before trading)
    if state.get("last_reconcile_date") != today:
        try:
            state = reconcile_state_with_alpaca(state)
        except Exception as e:
            logger.warning(f"Daily reconciliation failed: {e}")

    # 5. Force exit at 15:55 ET
    if is_force_exit_time() and state.get("last_force_exit_date") != today:
        if state.get("open_positions"):
            state = force_exit_all(token, state)
        else:
            state["last_force_exit_date"] = today
        return state

    # 5. Check exits on open positions
    state = check_exits(token, state, config)

    # 7. During ORB window: generate signals and enter
    risk = state.get("risk_state", {})
    risk_halt = risk.get("daily_halt_reason") or risk.get("rolling_halt_reason")
    if risk_halt:
        narrative.emit("risk", "decision", "halted", priority="critical", facts={
            "reason": risk_halt,
            "daily_loss_pct": risk.get("daily_loss_pct", 0),
            "drawdown_pct": risk.get("drawdown_pct", 0),
        })
        logger.warning(f"Risk guardrail active — skipping new entries: {risk_halt}")
        return state

    if not can_trade or goal_achieved:
        logger.info(f"Cycle skip: can_trade={can_trade} goal_achieved={goal_achieved}")
        return state

    if is_orb_window(config):
        state = run_orb_signals(token, symbols, config, state, equity)
    else:
        logger.info(f"Outside ORB window (current={et_time_str()}) — monitoring only")

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
    config = dict(ORB_CONFIG)
    symbols = list(DEFAULT_SYMBOLS)

    # Phase 3: Reconcile state with Alpaca on startup
    try:
        state = reconcile_state_with_alpaca(state)
        save_state(state)
    except Exception as e:
        logger.warning(f"Startup reconciliation failed: {e}")

    logger.info(f"State loaded: cycles_run={state.get('cycles_run', 0)} "
                f"open_positions={len(state.get('open_positions', {}))}")
    logger.info(f"Discovery: {config.get('discovery_mode', 'fixed')} | "
                f"Symbols: {symbols} | Poll: {poll_interval}s | "
                f"Range: {config['range_minutes']}min | "
                f"Stop: {config['stop_pct']}% | Target: {config['target_pct']}%")

    cycle = 0
    live_poll = poll_interval
    while not stop_event.is_set():
        cycle += 1
        cycle_start = time.time()
        narrative.begin_cycle(cycle)
        narrative.emit("cycle", "phase", facts={"cycle": cycle}, priority="action")

        try:
            # Fetch live config for poll interval
            live_config = fetch_config(token)
            if live_config.get("_unavailable"):
                raise RuntimeError("config service unavailable")
            live_poll = live_config.get("poll_interval", poll_interval)

            # Apply only explicitly allowed platform runtime settings.
            symbols = apply_platform_runtime_config(live_config, symbols)

            # Run the cycle
            state = run_cycle(token, state, config, symbols)

            # Send heartbeat
            send_heartbeat(token)

            # Update state
            state["cycles_run"] = state.get("cycles_run", 0) + 1
            state["last_cycle_time"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            cycle_time = time.time() - cycle_start
            logger.info(f"Cycle {cycle} done in {cycle_time:.1f}s — "
                        f"open_positions={len(state.get('open_positions', {}))}")
            narrative.recap({
                "duration_seconds": round(cycle_time, 2),
                "open_positions": len(state.get("open_positions", {})),
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

    logger.info(f"ORBRunner stopped after {cycle} cycles.")


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="ORBRunner — Opening Range Breakout Options Agent")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL,
                        help="Poll interval in seconds (default: 30)")
    parser.add_argument("--cycles", type=int, default=0,
                        help="Max cycles (0 = infinite)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  ORBRunner — Opening Range Breakout Options Agent")
    print(f"{'='*60}")
    print(f"  API: {API_BASE}")
    print(f"  Poll interval: {args.interval}s")
    print(f"  State file: {STATE_FILE}")
    print(f"  Symbols: {', '.join(DEFAULT_SYMBOLS)}")
    print(f"  Strategy: 5min ORB → OTM+1 options via Alpaca paper")
    print(f"{'='*60}\n")

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print(f"\nStopping ORBRunner...")
        stop_event.set()

    import signal as _signal
    _signal.signal(_signal.SIGINT, signal_handler)
    _signal.signal(_signal.SIGTERM, signal_handler)

    run_loop(stop_event, args.interval)


if __name__ == "__main__":
    main()
