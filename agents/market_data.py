"""
Market Data Module

Fetches news from the AI-Trader platform API and price/technical data
from yfinance. Provides a unified interface for agents to get both
fundamental (news/sentiment) and technical (charts/indicators) data.
"""

import os
import requests
import time
from typing import Optional
from dataclasses import dataclass, field


# ============================================================
# Technical Analysis Data Structure
# ============================================================

@dataclass
class TechnicalSnapshot:
    symbol: str
    price: float
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    sma_20: float
    sma_50: Optional[float]
    bollinger_upper: float
    bollinger_lower: float
    bollinger_mid: float
    support: float
    resistance: float
    return_5d: Optional[float]
    return_20d: Optional[float]
    volume: float
    avg_volume: float
    atr: Optional[float] = None
    signals: list[str] = field(default_factory=list)

    @property
    def is_oversold(self) -> bool:
        return self.rsi < 30 or self.price < self.bollinger_lower

    @property
    def is_overbought(self) -> bool:
        return self.rsi > 70 or self.price > self.bollinger_upper

    @property
    def is_bullish(self) -> bool:
        return self.price > self.sma_20 and self.macd_histogram > 0

    @property
    def is_bearish(self) -> bool:
        return self.price < self.sma_20 and self.macd_histogram < 0

    @property
    def confidence(self) -> float:
        """Return a 0-1 confidence score based on signal confluence."""
        score = 0.0
        checks = 0
        if self.rsi < 30:
            score += 1; checks += 1
        elif self.rsi > 70:
            score += 0; checks += 1
        else:
            score += 0.5; checks += 1
        if self.macd_histogram > 0:
            score += 1; checks += 1
        else:
            score += 0; checks += 1
        if self.price > self.sma_20:
            score += 1; checks += 1
        else:
            score += 0; checks += 1
        if self.price > self.bollinger_mid:
            score += 0.5; checks += 1
        else:
            score += 0; checks += 1
        if self.return_5d and self.return_5d > 0:
            score += 0.5; checks += 1
        else:
            score += 0; checks += 1
        return score / checks if checks > 0 else 0.5


# ============================================================
# News Item Data Structure
# ============================================================

@dataclass
class NewsItem:
    title: str
    sentiment_label: str
    sentiment_score: float
    tickers: list[str]
    category: str
    summary: str
    url: str
    time_published: str

    @property
    def is_bullish(self) -> bool:
        return "Bullish" in self.sentiment_label

    @property
    def is_bearish(self) -> bool:
        return "Bearish" in self.sentiment_label

    @property
    def is_strong(self) -> bool:
        return abs(self.sentiment_score) > 0.2


# ============================================================
# Market Data Client
# ============================================================

class MarketDataClient:
    """Unified client for fetching market data from the platform and yfinance."""

    # Finnhub API configuration (free tier: 60 calls/min)
    FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
    FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_base: str = "http://localhost:8000/api"):
        self.api_base = api_base
        self._yf_available = None
        self._price_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price, timestamp)
        self._cache_ttl = 60  # seconds

    def fetch_news(self, limit: int = 20) -> list[NewsItem]:
        """Fetch financial news from the platform API."""
        try:
            resp = requests.get(f"{self.api_base}/market-intel/news?limit={limit}", timeout=10)
            if not resp.ok:
                return []
            data = resp.json()
            items = []
            for cat in data.get("categories", []):
                cat_name = cat.get("category", "unknown")
                for item in cat.get("items", []):
                    tickers = [t.get("ticker", "").replace("CRYPTO:", "").replace("FOREX:", "")
                               for t in item.get("ticker_sentiment", []) if t.get("ticker")]
                    items.append(NewsItem(
                        title=item.get("title", ""),
                        sentiment_label=item.get("overall_sentiment_label", "Neutral"),
                        sentiment_score=item.get("overall_sentiment_score", 0),
                        tickers=tickers,
                        category=cat_name,
                        summary=item.get("summary", ""),
                        url=item.get("url", ""),
                        time_published=item.get("time_published", ""),
                    ))
            return items
        except Exception:
            return []

    def fetch_crypto_price(self, symbol: str) -> Optional[float]:
        """Fetch current crypto price via the platform API (Hyperliquid)."""
        cached = self._price_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self._cache_ttl:
            return cached[0]
        try:
            resp = requests.get(f"{self.api_base}/market-intel/overview", timeout=10)
            if resp.ok:
                data = resp.json()
                for coin in data.get("crypto", {}).get("coins", []):
                    if coin.get("symbol", "").upper() == symbol.upper():
                        price = float(coin.get("price", 0))
                        if price > 0:
                            self._price_cache[symbol] = (price, time.time())
                            return price
        except Exception:
            pass
        return None

    def fetch_technical(self, symbol: str) -> Optional[TechnicalSnapshot]:
        """Fetch technical analysis using yfinance, with Finnhub fallback."""
        snapshot = self._fetch_technical_yfinance(symbol)
        if snapshot is not None:
            return snapshot

        # yfinance failed or rate-limited — try Finnhub
        snapshot = self._fetch_technical_finnhub(symbol)
        if snapshot is not None:
            return snapshot

        return None

    def _fetch_technical_yfinance(self, symbol: str) -> Optional[TechnicalSnapshot]:
        """Fetch technical analysis using yfinance."""
        try:
            import yfinance as yf
        except ImportError:
            return None

        yf_symbol = self._normalize_symbol(symbol)
        try:
            df = yf.download(yf_symbol, period="3mo", interval="1d", progress=False)
        except Exception:
            return None

        if df is None or df.empty:
            return None

        closes = df["Close"].squeeze().dropna()
        volumes = df["Volume"].squeeze().dropna() if "Volume" in df else None
        highs = df["High"].squeeze().dropna() if "High" in df else None
        lows = df["Low"].squeeze().dropna() if "Low" in df else None

        if len(closes) < 20:
            return None

        latest = closes.iloc[-1]
        if hasattr(latest, "item"):
            latest = latest.item()
        latest = float(latest)

        sma_20 = float(closes.iloc[-20:].mean())
        sma_50 = float(closes.iloc[-min(50, len(closes)):].mean()) if len(closes) >= 50 else None

        # RSI (14-period)
        deltas = closes.diff().dropna()
        gains = deltas.clip(lower=0)
        losses = deltas.clip(upper=0).abs()
        avg_gain = float(gains.iloc[-14:].mean())
        avg_loss = float(losses.iloc[-14:].mean())
        rsi = 100 - (100 / (1 + (avg_gain / avg_loss if avg_loss > 0 else 999)))

        # MACD (12, 26, 9)
        ema_12 = closes.ewm(span=12, adjust=False).mean()
        ema_26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        macd_hist = macd_val - signal_val

        # Bollinger Bands
        bb_std = float(closes.iloc[-20:].std())
        bb_upper = sma_20 + 2 * bb_std
        bb_lower = sma_20 - 2 * bb_std

        # Support/Resistance
        recent = closes.iloc[-20:]
        support = float(recent.min())
        resistance = float(recent.max())

        # Returns
        ret_5d = ((latest - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100) if len(closes) >= 6 else None
        ret_20d = ((latest - float(closes.iloc[-21])) / float(closes.iloc[-21]) * 100) if len(closes) >= 21 else None

        # Volume
        vol = float(volumes.iloc[-1]) if volumes is not None and len(volumes) > 0 else 0
        avg_vol = float(volumes.iloc[-20:].mean()) if volumes is not None and len(volumes) >= 20 else vol

        # ATR (14-period)
        atr = self._calculate_atr(highs, lows, closes)

        # Build signals
        signals = []
        if rsi < 30:
            signals.append("RSI oversold")
        elif rsi > 70:
            signals.append("RSI overbought")
        if macd_hist > 0:
            signals.append("MACD bullish")
        else:
            signals.append("MACD bearish")
        if latest > sma_20:
            signals.append("Above SMA20")
        else:
            signals.append("Below SMA20")
        if latest < bb_lower:
            signals.append("Below lower BB")
        elif latest > bb_upper:
            signals.append("Above upper BB")
        if sma_50 and latest > sma_50:
            signals.append("Above SMA50")
        elif sma_50:
            signals.append("Below SMA50")

        return TechnicalSnapshot(
            symbol=symbol,
            price=latest,
            rsi=rsi,
            macd=macd_val,
            macd_signal=signal_val,
            macd_histogram=macd_hist,
            sma_20=sma_20,
            sma_50=sma_50,
            bollinger_upper=bb_upper,
            bollinger_lower=bb_lower,
            bollinger_mid=sma_20,
            support=support,
            resistance=resistance,
            return_5d=ret_5d,
            return_20d=ret_20d,
            volume=vol,
            avg_volume=avg_vol,
            atr=atr,
            signals=signals,
        )

    def _fetch_technical_finnhub(self, symbol: str) -> Optional[TechnicalSnapshot]:
        """Fallback: Fetch technical analysis using Finnhub API (free tier: 60 calls/min)."""
        if not self.FINNHUB_API_KEY:
            return None

        finnhub_symbol = self._normalize_symbol_finnhub(symbol)
        if not finnhub_symbol:
            return None

        try:
            import pandas as pd
        except ImportError:
            return None

        # Fetch ~90 calendar days of daily candles
        end_ts = int(time.time())
        start_ts = end_ts - 90 * 24 * 3600

        try:
            resp = requests.get(
                f"{self.FINNHUB_BASE_URL}/stock/candle",
                params={
                    "symbol": finnhub_symbol,
                    "resolution": "D",
                    "from": start_ts,
                    "to": end_ts,
                    "token": self.FINNHUB_API_KEY,
                },
                timeout=10,
            )
            if not resp.ok:
                return None
            data = resp.json()
        except Exception:
            return None

        if not data or data.get("s") != "ok":
            return None

        try:
            df = pd.DataFrame({
                "Close": data["c"],
                "High": data["h"],
                "Low": data["l"],
                "Volume": data["v"],
            })
        except (KeyError, TypeError):
            return None

        closes = df["Close"].dropna()
        volumes = df["Volume"].dropna()
        highs = df["High"].dropna()
        lows = df["Low"].dropna()

        if len(closes) < 20:
            return None

        latest = float(closes.iloc[-1])
        sma_20 = float(closes.iloc[-20:].mean())
        sma_50 = float(closes.iloc[-min(50, len(closes)):].mean()) if len(closes) >= 50 else None

        # RSI (14-period)
        deltas = closes.diff().dropna()
        gains = deltas.clip(lower=0)
        losses = deltas.clip(upper=0).abs()
        avg_gain = float(gains.iloc[-14:].mean())
        avg_loss = float(losses.iloc[-14:].mean())
        rsi = 100 - (100 / (1 + (avg_gain / avg_loss if avg_loss > 0 else 999)))

        # MACD (12, 26, 9)
        ema_12 = closes.ewm(span=12, adjust=False).mean()
        ema_26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])
        macd_hist = macd_val - signal_val

        # Bollinger Bands
        bb_std = float(closes.iloc[-20:].std())
        bb_upper = sma_20 + 2 * bb_std
        bb_lower = sma_20 - 2 * bb_std

        # Support/Resistance
        recent = closes.iloc[-20:]
        support = float(recent.min())
        resistance = float(recent.max())

        # Returns
        ret_5d = ((latest - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100) if len(closes) >= 6 else None
        ret_20d = ((latest - float(closes.iloc[-21])) / float(closes.iloc[-21]) * 100) if len(closes) >= 21 else None

        # Volume
        vol = float(volumes.iloc[-1]) if len(volumes) > 0 else 0
        avg_vol = float(volumes.iloc[-20:].mean()) if len(volumes) >= 20 else vol

        # ATR (14-period)
        atr = self._calculate_atr(highs, lows, closes)

        # Build signals
        signals = []
        if rsi < 30:
            signals.append("RSI oversold")
        elif rsi > 70:
            signals.append("RSI overbought")
        if macd_hist > 0:
            signals.append("MACD bullish")
        else:
            signals.append("MACD bearish")
        if latest > sma_20:
            signals.append("Above SMA20")
        else:
            signals.append("Below SMA20")
        if latest < bb_lower:
            signals.append("Below lower BB")
        elif latest > bb_upper:
            signals.append("Above upper BB")
        if sma_50 and latest > sma_50:
            signals.append("Above SMA50")
        elif sma_50:
            signals.append("Below SMA50")

        return TechnicalSnapshot(
            symbol=symbol,
            price=latest,
            rsi=rsi,
            macd=macd_val,
            macd_signal=signal_val,
            macd_histogram=macd_hist,
            sma_20=sma_20,
            sma_50=sma_50,
            bollinger_upper=bb_upper,
            bollinger_lower=bb_lower,
            bollinger_mid=sma_20,
            support=support,
            resistance=resistance,
            return_5d=ret_5d,
            return_20d=ret_20d,
            volume=vol,
            avg_volume=avg_vol,
            atr=atr,
            signals=signals,
        )

    @staticmethod
    def _calculate_atr(highs, lows, closes, period: int = 14) -> Optional[float]:
        """Calculate Average True Range (ATR)."""
        try:
            import pandas as pd
        except ImportError:
            return None
        if highs is None or lows is None or closes is None:
            return None
        if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
            return None

        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        val = atr.iloc[-1]
        if hasattr(val, "item"):
            val = val.item()
        return float(val) if pd.notna(val) else None

    @staticmethod
    def _normalize_symbol_finnhub(symbol: str) -> Optional[str]:
        """Convert platform symbol to Finnhub symbol."""
        s = symbol.strip().upper()
        # Finnhub doesn't support crypto candles on free tier for all pairs
        crypto_map = {"BTC": None, "ETH": None, "SOL": None, "DOGE": None,
                      "AVAX": None, "ADA": None, "DOT": None, "LINK": None,
                      "MATIC": None}
        if s in crypto_map:
            return None  # Crypto uses Hyperliquid, not Finnhub
        return s

    def fetch_trending(self) -> list[dict]:
        """Fetch trending symbols from the platform."""
        try:
            resp = requests.get(f"{self.api_base}/trending?limit=10", timeout=10)
            if resp.ok:
                data = resp.json()
                return data.get("symbols", [])
        except Exception:
            pass
        return []

    def fetch_leaderboard(self) -> list[dict]:
        """Fetch top agents from the leaderboard."""
        try:
            resp = requests.get(f"{self.api_base}/profit/history?limit=20&include_history=false", timeout=10)
            if resp.ok:
                data = resp.json()
                return data.get("top_agents", [])
        except Exception:
            pass
        return []

    def fetch_signals_feed(self, limit: int = 20) -> list[dict]:
        """Fetch recent signals from the platform feed."""
        try:
            resp = requests.get(f"{self.api_base}/signals/feed?limit={limit}", timeout=10)
            if resp.ok:
                data = resp.json()
                return data.get("signals", [])
        except Exception:
            pass
        return []

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert platform symbol to yfinance symbol."""
        s = symbol.strip().upper()
        crypto_map = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                      "DOGE": "DOGE-USD", "AVAX": "AVAX-USD", "ADA": "ADA-USD",
                      "DOT": "DOT-USD", "LINK": "LINK-USD", "MATIC": "MATIC-USD"}
        return crypto_map.get(s, s)
