"""Canonical ORB strategy core — pure, no network or broker dependencies.

This module is the single source of truth for ORB signal generation, opening
range construction, contract selection, and signal ranking.  Both the live
runner (``orb_runner.py``) and the research backtesters consume this module
so that live and backtest produce identical signal sequences for the same
bars and config.

Key design decisions
--------------------
- Opening range uses bars from 09:30 through 09:34 (five 1-minute bars).
  The 09:35 completed bar is the first eligible *breakout* bar.
- Signal freshness is enforced via ``max_signal_age_seconds``.
- Contract selection supports two modes:
    * ``symmetric_otm`` (default): calls → next higher strike, puts → next lower strike.
    * ``legacy_plus_strike``: both calls and puts use ATM + offset (reproduces the
      historical +147% result family).
- Intrabar conflict policy: ``conservative`` (stop-first) or ``legacy`` (target-first).
- One-trade-per-symbol-per-session is an explicit policy, not an emergent side-effect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from enum import Enum
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("ORBStrategy")

ET = ZoneInfo("America/New_York")

# ── Strike steps (shared by live + research) ───────────────────────────
STRIKE_STEPS: dict[str, float] = {
    "NVDA": 2.5, "TSLA": 2.5, "AMD": 0.5, "AAPL": 2.5, "META": 2.5,
    "AMZN": 2.5, "MSFT": 2.5, "GOOGL": 2.5, "NFLX": 5.0, "INTC": 0.5,
    "COIN": 2.5, "MU": 0.5, "BA": 5.0, "DIS": 0.5, "BABA": 2.5,
    "MARA": 0.5, "RIOT": 0.5, "SOFI": 0.5, "AAL": 0.5, "UAL": 0.5,
    "F": 0.5, "GM": 0.5, "NIO": 0.5, "XPEV": 0.5, "PLUG": 0.5,
    "DKNG": 1.0, "SPOT": 2.5, "SNAP": 0.5, "PINS": 0.5, "ROKU": 1.0,
    "ZM": 2.5, "SQ": 2.5, "SHOP": 2.5,
}


class StrategyMode(str, Enum):
    SYMMETRIC_OTM = "symmetric_otm"
    LEGACY_PLUS_STRIKE = "legacy_plus_strike"


class ExecutionMode(str, Enum):
    SHADOW = "shadow"
    PAPER = "paper"


class IntrabarPolicy(str, Enum):
    CONSERVATIVE = "conservative"  # stop-first when both touched in one bar
    LEGACY = "legacy"              # target-first (historical behavior)


class DiscoveryMode(str, Enum):
    FIXED = "fixed"
    DYNAMIC = "dynamic"


class RangeEndPolicy(str, Enum):
    EXCLUSIVE = "exclusive"  # range_end bar is the first breakout bar
    INCLUSIVE = "inclusive"  # range_end bar is part of the range (legacy)


@dataclass(frozen=True)
class ORBStrategyConfig:
    """Versioned, immutable ORB strategy configuration."""

    strategy_mode: StrategyMode = StrategyMode.SYMMETRIC_OTM
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    range_minutes: int = 5
    range_end_policy: RangeEndPolicy = RangeEndPolicy.EXCLUSIVE
    latest_entry: str = "10:30"
    stop_pct: float = 1.0
    target_pct: float = 1.5
    confirmation_minutes: int = 10
    confirmation_bars: int = 1
    skip_first_post_range_bar: bool = False
    max_positions: int = 3
    position_pct: float = 10.0
    dte_min: int = 2
    dte_max: int = 14
    intrabar_policy: IntrabarPolicy = IntrabarPolicy.CONSERVATIVE
    entry_latency_bars: int = 0
    max_signal_age_seconds: int = 120
    discovery_mode: DiscoveryMode = DiscoveryMode.DYNAMIC
    discovery_max_symbols: int = 8
    discovery_min_change_pct: float = 1.0
    paper_only: bool = True
    option_slippage_bps: float = 10.0
    risk_free_rate: float = 0.05
    min_entry_time: str = "09:30"
    strike_offset: int = 1
    circuit_breaker: int = 0  # 0 = disabled (one-trade-per-symbol is the policy)
    config_version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_mode": self.strategy_mode.value,
            "execution_mode": self.execution_mode.value,
            "range_minutes": self.range_minutes,
            "range_end_policy": self.range_end_policy.value,
            "latest_entry": self.latest_entry,
            "stop_pct": self.stop_pct,
            "target_pct": self.target_pct,
            "confirmation_minutes": self.confirmation_minutes,
            "confirmation_bars": self.confirmation_bars,
            "skip_first_post_range_bar": self.skip_first_post_range_bar,
            "max_positions": self.max_positions,
            "position_pct": self.position_pct,
            "dte_min": self.dte_min,
            "dte_max": self.dte_max,
            "intrabar_policy": self.intrabar_policy.value,
            "entry_latency_bars": self.entry_latency_bars,
            "max_signal_age_seconds": self.max_signal_age_seconds,
            "discovery_mode": self.discovery_mode.value,
            "discovery_max_symbols": self.discovery_max_symbols,
            "discovery_min_change_pct": self.discovery_min_change_pct,
            "paper_only": self.paper_only,
            "option_slippage_bps": self.option_slippage_bps,
            "risk_free_rate": self.risk_free_rate,
            "min_entry_time": self.min_entry_time,
            "strike_offset": self.strike_offset,
            "circuit_breaker": self.circuit_breaker,
            "config_version": self.config_version,
        }

    @classmethod
    def legacy(cls) -> "ORBStrategyConfig":
        """Config that reproduces the historical +147% result family."""
        return cls(
            strategy_mode=StrategyMode.LEGACY_PLUS_STRIKE,
            execution_mode=ExecutionMode.PAPER,
            range_end_policy=RangeEndPolicy.INCLUSIVE,
            intrabar_policy=IntrabarPolicy.LEGACY,
            discovery_mode=DiscoveryMode.FIXED,
            circuit_breaker=3,
            config_version="1.0-legacy",
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ORBStrategyConfig":
        """Build config from a dict, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {}
        for k, v in d.items():
            if k in known:
                if k in ("strategy_mode", "execution_mode", "intrabar_policy",
                         "discovery_mode", "range_end_policy"):
                    v = str(v)
                filtered[k] = v
        return cls(**filtered)


# ── Signal & Range dataclasses ─────────────────────────────────────────

@dataclass(frozen=True)
class ORBRange:
    """Opening range for a symbol."""
    symbol: str
    range_high: float
    range_low: float
    range_start_ts: datetime
    range_end_ts: datetime
    bar_count: int


@dataclass(frozen=True)
class ORBSignal:
    """ORB breakout signal — immutable, deterministic."""
    symbol: str
    side: str            # "long" or "short"
    option_type: str     # "call" or "put"
    entry_price: float   # underlying close at breakout
    stop_price: float    # underlying stop
    target_price: float  # underlying target
    signal_ts: datetime  # timestamp of the breakout bar
    range_high: float
    range_low: float
    range_width: float
    discovery_source: str = ""
    discovery_rank: int = 0

    @property
    def signal_timestamp_str(self) -> str:
        return str(self.signal_ts)


# ── Opening Range Builder ──────────────────────────────────────────────

class OpeningRangeBuilder:
    """Builds opening ranges from 1-minute bars using explicit timestamps.

    For a 5-minute range with EXCLUSIVE policy:
      Range bars: 09:30, 09:31, 09:32, 09:33, 09:34
      First breakout bar: 09:35

    For a 5-minute range with INCLUSIVE policy (legacy):
      Range bars: 09:30, 09:31, 09:32, 09:33, 09:34, 09:35
      First breakout bar: 09:36
    """

    def __init__(self, config: ORBStrategyConfig):
        self._config = config
        self._range_minutes = config.range_minutes
        self._range_end_policy = config.range_end_policy
        self._market_open = dt_time(9, 30)
        if self._range_end_policy == RangeEndPolicy.EXCLUSIVE:
            # Range bars: 09:30 .. 09:34 (5 bars), first breakout: 09:35
            self._range_end = dt_time(9, 30 + self._range_minutes - 1)
        else:
            # Range bars: 09:30 .. 09:35 (6 bars, legacy), first breakout: 09:36
            self._range_end = dt_time(9, 30 + self._range_minutes)

    def build(self, symbol: str, bars: list[dict]) -> Optional[ORBRange]:
        """Build the opening range from a list of 1-minute bar dicts.

        Each bar must have a 'Timestamp' key (datetime or ISO string),
        'High', and 'Low' keys.
        """
        range_bars = []
        for bar in bars:
            ts = _parse_ts(bar.get("Timestamp"))
            if ts is None:
                continue
            bar_time = ts.time()
            if bar_time < self._market_open:
                continue
            if bar_time <= self._range_end:
                range_bars.append((ts, bar))

        if not range_bars:
            return None

        highs = [float(b["High"]) for _, b in range_bars]
        lows = [float(b["Low"]) for _, b in range_bars]
        start_ts = range_bars[0][0]
        end_ts = range_bars[-1][0]

        return ORBRange(
            symbol=symbol,
            range_high=max(highs),
            range_low=min(lows),
            range_start_ts=start_ts,
            range_end_ts=end_ts,
            bar_count=len(range_bars),
        )


# ── Breakout Checker ───────────────────────────────────────────────────

class BreakoutChecker:
    """Checks for breakout closes after the opening range.

    Enforces:
    - Close must be outside the range (wick-only breaks don't trigger).
    - Signal timestamp must be after the range end.
    - Signal must not be older than max_signal_age_seconds.
    - One signal per symbol per session.
    - Latest entry cutoff is enforced.
    - Duplicate bar timestamps are ignored.
    """

    def __init__(self, config: ORBStrategyConfig):
        self._config = config
        self._latest_entry = _parse_time_str(config.latest_entry)
        self._min_entry = _parse_time_str(config.min_entry_time)
        self._processed_ts: set[datetime] = set()
        self._entered = False
        self._confirmation_side: Optional[str] = None
        self._confirmation_count = 0
        self._first_post_range_seen = False

    def reset(self) -> None:
        self._processed_ts.clear()
        self._entered = False
        self._confirmation_side = None
        self._confirmation_count = 0
        self._first_post_range_seen = False

    def check(
        self,
        symbol: str,
        bar: dict,
        orb_range: ORBRange,
        current_ts: Optional[datetime] = None,
    ) -> Optional[ORBSignal]:
        """Check a single bar for a breakout signal.

        Returns an ORBSignal if the bar is a valid breakout, else None.
        """
        if self._entered:
            return None

        ts = _parse_ts(bar.get("Timestamp"))
        if ts is None:
            return None

        if ts in self._processed_ts:
            return None
        self._processed_ts.add(ts)

        bar_time = ts.time()

        if bar_time < self._min_entry:
            return None

        if bar_time <= orb_range.range_end_ts.time():
            return None

        if self._config.skip_first_post_range_bar and not self._first_post_range_seen:
            self._first_post_range_seen = True
            return None
        self._first_post_range_seen = True

        if bar_time > self._latest_entry:
            return None

        if current_ts is not None:
            age = (current_ts - ts).total_seconds()
            if age > self._config.max_signal_age_seconds:
                logger.debug(
                    "Signal stale for %s: age=%ds max=%ds",
                    symbol, int(age), self._config.max_signal_age_seconds,
                )
                return None

        close = float(bar["Close"])
        stop_pct = self._config.stop_pct
        target_pct = self._config.target_pct
        range_width = orb_range.range_high - orb_range.range_low

        if close > orb_range.range_high:
            side = "long"
            option_type = "call"
            stop_price = close * (1 - stop_pct / 100)
            target_price = close * (1 + target_pct / 100)
        elif close < orb_range.range_low:
            side = "short"
            option_type = "put"
            stop_price = close * (1 + stop_pct / 100)
            target_price = close * (1 - target_pct / 100)
        else:
            self._confirmation_side = None
            self._confirmation_count = 0
            return None

        required_bars = max(1, self._config.confirmation_bars)
        if side == self._confirmation_side:
            self._confirmation_count += 1
        else:
            self._confirmation_side = side
            self._confirmation_count = 1
        if self._confirmation_count < required_bars:
            return None

        self._entered = True
        return ORBSignal(
            symbol=symbol,
            side=side,
            option_type=option_type,
            entry_price=close,
            stop_price=stop_price,
            target_price=target_price,
            signal_ts=ts,
            range_high=orb_range.range_high,
            range_low=orb_range.range_low,
            range_width=range_width,
        )


# ── Contract Selection ─────────────────────────────────────────────────

def select_strike(
    spot: float,
    option_type: str,
    symbol: str,
    config: ORBStrategyConfig,
) -> float:
    """Select the option strike for a signal.

    ``symmetric_otm`` (default):
        calls → next higher strike (ATM + offset)
        puts  → next lower strike  (ATM − offset)

    ``legacy_plus_strike``:
        both calls and puts → ATM + offset (reproduces historical behavior)
    """
    strike_step = STRIKE_STEPS.get(symbol, 2.5)
    atm = round(spot / strike_step) * strike_step
    offset = config.strike_offset

    if config.strategy_mode == StrategyMode.SYMMETRIC_OTM:
        if option_type == "call":
            return atm + offset * strike_step
        else:
            return atm - offset * strike_step
    else:
        return atm + offset * strike_step


def validate_strike(
    strike: float,
    symbol: str,
    option_type: str,
) -> bool:
    """Validate that a strike is on the symbol's strike grid."""
    step = STRIKE_STEPS.get(symbol, 2.5)
    remainder = strike % step
    tolerance = step * 0.01
    return abs(remainder) < tolerance or abs(remainder - step) < tolerance


# ── Signal Ranking ─────────────────────────────────────────────────────

def rank_signals(signals: list[ORBSignal]) -> list[ORBSignal]:
    """Rank signals deterministically for position admission.

    Priority:
    1. Wider range width (more conviction)
    2. Earlier signal timestamp (first come, first served)
    3. Alphabetical symbol (deterministic tie-break)
    """
    return sorted(
        signals,
        key=lambda s: (
            -s.range_width,
            s.signal_ts,
            s.symbol,
        ),
    )


# ── Exit Checker (pure) ────────────────────────────────────────────────

def check_exit(
    side: str,
    current_high: float,
    current_low: float,
    stop_price: float,
    target_price: float,
    in_confirmation: bool,
    intrabar_policy: IntrabarPolicy,
) -> Optional[str]:
    """Check if a position should be exited on this bar.

    Returns exit reason string or None.
    Confirmation period: stops are not checked during confirmation.
    Intrabar conflict: when both stop and target are touched in one bar,
    conservative policy assumes stop fires first.
    """
    if side == "long":
        target_touched = current_high >= target_price
        stop_touched = current_low <= stop_price
    else:
        target_touched = current_low <= target_price
        stop_touched = current_high >= stop_price

    if stop_touched and target_touched:
        if intrabar_policy == IntrabarPolicy.CONSERVATIVE:
            if not in_confirmation:
                return "stop_loss"
            return "take_profit"
        else:
            return "take_profit"

    if target_touched:
        return "take_profit"

    if stop_touched and not in_confirmation:
        return "stop_loss"

    return None


# ── Helpers ────────────────────────────────────────────────────────────

def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a timestamp from datetime, str, or pandas Timestamp."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        try:
            import pandas as pd
            return pd.Timestamp(value).to_pydatetime()
        except Exception:
            return None


def _parse_time_str(s: str) -> dt_time:
    """Parse 'HH:MM' into a datetime.time."""
    parts = s.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def find_expiration(
    date: Any,
    dte_min: int,
    dte_max: int,
) -> Optional[str]:
    """Find the nearest Friday expiration within DTE range.

    Returns YYYY-MM-DD string or None.
    """
    if isinstance(date, str):
        d = datetime.fromisoformat(date)
    elif isinstance(date, datetime):
        d = date
    else:
        d = datetime.fromisoformat(str(date))

    days_to_friday = (4 - d.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    friday = d + timedelta(days=days_to_friday)
    dte = (friday - d).days
    if dte < dte_min:
        friday += timedelta(days=7)
        dte = (friday - d).days
    if dte > dte_max:
        return None
    return friday.strftime("%Y-%m-%d")
