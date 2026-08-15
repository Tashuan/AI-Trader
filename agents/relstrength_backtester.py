"""Historical replay engine for Relative-Strength Opening Drive."""

from __future__ import annotations

from typing import Any

from relstrength_strategy import RELSTRENGTH_DEFAULTS, RelativeStrengthStrategy
from vol_filter_base import VolFilteredBacktester


class RelativeStrengthBacktester(VolFilteredBacktester):
    """Replay one relative-strength continuation trade per session."""

    @property
    def agent_name(self) -> str:
        return "Relative Strength"

    @property
    def default_params(self) -> dict[str, Any]:
        return RELSTRENGTH_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return RelativeStrengthStrategy(symbol, self.params, self._spy_opening_return(date))
