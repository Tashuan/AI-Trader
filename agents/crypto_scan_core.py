"""
crypto_scan_core.py — Shared, side-effect-free CryptoRunner scan logic.

Crypto-specific strategy parameters, indicator computations (including EMA
trend alignment), composite scoring, entry qualification, position review
with crypto-tuned exit rules, and helper functions for daily-trend agreement,
BTC regime filtering, and liquidity floor checks.

This module has NO network I/O (no yfinance, no HTTP calls) — every function
takes data (a DataFrame or a pre-computed indicator dict) as input.
"""

from typing import Any, Optional
import numpy as np
import pandas as pd


# ============================================================
# Canonical Default Strategy Parameters (Crypto Swing/Trend)
# ============================================================

CRYPTO_DEFAULT_PARAMS: dict[str, Any] = {
    "exit_rules": {
        "stop_loss_pct": -5.0,
        "stop_loss_pct_clamp": [-3.0, -5.0],
        "take_profit_pct": 8.0,
        "take_profit_pct_clamp": [6.0, 10.0],
        "stagnation_hours": 3,
        "stagnation_threshold_pct": 1.5,
        "momentum_death_vol_ratio": 0.4,
        "momentum_death_grace_hours": 5,
        "ob_exhaustion_rsi": 80,
        "trailing_sl_pct": 3.0,
        "trailing_activation_pct": 4.0,
    },
    "entry_criteria": {
        "min_signals": 5,
        "min_signal_families": 3,
        "min_vol_ratio": 1.5,
        "direction_mode": "both",
        "require_daily_trend_agreement": True,
        "require_btc_regime_ok_for_alts": True,
        "require_btc_regime_alignment": False,
        "min_avg_dollar_volume": 500000,
        "bearish_macro_min_signals": 6,
        "bearish_macro_threshold": 0.3,
        "regime_lookback_days": 55,
        "regime_persistence_bars": 3,
        "regime_neutral_mode": "block",
        "btc_self_filter": True,
    },
    "position_sizing": {
        "max_positions": 3,
        "normal_sizing_min_pct": 12,
        "normal_sizing_max_pct": 16,
        "approaching_sizing_min_pct": 8,
        "approaching_sizing_max_pct": 12,
        "final_stretch_tp_pct": 5.0,
        "max_position_dollar_cap": None,
        "slippage_buffer_pct": 0.15,
        "daily_loss_size_cut_pct": 50,
        "consecutive_loss_threshold": 3,
        "consecutive_loss_size_cut_pct": 50,
        "consecutive_loss_min_signals": 6,
        "daily_pnl_reset_timezone": "UTC",
    },
    "switch_logic": {
        "switch_score_threshold_pct": 30,
        "switch_require_profitable": True,
        "reentry_cooldown_hours": 8,
    },
    "scoring_weights": {
        "signal_count_weight": 0.30,
        "family_diversity_weight": 0.25,
        "candle_quality_weight": 0.15,
        "consolidation_bonus_weight": 0.15,
        "trend_strength_weight": 0.15,
    },
    "exposure_controls": {
        "max_correlated_positions": 2,
        "correlation_buckets": [],
        "reserve_btc_slot": False,
    },
    "indicators": {
        "candle_interval": "1d",
        "confirm_interval": "1d",
        "lookback_period": "1y",
        "rsi_period": 14,
        "rsi_bullish": 55,
        "rsi_overbought": 80,
        "rsi_oversold": 25,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "sma_periods": [20, 50, 200],
        "ema_periods": [9, 21, 55],
        "stochastic_period": 14,
        "atr_period": 14,
        "bb_squeeze_ratio": 0.6,
        "candle_body_conviction": 0.6,
        "candle_body_doji": 0.3,
        "vol_ratio_bullish": 1.3,
        "vol_ratio_dead": 0.4,
    },
    "watchlist": [
        "BTC", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK",
        "DOT", "LTC", "UNI", "ATOM", "NEAR", "ARB", "OP", "INJ",
        "SUI", "SEI", "TIA", "PEPE", "SHIB", "MATIC", "APT", "BCH",
    ],
    "sweep": {
        "enabled": True,
        "sweep_min_vol_ratio": 1.3,
        "sweep_min_price_change_pct": 2.0,
        "sweep_max_qualifiers": 15,
    },
    "cycle_timing": {
        "poll_interval_default": 1800,
        "poll_interval_min": 300,
        "poll_interval_max": 3600,
    },
}

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
    """Convert symbol to yfinance ticker format — always crypto (-USD)."""
    return f"{symbol}-USD"


def hours_to_cycles(hours: float, poll_interval_seconds: int) -> int:
    """Convert hour-based timeout to cycle count using the live poll interval."""
    return max(1, round(hours * 3600 / poll_interval_seconds))


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


def compute_ema_alignment(ema9: float, ema21: float, ema55: float) -> tuple[str, str]:
    """Return (alignment_string, signal).

    Bullish stack: 9 > 21 > 55 → ("9>21>55", "bullish")
    Bearish stack: 9 < 21 < 55 → ("9<21<55", "bearish")
    Otherwise → ("mixed", "neutral")
    """
    if ema9 > ema21 > ema55:
        return "9>21>55", "bullish"
    elif ema9 < ema21 < ema55:
        return "9<21<55", "bearish"
    return "mixed", "neutral"


def detect_obv_divergence(df: pd.DataFrame, obv: pd.Series, lookback: int = 10) -> bool:
    if len(df) < lookback + 1:
        return False
    price_trend = df['Close'].iloc[-1] > df['Close'].iloc[-lookback]
    obv_trend = obv.iloc[-1] < obv.iloc[-lookback]
    return bool(price_trend and obv_trend)


def detect_consolidation_breakout(df: pd.DataFrame, lookback: int = 3) -> bool:
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
    if vol_ratio > 1.3 and bb_state_str in ("expanding", "squeezing") and quality == "full_body":
        return "imbalance_bullish" if vol_ratio > 1.3 else "imbalance_bearish"
    elif vol_ratio < 0.4:
        return "dead"
    else:
        return "balanced"


# ============================================================
# Indicator Family Definitions (for confluence scoring)
# ============================================================

INDICATOR_FAMILIES: dict[str, list[str]] = {
    "trend": ["sma_alignment", "ema21", "macd_hist"],
    "trend_strength": ["ema_alignment"],
    "momentum": ["rsi", "stochastic", "return_1h"],
    "volume": ["vol_ratio", "obv_divergence"],
    "volatility": ["atr", "bb_state"],
    "timing": ["vwap", "candle_body_ratio", "consolidation_breakout"],
}

FAMILY_CORRELATION_GROUPS: dict[str, list[str]] = {
    "trend_stack": ["trend", "trend_strength"],
    "momentum_osc": ["momentum"],
    "volume_flow": ["volume"],
    "volatility_range": ["volatility"],
    "timing_micro": ["timing"],
}


def family_confluence_score(indicators: dict[str, Any]) -> tuple[int, float]:
    """Count distinct families with directional signals and compute diversity ratio.

    Returns (family_count, diversity_ratio) where diversity_ratio = family_count / total_families.
    Correlated families (e.g. trend + trend_strength) are counted once per
    correlation group to reduce overcounting.
    """
    family_signals: dict[str, str] = {}
    for name, data in indicators.items():
        fam = data.get("family", "")
        sig = data.get("signal", "neutral")
        if sig != "neutral" and fam:
            if fam not in family_signals:
                family_signals[fam] = sig

    correlated_count = 0
    seen_groups: set[str] = set()
    for group_name, members in FAMILY_CORRELATION_GROUPS.items():
        has_signal = any(f in family_signals for f in members)
        if has_signal:
            correlated_count += 1
            for m in members:
                seen_groups.add(m)

    for fam in family_signals:
        if fam not in seen_groups:
            correlated_count += 1

    total_groups = len(FAMILY_CORRELATION_GROUPS)
    diversity = correlated_count / total_groups if total_groups > 0 else 0.0
    return correlated_count, diversity


# ============================================================
# Regime Classification (persistent, symmetric)
# ============================================================

def classify_regime(
    btc_daily_df: pd.DataFrame | None,
    ts: pd.Timestamp | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify BTC regime as bullish, bearish, or neutral with persistence.

    Uses EMA21 on daily closes plus SMA50 alignment. Requires the regime
    to persist for regime_persistence_bars consecutive bars to avoid flicker.

    Returns dict with:
      - regime: "bullish" | "bearish" | "neutral"
      - persistence_bars: how many consecutive bars the regime has held
      - ema21, sma50, close: raw values for transparency
      - allows_long, allows_short: symmetric policy booleans
    """
    cfg = (params or {}).get("entry_criteria", {})
    lookback = cfg.get("regime_lookback_days", 55)
    persistence_needed = cfg.get("regime_persistence_bars", 3)
    neutral_mode = cfg.get("regime_neutral_mode", "block")

    if btc_daily_df is None or btc_daily_df.empty:
        return {"regime": "neutral", "persistence_bars": 0, "allows_long": neutral_mode == "allow",
                "allows_short": neutral_mode == "allow"}

    col = "Datetime" if "Datetime" in btc_daily_df.columns else "Date"
    df = btc_daily_df.sort_values(col).reset_index(drop=True)
    if ts is not None:
        df = df[df[col] < pd.Timestamp(ts).normalize()]

    if len(df) < 22:
        return {"regime": "neutral", "persistence_bars": 0, "allows_long": neutral_mode == "allow",
                "allows_short": neutral_mode == "allow"}

    closes = df["Close"].values
    ema21 = pd.Series(closes).ewm(span=21).mean().values
    sma50 = pd.Series(closes).rolling(50).mean().values

    regimes: list[str] = []
    for i in range(len(closes) - 1, max(len(closes) - lookback - 1, 20), -1):
        if i < 0 or np.isnan(ema21[i]) or np.isnan(sma50[i]):
            break
        if closes[i] > ema21[i] and ema21[i] > sma50[i]:
            regimes.append("bullish")
        elif closes[i] < ema21[i] and ema21[i] < sma50[i]:
            regimes.append("bearish")
        else:
            regimes.append("neutral")

    if not regimes:
        return {"regime": "neutral", "persistence_bars": 0, "allows_long": neutral_mode == "allow",
                "allows_short": neutral_mode == "allow"}

    current = regimes[0]
    persistence = 0
    for r in regimes:
        if r == current:
            persistence += 1
        else:
            break

    if persistence < persistence_needed:
        current = "neutral"

    allows_long = current == "bullish" or (current == "neutral" and neutral_mode == "allow")
    allows_short = current == "bearish" or (current == "neutral" and neutral_mode == "allow")

    return {
        "regime": current,
        "persistence_bars": persistence,
        "ema21": round(float(ema21[-1]), 4) if not np.isnan(ema21[-1]) else 0.0,
        "sma50": round(float(sma50[-1]), 4) if not np.isnan(sma50[-1]) else 0.0,
        "close": round(float(closes[-1]), 4),
        "allows_long": allows_long,
        "allows_short": allows_short,
    }


def regime_filter_entry(
    symbol: str,
    direction: str,
    regime: dict[str, Any],
    params: dict[str, Any],
) -> tuple[bool, str]:
    """Check whether the BTC regime allows this entry. Returns (allowed, reason).

    Symmetric policy: bearish blocks longs, bullish blocks shorts.
    If require_btc_regime_alignment is True, neutral also blocks both.
    """
    cfg = params.get("entry_criteria", {})
    require_alignment = cfg.get("require_btc_regime_alignment", False)
    btc_self_filter = cfg.get("btc_self_filter", True)
    regime_label = regime.get("regime", "neutral")

    if symbol == "BTC" and not btc_self_filter:
        return True, ""

    if regime_label == "bullish" and direction == "short":
        return False, "btc_regime_bullish_blocks_short"
    if regime_label == "bearish" and direction == "long":
        return False, "btc_regime_bearish_blocks_long"
    if regime_label == "neutral":
        neutral_mode = cfg.get("regime_neutral_mode", "block")
        if neutral_mode == "block":
            return False, "btc_regime_neutral_blocked"
        elif neutral_mode == "reduce":
            return True, "btc_regime_neutral_reduced"
        else:
            return True, ""

    if require_alignment and regime_label == "neutral":
        return False, "btc_regime_neutral_alignment_required"

    return True, ""


# ============================================================
# Exposure and Symbol-Quality Controls
# ============================================================

DEFAULT_CORRELATION_BUCKETS: list[list[str]] = [
    ["BTC", "WBTC", "BCH"],
    ["ETH", "STETH", "ETC"],
    ["SOL", "AVAX", "NEAR", "SUI", "SEI", "APT", "TIA"],
    ["DOGE", "SHIB", "PEPE", "MATIC"],
    ["LINK", "UNI", "ATOM", "DOT", "ARB", "OP", "INJ"],
    ["LTC", "XRP", "ADA"],
]


def check_symbol_eligibility(
    symbol: str,
    df: pd.DataFrame | None,
    params: dict[str, Any],
) -> tuple[bool, str]:
    """Data-aware symbol quality check. Returns (eligible, reason).

    Checks:
      - Minimum data rows (30 bars)
      - Minimum average dollar volume
      - Non-zero recent volume
    """
    if df is None or df.empty:
        return False, "no_data"

    if len(df) < 30:
        return False, f"insufficient_bars_{len(df)}"

    cfg = params.get("entry_criteria", {})
    min_adv = float(cfg.get("min_avg_dollar_volume", 500000))
    adv = float((df["Close"] * df["Volume"]).mean())
    if adv < min_adv:
        return False, f"low_dollar_volume_{adv:.0f}"

    recent_vol = df["Volume"].tail(10)
    if (recent_vol > 0).sum() < 5:
        return False, "insufficient_recent_volume"

    return True, ""


def _get_correlation_buckets(params: dict[str, Any]) -> list[list[str]]:
    exp_cfg = params.get("exposure_controls", {})
    buckets = exp_cfg.get("correlation_buckets", [])
    if not buckets:
        return DEFAULT_CORRELATION_BUCKETS
    return buckets


def _symbol_bucket(symbol: str, buckets: list[list[str]]) -> int | None:
    for i, bucket in enumerate(buckets):
        if symbol in bucket:
            return i
    return None


def check_correlation_exposure(
    symbol: str,
    open_positions: dict[str, Any],
    params: dict[str, Any],
) -> tuple[bool, str]:
    """Check whether adding this symbol would exceed correlated position limits.

    Returns (allowed, reason).
    """
    exp_cfg = params.get("exposure_controls", {})
    max_correlated = int(exp_cfg.get("max_correlated_positions", 2))
    buckets = _get_correlation_buckets(params)

    target_bucket = _symbol_bucket(symbol, buckets)
    if target_bucket is None:
        return True, ""

    correlated_count = 0
    for pos_symbol in open_positions:
        if pos_symbol == symbol:
            return False, "already_in_position"
        pos_bucket = _symbol_bucket(pos_symbol, buckets)
        if pos_bucket == target_bucket:
            correlated_count += 1

    if correlated_count >= max_correlated:
        return False, f"correlation_limit_{correlated_count}"

    return True, ""


def check_btc_slot_reservation(
    open_positions: dict[str, Any],
    params: dict[str, Any],
    max_positions: int,
) -> tuple[bool, str]:
    """If reserve_btc_slot is True, ensure one slot is reserved for BTC.

    Returns (can_add_non_btc, reason).
    """
    exp_cfg = params.get("exposure_controls", {})
    if not exp_cfg.get("reserve_btc_slot", False):
        return True, ""

    if "BTC" in open_positions:
        return True, ""

    if len(open_positions) >= max_positions - 1:
        return False, "btc_slot_reserved"

    return True, ""


# ============================================================
# Precomputed Indicators (for backtest performance)
# ============================================================

def precompute_indicators(df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Compute all indicator series across the full DataFrame once."""
    ind_cfg = params.get("indicators", {})
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']
    n = len(df)

    avg_vol_20 = volume.rolling(20).mean()
    raw_vol_ratio = (volume / avg_vol_20)
    raw_vol_ratio = raw_vol_ratio.where(volume > 0, np.nan)
    vol_ratio = raw_vol_ratio.ffill().fillna(0.0)

    atr_period = ind_cfg.get("atr_period", 14)
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_period).mean()

    bb_period = 20
    bb_sma = close.rolling(bb_period).mean()
    bb_std = close.rolling(bb_period).std()
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_sma
    bb_avg_width = bb_width.rolling(50).mean()
    bb_squeeze = (bb_width / bb_avg_width).fillna(1.0)
    bb_squeeze = bb_squeeze.where(bb_avg_width > 0, 1.0)

    sma_periods = ind_cfg.get("sma_periods", [20, 50, 200])
    sma20_s = close.rolling(sma_periods[0] if len(sma_periods) > 0 else 20).mean()
    sma50_s = close.rolling(sma_periods[1] if len(sma_periods) > 1 else 50).mean()
    sma200_s = close.rolling(sma_periods[2] if len(sma_periods) > 2 else 200).mean()

    ema_periods = ind_cfg.get("ema_periods", [9, 21, 55])
    ema9_s = close.ewm(span=ema_periods[0] if len(ema_periods) > 0 else 9).mean()
    ema21_s = close.ewm(span=ema_periods[1] if len(ema_periods) > 1 else 21).mean()
    ema55_s = close.ewm(span=ema_periods[2] if len(ema_periods) > 2 else 55).mean()

    macd_fast = ind_cfg.get("macd_fast", 12)
    macd_slow = ind_cfg.get("macd_slow", 26)
    macd_signal = ind_cfg.get("macd_signal", 9)
    macd_line_s = close.ewm(span=macd_fast).mean() - close.ewm(span=macd_slow).mean()
    macd_signal_s = macd_line_s.ewm(span=macd_signal).mean()
    macd_hist_s = macd_line_s - macd_signal_s

    rsi_period = ind_cfg.get("rsi_period", 14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi_s = (100 - (100 / (1 + rs))).fillna(50.0)

    stoch_period = ind_cfg.get("stochastic_period", 14)
    stoch_low = low.rolling(stoch_period).min()
    stoch_high = high.rolling(stoch_period).max()
    stoch_k_s = ((close - stoch_low) / (stoch_high - stoch_low) * 100).fillna(50.0)
    stoch_d_s = stoch_k_s.rolling(3).mean().fillna(50.0)

    obv_s = (np.sign(close.diff()) * volume).fillna(0).cumsum()

    typical_price = (high + low + close) / 3
    cum_tp_vol = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    vwap_s = (cum_tp_vol / cum_vol).fillna(close)

    body = (close - df['Open']).abs()
    range_ = (high - low).replace(0, np.nan)
    body_ratio_s = (body / range_).fillna(0.0)

    ret_1h_s = (close.pct_change() * 100).fillna(0.0)

    obv_div_s = pd.Series(False, index=df.index)
    lookback = 10
    for i in range(lookback + 1, n):
        price_up = close.iloc[i] > close.iloc[i - lookback]
        obv_down = obv_s.iloc[i] < obv_s.iloc[i - lookback]
        obv_div_s.iloc[i] = bool(price_up and obv_down)

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
        "ema9": ema9_s.values,
        "ema21": ema21_s.values,
        "ema55": ema55_s.values,
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


# ============================================================
# Deep Scan — 14 indicators + EMA trend alignment (15 total)
# ============================================================

def deep_scan_symbol_from_df(symbol: str, df: pd.DataFrame, params: dict[str, Any]) -> dict[str, Any]:
    """Run all 15 indicators on a single symbol using a pre-fetched DataFrame.

    Includes the new EMA trend alignment indicator (family: trend_strength).
    Entry qualification uses 5+ signals across 3+ families with vol > 1.3x.
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

    # Volume ratio
    prev_vol = df['Volume'].tail(20).mean()
    cur_vol = float(last['Volume'])
    if cur_vol > 0 and prev_vol and prev_vol > 0:
        vol_ratio = cur_vol / prev_vol
    elif cur_vol == 0:
        recent_vol = df['Volume'].iloc[-20:]
        recent_nonzero = recent_vol[recent_vol > 0]
        if len(recent_nonzero) > 0 and prev_vol and prev_vol > 0:
            vol_ratio = float(recent_nonzero.iloc[-1] / prev_vol)
        else:
            vol_ratio = 1.0
    else:
        vol_ratio = 0.0

    atr = compute_atr(df, ind_cfg.get("atr_period", 14))
    bb_upper, bb_lower, bb_width, bb_squeeze = compute_bollinger(df, 20)
    bb_state_str = bb_state(bb_width, bb_squeeze, ind_cfg.get("bb_squeeze_ratio", 0.6))

    sma_periods = ind_cfg.get("sma_periods", [20, 50, 200])
    sma20 = compute_sma(df, sma_periods[0] if len(sma_periods) > 0 else 20)
    sma50 = compute_sma(df, sma_periods[1] if len(sma_periods) > 1 else 50)
    sma200 = compute_sma(df, sma_periods[2] if len(sma_periods) > 2 else 200)
    sma_align = sma_alignment(sma20, sma50, sma200)

    ema_periods = ind_cfg.get("ema_periods", [9, 21, 55])
    ema9 = compute_ema(df, ema_periods[0] if len(ema_periods) > 0 else 9)
    ema21 = compute_ema(df, ema_periods[1] if len(ema_periods) > 1 else 21)
    ema55 = compute_ema(df, ema_periods[2] if len(ema_periods) > 2 else 55)
    ema_align_str, ema_align_signal = compute_ema_alignment(ema9, ema21, ema55)

    macd_hist, macd_line = compute_macd(
        df,
        ind_cfg.get("macd_fast", 12),
        ind_cfg.get("macd_slow", 26),
        ind_cfg.get("macd_signal", 9),
    )

    rsi = compute_rsi(df, ind_cfg.get("rsi_period", 14))
    stoch_k, stoch_d = compute_stochastic(df, ind_cfg.get("stochastic_period", 14))
    obv = compute_obv(df)
    obv_div = detect_obv_divergence(df, obv)

    vwap = compute_vwap(df)
    body_ratio = candle_body_ratio(df)
    candle_qual = candle_quality(
        body_ratio,
        ind_cfg.get("candle_body_conviction", 0.6),
        ind_cfg.get("candle_body_doji", 0.3),
    )
    consolidation_bo = detect_consolidation_breakout(df)

    ret_1h = float(((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100)

    mkt_state = market_state(vol_ratio, bb_state_str, candle_qual)

    rsi_bull = ind_cfg.get("rsi_bullish", 55)
    rsi_ob = ind_cfg.get("rsi_overbought", 80)
    vol_bull = ind_cfg.get("vol_ratio_bullish", 1.3)
    vol_dead = ind_cfg.get("vol_ratio_dead", 0.4)

    indicators: dict[str, Any] = {}
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    families: set[str] = set()

    def _add_indicator(name, value, signal, family):
        nonlocal bullish_count, bearish_count, neutral_count
        indicators[name] = {"value": value, "signal": signal, "family": family}
        if signal != "neutral":
            families.add(family)
        if signal == "bullish":
            bullish_count += 1
        elif signal == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1

    # 14 standard indicators (same families as scan_core)
    vol_signal = "bullish" if vol_ratio > vol_bull else ("bearish" if vol_ratio < vol_dead else "neutral")
    _add_indicator("vol_ratio", round(vol_ratio, 2), vol_signal, "volume")

    _add_indicator("atr", round(atr, 4), "neutral", "volatility")

    bb_signal = "bullish" if bb_state_str == "expanding" else "neutral"
    _add_indicator("bb_state", bb_state_str, bb_signal, "volatility")

    sma_signal = "bullish" if "20>50" in sma_align else ("bearish" if "20<50" in sma_align else "neutral")
    _add_indicator("sma_alignment", sma_align, sma_signal, "trend")

    ema_signal = "bullish" if price > ema21 else "bearish"
    _add_indicator("ema21", round(ema21, 4), ema_signal, "trend")

    macd_signal = "bullish" if macd_hist > 0 else "bearish"
    _add_indicator("macd_hist", round(macd_hist, 4), macd_signal, "trend")

    rsi_signal = "bullish" if rsi > rsi_bull else ("bearish" if rsi < ind_cfg.get("rsi_oversold", 25) else "neutral")
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

    # 15th indicator: EMA trend alignment (new family: trend_strength)
    _add_indicator("ema_alignment", ema_align_str, ema_align_signal, "trend_strength")

    # Entry qualification
    entry_cfg = params.get("entry_criteria", {})
    min_signals = entry_cfg.get("min_signals", 5)
    min_families = entry_cfg.get("min_signal_families", 3)
    min_vol = entry_cfg.get("min_vol_ratio", 1.3)

    direction = "long" if bullish_count > bearish_count else "short"
    directional_count = max(bullish_count, bearish_count)
    direction_mode = entry_cfg.get("direction_mode", "both")
    direction_allowed = direction_mode == "both" or direction == direction_mode

    # Use family confluence scoring (reduces correlated overcounting)
    confluence_families, confluence_diversity = family_confluence_score(indicators)

    qualifies = (
        direction_allowed
        and directional_count >= min_signals
        and confluence_families >= min_families
        and vol_ratio > min_vol
        and not obv_div
    )

    # Composite score using confluence-aware family count
    weights = params.get("scoring_weights", {})
    signal_count_score = max(bullish_count, bearish_count) / 15.0
    family_diversity_score = confluence_diversity
    candle_quality_score = min(body_ratio, 1.0)
    consolidation_bonus = 1.0 if consolidation_bo else 0.0
    trend_strength_bonus = 1.0 if ema_align_signal != "neutral" else 0.0

    composite_score = (
        signal_count_score * weights.get("signal_count_weight", 0.30)
        + family_diversity_score * weights.get("family_diversity_weight", 0.25)
        + candle_quality_score * weights.get("candle_quality_weight", 0.15)
        + consolidation_bonus * weights.get("consolidation_bonus_weight", 0.15)
        + trend_strength_bonus * weights.get("trend_strength_weight", 0.15)
    ) * 10.0

    return {
        "price": round(price, 6),
        "market_state": mkt_state,
        "indicators": indicators,
        "signal_count": {"bullish": bullish_count, "bearish": bearish_count, "neutral": neutral_count},
        "families_represented": sorted(list(families)),
        "confluence_families": confluence_families,
        "confluence_diversity": round(confluence_diversity, 3),
        "qualifies_for_entry": qualifies,
        "entry_direction": direction,
        "candle_quality": candle_qual,
        "obv_divergence": obv_div,
        "consolidation_breakout": consolidation_bo,
        "composite_score": round(composite_score, 2),
        "atr": round(atr, 6),
    }


# ============================================================
# Position Review — 6 Hard Exit Rules (Crypto-Tuned)
# ============================================================

def review_position_from_indicators(
    pos: dict[str, Any],
    params: dict[str, Any],
    cycles_flat: int,
    ind_data: dict[str, Any],
    bars_held: int = 0,
) -> dict[str, Any]:
    """Evaluate the 6 exit rules for a position using a pre-computed indicator dict.

    Crypto-tuned: wider stops (-5%), higher TP (+8%), higher OB threshold (80),
    lower momentum death vol ratio (0.4), and hour-based stagnation/grace
    converted to cycle counts by the caller.
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

    # Rule 1: Hard stop-loss
    sl_pct = exit_cfg.get("stop_loss_pct", -5.0)
    rules["rule_1_sl"] = "FIRED" if pnl_pct <= sl_pct else "NOT_FIRED"
    if pnl_pct <= sl_pct and not fired_rule:
        fired_rule = f"stop_loss_{sl_pct}%"

    # Rule 2: Take profit
    tp_pct = exit_cfg.get("take_profit_pct", 8.0)
    rules["rule_2_tp"] = "FIRED" if pnl_pct >= tp_pct else "NOT_FIRED"
    if pnl_pct >= tp_pct and not fired_rule:
        fired_rule = f"take_profit_{tp_pct}%"

    # Rule 3: Stagnation timeout (caller converts hours → cycles)
    stagnation_cycles = exit_cfg.get("_stagnation_cycles", 16)
    rules["rule_3_stagnation"] = "FIRED" if cycles_flat >= stagnation_cycles else "NOT_FIRED"
    if cycles_flat >= stagnation_cycles and not fired_rule:
        fired_rule = "stagnation_timeout"

    # Rule 4: Momentum death (vol ratio < threshold after grace period)
    death_vol = exit_cfg.get("momentum_death_vol_ratio", 0.4)
    death_grace = exit_cfg.get("_momentum_death_grace_bars", 8)
    momentum_dead = vol_ratio < death_vol and bars_held >= death_grace
    rules["rule_4_momentum_death"] = "FIRED" if momentum_dead else "NOT_FIRED"
    if momentum_dead and not fired_rule:
        fired_rule = "momentum_death"

    # Rule 5: Overbought exhaustion (long) / oversold bounce (short)
    ob_rsi = exit_cfg.get("ob_exhaustion_rsi", 80)
    vol_dropping = vol_ratio < 1.0
    if side == "long":
        price_rising = pnl_pct > 0
        ob_exhausted = rsi > ob_rsi and vol_dropping and price_rising
        rules["rule_5_ob_exhaustion"] = "FIRED" if ob_exhausted else "NOT_FIRED"
        if ob_exhausted and not fired_rule:
            fired_rule = "ob_exhaustion"
    else:
        price_falling = pnl_pct > 0
        os_rsi = 100 - ob_rsi  # mirror for shorts
        os_bounced = rsi < os_rsi and vol_dropping and price_falling
        rules["rule_5_oversold_bounce"] = "FIRED" if os_bounced else "NOT_FIRED"
        if os_bounced and not fired_rule:
            fired_rule = "oversold_bounce"

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
        "bars_held": bars_held,
        "exit_rules": rules,
        "verdict": verdict,
        "action": action,
        "exit_reason": fired_rule,
    }


# ============================================================
# ATR-based SL/TP with clamping
# ============================================================

def compute_atr_sl_tp(entry_price: float, side: str, scan_data: dict,
                      params: dict) -> tuple[float, float, float, float]:
    """Compute ATR-based SL/TP with percentage clamping.

    SL = 1.5x ATR, TP = 3x ATR (2:1 reward/risk ratio), then clamped
    into configured pct-of-entry ranges.
    Returns (stop_loss_price, take_profit_price, trailing_sl_pct, trailing_activation_pct).
    """
    exit_cfg = params.get("exit_rules", {})
    atr = scan_data.get("atr", 0) or scan_data.get("indicators", {}).get("atr", {}).get("value", 0)

    if atr <= 0:
        atr = entry_price * 0.03  # 3% fallback for crypto

    sl_distance = 1.5 * atr
    tp_distance = 3.0 * atr

    # Convert to pct of entry and clamp
    sl_pct_raw = (sl_distance / entry_price) * 100
    tp_pct_raw = (tp_distance / entry_price) * 100

    sl_clamp = exit_cfg.get("stop_loss_pct_clamp", [-3.0, -5.0])
    tp_clamp = exit_cfg.get("take_profit_pct_clamp", [6.0, 10.0])

    # Clamp: sl_pct is negative distance, clamp[0] is min (e.g. -3%), clamp[1] is max (e.g. -5%)
    # We want the absolute distance clamped between |clamp[0]| and |clamp[1]|
    sl_abs = min(max(abs(sl_pct_raw), abs(sl_clamp[0])), abs(sl_clamp[1]))
    tp_abs = min(max(tp_pct_raw, tp_clamp[0]), tp_clamp[1])

    sl_distance = entry_price * (sl_abs / 100.0)
    tp_distance = entry_price * (tp_abs / 100.0)

    if side == "long":
        stop_loss_price = entry_price - sl_distance
        take_profit_price = entry_price + tp_distance
    else:
        stop_loss_price = entry_price + sl_distance
        take_profit_price = entry_price - tp_distance

    trail_sl_pct = exit_cfg.get("trailing_sl_pct", 3.0)
    trail_act_pct = exit_cfg.get("trailing_activation_pct", 4.0)

    return stop_loss_price, take_profit_price, trail_sl_pct, trail_act_pct
