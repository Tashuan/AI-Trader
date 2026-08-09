"""Universal schema registry, effective-parameter resolver, and validation.

Each strategy implementation declares its schema (shared fields, strategy-specific
sections, defaults, validation ranges, enum choices). The resolver merges canonical
defaults with the agent's stored database config, validates the result, and returns
source metadata so UI and reports can prove parity.

Live bots, API backtests, CLI backtests, and UI-loaded settings all consume the
same effective parameter object for the selected agent.
"""

from __future__ import annotations

import copy
from typing import Any

import crypto_scan_core
import scan_core


# ============================================================
# Risk Control Defaults (shared across all strategies)
# ============================================================

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

# ============================================================
# Schema Field Type Definitions
# ============================================================

def _field(label: str, field_type: str, minimum: float | None = None,
           maximum: float | None = None, choices: list[str] | None = None,
           default: Any = None, description: str = "") -> dict[str, Any]:
    return {
        "label": label, "type": field_type, "min": minimum, "max": maximum,
        "choices": choices, "default": default, "description": description,
    }


# ============================================================
# Strategy Schema Registry
# ============================================================

STRATEGY_SCHEMAS: dict[str, dict[str, Any]] = {
    "crypto_swing": {
        "display_name": "CryptoRunner — Crypto Position/Trend",
        "strategy_type": "crypto_swing",
        "parity_status": "live_backtest_matched",
        "defaults": crypto_scan_core.CRYPTO_DEFAULT_PARAMS,
        "shared_fields": {
            "watchlist": _field("Watchlist", "list", default=crypto_scan_core.CRYPTO_DEFAULT_PARAMS["watchlist"]),
            "poll_interval": _field("Poll Interval (s)", "number", minimum=60, maximum=7200, default=1800),
        },
        "strategy_fields": {
            "exit_rules": {
                "stop_loss_pct": _field("Stop Loss %", "number", minimum=-20, maximum=0, default=-5.0),
                "stop_loss_pct_clamp": _field("SL Clamp [min, max]", "list", default=[-3.0, -5.0]),
                "take_profit_pct": _field("Take Profit %", "number", minimum=0, maximum=50, default=8.0),
                "take_profit_pct_clamp": _field("TP Clamp [min, max]", "list", default=[6.0, 10.0]),
                "stagnation_hours": _field("Stagnation Hours", "number", minimum=1, maximum=168, default=3),
                "stagnation_threshold_pct": _field("Stagnation Threshold %", "number", minimum=0, maximum=10, default=1.5),
                "momentum_death_vol_ratio": _field("Momentum Death Vol Ratio", "number", minimum=0, maximum=2, default=0.4),
                "momentum_death_grace_hours": _field("Momentum Death Grace Hours", "number", minimum=1, maximum=168, default=5),
                "ob_exhaustion_rsi": _field("OB Exhaustion RSI", "number", minimum=50, maximum=100, default=80),
                "trailing_sl_pct": _field("Trailing SL %", "number", minimum=0, maximum=20, default=3.0),
                "trailing_activation_pct": _field("Trailing Activation %", "number", minimum=0, maximum=50, default=4.0),
            },
            "entry_criteria": {
                "min_signals": _field("Min Signals", "number", minimum=1, maximum=15, default=5),
                "min_signal_families": _field("Min Signal Families", "number", minimum=1, maximum=6, default=3),
                "min_vol_ratio": _field("Min Vol Ratio", "number", minimum=0, maximum=10, default=1.3),
                "direction_mode": _field("Direction Mode", "enum", choices=["both", "long", "short"], default="both"),
                "require_daily_trend_agreement": _field("Require Daily Trend Agreement", "bool", default=True),
                "require_btc_regime_ok_for_alts": _field("Require BTC Regime for Alts", "bool", default=True),
                "require_btc_regime_alignment": _field("Symmetric BTC Regime Alignment", "bool", default=False),
                "min_avg_dollar_volume": _field("Min Avg Dollar Volume", "number", minimum=0, maximum=1e9, default=500000),
                "bearish_macro_min_signals": _field("Bearish Macro Min Signals", "number", minimum=1, maximum=15, default=6),
                "bearish_macro_threshold": _field("Bearish Macro Threshold", "number", minimum=0, maximum=1, default=0.3),
                "regime_lookback_days": _field("Regime Lookback Days", "number", minimum=7, maximum=200, default=55),
                "regime_persistence_bars": _field("Regime Persistence Bars", "number", minimum=1, maximum=20, default=3),
                "regime_neutral_mode": _field("Regime Neutral Mode", "enum", choices=["block", "reduce", "allow"], default="block"),
                "btc_self_filter": _field("BTC Self-Filter (apply regime to BTC)", "bool", default=True),
            },
            "position_sizing": {
                "max_positions": _field("Max Positions", "number", minimum=1, maximum=50, default=3),
                "normal_sizing_min_pct": _field("Normal Sizing Min %", "number", minimum=1, maximum=100, default=12),
                "normal_sizing_max_pct": _field("Normal Sizing Max %", "number", minimum=1, maximum=100, default=16),
                "approaching_sizing_min_pct": _field("Approaching Sizing Min %", "number", minimum=1, maximum=100, default=8),
                "approaching_sizing_max_pct": _field("Approaching Sizing Max %", "number", minimum=1, maximum=100, default=12),
                "final_stretch_tp_pct": _field("Final Stretch TP %", "number", minimum=0, maximum=20, default=5.0),
                "max_position_dollar_cap": _field("Max Position $ Cap", "number_or_null", default=None),
                "slippage_buffer_pct": _field("Slippage Buffer %", "number", minimum=0, maximum=5, default=0.15),
                "daily_loss_size_cut_pct": _field("Daily Loss Size Cut %", "number", minimum=0, maximum=100, default=50),
                "consecutive_loss_threshold": _field("Consecutive Loss Threshold", "number", minimum=1, maximum=20, default=3),
                "consecutive_loss_size_cut_pct": _field("Consecutive Loss Size Cut %", "number", minimum=0, maximum=100, default=50),
                "consecutive_loss_min_signals": _field("Consecutive Loss Min Signals", "number", minimum=1, maximum=15, default=6),
            },
            "switch_logic": {
                "switch_score_threshold_pct": _field("Switch Score Threshold %", "number", minimum=0, maximum=100, default=30),
                "switch_require_profitable": _field("Switch Require Profitable", "bool", default=True),
                "reentry_cooldown_hours": _field("Reentry Cooldown Hours", "number", minimum=0, maximum=168, default=8),
            },
            "scoring_weights": {
                "signal_count_weight": _field("Signal Count Weight", "number", minimum=0, maximum=1, default=0.30),
                "family_diversity_weight": _field("Family Diversity Weight", "number", minimum=0, maximum=1, default=0.25),
                "candle_quality_weight": _field("Candle Quality Weight", "number", minimum=0, maximum=1, default=0.15),
                "consolidation_bonus_weight": _field("Consolidation Bonus Weight", "number", minimum=0, maximum=1, default=0.15),
                "trend_strength_weight": _field("Trend Strength Weight", "number", minimum=0, maximum=1, default=0.15),
            },
            "indicators": {
                "candle_interval": _field("Candle Interval", "enum", choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"], default="1d"),
                "confirm_interval": _field("Confirmation Interval", "enum", choices=["1h", "4h", "1d"], default="1d"),
                "lookback_period": _field("Lookback Period", "enum", choices=["1mo", "3mo", "6mo", "1y", "2y"], default="1y"),
                "rsi_period": _field("RSI Period", "number", minimum=2, maximum=50, default=14),
                "rsi_bullish": _field("RSI Bullish", "number", minimum=0, maximum=100, default=55),
                "rsi_overbought": _field("RSI Overbought", "number", minimum=50, maximum=100, default=80),
                "rsi_oversold": _field("RSI Oversold", "number", minimum=0, maximum=50, default=25),
                "macd_fast": _field("MACD Fast", "number", minimum=2, maximum=50, default=12),
                "macd_slow": _field("MACD Slow", "number", minimum=5, maximum=100, default=26),
                "macd_signal": _field("MACD Signal", "number", minimum=2, maximum=50, default=9),
                "sma_periods": _field("SMA Periods", "list", default=[20, 50, 200]),
                "ema_periods": _field("EMA Periods", "list", default=[9, 21, 55]),
                "stochastic_period": _field("Stochastic Period", "number", minimum=2, maximum=50, default=14),
                "atr_period": _field("ATR Period", "number", minimum=2, maximum=50, default=14),
                "bb_squeeze_ratio": _field("BB Squeeze Ratio", "number", minimum=0, maximum=2, default=0.6),
                "candle_body_conviction": _field("Candle Body Conviction", "number", minimum=0, maximum=1, default=0.6),
                "candle_body_doji": _field("Candle Body Doji", "number", minimum=0, maximum=1, default=0.3),
                "vol_ratio_bullish": _field("Vol Ratio Bullish", "number", minimum=0, maximum=10, default=1.3),
                "vol_ratio_dead": _field("Vol Ratio Dead", "number", minimum=0, maximum=2, default=0.4),
            },
            "sweep": {
                "enabled": _field("Sweep Enabled", "bool", default=True),
                "sweep_min_vol_ratio": _field("Sweep Min Vol Ratio", "number", minimum=0, maximum=10, default=1.3),
                "sweep_min_price_change_pct": _field("Sweep Min Price Change %", "number", minimum=0, maximum=50, default=2.0),
                "sweep_max_qualifiers": _field("Sweep Max Qualifiers", "number", minimum=1, maximum=50, default=15),
            },
            "cycle_timing": {
                "poll_interval_default": _field("Default Poll Interval", "number", minimum=60, maximum=7200, default=1800),
                "poll_interval_min": _field("Min Poll Interval", "number", minimum=30, maximum=3600, default=300),
                "poll_interval_max": _field("Max Poll Interval", "number", minimum=60, maximum=7200, default=3600),
            },
            "exposure_controls": {
                "max_correlated_positions": _field("Max Correlated Positions", "number", minimum=1, maximum=10, default=2),
                "correlation_buckets": _field("Correlation Buckets", "list", default=[]),
                "reserve_btc_slot": _field("Reserve BTC Slot", "bool", default=False),
            },
        },
    },
    "equity_momentum": {
        "display_name": "BlitzRunner — Equity Momentum Scalp",
        "strategy_type": "momentum_scalp",
        "parity_status": "live_backtest_matched",
        "defaults": scan_core.DEFAULT_PARAMS,
        "shared_fields": {
            "watchlist": _field("Watchlist", "list", default=["NVDA", "TSLA", "META", "AMZN"]),
            "poll_interval": _field("Poll Interval (s)", "number", minimum=30, maximum=3600, default=60),
        },
        "strategy_fields": {
            "exit_rules": {
                "stop_loss_pct": _field("Stop Loss %", "number", minimum=-20, maximum=0, default=-2.0),
                "take_profit_pct": _field("Take Profit %", "number", minimum=0, maximum=20, default=2.0),
                "stagnation_cycles": _field("Stagnation Cycles", "number", minimum=1, maximum=100, default=6),
                "stagnation_threshold_pct": _field("Stagnation Threshold %", "number", minimum=0, maximum=10, default=0.3),
                "momentum_death_vol_ratio": _field("Momentum Death Vol Ratio", "number", minimum=0, maximum=2, default=0.7),
                "momentum_death_grace_bars": _field("Momentum Death Grace Bars", "number", minimum=0, maximum=50, default=3),
                "ob_exhaustion_rsi": _field("OB Exhaustion RSI", "number", minimum=50, maximum=100, default=75),
                "trailing_sl_pct": _field("Trailing SL %", "number", minimum=0, maximum=20, default=1.5),
                "trailing_activation_pct": _field("Trailing Activation %", "number", minimum=0, maximum=20, default=2.5),
            },
            "entry_criteria": {
                "min_signals": _field("Min Signals", "number", minimum=1, maximum=15, default=4),
                "min_signal_families": _field("Min Signal Families", "number", minimum=1, maximum=6, default=2),
                "min_vol_ratio": _field("Min Vol Ratio", "number", minimum=0, maximum=10, default=1.5),
                "bearish_macro_min_signals": _field("Bearish Macro Min Signals", "number", minimum=1, maximum=15, default=5),
                "bearish_macro_threshold": _field("Bearish Macro Threshold", "number", minimum=0, maximum=1, default=0.3),
            },
            "position_sizing": {
                "max_positions": _field("Max Positions", "number", minimum=1, maximum=50, default=1),
                "normal_sizing_min_pct": _field("Normal Sizing Min %", "number", minimum=1, maximum=100, default=25),
                "normal_sizing_max_pct": _field("Normal Sizing Max %", "number", minimum=1, maximum=100, default=40),
                "approaching_sizing_min_pct": _field("Approaching Sizing Min %", "number", minimum=1, maximum=100, default=15),
                "approaching_sizing_max_pct": _field("Approaching Sizing Max %", "number", minimum=1, maximum=100, default=25),
                "final_stretch_tp_pct": _field("Final Stretch TP %", "number", minimum=0, maximum=20, default=1.5),
                "max_position_dollar_cap": _field("Max Position $ Cap", "number_or_null", default=None),
                "slippage_buffer_pct": _field("Slippage Buffer %", "number", minimum=0, maximum=5, default=0.1),
                "daily_loss_size_cut_pct": _field("Daily Loss Size Cut %", "number", minimum=0, maximum=100, default=50),
                "consecutive_loss_threshold": _field("Consecutive Loss Threshold", "number", minimum=1, maximum=20, default=3),
                "consecutive_loss_size_cut_pct": _field("Consecutive Loss Size Cut %", "number", minimum=0, maximum=100, default=50),
                "consecutive_loss_min_signals": _field("Consecutive Loss Min Signals", "number", minimum=1, maximum=15, default=5),
            },
        },
    },
}

# Legacy alias for backward compat
_PROFILE_DEFAULTS = {
    "equity_momentum": scan_core.DEFAULT_PARAMS,
    "crypto_swing": crypto_scan_core.CRYPTO_DEFAULT_PARAMS,
}


# ============================================================
# Deep Merge
# ============================================================

def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ============================================================
# Profile Resolution
# ============================================================

def profile_for(agent_name: str = "", strategy_type: str = "") -> str:
    identity = f"{agent_name} {strategy_type}".lower()
    return "crypto_swing" if "crypto" in identity else "equity_momentum"


def get_schema(agent_name: str = "", strategy_type: str = "") -> dict[str, Any]:
    profile = profile_for(agent_name, strategy_type)
    return STRATEGY_SCHEMAS.get(profile, {})


def effective_params(
    agent_name: str = "",
    strategy_type: str = "",
    stored: dict[str, Any] | None = None,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve effective parameters from defaults → stored DB config → temporary override.

    Returns a dict with:
      - all strategy params merged and validated
      - risk_controls merged and validated
      - _meta with source tracking for parity proof
    """
    profile = profile_for(agent_name, strategy_type)
    schema = STRATEGY_SCHEMAS.get(profile, {})
    defaults = schema.get("defaults", _PROFILE_DEFAULTS.get(profile, {}))

    # Layer 1: canonical defaults
    params = copy.deepcopy(defaults)

    # Layer 2: stored DB config
    stored = stored or {}
    params = deep_merge(params, stored)

    # Layer 3: temporary test override (not persisted)
    sources: dict[str, str] = {}
    if override:
        params = deep_merge(params, override)
        sources["override"] = "temporary_test"

    # Risk controls (shared across all strategies)
    risk_merged = deep_merge(RISK_CONTROL_DEFAULTS, stored.get("risk_controls", {}))
    if override and override.get("risk_controls"):
        risk_merged = deep_merge(risk_merged, override["risk_controls"])
    params["risk_controls"] = risk_merged
    params["risk_controls"].setdefault(
        "max_positions",
        params.get("position_sizing", {}).get("max_positions", 1),
    )

    params["profile"] = profile
    params["schema_version"] = 2
    params["_meta"] = {
        "schema_name": profile,
        "display_name": schema.get("display_name", profile),
        "parity_status": schema.get("parity_status", "unknown"),
        "sources": sources,
    }

    validate_params(params)
    return params


def effective_params_with_sources(
    agent_name: str = "",
    strategy_type: str = "",
    stored: dict[str, Any] | None = None,
    override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (effective_params, source_metadata) for UI parity display."""
    params = effective_params(agent_name, strategy_type, stored, override)
    meta = params.pop("_meta", {})

    source_map: dict[str, str] = {}
    defaults = _PROFILE_DEFAULTS.get(meta.get("schema_name", ""), {})

    def _trace_sources(key_path: str, default_val: Any, stored_val: Any, override_val: Any):
        if override_val is not None and override_val != default_val:
            source_map[key_path] = "temporary_test_override"
        elif stored_val is not None and stored_val != default_val:
            source_map[key_path] = "database"
        else:
            source_map[key_path] = "defaults"

    meta["field_sources"] = source_map
    return params, meta


def get_config_schema(agent_name: str = "", strategy_type: str = "") -> dict[str, Any]:
    """Return the full schema for UI rendering (shared + strategy fields + risk controls)."""
    schema = get_schema(agent_name, strategy_type)
    if not schema:
        return {}

    risk_fields = {
        "paper_only": _field("Paper Only", "bool", default=True),
        "paper_account_budget": _field("Paper Account Budget", "number", minimum=0, maximum=1e9, default=10000.0),
        "sizing_mode": _field("Sizing Mode", "enum", choices=["risk_based", "notional"], default="risk_based"),
        "risk_per_trade_pct": _field("Risk Per Trade %", "number", minimum=0.01, maximum=5.0, default=0.50),
        "max_trade_notional_pct": _field("Max Trade Notional %", "number", minimum=0.1, maximum=100.0, default=25.0),
        "max_open_risk_pct": _field("Max Open Risk %", "number", minimum=0.01, maximum=20.0, default=1.50),
        "max_gross_exposure_pct": _field("Max Gross Exposure %", "number", minimum=0, maximum=100, default=100.0),
        "max_position_dollar_cap": _field("Max Position $ Cap", "number_or_null", default=None),
        "daily_loss_halt_pct": _field("Daily Loss Halt %", "number", minimum=0.1, maximum=100.0, default=3.0),
        "loss_streak_size_cut_pct": _field("Loss Streak Size Cut %", "number", minimum=0, maximum=100, default=50.0),
    }

    return {
        "schema_name": schema.get("strategy_type", ""),
        "display_name": schema.get("display_name", ""),
        "parity_status": schema.get("parity_status", "unknown"),
        "shared_fields": schema.get("shared_fields", {}),
        "strategy_fields": schema.get("strategy_fields", {}),
        "risk_controls": risk_fields,
    }


# ============================================================
# Position Sizing
# ============================================================

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


# ============================================================
# Validation
# ============================================================

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

    # Validate strategy-specific fields against schema
    profile = params.get("profile", "")
    schema = STRATEGY_SCHEMAS.get(profile)
    if not schema:
        return
    _validate_strategy_fields(params, schema.get("strategy_fields", {}))


def _validate_strategy_fields(params: dict[str, Any], schema_fields: dict[str, Any]) -> None:
    for section, fields in schema_fields.items():
        section_vals = params.get(section, {})
        if not isinstance(section_vals, dict):
            continue
        for field_name, field_def in fields.items():
            if field_name not in section_vals:
                continue
            val = section_vals[field_name]
            ftype = field_def.get("type", "")
            if ftype == "number":
                _range(section_vals, field_name,
                       field_def.get("min", -1e18),
                       field_def.get("max", 1e18))
            elif ftype == "enum":
                choices = field_def.get("choices", [])
                if val not in choices:
                    raise ValueError(f"{section}.{field_name} must be one of {choices}")


def _range(values: dict[str, Any], key: str, minimum: float, maximum: float) -> None:
    try:
        value = float(values.get(key))
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be numeric") from None
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
