"""Historical replay engine for First Pullback."""

from __future__ import annotations

from typing import Any

from first_pullback_strategy import FIRST_PULLBACK_DEFAULTS, FirstPullbackStrategy
from vol_filter_base import VolFilteredBacktester


class FirstPullbackBacktester(VolFilteredBacktester):
    """Replay one first-pullback trade per selected session."""

    @property
    def agent_name(self) -> str:
        return "First Pullback"

    @property
    def default_params(self) -> dict[str, Any]:
        return FIRST_PULLBACK_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return FirstPullbackStrategy(symbol, self.params,
                                     previous_close=self._previous_close(symbol, date))
