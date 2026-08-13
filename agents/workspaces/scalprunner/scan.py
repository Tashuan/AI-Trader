#!/usr/bin/env python3
"""
scalp_scan.py — Live I/O wrapper for the 4-step scalp scan.

Executes the human trader's 4-step process:
  1. Discover: Schwab movers + platform news + volume scanner → shortlist
  2. Filter: Real-time spread/depth/dollar-volume → liquid candidates
  3. Analyze: Multi-TF (1m/5m/15m) + Fib + S/R + breakout patterns → ranked setups
  4. (Pre-positioning handled by scalp_runner.py via pending order API)

This module handles all network I/O (Schwab API, yfinance fallback, platform
news) and delegates pure strategy logic to scalp_scan_core.py.

Usage:
  python3 scalp_scan.py --token $TOKEN          # Full scan
  python3 scalp_scan.py                          # Scan only (defaults)
  python3 scalp_scan.py --symbol NVDA            # Single symbol debug
  python3 scalp_scan.py --config '{"...": ...}'  # Inline config override
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

_AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)

import scalp_scan_core
from scalp_scan_core import SCALP_DEFAULT_PARAMS, deep_merge
from arena_market_data import get_arena_market_data
from schwab_provider import get_schwab_provider
from alpaca_realtime_provider import get_alpaca_provider

# Default equity universe for the scanner (broad sweep)
# Overridable via params.discovery.scanner_universe
_SCANNER_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "QQQ", "SPY", "IWM", "BA", "DIS",
    "BABA", "JD", "COIN", "MARA", "RIOT", "SOFI", "AAL", "UAL",
    "F", "GM", "NIO", "XPEV", "PLUG", "FCEL", "DKNG", "PENN",
]

# Fallback universe if Schwab movers unavailable
# Overridable via params.discovery.fallback_shortlist
_FALLBACK_SHORTLIST = ["NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL"]


# ============================================================
# Config Loading
# ============================================================

def _load_config(token: Optional[str], inline_config: Optional[str]) -> dict[str, Any]:
    """Load config in priority: inline > API > defaults."""
    params = dict(SCALP_DEFAULT_PARAMS)

    if token:
        try:
            url = "http://localhost:8000/api/claw/agents/me/strategy-params"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if isinstance(data, dict) and "strategy_params" in data:
                    params = deep_merge(params, data["strategy_params"])
                elif isinstance(data, dict):
                    params = deep_merge(params, data)
        except Exception as e:
            print(f"[ScalpScan] Warning: could not fetch strategy params: {e}")

    if inline_config:
        try:
            override = json.loads(inline_config)
            params = deep_merge(params, override)
        except json.JSONDecodeError as e:
            print(f"[ScalpScan] Warning: invalid inline config JSON: {e}")

    return params


# ============================================================
# Data Fetching
# ============================================================

def _fetch_history(symbol: str, interval: str, lookback_bars: int = 200,
                   params: dict | None = None) -> Optional[pd.DataFrame]:
    """Fetch Arena history through the canonical provider router."""
    p = params or {}
    df_cfg = p.get("data_fetch", {})
    if interval == "1d":
        period = df_cfg.get("daily_period", "3mo")
        min_bars = int(df_cfg.get("daily_min_bars", 10))
    elif interval in ("1m", "5m", "15m"):
        period = df_cfg.get("intraday_period", "5d")
        min_bars = int(df_cfg.get("intraday_min_bars", 30))
    else:
        period = df_cfg.get("default_period", "1mo")
        min_bars = int(df_cfg.get("intraday_min_bars", 30))
    try:
        df = get_arena_market_data().history(symbol, period=period, interval=interval)
        if df is not None and not df.empty and len(df) >= min_bars:
            return df.tail(lookback_bars)
    except Exception as e:
        print(f"[ScalpScan] history failed for {symbol} {interval}: {e}")
    return None


def _fetch_quote(symbol: str) -> Optional[dict]:
    """Fetch a live quote through Schwab-first Arena routing."""
    try:
        quote = get_arena_market_data().quote(symbol)
        return quote if quote and quote.get("last", 0) > 0 else None
    except Exception:
        return None


def _fetch_level2(symbol: str) -> Optional[dict]:
    """Fetch Level 2 order book.

    For crypto pairs: tries Alpaca crypto orderbook (free tier, full L2 depth).
    For equities: tries Schwab (requires Level 2 subscription).
    """
    sym = symbol.strip().upper()

    # Crypto: Alpaca free tier offers full L2 orderbook
    if "/" in sym or sym in ("BTC", "ETH", "SOL", "DOGE", "LTC", "BCH", "AVAX", "LINK"):
        alpaca = get_alpaca_provider()
        if alpaca.is_configured:
            try:
                return alpaca.level2(sym)
            except Exception:
                pass

    # Equities: Schwab Level 2 (requires subscription)
    provider = get_schwab_provider()
    if provider.is_configured:
        try:
            return provider.level2(sym)
        except Exception:
            pass
    return None


# ============================================================
# Step 1: Discover Shortlist
# ============================================================

def _discover_shortlist(params: dict, token: Optional[str] = None) -> list[str]:
    """Step 1: Build shortlist from movers + news + scanner.

    Returns a list of symbols ranked by momentum score.
    """
    discovery_cfg = params.get("discovery", {})
    max_shortlist = discovery_cfg.get("max_shortlist", 15)
    candidates: dict[str, dict] = {}

    # 1a. Schwab movers are the live-equity discovery source.
    if discovery_cfg.get("movers_enabled", True):
        provider = get_schwab_provider()
        if provider.is_configured:
            try:
                movers = provider.movers_all()
                for m in movers:
                    sym = m.get("symbol", "")
                    if sym and sym not in candidates:
                        candidates[sym] = {
                            "symbol": sym,
                            "change_pct": abs(m.get("change_pct", 0)),
                            "source": "schwab_movers",
                        }
            except Exception as e:
                print(f"[ScalpScan] Schwab movers failed: {e}")

    # Alpaca snapshots remain the discovery fallback.
    scanner_universe = discovery_cfg.get("scanner_universe", _SCANNER_UNIVERSE)
    if discovery_cfg.get("movers_enabled", True) and not candidates:
        alpaca = get_alpaca_provider()
        if alpaca.is_configured:
            try:
                movers = alpaca.screen_movers(scanner_universe, top_n=max_shortlist)
                for m in movers:
                    sym = m.get("symbol", "")
                    if sym and sym not in candidates:
                        candidates[sym] = {
                            "symbol": sym,
                            "change_pct": abs(m.get("change_pct", 0)),
                            "source": "alpaca_movers",
                        }
            except Exception as e:
                print(f"[ScalpScan] Alpaca movers failed: {e}")

    # 1b. Platform News (extract tickers from recent news)
    if discovery_cfg.get("news_enabled", True) and token:
        try:
            news_symbols = _fetch_news_tickers(token, discovery_cfg.get("news_lookback_hours", 4), discovery_cfg)
            for sym in news_symbols:
                if sym not in candidates:
                    candidates[sym] = {"symbol": sym, "change_pct": 0, "source": "news"}
        except Exception as e:
            print(f"[ScalpScan] News fetch failed: {e}")

    # 1c. Volume/Price Scanner on broad universe
    if discovery_cfg.get("scanner_enabled", True):
        scanner_symbols = scanner_universe[:discovery_cfg.get("scanner_universe_size", 100)]
        min_vol_ratio = discovery_cfg.get("scanner_min_vol_ratio", 2.0)
        min_change = discovery_cfg.get("scanner_min_price_change_pct", 0.5)
        scanner_interval = discovery_cfg.get("scanner_interval", "5m")
        scanner_lookback = int(discovery_cfg.get("scanner_lookback_bars", 50))
        scanner_min_bars = int(discovery_cfg.get("scanner_min_bars", 20))
        scanner_vol_lookback = int(discovery_cfg.get("scanner_vol_lookback_bars", 20))

        for sym in scanner_symbols:
            try:
                df = _fetch_history(sym, scanner_interval, lookback_bars=scanner_lookback, params=params)
                if df is None or df.empty or len(df) < scanner_min_bars:
                    continue
                vol_ratio = float(df["Volume"].iloc[-1]) / max(float(df["Volume"].tail(scanner_vol_lookback).mean()), 1)
                price_change = abs((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[0]) - 1) * 100)

                if vol_ratio >= min_vol_ratio and price_change >= min_change:
                    if sym not in candidates:
                        candidates[sym] = {
                            "symbol": sym,
                            "change_pct": price_change,
                            "vol_ratio": vol_ratio,
                            "source": "scanner",
                        }
            except Exception:
                continue

    # If no candidates found, use fallback
    if not candidates:
        print("[ScalpScan] No candidates from movers/news/scanner — using fallback shortlist")
        fallback = discovery_cfg.get("fallback_shortlist", _FALLBACK_SHORTLIST)
        return fallback[:max_shortlist]

    # Rank by change_pct (momentum) and return top N
    ranked = sorted(candidates.values(), key=lambda c: c.get("change_pct", 0), reverse=True)
    return [c["symbol"] for c in ranked[:max_shortlist]]


def _fetch_news_tickers(token: str, lookback_hours: int,
                        discovery_cfg: dict | None = None) -> list[str]:
    """Fetch recent news from the platform and extract ticker symbols."""
    dc = discovery_cfg or {}
    news_limit = int(dc.get("news_limit", 50))
    news_process_limit = int(dc.get("news_process_limit", 50))
    max_ticker_len = int(dc.get("news_max_ticker_length", 5))
    max_symbols = int(dc.get("news_max_symbols", 20))
    try:
        url = f"http://localhost:8000/api/market/news?limit={news_limit}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            news_items = data if isinstance(data, list) else data.get("news", data.get("items", []))

        # Extract symbols from news items
        symbols = set()
        for item in news_items[:news_process_limit]:
            sym = item.get("symbol") or item.get("ticker")
            if sym and len(sym) <= max_ticker_len and sym.isalpha():
                symbols.add(sym.upper())
            # Also check content for $TICKER mentions
            content = item.get("content", "") or item.get("title", "")
            import re
            tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
            symbols.update(tickers)

        return list(symbols)[:max_symbols]
    except Exception:
        return []


# ============================================================
# Step 2: Liquidity Filter
# ============================================================

def _filter_liquidity(shortlist: list[str], params: dict) -> list[dict]:
    """Step 2: Filter shortlist by liquidity (spread, depth, dollar volume).

    Returns list of {"symbol", "quote", "level2", "liquidity"} for passing symbols.
    """
    results = []
    tf_cfg = params.get("timeframes", {})
    pattern_interval = tf_cfg.get("pattern_interval", "5m")
    lookback = tf_cfg.get("lookback_bars", 200)
    for sym in shortlist:
        quote = _fetch_quote(sym)
        level2 = _fetch_level2(sym)
        df = _fetch_history(sym, pattern_interval, lookback_bars=lookback, params=params)

        if df is None or df.empty:
            continue

        liq = scalp_scan_core.liquidity_score(quote or {}, level2, df, params)
        if liq.get("passes", False):
            results.append({
                "symbol": sym,
                "quote": quote,
                "level2": level2,
                "liquidity": liq,
                "df_5m": df,
            })

    return results


# ============================================================
# Step 3: Multi-Timeframe Analysis
# ============================================================

def _analyze_setup(symbol: str, quote: Optional[dict], level2: Optional[dict],
                   df_5m: pd.DataFrame, params: dict) -> dict[str, Any]:
    """Step 3: Run multi-TF analysis on a single symbol.

    Fetches 1m and 15m data, computes indicators, Fib levels, S/R,
    breakout detection, pattern detection, and composite score.
    """
    tf_cfg = params.get("timeframes", {})
    entry_interval = tf_cfg.get("entry_interval", "1m")
    trend_interval = tf_cfg.get("trend_interval", "15m")
    lookback = tf_cfg.get("lookback_bars", 200)

    # Fetch 1m and 15m data
    df_1m = _fetch_history(symbol, entry_interval, lookback, params=params)
    df_15m = _fetch_history(symbol, trend_interval, lookback, params=params)

    entry_min_bars = int(params.get("data_fetch", {}).get("entry_min_bars", 30))
    if df_1m is None or df_1m.empty or len(df_1m) < entry_min_bars:
        return {"symbol": symbol, "error": "no_1m_data", "qualifies": False}

    # Precompute indicators on all 3 timeframes
    pre = scalp_scan_core.precompute_indicators_multi_tf(df_1m, df_5m, df_15m, params)
    bar_idx = len(df_1m) - 1

    # Multi-TF deep scan
    mtf_result = scalp_scan_core.deep_scan_multi_tf(symbol, pre, bar_idx, params)

    # Fibonacci levels
    swings = scalp_scan_core.detect_swing_highs_lows(
        df_5m, params.get("levels", {}).get("sr_lookback_bars", 50), params,
    )
    swing_highs = swings.get("swing_highs", [])
    swing_lows = swings.get("swing_lows", [])

    direction = mtf_result.get("entry_direction", "long")
    fib_levels = {}
    fib_extensions = {}
    if swing_highs and swing_lows:
        # Use most recent swing high and low
        recent_high = max(p for _, p in swing_highs[-3:])
        recent_low = min(p for _, p in swing_lows[-3:])
        fib_levels = scalp_scan_core.compute_fib_retracement(recent_high, recent_low, direction, params)
        fib_extensions = scalp_scan_core.compute_fib_extension(recent_high, recent_low, direction, params)

    # Support/Resistance
    sr_levels = scalp_scan_core.detect_support_resistance(
        df_5m,
        lookback=params.get("levels", {}).get("sr_lookback_bars", 50),
        min_touches=params.get("levels", {}).get("sr_min_touches", 2),
        tolerance_pct=params.get("levels", {}).get("sr_tolerance_pct", 0.15),
        params=params,
    )

    # Breakout detection
    breakout = scalp_scan_core.detect_breakout_level(df_5m, sr_levels, params)

    # Pattern detection
    pattern = scalp_scan_core.detect_pattern(df_5m, params)

    # Liquidity score
    liq = scalp_scan_core.liquidity_score(quote or {}, level2, df_5m, params)

    # Composite score
    setup = scalp_scan_core.score_scalp_setup(
        mtf_result, fib_levels, sr_levels, breakout, pattern, liq, params,
    )

    return {
        "symbol": symbol,
        "price": mtf_result.get("price", 0),
        "atr": mtf_result.get("atr", 0),
        "indicators": mtf_result.get("indicators", {}),
        "signal_count": mtf_result.get("signal_count", {}),
        "qualifies_for_entry": mtf_result.get("qualifies_for_entry", False),
        "entry_direction": direction,
        "composite_score": mtf_result.get("composite_score", 0),
        "confluence_score": mtf_result.get("confluence_score", 0),
        "trend_5m": mtf_result.get("trend_5m", {}),
        "trend_15m": mtf_result.get("trend_15m", {}),
        "fib_levels": fib_levels,
        "fib_extensions": fib_extensions,
        "sr_levels": {
            "supports": sr_levels.get("supports", []),
            "resistances": sr_levels.get("resistances", []),
        },
        "breakout": breakout,
        "pattern": pattern,
        "liquidity": liq,
        "setup": setup,
    }


def _check_premove_filter(symbol: str, direction: str, df_5m: pd.DataFrame,
                          params: dict) -> bool:
    """Pre-move cap filter: reject setups where the stock already moved too far.

    Returns True if the setup passes (should be kept), False if blocked.
    """
    premove_cfg = params.get("premove_filter", {})
    if not premove_cfg.get("enabled", True):
        return True

    max_move = float(premove_cfg.get("max_move_pct", 2.0))
    lookback = int(premove_cfg.get("lookback_bars", 8))

    if df_5m is None or df_5m.empty or len(df_5m) < lookback + 1:
        return True  # Can't compute, allow

    recent = df_5m.tail(lookback + 1)
    start_px = float(recent["Close"].iloc[0])
    end_px = float(recent["Close"].iloc[-1])
    if start_px <= 0:
        return True

    recent_ret = (end_px / start_px - 1) * 100.0

    # For longs: reject if stock already rose > max_move
    # For shorts: reject if stock already fell > max_move
    if direction == "long" and recent_ret > max_move:
        return False
    if direction == "short" and recent_ret < -max_move:
        return False

    return True


def _check_market_regime(direction: str, params: dict) -> tuple[bool, str]:
    """SPY daily EMA regime filter.

    Returns (passes, regime_label).
    Blocks short trades when SPY > daily EMA (bull regime).
    Blocks long trades when SPY < daily EMA (bear regime) if configured.
    """
    regime_cfg = params.get("market_regime", {})
    if not regime_cfg.get("enabled", True):
        return True, "disabled"

    spy_symbol = regime_cfg.get("symbol", "SPY")
    ema_period = int(regime_cfg.get("daily_ema_period", 10))
    threshold_pct = float(regime_cfg.get("threshold_pct", 0.0))

    try:
        spy_df = _fetch_history(spy_symbol, "1d", ema_period + 10)
    except Exception:
        spy_df = None

    if spy_df is None or spy_df.empty or len(spy_df) < ema_period + 1:
        return True, "no_data"  # Can't determine regime, allow

    closes = spy_df["Close"]
    ema = closes.ewm(span=ema_period, adjust=False).mean()
    spy_close = float(closes.iloc[-1])
    spy_ema = float(ema.iloc[-1])

    if pd.isna(spy_ema) or spy_ema <= 0:
        return True, "no_ema"

    bull_threshold = spy_ema * (1 + threshold_pct / 100.0)
    bear_threshold = spy_ema * (1 - threshold_pct / 100.0)

    if spy_close > bull_threshold:
        regime = "bull"
    elif spy_close < bear_threshold:
        regime = "bear"
    else:
        regime = "neutral"

    # Block shorts in bull regime
    if regime == "bull" and direction == "short" and regime_cfg.get("block_shorts_in_bull", True):
        return False, regime

    # Block longs in bear regime (optional)
    if regime == "bear" and direction == "long" and regime_cfg.get("block_longs_in_bear", False):
        return False, regime

    return True, regime


# ============================================================
# Main Scan Function
# ============================================================

def run_scan(token: Optional[str] = None, inline_config: Optional[str] = None,
             symbol: Optional[str] = None) -> dict[str, Any]:
    """Run the full 4-step scalp scan.

    If `symbol` is provided, only scan that single symbol (debug mode).
    Otherwise, run the full discovery → filter → analyze pipeline.

    Returns:
    {
        "shortlist": [symbols],
        "liquid_candidates": [{symbol, quote, liquidity}],
        "ranked_setups": [{symbol, score, direction, entry_level, sl_level, tp_level, ...}],
        "symbols": {symbol: full_analysis_dict},
        "scan_time": iso_timestamp,
    }
    """
    params = _load_config(token, inline_config)
    scan_start = datetime.now(timezone.utc)

    # Single symbol debug mode
    if symbol:
        shortlist = [symbol.upper()]
    else:
        shortlist = _discover_shortlist(params, token)

    print(f"[ScalpScan] Step 1: Shortlist = {shortlist}")

    # Step 2: Liquidity filter
    liquid = _filter_liquidity(shortlist, params)
    print(f"[ScalpScan] Step 2: {len(liquid)} liquid candidates (from {len(shortlist)})")

    # Step 3: Multi-TF analysis
    symbols_data = {}
    ranked_setups = []

    # Pre-fetch SPY regime once for the entire scan
    regime_cfg = params.get("market_regime", {})
    direction_mode = params.get("entry_criteria", {}).get("direction_mode", "short")
    spy_regime_label = "disabled"
    if regime_cfg.get("enabled", True):
        # Determine regime using the configured direction mode
        _, spy_regime_label = _check_market_regime(direction_mode, params)
    print(f"[ScalpScan] Market regime: {spy_regime_label}")

    for candidate in liquid:
        sym = candidate["symbol"]
        df_5m = candidate["df_5m"]
        analysis = _analyze_setup(sym, candidate.get("quote"), candidate.get("level2"), df_5m, params)
        symbols_data[sym] = analysis

        setup = analysis.get("setup", {})
        if setup.get("qualifies", False):
            direction = setup.get("direction", "long")

            # Pre-move cap filter
            if not _check_premove_filter(sym, direction, df_5m, params):
                print(f"[ScalpScan]   {sym} {direction}: blocked by pre-move cap")
                continue

            # SPY market regime filter
            regime_pass, regime = _check_market_regime(direction, params)
            if not regime_pass:
                print(f"[ScalpScan]   {sym} {direction}: blocked by market regime ({regime})")
                continue

            ranked_setups.append({
                "symbol": sym,
                "score": setup.get("score", 0),
                "direction": direction,
                "entry_level": setup.get("entry_level", 0),
                "sl_level": setup.get("sl_level", 0),
                "tp_level": setup.get("tp_level", 0),
                "atr": setup.get("atr", 0),
                "reason": setup.get("reason", ""),
                "pattern_type": setup.get("pattern_type", "none"),
                "breakout_level": setup.get("breakout_level", 0),
            })

    # Rank by score
    ranked_setups.sort(key=lambda s: s.get("score", 0), reverse=True)
    print(f"[ScalpScan] Step 3: {len(ranked_setups)} qualifying setups")

    scan_time = (datetime.now(timezone.utc) - scan_start).total_seconds()

    return {
        "shortlist": shortlist,
        "liquid_candidates": [{"symbol": c["symbol"], "liquidity": c["liquidity"]} for c in liquid],
        "ranked_setups": ranked_setups,
        "symbols": symbols_data,
        "scan_time_seconds": round(scan_time, 2),
        "scan_timestamp": scan_start.isoformat(),
        "market_regime": spy_regime_label,
        "params_used": {"timeframes": params.get("timeframes", {})},
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="ScalpRunner — 4-Step Scalp Scan")
    parser.add_argument("--token", type=str, help="Agent auth token")
    parser.add_argument("--symbol", type=str, help="Single symbol to scan (debug mode)")
    parser.add_argument("--config", type=str, help="Inline JSON config override")
    args = parser.parse_args()

    result = run_scan(token=args.token, inline_config=args.config, symbol=args.symbol)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
