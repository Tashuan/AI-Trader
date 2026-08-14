"""Tests for historical premarket replay enrichment."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from premarket_replay import PremarketReplayProvider
from premarket_scanner import DEFAULT_CONFIG, _score
from strategy_lab import deep_merge


def _provider_for_bars() -> PremarketReplayProvider:
    provider = PremarketReplayProvider.__new__(PremarketReplayProvider)
    provider._target = pd.Timestamp("2026-08-11").date()
    target_index = pd.date_range(
        "2026-08-11 04:00", periods=3, freq="5min", tz="America/New_York"
    )
    prior_index = pd.date_range(
        "2026-08-08 04:00", periods=3, freq="5min", tz="America/New_York"
    )
    target = pd.DataFrame({
        "Open": [100.0, 100.1, 100.2],
        "High": [100.2, 100.3, 100.4],
        "Low": [99.9, 100.0, 100.1],
        "Close": [100.1, 100.2, 100.3],
        "Volume": [300.0, 300.0, 400.0],
    }, index=target_index)
    prior = pd.DataFrame({
        "Open": [100.0] * 3,
        "High": [100.1] * 3,
        "Low": [99.9] * 3,
        "Close": [100.0] * 3,
        "Volume": [100.0] * 3,
    }, index=prior_index)
    provider._premarket_cache = {"NVDA": target}
    provider._premarket_history_cache = {"NVDA": pd.concat([prior, target])}
    provider._daily_cache = {}
    return provider


def test_spread_proxy_is_smaller_than_full_candle_range():
    provider = _provider_for_bars()
    bars = provider._premarket_cache["NVDA"]
    full_range_pct = (bars["High"].iloc[-1] - bars["Low"].iloc[-1]) / bars["Close"].iloc[-1] * 100
    spread_pct = provider._spread_proxy_pct(bars)
    assert 0.01 <= spread_pct <= 0.25
    assert spread_pct < full_range_pct


def test_premarket_relative_volume_uses_only_prior_sessions():
    provider = _provider_for_bars()
    relative_volume, average = provider._premarket_volume_stats("NVDA")
    # Target volume is 1,000; prior session volume is 300.
    assert average == 300.0
    assert relative_volume == round(1000 / 300, 3)


def test_scanner_uses_premarket_relative_volume_when_provider_supplies_it():
    metrics = {
        "price": 100.0,
        "prior_high": 101.0,
        "prior_low": 99.0,
        "avg_dollar_volume": 50_000_000,
        "relative_volume": 0.1,
        "relative_volume_label": "premarket relative volume",
        "trend": "bullish",
    }
    config = deep_merge(DEFAULT_CONFIG, {
        "min_relative_volume": 1.25,
        "min_gap_pct": 0,
    })
    score, _, evidence, risks, _ = _score(
        metrics,
        {"spread_pct": 0.05},
        0.0,
        [],
        config,
    )
    assert score >= 0
    assert "premarket volume not elevated" in risks
    assert not any("relative volume 0.10x" in item for item in evidence)
