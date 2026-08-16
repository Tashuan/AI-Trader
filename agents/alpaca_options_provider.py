"""Alpaca options data provider.

Fetches historical option contracts and 1m bars from Alpaca's options API.
Used by the options-based ORB backtester to amplify the thin equity edge
via options leverage.

Alpaca options API:
  - Contracts: GET /v2/options/contracts (Trading API)
  - Bars:      GET /v1beta1/options/bars (Data API)
  - Chain:     GET /v1beta1/options/snapshots (Data API, real-time only)

Historical option bars available since Feb 2024.
Option symbol format: OCC format, e.g. NVDA260817C00200000
  = NVDA, 2026-08-17 expiry, Call, $200.00 strike

Requires APCA_API_KEY_ID / APCA_API_SECRET_KEY (same as equity data).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
_SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
_DATA_URL = "https://data.alpaca.markets/v1beta1/options"
_TRADING_URL = "https://api.alpaca.markets/v2"
_TIMEOUT = 30
_MAX_RETRIES = 2
_RETRY_DELAY = 0.5
_MAX_BARS = 10_000


@dataclass
class OptionContract:
    """Metadata for a single option contract."""
    symbol: str          # OCC symbol, e.g. NVDA260817C00200000
    underlying: str      # NVDA
    type: str            # call or put
    strike: float
    expiration: str      # YYYY-MM-DD
    style: str           # american
    multiplier: int      # 100
    status: str          # active


def build_occ_symbol(underlying: str, expiration: str, option_type: str,
                     strike: float) -> str:
    """Construct an OCC option symbol from components.

    Format: {ROOT}{YYMMDD}{C/P}{STRIKE*1000 padded to 8 digits}
    Example: NVDA, 2026-08-17, call, 200.00 → NVDA260817C00200000

    Args:
        underlying: Stock ticker, e.g. "NVDA"
        expiration: YYYY-MM-DD
        option_type: "call" or "put"
        strike: Strike price, e.g. 200.0

    Returns:
        OCC symbol string
    """
    exp = datetime.fromisoformat(expiration)
    date_str = exp.strftime("%y%m%d")
    type_char = "C" if option_type.lower().startswith("c") else "P"
    strike_int = int(round(strike * 1000))
    return f"{underlying.upper()}{date_str}{type_char}{strike_int:08d}"


class AlpacaOptionsProvider:
    """Fetches option contracts and historical bars from Alpaca."""

    def __init__(self, api_key: str = "", secret_key: str = ""):
        self._api_key = api_key or _API_KEY
        self._secret_key = secret_key or _SECRET_KEY

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._secret_key)

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _get(self, base_url: str, path: str, params: dict | None = None) -> dict | None:
        url = f"{base_url}{path}"
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=self._headers(), params=params,
                                    timeout=_TIMEOUT)
                if resp.status_code == 429:
                    wait = 1.0 * (attempt + 1)
                    logger.warning("Options provider rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                logger.warning("Options GET %s failed (attempt %d): %s",
                               path, attempt + 1, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
        return None

    def get_contracts(
        self, underlying: str, expiration: str,
        option_type: str = "", strike_min: float = 0, strike_max: float = 0,
        limit: int = 1000,
    ) -> list[OptionContract]:
        """Get option contracts for an underlying on a specific expiration date.

        Args:
            underlying: Stock ticker, e.g. "NVDA"
            expiration: Expiration date YYYY-MM-DD
            option_type: "call" or "put" (empty = both)
            strike_min: Minimum strike (0 = no filter)
            strike_max: Maximum strike (0 = no filter)
            limit: Max contracts to return

        Returns:
            List of OptionContract objects
        """
        params: dict = {
            "underlying_symbols": underlying,
            "expiration_date": expiration,
            "limit": limit,
            "status": "active",
        }
        if option_type:
            params["type"] = option_type
        if strike_min > 0:
            params["strike_price_gte"] = str(strike_min)
        if strike_max > 0:
            params["strike_price_lte"] = str(strike_max)

        contracts: list[OptionContract] = []
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token
            data = self._get(_TRADING_URL, "/options/contracts", params)
            if data is None:
                break
            for c in data.get("option_contracts", []):
                contracts.append(OptionContract(
                    symbol=c["symbol"],
                    underlying=c.get("underlying_symbol", underlying),
                    type=c.get("type", ""),
                    strike=float(c.get("strike_price", 0)),
                    expiration=c.get("expiration_date", expiration),
                    style=c.get("style", "american"),
                    multiplier=int(c.get("multiplier", 100)),
                    status=c.get("status", "active"),
                ))
            page_token = data.get("next_page_token")
            if not page_token or len(contracts) >= limit:
                break
        return contracts

    def get_bars(
        self, option_symbol: str, start: str, end: str,
        timeframe: str = "1Min", limit: int = _MAX_BARS,
    ) -> list[dict]:
        """Get historical OHLC bars for an option contract.

        Args:
            option_symbol: OCC symbol, e.g. "NVDA260817C00200000"
            start: Start date/time (YYYY-MM-DD or RFC3339)
            end: End date/time
            timeframe: 1Min, 5Min, etc.
            limit: Max bars per page

        Returns:
            List of bar dicts with keys: t, o, h, l, c, v, n, vw
        """
        all_bars: list[dict] = []
        page_token = None
        while True:
            params: dict = {
                "symbols": option_symbol,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": limit,
            }
            if page_token:
                params["page_token"] = page_token
            data = self._get(_DATA_URL, "/bars", params)
            if data is None:
                break
            bars = data.get("bars", {}).get(option_symbol, [])
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return all_bars

    def get_atm_contract(
        self, underlying: str, expiration: str, spot_price: float,
        option_type: str, strike_offset: int = 0,
        strike_step: float = 2.5,
    ) -> OptionContract | None:
        """Find the at-the-money option contract closest to spot price.

        Constructs the OCC symbol directly (no trading API needed).
        Uses strike_step to round to the nearest valid strike.

        Args:
            underlying: Stock ticker
            expiration: Expiration date YYYY-MM-DD
            spot_price: Current underlying price
            option_type: "call" or "put"
            strike_offset: Number of strikes away from ATM (positive = OTM, negative = ITM)
            strike_step: Strike increment (2.5 for NVDA, 0.5 for AAPL, etc.)

        Returns:
            OptionContract or None
        """
        # Round spot to nearest strike
        atm_strike = round(spot_price / strike_step) * strike_step
        target_strike = atm_strike + strike_offset * strike_step
        symbol = build_occ_symbol(underlying, expiration, option_type, target_strike)
        return OptionContract(
            symbol=symbol, underlying=underlying, type=option_type,
            strike=target_strike, expiration=expiration,
            style="american", multiplier=100, status="active",
        )

    def get_expirations(
        self, underlying: str, min_date: str = "", max_date: str = "",
    ) -> list[str]:
        """Get available expiration dates for an underlying.

        Returns sorted list of YYYY-MM-DD strings.
        """
        params: dict = {"underlying_symbols": underlying, "limit": 1000}
        if min_date:
            params["expiration_date_gte"] = min_date
        if max_date:
            params["expiration_date_lte"] = max_date
        expirations: set[str] = set()
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token
            data = self._get(_TRADING_URL, "/options/contracts", params)
            if data is None:
                break
            for c in data.get("option_contracts", []):
                expirations.add(c.get("expiration_date", ""))
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return sorted(e for e in expirations if e)
