"""Historical replay engine for Prior-Day Liquidity Sweep Reclaim."""

from __future__ import annotations

from typing import Any

from sweep_reclaim_strategy import SWEEP_RECLAIM_DEFAULTS, SweepReclaimStrategy
from vol_filter_base import VolFilteredBacktester


class SweepReclaimBacktester(VolFilteredBacktester):
    """Replay one prior-level reclaim trade per selected session."""

    @property
    def agent_name(self) -> str:
        return "Sweep Reclaim"

    @property
    def default_params(self) -> dict[str, Any]:
        return SWEEP_RECLAIM_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return SweepReclaimStrategy(symbol, self.params, self._previous_day_levels(symbol, date))
