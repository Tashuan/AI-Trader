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
