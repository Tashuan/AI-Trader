"""Regression tests for the futures ORB research foundation."""

from datetime import date, datetime

import pandas as pd

from research.strategy_search.massive_futures_data import (
    _parse_bars,
    _quarter_contracts,
)
from research.strategy_search.orb_futures_backtester import (
    CanonicalFuturesSignalAdapter,
    FUTURES_ORB_CONFIG,
    CONTRACTS,
)
from agents.mnq_scalp_shadow import _default_state, _exit_for_trade


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


def test_massive_contract_schedule_uses_quarterly_codes():
    contracts = _quarter_contracts(date(2026, 1, 1), date(2026, 12, 31), "MES")
    assert [item[0] for item in contracts[:4]] == ["MESH6", "MESM6", "MESU6", "MESZ6"]


def test_mnq_shadow_state_is_explicitly_order_free():
    state = _default_state()
    assert state["mode"] == "shadow_only"
    assert state["paper_orders"] is False
    assert state["live_orders"] is False


def test_mnq_shadow_grace_allows_target_but_not_stop():
    trade = {
        "side": "long",
        "entry_time": "2025-01-15T09:40:00-05:00",
        "stop_price": 99.0,
        "target_price": 110.0,
    }
    row = pd.Series({"High": 110.0, "Low": 99.0, "Close": 105.0})
    ts = datetime.fromisoformat("2025-01-15T09:44:00-05:00")
    assert _exit_for_trade(trade, row, ts) == ("take_profit", 110.0)
    ts = datetime.fromisoformat("2025-01-15T09:46:00-05:00")
    assert _exit_for_trade(trade, row, ts) == ("stop_loss", 99.0)


def test_massive_bar_normalization_preserves_contract_and_session():
    frame = _parse_bars([{
        "ticker": "MESU6",
        "session_end_date": "2026-08-18",
        "window_start": 1787056200000000000,
        "open": 7700,
        "high": 7701,
        "low": 7699,
        "close": 7700.5,
        "volume": 12,
    }])
    assert frame.iloc[0]["Contract"] == "MESU6"
    assert frame.iloc[0]["SessionEndDate"] == "2026-08-18"
    assert frame.iloc[0]["Close"] == 7700.5
    assert str(frame.index.tz) == "America/New_York"
