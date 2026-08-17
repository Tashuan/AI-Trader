#!/usr/bin/env python3
"""
ORBRunner — Opening Range Breakout Options Agent

Executes the ORB Options strategy with zero LLM judgment:
  1. 09:30-09:35 ET: fetch 1m bars, build opening range for each symbol
  2. 09:35-10:30 ET: watch for breakout closes, enter options on signal
  3. Throughout the day: monitor underlying for stop/target, exit options
  4. 15:55 ET: force-close any remaining option positions

Uses the winning parameters from the validated ORB Options backtest:
  - 5min opening range, 1.0% stop / 1.5% target on underlying
  - OTM+1 strike call (long) / put (short) via Alpaca options API
  - 10% position sizing (option premium), max 3 concurrent positions
  - 10min confirmation period (no stop checks right after entry)
  - 3-loss circuit breaker per symbol per day
  - DTE 2-14 days, 10 bps option slippage

Backtest results (2026-04-01 → 2026-08-16):
  - +147% return, PF 1.259, 45% win rate, 354 trades
  - IV sensitivity: PASS (profitable 25%-75% IV)
  - Walk-forward: PASS (3/3 OOS windows, +68% compounded)
  - Bear market: MIXED (profitable both regimes, not regime-specific)

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
    "range_minutes": 5,
    "stop_pct": 1.0,
    "target_pct": 1.5,
    "latest_entry": "10:30",
    "max_positions": 3,
    "position_pct": 10.0,
    "strike_offset": 1,
    "dte_min": 2,
    "dte_max": 14,
    "option_slippage_bps": 10.0,
    "confirmation_minutes": 10,
    "circuit_breaker": 3,
    "risk_free_rate": 0.05,
    "min_entry_time": "09:30",
    # Discovery config — set discovery_mode to "dynamic" to use movers
    "discovery_mode": "dynamic",     # "fixed" or "dynamic"
    "discovery_max_symbols": 8,       # max symbols to trade after discovery
    "discovery_min_change_pct": 1.0,  # min abs daily change % to qualify
    "discovery_universe": SCANNER_UNIVERSE,
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
    """Check if current ET time is within the ORB signal window (09:30-10:30)."""
    min_entry = config.get("min_entry_time", "09:30")
    latest = config.get("latest_entry", "10:30")
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
    """
    max_symbols = config.get("discovery_max_symbols", 8)
    min_change = config.get("discovery_min_change_pct", 1.0)
    universe = config.get("discovery_universe", SCANNER_UNIVERSE)
    candidates: dict[str, dict] = {}

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
        frame = provider.history(symbol, period="5d", interval="1m")
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
    """Build the opening range from the first N minutes of bars."""
    range_minutes = config.get("range_minutes", 5)
    range_end = f"09:{30 + range_minutes}"
    range_bars = [b for b in bars if str(b["Timestamp"].time())[:5] <= range_end]
    if not range_bars:
        return None
    highs = [float(b["High"]) for b in range_bars]
    lows = [float(b["Low"]) for b in range_bars]
    return ORBRange(
        symbol=symbol,
        range_high=max(highs),
        range_low=min(lows),
        range_end_time=range_end,
    )


def check_breakout(symbol: str, bars: list[dict], orb_range: ORBRange,
                   config: dict) -> Optional[ORBSignal]:
    """Check if any bar after the range has a close outside the range."""
    range_end = orb_range.range_end_time
    stop_pct = config.get("stop_pct", 1.0)
    target_pct = config.get("target_pct", 1.5)

    for bar in bars:
        bar_time = str(bar["Timestamp"].time())[:5]
        if bar_time <= range_end:
            continue
        if bar_time > config.get("latest_entry", "10:30"):
            break
        close = float(bar["Close"])
        if close > orb_range.range_high:
            side = "long"
            option_type = "call"
            stop_price = close * (1 - stop_pct / 100)
            target_price = close * (1 + target_pct / 100)
            return ORBSignal(
                symbol=symbol, side=side, entry_price=close,
                stop_price=stop_price, target_price=target_price,
                timestamp=str(bar["Timestamp"]),
                option_type=option_type,
            )
        elif close < orb_range.range_low:
            side = "short"
            option_type = "put"
            stop_price = close * (1 + stop_pct / 100)
            target_price = close * (1 - target_pct / 100)
            return ORBSignal(
                symbol=symbol, side=side, entry_price=close,
                stop_price=stop_price, target_price=target_price,
                timestamp=str(bar["Timestamp"]),
                option_type=option_type,
            )
    return None


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
    """Find an option contract for the ORB signal via AlpacaOptionsProvider."""
    try:
        from alpaca_options_provider import AlpacaOptionsProvider, build_occ_symbol
        provider = AlpacaOptionsProvider()
        if not provider.available:
            logger.error("Alpaca options provider not available — check API keys")
            return None

        spot = signal.entry_price
        strike_step = STRIKE_STEPS.get(symbol, 2.5)
        atm_strike = round(spot / strike_step) * strike_step
        target_strike = atm_strike + config.get("strike_offset", 1) * strike_step

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


def place_option_order(contract: dict, qty: int) -> Optional[dict]:
    """Place a market buy order for an option contract via Alpaca paper API."""
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
    """Place a market sell order to close an option position via Alpaca paper API."""
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

def execute_entry(token: str, signal: ORBSignal, config: dict, equity: float,
                  state: dict) -> bool:
    """Execute an ORB options entry: find contract, place order, record position."""
    symbol = signal.symbol

    # Find option contract
    contract = find_option_contract(symbol, signal, config)
    if contract is None:
        post_activity(token, f"ENTRY FAILED {symbol} — no option contract found", symbol=symbol)
        return False

    # Position sizing: position_pct of equity for option premium
    position_pct = config.get("position_pct", 10.0)
    budget = equity * position_pct / 100.0

    # Estimate option price (we'll use a rough OTM price estimate)
    # In live trading, we'd fetch a quote; for paper, market order handles it
    # Assume ~$2-5 per contract for OTM short-dated options
    est_option_price = 3.0  # conservative estimate
    qty = max(1, int(budget / (est_option_price * 100)))
    if qty < 1:
        logger.warning(f"Insufficient budget for {symbol} option: budget=${budget:.2f}")
        return False

    # Place the order
    result = place_option_order(contract, qty)
    if result is None:
        return False

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

    del state["open_positions"][symbol]

    narrative.emit("exit", "exit", "complete", priority="trade", facts={
        "symbol": symbol, "reason": reason, "occ_symbol": occ_symbol, "qty": qty,
    })
    post_activity(token, f"ORB EXIT {symbol} — {reason} — {occ_symbol} qty={qty}", symbol=symbol)
    return True


def check_exits(token: str, state: dict, config: dict) -> dict:
    """Check all open positions for stop/target/EOD exits."""
    positions = state.get("open_positions", {})
    if not positions:
        return state

    confirmation_minutes = config.get("confirmation_minutes", 10)

    for symbol in list(positions.keys()):
        pos = positions[symbol]
        pos["bars_held"] = pos.get("bars_held", 0) + 1

        # Fetch current underlying price
        bars = fetch_1m_bars(symbol)
        if not bars:
            continue
        latest_bar = bars[-1]
        current_price = float(latest_bar["Close"])
        current_high = float(latest_bar["High"])
        current_low = float(latest_bar["Low"])

        exit_reason = None
        in_confirmation = pos["bars_held"] < confirmation_minutes

        if pos["side"] == "long":
            if current_high >= pos["target_price"]:
                exit_reason = "take_profit"
            elif not in_confirmation and current_low <= pos["stop_price"]:
                exit_reason = "stop_loss"
        else:
            if current_low <= pos["target_price"]:
                exit_reason = "take_profit"
            elif not in_confirmation and current_high >= pos["stop_price"]:
                exit_reason = "stop_loss"

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
        "symbols": symbols, "window": "09:30-10:30",
        "open_positions": len(open_positions), "max_positions": max_positions,
    })

    placed = 0
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

        # Fetch bars and build range
        bars = fetch_1m_bars(symbol)
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

    narrative.emit("portfolio", "phase", "measured", priority="action", facts={
        "cash": round(cash, 2), "equity": round(equity, 2),
        "open_positions": len(state.get("open_positions", {})),
    })

    # 3. Check if we're in market hours
    if not is_market_hours():
        logger.info("Outside market hours — skipping cycle")
        return state

    # 4. Force exit at 15:55 ET
    today = et_date_str()
    if is_force_exit_time() and state.get("last_force_exit_date") != today:
        if state.get("open_positions"):
            state = force_exit_all(token, state)
        else:
            state["last_force_exit_date"] = today
        return state

    # 5. Check exits on open positions
    state = check_exits(token, state, config)

    # 6. Dynamic symbol discovery (once per day, before ORB window)
    discovery_mode = config.get("discovery_mode", "fixed")
    discovered = state.get("discovered_symbols", {})
    if discovery_mode == "dynamic" and today not in discovered:
        if et_time_str() >= "09:20" and et_time_str() < config.get("min_entry_time", "09:30"):
            movers = discover_movers(config)
            discovered[today] = movers
            state["discovered_symbols"] = discovered
            symbols = movers
            narrative.emit("discovery", "phase", "complete", priority="action", facts={
                "mode": "dynamic", "symbols": movers, "count": len(movers),
            })
            post_activity(token, f"Discovery: {len(movers)} movers selected — {', '.join(movers)}")
        elif et_time_str() >= config.get("min_entry_time", "09:30") and today not in discovered:
            # ORB window already started without discovery — do it now
            movers = discover_movers(config)
            discovered[today] = movers
            state["discovered_symbols"] = discovered
            symbols = movers
            narrative.emit("discovery", "phase", "late", priority="action", facts={
                "mode": "dynamic", "symbols": movers, "count": len(movers),
            })
            post_activity(token, f"Late discovery: {len(movers)} movers — {', '.join(movers)}")
    elif discovery_mode == "dynamic" and today in discovered:
        symbols = discovered[today]

    # 7. During ORB window: generate signals and enter
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

            # Override symbols from config if present
            if live_config.get("watchlist"):
                symbols = live_config["watchlist"]

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
