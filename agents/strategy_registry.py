"""Agent-specific strategy profiles and validated risk controls."""

from __future__ import annotations

import copy
from typing import Any

import crypto_scan_core
import scan_core


RISK_CONTROL_DEFAULTS: dict[str, Any] = {
    "paper_only": True,
    "paper_account_budget": 10_000.0,
    "sizing_mode": "risk_based",
    "risk_per_trade_pct": 0.50,
    "max_trade_notional_pct": 25.0,
    "max_open_risk_pct": 1.50,
    "max_gross_exposure_pct": 100.0,
    "max_position_dollar_cap": None,
    "daily_loss_halt_pct": 3.0,
    "loss_streak_size_cut_pct": 50.0,
}

_PROFILE_DEFAULTS = {
    "equity_momentum": scan_core.DEFAULT_PARAMS,
    "crypto_swing": crypto_scan_core.CRYPTO_DEFAULT_PARAMS,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def profile_for(agent_name: str = "", strategy_type: str = "") -> str:
    identity = f"{agent_name} {strategy_type}".lower()
    return "crypto_swing" if "crypto" in identity else "equity_momentum"


def effective_params(
    agent_name: str = "",
    strategy_type: str = "",
    stored: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile_for(agent_name, strategy_type)
    params = deep_merge(_PROFILE_DEFAULTS[profile], stored or {})
    params["risk_controls"] = deep_merge(
        RISK_CONTROL_DEFAULTS,
        (stored or {}).get("risk_controls", {}),
    )
    params["risk_controls"].setdefault(
        "max_positions",
        params.get("position_sizing", {}).get("max_positions", 1),
    )
    params["profile"] = profile
    params["schema_version"] = 1
    validate_params(params)
    return params


def position_notional(
    equity: float,
    stop_distance_pct: float,
    current_gross_exposure: float,
    params: dict[str, Any],
) -> float:
    risk = params.get("risk_controls", {})
    budget = float(risk.get("paper_account_budget", 10_000.0))
    gross_cap = max(0.0, equity) * float(risk.get("max_gross_exposure_pct", 100.0)) / 100.0
    remaining_budget = max(0.0, min(budget, gross_cap) - max(0.0, current_gross_exposure)) if budget > 0 else max(0.0, gross_cap - max(0.0, current_gross_exposure))
    if risk.get("sizing_mode") == "notional":
        notional = equity * float(params.get("position_sizing", {}).get("normal_sizing_max_pct", 10)) / 100.0
    else:
        stop_pct = max(float(stop_distance_pct), 0.01)
        risk_dollars = max(0.0, equity) * float(risk.get("risk_per_trade_pct", 0.5)) / 100.0
        notional = risk_dollars / (stop_pct / 100.0)
    cap = risk.get("max_position_dollar_cap")
    if cap is not None:
        notional = min(notional, float(cap))
    return max(0.0, min(notional, remaining_budget))


def validate_params(params: dict[str, Any]) -> None:
    risk = params.get("risk_controls", {})
    _range(risk, "paper_account_budget", 0.0, 1_000_000_000.0)
    _range(risk, "max_positions", 1.0, 50.0)
    _range(risk, "risk_per_trade_pct", 0.01, 5.0)
    _range(risk, "max_trade_notional_pct", 0.1, 100.0)
    _range(risk, "max_open_risk_pct", 0.01, 20.0)
    _range(risk, "max_gross_exposure_pct", 0.0, 100.0)
    _range(risk, "daily_loss_halt_pct", 0.1, 100.0)
    _range(risk, "loss_streak_size_cut_pct", 0.0, 100.0)
    if risk.get("sizing_mode") not in {"risk_based", "notional"}:
        raise ValueError("risk_controls.sizing_mode must be risk_based or notional")
    if risk.get("max_position_dollar_cap") is not None:
        _range(risk, "max_position_dollar_cap", 0.0, 1_000_000_000.0)


def _range(values: dict[str, Any], key: str, minimum: float, maximum: float) -> None:
    try:
        value = float(values.get(key))
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be numeric") from None
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
