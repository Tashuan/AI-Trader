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
    "exit_rules": {
        "stop_loss_pct": -1.0,
        "take_profit_pct": 1.5,
        "trailing_sl_pct": 0.5,
        "trailing_activation_pct": 0.8,
        "stagnation_minutes": 10,
        "stagnation_threshold_pct": 0.1,
        "momentum_death_vol_ratio": 0.5,
        "momentum_death_grace_bars": 5,
        "ob_exhaustion_rsi": 78,
        "exit_mode": "set_and_forget",
    },
    "entry_criteria": {
        "min_signals": 3,
        "min_signal_families": 2,
        "min_vol_ratio": 1.5,
        "max_spread_pct": 0.15,
        "min_dollar_volume": 1_000_000,
        "min_depth_dollars": 50_000,
        "require_trend_agreement": True,
        "block_on_obv_divergence": True,
    },
    "position_sizing": {
        "max_positions": 3,
        "max_pending_orders": 5,
        "normal_sizing_min_pct": 5,
        "normal_sizing_max_pct": 10,
        "risk_per_trade_pct": 0.25,
        "consecutive_loss_threshold": 3,
        "consecutive_loss_size_cut_pct": 50,
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
    },
    "order": {
        "stop_limit_offset_pct": 0.02,
        "entry_trigger_offset_pct": 0.08,
        "order_expiry_minutes": 30,
        "sl_atr_multiple": 1.0,
        "tp_atr_multiple": 1.5,
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
    },
    "cycle_timing": {
        "poll_interval_default": 15,
        "poll_interval_min": 5,
        "poll_interval_max": 60,
    },
    "watchlist": [],
}


# ============================================================
# Fibonacci Levels
# ============================================================

def detect_swing_highs_lows(df: pd.DataFrame, lookback: int = 50) -> dict[str, list]:
    """Find recent swing highs and lows using fractal detection.

    A swing high is a bar whose high is higher than the highs of the
    bars on either side (left/right window). Same logic inverted for lows.

    Returns {"swing_highs": [(bar_idx, price)], "swing_lows": [(bar_idx, price)]}
    """
    if df is None or df.empty or len(df) < 5:
        return {"swing_highs": [], "swing_lows": []}

    window = 2  # bars on each side
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
                            direction: str = "long") -> dict[str, float]:
    """Compute Fibonacci retracement levels.

    For longs: levels are below the swing high (potential entry on pullback).
    For shorts: levels are above the swing low.

    Returns {"0.382": price, "0.5": price, "0.618": price, "0.786": price}
    """
    diff = swing_high - swing_low
    if diff <= 0:
        return {}

    levels = {}
    fib_ratios = [0.382, 0.5, 0.618, 0.786]
    for ratio in fib_ratios:
        if direction == "long":
            levels[str(ratio)] = swing_high - diff * ratio
        else:
            levels[str(ratio)] = swing_low + diff * ratio
    return levels


def compute_fib_extension(swing_high: float, swing_low: float,
                          direction: str = "long") -> dict[str, float]:
    """Compute Fibonacci extension levels for TP targets.

    For longs: levels above the swing high (profit targets).
    For shorts: levels below the swing low.

    Returns {"1.272": price, "1.618": price}
    """
    diff = swing_high - swing_low
    if diff <= 0:
        return {}

    levels = {}
    fib_ratios = [1.272, 1.618]
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
                              tolerance_pct: float = 0.15) -> dict[str, Any]:
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
            "strength": len(cluster) / 10.0,  # Normalized 0-1ish
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

    if df_5m is None or df_5m.empty:
        return {"ready_to_break": False, "level_price": 0, "distance_pct": 999}

    current_price = float(df_5m["Close"].iloc[-1])
    nearest = nearest_sr_level(current_price, sr_levels)
    if not nearest:
        return {"ready_to_break": False, "level_price": 0, "distance_pct": 999}

    level_price = nearest["price"]
    level_type = nearest["type"]
    distance_pct = nearest["distance_pct"]

    # Check if price has been approaching (within 0.5% of the level)
    approaching = distance_pct < 0.5

    # Check if price has been consolidating near the level
    # (last `confirm_bars` bars all within 1% of the level)
    recent = df_5m.iloc[-confirm_bars:]
    near_level = all(
        abs(float(bar["Close"]) - level_price) / level_price < 0.01
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


def detect_pattern(df_5m: pd.DataFrame) -> dict[str, Any]:
    """Detect simple chart patterns on 5m chart.

    Detects:
    - Range breakout (consolidation → expansion)
    - Flag/pennant (tight pullback after strong move)
    - Wedge (converging trendlines)

    Returns {"pattern_type", "direction", "confidence"}
    """
    if df_5m is None or df_5m.empty or len(df_5m) < 20:
        return {"pattern_type": "none", "direction": "neutral", "confidence": 0.0}

    # Range breakout: was consolidating, now breaking
    cons_bo = detect_consolidation_breakout(df_5m, lookback=3)
    if cons_bo:
        last = df_5m.iloc[-1]
        direction = "long" if last["Close"] > last["Open"] else "short"
        return {"pattern_type": "range_breakout", "direction": direction, "confidence": 0.7}

    # Flag/pennant: strong move followed by tight consolidation
    if len(df_5m) >= 15:
        # Strong move in first 5 bars
        first_5 = df_5m.iloc[-15:-10]
        last_10 = df_5m.iloc[-10:]
        move_pct = (first_5["Close"].iloc[-1] / first_5["Close"].iloc[0] - 1) * 100
        # Consolidation range in last 10 bars
        cons_range = (last_10["High"].max() - last_10["Low"].min()) / last_10["Close"].mean() * 100

        if abs(move_pct) > 1.5 and cons_range < 1.0:
            direction = "long" if move_pct > 0 else "short"
            return {"pattern_type": "flag", "direction": direction, "confidence": 0.6}

    # Wedge: converging highs and lows
    if len(df_5m) >= 20:
        recent = df_5m.iloc[-20:]
        highs = recent["High"].values
        lows = recent["Low"].values

        # Simple linear regression slope
        x = np.arange(len(highs))
        high_slope = np.polyfit(x, highs, 1)[0] if len(highs) > 1 else 0
        low_slope = np.polyfit(x, lows, 1)[0] if len(lows) > 1 else 0

        # Converging: highs descending, lows ascending (or vice versa)
        if high_slope < 0 and low_slope > 0:
            return {"pattern_type": "wedge_bearish", "direction": "short", "confidence": 0.5}
        elif high_slope > 0 and low_slope > 0 and high_slope > low_slope:
            return {"pattern_type": "wedge_bullish", "direction": "long", "confidence": 0.5}

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
    max_spread = entry_cfg.get("max_spread_pct", 0.15)
    min_depth = entry_cfg.get("min_depth_dollars", 50_000)
    min_dollar_vol = entry_cfg.get("min_dollar_volume", 1_000_000)

    spread_pct = quote.get("spread_pct", 999) if quote else 999
    total_volume = quote.get("total_volume", 0) if quote else 0

    # Dollar volume: use recent bars if available
    if df is not None and not df.empty:
        avg_price = float(df["Close"].tail(20).mean())
        avg_vol = float(df["Volume"].tail(20).mean())
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
    score = (spread_score * 0.4 + depth_score * 0.3 + vol_score * 0.3)

    if score >= 0.6:
        verdict = "good"
    elif score >= 0.3:
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
    for label, df in [("1m", df_1m), ("5m", df_5m), ("15m", df_15m)]:
        if df is None or df.empty or len(df) < 30:
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

    if rsi > 55:
        bullish_signals += 1
    elif rsi < 45:
        bearish_signals += 1

    if bullish_signals > bearish_signals:
        direction = "bullish"
        strength = bullish_signals / 4.0
    elif bearish_signals > bullish_signals:
        direction = "bearish"
        strength = bearish_signals / 4.0
    else:
        direction = "neutral"
        strength = 0.0

    return {"direction": direction, "strength": round(strength, 2)}


def deep_scan_multi_tf(
    symbol: str, pre: dict, bar_idx_1m: int, params: dict,
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
    bar_5m = min(bar_idx_1m, pre_5m["n"] - 1) if pre_5m else -1
    trend_5m = _trend_direction(pre_5m, bar_5m, params)

    # 15m trend
    pre_15m = pre.get("15m")
    bar_15m = min(bar_idx_1m, pre_15m["n"] - 1) if pre_15m else -1
    trend_15m = _trend_direction(pre_15m, bar_15m, params)

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

    trend_agrees = confluence >= 2 or not require_agreement

    # ATR from 1m for SL/TP computation
    atr = _safe_float(pre_1m["atr"][bar_idx_1m])
    if atr <= 0:
        atr = _safe_float(pre_1m["close"][bar_idx_1m]) * 0.002  # 0.2% fallback

    # Override qualification with trend agreement
    base_qualifies = scan_1m.get("qualifies_for_entry", False)
    qualifies = base_qualifies and trend_agrees

    return {
        **scan_1m,
        "trend_5m": trend_5m,
        "trend_15m": trend_15m,
        "confluence_score": confluence,
        "trend_agrees": trend_agrees,
        "atr": atr,
        "qualifies_for_entry": qualifies,
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
    # Multi-TF confluence (0-1)
    confluence = mtf_result.get("confluence_score", 0) / 2.0

    # Level alignment (0-1): is price near a Fib or S/R breakout level?
    level_alignment = 0.0
    if breakout.get("ready_to_break"):
        level_alignment = 1.0
    elif breakout.get("approaching"):
        level_alignment = 0.5
    elif fib_levels:
        price = mtf_result.get("price", 0)
        nearest = nearest_fib_level(price, fib_levels)
        if nearest and nearest["distance_pct"] < 0.5:
            level_alignment = 0.7
        elif nearest and nearest["distance_pct"] < 1.0:
            level_alignment = 0.3

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

    # Composite
    score = (
        confluence * 0.30
        + level_alignment * 0.25
        + pattern_conf * 0.20
        + liq_score * 0.15
        + vol_momentum * 0.10
    ) * 10.0

    # Determine direction
    direction = mtf_result.get("entry_direction", pattern.get("direction", "long"))
    if direction == "neutral":
        direction = "long"

    # Compute entry/SL/TP from levels + ATR
    price = mtf_result.get("price", 0)
    atr = mtf_result.get("atr", price * 0.002 if price > 0 else 0)
    order_cfg = params.get("order", {})
    sl_mult = order_cfg.get("sl_atr_multiple", 1.0)
    tp_mult = order_cfg.get("tp_atr_multiple", 1.5)

    reference_level = breakout.get("level_price", price) if breakout.get("ready_to_break") else price
    trigger_offset_pct = max(0.0, float(order_cfg.get("entry_trigger_offset_pct", 0.08)))
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
    min_score = 4.0  # Out of 10
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
