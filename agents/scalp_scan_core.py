"""
scalp_scan_core.py — Pure, side-effect-free scalp strategy logic.

Implements the 4-step scalp trading process:
  1. Discover: shortlist from movers/news/scanner (handled in scalp_scan.py)
  2. Filter: liquidity scoring (spread, depth, dollar volume)
  3. Analyze: multi-timeframe (1m/5m/15m) + Fibonacci + S/R + breakout patterns
  4. Pre-position: compute entry/SL/TP levels for stop-limit orders

This module has NO network I/O — every function takes DataFrames or
pre-computed dicts as input. This lets the exact same logic be replayed
against live data (scalp_scan.py) or historical data (scalp_scan_backtester.py)
with zero drift between the two.

Mirrors the pattern established by scan_core.py and crypto_scan_core.py.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

# Reuse shared indicator computations from scan_core
from scan_core import (
    compute_rsi, compute_macd, compute_sma, compute_ema,
    compute_atr, compute_bollinger, compute_stochastic,
    compute_obv, compute_vwap, candle_body_ratio, candle_quality,
    detect_obv_divergence, detect_consolidation_breakout,
    precompute_indicators, deep_scan_from_precomputed,
    _safe_float,
)


# ============================================================
# Canonical Default Strategy Parameters
# ============================================================

SCALP_DEFAULT_PARAMS: dict[str, Any] = {
    "entry_criteria": {
        "min_signals": 3,
        "min_signal_families": 2,
        "min_vol_ratio": 1.5,
        "max_spread_pct": 0.15,
        "min_dollar_volume": 1_000_000,
        "min_depth_dollars": 50_000,
        "require_trend_agreement": True,
        "block_on_obv_divergence": True,
        "direction_mode": "adaptive",
    },
    "timeframes": {
        "entry_interval": "1m",
        "pattern_interval": "5m",
        "trend_interval": "15m",
        "lookback_bars": 200,
    },
    "levels": {
        "fib_retracement": [0.382, 0.5, 0.618, 0.786],
        "fib_extension": [1.272, 1.618],
        "sr_lookback_bars": 50,
        "sr_min_touches": 2,
        "sr_tolerance_pct": 0.15,
        "breakout_confirm_bars": 3,
    },
    "premove_filter": {
        "enabled": True,
        "max_move_pct": 2.0,
        "lookback_bars": 8,
    },
    "market_regime": {
        "enabled": True,
        "symbol": "SPY",
        "daily_ema_period": 10,
        "block_shorts_in_bull": True,
        "block_longs_in_bear": False,
        "threshold_pct": 0.0,
        "adaptive_direction": True,
        "adaptive_long_in_bull": True,
        "adaptive_short_in_bear": True,
        "adaptive_both_in_neutral": True,
    },
    "indicators": {
        "rsi_period": 14,
        "rsi_bullish": 55,
        "rsi_overbought": 75,
        "rsi_oversold": 25,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "ema_periods": [9, 21, 55],
        "atr_period": 14,
        "bb_squeeze_ratio": 0.6,
        "candle_body_conviction": 0.6,
        "candle_body_doji": 0.3,
        "vol_ratio_bullish": 2.0,
        "vol_ratio_dead": 0.5,
        "tape_reading": {
            "enabled": True,
            "velocity_lookback": 5,
            "velocity_threshold": 1.5,
            "vol_accel_lookback": 10,
            "vol_accel_threshold": 1.8,
            "velocity_weight": 0.05,
            "vol_accel_weight": 0.05,
        },
    },
    "cycle_timing": {
        "poll_interval_default": 15,
        "poll_interval_min": 5,
        "poll_interval_max": 60,
    },
    "breakout_detection": {
        "approaching_threshold_pct": 0.5,
        "consolidation_threshold_pct": 1.0,
    },
    "pattern_detection": {
        "min_bars": 20,
        "consolidation_lookback": 3,
        "range_breakout_confidence": 0.7,
        "flag_min_bars": 15,
        "flag_strong_move_bars": 5,
        "flag_consolidation_bars": 10,
        "flag_min_move_pct": 1.5,
        "flag_max_consolidation_range_pct": 1.0,
        "flag_confidence": 0.6,
        "wedge_min_bars": 20,
        "wedge_lookback": 20,
        "wedge_confidence": 0.5,
    },
    "liquidity_scoring": {
        "avg_bars": 20,
        "spread_weight": 0.4,
        "depth_weight": 0.3,
        "volume_weight": 0.3,
        "good_threshold": 0.6,
        "marginal_threshold": 0.3,
    },
    "trend_detection": {
        "rsi_bullish": 55,
        "rsi_bearish": 45,
        "max_signals": 4,
    },
    "scoring_weights": {
        "confluence_weight": 0.30,
        "level_alignment_weight": 0.25,
        "pattern_weight": 0.20,
        "liquidity_weight": 0.15,
        "volume_momentum_weight": 0.10,
    },
    "scoring_thresholds": {
        "min_qualification_score": 4.0,
        "fib_near_threshold_pct": 0.5,
        "fib_medium_threshold_pct": 1.0,
        "fib_near_score": 0.7,
        "fib_medium_score": 0.3,
        "level_alignment_ready": 1.0,
        "level_alignment_approaching": 0.5,
        "confluence_max": 2.0,
        "score_scale": 10.0,
    },
    "technical": {
        "atr_fallback_pct": 0.2,
        "min_confluence_for_agreement": 2,
        "swing_window": 2,
        "swing_min_bars": 5,
        "min_bars_precompute": 30,
        "sr_strength_normalization": 10.0,
    },
    "discovery": {
        "movers_enabled": True,
        "movers_indices": ["$COMPX", "$DJI", "$SPX"],
        "news_enabled": True,
        "news_lookback_hours": 4,
        "scanner_enabled": True,
        "scanner_min_vol_ratio": 2.0,
        "scanner_min_price_change_pct": 0.5,
        "scanner_universe_size": 100,
        "max_shortlist": 15,
        "scanner_universe": [
            "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
            "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS",
            "BABA", "JD", "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL",
            "F", "GM", "NIO", "XPEV", "PLUG", "FCEL", "DKNG", "PENN",
        ],
        "fallback_shortlist": [
            "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
        ],
        "scanner_interval": "5m",
        "scanner_lookback_bars": 50,
        "scanner_min_bars": 20,
        "scanner_vol_lookback_bars": 20,
        "news_limit": 50,
        "news_process_limit": 50,
        "news_max_ticker_length": 5,
        "news_max_symbols": 20,
        "catalyst": {
            "enabled": True,
            "fresh_window_hours": 4,
            "min_confidence": 0.60,
            "bullish_boost": 1.5,
            "bearish_penalty": 0.5,
            "no_catalyst_penalty": 0.9,
            "block_bearish_catalyst": False,
        },
    },
    "data_fetch": {
        "intraday_period": "5d",
        "daily_period": "3mo",
        "default_period": "1mo",
        "intraday_min_bars": 30,
        "daily_min_bars": 10,
        "entry_min_bars": 30,
    },
    "exit_rules": {
        "stop_loss_pct": -1.0,
        "take_profit_pct": 1.5,
        "trailing_sl_pct": 0.4,
        "trailing_activation_pct": 0.5,
        "stagnation_minutes": 10,
        "stagnation_threshold_pct": 0.1,
        "momentum_death_vol_ratio": 0.5,
        "momentum_death_grace_bars": 5,
        "ob_exhaustion_rsi": 78,
        "exit_mode": "set_and_forget",
        "reentry_cooldown_cycles": 3,
        "default_rsi": 50,
        "adaptive_exit": True,
        "phase1_minutes": 15,
        "phase1_sl_atr_multiple": 1.5,
        "phase2_minutes": 45,
        "phase2_sl_atr_multiple": 1.0,
        "phase2_trailing_activation_pct": 0.4,
        "phase3_sl_atr_multiple": 0.5,
        "phase3_stagnation_exit": True,
    },
    "position_sizing": {
        "max_positions": 3,
        "max_pending_orders": 5,
        "normal_sizing_min_pct": 5,
        "normal_sizing_max_pct": 10,
        "risk_per_trade_pct": 0.25,
        "consecutive_loss_threshold": 3,
        "consecutive_loss_size_cut_pct": 50,
        "final_stretch_threshold_pct": 80.0,
    },
    "order": {
        "stop_limit_offset_pct": 0.02,
        "entry_trigger_offset_pct": 0.08,
        "order_expiry_minutes": 180,
        "sl_atr_multiple": 1.5,
        "tp_atr_multiple": 2.5,
        "market_type": "us-stock",
        "order_type": "stop_limit",
        "price_decimals": 6,
        "default_stop_distance_pct": 1.0,
    },
    "watchlist": [],
}


# ============================================================
# Fibonacci Levels
# ============================================================

def detect_swing_highs_lows(df: pd.DataFrame, lookback: int = 50,
                            params: dict | None = None) -> dict[str, list]:
    """Find recent swing highs and lows using fractal detection.

    A swing high is a bar whose high is higher than the highs of the
    bars on either side (left/right window). Same logic inverted for lows.

    Returns {"swing_highs": [(bar_idx, price)], "swing_lows": [(bar_idx, price)]}
    """
    p = params or {}
    tech_cfg = p.get("technical", {})
    window = int(tech_cfg.get("swing_window", 2))
    min_bars = int(tech_cfg.get("swing_min_bars", 5))

    if df is None or df.empty or len(df) < min_bars:
        return {"swing_highs": [], "swing_lows": []}

    recent = df.iloc[-lookback:] if len(df) > lookback else df
    highs = recent["High"].values
    lows = recent["Low"].values
    offset = len(df) - len(recent)

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    for i in range(window, len(recent) - window):
        # Swing high: higher than `window` bars on each side
        is_high = all(highs[i] > highs[i - j] for j in range(1, window + 1)) and \
                  all(highs[i] > highs[i + j] for j in range(1, window + 1))
        if is_high:
            swing_highs.append((offset + i, float(highs[i])))

        # Swing low: lower than `window` bars on each side
        is_low = all(lows[i] < lows[i - j] for j in range(1, window + 1)) and \
                 all(lows[i] < lows[i + j] for j in range(1, window + 1))
        if is_low:
            swing_lows.append((offset + i, float(lows[i])))

    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


def compute_fib_retracement(swing_high: float, swing_low: float,
                            direction: str = "long",
                            params: dict | None = None) -> dict[str, float]:
    """Compute Fibonacci retracement levels.

    For longs: levels are below the swing high (potential entry on pullback).
    For shorts: levels are above the swing low.

    Returns {"0.382": price, "0.5": price, "0.618": price, "0.786": price}
    """
    diff = swing_high - swing_low
    if diff <= 0:
        return {}

    levels = {}
    fib_ratios = (params or {}).get("levels", {}).get("fib_retracement", [0.382, 0.5, 0.618, 0.786])
    for ratio in fib_ratios:
        if direction == "long":
            levels[str(ratio)] = swing_high - diff * ratio
        else:
            levels[str(ratio)] = swing_low + diff * ratio
    return levels


def compute_fib_extension(swing_high: float, swing_low: float,
                          direction: str = "long",
                          params: dict | None = None) -> dict[str, float]:
    """Compute Fibonacci extension levels for TP targets.

    For longs: levels above the swing high (profit targets).
    For shorts: levels below the swing low.

    Returns {"1.272": price, "1.618": price}
    """
    diff = swing_high - swing_low
    if diff <= 0:
        return {}

    levels = {}
    fib_ratios = (params or {}).get("levels", {}).get("fib_extension", [1.272, 1.618])
    for ratio in fib_ratios:
        if direction == "long":
            levels[str(ratio)] = swing_low + diff * ratio
        else:
            levels[str(ratio)] = swing_high - diff * ratio
    return levels


def nearest_fib_level(price: float, fib_levels: dict[str, float]) -> Optional[dict]:
    """Find the nearest Fib level to the current price.

    Returns {"ratio": "0.618", "price": 123.45, "distance_pct": 0.5}
    """
    if not fib_levels:
        return None
    nearest = min(fib_levels.items(), key=lambda kv: abs(kv[1] - price))
    ratio, level_price = nearest
    distance_pct = abs(price - level_price) / price * 100 if price > 0 else 0
    return {"ratio": ratio, "price": level_price, "distance_pct": distance_pct}


# ============================================================
# Support / Resistance Detection
# ============================================================

def detect_support_resistance(df: pd.DataFrame, lookback: int = 50,
                              min_touches: int = 2,
                              tolerance_pct: float = 0.15,
                              params: dict | None = None) -> dict[str, Any]:
    """Detect support/resistance levels from clustered swing points.

    Groups swing highs and lows into clusters. A cluster with >= min_touches
    becomes a level. Supports are below current price, resistances above.

    Returns {"supports": [price], "resistances": [price],
             "levels": [{"price", "type", "touches", "strength"}]}
    """
    if df is None or df.empty:
        return {"supports": [], "resistances": [], "levels": []}

    swings = detect_swing_highs_lows(df, lookback)
    all_points = [p for _, p in swings["swing_highs"]] + \
                 [p for _, p in swings["swing_lows"]]

    if not all_points:
        return {"supports": [], "resistances": [], "levels": []}

    current_price = float(df["Close"].iloc[-1])
    tolerance = current_price * tolerance_pct / 100

    # Cluster nearby price points
    all_points.sort()
    clusters: list[list[float]] = []
    for price in all_points:
        if clusters and abs(price - np.mean(clusters[-1])) <= tolerance:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    # Build levels from clusters with enough touches
    levels = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        level_price = float(np.mean(cluster))
        level_type = "resistance" if level_price > current_price else "support"
        levels.append({
            "price": level_price,
            "type": level_type,
            "touches": len(cluster),
            "strength": len(cluster) / float((params or {}).get("technical", {}).get("sr_strength_normalization", 10.0)),
        })

    levels.sort(key=lambda l: abs(l["price"] - current_price))
    supports = [l["price"] for l in levels if l["type"] == "support"]
    resistances = [l["price"] for l in levels if l["type"] == "resistance"]

    return {
        "supports": supports,
        "resistances": resistances,
        "levels": levels,
    }


def nearest_sr_level(price: float, sr_levels: dict[str, Any]) -> Optional[dict]:
    """Find the nearest S/R level to current price.

    Returns {"price": level_price, "type": "support"/"resistance", "distance_pct": x}
    """
    all_levels = sr_levels.get("levels", [])
    if not all_levels:
        return None
    nearest = min(all_levels, key=lambda l: abs(l["price"] - price))
    distance_pct = abs(price - nearest["price"]) / price * 100 if price > 0 else 0
    return {
        "price": nearest["price"],
        "type": nearest["type"],
        "touches": nearest["touches"],
        "distance_pct": distance_pct,
    }


# ============================================================
# Breakout Pattern Detection
# ============================================================

def detect_breakout_level(df_5m: pd.DataFrame, sr_levels: dict[str, Any],
                          params: dict) -> dict[str, Any]:
    """Identify the nearest breakout level and whether price is approaching it.

    Returns {"level_price", "level_type", "distance_pct",
             "bars_since_touch", "ready_to_break"}
    """
    levels_cfg = params.get("levels", {})
    confirm_bars = levels_cfg.get("breakout_confirm_bars", 3)
    bo_cfg = params.get("breakout_detection", {})
    approaching_threshold = float(bo_cfg.get("approaching_threshold_pct", 0.5))
    consolidation_threshold = float(bo_cfg.get("consolidation_threshold_pct", 1.0))

    if df_5m is None or df_5m.empty:
        return {"ready_to_break": False, "level_price": 0, "distance_pct": 999}

    current_price = float(df_5m["Close"].iloc[-1])
    nearest = nearest_sr_level(current_price, sr_levels)
    if not nearest:
        return {"ready_to_break": False, "level_price": 0, "distance_pct": 999}

    level_price = nearest["price"]
    level_type = nearest["type"]
    distance_pct = nearest["distance_pct"]

    # Check if price has been approaching (within threshold of the level)
    approaching = distance_pct < approaching_threshold

    # Check if price has been consolidating near the level
    # (last `confirm_bars` bars all within consolidation_threshold of the level)
    recent = df_5m.iloc[-confirm_bars:]
    near_level = all(
        abs(float(bar["Close"]) - level_price) / level_price < consolidation_threshold / 100.0
        for _, bar in recent.iterrows()
    )

    # Ready to break = approaching + consolidating near level
    ready = approaching and near_level

    return {
        "level_price": level_price,
        "level_type": level_type,
        "distance_pct": distance_pct,
        "approaching": approaching,
        "near_level": near_level,
        "ready_to_break": ready,
    }


def detect_pattern(df_5m: pd.DataFrame, params: dict | None = None) -> dict[str, Any]:
    """Detect simple chart patterns on 5m chart.

    Detects:
    - Range breakout (consolidation → expansion)
    - Flag/pennant (tight pullback after strong move)
    - Wedge (converging trendlines)

    Returns {"pattern_type", "direction", "confidence"}
    """
    p = params or {}
    pd_cfg = p.get("pattern_detection", {})

    min_bars = int(pd_cfg.get("min_bars", 20))
    cons_lookback = int(pd_cfg.get("consolidation_lookback", 3))
    bo_conf = float(pd_cfg.get("range_breakout_confidence", 0.7))
    flag_min_bars = int(pd_cfg.get("flag_min_bars", 15))
    flag_strong_bars = int(pd_cfg.get("flag_strong_move_bars", 5))
    flag_cons_bars = int(pd_cfg.get("flag_consolidation_bars", 10))
    flag_min_move = float(pd_cfg.get("flag_min_move_pct", 1.5))
    flag_max_cons = float(pd_cfg.get("flag_max_consolidation_range_pct", 1.0))
    flag_conf = float(pd_cfg.get("flag_confidence", 0.6))
    wedge_min_bars = int(pd_cfg.get("wedge_min_bars", 20))
    wedge_lookback = int(pd_cfg.get("wedge_lookback", 20))
    wedge_conf = float(pd_cfg.get("wedge_confidence", 0.5))

    if df_5m is None or df_5m.empty or len(df_5m) < min_bars:
        return {"pattern_type": "none", "direction": "neutral", "confidence": 0.0}

    # Range breakout: was consolidating, now breaking
    cons_bo = detect_consolidation_breakout(df_5m, lookback=cons_lookback)
    if cons_bo:
        last = df_5m.iloc[-1]
        direction = "long" if last["Close"] > last["Open"] else "short"
        return {"pattern_type": "range_breakout", "direction": direction, "confidence": bo_conf}

    # Flag/pennant: strong move followed by tight consolidation
    if len(df_5m) >= flag_min_bars:
        # Strong move in first `flag_strong_bars` bars
        first_bars = df_5m.iloc[-flag_min_bars:-flag_cons_bars]
        last_bars = df_5m.iloc[-flag_cons_bars:]
        move_pct = (first_bars["Close"].iloc[-1] / first_bars["Close"].iloc[0] - 1) * 100
        # Consolidation range in last `flag_cons_bars` bars
        cons_range = (last_bars["High"].max() - last_bars["Low"].min()) / last_bars["Close"].mean() * 100

        if abs(move_pct) > flag_min_move and cons_range < flag_max_cons:
            direction = "long" if move_pct > 0 else "short"
            return {"pattern_type": "flag", "direction": direction, "confidence": flag_conf}

    # Wedge: converging highs and lows
    if len(df_5m) >= wedge_min_bars:
        recent = df_5m.iloc[-wedge_lookback:]
        highs = recent["High"].values
        lows = recent["Low"].values

        # Simple linear regression slope
        x = np.arange(len(highs))
        high_slope = np.polyfit(x, highs, 1)[0] if len(highs) > 1 else 0
        low_slope = np.polyfit(x, lows, 1)[0] if len(lows) > 1 else 0

        # Converging: highs descending, lows ascending (or vice versa)
        if high_slope < 0 and low_slope > 0:
            return {"pattern_type": "wedge_bearish", "direction": "short", "confidence": wedge_conf}
        elif high_slope > 0 and low_slope > 0 and high_slope > low_slope:
            return {"pattern_type": "wedge_bullish", "direction": "long", "confidence": wedge_conf}

    return {"pattern_type": "none", "direction": "neutral", "confidence": 0.0}


# ============================================================
# Liquidity Scoring
# ============================================================

def liquidity_score(quote: dict, level2: Optional[dict],
                    df: pd.DataFrame, params: dict) -> dict[str, Any]:
    """Score liquidity quality from spread, depth, and dollar volume.

    Returns {"score" (0-1), "spread_pct", "depth_dollars",
             "dollar_volume", "verdict" ("good"/"marginal"/"poor")}
    """
    entry_cfg = params.get("entry_criteria", {})
    liq_cfg = params.get("liquidity_scoring", {})
    max_spread = entry_cfg.get("max_spread_pct", 0.15)
    min_depth = entry_cfg.get("min_depth_dollars", 50_000)
    min_dollar_vol = entry_cfg.get("min_dollar_volume", 1_000_000)
    avg_bars = int(liq_cfg.get("avg_bars", 20))
    spread_weight = float(liq_cfg.get("spread_weight", 0.4))
    depth_weight = float(liq_cfg.get("depth_weight", 0.3))
    volume_weight = float(liq_cfg.get("volume_weight", 0.3))
    good_threshold = float(liq_cfg.get("good_threshold", 0.6))
    marginal_threshold = float(liq_cfg.get("marginal_threshold", 0.3))

    spread_pct = quote.get("spread_pct", 999) if quote else 999
    total_volume = quote.get("total_volume", 0) if quote else 0

    # Dollar volume: use recent bars if available
    if df is not None and not df.empty:
        avg_price = float(df["Close"].tail(avg_bars).mean())
        avg_vol = float(df["Volume"].tail(avg_bars).mean())
        dollar_volume = avg_price * avg_vol
    else:
        dollar_volume = 0

    # Depth from Level 2
    depth_dollars = 0
    if level2:
        depth_dollars = level2.get("total_depth_dollars", 0)

    # Score components (each 0-1)
    spread_score = max(0, 1 - (spread_pct / max_spread)) if max_spread > 0 else 0
    depth_score = min(1, depth_dollars / min_depth) if min_depth > 0 else 0
    vol_score = min(1, dollar_volume / min_dollar_vol) if min_dollar_vol > 0 else 0

    # Composite (weighted)
    score = (spread_score * spread_weight + depth_score * depth_weight + vol_score * volume_weight)

    if score >= good_threshold:
        verdict = "good"
    elif score >= marginal_threshold:
        verdict = "marginal"
    else:
        verdict = "poor"

    return {
        "score": round(score, 3),
        "spread_pct": round(spread_pct, 4),
        "depth_dollars": round(depth_dollars, 2),
        "dollar_volume": round(dollar_volume, 2),
        "verdict": verdict,
        "passes": verdict != "poor",
    }


# ============================================================
# Multi-Timeframe Engine
# ============================================================

def precompute_indicators_multi_tf(
    df_1m: pd.DataFrame, df_5m: pd.DataFrame, df_15m: pd.DataFrame,
    params: dict,
) -> dict[str, Any]:
    """Precompute indicators on all 3 timeframes.

    Returns {"1m": pre_dict, "5m": pre_dict, "15m": pre_dict}
    where each pre_dict is the output of scan_core.precompute_indicators.
    """
    result = {}
    min_bars = int(params.get("technical", {}).get("min_bars_precompute", 30))
    for label, df in [("1m", df_1m), ("5m", df_5m), ("15m", df_15m)]:
        if df is None or df.empty or len(df) < min_bars:
            result[label] = None
        else:
            result[label] = precompute_indicators(df, params)
    return result


def _trend_direction(pre: dict, bar_idx: int, params: dict) -> dict[str, Any]:
    """Determine trend direction from a precomputed timeframe.

    Returns {"direction": "bullish"/"bearish"/"neutral", "strength": 0-1}
    """
    if pre is None or bar_idx < 0 or bar_idx >= pre["n"]:
        return {"direction": "neutral", "strength": 0}

    td_cfg = params.get("trend_detection", {})
    rsi_bull = float(td_cfg.get("rsi_bullish", 55))
    rsi_bear = float(td_cfg.get("rsi_bearish", 45))
    max_signals = float(td_cfg.get("max_signals", 4))

    sma20 = _safe_float(pre["sma20"][bar_idx])
    sma50 = _safe_float(pre["sma50"][bar_idx])
    price = _safe_float(pre["close"][bar_idx])
    macd_hist = _safe_float(pre["macd_hist"][bar_idx])
    rsi = _safe_float(pre["rsi"][bar_idx], 50.0)

    bullish_signals = 0
    bearish_signals = 0

    if sma20 > sma50 and sma50 > 0:
        bullish_signals += 1
    elif sma20 < sma50 and sma50 > 0:
        bearish_signals += 1

    if price > sma20 and sma20 > 0:
        bullish_signals += 1
    elif price < sma20 and sma20 > 0:
        bearish_signals += 1

    if macd_hist > 0:
        bullish_signals += 1
    elif macd_hist < 0:
        bearish_signals += 1

    if rsi > rsi_bull:
        bullish_signals += 1
    elif rsi < rsi_bear:
        bearish_signals += 1

    if bullish_signals > bearish_signals:
        direction = "bullish"
        strength = bullish_signals / max_signals
    elif bearish_signals > bullish_signals:
        direction = "bearish"
        strength = bearish_signals / max_signals
    else:
        direction = "neutral"
        strength = 0.0

    return {"direction": direction, "strength": round(strength, 2)}


def deep_scan_multi_tf(
    symbol: str, pre: dict, bar_idx_1m: int, params: dict,
    bar_idx_5m: Optional[int] = None,
    bar_idx_15m: Optional[int] = None,
) -> dict[str, Any]:
    """Multi-timeframe deep scan.

    Combines:
    - 1m indicators (entry timing) via scan_core.deep_scan_from_precomputed
    - 5m pattern detection
    - 15m trend direction (trend filter)
    - Cross-TF confluence score

    Returns a dict with all indicator data, confluence score, and qualification.
    """
    # 1m entry scan (reuse scan_core logic)
    pre_1m = pre.get("1m")
    if pre_1m is None:
        return {"error": "no_1m_data", "qualifies_for_entry": False}

    scan_1m = deep_scan_from_precomputed(symbol, pre_1m, bar_idx_1m, params)

    # 5m trend
    pre_5m = pre.get("5m")
    if bar_idx_5m is None:
        bar_idx_5m = min(bar_idx_1m, pre_5m["n"] - 1) if pre_5m else -1
    trend_5m = _trend_direction(pre_5m, bar_idx_5m, params)

    # 15m trend
    pre_15m = pre.get("15m")
    if bar_idx_15m is None:
        bar_idx_15m = min(bar_idx_1m, pre_15m["n"] - 1) if pre_15m else -1
    trend_15m = _trend_direction(pre_15m, bar_idx_15m, params)

    # Cross-TF confluence
    entry_cfg = params.get("entry_criteria", {})
    require_agreement = entry_cfg.get("require_trend_agreement", True)

    direction_1m = scan_1m.get("entry_direction", "long")
    # Map 1m direction ("long"/"short") to trend vocabulary ("bullish"/"bearish")
    dir_trend = "bullish" if direction_1m == "long" else "bearish"
    confluence = 0
    if trend_5m["direction"] == dir_trend:
        confluence += 1
    if trend_15m["direction"] == dir_trend:
        confluence += 1

    tech_cfg = params.get("technical", {})
    min_confluence = int(tech_cfg.get("min_confluence_for_agreement", 2))
    atr_fallback_pct = float(tech_cfg.get("atr_fallback_pct", 0.2))

    trend_agrees = confluence >= min_confluence or not require_agreement

    # ATR from 1m for SL/TP computation
    atr = _safe_float(pre_1m["atr"][bar_idx_1m])
    if atr <= 0:
        atr = _safe_float(pre_1m["close"][bar_idx_1m]) * atr_fallback_pct / 100.0

    # Override qualification with trend agreement
    base_qualifies = scan_1m.get("qualifies_for_entry", False)
    qualifies = base_qualifies and trend_agrees

    # Tape reading signals (opt-in)
    tape_cfg = params.get("indicators", {}).get("tape_reading", {})
    tape_signals: dict[str, Any] = {}
    if tape_cfg.get("enabled", False):
        closes_1m = pre_1m.get("close", [])
        volumes_1m = pre_1m.get("volume", [])
        tape_signals = compute_tape_signals(closes_1m, volumes_1m, bar_idx_1m, params)

    return {
        **scan_1m,
        "trend_5m": trend_5m,
        "trend_15m": trend_15m,
        "confluence_score": confluence,
        "trend_agrees": trend_agrees,
        "atr": atr,
        "qualifies_for_entry": qualifies,
        "tape_signals": tape_signals,
    }


# ============================================================
# Composite Setup Scoring
# ============================================================

def score_scalp_setup(
    mtf_result: dict, fib_levels: dict, sr_levels: dict,
    breakout: dict, pattern: dict, liquidity: dict, params: dict,
) -> dict[str, Any]:
    """Composite score for a scalp setup.

    Weighting:
    - Multi-TF confluence (30%)
    - Level alignment: price near Fib/S/R breakout (25%)
    - Pattern quality (20%)
    - Liquidity score (15%)
    - Volume momentum (10%)

    Returns {"score" (0-10), "qualifies", "entry_level", "sl_level",
             "tp_level", "direction", "reason"}
    """
    sw = params.get("scoring_weights", {})
    st = params.get("scoring_thresholds", {})
    tech_cfg = params.get("technical", {})

    # Multi-TF confluence (0-1)
    confluence_max = float(st.get("confluence_max", 2.0))
    confluence = mtf_result.get("confluence_score", 0) / confluence_max

    # Level alignment (0-1): is price near a Fib or S/R breakout level?
    level_alignment = 0.0
    la_ready = float(st.get("level_alignment_ready", 1.0))
    la_approaching = float(st.get("level_alignment_approaching", 0.5))
    fib_near_thresh = float(st.get("fib_near_threshold_pct", 0.5))
    fib_medium_thresh = float(st.get("fib_medium_threshold_pct", 1.0))
    fib_near_score = float(st.get("fib_near_score", 0.7))
    fib_medium_score = float(st.get("fib_medium_score", 0.3))

    if breakout.get("ready_to_break"):
        level_alignment = la_ready
    elif breakout.get("approaching"):
        level_alignment = la_approaching
    elif fib_levels:
        price = mtf_result.get("price", 0)
        nearest = nearest_fib_level(price, fib_levels)
        if nearest and nearest["distance_pct"] < fib_near_thresh:
            level_alignment = fib_near_score
        elif nearest and nearest["distance_pct"] < fib_medium_thresh:
            level_alignment = fib_medium_score

    # Pattern quality (0-1)
    pattern_conf = pattern.get("confidence", 0)

    # Liquidity score (0-1)
    liq_score = liquidity.get("score", 0)

    # Volume momentum (0-1)
    vol_ratio = 0
    indicators = mtf_result.get("indicators", {})
    vol_ind = indicators.get("vol_ratio", {})
    vol_ratio_val = vol_ind.get("value", 0)
    vol_bull = params.get("indicators", {}).get("vol_ratio_bullish", 2.0)
    vol_momentum = min(1.0, vol_ratio_val / vol_bull) if vol_bull > 0 else 0

    # Tape reading signals (opt-in)
    tape_cfg = params.get("indicators", {}).get("tape_reading", {})
    tape_enabled = tape_cfg.get("enabled", False)
    tape_score_val = 0.0
    tape_bullish = False
    tape_bearish = False
    if tape_enabled:
        tape_data = mtf_result.get("tape_signals", {})
        tape_score_val = tape_data.get("tape_score", 0.0)
        tape_bullish = tape_data.get("tape_bullish", False)
        tape_bearish = tape_data.get("tape_bearish", False)

    # Composite
    score_scale = float(st.get("score_scale", 10.0))
    base_score = (
        confluence * float(sw.get("confluence_weight", 0.30))
        + level_alignment * float(sw.get("level_alignment_weight", 0.25))
        + pattern_conf * float(sw.get("pattern_weight", 0.20))
        + liq_score * float(sw.get("liquidity_weight", 0.15))
        + vol_momentum * float(sw.get("volume_momentum_weight", 0.10))
    )
    # Add tape reading bonus when enabled
    if tape_enabled:
        tape_vel_w = float(tape_cfg.get("velocity_weight", 0.05))
        tape_vol_w = float(tape_cfg.get("vol_accel_weight", 0.05))
        base_score += tape_score_val * (tape_vel_w + tape_vol_w)
    score = base_score * score_scale

    # Determine direction
    direction = mtf_result.get("entry_direction", pattern.get("direction", "long"))
    if direction == "neutral":
        direction = "long"

    # Mean-reversion mode: invert the direction (fade the move instead of following it)
    entry_cfg = params.get("entry_criteria", {})
    if entry_cfg.get("entry_style", "breakout") == "mean_reversion":
        direction = "short" if direction == "long" else "long"

    # Tape reading direction bias: when tape strongly disagrees with pattern direction, flip
    if tape_enabled and tape_bullish and direction == "short":
        direction = "long"
    elif tape_enabled and tape_bearish and direction == "long":
        direction = "short"

    # Compute entry/SL/TP from levels + ATR
    price = mtf_result.get("price", 0)
    atr_fallback_pct = float(tech_cfg.get("atr_fallback_pct", 0.2))
    atr = mtf_result.get("atr", price * atr_fallback_pct / 100.0 if price > 0 else 0)
    order_cfg = params.get("order", {})
    # Support side-specific ATR multiples: long_sl_atr_multiple, short_sl_atr_multiple, etc.
    # Falls back to sl_atr_multiple / tp_atr_multiple for backward compatibility.
    if direction == "long":
        sl_mult = order_cfg.get("long_sl_atr_multiple", order_cfg.get("sl_atr_multiple", 1.0))
        tp_mult = order_cfg.get("long_tp_atr_multiple", order_cfg.get("tp_atr_multiple", 1.5))
    else:
        sl_mult = order_cfg.get("short_sl_atr_multiple", order_cfg.get("sl_atr_multiple", 1.0))
        tp_mult = order_cfg.get("short_tp_atr_multiple", order_cfg.get("tp_atr_multiple", 1.5))

    reference_level = breakout.get("level_price", price) if breakout.get("ready_to_break") else price
    trigger_offset_pct = float(order_cfg.get("entry_trigger_offset_pct", 0.08))
    if direction == "long":
        entry_level = reference_level * (1 + trigger_offset_pct / 100.0)
        sl_level = entry_level - sl_mult * atr
        tp_level = entry_level + tp_mult * atr
    else:
        entry_level = reference_level * (1 - trigger_offset_pct / 100.0)
        sl_level = entry_level + sl_mult * atr
        tp_level = entry_level - tp_mult * atr

    # Qualification
    entry_cfg = params.get("entry_criteria", {})
    min_score = float(st.get("min_qualification_score", 4.0))
    qualifies = (
        score >= min_score
        and mtf_result.get("qualifies_for_entry", False)
        and liquidity.get("passes", False)
    )

    return {
        "score": round(score, 2),
        "qualifies": qualifies,
        "direction": direction,
        "entry_level": round(entry_level, 6),
        "sl_level": round(sl_level, 6),
        "tp_level": round(tp_level, 6),
        "atr": round(atr, 6),
        "confluence": round(confluence, 2),
        "level_alignment": round(level_alignment, 2),
        "pattern_confidence": round(pattern_conf, 2),
        "liquidity_score": round(liq_score, 2),
        "vol_momentum": round(vol_momentum, 2),
        "tape_score": round(tape_score_val, 2) if tape_enabled else 0.0,
        "tape_bullish": tape_bullish,
        "tape_bearish": tape_bearish,
        "pattern_type": pattern.get("pattern_type", "none"),
        "breakout_level": breakout.get("level_price", 0),
        "reason": _build_reason(mtf_result, pattern, breakout, liquidity),
    }


def _build_reason(mtf: dict, pattern: dict, breakout: dict, liquidity: dict) -> str:
    """Build a human-readable reason string for the setup."""
    parts = []
    parts.append(f"score={mtf.get('composite_score', 0):.1f}")
    parts.append(f"confluence={mtf.get('confluence_score', 0)}/2")
    if pattern.get("pattern_type") != "none":
        parts.append(f"pattern={pattern['pattern_type']}")
    if breakout.get("ready_to_break"):
        parts.append(f"breakout@{breakout.get('level_price', 0):.2f}")
    parts.append(f"liq={liquidity.get('verdict', 'unknown')}")
    return " | ".join(parts)


# ============================================================
# Tape Reading Proxies (bar velocity, volume acceleration)
# ============================================================

def compute_bar_velocity(
    closes: list[float] | Any, bar_idx: int, lookback: int = 5,
) -> dict[str, Any]:
    """Bar velocity: rate of price change per bar vs recent average.

    Measures the speed of recent price movement relative to the trailing
    average speed. A velocity > threshold indicates a rapid move (tape speeding up).

    Returns {"velocity": float, "avg_velocity": float, "ratio": float,
             "direction": "up"/"down", "surging": bool}
    """
    if bar_idx < lookback or lookback < 2:
        return {"velocity": 0.0, "avg_velocity": 0.0, "ratio": 1.0,
                "direction": "up", "surging": False}

    # Convert to list if needed
    if hasattr(closes, "__getitem__") and not isinstance(closes, list):
        close_vals = [float(closes[i]) for i in range(max(0, bar_idx - lookback - 1), bar_idx + 1)]
    else:
        close_vals = [float(c) for c in closes[max(0, bar_idx - lookback - 1):bar_idx + 1]]

    if len(close_vals) < lookback + 1:
        return {"velocity": 0.0, "avg_velocity": 0.0, "ratio": 1.0,
                "direction": "up", "surging": False}

    # Recent velocity: last bar change
    recent_vel = close_vals[-1] - close_vals[-2]
    # Average velocity over lookback
    velocities = [close_vals[i] - close_vals[i - 1] for i in range(1, len(close_vals))]
    avg_vel = sum(velocities) / len(velocities) if velocities else 0.0

    # Normalize by price to get percentage
    price = close_vals[-1] if close_vals[-1] != 0 else 1.0
    recent_vel_pct = abs(recent_vel) / price * 100
    avg_vel_pct = abs(avg_vel) / price * 100

    ratio = recent_vel_pct / avg_vel_pct if avg_vel_pct > 0.001 else 1.0
    direction = "up" if recent_vel > 0 else "down"

    return {
        "velocity": round(recent_vel_pct, 4),
        "avg_velocity": round(avg_vel_pct, 4),
        "ratio": round(ratio, 2),
        "direction": direction,
        "surging": ratio >= 1.5,
    }


def compute_volume_acceleration(
    volumes: list[float] | Any, bar_idx: int, lookback: int = 10,
) -> dict[str, Any]:
    """Volume acceleration: current bar volume vs trailing average, accelerating or decaying.

    Returns {"current_vol": float, "avg_vol": float, "ratio": float,
             "accelerating": bool, "decaying": bool}
    """
    if bar_idx < 1 or lookback < 2:
        return {"current_vol": 0.0, "avg_vol": 0.0, "ratio": 1.0,
                "accelerating": False, "decaying": False}

    if hasattr(volumes, "__getitem__") and not isinstance(volumes, list):
        vol_vals = [float(volumes[i]) for i in range(max(0, bar_idx - lookback), bar_idx + 1)]
    else:
        vol_vals = [float(v) for v in volumes[max(0, bar_idx - lookback):bar_idx + 1]]

    if len(vol_vals) < 3:
        return {"current_vol": 0.0, "avg_vol": 0.0, "ratio": 1.0,
                "accelerating": False, "decaying": False}

    current_vol = vol_vals[-1]
    avg_vol = sum(vol_vals[:-1]) / len(vol_vals[:-1]) if len(vol_vals) > 1 else current_vol

    if avg_vol <= 0:
        return {"current_vol": current_vol, "avg_vol": 0.0, "ratio": 1.0,
                "accelerating": False, "decaying": False}

    ratio = current_vol / avg_vol

    return {
        "current_vol": round(current_vol, 2),
        "avg_vol": round(avg_vol, 2),
        "ratio": round(ratio, 2),
        "accelerating": ratio >= 1.8,
        "decaying": ratio <= 0.5,
    }


def compute_tape_signals(
    closes: list[float] | Any, volumes: list[float] | Any,
    bar_idx: int, params: dict,
) -> dict[str, Any]:
    """Compute combined tape reading signals (velocity + volume acceleration).

    Returns {"velocity": dict, "vol_accel": dict, "tape_score": float,
             "tape_bullish": bool, "tape_bearish": bool}
    """
    tape_cfg = params.get("indicators", {}).get("tape_reading", {})
    if not tape_cfg.get("enabled", False):
        return {"velocity": {}, "vol_accel": {}, "tape_score": 0.0,
                "tape_bullish": False, "tape_bearish": False}

    vel_lookback = int(tape_cfg.get("velocity_lookback", 5))
    vol_lookback = int(tape_cfg.get("vol_accel_lookback", 10))
    vel_thresh = float(tape_cfg.get("velocity_threshold", 1.5))
    vol_thresh = float(tape_cfg.get("vol_accel_threshold", 1.8))

    velocity = compute_bar_velocity(closes, bar_idx, vel_lookback)
    vol_accel = compute_volume_acceleration(volumes, bar_idx, vol_lookback)

    # Tape score: 0-1, combines velocity ratio and volume acceleration
    vel_component = min(1.0, velocity.get("ratio", 1.0) / vel_thresh) if vel_thresh > 0 else 0
    vol_component = min(1.0, vol_accel.get("ratio", 1.0) / vol_thresh) if vol_thresh > 0 else 0
    tape_score = (vel_component * 0.5 + vol_component * 0.5)

    # Directional bias from velocity
    tape_bullish = velocity.get("surging", False) and velocity.get("direction") == "up" and vol_accel.get("accelerating", False)
    tape_bearish = velocity.get("surging", False) and velocity.get("direction") == "down" and vol_accel.get("accelerating", False)

    return {
        "velocity": velocity,
        "vol_accel": vol_accel,
        "tape_score": round(tape_score, 2),
        "tape_bullish": tape_bullish,
        "tape_bearish": tape_bearish,
    }


# ============================================================
# Adaptive Exit (phase-based stops)
# ============================================================

def compute_adaptive_exit(
    pos: dict, params: dict, minutes_held: int, ind_data: dict,
) -> dict[str, Any]:
    """Phase-based adaptive exit logic.

    Phase 1 (0–phase1_minutes): Wide stop, no trailing — let the trade breathe.
    Phase 2 (phase1_minutes–phase2_minutes): Tighten stop, activate trailing.
    Phase 3 (phase2_minutes+): Very tight stop, exit on any stagnation.

    Returns {"verdict": "HOLD"/"EXIT", "exit_reason": str,
             "phase": 1|2|3, "adjusted_sl": float|None}
    """
    exit_cfg = params.get("exit_rules", {})
    phase1_min = int(exit_cfg.get("phase1_minutes", 15))
    phase2_min = int(exit_cfg.get("phase2_minutes", 45))
    pnl_pct = pos.get("pnl_pct", 0)

    if minutes_held < phase1_min:
        phase = 1
        # Phase 1: wide stop, no early exit
        return {"verdict": "HOLD", "exit_reason": "", "phase": phase, "adjusted_sl": None}

    if minutes_held < phase2_min:
        phase = 2
        # Phase 2: check stagnation with tighter threshold
        stagnation_thresh = float(exit_cfg.get("stagnation_threshold_pct", 0.1))
        if minutes_held >= phase1_min + 5 and abs(pnl_pct) < stagnation_thresh:
            return {"verdict": "EXIT", "exit_reason": f"phase2_stagnation_{minutes_held}min",
                     "phase": phase, "adjusted_sl": None}
        return {"verdict": "HOLD", "exit_reason": "", "phase": phase, "adjusted_sl": None}

    # Phase 3: very tight — exit on stagnation or minimal negative
    phase = 3
    if exit_cfg.get("phase3_stagnation_exit", True):
        if abs(pnl_pct) < float(exit_cfg.get("stagnation_threshold_pct", 0.1)):
            return {"verdict": "EXIT", "exit_reason": f"phase3_stagnation_{minutes_held}min",
                     "phase": phase, "adjusted_sl": None}
        # Exit if barely positive and momentum dying
        vol_ratio = ind_data.get("vol_ratio", 1.0)
        if vol_ratio < float(exit_cfg.get("momentum_death_vol_ratio", 0.5)) and pnl_pct < 0.5:
            return {"verdict": "EXIT", "exit_reason": f"phase3_momentum_death_{minutes_held}min",
                     "phase": phase, "adjusted_sl": None}

    return {"verdict": "HOLD", "exit_reason": "", "phase": phase, "adjusted_sl": None}


# ============================================================
# Position Review (active mode only)
# ============================================================

def review_scalp_position(
    pos: dict, params: dict, minutes_held: int, ind_data: dict,
) -> dict[str, Any]:
    """Exit review for active mode. Minute-based instead of cycle-based.

    In set-and-forget mode (default), this is a no-op — the server
    auto-close loop handles SL/TP.

    Returns {"verdict": "HOLD"/"EXIT", "exit_reason": str}
    """
    exit_cfg = params.get("exit_rules", {})
    mode = exit_cfg.get("exit_mode", "set_and_forget")

    if mode == "set_and_forget" and not exit_cfg.get("adaptive_exit", False):
        return {"verdict": "HOLD", "exit_reason": ""}

    # Adaptive exit: check phase-based stagnation
    if exit_cfg.get("adaptive_exit", False):
        phase_result = compute_adaptive_exit(pos, params, minutes_held, ind_data)
        if phase_result.get("verdict") == "EXIT":
            return phase_result

    if mode == "set_and_forget":
        return {"verdict": "HOLD", "exit_reason": ""}

    # Active mode: check stagnation and momentum death
    stagnation_min = exit_cfg.get("stagnation_minutes", 10)
    stagnation_thresh = exit_cfg.get("stagnation_threshold_pct", 0.1)
    mom_death_vol = exit_cfg.get("momentum_death_vol_ratio", 0.5)
    mom_death_grace = exit_cfg.get("momentum_death_grace_bars", 5)
    ob_rsi = exit_cfg.get("ob_exhaustion_rsi", 78)

    pnl_pct = pos.get("pnl_pct", 0)
    vol_ratio = ind_data.get("vol_ratio", 1.0)
    rsi = ind_data.get("rsi", 50)

    # Stagnation: held too long with minimal movement
    if minutes_held >= stagnation_min and abs(pnl_pct) < stagnation_thresh:
        return {"verdict": "EXIT", "exit_reason": f"stagnation_{minutes_held}min"}

    # Momentum death: volume dried up after grace period
    if minutes_held > mom_death_grace and vol_ratio < mom_death_vol:
        return {"verdict": "EXIT", "exit_reason": f"momentum_death_vol={vol_ratio:.2f}"}

    # Overbought exhaustion (longs) / oversold (shorts)
    side = pos.get("side", "long")
    if side == "long" and rsi >= ob_rsi and pnl_pct > 0:
        return {"verdict": "EXIT", "exit_reason": f"ob_exhaustion_rsi={rsi:.0f}"}
    if side == "short" and rsi <= (100 - ob_rsi) and pnl_pct > 0:
        return {"verdict": "EXIT", "exit_reason": f"os_exhaustion_rsi={rsi:.0f}"}

    return {"verdict": "HOLD", "exit_reason": ""}


# ============================================================
# Deep Merge (for config override)
# ============================================================

def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (same as scan_core.deep_merge)."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result
