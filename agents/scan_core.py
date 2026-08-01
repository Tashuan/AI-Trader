"""
scan_core.py — Shared, side-effect-free BlitzTrader scan logic.

Contains the canonical strategy parameters, all 15 indicator computations,
composite scoring, entry qualification, and the 6-rule position exit review.

This module has NO network I/O (no yfinance, no HTTP calls) — every function
takes data (a DataFrame or a pre-computed indicator dict) as input. This lets
the exact same logic be replayed against live data (agents/workspaces/*/scan.py)
or historical data (agents/scan_backtester.py) with zero drift between the two.
"""

from typing import Any, Optional

import numpy as np
import pandas as pd


# ============================================================
# Canonical Default Strategy Parameters
#
# This is the single source of truth for BlitzTrader's Goal Runner
# strategy defaults. Both the live agent (via GET /api/claw/agents/me/
# strategy-params, merged with agent_configs.config_json.strategy_params)
# and the backtester read this same dict, so there is exactly one place
# to update thresholds instead of two hand-copied dicts.
# ============================================================

DEFAULT_PARAMS: dict[str, Any] = {
    "exit_rules": {
        "stop_loss_pct": -2.0,
        "take_profit_pct": 2.0,
        "stagnation_cycles": 6,
        "stagnation_threshold_pct": 0.3,
        "momentum_death_vol_ratio": 0.7,
        "momentum_death_grace_bars": 3,
        "ob_exhaustion_rsi": 75,
        "trailing_sl_pct": 1.5,
        "trailing_activation_pct": 2.5,
    },
    "entry_criteria": {
        "min_signals": 4,
        "min_signal_families": 2,
        "min_vol_ratio": 1.5,
        "bearish_macro_min_signals": 5,
        "bearish_macro_threshold": 0.3,
    },
    "position_sizing": {
        "max_positions": 1,
        "normal_sizing_min_pct": 25,
        "normal_sizing_max_pct": 40,
        "approaching_sizing_min_pct": 15,
        "approaching_sizing_max_pct": 25,
        "final_stretch_tp_pct": 1.5,
        "max_position_dollar_cap": None,
        "slippage_buffer_pct": 0.1,
        "daily_loss_size_cut_pct": 50,
        "consecutive_loss_threshold": 3,
        "consecutive_loss_size_cut_pct": 50,
        "consecutive_loss_min_signals": 5,
        "daily_pnl_reset_timezone": "UTC",
    },
    "switch_logic": {
        "switch_score_threshold_pct": 20,
        "switch_require_profitable": True,
        "reentry_cooldown_cycles": 3,
    },
    "scoring_weights": {
        "signal_count_weight": 0.35,
        "family_diversity_weight": 0.25,
        "candle_quality_weight": 0.20,
        "consolidation_bonus_weight": 0.20,
    },
    "indicators": {
        "candle_interval": "1h",
        "lookback_period": "1mo",
        "rsi_period": 14,
        "rsi_bullish": 55,
        "rsi_overbought": 75,
        "rsi_oversold": 30,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "sma_periods": [20, 50, 200],
        "ema_period": 20,
        "stochastic_period": 14,
        "atr_period": 14,
        "bb_squeeze_ratio": 0.6,
        "candle_body_conviction": 0.6,
        "candle_body_doji": 0.3,
        "vol_ratio_bullish": 1.5,
        "vol_ratio_dead": 0.5,
    },
    "watchlist": ["BTC", "ETH", "SOL", "AVAX", "NVDA", "TSLA", "META", "AMZN"],
    "sweep": {
        "enabled": True,
        "sweep_min_vol_ratio": 1.5,
        "sweep_min_price_change_pct": 1.0,
        "sweep_max_qualifiers": 10,
    },
    "cycle_timing": {
        "poll_interval_default": 120,
        "poll_interval_min": 10,
        "poll_interval_max": 3600,
    },
}

# Symbols that need -USD suffix for yfinance
CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "XRP", "LINK",
    "MATIC", "DOT", "LTC", "BCH", "UNI", "ATOM", "NEAR", "APT",
    "ARB", "OP", "INJ", "SUI", "SEI", "TIA", "PEPE", "SHIB",
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def yf_ticker(symbol: str) -> str:
    """Convert symbol to yfinance ticker format."""
    if symbol in CRYPTO_SYMBOLS:
        return f"{symbol}-USD"
    return symbol


# ============================================================
# Indicator Computations (pure functions over a DataFrame)
# ============================================================

def compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    return float(rsi) if not np.isnan(rsi) else 50.0


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float]:
    macd_line = df['Close'].ewm(span=fast).mean() - df['Close'].ewm(span=slow).mean()
    signal_line = macd_line.ewm(span=signal).mean()
    hist = (macd_line - signal_line).iloc[-1]
    macd_val = macd_line.iloc[-1]
    return float(hist) if not np.isnan(hist) else 0.0, float(macd_val) if not np.isnan(macd_val) else 0.0


def compute_sma(df: pd.DataFrame, period: int) -> float:
    sma = df['Close'].rolling(period).mean().iloc[-1]
    return float(sma) if not np.isnan(sma) else 0.0


def compute_ema(df: pd.DataFrame, period: int) -> float:
    ema = df['Close'].ewm(span=period).mean().iloc[-1]
    return float(ema) if not np.isnan(ema) else 0.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0


def compute_bollinger(df: pd.DataFrame, period: int = 20) -> tuple[float, float, float, float]:
    """Returns (upper, lower, width, squeeze_ratio)."""
    sma = df['Close'].rolling(period).mean()
    std = df['Close'].rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    width = ((upper - lower) / sma).iloc[-1]
    avg_width = ((upper - lower) / sma).rolling(50).mean().iloc[-1]
    squeeze_ratio = float(width / avg_width) if avg_width and not np.isnan(avg_width) and avg_width > 0 else 1.0
    return float(upper.iloc[-1]), float(lower.iloc[-1]), float(width) if not np.isnan(width) else 0.0, squeeze_ratio


def compute_stochastic(df: pd.DataFrame, period: int = 14) -> tuple[float, float]:
    low_min = df['Low'].rolling(period).min()
    high_max = df['High'].rolling(period).max()
    k = ((df['Close'] - low_min) / (high_max - low_min)) * 100
    d = k.rolling(3).mean()
    k_val = k.iloc[-1]
    d_val = d.iloc[-1]
    return float(k_val) if not np.isnan(k_val) else 50.0, float(d_val) if not np.isnan(d_val) else 50.0


def compute_obv(df: pd.DataFrame) -> pd.Series:
    obv = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    return obv


def compute_vwap(df: pd.DataFrame) -> float:
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    return float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else float(df['Close'].iloc[-1])


def detect_obv_divergence(df: pd.DataFrame, obv: pd.Series, lookback: int = 10) -> bool:
    """Price up but OBV flat/down = fake breakout warning."""
    if len(df) < lookback + 1:
        return False
    price_trend = df['Close'].iloc[-1] > df['Close'].iloc[-lookback]
    obv_trend = obv.iloc[-1] < obv.iloc[-lookback]
    return bool(price_trend and obv_trend)


def detect_consolidation_breakout(df: pd.DataFrame, lookback: int = 3) -> bool:
    """Was ranging (tight spread) for last `lookback` candles, now breaking out."""
    if len(df) < lookback + 2:
        return False
    recent = df.iloc[-lookback - 1:-1]
    current = df.iloc[-1]
    recent_spread = (recent['High'] - recent['Low']).mean()
    current_spread = current['High'] - current['Low']
    recent_range = recent['High'].max() - recent['Low'].min()
    was_tight = recent_range / recent['Close'].mean() < 0.03
    breaking = current_spread > recent_spread * 1.5
    return bool(was_tight and breaking)


def candle_body_ratio(df: pd.DataFrame) -> float:
    """abs(close-open) / (high-low) for last candle."""
    last = df.iloc[-1]
    body = abs(last['Close'] - last['Open'])
    range_ = last['High'] - last['Low']
    return float(body / range_) if range_ and range_ > 0 else 0.0


def candle_quality(ratio: float, conviction: float = 0.6, doji: float = 0.3) -> str:
    if ratio >= conviction:
        return "full_body"
    elif ratio <= doji:
        return "doji"
    else:
        return "wicked"


def sma_alignment(sma20: float, sma50: float, sma200: float) -> str:
    if sma20 > sma50 > sma200:
        return "20>50>200"
    elif sma20 < sma50 < sma200:
        return "20<50<200"
    elif sma20 > sma50:
        return "20>50"
    elif sma20 < sma50:
        return "20<50"
    return "mixed"


def bb_state(width: float, squeeze_ratio: float, squeeze_threshold: float = 0.6) -> str:
    if squeeze_ratio < squeeze_threshold:
        return "squeezing"
    elif width > 0.05:
        return "expanding"
    else:
        return "normal"


def market_state(vol_ratio: float, bb_state_str: str, quality: str) -> str:
    if vol_ratio > 1.5 and bb_state_str in ("expanding", "squeezing") and quality == "full_body":
        return "imbalance_bullish" if vol_ratio > 1.5 else "imbalance_bearish"
    elif vol_ratio < 0.5:
        return "dead"
    else:
        return "balanced"


# ============================================================
# Precomputed Indicators (for backtest performance — O(n) not O(n²))
# ============================================================

def precompute_indicators(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Compute all indicator series across the full DataFrame once.

    Returns a dict of pd.Series (or numpy arrays) that can be indexed at any
    bar position to get the indicator value "as of" that bar — no lookahead,
    no recomputation. This is the performance-critical path for the backtester.

    The series are aligned to df's index: series.iloc[i] gives the value
    using only data up to and including bar i.
    """
    ind_cfg = params.get("indicators", {})
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']
    n = len(df)

    # ── Volume ratio (current vol / 20-bar avg vol) ───────────────
    # Handle zero-volume bars (yfinance crypto reports 0 volume intermittently):
    # forward-fill vol_ratio when current volume is 0 so momentum_death doesn't
    # fire on missing data.
    avg_vol_20 = volume.rolling(20).mean()
    raw_vol_ratio = (volume / avg_vol_20)
    # Replace 0-volume bars with NaN, then forward-fill
    raw_vol_ratio = raw_vol_ratio.where(volume > 0, np.nan)
    vol_ratio = raw_vol_ratio.ffill().fillna(0.0)

    # ── ATR ───────────────────────────────────────────────────────
    atr_period = ind_cfg.get("atr_period", 14)
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_period).mean()

    # ── Bollinger Bands ───────────────────────────────────────────
    bb_period = 20
    bb_sma = close.rolling(bb_period).mean()
    bb_std = close.rolling(bb_period).std()
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_sma
    bb_avg_width = bb_width.rolling(50).mean()
    bb_squeeze = (bb_width / bb_avg_width).fillna(1.0)
    bb_squeeze = bb_squeeze.where(bb_avg_width > 0, 1.0)

    # ── SMA ───────────────────────────────────────────────────────
    sma_periods = ind_cfg.get("sma_periods", [20, 50, 200])
    sma20_s = close.rolling(sma_periods[0] if len(sma_periods) > 0 else 20).mean()
    sma50_s = close.rolling(sma_periods[1] if len(sma_periods) > 1 else 50).mean()
    sma200_s = close.rolling(sma_periods[2] if len(sma_periods) > 2 else 200).mean()

    # ── EMA ───────────────────────────────────────────────────────
    ema_period = ind_cfg.get("ema_period", 20)
    ema20_s = close.ewm(span=ema_period).mean()

    # ── MACD ──────────────────────────────────────────────────────
    macd_fast = ind_cfg.get("macd_fast", 12)
    macd_slow = ind_cfg.get("macd_slow", 26)
    macd_signal = ind_cfg.get("macd_signal", 9)
    macd_line_s = close.ewm(span=macd_fast).mean() - close.ewm(span=macd_slow).mean()
    macd_signal_s = macd_line_s.ewm(span=macd_signal).mean()
    macd_hist_s = macd_line_s - macd_signal_s

    # ── RSI ───────────────────────────────────────────────────────
    rsi_period = ind_cfg.get("rsi_period", 14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi_s = (100 - (100 / (1 + rs))).fillna(50.0)

    # ── Stochastic ────────────────────────────────────────────────
    stoch_period = ind_cfg.get("stochastic_period", 14)
    stoch_low = low.rolling(stoch_period).min()
    stoch_high = high.rolling(stoch_period).max()
    stoch_k_s = ((close - stoch_low) / (stoch_high - stoch_low) * 100).fillna(50.0)
    stoch_d_s = stoch_k_s.rolling(3).mean().fillna(50.0)

    # ── OBV ───────────────────────────────────────────────────────
    obv_s = (np.sign(close.diff()) * volume).fillna(0).cumsum()

    # ── VWAP (cumulative) ─────────────────────────────────────────
    typical_price = (high + low + close) / 3
    cum_tp_vol = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    vwap_s = (cum_tp_vol / cum_vol).fillna(close)

    # ── Candle body ratio ─────────────────────────────────────────
    body = (close - df['Open']).abs()
    range_ = (high - low).replace(0, np.nan)
    body_ratio_s = (body / range_).fillna(0.0)

    # ── 1h return ─────────────────────────────────────────────────
    ret_1h_s = (close.pct_change() * 100).fillna(0.0)

    # ── OBV divergence (price up, OBV down over 10 bars) ──────────
    obv_div_s = pd.Series(False, index=df.index)
    lookback = 10
    for i in range(lookback + 1, n):
        price_up = close.iloc[i] > close.iloc[i - lookback]
        obv_down = obv_s.iloc[i] < obv_s.iloc[i - lookback]
        obv_div_s.iloc[i] = bool(price_up and obv_down)

    # ── Consolidation breakout (3-bar lookback) ───────────────────
    cons_bo_s = pd.Series(False, index=df.index)
    cbo_lookback = 3
    for i in range(cbo_lookback + 2, n):
        recent = df.iloc[i - cbo_lookback - 1:i]
        current = df.iloc[i]
        recent_spread = (recent['High'] - recent['Low']).mean()
        current_spread = current['High'] - current['Low']
        recent_range = recent['High'].max() - recent['Low'].min()
        was_tight = recent_range / recent['Close'].mean() < 0.03
        breaking = current_spread > recent_spread * 1.5
        cons_bo_s.iloc[i] = bool(was_tight and breaking)

    return {
        "close": close.values,
        "high": high.values,
        "low": low.values,
        "volume": volume.values,
        "vol_ratio": vol_ratio.values,
        "atr": atr_series.values,
        "bb_upper": bb_upper.values,
        "bb_lower": bb_lower.values,
        "bb_width": bb_width.values,
        "bb_squeeze": bb_squeeze.values,
        "sma20": sma20_s.values,
        "sma50": sma50_s.values,
        "sma200": sma200_s.values,
        "ema20": ema20_s.values,
        "macd_hist": macd_hist_s.values,
        "macd_line": macd_line_s.values,
        "rsi": rsi_s.values,
        "stoch_k": stoch_k_s.values,
        "stoch_d": stoch_d_s.values,
        "obv": obv_s.values,
        "obv_div": obv_div_s.values,
        "vwap": vwap_s.values,
        "body_ratio": body_ratio_s.values,
        "ret_1h": ret_1h_s.values,
        "consolidation_bo": cons_bo_s.values,
        "n": n,
    }


def _safe_float(val, default=0.0):
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return default
    return float(val)


def deep_scan_from_precomputed(symbol: str, pre: dict[str, Any], bar_idx: int, params: dict[str, Any]) -> dict[str, Any]:
    """Produce the same output as deep_scan_symbol_from_df but from precomputed series.

    Reads indicator values at bar_idx from the precomputed dict — O(1) per bar.
    """
    if bar_idx < 30 or bar_idx >= pre["n"]:
        return {"error": "no_data", "error_detail": f"bar_idx {bar_idx} out of range", "qualifies_for_entry": False}

    ind_cfg = params.get("indicators", {})
    price = _safe_float(pre["close"][bar_idx])

    vol_ratio = _safe_float(pre["vol_ratio"][bar_idx])
    atr = _safe_float(pre["atr"][bar_idx])
    bb_width = _safe_float(pre["bb_width"][bar_idx])
    bb_squeeze = _safe_float(pre["bb_squeeze"][bar_idx])
    bb_state_str = bb_state(bb_width, bb_squeeze, ind_cfg.get("bb_squeeze_ratio", 0.6))

    sma20 = _safe_float(pre["sma20"][bar_idx])
    sma50 = _safe_float(pre["sma50"][bar_idx])
    sma200 = _safe_float(pre["sma200"][bar_idx])
    sma_align = sma_alignment(sma20, sma50, sma200)
    ema20 = _safe_float(pre["ema20"][bar_idx])
    macd_hist = _safe_float(pre["macd_hist"][bar_idx])
    rsi = _safe_float(pre["rsi"][bar_idx], 50.0)
    stoch_k = _safe_float(pre["stoch_k"][bar_idx], 50.0)
    stoch_d = _safe_float(pre["stoch_d"][bar_idx], 50.0)
    obv_div = bool(pre["obv_div"][bar_idx])
    vwap = _safe_float(pre["vwap"][bar_idx], price)
    body_ratio = _safe_float(pre["body_ratio"][bar_idx])
    consolidation_bo = bool(pre["consolidation_bo"][bar_idx])
    ret_1h = _safe_float(pre["ret_1h"][bar_idx])

    candle_qual = candle_quality(
        body_ratio,
        ind_cfg.get("candle_body_conviction", 0.6),
        ind_cfg.get("candle_body_doji", 0.3),
    )
    mkt_state = market_state(vol_ratio, bb_state_str, candle_qual)

    rsi_bull = ind_cfg.get("rsi_bullish", 55)
    rsi_ob = ind_cfg.get("rsi_overbought", 75)
    vol_bull = ind_cfg.get("vol_ratio_bullish", 1.5)
    vol_dead = ind_cfg.get("vol_ratio_dead", 0.5)

    indicators: dict[str, Any] = {}
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    families: set[str] = set()

    def _add_indicator(name, value, signal, family):
        nonlocal bullish_count, bearish_count, neutral_count
        indicators[name] = {"value": value, "signal": signal, "family": family}
        families.add(family)
        if signal == "bullish":
            bullish_count += 1
        elif signal == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1

    vol_signal = "bullish" if vol_ratio > vol_bull else ("bearish" if vol_ratio < vol_dead else "neutral")
    _add_indicator("vol_ratio", round(vol_ratio, 2), vol_signal, "volume")
    _add_indicator("atr", round(atr, 4), "neutral", "volatility")

    bb_signal = "bullish" if bb_state_str == "expanding" else "neutral"
    _add_indicator("bb_state", bb_state_str, bb_signal, "volatility")

    sma_signal = "bullish" if "20>50" in sma_align else ("bearish" if "20<50" in sma_align else "neutral")
    _add_indicator("sma_alignment", sma_align, sma_signal, "trend")

    ema_signal = "bullish" if price > ema20 else "bearish"
    _add_indicator("ema20", round(ema20, 4), ema_signal, "trend")

    macd_signal = "bullish" if macd_hist > 0 else "bearish"
    _add_indicator("macd_hist", round(macd_hist, 4), macd_signal, "trend")

    rsi_signal = "bullish" if rsi > rsi_bull else ("bearish" if rsi < ind_cfg.get("rsi_oversold", 30) else "neutral")
    _add_indicator("rsi", round(rsi, 1), rsi_signal, "momentum")

    stoch_signal = "bullish" if stoch_k > stoch_d and stoch_k < 80 else ("bearish" if stoch_k < stoch_d and stoch_k > 20 else "neutral")
    _add_indicator("stochastic", {"k": round(stoch_k, 1), "d": round(stoch_d, 1)}, stoch_signal, "momentum")

    obv_signal = "bearish" if obv_div else "neutral"
    _add_indicator("obv_divergence", obv_div, obv_signal, "volume")

    vwap_signal = "bullish" if price > vwap else "bearish"
    _add_indicator("vwap", round(vwap, 4), vwap_signal, "timing")

    candle_signal = "bullish" if body_ratio >= ind_cfg.get("candle_body_conviction", 0.6) else ("bearish" if body_ratio <= ind_cfg.get("candle_body_doji", 0.3) else "neutral")
    _add_indicator("candle_body_ratio", round(body_ratio, 3), candle_signal, "timing")

    cons_signal = "bullish" if consolidation_bo else "neutral"
    _add_indicator("consolidation_breakout", consolidation_bo, cons_signal, "timing")

    _add_indicator("return_1h", round(ret_1h, 2), "bullish" if ret_1h > 0 else "bearish", "momentum")

    entry_cfg = params.get("entry_criteria", {})
    min_signals = entry_cfg.get("min_signals", 4)
    min_families = entry_cfg.get("min_signal_families", 2)
    min_vol = entry_cfg.get("min_vol_ratio", 1.5)

    qualifies = (
        bullish_count >= min_signals
        and len(families) >= min_families
        and vol_ratio > min_vol
        and not obv_div
    )
    direction = "long" if bullish_count > bearish_count else "short"

    weights = params.get("scoring_weights", {})
    signal_count_score = bullish_count / 13.0
    family_diversity_score = len(families) / 5.0
    candle_quality_score = min(body_ratio, 1.0)
    consolidation_bonus = 1.0 if consolidation_bo else 0.0

    composite_score = (
        signal_count_score * weights.get("signal_count_weight", 0.35)
        + family_diversity_score * weights.get("family_diversity_weight", 0.25)
        + candle_quality_score * weights.get("candle_quality_weight", 0.20)
        + consolidation_bonus * weights.get("consolidation_bonus_weight", 0.20)
    ) * 10.0

    return {
        "price": round(price, 6),
        "market_state": mkt_state,
        "indicators": indicators,
        "signal_count": {"bullish": bullish_count, "bearish": bearish_count, "neutral": neutral_count},
        "families_represented": sorted(list(families)),
        "qualifies_for_entry": qualifies,
        "entry_direction": direction,
        "candle_quality": candle_qual,
        "obv_divergence": obv_div,
        "consolidation_breakout": consolidation_bo,
        "composite_score": round(composite_score, 2),
    }




def deep_scan_symbol_from_df(symbol: str, df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Run all 15 indicators on a single symbol using a pre-fetched DataFrame.

    `df` must have OHLCV columns (Open, High, Low, Close, Volume) and be
    truncated to only the data available "as of" the point in time being
    evaluated (no lookahead) when used for backtesting. The live scan.py
    passes the full live-fetched history; the backtester passes a window
    ending at the current simulated bar.
    """
    ind_cfg = params.get("indicators", {})

    if df is None or df.empty or len(df) < 30:
        return {
            "error": "no_data",
            "error_detail": f"insufficient data ({len(df) if df is not None else 0} rows)",
            "qualifies_for_entry": False,
        }

    last = df.iloc[-1]
    price = float(last['Close'])

    # Layer 1: Market State
    prev_vol = df['Volume'].tail(20).mean()
    cur_vol = float(last['Volume'])
    if cur_vol > 0 and prev_vol and prev_vol > 0:
        vol_ratio = cur_vol / prev_vol
    elif cur_vol == 0:
        # Zero-volume bar (common in yfinance crypto): use last known non-zero vol_ratio
        recent_vol = df['Volume'].iloc[-20:]
        recent_nonzero = recent_vol[recent_vol > 0]
        if len(recent_nonzero) > 0 and prev_vol and prev_vol > 0:
            vol_ratio = float(recent_nonzero.iloc[-1] / prev_vol)
        else:
            vol_ratio = 1.0  # neutral fallback
    else:
        vol_ratio = 0.0
    atr = compute_atr(df, ind_cfg.get("atr_period", 14))
    bb_upper, bb_lower, bb_width, bb_squeeze = compute_bollinger(df, 20)
    bb_state_str = bb_state(bb_width, bb_squeeze, ind_cfg.get("bb_squeeze_ratio", 0.6))

    # Layer 2: Trend Direction
    sma_periods = ind_cfg.get("sma_periods", [20, 50, 200])
    sma20 = compute_sma(df, sma_periods[0] if len(sma_periods) > 0 else 20)
    sma50 = compute_sma(df, sma_periods[1] if len(sma_periods) > 1 else 50)
    sma200 = compute_sma(df, sma_periods[2] if len(sma_periods) > 2 else 200)
    sma_align = sma_alignment(sma20, sma50, sma200)
    ema20 = compute_ema(df, ind_cfg.get("ema_period", 20))
    macd_hist, macd_line = compute_macd(
        df,
        ind_cfg.get("macd_fast", 12),
        ind_cfg.get("macd_slow", 26),
        ind_cfg.get("macd_signal", 9),
    )

    # Layer 3: Momentum Quality
    rsi = compute_rsi(df, ind_cfg.get("rsi_period", 14))
    stoch_k, stoch_d = compute_stochastic(df, ind_cfg.get("stochastic_period", 14))
    obv = compute_obv(df)
    obv_div = detect_obv_divergence(df, obv)

    # Layer 4: Entry Timing
    vwap = compute_vwap(df)
    body_ratio = candle_body_ratio(df)
    candle_qual = candle_quality(
        body_ratio,
        ind_cfg.get("candle_body_conviction", 0.6),
        ind_cfg.get("candle_body_doji", 0.3),
    )
    consolidation_bo = detect_consolidation_breakout(df)

    # 1h (or configured interval) return
    ret_1h = float(((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100)

    mkt_state = market_state(vol_ratio, bb_state_str, candle_qual)

    rsi_bull = ind_cfg.get("rsi_bullish", 55)
    rsi_ob = ind_cfg.get("rsi_overbought", 75)
    vol_bull = ind_cfg.get("vol_ratio_bullish", 1.5)
    vol_dead = ind_cfg.get("vol_ratio_dead", 0.5)

    indicators: dict[str, Any] = {}
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    families: set[str] = set()

    def _add_indicator(name, value, signal, family):
        nonlocal bullish_count, bearish_count, neutral_count
        indicators[name] = {"value": value, "signal": signal, "family": family}
        families.add(family)
        if signal == "bullish":
            bullish_count += 1
        elif signal == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1

    vol_signal = "bullish" if vol_ratio > vol_bull else ("bearish" if vol_ratio < vol_dead else "neutral")
    _add_indicator("vol_ratio", round(vol_ratio, 2), vol_signal, "volume")

    _add_indicator("atr", round(atr, 4), "neutral", "volatility")

    bb_signal = "bullish" if bb_state_str == "expanding" else "neutral"
    _add_indicator("bb_state", bb_state_str, bb_signal, "volatility")

    sma_signal = "bullish" if "20>50" in sma_align else ("bearish" if "20<50" in sma_align else "neutral")
    _add_indicator("sma_alignment", sma_align, sma_signal, "trend")

    ema_signal = "bullish" if price > ema20 else "bearish"
    _add_indicator("ema20", round(ema20, 4), ema_signal, "trend")

    macd_signal = "bullish" if macd_hist > 0 else "bearish"
    _add_indicator("macd_hist", round(macd_hist, 4), macd_signal, "trend")

    rsi_signal = "bullish" if rsi > rsi_bull else ("bearish" if rsi < ind_cfg.get("rsi_oversold", 30) else "neutral")
    _add_indicator("rsi", round(rsi, 1), rsi_signal, "momentum")

    stoch_signal = "bullish" if stoch_k > stoch_d and stoch_k < 80 else ("bearish" if stoch_k < stoch_d and stoch_k > 20 else "neutral")
    _add_indicator("stochastic", {"k": round(stoch_k, 1), "d": round(stoch_d, 1)}, stoch_signal, "momentum")

    obv_signal = "bearish" if obv_div else "neutral"
    _add_indicator("obv_divergence", obv_div, obv_signal, "volume")

    vwap_signal = "bullish" if price > vwap else "bearish"
    _add_indicator("vwap", round(vwap, 4), vwap_signal, "timing")

    candle_signal = "bullish" if body_ratio >= ind_cfg.get("candle_body_conviction", 0.6) else ("bearish" if body_ratio <= ind_cfg.get("candle_body_doji", 0.3) else "neutral")
    _add_indicator("candle_body_ratio", round(body_ratio, 3), candle_signal, "timing")

    cons_signal = "bullish" if consolidation_bo else "neutral"
    _add_indicator("consolidation_breakout", consolidation_bo, cons_signal, "timing")

    _add_indicator("return_1h", round(ret_1h, 2), "bullish" if ret_1h > 0 else "bearish", "momentum")

    entry_cfg = params.get("entry_criteria", {})
    min_signals = entry_cfg.get("min_signals", 4)
    min_families = entry_cfg.get("min_signal_families", 2)
    min_vol = entry_cfg.get("min_vol_ratio", 1.5)

    qualifies = (
        bullish_count >= min_signals
        and len(families) >= min_families
        and vol_ratio > min_vol
        and not obv_div
    )

    direction = "long" if bullish_count > bearish_count else "short"

    weights = params.get("scoring_weights", {})
    signal_count_score = bullish_count / 13.0
    family_diversity_score = len(families) / 5.0
    candle_quality_score = min(body_ratio, 1.0)
    consolidation_bonus = 1.0 if consolidation_bo else 0.0

    composite_score = (
        signal_count_score * weights.get("signal_count_weight", 0.35)
        + family_diversity_score * weights.get("family_diversity_weight", 0.25)
        + candle_quality_score * weights.get("candle_quality_weight", 0.20)
        + consolidation_bonus * weights.get("consolidation_bonus_weight", 0.20)
    ) * 10.0

    return {
        "price": round(price, 6),
        "market_state": mkt_state,
        "indicators": indicators,
        "signal_count": {"bullish": bullish_count, "bearish": bearish_count, "neutral": neutral_count},
        "families_represented": sorted(list(families)),
        "qualifies_for_entry": qualifies,
        "entry_direction": direction,
        "candle_quality": candle_qual,
        "obv_divergence": obv_div,
        "consolidation_breakout": consolidation_bo,
        "composite_score": round(composite_score, 2),
    }


# ============================================================
# Position Review — decoupled from data-fetching
# ============================================================

def review_position_from_indicators(
    pos: dict[str, Any],
    params: dict[str, Any],
    cycles_flat: int,
    ind_data: dict[str, Any],
    bars_held: int = 0,
) -> dict[str, Any]:
    """Evaluate the 6 exit rules for a position using a pre-computed indicator dict.

    `ind_data` must be the output of `deep_scan_symbol_from_df` for this symbol
    at the current point in time (live-fetched or historical window).
    `bars_held` is the number of bars since entry — used for momentum_death grace period.
    """
    exit_cfg = params.get("exit_rules", {})

    symbol = pos.get("symbol", "")
    side = pos.get("side", "long")
    entry_price = float(pos.get("entry_price", 0))
    current_price = float(pos.get("current_price", 0)) or entry_price

    if side == "long":
        pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
    else:
        pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price else 0

    vol_ratio = 0.0
    rsi = 50.0
    vwap = current_price
    if "indicators" in ind_data:
        vol_ratio = ind_data["indicators"].get("vol_ratio", {}).get("value", 0.0)
        rsi = ind_data["indicators"].get("rsi", {}).get("value", 50.0)
        vwap = ind_data["indicators"].get("vwap", {}).get("value", current_price)

    rules: dict[str, str] = {}
    fired_rule = None

    sl_pct = exit_cfg.get("stop_loss_pct", -2.0)
    rules["rule_1_sl_neg2pct"] = "FIRED" if pnl_pct <= sl_pct else "NOT_FIRED"
    if pnl_pct <= sl_pct and not fired_rule:
        fired_rule = f"stop_loss_{sl_pct}%"

    tp_pct = exit_cfg.get("take_profit_pct", 2.0)
    rules["rule_2_tp_pos2pct"] = "FIRED" if pnl_pct >= tp_pct else "NOT_FIRED"
    if pnl_pct >= tp_pct and not fired_rule:
        fired_rule = f"take_profit_{tp_pct}%"

    stagnation_cycles = exit_cfg.get("stagnation_cycles", 6)
    rules["rule_3_stagnation"] = "FIRED" if cycles_flat >= stagnation_cycles else "NOT_FIRED"
    if cycles_flat >= stagnation_cycles and not fired_rule:
        fired_rule = "stagnation_timeout"

    death_vol = exit_cfg.get("momentum_death_vol_ratio", 0.5)
    death_grace = exit_cfg.get("momentum_death_grace_bars", 3)
    momentum_dead = vol_ratio < death_vol and bars_held >= death_grace
    rules["rule_4_momentum_death"] = "FIRED" if momentum_dead else "NOT_FIRED"
    if momentum_dead and not fired_rule:
        fired_rule = "momentum_death"

    ob_rsi = exit_cfg.get("ob_exhaustion_rsi", 75)
    vol_dropping = vol_ratio < 1.0
    price_rising = pnl_pct > 0
    ob_exhausted = rsi > ob_rsi and vol_dropping and price_rising
    rules["rule_5_ob_exhaustion"] = "FIRED" if ob_exhausted else "NOT_FIRED"
    if ob_exhausted and not fired_rule:
        fired_rule = "ob_exhaustion"

    vwap_loss = False
    if side == "long" and current_price < vwap and entry_price > vwap:
        vwap_loss = True
    elif side == "short" and current_price > vwap and entry_price < vwap:
        vwap_loss = True
    rules["rule_6_vwap_loss"] = "FIRED" if vwap_loss else "NOT_FIRED"
    if vwap_loss and not fired_rule:
        fired_rule = "vwap_loss"

    verdict = "EXIT" if fired_rule else "HOLD"
    action = "close" if fired_rule else None

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "current_price": round(current_price, 6),
        "pnl_pct": round(pnl_pct, 2),
        "cycles_flat": cycles_flat,
        "exit_rules": rules,
        "verdict": verdict,
        "action": action,
        "exit_reason": fired_rule,
    }
