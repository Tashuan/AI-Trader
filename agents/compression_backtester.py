"""Historical replay engine for Intraday Compression Expansion."""

from __future__ import annotations

from typing import Any

from compression_strategy import COMPRESSION_DEFAULTS, CompressionStrategy
from vol_filter_base import VolFilteredBacktester


class CompressionBacktester(VolFilteredBacktester):
    """Replay one compression expansion trade per selected session."""

    @property
    def agent_name(self) -> str:
        return "Compression Expansion"

    @property
    def default_params(self) -> dict[str, Any]:
        return COMPRESSION_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return CompressionStrategy(symbol, self.params)
