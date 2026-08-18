"""Regression tests for the futures ORB research foundation."""

from datetime import datetime

import pandas as pd

from research.strategy_search.orb_futures_backtester import (
    CanonicalFuturesSignalAdapter,
    FUTURES_ORB_CONFIG,
    CONTRACTS,
)


def _day_bars() -> pd.DataFrame:
    rows = []
    for minute, close in [
        (30, 100.0), (35, 101.0), (40, 102.0), (45, 103.0)
    ]:
        rows.append({
            "Timestamp": datetime(2025, 1, 15, 9, minute),
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
        })
    return pd.DataFrame(rows)


def test_futures_adapter_uses_two_confirmed_closes_after_skipping_first():
    config = {**FUTURES_ORB_CONFIG}
    adapter = CanonicalFuturesSignalAdapter("MES=F", config)
    bars = _day_bars()

    first = bars.iloc[0]
    second = bars.iloc[1]
    third = bars.iloc[2]
    fourth = bars.iloc[3]

    assert adapter.on_bar(
        first["Timestamp"], first.to_dict(), bars
    ) is None
    assert adapter.on_bar(
        second["Timestamp"], second.to_dict(), bars
    ) is None
    assert adapter.on_bar(
        third["Timestamp"], third.to_dict(), bars
    ) is None
    signal = adapter.on_bar(fourth["Timestamp"], fourth.to_dict(), bars)

    assert signal is not None
    assert signal.side == "long"
    assert signal.range_high == 100.5
    assert signal.range_low == 99.5


def test_micro_contract_specs_have_tick_consistent_values():
    for symbol in ("MES=F", "MNQ=F", "M2K=F", "MYM=F"):
        contract = CONTRACTS[symbol]
        assert contract.tick_value == contract.tick_size * contract.multiplier
