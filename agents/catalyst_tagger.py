"""Catalyst tagger module — keyword-based news classification.

Tags news headlines with catalyst types and a bullish/bearish/neutral bias.
Used by the live scanner and backtester to boost/cull setups based on
whether a stock has a fresh catalyst.

Catalyst categories:
  - earnings: earnings beat/miss, guidance, revenue
  - fda: FDA approval, trial results, clinical data
  - merger: M&A, acquisition, buyout, merger
  - upgrade: analyst upgrade/downgrade, price target
  - product: product launch, new release, partnership
  - macro: Fed, CPI, jobs report, tariff, geopolitical
  - offering: secondary offering, dilution, share sale
  - halt: trading halt, delisting, investigation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class CatalystTag:
    """A single catalyst classification result."""
    category: str
    bias: str  # "bullish", "bearish", "neutral"
    confidence: float  # 0.0 - 1.0
    keywords_matched: list[str] = field(default_factory=list)
    headline: str = ""
    timestamp: str = ""


# ── Keyword patterns ──────────────────────────────────────────────

_CATALYST_PATTERNS: dict[str, list[tuple[list[str], str, float]]] = {
    "earnings": [
        (["earnings beat", "beats earnings", "earnings surprise", "tops estimates",
          "revenue beat", "EPS beat", "raises guidance", "strong guidance",
          "raises outlook", "upgrades guidance"], "bullish", 0.85),
        (["earnings miss", "misses earnings", "misses estimates", "revenue miss",
          "EPS miss", "cuts guidance", "lowers guidance", "weak guidance",
          "cuts outlook", "warns on"], "bearish", 0.85),
        (["earnings", "quarterly results", "Q1", "Q2", "Q3", "Q4",
          "revenue", "EPS", "guidance", "outlook"], "neutral", 0.50),
    ],
    "fda": [
        (["FDA approves", "approval granted", "phase 3 success", "positive trial",
          "clinical trial success", "breakthrough designation", "fast track",
          "positive data", "positive results", "endpoints met"], "bullish", 0.90),
        (["FDA rejects", "clinical trial failure", "phase 3 failure", "negative data",
          "trial halted", "adverse events", "complete response letter",
          "FDA delay", "rejected"], "bearish", 0.90),
        (["FDA", "clinical trial", "phase 1", "phase 2", "phase 3",
          "trial results", "biologics", "drug application"], "neutral", 0.50),
    ],
    "merger": [
        (["acquires", "to acquire", "acquisition of", "buyout", "merger agreement",
          "takeover", "deal to buy", "agrees to buy", "merger deal"], "bullish", 0.75),
        (["acquisition falls through", "merger terminated", "deal collapses",
          "antitrust blocks", "regulators block"], "bearish", 0.75),
        (["merger", "acquisition", "M&A", "buyout", "takeover",
          "consolidation"], "neutral", 0.50),
    ],
    "upgrade": [
        (["upgrade to buy", "upgrades to overweight", "raises price target",
          "raises rating", "initiates with buy", "initiates with overweight",
          "bullish initiation"], "bullish", 0.70),
        (["downgrade to sell", "downgrades to underweight", "cuts price target",
          "cuts rating", "initiates with sell", "bearish initiation",
          "reduces price target"], "bearish", 0.70),
        (["analyst", "price target", "rating", "upgrade", "downgrade",
          "outperform", "underperform"], "neutral", 0.40),
    ],
    "product": [
        (["launches new", "unveils", "introduces", "debut", "rolls out",
          "partnership with", "strategic partnership", "collaboration",
          "new contract", "wins contract", "selected by"], "bullish", 0.65),
        (["recalls", "product defect", "safety issue", "halted production",
          "discontinues"], "bearish", 0.65),
        (["product", "launch", "partnership", "contract", "collaboration",
          "unveils", "announces"], "neutral", 0.40),
    ],
    "macro": [
        (["rate cut", "dovish", "stimulus", "infrastructure bill",
          "tax cut", "deregulation"], "bullish", 0.60),
        (["rate hike", "hawkish", "inflation surge", "recession fears",
          "tariff increase", "trade war", "sanctions", "geopolitical tension"], "bearish", 0.60),
        (["Fed", "CPI", "jobs report", "tariff", "inflation", "recession",
          "GDP", "unemployment", "interest rate", "treasury"], "neutral", 0.40),
    ],
    "offering": [
        (["secondary offering", "share offering", "dilution", "raises capital",
          "common stock offering", "at-the-market offering"], "bearish", 0.75),
        (["buyback", "share repurchase", "repurchase program",
          "authorizes buyback"], "bullish", 0.70),
    ],
    "halt": [
        (["trading halt", "halted", "delisting", "investigation",
          "SEC probe", "DOJ investigation", "fraud", "accounting irregularity",
          "class action", "lawsuit"], "bearish", 0.85),
    ],
}


def tag_headline(headline: str, timestamp: str = "") -> CatalystTag | None:
    """Classify a single news headline into a catalyst tag.

    Returns None if no catalyst keywords match.
    """
    if not headline:
        return None

    headline_lower = headline.lower()

    best_tag: CatalystTag | None = None
    best_confidence = 0.0

    for category, patterns in _CATALYST_PATTERNS.items():
        for keywords, bias, base_confidence in patterns:
            for kw in keywords:
                if kw.lower() in headline_lower:
                    if base_confidence > best_confidence:
                        best_confidence = base_confidence
                        best_tag = CatalystTag(
                            category=category,
                            bias=bias,
                            confidence=base_confidence,
                            keywords_matched=[kw],
                            headline=headline,
                            timestamp=timestamp,
                        )
                    break  # Only match once per pattern group

    return best_tag


def tag_news_items(
    news_items: list[dict[str, Any]],
    symbol: str | None = None,
) -> list[CatalystTag]:
    """Tag a list of news items (dicts with 'headline'/'title' and 'timestamp'/'date').

    Optionally filter by symbol if the news item has a 'symbols' field.
    """
    tags: list[CatalystTag] = []
    for item in news_items:
        headline = item.get("headline") or item.get("title") or ""
        timestamp = item.get("timestamp") or item.get("date") or ""
        if symbol:
            item_symbols = item.get("symbols", [])
            if item_symbols and symbol.upper() not in [s.upper() for s in item_symbols]:
                continue
        tag = tag_headline(headline, str(timestamp))
        if tag is not None:
            tags.append(tag)
    return tags


def get_catalyst_bias(
    tags: list[CatalystTag],
    max_age_hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate catalyst tags into a single bias signal.

    Returns {"bias": "bullish"/"bearish"/"neutral", "score": float,
             "categories": list[str], "top_tag": CatalystTag|None}
    """
    if not tags:
        return {"bias": "neutral", "score": 0.0, "categories": [], "top_tag": None}

    now = now or datetime.utcnow()
    bullish_score = 0.0
    bearish_score = 0.0
    categories: set[str] = set()
    top_tag: CatalystTag | None = None
    top_confidence = 0.0

    for tag in tags:
        # Age decay: older news has less weight
        if tag.timestamp:
            try:
                tag_time = datetime.fromisoformat(tag.timestamp.replace("Z", "+00:00"))
                age_hours = (now - tag_time.replace(tzinfo=None)).total_seconds() / 3600
                if age_hours > max_age_hours:
                    continue
                decay = max(0.1, 1.0 - (age_hours / max_age_hours) * 0.5)
            except Exception:
                decay = 1.0
        else:
            decay = 1.0

        weighted = tag.confidence * decay
        if tag.bias == "bullish":
            bullish_score += weighted
        elif tag.bias == "bearish":
            bearish_score += weighted
        categories.add(tag.category)

        if tag.confidence > top_confidence:
            top_confidence = tag.confidence
            top_tag = tag

    net = bullish_score - bearish_score
    if net > 0.3:
        bias = "bullish"
    elif net < -0.3:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "bias": bias,
        "score": round(net, 2),
        "bullish_score": round(bullish_score, 2),
        "bearish_score": round(bearish_score, 2),
        "categories": sorted(categories),
        "top_tag": top_tag,
    }


def has_fresh_catalyst(
    tags: list[CatalystTag],
    max_age_hours: int = 4,
    now: datetime | None = None,
) -> bool:
    """Check if there's a high-confidence catalyst within the freshness window."""
    now = now or datetime.utcnow()
    for tag in tags:
        if tag.confidence < 0.60:
            continue
        if tag.timestamp:
            try:
                tag_time = datetime.fromisoformat(tag.timestamp.replace("Z", "+00:00"))
                age_hours = (now - tag_time.replace(tzinfo=None)).total_seconds() / 3600
                if age_hours <= max_age_hours:
                    return True
            except Exception:
                return True  # If we can't parse time, assume fresh
        else:
            return True
    return False
