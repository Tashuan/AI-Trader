"""Historical replay engine for Fakeout Fade."""

from __future__ import annotations

from typing import Any

from fakeout_fade_strategy import FAKEOUT_FADE_DEFAULTS, FakeoutFadeStrategy
from vol_filter_base import VolFilteredBacktester


class FakeoutFadeBacktester(VolFilteredBacktester):
    """Replay one failed-breakout reversal per selected session."""

    @property
    def agent_name(self) -> str:
        return "Fakeout Fade"

    @property
    def default_params(self) -> dict[str, Any]:
        return FAKEOUT_FADE_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return FakeoutFadeStrategy(symbol, self.params)
