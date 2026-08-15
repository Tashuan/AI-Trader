"""Historical replay engine for VWAP Magnet."""

from __future__ import annotations

from typing import Any

from vwap_magnet_strategy import VWAP_MAGNET_DEFAULTS, VWAPMagnetStrategy
from vol_filter_base import VolFilteredBacktester


class VWAPMagnetBacktester(VolFilteredBacktester):
    """Replay one VWAP Magnet trade per selected session."""

    @property
    def agent_name(self) -> str:
        return "VWAP Magnet"

    @property
    def default_params(self) -> dict[str, Any]:
        return VWAP_MAGNET_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return VWAPMagnetStrategy(symbol, self.params)
