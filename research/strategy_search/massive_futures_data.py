"""Massive REST historical futures data for ORB research.

Uses Massive's futures aggregate endpoint with individual quarterly
contract tickers, then stitches the requested contracts into a continuous
research frame. This module is research-only; it does not place orders.
"""

from __future__ import annotations

import logging
import os
import time
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env", override=True)

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("MASSIVE_API_BASE", "https://api.massive.com")
MONTH_CODES = ((3, "H"), (6, "M"), (9, "U"), (12, "Z"))
ROOTS = {"MES=F": "MES", "MNQ=F": "MNQ", "M2K=F": "M2K", "MYM=F": "MYM"}
CACHE_DIR = _PROJECT_ROOT / "cache" / "massive_futures"


def _quarter_contracts(start: date, end: date, root: str) -> list[tuple[str, date, date]]:
    """Return quarterly contract tickers covering an inclusive date range."""
    result = []
    year = start.year - 1
    while year <= end.year + 1:
        for month, code in MONTH_CODES:
            prior_year = year - 1 if month == 3 else year
            prior_month = month - 3 if month > 3 else 10
            contract_start = date(prior_year, prior_month, 1)
            contract_end = date(year, month, monthrange(year, month)[1])
            if contract_end >= start and contract_start <= end:
                result.append((f"{root}{code}{year % 10}", contract_start, contract_end))
        year += 1
    return result


def _parse_bars(results: list[dict]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame()
    frame = pd.DataFrame(results)
    frame["Timestamp"] = pd.to_datetime(frame["window_start"], unit="ns", utc=True)
    frame = frame.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "volume": "Volume", "ticker": "Contract", "session_end_date": "SessionEndDate",
    })
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column not in frame:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["Timestamp"] = frame["Timestamp"].dt.tz_convert("America/New_York")
    return frame.set_index("Timestamp").sort_index()


class MassiveFuturesProvider:
    """Fetch individual futures contracts from Massive REST aggregates."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        cache_dir: Path = CACHE_DIR,
    ):
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self._last_request = 0.0
        if not self.api_key:
            raise RuntimeError("MASSIVE_API_KEY is not configured")

    def _request(self, path: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(8):
            wait = 1.0 - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            url = path if path.startswith("http") else f"{self.base_url}{path}"
            response = requests.get(
                url, headers=headers, params=params, timeout=60
            )
            self._last_request = time.time()
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else min(60.0, 5.0 * (attempt + 1))
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Massive API rate limit persisted after retries")

    def fetch_contract(
        self, ticker: str, start: date, end: date, resolution: str = "5min"
    ) -> pd.DataFrame:
        """Fetch one contract with pagination and normalized timestamps."""
        cache_path = self.cache_dir / f"{ticker}_{start}_{end}_{resolution}.parquet"
        if cache_path.exists():
            return pd.read_parquet(cache_path)
        path = f"/futures/v1/aggs/{ticker}"
        params = {
            "resolution": resolution,
            "window_start.gte": str(start),
            "window_start.lte": str(end),
            "sort": "window_start.asc",
            "limit": 50000,
        }
        rows: list[dict] = []
        while True:
            payload = self._request(path, params)
            rows.extend(payload.get("results", []) or [])
            next_url = payload.get("next_url")
            if not next_url:
                break
            if next_url.startswith(self.base_url):
                path = next_url[len(self.base_url):]
            else:
                path = next_url
            params = {}
        frame = _parse_bars(rows)
        if not frame.empty:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(cache_path)
        return frame

    def fetch_continuous(
        self,
        symbol: str,
        start: str | date,
        end: str | date,
        resolution: str = "5min",
    ) -> pd.DataFrame:
        """Fetch and stitch quarterly contracts for a product root."""
        if symbol not in ROOTS:
            raise ValueError(f"Unsupported Massive futures root: {symbol}")
        start_date = date.fromisoformat(start) if isinstance(start, str) else start
        end_date = date.fromisoformat(end) if isinstance(end, str) else end
        frames = []
        for ticker, contract_start, contract_end in _quarter_contracts(
            start_date, end_date, ROOTS[symbol]
        ):
            fetch_start = max(start_date, contract_start)
            fetch_end = min(end_date, contract_end)
            frame = self.fetch_contract(ticker, fetch_start, fetch_end, resolution)
            if not frame.empty:
                frames.append(frame)
                logger.info("Massive futures: %s %s bars=%d", ticker, resolution, len(frame))
        if not frames:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        result = pd.concat(frames).sort_index()
        result = result[~result.index.duplicated(keep="last")]
        rth = (result.index.time >= datetime.strptime("09:30", "%H:%M").time()) & (
            result.index.time <= datetime.strptime("15:55", "%H:%M").time()
        )
        result = result.loc[rth].copy()
        result["Timestamp"] = result.index
        return result


def fetch_massive_futures_bars(
    symbols: list[str], start: str, end: str, resolution: str = "5min"
) -> dict[str, pd.DataFrame]:
    """Backtester-compatible loader for Massive RTH futures bars."""
    provider = MassiveFuturesProvider()
    frames = {}
    for symbol in symbols:
        frame = provider.fetch_continuous(symbol, start, end, resolution=resolution)
        if frame.empty:
            print(f"  {symbol}: no Massive data")
            continue
        frames[symbol] = frame
        print(f"  {symbol}: {len(frame)} Massive RTH {resolution} bars, {frame.index[0].date()} to {frame.index[-1].date()}")
    return frames


def fetch_massive_futures_5m(
    symbols: list[str], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Backward-compatible loader for Massive 5-minute RTH data."""
    return fetch_massive_futures_bars(symbols, start, end, resolution="5min")
