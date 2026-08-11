"""StockBoy deterministic policy guardrails.

All StockBoy actions must pass through these checks before execution.
The policy layer enforces:
- Paper-only mode invariant
- No new entries (buy/short) — ever
- Allowlisted controlled runners only
- Position ownership by controlled runners
- Bounded adjustment notional and frequency
- Stale price/data rejection
- Stop-tightening-only by default
- Kill switch and actions-disabled enforcement
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from stockboy_models import StockBoyActionRequest


# ============================================================
# Constants and configuration
# ============================================================

SUPERVISOR_ROLE = "supervisor"
STOCKBOY_NAME = "StockBoy"
STOCKBOY_EMAIL = "stockboy@agent.dev"

# The three deterministic runners StockBoy is allowed to control.
CONTROLLED_RUNNERS: dict[str, str] = {
    "blitztrader": "BlitzRunner",
    "cryptorunner": "CryptoRunner",
    "scalprunner": "ScalpRunner",
}

# Actions that are strictly forbidden — StockBoy can never create new entries.
FORBIDDEN_ACTIONS = frozenset({"buy", "short", "enter", "open_position"})

# Allowed adjustment action types.
ALLOWED_ACTION_TYPES = frozenset({
    "close_position",
    "partial_close",
    "set_stop",
    "set_target",
    "set_trailing",
    "cancel_order",
})

# Runner config fields that may be temporarily overridden.
OVERRIDABLE_FIELD_PREFIXES = (
    "exit_rules.",
    "entry_criteria.",
    "position_sizing.",
    "cycle_timing.",
    "risk_controls.",
)

# Fields that must never be overridden for safety.
NON_OVERRIDABLE_FIELDS = frozenset({
    "risk_controls.paper_only",
    "risk_controls.paper_account_budget",
})


@dataclass
class PolicyConfig:
    """Configuration-driven policy thresholds (env-overridable)."""
    max_total_adjustment_notional: float = 50000.0
    max_actions_per_cycle: int = 10
    max_actions_per_day: int = 100
    max_partial_close_pct: float = 100.0  # may close fully
    min_residual_quantity: float = 0.0
    stale_price_max_age_seconds: float = 300.0
    stop_tighten_only: bool = True
    cooldown_seconds_per_position: float = 60.0
    daily_loss_halt_pct: float = 5.0
    max_gross_exposure_pct: float = 100.0
    pending_order_stale_minutes: float = 60.0

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        def _env_float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        return cls(
            max_total_adjustment_notional=_env_float("STOCKBOY_MAX_ADJUSTMENT_NOTIONAL", 50000.0),
            max_actions_per_cycle=_env_int("STOCKBOY_MAX_ACTIONS_PER_CYCLE", 10),
            max_actions_per_day=_env_int("STOCKBOY_MAX_ACTIONS_PER_DAY", 100),
            max_partial_close_pct=_env_float("STOCKBOY_MAX_PARTIAL_CLOSE_PCT", 100.0),
            min_residual_quantity=_env_float("STOCKBOY_MIN_RESIDUAL_QTY", 0.0),
            stale_price_max_age_seconds=_env_float("STOCKBOY_STALE_PRICE_AGE_SECONDS", 300.0),
            stop_tighten_only=_env_bool("STOCKBOY_STOP_TIGHTEN_ONLY", True),
            cooldown_seconds_per_position=_env_float("STOCKBOY_COOLDOWN_SECONDS", 60.0),
            daily_loss_halt_pct=_env_float("STOCKBOY_DAILY_LOSS_HALT_PCT", 5.0),
            max_gross_exposure_pct=_env_float("STOCKBOY_MAX_GROSS_EXPOSURE_PCT", 100.0),
            pending_order_stale_minutes=_env_float("STOCKBOY_PENDING_ORDER_STALE_MINUTES", 60.0),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# Policy violation
# ============================================================

class PolicyViolation(Exception):
    """Raised when a proposed StockBoy action violates the supervisor policy."""

    def __init__(self, reason: str, category: str = "policy", metadata: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.category = category
        self.metadata = metadata or {}


# ============================================================
# Validation helpers
# ============================================================

def is_controlled_runner(runner_key: str) -> bool:
    """Return True if the runner key is one of the three controlled runners."""
    return runner_key in CONTROLLED_RUNNERS


def is_forbidden_action(action_type: str) -> bool:
    """Return True if the action type is a forbidden entry action."""
    return action_type.lower() in FORBIDDEN_ACTIONS


def is_allowed_action_type(action_type: str) -> bool:
    """Return True if the action type is an allowed adjustment."""
    return action_type in ALLOWED_ACTION_TYPES


def is_overridable_field(field_path: str) -> bool:
    """Return True if the field path may be temporarily overridden."""
    if field_path in NON_OVERRIDABLE_FIELDS:
        return False
    return any(field_path.startswith(prefix) for prefix in OVERRIDABLE_FIELD_PREFIXES)


def validate_action(
    request: StockBoyActionRequest,
    *,
    config: Optional[PolicyConfig] = None,
    supervisor_enabled: bool = True,
    actions_enabled: bool = True,
    kill_switch: bool = False,
    paper_only: bool = True,
    target_position: Optional[Dict[str, Any]] = None,
    current_price: Optional[float] = None,
    current_price_age_seconds: Optional[float] = None,
    target_order: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate a proposed StockBoy action against all policy guardrails.

    Raises PolicyViolation if the action is rejected.
    """
    cfg = config or PolicyConfig.from_env()

    if kill_switch:
        raise PolicyViolation("Kill switch is engaged — all StockBoy actions are blocked", "kill_switch")

    if not supervisor_enabled:
        raise PolicyViolation("StockBoy supervisor is disabled", "disabled")

    if not actions_enabled:
        raise PolicyViolation("StockBoy actions are disabled", "actions_disabled")

    if not paper_only or _env_bool("STOCKBOY_ALLOW_LIVE", False):
        raise PolicyViolation(
            "StockBoy is paper-only; live actions are disabled",
            "live_mode",
        )

    if is_forbidden_action(request.action_type):
        raise PolicyViolation(
            f"Action '{request.action_type}' is forbidden — StockBoy cannot create new entries",
            "no_entry",
        )

    if not is_allowed_action_type(request.action_type):
        raise PolicyViolation(
            f"Action type '{request.action_type}' is not in the allowed adjustment set",
            "unknown_action",
        )

    if not is_controlled_runner(request.runner_key):
        raise PolicyViolation(
            f"Runner '{request.runner_key}' is not a controlled runner",
            "unauthorized_target",
        )

    # Position-targeted actions require a valid position owned by a controlled runner.
    if request.action_type in ("close_position", "partial_close", "set_stop", "set_target", "set_trailing"):
        if target_position is None:
            raise PolicyViolation(
                f"Action '{request.action_type}' requires a target position",
                "missing_target",
            )

        pos_agent_name = (target_position.get("agent_name") or "").strip()
        expected_name = CONTROLLED_RUNNERS.get(request.runner_key, "")
        if pos_agent_name != expected_name:
            raise PolicyViolation(
                f"Position belongs to '{pos_agent_name}', not controlled runner '{expected_name}'",
                "ownership_mismatch",
            )

        qty = float(target_position.get("quantity") or 0)
        if abs(qty) <= 0:
            raise PolicyViolation("Target position has zero quantity", "empty_position")

        # Stale price check for close/partial_close.
        if request.action_type in ("close_position", "partial_close"):
            if current_price is None or current_price <= 0:
                raise PolicyViolation("Cannot close position without a valid current price", "stale_price")
            if current_price_age_seconds is not None and current_price_age_seconds > cfg.stale_price_max_age_seconds:
                raise PolicyViolation(
                    f"Current price is stale ({current_price_age_seconds:.0f}s old)",
                    "stale_price",
                )

        # Partial close bounds.
        if request.action_type == "partial_close":
            close_qty = request.quantity
            if close_qty is None or close_qty <= 0:
                raise PolicyViolation("Partial close requires a positive quantity", "invalid_quantity")
            if close_qty >= abs(qty):
                raise PolicyViolation(
                    "Partial close quantity must be less than total position quantity — use close_position for full close",
                    "invalid_quantity",
                )
            residual = abs(qty) - close_qty
            if residual < cfg.min_residual_quantity:
                raise PolicyViolation(
                    f"Residual quantity {residual} is below minimum {cfg.min_residual_quantity}",
                    "invalid_quantity",
                )

    # Stop-tightening-only check.
    if cfg.stop_tighten_only and request.action_type == "set_stop":
        if target_position is not None and request.stop_loss_price is not None:
            existing_stop = target_position.get("stop_loss_price")
            side = (target_position.get("side") or "long").lower()
            entry = float(target_position.get("entry_price") or 0)
            new_stop = request.stop_loss_price
            if side == "long":
                # For longs, a tighter stop is closer to entry (higher stop price).
                # Loosening means moving the stop further away (lower stop price).
                if existing_stop is not None and new_stop < float(existing_stop):
                    raise PolicyViolation(
                        f"Stop loss may only be tightened for longs — new {new_stop} < existing {existing_stop}",
                        "stop_loosened",
                    )
                if new_stop > entry:
                    raise PolicyViolation(
                        f"Stop loss for a long must be below entry price {entry}",
                        "invalid_stop",
                    )
            elif side == "short":
                if existing_stop is not None and new_stop > float(existing_stop):
                    raise PolicyViolation(
                        f"Stop loss may only be tightened for shorts — new {new_stop} > existing {existing_stop}",
                        "stop_loosened",
                    )
                if new_stop < entry:
                    raise PolicyViolation(
                        f"Stop loss for a short must be above entry price {entry}",
                        "invalid_stop",
                    )

    # Cancel order requires a target order owned by a controlled runner.
    if request.action_type == "cancel_order":
        if request.target_order_id is None:
            raise PolicyViolation("Cancel order requires a target_order_id", "missing_target")
        if target_order is None:
            raise PolicyViolation(
                f"Order {request.target_order_id} not found",
                "missing_target",
            )
        order_agent_name = (target_order.get("agent_name") or "").strip()
        expected_name = CONTROLLED_RUNNERS.get(request.runner_key, "")
        if order_agent_name != expected_name:
            raise PolicyViolation(
                f"Order belongs to '{order_agent_name}', not controlled runner '{expected_name}'",
                "ownership_mismatch",
            )
        order_status = (target_order.get("status") or "").upper()
        if order_status != "PENDING":
            raise PolicyViolation(
                f"Order is not pending (status={order_status})",
                "invalid_order_state",
            )


def validate_override(
    runner_key: str,
    field_path: str,
    new_value: Any,
    *,
    config: Optional[PolicyConfig] = None,
) -> None:
    """Validate a proposed runner configuration override. Raises PolicyViolation on rejection."""
    if not is_controlled_runner(runner_key):
        raise PolicyViolation(f"Runner '{runner_key}' is not controlled", "unauthorized_target")

    if not is_overridable_field(field_path):
        raise PolicyViolation(
            f"Field '{field_path}' is not in the overridable allowlist",
            "non_overridable_field",
        )

    # Reject None or empty values that would break runner config.
    if new_value is None:
        raise PolicyViolation("Override value cannot be None", "invalid_value")


def action_cooldown_key(runner_key: str, position_id: Optional[int], action_type: str) -> str:
    """Build a deduplication/cooldown key for an action."""
    return f"{runner_key}:{position_id or 'none'}:{action_type}"


class CooldownTracker:
    """In-memory cooldown tracker to prevent oscillating adjustments."""

    def __init__(self) -> None:
        self._timestamps: dict[str, float] = {}

    def check_and_record(self, key: str, cooldown_seconds: float) -> bool:
        """Return True if the action is allowed (not in cooldown), False otherwise."""
        now = time.time()
        last = self._timestamps.get(key, 0)
        if now - last < cooldown_seconds:
            return False
        self._timestamps[key] = now
        return True

    def clear(self) -> None:
        self._timestamps.clear()
