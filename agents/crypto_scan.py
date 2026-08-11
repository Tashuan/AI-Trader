#!/usr/bin/env python3
"""
crypto_scan.py — Deterministic TA scan for CryptoRunner.

Two-tier scanning over a 24-symbol crypto universe:
  Tier 1 (broad sweep): vol ratio + price change filter on 4h candles
  Tier 2 (deep scan): 15 indicators across 6 families on qualifying symbols,
    plus daily-trend agreement, BTC regime filter (alts), and liquidity floor gates

Output: structured JSON with all indicators, composite scores, ranked setups,
position reviews with 6 exit rules, and daily P&L.

Usage:
  python3 crypto_scan.py --token $TOKEN          # Full scan + position review
  python3 crypto_scan.py                          # Scan only (defaults)
  python3 crypto_scan.py --symbol BTC             # Single symbol debug
  python3 crypto_scan.py --config '{"...": ...}'  # Inline config override
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
# Shared, side-effect-free indicator/scoring/exit-rule logic
_AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)
import crypto_scan_core as core
from arena_market_data import ArenaMarketDataProvider, get_arena_market_data

_DATA_PROVIDER: ArenaMarketDataProvider = get_arena_market_data()


# ============================================================
# Default Strategy Parameters (re-exported from crypto_scan_core)
# ============================================================

DEFAULT_PARAMS: dict[str, Any] = core.CRYPTO_DEFAULT_PARAMS


# ============================================================
# Config Loading
# ============================================================

def _deep_merge(base: dict, override: dict) -> dict:
    return core.deep_merge(base, override)


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
            print(f"[crypto_scan] Warning: could not fetch strategy params from API: {e}", file=sys.stderr)

    if inline_config:
        try:
            override = json.loads(inline_config)
            params = _deep_merge(params, override)
        except json.JSONDecodeError as e:
            print(f"[crypto_scan] Warning: invalid inline config JSON: {e}", file=sys.stderr)

    return params


def _yf_ticker(symbol: str) -> str:
    return core.yf_ticker(symbol)


# ============================================================
# Tier 1: Broad Sweep
# ============================================================

def _sweep_scan(params: dict[str, Any]) -> list[str]:
    """Tier 1: broad sweep to find crypto symbols with volume + price movement."""
    sweep_cfg = params.get("sweep", {})
    if not sweep_cfg.get("enabled", True):
        return []

    min_vol_ratio = sweep_cfg.get("sweep_min_vol_ratio", 1.3)
    min_price_change = sweep_cfg.get("sweep_min_price_change_pct", 2.0)
    max_qualifiers = sweep_cfg.get("sweep_max_qualifiers", 15)

    watchlist = params.get("watchlist", [])
    sweep_universe = list(set(watchlist))

    qualifiers = []
    for symbol in sweep_universe:
        try:
            ticker = _yf_ticker(symbol)
            df = _DATA_PROVIDER.history(ticker, period="5d", interval="1h")
            if df is None or df.empty or len(df) < 22:
                continue

            avg_vol = df['Volume'].tail(20).mean()
            cur_vol = float(df['Volume'].iloc[-1])
            if avg_vol > 0 and cur_vol > 0:
                vol_ratio = cur_vol / avg_vol
            else:
                vol_ratio = 1.0

            ret_1h = abs(float(((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100))

            if vol_ratio > min_vol_ratio and ret_1h > min_price_change:
                qualifiers.append(symbol)
        except Exception:
            pass

    return qualifiers[:max_qualifiers]


# ============================================================
# Daily-Trend Agreement, BTC Regime, Liquidity Floor
# ============================================================

def check_daily_trend_agreement(symbol: str, direction: str, params: dict[str, Any]) -> bool:
    """Check if daily trend agrees with the 4h entry direction.

    Fetches daily candles, computes SMA20, returns True if:
    - direction == "long" and price > daily SMA20
    - direction == "short" and price < daily SMA20
    """
    entry_cfg = params.get("entry_criteria", {})
    if not entry_cfg.get("require_daily_trend_agreement", True):
        return True

    try:
        ticker = _yf_ticker(symbol)
        df = _DATA_PROVIDER.history(ticker, period="6mo", interval="1d")
        if df is None or df.empty or len(df) < 20:
            return True  # can't verify, don't block

        sma20 = df['Close'].rolling(20).mean().iloc[-1]
        price = float(df['Close'].iloc[-1])

        if np.isnan(sma20):
            return True

        if direction == "long":
            return price > sma20
        else:
            return price < sma20
    except Exception:
        return True  # don't block on fetch errors


_btc_regime_cache: Optional[str] = None


def check_btc_regime_ok(params: dict[str, Any]) -> str:
    """Check BTC daily trend (price vs daily EMA21).

    Returns "bullish", "bearish", or "neutral".
    Cached per scan run to avoid redundant fetches.
    """
    global _btc_regime_cache
    if _btc_regime_cache is not None:
        return _btc_regime_cache

    try:
        df = _DATA_PROVIDER.history("BTC-USD", period="6mo", interval="1d")
        if df is None or df.empty or len(df) < 21:
            _btc_regime_cache = "neutral"
            return _btc_regime_cache

        ema21 = df['Close'].ewm(span=21).mean().iloc[-1]
        price = float(df['Close'].iloc[-1])

        if np.isnan(ema21):
            _btc_regime_cache = "neutral"
        elif price > ema21:
            _btc_regime_cache = "bullish"
        else:
            _btc_regime_cache = "bearish"
    except Exception:
        _btc_regime_cache = "neutral"

    return _btc_regime_cache


def reset_btc_regime_cache():
    """Reset the BTC regime cache at the start of each scan run."""
    global _btc_regime_cache
    _btc_regime_cache = None


def check_liquidity_floor(df: pd.DataFrame, params: dict[str, Any]) -> bool:
    """Check if average dollar volume meets the liquidity floor."""
    entry_cfg = params.get("entry_criteria", {})
    min_adv = entry_cfg.get("min_avg_dollar_volume", 500000)

    if df is None or df.empty:
        return False

    avg_dollar_vol = (df['Close'] * df['Volume']).mean()
    return float(avg_dollar_vol) >= min_adv


# ============================================================
# Tier 2: Deep Scan
# ============================================================

def _deep_scan_symbol(symbol: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch live 4h history for `symbol` and run the deep scan.

    Applies daily-trend agreement, BTC regime filter (alts), and liquidity floor
    as hard pre-entry gates — they veto the trade regardless of signal count.
    """
    ind_cfg = params.get("indicators", {})
    interval = ind_cfg.get("candle_interval", "4h")
    lookback = ind_cfg.get("lookback_period", "3mo")

    ticker = _yf_ticker(symbol)
    df = _DATA_PROVIDER.history(ticker, period=lookback, interval=interval)

    result = core.deep_scan_symbol_from_df(symbol, df, params)

    # Apply hard pre-entry gates (these don't add to signal count, they veto)
    if result.get("qualifies_for_entry"):
        direction = result.get("entry_direction", "long")
        entry_cfg = params.get("entry_criteria", {})

        # Gate 1: Daily-trend agreement
        if entry_cfg.get("require_daily_trend_agreement", True):
            if not check_daily_trend_agreement(symbol, direction, params):
                result["qualifies_for_entry"] = False
                result["entry_veto_reason"] = "daily_trend_disagreement"

        # Gate 2: BTC regime check (alts only)
        if result.get("qualifies_for_entry") and entry_cfg.get("require_btc_regime_ok_for_alts", True):
            if symbol.upper() != "BTC":
                btc_regime = check_btc_regime_ok(params)
                if btc_regime == "bearish" and direction == "long":
                    result["qualifies_for_entry"] = False
                    result["entry_veto_reason"] = "btc_regime_bearish"

        # Gate 3: Liquidity floor
        if result.get("qualifies_for_entry"):
            if not check_liquidity_floor(df, params):
                result["qualifies_for_entry"] = False
                result["entry_veto_reason"] = "liquidity_floor_not_met"

    return result


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
        print(f"[crypto_scan] Warning: could not fetch positions: {e}", file=sys.stderr)
        return []


def _fetch_current_price(symbol: str) -> Optional[float]:
    """Fetch current price via yfinance."""
    try:
        ticker = _yf_ticker(symbol)
        df = _DATA_PROVIDER.history(ticker, period="1d", interval="1m")
        if df is not None and not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


def _review_position(pos: dict[str, Any], params: dict[str, Any],
                     cycles_flat: int, bars_held: int) -> dict[str, Any]:
    """Evaluate the 6 exit rules for a position, fetching live indicator data.

    Now passes a real `bars_held` computed from the position's persisted entry_time.
    """
    symbol = pos.get("symbol", "")
    entry_price = float(pos.get("entry_price", 0))
    current_price = float(pos.get("current_price", 0)) or _fetch_current_price(symbol) or entry_price
    pos = {**pos, "current_price": current_price}

    ind_data = _deep_scan_symbol(symbol, params)
    return core.review_position_from_indicators(pos, params, cycles_flat, ind_data, bars_held)


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
        print(f"[crypto_scan] Warning: could not patch position state: {e}", file=sys.stderr)


# ============================================================
# Market Classification & Trade Execution
# ============================================================

def _classify_market(symbol: str) -> str:
    """All symbols are crypto."""
    return "crypto"


def _api_trade(token: str, action: str, symbol: str, quantity: float, market: str,
               stop_loss_price: float = None, take_profit_price: float = None,
               trailing_sl_pct: float = None, trailing_activation_pct: float = None,
               content: str = "") -> dict:
    """Execute a trade via POST /api/signals/realtime."""
    body: dict[str, Any] = {
        "market": market,
        "action": action,
        "symbol": symbol,
        "price": 0,
        "quantity": quantity,
        "executed_at": "now",
        "content": content,
    }
    if stop_loss_price is not None:
        body["stop_loss_price"] = stop_loss_price
    if take_profit_price is not None:
        body["take_profit_price"] = take_profit_price
    if trailing_sl_pct is not None:
        body["trailing_sl_pct"] = trailing_sl_pct
    if trailing_activation_pct is not None:
        body["trailing_activation_pct"] = trailing_activation_pct

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "http://localhost:8000/api/signals/realtime",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "detail": e.read().decode() if hasattr(e, 'read') else ""}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Main Scan
# ============================================================

def run_scan(
    token: Optional[str] = None,
    inline_config: Optional[str] = None,
    single_symbol: Optional[str] = None,
    auto_exit: bool = False,
    auto_enter: bool = False,
    position_entry_times: Optional[dict[str, str]] = None,
    poll_interval: int = 1800,
) -> dict[str, Any]:
    """Run the full scan and return JSON-serializable dict.

    `position_entry_times` maps symbol → ISO timestamp of entry, used to compute
    real `bars_held` from the candle interval duration.
    `poll_interval` is used to convert hour-based exit rule timeouts to cycle counts.
    """
    params = _load_config(token, inline_config)
    watchlist = params.get("watchlist", [])

    # Reset BTC regime cache for this scan run
    reset_btc_regime_cache()

    # Convert hour-based timeouts to cycle counts for position review
    exit_cfg = params.get("exit_rules", {})
    ind_cfg = params.get("indicators", {})
    candle_interval = ind_cfg.get("candle_interval", "4h")

    stagnation_cycles = core.hours_to_cycles(exit_cfg.get("stagnation_hours", 8), poll_interval)
    momentum_death_grace_bars = core.hours_to_cycles(
        exit_cfg.get("momentum_death_grace_hours", 32), poll_interval
    )
    # Store converted values for review_position_from_indicators
    exit_cfg["_stagnation_cycles"] = stagnation_cycles
    exit_cfg["_momentum_death_grace_bars"] = momentum_death_grace_bars

    # Compute candle interval in seconds for bars_held calculation
    interval_seconds = _interval_to_seconds(candle_interval)

    # Determine symbols to deep scan
    if single_symbol:
        deep_scan_symbols = [single_symbol]
    else:
        sweep_qualifiers = _sweep_scan(params) if not single_symbol else []
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
        max_pos = params.get("position_sizing", {}).get("max_positions", 3)
        max_positions_reached = open_position_count >= max_pos

        for pos in positions:
            cycles_flat = int(pos.get("cycles_flat", 0))
            entry_score = float(pos.get("entry_score", 0))
            pos_id = pos.get("id")

            # Compute bars_held from persisted entry_time
            bars_held = 0
            sym = pos.get("symbol", "")
            if position_entry_times and sym in position_entry_times:
                try:
                    entry_time = datetime.fromisoformat(position_entry_times[sym].replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    elapsed_seconds = (now - entry_time).total_seconds()
                    bars_held = max(0, int(elapsed_seconds / interval_seconds))
                except Exception:
                    bars_held = 0

            review = _review_position(pos, params, cycles_flat, bars_held)

            # Increment cycles_flat if position was flat this cycle
            stagnation_threshold = params.get("exit_rules", {}).get("stagnation_threshold_pct", 1.0)
            if abs(review["pnl_pct"]) < stagnation_threshold:
                cycles_flat += 1
            else:
                cycles_flat = 0

            review["cycles_flat"] = cycles_flat
            positions_output.append(review)

            if pos_id:
                _patch_position_state(token, pos_id, cycles_flat, entry_score)

    return {
        "scan_time": datetime.now(timezone.utc).isoformat() + "Z",
        "symbols": symbols_output,
        "ranked_setups": ranked,
        "positions": positions_output,
        "open_position_count": open_position_count,
        "max_positions_reached": max_positions_reached,
    }


def _interval_to_seconds(interval: str) -> int:
    """Convert yfinance interval string to seconds."""
    mapping = {
        "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "4h": 14400, "1d": 86400,
    }
    return mapping.get(interval, 14400)  # default 4h


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="CryptoRunner deterministic TA scan")
    parser.add_argument("--token", help="Agent auth token (enables position review + API config)")
    parser.add_argument("--config", help="Inline JSON config override")
    parser.add_argument("--symbol", help="Single symbol debug mode")
    parser.add_argument("--auto-exit", action="store_true", help="Automatically close positions with EXIT verdict")
    parser.add_argument("--auto-enter", action="store_true", help="Automatically enter top-ranked setup")
    args = parser.parse_args()

    result = run_scan(
        token=args.token,
        inline_config=args.config,
        single_symbol=args.symbol,
        auto_exit=args.auto_exit,
        auto_enter=args.auto_enter,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
