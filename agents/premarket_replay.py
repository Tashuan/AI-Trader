"""Replay provider for backtesting the premarket scanner on historical data.

Pulls historical intraday bars with extended-hours (premarket) data from
yfinance (rich premarket coverage: 67 bars/day at 5m for 60 days back)
and daily history from Alpaca (going back to 2016). Also provides a
Finnhub news fetcher for historical company news on the target date.

This lets us test the scanner's ranking logic on real premarket sessions
from any past trading day (up to 60 days back for 5m bars, 7 days for 1m).

Usage:
    python agents/run_premarket_replay.py --date 2026-08-11
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd

from equity_data_providers import AlpacaProvider

logger = logging.getLogger(__name__)

# US premarket session: 4:00 AM – 9:30 AM Eastern
_PREMARKET_START = "04:00"
# Exclude the 09:30 regular-session bar. For 5m data this ends at 09:25;
# for 1m data it includes through 09:29.
_PREMARKET_END = "09:29"

# Eastern timezone for filtering premarket hours
_ET = "America/New_York"

# yfinance interval limits:
#   1m: 7 days back, 5m/15m/30m: 60 days back, 1h: 730 days back
_YF_INTERVAL_PERIOD_MAP = {
    "1m": "5d",    # 1m only goes back 7 days; use 5d to be safe
    "2m": "60d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "60m": "730d",
    "1h": "730d",
}


def _suppress_yfinance_logging() -> None:
    """Silence yfinance's internal error prints."""
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        import yfinance as yf
        yf.enableDebugMode(False) if hasattr(yf, "enableDebugMode") else None
    except Exception:
        pass


class PremarketReplayProvider:
    """Market data provider that replays a historical premarket session.

    Data sources:
      - yfinance (prepost=True): rich premarket intraday bars (67 bars/day at 5m)
      - Alpaca: daily OHLCV history going back to 2016
      - Finnhub: historical company news for the target date

    - history(): returns daily bars ending the trading day BEFORE the target
      date, so the scanner sees correct prior_high / prior_low.
    - quote(): returns a synthetic quote based on the last premarket bar
      of the target date.
    """

    def __init__(self, target_date: str, interval: str = "5m"):
        """Args:
            target_date: ISO date string (e.g. "2026-08-11") for the premarket
                session to replay.
            interval: Intraday bar interval for premarket data.
                "1m" (7 days back), "5m" (60 days back), "15m" (60 days back).
        """
        self._target = datetime.fromisoformat(target_date).date()
        self._interval = interval
        self._alpaca = AlpacaProvider()
        # yfinance doesn't need API keys — it's free.
        _suppress_yfinance_logging()

        # Cache: symbol -> target-date premarket bars DataFrame (ET timezone)
        self._premarket_cache: dict[str, pd.DataFrame] = {}
        # Cache: symbol -> all available premarket bars in the yfinance window.
        # This is used to compute a no-lookahead historical premarket-volume baseline.
        self._premarket_history_cache: dict[str, pd.DataFrame] = {}
        # Cache: symbol -> daily history DataFrame (up to day before target)
        self._daily_cache: dict[str, pd.DataFrame] = {}

    # ── Public API (matches ArenaMarketDataProvider protocol) ──────────

    def history(self, symbol: str, *, period: str = "3mo",
                interval: str = "1d", **kwargs) -> Optional[pd.DataFrame]:
        """Return daily history ending the trading day before target_date."""
        if interval != "1d":
            # For intraday requests, delegate to Alpaca/yfinance directly.
            return self._alpaca.history(symbol, period=period, interval=interval, **kwargs)

        if symbol not in self._daily_cache:
            self._daily_cache[symbol] = self._fetch_daily_history(symbol, period)
        return self._daily_cache[symbol]

    def prepare(self, symbols: list[str], period: str = "3mo") -> None:
        """Load target-session bars and prior daily history for scanner use."""
        for symbol in symbols:
            self._get_premarket_bars(symbol)
            self.history(symbol, period=period, interval="1d")

    def quote(self, symbol: str) -> Optional[dict[str, Any]]:
        """Return a synthetic quote from the last premarket bar."""
        bars = self._get_premarket_bars(symbol)
        if bars is None or bars.empty:
            return None
        last = bars.iloc[-1]
        close = float(last["Close"])
        spread_pct = self._spread_proxy_pct(bars)
        spread = close * spread_pct / 100
        relative_volume, avg_premarket_volume = self._premarket_volume_stats(symbol)
        return {
            "last": close,
            "bid": close - spread / 2,
            "ask": close + spread / 2,
            "spread": spread,
            "spread_pct": spread_pct,
            "spread_is_estimated": True,
            "spread_method": "10th-percentile-premarket-range-x0.25",
            "premarket_volume": float(bars["Volume"].sum()),
            "avg_premarket_volume": avg_premarket_volume,
            "premarket_relative_volume": relative_volume,
            "timestamp": bars.index[-1].isoformat(),
        }

    def _spread_proxy_pct(self, bars: pd.DataFrame) -> float:
        """Estimate spread without treating a whole 5m candle as the spread.

        Historical OHLCV bars do not contain bid/ask quotes. A candle's full
        high-low range is therefore a biased spread estimate. We use the
        10th percentile of observed premarket ranges and apply a conservative
        25% factor, clamped to a small floor/cap. This is a liquidity proxy,
        not a historical NBBO measurement.
        """
        close = bars["Close"].where(bars["Close"] != 0)
        ranges = ((bars["High"] - bars["Low"]).abs() / close * 100).dropna()
        ranges = ranges[ranges > 0]
        if ranges.empty:
            return 0.01
        return round(min(0.25, max(0.01, float(ranges.quantile(0.10)) * 0.25)), 4)

    def _premarket_volume_stats(self, symbol: str) -> tuple[float, float]:
        """Return target volume relative to average volume on prior PM sessions."""
        frame = self._premarket_history_cache.get(symbol)
        target_bars = self._premarket_cache.get(symbol)
        if frame is None or frame.empty or target_bars is None or target_bars.empty:
            return 0.0, 0.0

        daily_volume = frame.groupby(frame.index.date)["Volume"].sum()
        target_volume = float(target_bars["Volume"].sum())
        prior_volume = daily_volume[daily_volume.index < self._target].tail(20)
        if prior_volume.empty or float(prior_volume.mean()) <= 0:
            return 0.0, round(float(prior_volume.mean()), 2) if not prior_volume.empty else 0.0
        average = float(prior_volume.mean())
        return round(target_volume / average, 3), round(average, 2)

    # ── Mover fetcher for scan() ───────────────────────────────────────

    def mover_fetcher(self) -> list[dict[str, Any]]:
        """Return premarket change_pct for all cached symbols, formatted as movers."""
        movers: list[dict[str, Any]] = []
        for symbol, bars in self._premarket_cache.items():
            if bars is None or bars.empty:
                continue
            daily = self._daily_cache.get(symbol)
            if daily is None or daily.empty:
                continue
            prev_close = float(daily["Close"].iloc[-1])
            pm_close = float(bars["Close"].iloc[-1])
            if prev_close <= 0:
                continue
            change_pct = (pm_close / prev_close - 1) * 100
            movers.append({
                "symbol": symbol,
                "change_pct": round(change_pct, 4),
                "source": "premarket_replay",
            })
        return movers

    # ── News fetcher for scan() ────────────────────────────────────────

    def news_fetcher(self) -> list[dict[str, Any]]:
        """Fetch company news from Finnhub for all cached symbols around the target date."""
        finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
        if not finnhub_key:
            return []

        import requests

        # Search a 3-day window around the target date
        date_from = (self._target - timedelta(days=1)).isoformat()
        date_to = (self._target + timedelta(days=1)).isoformat()

        all_news: list[dict[str, Any]] = []
        for symbol in self._premarket_cache:
            try:
                resp = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={
                        "symbol": symbol,
                        "from": date_from,
                        "to": date_to,
                        "token": finnhub_key,
                    },
                    timeout=10,
                )
                if not resp.ok:
                    continue
                items = resp.json()
                if not isinstance(items, list):
                    continue
                for item in items[:10]:  # cap per symbol
                    all_news.append({
                        "title": item.get("headline", ""),
                        "source": item.get("source", ""),
                        "published_at": str(item.get("datetime", "")),
                        "url": item.get("url", ""),
                        "symbol": symbol,
                    })
            except Exception:
                continue

        logger.info("PremarketReplay: fetched %d news items for %s",
                    len(all_news), self._target)
        return all_news

    # ── Internal helpers ───────────────────────────────────────────────

    def _fetch_daily_history(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """Fetch daily bars ending the trading day BEFORE target_date."""
        # Convert period to explicit start/end relative to target date so
        # Alpaca doesn't interpret "3mo" as "3 months from today".
        period_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}.get(period, 90)
        end_date = self._target.isoformat()
        start_date = (self._target - timedelta(days=period_days)).isoformat()
        frame = self._alpaca.history(symbol, interval="1d",
                                     start=start_date, end=end_date)
        if frame is None or frame.empty:
            # Fallback to yfinance daily
            try:
                import yfinance as yf
                yf_sym = symbol.upper().replace("-USD", "")
                frame = yf.Ticker(yf_sym).history(
                    period=period, interval="1d",
                    auto_adjust=False, raise_errors=False,
                )
                if frame is not None and not frame.empty:
                    frame.index = frame.index.tz_localize("UTC") if frame.index.tz is None else frame.index
            except Exception:
                return None
        if frame is None or frame.empty:
            logger.warning("PremarketReplay: no daily bars before %s for %s", self._target, symbol)
            return None

        # Trim to bars before target_date
        frame = frame.copy()
        frame["_date"] = frame.index.date
        trimmed = frame[frame["_date"] < self._target].drop(columns=["_date"])
        if trimmed.empty:
            logger.warning("PremarketReplay: no daily bars before %s for %s", self._target, symbol)
            return None
        return trimmed

    def _get_premarket_bars(self, symbol: str) -> Optional[pd.DataFrame]:
        """Fetch and cache intraday premarket bars for the target date via yfinance."""
        if symbol in self._premarket_cache:
            return self._premarket_cache[symbol]

        try:
            import yfinance as yf
        except ImportError:
            logger.warning("PremarketReplay: yfinance not installed")
            self._premarket_cache[symbol] = pd.DataFrame()
            return None

        yf_sym = symbol.upper().replace("-USD", "")
        yf_period = _YF_INTERVAL_PERIOD_MAP.get(self._interval, "60d")

        try:
            ticker = yf.Ticker(yf_sym)
            frame = ticker.history(
                period=yf_period, interval=self._interval,
                prepost=True, auto_adjust=False, raise_errors=False,
            )
        except Exception as exc:
            logger.warning("PremarketReplay: yfinance fetch failed for %s: %s", symbol, exc)
            self._premarket_cache[symbol] = pd.DataFrame()
            return None

        if frame is None or frame.empty:
            logger.warning("PremarketReplay: no yfinance bars for %s", symbol)
            self._premarket_cache[symbol] = pd.DataFrame()
            return None

        # Filter to premarket hours in Eastern time. yfinance returns data in
        # the exchange timezone for US equities.
        if frame.index.tz is None:
            frame.index = frame.index.tz_localize(_ET)
        frame_et = frame.tz_convert(_ET) if frame.index.tz else frame
        all_premarket = frame_et.between_time(_PREMARKET_START, _PREMARKET_END)
        self._premarket_history_cache[symbol] = all_premarket

        # Retain prior sessions for the historical volume baseline, but expose
        # only the target date to the replay provider.
        target_mask = all_premarket.index.date == self._target
        premarket = all_premarket[target_mask]
        if premarket.empty:
            logger.warning("PremarketReplay: no bars for %s on %s (outside %s window)",
                         symbol, self._target, yf_period)
            self._premarket_cache[symbol] = pd.DataFrame()
            return None

        self._premarket_cache[symbol] = premarket
        return premarket

    def premarket_summary(self, symbol: str) -> dict[str, Any]:
        """Return a summary of the premarket session for a symbol."""
        bars = self._get_premarket_bars(symbol)
        daily = self._daily_cache.get(symbol)
        if daily is None:
            daily = self._fetch_daily_history(symbol, "3mo")
        if bars is None or bars.empty or daily is None or daily.empty:
            return {}
        self._daily_cache[symbol] = daily
        prev_close = float(daily["Close"].iloc[-1])
        pm_open = float(bars["Open"].iloc[0])
        pm_close = float(bars["Close"].iloc[-1])
        pm_high = float(bars["High"].max())
        pm_low = float(bars["Low"].min())
        pm_volume = int(bars["Volume"].sum())
        change_pct = (pm_close / prev_close - 1) * 100 if prev_close else 0.0
        return {
            "symbol": symbol,
            "date": str(self._target),
            "prev_close": round(prev_close, 4),
            "pm_open": round(pm_open, 4),
            "pm_close": round(pm_close, 4),
            "pm_high": round(pm_high, 4),
            "pm_low": round(pm_low, 4),
            "pm_volume": pm_volume,
            "change_pct": round(change_pct, 4),
            "bar_count": len(bars),
            "first_bar": bars.index[0].isoformat(),
            "last_bar": bars.index[-1].isoformat(),
        }
