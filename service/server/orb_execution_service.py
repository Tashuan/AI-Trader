"""Centralized ORB execution service.

All ORBRunner order execution flows through this module.  It wraps
``AlpacaBroker`` with ORB-specific concerns:

- Paper-only enforcement (hard guard, never bypassed)
- Client order ID generation (deterministic, idempotent)
- Fill polling with timeout
- Position state reconciliation
- Execution audit trail via ``alpaca_order_executions`` table

This service is the *only* module that should call ``AlpacaBroker`` for
ORB-related trades.  The live runner calls this service; the runner never
touches the broker directly.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("ORBExecution")

# ── Paper-only guard ──────────────────────────────────────────────────

_PAPER_URL_PREFIX = "https://paper-api.alpaca.markets"
_LIVE_URL_PREFIX = "https://api.alpaca.markets"


def _assert_paper_only(base_url: str) -> None:
    """Hard guard: refuse to execute against a live Alpaca endpoint."""
    if _LIVE_URL_PREFIX in base_url and _PAPER_URL_PREFIX not in base_url:
        raise RuntimeError(
            f"ORB execution service refuses live endpoint: {base_url}. "
            "Set ALPACA_PAPER_TRADING_URL to the paper endpoint."
        )


# ── Client order ID ───────────────────────────────────────────────────

_AGENT_ID = "orb"
_ENTRY_PREFIX = "orb:entry"
_EXIT_PREFIX = "orb:exit"


def make_entry_client_order_id(symbol: str, session_date: str) -> str:
    """Deterministic client order ID for entries (idempotent)."""
    return f"{_ENTRY_PREFIX}:{symbol}:{session_date}"


def make_exit_client_order_id(symbol: str, session_date: str) -> str:
    """Deterministic client order ID for exits (idempotent)."""
    return f"{_EXIT_PREFIX}:{symbol}:{session_date}"


# ── Execution result ──────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    status: str           # "filled", "rejected", "pending", "cancelled", "unknown"
    filled_qty: float
    filled_price: float
    alpaca_order_id: str | None = None
    client_order_id: str = ""
    error: str | None = None
    raw_order: dict | None = None

    @property
    def is_filled(self) -> bool:
        return self.status == "filled"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"filled", "rejected", "cancelled", "expired"}


# ── Execution Service ─────────────────────────────────────────────────

class ORBExecutionService:
    """Centralized paper execution for ORBRunner.

    Wraps AlpacaBroker with paper-only enforcement, deterministic client
    order IDs, and fill polling.
    """

    def __init__(self, broker: Any | None = None):
        """Initialize with an AlpacaBroker instance or lazy-load from env."""
        if broker is not None:
            self._broker = broker
        else:
            from alpaca_broker import AlpacaBroker
            self._broker = AlpacaBroker(managed_enabled=True)

        base_url = getattr(self._broker, "_base_url", "")
        _assert_paper_only(base_url)

        self._poll_timeout = float(
            os.environ.get("ORB_FILL_POLL_TIMEOUT", "15")
        )

    @property
    def broker(self) -> Any:
        return self._broker

    @property
    def enabled(self) -> bool:
        return getattr(self._broker, "enabled", False)

    def execute_entry(
        self,
        *,
        symbol: str,
        occ_symbol: str,
        qty: int,
        session_date: str,
    ) -> ExecutionResult:
        """Execute an ORB option entry (buy to open).

        Args:
            symbol: Underlying ticker (e.g. 'NVDA')
            occ_symbol: OCC option symbol (e.g. 'NVDA250321C00150000')
            qty: Number of contracts
            session_date: Trading session date (YYYY-MM-DD) for idempotency

        Returns:
            ExecutionResult with fill status and details.
        """
        coid = make_entry_client_order_id(symbol, session_date)
        try:
            raw = self._broker.execute_order(
                symbol=occ_symbol,
                quantity=qty,
                action="buy",
                client_order_id=coid,
                order_type="market",
                poll_timeout=self._poll_timeout,
            )
        except Exception as exc:
            logger.error("Entry failed for %s: %s", symbol, exc)
            return ExecutionResult(
                status="unknown", filled_qty=0, filled_price=0,
                client_order_id=coid, error=str(exc),
            )

        return self._result_from_raw(raw, coid)

    def execute_exit(
        self,
        *,
        symbol: str,
        occ_symbol: str,
        qty: int,
        session_date: str,
    ) -> ExecutionResult:
        """Execute an ORB option exit (sell to close).

        Args:
            symbol: Underlying ticker
            occ_symbol: OCC option symbol
            qty: Number of contracts to close
            session_date: Trading session date for idempotency

        Returns:
            ExecutionResult with fill status and details.
        """
        coid = make_exit_client_order_id(symbol, session_date)
        try:
            raw = self._broker.execute_close(
                symbol=occ_symbol,
                quantity=qty,
                side="long",
                client_order_id=coid,
            )
        except Exception as exc:
            logger.error("Exit failed for %s: %s", symbol, exc)
            return ExecutionResult(
                status="unknown", filled_qty=0, filled_price=0,
                client_order_id=coid, error=str(exc),
            )

        return self._result_from_raw(raw, coid)

    def get_account_equity(self) -> float:
        """Fetch current paper account equity."""
        account = self._broker.get_account_cached()
        if not account:
            return 0.0
        return float(account.get("equity", 0) or 0)

    def reconcile_positions(self, internal_positions: list[dict]) -> dict:
        """Reconcile internal state against Alpaca paper positions."""
        return self._broker.reconcile_positions(internal_positions)

    def _result_from_raw(self, raw: dict, coid: str) -> ExecutionResult:
        """Convert raw broker result to ExecutionResult."""
        status = raw.get("status", "unknown")
        filled_qty = float(raw.get("filled_qty", 0) or 0)
        filled_price = float(raw.get("filled_price", 0) or 0)
        order_id = raw.get("alpaca_order_id")
        raw_order = raw.get("order")
        error = raw.get("error")
        return ExecutionResult(
            status=status,
            filled_qty=filled_qty,
            filled_price=filled_price,
            alpaca_order_id=order_id,
            client_order_id=coid,
            error=error,
            raw_order=raw_order,
        )
