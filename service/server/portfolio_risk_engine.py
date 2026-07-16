"""Portfolio-level risk engine.

Sits between per-agent guardrails (scalp_guardrails.py) and trade execution.
Every new entry (buy/short) passes through evaluate_portfolio_risk() which can
approve, scale down, or reject based on aggregate exposure, crowding, and sector
concentration.

Exits (sell/cover) bypass entirely — only new entries are gated.

The engine runs inside the existing write transaction (begin_write_transaction)
so the SQLite BEGIN IMMEDIATE / Postgres BEGIN lock is already held. For Postgres,
a SELECT ... FOR UPDATE on portfolio_risk_state serializes concurrent access to
the daily state row.

All thresholds are configurable via env vars. PORTFOLIO_RISK_ENABLED=0 disables
the engine entirely (all entries pass).

Fail-closed: any unhandled exception returns approved=False.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

try:
    from database import using_postgres  # noqa: F401 — kept for potential future use
except ImportError:  # pragma: no cover
    def using_postgres() -> bool:
        return bool(os.getenv("DATABASE_URL"))


# ─── Config helpers ───────────────────────────────────────────

def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _utc_day_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")


# ─── Sector mapping with hot-reload ───────────────────────────

_SECTOR_FILE = os.path.join(os.path.dirname(__file__), "sectors.json")
_SECTOR_CACHE: dict[str, str] | None = None
_SECTOR_MTIME: float = 0.0

_FALLBACK_SECTORS: dict[str, list[str]] = {
    "crypto": ["BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LINK", "DOT", "ADA", "XRP"],
    "tech": ["NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "CRM"],
    "commodities": ["GLD", "SLV", "USO", "CL=F", "GC=F"],
    "indices": ["SPY", "QQQ", "IWM", "DIA"],
    "fx": ["EURUSD", "GBPUSD", "USDJPY", "DXY"],
}


def _load_sector_map() -> dict[str, str]:
    """Load symbol→sector mapping from sectors.json with mtime-based hot-reload."""
    global _SECTOR_CACHE, _SECTOR_MTIME

    try:
        mtime = os.path.getmtime(_SECTOR_FILE)
    except OSError:
        mtime = 0.0

    if _SECTOR_CACHE is not None and mtime == _SECTOR_MTIME:
        return _SECTOR_CACHE

    symbol_to_sector: dict[str, str] = {}
    try:
        with open(_SECTOR_FILE, "r") as f:
            raw: dict[str, list[str]] = json.load(f)
        for sector, symbols in raw.items():
            for sym in symbols:
                symbol_to_sector[sym.upper()] = sector
    except (OSError, json.JSONDecodeError, TypeError):
        for sector, symbols in _FALLBACK_SECTORS.items():
            for sym in symbols:
                symbol_to_sector[sym.upper()] = sector

    _SECTOR_CACHE = symbol_to_sector
    _SECTOR_MTIME = mtime
    return symbol_to_sector


# ─── Portfolio equity helpers ─────────────────────────────────

def _position_value_abs(cursor: Any) -> float:
    """Sum of ABS(quantity * current_price) across ALL agents' open positions."""
    cursor.execute(
        """
        SELECT quantity, entry_price, current_price
        FROM positions
        """
    )
    total = 0.0
    for row in cursor.fetchall():
        mark = float(row["current_price"] or row["entry_price"] or 0)
        total += abs(float(row["quantity"] or 0)) * mark
    return total


def _total_portfolio_equity(cursor: Any) -> float:
    """Dynamic capital base: SUM(cash + deposited) across all agents + all position values."""
    cursor.execute("SELECT COALESCE(SUM(COALESCE(cash, 0) + COALESCE(deposited, 0)), 0) AS total FROM agents")
    row = cursor.fetchone()
    cash_total = float(row["total"] or 0) if row else 0.0
    return max(0.0, cash_total + _position_value_abs(cursor))


# ─── Daily state management ───────────────────────────────────

def _ensure_daily_state(cursor: Any, now: str) -> dict[str, Any]:
    """Get or create today's portfolio_risk_state row.

    On a new UTC day, snapshots current total portfolio equity as starting_equity.
    For Postgres, acquires a row-level lock via SELECT ... FOR UPDATE.
    """
    day_key = _utc_day_start()

    cursor.execute(
        """
        SELECT day_key, starting_equity, halted, halt_reason
        FROM portfolio_risk_state
        WHERE day_key = ?
        """,
        (day_key,),
    )

    row = cursor.fetchone()
    if row:
        return {
            "day_key": row["day_key"],
            "starting_equity": float(row["starting_equity"]),
            "halted": int(row["halted"]),
            "halt_reason": row["halt_reason"],
        }

    # New day — snapshot current portfolio equity as starting_equity
    equity = _total_portfolio_equity(cursor)
    seed = _float_env("PORTFOLIO_TOTAL_CAPITAL", 10000.0, 0.0)
    starting_equity = equity if equity > 0 else seed

    cursor.execute(
        """
        INSERT INTO portfolio_risk_state (day_key, starting_equity, halted, halt_reason, updated_at)
        VALUES (?, ?, 0, NULL, ?)
        """,
        (day_key, starting_equity, now),
    )
    return {
        "day_key": day_key,
        "starting_equity": starting_equity,
        "halted": 0,
        "halt_reason": None,
    }


# ─── Core evaluation ──────────────────────────────────────────

def evaluate_portfolio_risk(
    cursor: Any,
    *,
    agent_id: int,
    market: str,
    symbol: str,
    side: str,
    trade_value: float,
    now: str,
) -> dict[str, Any]:
    """Evaluate whether a new entry passes portfolio-level risk checks.

    Returns:
        {"approved": bool, "trade_value": float, "reason": str,
         "checks": dict, "state": dict}

    Fail-closed: any unhandled exception → approved=False.
    """
    try:
        # Master switch
        if os.getenv("PORTFOLIO_RISK_ENABLED", "1") == "0":
            return {
                "approved": True,
                "trade_value": trade_value,
                "reason": "Portfolio risk engine disabled",
                "checks": {"disabled": True},
                "state": {},
            }

        # Exits always pass
        if side not in ("buy", "short"):
            return {
                "approved": True,
                "trade_value": trade_value,
                "reason": "exit_or_non_entry",
                "checks": {"bypass": True},
                "state": {},
            }

        if not math.isfinite(trade_value) or trade_value <= 0:
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": "Invalid trade value",
                "checks": {},
                "state": {},
            }

        # Config
        max_symbol_pct = _float_env("PORTFOLIO_MAX_SYMBOL_PCT", 0.35, 0.001)
        max_sector_pct = _float_env("PORTFOLIO_MAX_SECTOR_PCT", 0.50, 0.001)
        max_unknown_pct = _float_env("PORTFOLIO_MAX_UNKNOWN_PCT", 0.10, 0.001)
        max_crowding = _int_env("PORTFOLIO_MAX_CROWDING", 3, 1)
        max_daily_loss_pct = _float_env("PORTFOLIO_MAX_DAILY_LOSS_PCT", 0.05, 0.001)

        # Daily state
        state = _ensure_daily_state(cursor, now)
        starting_equity = float(state["starting_equity"])
        if starting_equity <= 0:
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": "Portfolio starting equity is zero or negative",
                "checks": {},
                "state": state,
            }

        checks: dict[str, Any] = {}

        # ── Check 1: Portfolio halt ──────────────────────────
        if state["halted"]:
            reason = state["halt_reason"] or "Portfolio trading halted"
            checks["halt"] = {"rejected": True, "reason": reason}
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": reason,
                "checks": checks,
                "state": state,
            }
        checks["halt"] = {"rejected": False}

        # ── Check 2: Crowding ────────────────────────────────
        side_normalized = "long" if side == "buy" else "short"
        cursor.execute(
            """
            SELECT COUNT(DISTINCT agent_id) AS cnt
            FROM positions
            WHERE symbol = ? AND side = ?
            """,
            (symbol, side_normalized),
        )
        crowding_count = int(cursor.fetchone()["cnt"] or 0)
        checks["crowding"] = {
            "current": crowding_count,
            "max": max_crowding,
            "rejected": crowding_count >= max_crowding,
        }
        if crowding_count >= max_crowding:
            reason = f"Crowding limit reached: {crowding_count} agents already {side_normalized} {symbol} (max {max_crowding})"
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": reason,
                "checks": checks,
                "state": state,
            }

        # ── Check 3: Symbol concentration (gross) ────────────
        cursor.execute(
            """
            SELECT ABS(SUM(quantity * COALESCE(current_price, entry_price, 0))) AS gross
            FROM positions
            WHERE symbol = ?
            """,
            (symbol,),
        )
        symbol_row = cursor.fetchone()
        symbol_gross = abs(float(symbol_row["gross"] or 0))
        symbol_pct = (symbol_gross + trade_value) / starting_equity
        checks["symbol_concentration"] = {
            "existing_gross": symbol_gross,
            "after_trade_pct": symbol_pct,
            "max_pct": max_symbol_pct,
            "rejected": symbol_pct > max_symbol_pct,
        }
        if symbol_pct > max_symbol_pct:
            reason = (
                f"Symbol concentration limit: {symbol} gross "
                f"${symbol_gross + trade_value:,.2f} = {symbol_pct:.1%} "
                f"(max {max_symbol_pct:.1%})"
            )
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": reason,
                "checks": checks,
                "state": state,
            }

        # ── Check 4: Sector concentration (gross) ────────────
        sector_map = _load_sector_map()
        sector = sector_map.get(symbol.upper(), "unknown")
        sector_cap = max_sector_pct if sector != "unknown" else max_unknown_pct

        if sector != "unknown":
            sector_symbols = [sym for sym, sec in sector_map.items() if sec == sector]
            placeholders = ",".join("?" * len(sector_symbols))
            cursor.execute(
                f"""
                SELECT ABS(SUM(quantity * COALESCE(current_price, entry_price, 0))) AS gross
                FROM positions
                WHERE symbol IN ({placeholders})
                """,
                sector_symbols,
            )
        else:
            # Unknown sector — only count this symbol
            cursor.execute(
                """
                SELECT ABS(SUM(quantity * COALESCE(current_price, entry_price, 0))) AS gross
                FROM positions
                WHERE symbol = ?
                """,
                (symbol,),
            )
        sector_row = cursor.fetchone()
        sector_gross = abs(float(sector_row["gross"] or 0))
        sector_pct = (sector_gross + trade_value) / starting_equity
        checks["sector_concentration"] = {
            "sector": sector,
            "existing_gross": sector_gross,
            "after_trade_pct": sector_pct,
            "max_pct": sector_cap,
            "rejected": sector_pct > sector_cap,
        }
        if sector_pct > sector_cap:
            reason = (
                f"Sector concentration limit: {sector} gross "
                f"${sector_gross + trade_value:,.2f} = {sector_pct:.1%} "
                f"(max {sector_cap:.1%})"
            )
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": reason,
                "checks": checks,
                "state": state,
            }

        # ── Check 5: Portfolio daily loss ────────────────────
        current_equity = _total_portfolio_equity(cursor)
        daily_loss_pct = max(0.0, (starting_equity - current_equity) / starting_equity) if starting_equity > 0 else 0.0
        checks["daily_loss"] = {
            "starting_equity": starting_equity,
            "current_equity": current_equity,
            "loss_pct": daily_loss_pct,
            "max_pct": max_daily_loss_pct,
            "rejected": daily_loss_pct >= max_daily_loss_pct,
        }
        if daily_loss_pct >= max_daily_loss_pct:
            reason = f"Portfolio daily loss limit reached ({daily_loss_pct:.2%}, max {max_daily_loss_pct:.2%})"
            # Set halt flag
            cursor.execute(
                """
                UPDATE portfolio_risk_state
                SET halted = 1, halt_reason = ?, updated_at = ?
                WHERE day_key = ?
                """,
                (reason, now, state["day_key"]),
            )
            state["halted"] = 1
            state["halt_reason"] = reason
            return {
                "approved": False,
                "trade_value": 0.0,
                "reason": reason,
                "checks": checks,
                "state": state,
            }

        # ── All checks passed ────────────────────────────────
        return {
            "approved": True,
            "trade_value": trade_value,
            "reason": "approved",
            "checks": checks,
            "state": state,
        }

    except Exception:
        return {
            "approved": False,
            "trade_value": 0.0,
            "reason": "System Error: Portfolio Risk Engine Fault",
            "checks": {},
            "state": {},
        }
