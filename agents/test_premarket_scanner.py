"""Tests for the standalone premarket candidate scanner."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from premarket_scanner import DEFAULT_CONFIG, _history_metrics, _news_index, scan
from strategy_lab import deep_merge


def _history(price: float = 140.0, trend: float = 1.0) -> pd.DataFrame:
    rows = []
    for index in range(40):
        close = 100 + index * trend
        rows.append({"Open": close - 0.5, "High": close + 1, "Low": close - 1,
                     "Close": close, "Volume": 100_000})
    rows[-1]["Close"] = price
    rows[-1]["High"] = price + 1
    rows[-1]["Low"] = price - 1
    rows[-1]["Volume"] = 300_000
    return pd.DataFrame(rows)


class FakeProvider:
    def __init__(self):
        self.frames = {"NVDA": _history(), "TSLA": _history(price=200, trend=-0.5)}

    def history(self, symbol, **kwargs):
        return self.frames.get(symbol, pd.DataFrame())

    def quote(self, symbol):
        price = 140 if symbol == "NVDA" else 200
        return {"last": price, "spread": 0.05, "spread_pct": 0.05}


def test_news_index_extracts_explicit_and_dollar_tickers():
    result = _news_index([{
        "title": "$NVDA announces new product",
        "source": "wire",
        "published_at": "2026-08-13T08:00:00Z",
    }])
    assert "NVDA" in result
    assert result["NVDA"][0]["source"] == "wire"


def test_history_metrics_produces_trend_and_liquidity():
    metrics = _history_metrics(_history(), DEFAULT_CONFIG)
    assert metrics is not None
    assert metrics["trend"] == "bullish"
    assert metrics["avg_dollar_volume"] > 0
    assert metrics["relative_volume"] > 1


def test_scan_ranks_candidates_and_returns_ai_context():
    config = deep_merge(DEFAULT_CONFIG, {
        "min_avg_dollar_volume": 1,
        "min_gap_pct": 0,
        "min_relative_volume": 0,
        "min_score": 0,
    })
    result = scan(
        config,
        provider=FakeProvider(),
        symbols=["NVDA", "TSLA"],
        mover_fetcher=lambda: [{"symbol": "NVDA", "change_pct": 3.0, "source": "test_mover"}],
        news_fetcher=lambda: [{"symbol": "NVDA", "title": "NVDA catalyst", "source": "test"}],
    )
    assert result["candidate_count"] == 2
    assert result["watchlist"][0]["symbol"] == "NVDA"
    assert result["watchlist"][0]["status"] == "monitor"
    assert result["watchlist"][0]["ai_context"]["needs_catalyst_review"] is False
    assert "test_mover" in result["watchlist"][0]["sources"]


def test_scanner_does_not_create_orders_or_entry_signals():
    result = scan(DEFAULT_CONFIG, provider=FakeProvider(), symbols=["NVDA"])
    assert "orders" not in result
    assert "entry_price" not in result["watchlist"][0]
