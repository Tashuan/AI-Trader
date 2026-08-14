"""Deterministic premarket candidate scanner.

Builds an AI-ready watchlist from movers, configured symbols, quotes, daily
history, liquidity, trend, and proximity to prior-day levels. It ranks and
filters candidates but never creates orders or trade signals.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from alpaca_realtime_provider import get_alpaca_provider
from arena_market_data import ArenaMarketDataProvider, get_arena_market_data
from schwab_provider import get_schwab_provider

DEFAULT_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS", "BABA",
    "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL", "F", "GM", "NIO",
    "XPEV", "PLUG", "DKNG",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "universe": DEFAULT_UNIVERSE,
    "max_candidates": 15,
    "min_price": 5.0,
    "max_price": 1000.0,
    "min_avg_dollar_volume": 25_000_000,
    "max_spread_pct": 0.25,
    "min_gap_pct": 1.0,
    "min_relative_volume": 1.25,
    "history_period": "3mo",
    "history_interval": "1d",
    "min_history_bars": 30,
    "proximity_pct": 1.0,
    "min_score": 35.0,
}

@dataclass
class Candidate:
    symbol: str
    score: float
    status: str
    direction: str
    sources: list[str]
    price: float
    change_pct: float
    relative_volume: float
    avg_dollar_volume: float
    spread_pct: float
    prior_high: float
    prior_low: float
    distance_to_prior_high_pct: float
    distance_to_prior_low_pct: float
    trend: str
    evidence: list[str]
    risks: list[str]
    news: list[dict[str, Any]]
    ai_context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if pd.notna(result) else default
    except (TypeError, ValueError):
        return default


def _news_index(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        text = " ".join(str(item.get(k, "")) for k in ("title", "content", "summary"))
        symbols = set()
        for key in ("symbol", "ticker"):
            value = str(item.get(key, "")).upper().strip()
            if value.isalpha() and 1 < len(value) <= 5:
                symbols.add(value)
        symbols.update(re.findall(r"\$([A-Z]{1,5})\b", text.upper()))
        record = {
            "title": item.get("title", ""),
            "source": item.get("source", item.get("publisher", "")),
            "published_at": item.get("published_at", item.get("timestamp", "")),
            "url": item.get("url", ""),
        }
        for symbol in symbols:
            indexed.setdefault(symbol, []).append(record)
    return indexed


def fetch_news(token: str | None = None,
               fetcher: Callable[[], list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    if fetcher:
        return fetcher() or []
    if not token:
        return []
    try:
        request = urllib.request.Request(
            "http://localhost:8000/api/market/news?limit=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        return payload if isinstance(payload, list) else payload.get("news", payload.get("items", []))
    except Exception:
        return []


def fetch_movers(config: dict[str, Any],
                 fetcher: Callable[[], list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    if fetcher:
        return fetcher() or []
    if not config.get("movers_enabled", True):
        return []
    movers: list[dict[str, Any]] = []
    schwab = get_schwab_provider()
    if schwab.is_configured:
        try:
            movers.extend(schwab.movers_all())
        except Exception:
            pass
    if not movers:
        alpaca = get_alpaca_provider()
        if alpaca.is_configured:
            try:
                movers.extend(alpaca.screen_movers(config["universe"], config["max_candidates"] * 2))
            except Exception:
                pass
    return movers


def _history_metrics(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, float | str] | None:
    required = {"High", "Low", "Close", "Volume"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return None
    frame = frame.dropna(subset=list(required))
    if len(frame) < int(config["min_history_bars"]):
        return None
    close = _num(frame["Close"].iloc[-1])
    prior = frame.iloc[:-1]
    prior_high = _num(prior["High"].iloc[-1])
    prior_low = _num(prior["Low"].iloc[-1])
    avg_volume = _num(prior["Volume"].tail(20).mean())
    avg_dollar_volume = close * avg_volume
    relative_volume = _num(frame["Volume"].iloc[-1]) / avg_volume if avg_volume else 0.0
    sma20 = _num(frame["Close"].tail(20).mean())
    trend = "bullish" if close > sma20 else "bearish" if close < sma20 else "neutral"
    return {
        "price": close, "prior_high": prior_high, "prior_low": prior_low,
        "avg_dollar_volume": avg_dollar_volume, "relative_volume": relative_volume,
        "trend": trend,
    }


def _score(metrics: dict[str, Any], quote: dict[str, Any], change_pct: float,
           news: list[dict[str, Any]], config: dict[str, Any]) -> tuple[float, str, list[str], list[str], float]:
    price = float(metrics["price"])
    spread_pct = _num(quote.get("spread_pct"))
    if not spread_pct and _num(quote.get("spread")) and price:
        spread_pct = _num(quote.get("spread")) / price * 100
    direction = "long" if change_pct >= 0 else "short"
    evidence: list[str] = []
    risks: list[str] = []
    score = 0.0
    if abs(change_pct) >= float(config["min_gap_pct"]):
        score += min(25.0, abs(change_pct) * 5)
        evidence.append(f"momentum change {change_pct:+.2f}%")
    else:
        risks.append("weak directional move")
    rv = float(metrics["relative_volume"])
    volume_label = str(metrics.get("relative_volume_label", "relative volume"))
    volume_risk = "premarket volume not elevated" if volume_label == "premarket relative volume" else "volume not elevated"
    if rv >= float(config["min_relative_volume"]):
        score += min(20.0, rv * 6)
        evidence.append(f"{volume_label} {rv:.2f}x")
    else:
        risks.append(volume_risk)
    adv = float(metrics["avg_dollar_volume"])
    if adv >= float(config["min_avg_dollar_volume"]):
        score += 20.0
        evidence.append(f"average dollar volume ${adv:,.0f}")
    else:
        risks.append("insufficient average dollar volume")
    if spread_pct <= float(config["max_spread_pct"]):
        score += 15.0
        evidence.append(f"spread {spread_pct:.3f}%")
    else:
        risks.append(f"wide spread {spread_pct:.3f}%")
    level = float(metrics["prior_high"] if direction == "long" else metrics["prior_low"])
    distance = abs(price - level) / level * 100 if level else 999.0
    if distance <= float(config["proximity_pct"]):
        score += 15.0
        evidence.append(f"within {distance:.2f}% of prior-day level")
    else:
        risks.append("not near prior-day breakout level")
    if (direction == "long" and metrics["trend"] == "bullish") or (direction == "short" and metrics["trend"] == "bearish"):
        score += 5.0
        evidence.append(f"{metrics['trend']} SMA20 context agrees")
    else:
        risks.append("SMA20 context disagrees with move")
    if news:
        score += 5.0
        evidence.append(f"{len(news)} associated news item(s)")
    else:
        risks.append("no indexed news catalyst")
    return round(score, 2), direction, evidence, risks, round(spread_pct, 4)


def scan(config: dict[str, Any] | None = None, *,
         provider: ArenaMarketDataProvider | None = None,
         symbols: list[str] | None = None, token: str | None = None,
         news_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
         mover_fetcher: Callable[[], list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Return a ranked, structured watchlist for the opening-range monitor."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    provider = provider or get_arena_market_data()
    universe = [s.upper() for s in (symbols or cfg["universe"])]
    discovered: dict[str, dict[str, Any]] = {}
    for mover in fetch_movers(cfg, mover_fetcher):
        symbol = str(mover.get("symbol", "")).upper().strip()
        if symbol:
            row = discovered.setdefault(symbol, {"sources": set(), "change_pct": 0.0})
            row["sources"].add(str(mover.get("source", "movers")))
            change = _num(mover.get("change_pct", mover.get("change", 0)))
            if abs(change) > abs(row["change_pct"]):
                row["change_pct"] = change
    for symbol in universe:
        discovered.setdefault(symbol, {"sources": set(), "change_pct": 0.0})["sources"].add("configured_universe")
    news_by_symbol = _news_index(fetch_news(token, news_fetcher))
    candidates: list[Candidate] = []
    for symbol, source in discovered.items():
        try:
            frame = provider.history(symbol, period=cfg["history_period"], interval=cfg["history_interval"])
            metrics = _history_metrics(frame, cfg)
            if not metrics:
                continue
            price = _num((provider.quote(symbol) or {}).get("last"), float(metrics["price"]))
            if not (float(cfg["min_price"]) <= price <= float(cfg["max_price"])):
                continue
            metrics["price"] = price
            change_pct = _num(source["change_pct"])
            if not change_pct and float(metrics["prior_high"]):
                change_pct = (price / float(metrics["prior_high"]) - 1) * 100
            quote = provider.quote(symbol) or {}
            # Historical replay providers can supply a premarket-specific
            # volume baseline. Prefer it over daily-volume relative volume
            # when available; live providers continue using daily metrics.
            if "premarket_relative_volume" in quote:
                metrics["relative_volume"] = _num(quote["premarket_relative_volume"])
                metrics["relative_volume_label"] = "premarket relative volume"
            news = news_by_symbol.get(symbol, [])
            score, direction, evidence, risks, spread_pct = _score(metrics, quote, change_pct, news, cfg)
            blocked = any("insufficient" in risk or "wide spread" in risk for risk in risks)
            status = "monitor" if score >= float(cfg["min_score"]) and not blocked else "watch"
            candidates.append(Candidate(
                symbol=symbol, score=score, status=status, direction=direction,
                sources=sorted(source["sources"]), price=round(price, 4),
                change_pct=round(change_pct, 4), relative_volume=round(float(metrics["relative_volume"]), 3),
                avg_dollar_volume=round(float(metrics["avg_dollar_volume"]), 2), spread_pct=spread_pct,
                prior_high=round(float(metrics["prior_high"]), 4), prior_low=round(float(metrics["prior_low"]), 4),
                distance_to_prior_high_pct=round(abs(price - float(metrics["prior_high"])) / float(metrics["prior_high"]) * 100, 4) if float(metrics["prior_high"]) else 0,
                distance_to_prior_low_pct=round(abs(price - float(metrics["prior_low"])) / float(metrics["prior_low"]) * 100, 4) if float(metrics["prior_low"]) else 0,
                trend=str(metrics["trend"]), evidence=evidence, risks=risks, news=news,
                ai_context={"needs_catalyst_review": not bool(news), "thesis": "", "failure_case": "", "confidence": None},
            ))
        except Exception:
            continue
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    watchlist = candidates[:int(cfg["max_candidates"])]
    return {
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "scanner": "premarket_breakout_candidate_scanner",
        "config": cfg,
        "watchlist": [candidate.to_dict() for candidate in watchlist],
        "candidate_count": len(candidates),
        "monitor_count": sum(candidate.status == "monitor" for candidate in watchlist),
        "limitations": [
            "Premarket volume and quotes depend on provider entitlements.",
            "This ranks candidates; it does not confirm a Fence Bar breakout or place orders.",
            "AI context is intentionally empty until a separate analyst is added.",
        ],
    }
