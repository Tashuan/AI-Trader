#!/usr/bin/env python3
"""
scan.py — Deterministic TA Checklist for BlitzTrader Goal Runner

Two-tier scanning:
  Tier 1 (broad sweep): vol ratio + 1h return filter on watchlist + yfinance top markets
  Tier 2 (deep scan): 15 indicators across 5 layers on qualifying symbols

Output: structured JSON with all indicators, composite scores, ranked setups,
position review with 6 exit rules, and daily P&L.

Usage:
  python3 scan.py --token $TOKEN          # Full scan + position review
  python3 scan.py                          # Scan only (defaults)
  python3 scan.py --symbol BTC             # Single symbol debug
  python3 scan.py --config '{"...": ...}'  # Inline config override
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# Default Strategy Parameters
# ============================================================

DEFAULT_PARAMS: dict[str, Any] = {
    "exit_rules": {
        "stop_loss_pct": -2.0,
        "take_profit_pct": 2.0,
        "stagnation_cycles": 6,
        "stagnation_threshold_pct": 0.3,
        "momentum_death_vol_ratio": 0.5,
        "ob_exhaustion_rsi": 75,
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
_CRYPTO_SYMBOLS = {
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "ADA", "XRP", "LINK",
    "MATIC", "DOT", "LTC", "BCH", "UNI", "ATOM", "NEAR", "APT",
    "ARB", "OP", "INJ", "SUI", "SEI", "TIA", "PEPE", "SHIB",
}

# Default sweep universe (top crypto by market cap + top equities + commodities)
_SWEEP_CRYPTO = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK", "DOT", "LTC", "UNI", "ATOM", "NEAR", "ARB", "OP"]
_SWEEP_EQUITIES = ["NVDA", "TSLA", "AAPL", "AMZN", "META", "MSFT", "GOOGL", "AMD", "NFLX", "JPM", "BAC", "V", "DIS", "SHOP", "COIN"]
_SWEEP_COMMODITIES = ["GC=F", "SI=F", "CL=F", "SPY", "^GSPC"]


# ============================================================
# Config Loading
# ============================================================

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_config(token: Optional[str], inline_config: Optional[str]) -> dict[str, Any]:
    """Load config in priority: inline > API > defaults."""
    params = dict(DEFAULT_PARAMS)

    if token:
        try:
            url = "http://localhost:8000/api/claw/agents/me/strategy-params"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if isinstance(data, dict) and "strategy_params" in data:
                    params = _deep_merge(params, data["strategy_params"])
                elif isinstance(data, dict):
                    params = _deep_merge(params, data)
        except Exception as e:
            print(f"[scan.py] Warning: could not fetch strategy params from API: {e}", file=sys.stderr)

    if inline_config:
        try:
            override = json.loads(inline_config)
            params = _deep_merge(params, override)
        except json.JSONDecodeError as e:
            print(f"[scan.py] Warning: invalid inline config JSON: {e}", file=sys.stderr)

    return params


def _yf_ticker(symbol: str) -> str:
    """Convert symbol to yfinance ticker format."""
    if symbol in _CRYPTO_SYMBOLS:
        return f"{symbol}-USD"
    return symbol


# ============================================================
# Tier 1: Broad Sweep
# ============================================================

def _sweep_scan(params: dict[str, Any]) -> list[str]:
    """Tier 1: broad sweep to find symbols with volume + price movement."""
    sweep_cfg = params.get("sweep", {})
    if not sweep_cfg.get("enabled", True):
        return []

    min_vol_ratio = sweep_cfg.get("sweep_min_vol_ratio", 1.5)
    min_price_change = sweep_cfg.get("sweep_min_price_change_pct", 1.0)
    max_qualifiers = sweep_cfg.get("sweep_max_qualifiers", 10)

    # Build sweep universe
    watchlist = params.get("watchlist", [])
    sweep_universe = list(set(_SWEEP_CRYPTO + _SWEEP_EQUITIES + _SWEEP_COMMODITIES + watchlist))

    qualifiers = []
    errors = []

    for symbol in sweep_universe:
        try:
            ticker = _yf_ticker(symbol)
            t = yf.Ticker(ticker)
            df = t.history(period="5d", interval="1h")
            if df is None or df.empty or len(df) < 22:
                continue

            vol_ratio = float(df['Volume'].iloc[-1] / df['Volume'].tail(20).mean()) if df['Volume'].tail(20).mean() > 0 else 0
            ret_1h = abs(float(((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100))

            if vol_ratio > min_vol_ratio and ret_1h > min_price_change:
                qualifiers.append(symbol)
        except Exception as e:
            errors.append(f"{symbol}: {e}")

    # Cap qualifiers
    return qualifiers[:max_qualifiers]


# ============================================================
# Tier 2: Deep Scan — 15 Indicators
# ============================================================

def _compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    return float(rsi) if not np.isnan(rsi) else 50.0


def _compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float]:
    macd_line = df['Close'].ewm(span=fast).mean() - df['Close'].ewm(span=slow).mean()
    signal_line = macd_line.ewm(span=signal).mean()
    hist = (macd_line - signal_line).iloc[-1]
    macd_val = macd_line.iloc[-1]
    return float(hist) if not np.isnan(hist) else 0.0, float(macd_val) if not np.isnan(macd_val) else 0.0


def _compute_sma(df: pd.DataFrame, period: int) -> float:
    sma = df['Close'].rolling(period).mean().iloc[-1]
    return float(sma) if not np.isnan(sma) else 0.0


def _compute_ema(df: pd.DataFrame, period: int) -> float:
    ema = df['Close'].ewm(span=period).mean().iloc[-1]
    return float(ema) if not np.isnan(ema) else 0.0


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0


def _compute_bollinger(df: pd.DataFrame, period: int = 20) -> tuple[float, float, float, float]:
    """Returns (upper, lower, width, squeeze_ratio)."""
    sma = df['Close'].rolling(period).mean()
    std = df['Close'].rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    width = ((upper - lower) / sma).iloc[-1]
    # Squeeze: current width vs average width
    avg_width = ((upper - lower) / sma).rolling(50).mean().iloc[-1]
    squeeze_ratio = float(width / avg_width) if avg_width and not np.isnan(avg_width) and avg_width > 0 else 1.0
    return float(upper.iloc[-1]), float(lower.iloc[-1]), float(width) if not np.isnan(width) else 0.0, squeeze_ratio


def _compute_stochastic(df: pd.DataFrame, period: int = 14) -> tuple[float, float]:
    low_min = df['Low'].rolling(period).min()
    high_max = df['High'].rolling(period).max()
    k = ((df['Close'] - low_min) / (high_max - low_min)) * 100
    d = k.rolling(3).mean()
    k_val = k.iloc[-1]
    d_val = d.iloc[-1]
    return float(k_val) if not np.isnan(k_val) else 50.0, float(d_val) if not np.isnan(d_val) else 50.0


def _compute_obv(df: pd.DataFrame) -> pd.Series:
    obv = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    return obv


def _compute_vwap(df: pd.DataFrame) -> float:
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    return float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else float(df['Close'].iloc[-1])


def _detect_obv_divergence(df: pd.DataFrame, obv: pd.Series, lookback: int = 10) -> bool:
    """Price up but OBV flat/down = fake breakout warning."""
    if len(df) < lookback + 1:
        return False
    price_trend = df['Close'].iloc[-1] > df['Close'].iloc[-lookback]
    obv_trend = obv.iloc[-1] < obv.iloc[-lookback]
    return bool(price_trend and obv_trend)


def _detect_consolidation_breakout(df: pd.DataFrame, lookback: int = 3) -> bool:
    """Was ranging (tight spread) for last `lookback` candles, now breaking out."""
    if len(df) < lookback + 2:
        return False
    recent = df.iloc[-lookback-1:-1]
    current = df.iloc[-1]
    recent_spread = (recent['High'] - recent['Low']).mean()
    current_spread = current['High'] - current['Low']
    recent_range = recent['High'].max() - recent['Low'].min()
    # Was consolidating (tight range) and now candle is larger than average
    was_tight = recent_range / recent['Close'].mean() < 0.03
    breaking = current_spread > recent_spread * 1.5
    return bool(was_tight and breaking)


def _candle_body_ratio(df: pd.DataFrame) -> float:
    """abs(close-open) / (high-low) for last candle."""
    last = df.iloc[-1]
    body = abs(last['Close'] - last['Open'])
    range_ = last['High'] - last['Low']
    return float(body / range_) if range_ and range_ > 0 else 0.0


def _candle_quality(ratio: float, conviction: float = 0.6, doji: float = 0.3) -> str:
    if ratio >= conviction:
        return "full_body"
    elif ratio <= doji:
        return "doji"
    else:
        return "wicked"


def _sma_alignment(sma20: float, sma50: float, sma200: float) -> str:
    if sma20 > sma50 > sma200:
        return "20>50>200"
    elif sma20 < sma50 < sma200:
        return "20<50<200"
    elif sma20 > sma50:
        return "20>50"
    elif sma20 < sma50:
        return "20<50"
    return "mixed"


def _bb_state(width: float, squeeze_ratio: float, squeeze_threshold: float = 0.6) -> str:
    if squeeze_ratio < squeeze_threshold:
        return "squeezing"
    elif width > 0.05:
        return "expanding"
    else:
        return "normal"


def _market_state(vol_ratio: float, bb_state_str: str, candle_quality: str) -> str:
    if vol_ratio > 1.5 and bb_state_str in ("expanding", "squeezing") and candle_quality == "full_body":
        return "imbalance_bullish" if vol_ratio > 1.5 else "imbalance_bearish"
    elif vol_ratio < 0.5:
        return "dead"
    else:
        return "balanced"


def _deep_scan_symbol(symbol: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run all 15 indicators on a single symbol."""
    ind_cfg = params.get("indicators", {})
    interval = ind_cfg.get("candle_interval", "1h")
    lookback = ind_cfg.get("lookback_period", "1mo")

    ticker = _yf_ticker(symbol)
    t = yf.Ticker(ticker)
    df = t.history(period=lookback, interval=interval)

    if df is None or df.empty or len(df) < 30:
        return {
            "error": "no_data",
            "error_detail": f"yfinance returned insufficient data ({len(df) if df is not None else 0} rows)",
            "qualifies_for_entry": False,
        }

    last = df.iloc[-1]
    price = float(last['Close'])

    # Layer 1: Market State
    prev_vol = df['Volume'].tail(20).mean()
    vol_ratio = float(last['Volume'] / prev_vol) if prev_vol and prev_vol > 0 else 0.0
    atr = _compute_atr(df, ind_cfg.get("atr_period", 14))
    bb_upper, bb_lower, bb_width, bb_squeeze = _compute_bollinger(df, 20)
    bb_state_str = _bb_state(bb_width, bb_squeeze, ind_cfg.get("bb_squeeze_ratio", 0.6))

    # Layer 2: Trend Direction
    sma_periods = ind_cfg.get("sma_periods", [20, 50, 200])
    sma20 = _compute_sma(df, sma_periods[0] if len(sma_periods) > 0 else 20)
    sma50 = _compute_sma(df, sma_periods[1] if len(sma_periods) > 1 else 50)
    sma200 = _compute_sma(df, sma_periods[2] if len(sma_periods) > 2 else 200)
    sma_align = _sma_alignment(sma20, sma50, sma200)
    ema20 = _compute_ema(df, ind_cfg.get("ema_period", 20))
    macd_hist, macd_line = _compute_macd(
        df,
        ind_cfg.get("macd_fast", 12),
        ind_cfg.get("macd_slow", 26),
        ind_cfg.get("macd_signal", 9),
    )

    # Layer 3: Momentum Quality
    rsi = _compute_rsi(df, ind_cfg.get("rsi_period", 14))
    stoch_k, stoch_d = _compute_stochastic(df, ind_cfg.get("stochastic_period", 14))
    obv = _compute_obv(df)
    obv_div = _detect_obv_divergence(df, obv)

    # Layer 4: Entry Timing
    vwap = _compute_vwap(df)
    body_ratio = _candle_body_ratio(df)
    candle_qual = _candle_quality(
        body_ratio,
        ind_cfg.get("candle_body_conviction", 0.6),
        ind_cfg.get("candle_body_doji", 0.3),
    )
    consolidation_bo = _detect_consolidation_breakout(df)

    # 1h return
    ret_1h = float(((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100)

    # Market state
    mkt_state = _market_state(vol_ratio, bb_state_str, candle_qual)

    # Signal scoring
    rsi_bull = ind_cfg.get("rsi_bullish", 55)
    rsi_ob = ind_cfg.get("rsi_overbought", 75)
    vol_bull = ind_cfg.get("vol_ratio_bullish", 1.5)
    vol_dead = ind_cfg.get("vol_ratio_dead", 0.5)

    indicators = {}
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    families = set()

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

    # 1. Volume ratio
    vol_signal = "bullish" if vol_ratio > vol_bull else ("bearish" if vol_ratio < vol_dead else "neutral")
    _add_indicator("vol_ratio", round(vol_ratio, 2), vol_signal, "volume")

    # 2. ATR
    atr_signal = "neutral"  # ATR is contextual
    _add_indicator("atr", round(atr, 4), atr_signal, "volatility")

    # 3. BB state
    bb_signal = "bullish" if bb_state_str == "expanding" else ("neutral" if bb_state_str == "squeezing" else "neutral")
    _add_indicator("bb_state", bb_state_str, bb_signal, "volatility")

    # 4. SMA alignment
    sma_signal = "bullish" if "20>50" in sma_align else ("bearish" if "20<50" in sma_align else "neutral")
    _add_indicator("sma_alignment", sma_align, sma_signal, "trend")

    # 5. EMA 20
    ema_signal = "bullish" if price > ema20 else "bearish"
    _add_indicator("ema20", round(ema20, 4), ema_signal, "trend")

    # 6. MACD histogram
    macd_signal = "bullish" if macd_hist > 0 else "bearish"
    _add_indicator("macd_hist", round(macd_hist, 4), macd_signal, "trend")

    # 7. RSI
    rsi_signal = "bullish" if rsi > rsi_bull else ("bearish" if rsi < ind_cfg.get("rsi_oversold", 30) else "neutral")
    _add_indicator("rsi", round(rsi, 1), rsi_signal, "momentum")

    # 8. Stochastic
    stoch_signal = "bullish" if stoch_k > stoch_d and stoch_k < 80 else ("bearish" if stoch_k < stoch_d and stoch_k > 20 else "neutral")
    _add_indicator("stochastic", {"k": round(stoch_k, 1), "d": round(stoch_d, 1)}, stoch_signal, "momentum")

    # 9. OBV divergence
    obv_signal = "bearish" if obv_div else "neutral"
    _add_indicator("obv_divergence", obv_div, obv_signal, "volume")

    # 10. VWAP
    vwap_signal = "bullish" if price > vwap else "bearish"
    _add_indicator("vwap", round(vwap, 4), vwap_signal, "timing")

    # 11. Candle body ratio
    candle_signal = "bullish" if body_ratio >= ind_cfg.get("candle_body_conviction", 0.6) else ("bearish" if body_ratio <= ind_cfg.get("candle_body_doji", 0.3) else "neutral")
    _add_indicator("candle_body_ratio", round(body_ratio, 3), candle_signal, "timing")

    # 12. Consolidation breakout
    cons_signal = "bullish" if consolidation_bo else "neutral"
    _add_indicator("consolidation_breakout", consolidation_bo, cons_signal, "timing")

    # 1h return as momentum indicator (not in the 12 scored indicators but used for sweep)
    _add_indicator("return_1h", round(ret_1h, 2), "bullish" if ret_1h > 0 else "bearish", "momentum")

    # Entry qualification
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

    # Direction: majority of bullish vs bearish
    direction = "long" if bullish_count > bearish_count else "short"

    # Composite score (0-10 scale)
    weights = params.get("scoring_weights", {})
    signal_count_score = bullish_count / 13.0  # 12 indicators + 1h return
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
# Position Review
# ============================================================

def _fetch_positions(token: str) -> list[dict[str, Any]]:
    """Fetch open positions from the API."""
    try:
        url = "http://localhost:8000/api/positions"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if isinstance(data, dict) and "positions" in data:
                return data["positions"]
            elif isinstance(data, list):
                return data
            return []
    except Exception as e:
        print(f"[scan.py] Warning: could not fetch positions: {e}", file=sys.stderr)
        return []


def _fetch_current_price(symbol: str) -> Optional[float]:
    """Fetch current price via yfinance."""
    try:
        ticker = _yf_ticker(symbol)
        t = yf.Ticker(ticker)
        df = t.history(period="1d", interval="1m")
        if df is not None and not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


def _review_position(pos: dict[str, Any], params: dict[str, Any], cycles_flat: int) -> dict[str, Any]:
    """Evaluate 6 exit rules for a position."""
    exit_cfg = params.get("exit_rules", {})

    symbol = pos.get("symbol", "")
    side = pos.get("side", "long")
    entry_price = float(pos.get("entry_price", 0))
    current_price = float(pos.get("current_price", 0)) or _fetch_current_price(symbol) or entry_price

    if side == "long":
        pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
    else:
        pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price else 0

    # Fetch indicator data for this symbol
    ind_data = _deep_scan_symbol(symbol, params)
    vol_ratio = 0.0
    rsi = 50.0
    vwap = current_price
    if "indicators" in ind_data:
        vol_ratio = ind_data["indicators"].get("vol_ratio", {}).get("value", 0.0)
        rsi = ind_data["indicators"].get("rsi", {}).get("value", 50.0)
        vwap = ind_data["indicators"].get("vwap", {}).get("value", current_price)

    # Evaluate exit rules
    rules = {}
    fired_rule = None

    # Rule 1: Hard stop-loss
    sl_pct = exit_cfg.get("stop_loss_pct", -2.0)
    rules["rule_1_sl_neg2pct"] = "FIRED" if pnl_pct <= sl_pct else "NOT_FIRED"
    if pnl_pct <= sl_pct and not fired_rule:
        fired_rule = f"stop_loss_{sl_pct}%"

    # Rule 2: Profit target
    tp_pct = exit_cfg.get("take_profit_pct", 2.0)
    rules["rule_2_tp_pos2pct"] = "FIRED" if pnl_pct >= tp_pct else "NOT_FIRED"
    if pnl_pct >= tp_pct and not fired_rule:
        fired_rule = f"take_profit_{tp_pct}%"

    # Rule 3: Stagnation
    stagnation_cycles = exit_cfg.get("stagnation_cycles", 6)
    rules["rule_3_stagnation"] = "FIRED" if cycles_flat >= stagnation_cycles else "NOT_FIRED"
    if cycles_flat >= stagnation_cycles and not fired_rule:
        fired_rule = "stagnation_timeout"

    # Rule 4: Momentum death
    death_vol = exit_cfg.get("momentum_death_vol_ratio", 0.5)
    rules["rule_4_momentum_death"] = "FIRED" if vol_ratio < death_vol else "NOT_FIRED"
    if vol_ratio < death_vol and not fired_rule:
        fired_rule = "momentum_death"

    # Rule 5: OB exhaustion
    ob_rsi = exit_cfg.get("ob_exhaustion_rsi", 75)
    vol_dropping = vol_ratio < 1.0
    price_rising = pnl_pct > 0
    ob_exhausted = rsi > ob_rsi and vol_dropping and price_rising
    rules["rule_5_ob_exhaustion"] = "FIRED" if ob_exhausted else "NOT_FIRED"
    if ob_exhausted and not fired_rule:
        fired_rule = "ob_exhaustion"

    # Rule 6: VWAP loss
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


def _patch_position_state(token: str, position_id: int, cycles_flat: int, entry_score: float) -> None:
    """PATCH position state back to the API."""
    try:
        url = f"http://localhost:8000/api/positions/{position_id}/state"
        data = json.dumps({"cycles_flat": cycles_flat, "entry_score": entry_score}).encode()
        req = urllib.request.Request(url, data=data, method="PATCH", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[scan.py] Warning: could not patch position state: {e}", file=sys.stderr)


# ============================================================
# Daily P&L
# ============================================================

def _compute_daily_pnl(token: Optional[str]) -> dict[str, float]:
    """Compute today's P&L (resets at midnight UTC)."""
    if not token:
        return {"realized": 0.0, "unrealized": 0.0, "total": 0.0}

    try:
        now_utc = datetime.now(timezone.utc)
        midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        since = midnight.isoformat()

        url = f"http://localhost:8000/api/positions?since={since}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        # This is a simplified version — the actual P&L would come from closed trades
        # For now, return zeros and let the agent compute from its own data
        return {"realized": 0.0, "unrealized": 0.0, "total": 0.0}
    except Exception:
        return {"realized": 0.0, "unrealized": 0.0, "total": 0.0}


# ============================================================
# Main Scan
# ============================================================

def run_scan(
    token: Optional[str] = None,
    inline_config: Optional[str] = None,
    single_symbol: Optional[str] = None,
) -> dict[str, Any]:
    """Run the full scan and return JSON-serializable dict."""
    params = _load_config(token, inline_config)
    watchlist = params.get("watchlist", ["BTC", "ETH", "SOL"])

    # Determine symbols to deep scan
    if single_symbol:
        deep_scan_symbols = [single_symbol]
    else:
        # Tier 1: Broad sweep
        sweep_qualifiers = _sweep_scan(params) if not single_symbol else []
        # Watchlist always passes to Tier 2; add sweep qualifiers
        deep_scan_symbols = list(set(watchlist + sweep_qualifiers))

    # Tier 2: Deep scan
    symbols_output = {}
    for symbol in deep_scan_symbols:
        try:
            symbols_output[symbol] = _deep_scan_symbol(symbol, params)
        except Exception as e:
            symbols_output[symbol] = {
                "error": "scan_failed",
                "error_detail": str(e),
                "qualifies_for_entry": False,
            }

    # Rank setups
    ranked = []
    for symbol, data in symbols_output.items():
        if data.get("qualifies_for_entry") and "composite_score" in data:
            ranked.append({
                "symbol": symbol,
                "score": data["composite_score"],
                "direction": data.get("entry_direction", "long"),
            })
    ranked.sort(key=lambda x: x["score"], reverse=True)

    # Position review (if token provided)
    positions_output = []
    open_position_count = 0
    max_positions_reached = False

    if token:
        positions = _fetch_positions(token)
        open_position_count = len(positions)
        max_pos = params.get("position_sizing", {}).get("max_positions", 1)
        max_positions_reached = open_position_count >= max_pos

        for pos in positions:
            cycles_flat = int(pos.get("cycles_flat", 0))
            entry_score = float(pos.get("entry_score", 0))
            pos_id = pos.get("id")

            review = _review_position(pos, params, cycles_flat)

            # Increment cycles_flat if position was flat this cycle
            stagnation_threshold = params.get("exit_rules", {}).get("stagnation_threshold_pct", 0.3)
            if abs(review["pnl_pct"]) < stagnation_threshold:
                cycles_flat += 1
            else:
                cycles_flat = 0

            review["cycles_flat"] = cycles_flat
            positions_output.append(review)

            # Patch state back to API
            if pos_id:
                _patch_position_state(token, pos_id, cycles_flat, entry_score)

    # Daily P&L
    daily_pnl = _compute_daily_pnl(token)

    return {
        "scan_time": datetime.now(timezone.utc).isoformat() + "Z",
        "symbols": symbols_output,
        "ranked_setups": ranked,
        "positions": positions_output,
        "daily_pnl": daily_pnl,
        "open_position_count": open_position_count,
        "max_positions_reached": max_positions_reached,
    }


def main():
    parser = argparse.ArgumentParser(description="BlitzTrader deterministic TA scan")
    parser.add_argument("--token", help="Agent auth token (enables position review + API config)")
    parser.add_argument("--config", help="Inline JSON config override")
    parser.add_argument("--symbol", help="Single symbol debug mode")
    parser.add_argument("--backtest", action="store_true", help="Backtest mode (historical replay)")
    parser.add_argument("--from", dest="from_date", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="Backtest end date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.backtest:
        print(json.dumps({"error": "Backtest mode not yet implemented"}, indent=2))
        sys.exit(0)

    result = run_scan(
        token=args.token,
        inline_config=args.config,
        single_symbol=args.symbol,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
