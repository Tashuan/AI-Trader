"""Schwab options data provider.

Fetches historical option bars and real-time option chains from Schwab's
Market Data API. Free with a Schwab brokerage account — no paid data
subscription required (unlike Alpaca's OPRA feed).

Schwab option symbol format: "NVDA  260817C00222500" (two spaces between
the underlying ticker and the OCC symbol).

Endpoints used:
  GET /marketdata/v1/pricehistory  — historical 1m/daily OHLCV for options
  GET /marketdata/v1/chains        — real-time option chain (greeks, IV, bid/ask)
  GET /marketdata/v1/expirationchain — available expiration dates

Requires Schwab OAuth (schwab_auth.py). Run schwab_oauth_flow.py once to
get a refresh token.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from schwab_auth import SchwabOAuth, from_env as _auth_from_env

logger = logging.getLogger("SchwabOptionsProvider")

_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
_TIMEOUT = 30
_MAX_RETRIES = 2
_RETRY_DELAY = 0.5


@dataclass
class OptionContract:
    """Metadata for a single option contract."""
    symbol: str          # Schwab symbol, e.g. "NVDA  260817C00222500"
    occ_symbol: str      # OCC symbol, e.g. "NVDA260817C00222500"
    underlying: str
    type: str            # call or put
    strike: float
    expiration: str      # YYYY-MM-DD
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0
    open_interest: int = 0


def build_schwab_symbol(underlying: str, expiration: str, option_type: str,
                        strike: float) -> str:
    """Construct a Schwab option symbol from components.

    Format: {UNDERLYING}  {YYMMDD}{C/P}{STRIKE*1000 padded to 8 digits}
    Note: TWO spaces between underlying and OCC symbol.

    Example: NVDA, 2026-08-17, call, 222.5 → "NVDA  260817C00222500"
    """
    exp = datetime.fromisoformat(expiration)
    date_str = exp.strftime("%y%m%d")
    type_char = "C" if option_type.lower().startswith("c") else "P"
    strike_int = int(round(strike * 1000))
    occ = f"{date_str}{type_char}{strike_int:08d}"
    return f"{underlying.upper()}  {occ}"


class SchwabOptionsProvider:
    """Fetches option bars and chains from Schwab's Market Data API."""

    def __init__(self, auth: Optional[SchwabOAuth] = None):
        self._auth = auth or _auth_from_env()
        if not self._auth or not self._auth.is_configured:
            logger.warning("SchwabOptionsProvider: no credentials configured")

    @property
    def available(self) -> bool:
        return self._auth is not None and self._auth.is_configured

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        """GET request to Schwab API. Returns parsed JSON or None."""
        if not self.available:
            return None
        token = self._auth.get_access_token()
        if not token:
            logger.error("No valid access token")
            return None

        url = f"{_BASE_URL}{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        for attempt in range(_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 1.0 * (attempt + 1)
                    logger.warning("Rate limited, waiting %.1fs", wait)
                    time.sleep(wait)
                    continue
                body = e.read().decode()[:200] if e.fp else ""
                logger.warning("HTTP %d: %s", e.code, body)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
            except Exception as exc:
                logger.warning("GET %s failed (attempt %d): %s",
                               path, attempt + 1, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY)
        return None

    def get_expirations(self, underlying: str) -> list[dict]:
        """Get available option expiration dates for an underlying.

        Returns:
            List of dicts with keys: expirationDate, expirationType,
            daysToExpiration, strikeCount
        """
        data = self._get("/expirationchain", {"symbol": underlying})
        if data is None:
            return []
        return data.get("expirationList", [])

    def get_chain(
        self, underlying: str, contract_type: str = "ALL",
        strike_count: int = 20, include_quotes: bool = True,
        from_date: str = "", to_date: str = "",
    ) -> dict[str, list[OptionContract]]:
        """Get real-time option chain for an underlying.

        Args:
            underlying: Stock ticker
            contract_type: "CALL", "PUT", or "ALL"
            strike_count: Number of strikes per expiration (centered on ATM)
            include_quotes: Include bid/ask in response
            from_date: Filter expirations >= this date (YYYY-MM-DD)
            to_date: Filter expirations <= this date (YYYY-MM-DD)

        Returns:
            Dict mapping expiration date string -> list of OptionContract
        """
        params: dict = {
            "symbol": underlying,
            "contractType": contract_type.upper(),
            "strikeCount": strike_count,
            "includeQuotes": str(include_quotes).lower(),
        }
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date

        data = self._get("/chains", params)
        if data is None:
            return {}

        result: dict[str, list[OptionContract]] = {}

        # Parse callExpDateMap and putExpDateMap
        for exp_map_key in ("callExpDateMap", "putExpDateMap"):
            exp_map = data.get(exp_map_key, {})
            for exp_date_key, strikes in exp_map.items():
                # exp_date_key format: "2026-08-17:1" (date:daysToExp)
                exp_date = exp_date_key.split(":")[0]
                if exp_date not in result:
                    result[exp_date] = []
                for strike_key, contracts in strikes.items():
                    for c in contracts:
                        occ = c.get("symbol", "").replace(" ", "")
                        opt_type = "call" if "C" in occ[-9:] else "put"
                        # Better type detection from the put/call map
                        if exp_map_key == "callExpDateMap":
                            opt_type = "call"
                        else:
                            opt_type = "put"
                        result[exp_date].append(OptionContract(
                            symbol=c.get("symbol", ""),
                            occ_symbol=occ,
                            underlying=underlying,
                            type=opt_type,
                            strike=float(c.get("strikePrice", 0)),
                            expiration=exp_date,
                            bid=float(c.get("bid", 0) or 0),
                            ask=float(c.get("ask", 0) or 0),
                            last=float(c.get("last", 0) or 0),
                            delta=float(c.get("delta", 0) or 0),
                            gamma=float(c.get("gamma", 0) or 0),
                            theta=float(c.get("theta", 0) or 0),
                            vega=float(c.get("vega", 0) or 0),
                            iv=float(c.get("volatility", 0) or 0),
                            open_interest=int(c.get("openInterest", 0) or 0),
                        ))
        return result

    def get_atm_contract(
        self, underlying: str, expiration: str, spot_price: float,
        option_type: str, strike_offset: int = 0,
        strike_step: float = 2.5,
    ) -> OptionContract | None:
        """Find the ATM option contract from the real-time chain.

        Args:
            underlying: Stock ticker
            expiration: Expiration date YYYY-MM-DD
            spot_price: Current underlying price
            option_type: "call" or "put"
            strike_offset: Strikes from ATM (0=ATM, +1=OTM, -1=ITM)
            strike_step: Strike increment for fallback symbol construction

        Returns:
            OptionContract or None
        """
        contract_type = "CALL" if option_type.lower().startswith("c") else "PUT"
        chain = self.get_chain(underlying, contract_type, strike_count=20)
        contracts = chain.get(expiration, [])
        if not contracts:
            # Fallback: construct symbol without chain data
            atm_strike = round(spot_price / strike_step) * strike_step
            target_strike = atm_strike + strike_offset * strike_step
            sym = build_schwab_symbol(underlying, expiration, option_type, target_strike)
            return OptionContract(
                symbol=sym, occ_symbol=sym.replace(" ", ""),
                underlying=underlying, type=option_type,
                strike=target_strike, expiration=expiration,
            )
        # Sort by distance from spot
        contracts.sort(key=lambda c: abs(c.strike - spot_price))
        idx = min(strike_offset, len(contracts) - 1) if strike_offset >= 0 else \
              max(strike_offset, -len(contracts))
        return contracts[idx]

    def get_bars(
        self, option_symbol: str, start: str, end: str,
        frequency_type: str = "minute", frequency: int = 1,
    ) -> pd.DataFrame | None:
        """Get historical OHLCV bars for an option contract.

        Args:
            option_symbol: Schwab option symbol (with two spaces)
                          e.g. "NVDA  260817C00222500"
            start: Start date YYYY-MM-DD
            end: End date YYYY-MM-DD
            frequency_type: "minute", "daily", "weekly", or "monthly"
            frequency: Bar frequency (1 = every minute, 5 = every 5 min, etc.)

        Returns:
            DataFrame with UTC DatetimeIndex and OHLCV columns, or None

        Note: Schwab limits minute data to periodType=day with max period=70.
        For ranges > 70 days, we chunk the requests and concatenate.
        """
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)

        if frequency_type == "minute":
            # Schwab: minute data requires periodType=day, max period=70
            return self._get_minute_bars_chunked(option_symbol, start_dt, end_dt, frequency)
        else:
            # Daily/weekly/monthly can use periodType=year
            start_ms = int(start_dt.timestamp() * 1000)
            end_ms = int(end_dt.timestamp() * 1000)
            params = {
                "symbol": option_symbol,
                "periodType": "year",
                "period": 1,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "startDate": start_ms,
                "endDate": end_ms,
                "needExtendedHoursData": "false",
            }
            return self._fetch_bars_df(params)

    def _get_minute_bars_chunked(
        self, option_symbol: str, start_dt: datetime, end_dt: datetime,
        frequency: int,
    ) -> pd.DataFrame | None:
        """Fetch minute bars in 10-day chunks (Schwab limit: period max 10)."""
        from datetime import timedelta
        chunks: list[pd.DataFrame] = []
        current = start_dt
        while current < end_dt:
            chunk_end = min(current + timedelta(days=10), end_dt)
            start_ms = int(current.timestamp() * 1000)
            end_ms = int(chunk_end.timestamp() * 1000)
            params = {
                "symbol": option_symbol,
                "periodType": "day",
                "period": 10,
                "frequencyType": "minute",
                "frequency": frequency,
                "startDate": start_ms,
                "endDate": end_ms,
                "needExtendedHoursData": "false",
            }
            df = self._fetch_bars_df(params)
            if df is not None and not df.empty:
                chunks.append(df)
            current = chunk_end
        if not chunks:
            return None
        return pd.concat(chunks).sort_index()

    def _fetch_bars_df(self, params: dict) -> pd.DataFrame | None:
        """Fetch bars from API and return as DataFrame."""
        data = self._get("/pricehistory", params)
        if data is None:
            return None
        candles = data.get("candles", [])
        if not candles:
            return None
        df = pd.DataFrame(candles)
        df["Timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.set_index("Timestamp")
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[cols]

        data = self._get("/pricehistory", params)
        if data is None:
            return None

        candles = data.get("candles", [])
        if not candles:
            return None

        # Build DataFrame
        df = pd.DataFrame(candles)
        # datetime is epoch milliseconds
        df["Timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.set_index("Timestamp")
        # Rename columns to match our convention
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })
        # Keep only OHLCV
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols]
        return df

    def get_bars_by_occ(
        self, underlying: str, expiration: str, option_type: str,
        strike: float, start: str, end: str,
        frequency_type: str = "minute", frequency: int = 1,
    ) -> pd.DataFrame | None:
        """Get historical bars by constructing the Schwab symbol from components.

        Convenience method — constructs the symbol and calls get_bars.
        """
        sym = build_schwab_symbol(underlying, expiration, option_type, strike)
        return self.get_bars(sym, start, end, frequency_type, frequency)


# Module-level instance
_provider_instance: Optional[SchwabOptionsProvider] = None


def get_provider() -> SchwabOptionsProvider:
    """Get or create the singleton SchwabOptionsProvider instance."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = SchwabOptionsProvider()
    return _provider_instance
