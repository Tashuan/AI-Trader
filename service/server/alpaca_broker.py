"""Alpaca paper trading broker — mirrors internal AI-Trader trades to Alpaca.

When enabled (ALPACA_MIRROR_TRADES=true), every internal paper trade executed
via POST /api/signals/realtime is also submitted to Alpaca's paper trading
API. This lets you compare positions and PnL side-by-side.

Alpaca paper trading endpoint: https://paper-api.alpaca.markets/v2
Requires APCA_API_KEY_ID / APCA_API_SECRET_KEY (same keys used for market data).

Only US equities are mirrored. Crypto, polymarket, and other markets are skipped.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

_API_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
_SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")

# Paper trading endpoint (NOT the data endpoint)
_PAPER_TRADING_URL = os.environ.get("ALPACA_PAPER_TRADING_URL", "https://paper-api.alpaca.markets/v2")

_MIRROR_ENABLED = os.environ.get("ALPACA_MIRROR_TRADES", "true").strip().lower() in {"1", "true", "yes", "on"}

_REQUEST_TIMEOUT = 15

# Actions that map to Alpaca order sides
# buy  → buy   (open long)
# sell → sell  (close long)
# short → sell (open short)
# cover → buy  (close short)
_SIDE_MAP: dict[str, str] = {
    "buy": "buy",
    "sell": "sell",
    "short": "sell",
    "cover": "buy",
}


class AlpacaBroker:
    """Thin client for Alpaca paper trading API."""

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        base_url: str = "",
        websocket_url: str = "",
        managed_enabled: bool = False,
    ):
        self._api_key = api_key or _API_KEY
        self._secret_key = secret_key or _SECRET_KEY
        self._base_url = base_url or _PAPER_TRADING_URL
        self.websocket_url = websocket_url or os.environ.get(
            "ALPACA_WEBSOCKET_URL", "wss://paper-api.alpaca.markets/stream"
        )
        self._managed_enabled = managed_enabled
        self._cache: dict[str, tuple[float, Any]] = {}

    @property
    def enabled(self) -> bool:
        """True when this broker is usable for its configured execution mode."""
        if self._managed_enabled:
            return bool(self._api_key and self._secret_key)
        return _MIRROR_ENABLED and bool(self._api_key and self._secret_key)

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._secret_key)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _request(self, method: str, path: str, json_body: dict | None = None) -> Optional[dict]:
        url = f"{self._base_url}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(),
                json=json_body, timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 204:
                return {}
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except requests.RequestException as exc:
            logger.warning("AlpacaBroker: %s %s failed: %s", method, path, exc)
            return None

    # ── Account ──────────────────────────────────────────────────

    def get_account(self) -> Optional[dict]:
        """Fetch paper trading account info (balance, equity, buying power)."""
        return self._request("GET", "/account")

    # ── Orders ───────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "gtc",
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
    ) -> Optional[dict]:
        """Submit an order to Alpaca paper trading.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL')
            qty: Number of shares
            side: 'buy' or 'sell'
            order_type: 'market', 'limit', 'stop', 'stop_limit'
            time_in_force: 'gtc', 'day', 'ioc', 'opg'
            limit_price: Required for limit/stop_limit orders
            stop_price: Required for stop/stop_limit orders

        Returns:
            Order dict from Alpaca or None on failure.
        """
        body: dict[str, Any] = {
            "symbol": symbol.upper().replace("-USD", "").replace("=F", "").replace("^", ""),
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if client_order_id:
            body["client_order_id"] = client_order_id

        result = self._request("POST", "/orders", json_body=body)
        if result:
            logger.info(
                "AlpacaBroker: order submitted %s %s %.4f shares (%s) → id=%s",
                side, symbol, qty, order_type, result.get("id", "?"),
            )
        return result

    def submit_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "gtc",
        limit_price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        client_order_id: str | None = None,
    ) -> Optional[dict]:
        """Submit a bracket order — entry + SL + TP as linked OCO children.

        When the entry fills, both SL and TP are activated. When one fills,
        the other is automatically cancelled. This avoids the "insufficient
        qty" error from submitting separate SL/TP orders.

        Args:
            symbol: Ticker symbol
            qty: Share count
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit' for the entry
            time_in_force: 'gtc', 'day', etc.
            limit_price: Required if entry is limit
            stop_loss_price: Stop loss trigger price
            take_profit_price: Take profit limit price

        Returns:
            Order dict from Alpaca or None on failure.
        """
        body: dict[str, Any] = {
            "symbol": symbol.upper().replace("-USD", "").replace("=F", "").replace("^", ""),
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "order_class": "bracket",
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_loss_price is not None:
            body["stop_loss"] = {"stop_price": str(stop_loss_price)}
        if take_profit_price is not None:
            body["take_profit"] = {"limit_price": str(take_profit_price)}
        if client_order_id:
            body["client_order_id"] = client_order_id

        result = self._request("POST", "/orders", json_body=body)
        if result:
            logger.info(
                "AlpacaBroker: bracket order submitted %s %s %.4f shares (%s) "
                "SL=%s TP=%s → id=%s",
                side, symbol, qty, order_type,
                stop_loss_price, take_profit_price, result.get("id", "?"),
            )
        return result

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID."""
        result = self._request("DELETE", f"/orders/{order_id}")
        return result is not None

    def list_orders(self, status: str = "open") -> list[dict]:
        """List orders (default: open orders)."""
        url = f"{self._base_url}/orders"
        try:
            resp = requests.get(
                url, headers=self._headers(),
                params={"status": status}, timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("AlpacaBroker: list_orders failed: %s", exc)
            return []

    # ── Positions ────────────────────────────────────────────────

    def list_positions(self) -> list[dict]:
        """List all open positions on Alpaca paper account."""
        return self._request("GET", "/positions") or []

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get a specific position by symbol."""
        sym = symbol.upper().replace("-USD", "").replace("=F", "").replace("^", "")
        return self._request("GET", f"/positions/{sym}")

    def close_position(self, symbol: str, qty: float | None = None) -> Optional[dict]:
        """Close a position (fully or partially) by symbol.

        Uses Alpaca's close-position endpoint which issues a market order.
        """
        sym = symbol.upper().replace("-USD", "").replace("=F", "").replace("^", "")
        body: dict[str, Any] = {}
        if qty is not None:
            body["qty"] = str(qty)
        # DELETE /positions/{symbol} with optional qty in body
        url = f"{self._base_url}/positions/{sym}"
        try:
            resp = requests.delete(
                url, headers=self._headers(),
                json=body if body else None, timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 204:
                logger.info("AlpacaBroker: closed position %s", sym)
                return {}
            resp.raise_for_status()
            result = resp.json() if resp.content else {}
            logger.info("AlpacaBroker: closed position %s", sym)
            return result
        except requests.RequestException as exc:
            logger.warning("AlpacaBroker: close_position %s failed: %s", sym, exc)
            return None

    def _cancel_open_orders_for_symbol(self, symbol: str) -> int:
        """Cancel all open orders for a symbol on Alpaca. Returns count cancelled."""
        sym = symbol.upper().replace("-USD", "").replace("=F", "").replace("^", "")
        orders = self.list_orders(status="open")
        cancelled = 0
        for order in orders:
            if order.get("symbol", "").upper() == sym:
                oid = order.get("id")
                if oid and self.cancel_order(oid):
                    cancelled += 1
        return cancelled

    # ── Mirror helpers ───────────────────────────────────────────

    def mirror_trade(
        self,
        action: str,
        symbol: str,
        quantity: float,
        market: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> Optional[dict]:
        """Mirror an internal AI-Trader trade to Alpaca paper account.

        For entries (buy/short), optionally submits SL and TP as separate
        stop/limit orders on Alpaca after the entry fills.

        Only mirrors US equity trades (market == 'us-stock').
        Skips crypto, polymarket, etc.

        Args:
            action: 'buy', 'sell', 'short', 'cover'
            symbol: Ticker symbol
            quantity: Share count
            market: Internal market type ('us-stock', 'crypto', etc.)
            order_type: 'market' or 'limit'
            limit_price: Required for limit orders
            stop_loss_price: If set, submits a stop order on Alpaca
            take_profit_price: If set, submits a limit order on Alpaca

        Returns:
            Alpaca order dict or None if skipped/failed.
        """
        if not self.enabled:
            return None

        if market != "us-stock":
            logger.debug("AlpacaBroker: skipping non-equity trade (%s %s)", market, symbol)
            return None

        side = _SIDE_MAP.get(action.lower())
        if not side:
            logger.warning("AlpacaBroker: unknown action '%s', skipping", action)
            return None

        alpaca_tif = "gtc"

        # For entries with SL/TP, use bracket orders (entry + OCO SL/TP)
        # to avoid "insufficient qty" errors from separate orders.
        if action.lower() in ("buy", "short") and (stop_loss_price is not None or take_profit_price is not None):
            if order_type == "limit" and limit_price is not None:
                return self.submit_bracket_order(
                    symbol, quantity, side,
                    order_type="limit", time_in_force=alpaca_tif,
                    limit_price=limit_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                )
            return self.submit_bracket_order(
                symbol, quantity, side,
                order_type="market", time_in_force=alpaca_tif,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
            )

        # Exits (sell/cover) or entries without SL/TP — plain order
        if order_type == "limit" and limit_price is not None:
            return self.submit_order(
                symbol, quantity, side,
                order_type="limit", time_in_force=alpaca_tif,
                limit_price=limit_price,
            )
        return self.submit_order(
            symbol, quantity, side,
            order_type="market", time_in_force=alpaca_tif,
        )

    def mirror_close(self, symbol: str, quantity: float, side: str) -> Optional[dict]:
        """Mirror a position close (from auto-close loop) to Alpaca.

        Cancels any open SL/TP orders for the symbol first, then submits
        a market close order.

        Args:
            symbol: Ticker symbol
            quantity: Shares to close
            side: Internal position side ('long' or 'short')
        """
        if not self.enabled:
            return None

        # Cancel any resting SL/TP orders for this symbol on Alpaca
        self._cancel_open_orders_for_symbol(symbol)

        # For long positions, sell to close. For short, buy to close.
        close_side = "sell" if side == "long" else "buy"
        return self.submit_order(
            symbol, quantity, close_side,
            order_type="market", time_in_force="gtc",
        )

    def mirror_pending_order_create(
        self,
        symbol: str,
        side: str,
        order_type: str,
        stop_price: float,
        limit_price: float | None,
        quantity: float,
        market: str,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> Optional[dict]:
        """Mirror a pending stop-limit order creation to Alpaca.

        Submits a stop or stop_limit order to Alpaca. SL/TP are NOT included
        here because Alpaca bracket orders don't support stop entries. When
        the internal pending order fills, the filler loop mirrors the entry
        as a bracket order with SL/TP — but since Alpaca may have already
        opened the position from this stop order, we skip mirroring the fill
        if Alpaca already has the position.

        Args:
            symbol: Ticker symbol
            side: 'long' or 'short'
            order_type: 'stop_limit' or 'stop_market'
            stop_price: Stop trigger price
            limit_price: Limit price for stop_limit orders
            quantity: Share count
            market: Internal market type
            stop_loss_price: Not used here (applied on fill via reconciliation)
            take_profit_price: Not used here (applied on fill via reconciliation)
        """
        if not self.enabled or market != "us-stock":
            return None

        alpaca_side = "buy" if side == "long" else "sell"

        if order_type == "stop_limit" and limit_price is not None:
            return self.submit_order(
                symbol, quantity, alpaca_side,
                order_type="stop_limit", time_in_force="gtc",
                stop_price=stop_price, limit_price=limit_price,
            )
        return self.submit_order(
            symbol, quantity, alpaca_side,
            order_type="stop", time_in_force="gtc",
            stop_price=stop_price,
        )

    def mirror_pending_order_cancel(self, symbol: str) -> int:
        """Cancel all open orders for a symbol on Alpaca (pending order cancelled).

        Returns count of orders cancelled.
        """
        if not self.enabled:
            return 0
        return self._cancel_open_orders_for_symbol(symbol)

    # ── Order verification ───────────────────────────────────────

    def get_order(self, order_id: str) -> Optional[dict]:
        """Fetch a single order by ID to check its status."""
        return self._request("GET", f"/orders/{order_id}")

    def verify_order_filled(self, order_id: str) -> dict:
        """Check if an Alpaca order was filled, rejected, or still pending.

        Returns:
            {'status': 'filled'|'rejected'|'pending'|'cancelled'|'unknown',
             'filled_qty': float, 'filled_price': float, 'order': dict}
        """
        order = self.get_order(order_id)
        if not order:
            return {'status': 'unknown', 'filled_qty': 0, 'filled_price': 0, 'order': None}
        status = order.get('status', 'unknown')
        filled_qty = float(order.get('filled_qty', 0) or 0)
        filled_avg_price = float(order.get('filled_avg_price', 0) or 0)
        return {
            'status': status,
            'filled_qty': filled_qty,
            'filled_price': filled_avg_price,
            'order': order,
        }

    # ── Execution-layer helpers ───────────────────────────────────

    def get_account_cached(self, ttl: int | None = None) -> Optional[dict]:
        ttl = ttl if ttl is not None else int(os.environ.get("ALPACA_ACCOUNT_CACHE_TTL", "5"))
        key = "account"
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < max(1, ttl):
            return cached[1]
        account = self.get_account()
        if account is not None:
            self._cache[key] = (time.monotonic(), account)
        return account

    def get_position_cached(self, symbol: str, ttl: int | None = None) -> Optional[dict]:
        ttl = ttl if ttl is not None else int(os.environ.get("ALPACA_POSITION_CACHE_TTL", "5"))
        key = f"position:{symbol.upper()}"
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < max(1, ttl):
            return cached[1]
        position = self.get_position(symbol)
        self._cache[key] = (time.monotonic(), position)
        return position

    def invalidate_cache(self, symbol: str | None = None) -> None:
        if symbol:
            self._cache.pop(f"position:{symbol.upper()}", None)
        else:
            self._cache.clear()

    def find_order_by_client_order_id(self, client_order_id: str) -> Optional[dict]:
        orders = self.list_orders(status="all")
        return next((order for order in orders if order.get("client_order_id") == client_order_id), None)

    def execute_order(
        self,
        *,
        symbol: str,
        quantity: float,
        action: str,
        client_order_id: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        poll_timeout: float | None = None,
    ) -> dict:
        """Submit an execution-layer order and return a non-ambiguous result."""
        side = _SIDE_MAP.get(action.lower())
        if not side:
            return {"status": "rejected", "error": f"Unsupported action: {action}"}
        existing = self.find_order_by_client_order_id(client_order_id)
        if existing is not None:
            order = existing
        elif action.lower() in ("buy", "short") and (stop_loss_price is not None or take_profit_price is not None):
            order = self.submit_bracket_order(
                symbol, quantity, side, order_type=order_type, time_in_force="gtc",
                limit_price=limit_price, stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price, client_order_id=client_order_id,
            )
        else:
            order = self.submit_order(
                symbol, quantity, side, order_type=order_type, time_in_force="gtc",
                limit_price=limit_price, client_order_id=client_order_id,
            )
        if not order:
            return {"status": "unknown", "client_order_id": client_order_id, "error": "Alpaca submission failed"}

        order_id = order.get("id")
        result = self._order_result(order)
        timeout = poll_timeout if poll_timeout is not None else float(os.environ.get("ALPACA_FILL_POLL_TIMEOUT", "10"))
        deadline = time.monotonic() + max(0, timeout)
        while result["status"] in {"pending", "submitted", "accepted", "new", "partially_filled"} and time.monotonic() < deadline:
            time.sleep(0.5)
            latest = self.get_order(order_id) if order_id else None
            if latest:
                order = latest
                result = self._order_result(order)
        result.update({"order": order, "alpaca_order_id": order_id, "client_order_id": client_order_id})
        return result

    @staticmethod
    def _order_result(order: dict) -> dict:
        status = str(order.get("status", "unknown")).lower()
        mapping = {
            "filled": "filled", "partially_filled": "partially_filled",
            "rejected": "rejected", "canceled": "cancelled", "cancelled": "cancelled",
            "expired": "expired", "new": "pending", "accepted": "pending",
            "pending_new": "pending", "pending_cancel": "pending", "held": "pending",
        }
        return {
            "status": mapping.get(status, "unknown"),
            "filled_qty": float(order.get("filled_qty") or 0),
            "filled_price": float(order.get("filled_avg_price") or 0),
            "raw_status": status,
        }

    def execute_close(self, *, symbol: str, quantity: float, side: str, client_order_id: str) -> dict:
        self._cancel_open_orders_for_symbol(symbol)
        close_side = "sell" if side == "long" else "buy"
        order = self.submit_order(symbol, quantity, close_side, order_type="market", time_in_force="gtc", client_order_id=client_order_id)
        if not order:
            return {"status": "unknown", "client_order_id": client_order_id}
        result = self._order_result(order)
        deadline = time.monotonic() + float(os.environ.get("ALPACA_FILL_POLL_TIMEOUT", "10"))
        while result["status"] in {"pending", "partially_filled"} and time.monotonic() < deadline:
            time.sleep(0.5)
            latest = self.get_order(order.get("id"))
            if latest:
                order = latest
                result = self._order_result(order)
        self.invalidate_cache(symbol)
        result.update({"order": order, "alpaca_order_id": order.get("id"), "client_order_id": client_order_id})
        return result

    # ── Reconciliation ───────────────────────────────────────────

    def reconcile_positions(self, internal_positions: list[dict]) -> dict:
        """Compare internal open US-equity positions against Alpaca positions.

        Args:
            internal_positions: List of dicts with keys: symbol, side, quantity, entry_price
                               (only us-stock market positions should be passed)

        Returns:
            {
                'matched': [ {symbol, internal: {...}, alpaca: {...}} ],
                'internal_only': [ {symbol, internal: {...}} ],  # Alpaca doesn't have it
                'alpaca_only': [ {symbol, alpaca: {...}} ],      # Internal doesn't have it
                'qty_mismatch': [ {symbol, internal_qty, alpaca_qty, diff} ],
                'side_mismatch': [ {symbol, internal_side, alpaca_side} ],
                'summary': { 'matched': N, 'internal_only': N, 'alpaca_only': N, ... },
            }
        """
        alpaca_positions = self.list_positions()

        # Index by symbol
        alpaca_by_symbol: dict[str, dict] = {}
        for pos in alpaca_positions:
            sym = pos.get('symbol', '').upper()
            alpaca_by_symbol[sym] = pos

        internal_by_symbol: dict[str, dict] = {}
        for pos in internal_positions:
            sym = pos.get('symbol', '').upper().replace('-USD', '').replace('=F', '').replace('^', '')
            internal_by_symbol[sym] = pos

        all_symbols = set(internal_by_symbol.keys()) | set(alpaca_by_symbol.keys())

        matched = []
        internal_only = []
        alpaca_only = []
        qty_mismatch = []
        side_mismatch = []

        for sym in sorted(all_symbols):
            internal = internal_by_symbol.get(sym)
            alpaca = alpaca_by_symbol.get(sym)

            if internal and alpaca:
                # Both have it — check qty and side
                internal_qty = abs(float(internal.get('quantity', 0)))
                alpaca_qty = abs(float(alpaca.get('qty', 0)))
                internal_side = internal.get('side', 'long')
                alpaca_side = 'long' if float(alpaca.get('qty', 0)) > 0 else 'short'

                qty_diff = abs(internal_qty - alpaca_qty)
                if qty_diff > 0.001:
                    qty_mismatch.append({
                        'symbol': sym,
                        'internal_qty': internal_qty,
                        'alpaca_qty': alpaca_qty,
                        'diff': qty_diff,
                    })
                if internal_side != alpaca_side:
                    side_mismatch.append({
                        'symbol': sym,
                        'internal_side': internal_side,
                        'alpaca_side': alpaca_side,
                    })
                if qty_diff <= 0.001 and internal_side == alpaca_side:
                    matched.append({'symbol': sym, 'internal': internal, 'alpaca': alpaca})
                else:
                    # Still include in matched but with flags
                    matched.append({
                        'symbol': sym, 'internal': internal, 'alpaca': alpaca,
                        'qty_mismatch': qty_diff > 0.001,
                        'side_mismatch': internal_side != alpaca_side,
                    })
            elif internal and not alpaca:
                internal_only.append({'symbol': sym, 'internal': internal})
            elif alpaca and not internal:
                alpaca_only.append({'symbol': sym, 'alpaca': alpaca})

        return {
            'matched': matched,
            'internal_only': internal_only,
            'alpaca_only': alpaca_only,
            'qty_mismatch': qty_mismatch,
            'side_mismatch': side_mismatch,
            'summary': {
                'matched': len(matched),
                'internal_only': len(internal_only),
                'alpaca_only': len(alpaca_only),
                'qty_mismatch': len(qty_mismatch),
                'side_mismatch': len(side_mismatch),
                'total_drift': len(internal_only) + len(alpaca_only) + len(qty_mismatch) + len(side_mismatch),
            },
        }

    def get_alpaca_position_closes(self, internal_symbols: list[str]) -> list[dict]:
        """Detect positions that Alpaca closed (SL/TP fired) but internal still shows open.

        Returns list of {'symbol', 'alpaca_side', 'alpaca_qty'} for positions
        that exist internally but not on Alpaca (meaning Alpaca closed them).

        Note: This can also mean the Alpaca order was rejected on entry.
        Use recent order history to distinguish.
        """
        alpaca_positions = self.list_positions()
        alpaca_symbols = {pos.get('symbol', '').upper() for pos in alpaca_positions}

        closed_on_alpaca = []
        for sym in internal_symbols:
            sym_clean = sym.upper().replace('-USD', '').replace('=F', '').replace('^', '')
            if sym_clean not in alpaca_symbols:
                closed_on_alpaca.append({'symbol': sym_clean})

        return closed_on_alpaca

    def get_recent_orders_for_symbol(self, symbol: str, limit: int = 10) -> list[dict]:
        """Get recent orders for a symbol from Alpaca (to check if SL/TP fired)."""
        sym = symbol.upper().replace('-USD', '').replace('=F', '').replace('^', "")
        url = f"{self._base_url}/orders"
        try:
            resp = requests.get(
                url, headers=self._headers(),
                params={"symbols": sym, "limit": limit, "status": "all"},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("AlpacaBroker: get_recent_orders_for_symbol %s failed: %s", sym, exc)
            return []


# ── Module-level singleton ────────────────────────────────────────

_broker_instance: Optional[AlpacaBroker] = None
_agent_brokers: dict[int, AlpacaBroker] = {}


def get_alpaca_broker_for_agent(agent_id: int) -> Optional[AlpacaBroker]:
    """Return the enabled, per-agent execution broker, if configured."""
    cached = _agent_brokers.get(int(agent_id))
    if cached is not None:
        return cached if cached.enabled else None
    try:
        from database import get_db_connection
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT alpaca_api_key, alpaca_secret_key, base_url, websocket_url, enabled "
                "FROM alpaca_account_config WHERE agent_id = ?",
                (int(agent_id),),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Alpaca config lookup failed for agent %s: %s", agent_id, exc)
        return None
    if not row or not bool(row["enabled"]):
        return None
    broker = AlpacaBroker(
        api_key=row["alpaca_api_key"],
        secret_key=row["alpaca_secret_key"],
        base_url=row["base_url"],
        websocket_url=row["websocket_url"],
        managed_enabled=True,
    )
    _agent_brokers[int(agent_id)] = broker
    return broker


def get_alpaca_broker() -> AlpacaBroker:
    """Return shared AlpacaBroker singleton."""
    global _broker_instance
    if _broker_instance is None:
        _broker_instance = AlpacaBroker()
    return _broker_instance
