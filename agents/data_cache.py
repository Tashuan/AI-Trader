"""Disk cache for historical OHLCV data — avoids re-fetching the same
bars from Alpaca/yfinance on every backtest experiment.

Stores DataFrames as parquet files under .data_cache/ keyed by
(provider, symbol, interval, start, end). On a cache hit, loads from
disk in milliseconds instead of making dozens of paginated API calls.

Usage:
    from data_cache import CachedProvider
    from equity_data_providers import AlpacaProvider

    provider = CachedProvider(AlpacaProvider())
    df = provider.history("NVDA", start="2024-01-01", end="2026-08-09", interval="15m")
    # First call: fetches from Alpaca, saves to .data_cache/
    # Second call: loads from disk instantly
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".data_cache",
)


def _cache_key(provider_name: str, symbol: str, interval: str,
               start: Optional[str], end: Optional[str], period: Optional[str]) -> str:
    """Build a deterministic cache key from the request parameters."""
    raw = f"{provider_name}|{symbol}|{interval}|{start or ''}|{end or ''}|{period or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.parquet")


def _meta_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, f"{key}.meta.json")


class CachedProvider:
    """Wraps any MarketDataProvider with disk-based parquet caching.

    Cache is keyed by (provider_class, symbol, interval, start, end, period).
    A cached file is reused if it exists and its row count matches the
    metadata — otherwise the fetch is re-run and the cache is overwritten.
    """

    def __init__(self, inner, cache_dir: str = ""):
        self.inner = inner
        self._cache_dir = cache_dir or _CACHE_DIR
        self._provider_name = type(inner).__name__
        os.makedirs(self._cache_dir, exist_ok=True)

    @property
    def available(self) -> bool:
        return getattr(self.inner, "available", True)

    def history(self, symbol: str, *, period: Optional[str] = "1mo",
                interval: str = "1d", **kwargs):
        start = kwargs.get("start")
        end = kwargs.get("end")
        key = _cache_key(self._provider_name, symbol, interval, start, end, period)
        cpath = _cache_path(key)
        mpath = _meta_path(key)

        # Try cache hit
        if os.path.exists(cpath) and os.path.exists(mpath):
            try:
                import json
                with open(mpath) as f:
                    meta = json.load(f)
                df = pd.read_parquet(cpath)
                if len(df) == meta.get("rows", 0) and not df.empty:
                    logger.info("Cache HIT: %s %s %s → %d rows (disk)",
                                self._provider_name, symbol, interval, len(df))
                    return df
                logger.info("Cache STALE: %s %s %s (rows mismatch, re-fetching)",
                            self._provider_name, symbol, interval)
            except Exception as exc:
                logger.warning("Cache read failed for %s: %s, re-fetching", symbol, exc)

        # Cache miss — fetch from provider
        logger.info("Cache MISS: %s %s %s — fetching from %s",
                    self._provider_name, symbol, interval, self._provider_name)
        df = self.inner.history(symbol, period=period, interval=interval, **kwargs)

        if df is not None and not df.empty:
            try:
                import json
                # Reset index so Datetime becomes a column for parquet storage
                df_to_save = df.reset_index() if df.index.name else df.copy()
                df_to_save.to_parquet(cpath, index=False)
                meta = {
                    "provider": self._provider_name,
                    "symbol": symbol,
                    "interval": interval,
                    "start": start,
                    "end": end,
                    "period": period,
                    "rows": len(df),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                }
                with open(mpath, "w") as f:
                    json.dump(meta, f, indent=2)
                logger.info("Cache SAVED: %s %s %s → %d rows → %s",
                            self._provider_name, symbol, interval, len(df), cpath)
            except Exception as exc:
                logger.warning("Cache write failed for %s: %s", symbol, exc)

        return df

    def quote(self, symbol: str):
        return self.inner.quote(symbol)


class CacheOnlyProvider:
    """Reads OHLCV data exclusively from .data_cache/ — no API calls.

    Scans meta files for matching (symbol, interval) and loads the parquet,
    filtering to the requested date range. If no exact (start, end) match
    exists, returns the full cached range intersected with the request.

    Falls back gracefully: returns None on any miss so the caller can
    decide whether to skip the symbol or try another provider.
    """

    def __init__(self, cache_dir: str = ""):
        self._cache_dir = cache_dir or _CACHE_DIR
        self._provider_name = "CacheOnly"

    @property
    def available(self) -> bool:
        return os.path.isdir(self._cache_dir)

    def _scan_metas(self, symbol: str, interval: str) -> list[dict]:
        """Return all cached meta entries matching symbol + interval."""
        import json
        hits = []
        if not os.path.isdir(self._cache_dir):
            return hits
        for fname in os.listdir(self._cache_dir):
            if not fname.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(self._cache_dir, fname)) as f:
                    meta = json.load(f)
                if meta.get("symbol") == symbol and meta.get("interval") == interval:
                    key = fname.replace(".meta.json", "")
                    hits.append((meta, key))
            except Exception:
                continue
        return hits

    def history(self, symbol: str, *, period: Optional[str] = "1mo",
                interval: str = "1d", **kwargs):
        import pandas as pd
        start = kwargs.get("start")
        end = kwargs.get("end")

        candidates = self._scan_metas(symbol, interval)
        if not candidates:
            logger.info("CacheOnly MISS: %s %s (no cached entry)", symbol, interval)
            return None

        # Pick the candidate whose date range best covers the request.
        # Prefer exact start/end match; otherwise pick the widest range.
        best = None
        best_score = -1
        for meta, key in candidates:
            m_start = meta.get("start")
            m_end = meta.get("end")
            m_rows = meta.get("rows", 0)
            if m_rows == 0:
                continue

            score = m_rows  # wider = better by default
            if start and m_start and m_end:
                # If cached range covers the requested range, boost score
                if m_start <= start and m_end >= (end or m_end):
                    score += 1_000_000
            if score > best_score:
                best = (meta, key)
                best_score = score

        if best is None:
            logger.info("CacheOnly MISS: %s %s (all entries empty)", symbol, interval)
            return None

        meta, key = best
        cpath = _cache_path(key)
        try:
            df = pd.read_parquet(cpath)
        except Exception as exc:
            logger.warning("CacheOnly read failed for %s: %s", symbol, exc)
            return None

        if df.empty:
            return None

        # Normalize: ensure Datetime column is UTC
        tcol = "Datetime" if "Datetime" in df.columns else "Date"
        if tcol in df.columns:
            df[tcol] = pd.to_datetime(df[tcol], utc=True)
            df = df.sort_values(tcol).reset_index(drop=True)
        elif df.index.name in ("Datetime", "Date"):
            df.index = pd.to_datetime(df.index, utc=True)
            df = df.sort_index().reset_index()
            tcol = df.columns[0]

        # Filter to requested date range
        if start and tcol in df.columns:
            df = df[df[tcol] >= pd.Timestamp(start, tz="UTC")]
        if end and tcol in df.columns:
            df = df[df[tcol] <= pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]

        if df.empty:
            logger.info("CacheOnly EMPTY after filter: %s %s", symbol, interval)
            return None

        # Restore DatetimeIndex for compatibility with backtester expectations
        if tcol in df.columns:
            df = df.set_index(tcol)

        logger.info("CacheOnly HIT: %s %s → %d rows", symbol, interval, len(df))
        return df

    def quote(self, symbol: str):
        df = self.history(symbol, period="1d", interval="1m")
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
