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
# Shared, side-effect-free indicator/scoring/exit-rule logic. This is the
# single source of truth for strategy defaults and math — both this live
# agent script and the backend backtester (agents/scan_backtester.py) import
# from here so backtests replay exactly what the live agent does.
_AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)
import scan_core
from market_data import MarketDataProvider, YFinanceProvider

_DATA_PROVIDER: MarketDataProvider = YFinanceProvider()


# ============================================================
# Default Strategy Parameters (re-exported from scan_core for
# backwards compatibility with existing imports of scan.DEFAULT_PARAMS)
# ============================================================

DEFAULT_PARAMS: dict[str, Any] = scan_core.DEFAULT_PARAMS
_CRYPTO_SYMBOLS = scan_core.CRYPTO_SYMBOLS

# Default sweep universe (top crypto by market cap + top equities + commodities)
_SWEEP_CRYPTO = ["BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK", "DOT", "LTC", "UNI", "ATOM", "NEAR", "ARB", "OP"]
_SWEEP_EQUITIES = ["NVDA", "TSLA", "AAPL", "AMZN", "META", "MSFT", "GOOGL", "AMD", "NFLX", "JPM", "BAC", "V", "DIS", "SHOP", "COIN"]
_SWEEP_COMMODITIES = ["GC=F", "SI=F", "CL=F", "SPY", "^GSPC"]


# ============================================================
# Config Loading
# ============================================================

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    return scan_core.deep_merge(base, override)


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
    return scan_core.yf_ticker(symbol)


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
            df = _DATA_PROVIDER.history(ticker, period="5d", interval="1h")
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

def _deep_scan_symbol(symbol: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch live history for `symbol` and run the shared 15-indicator deep scan.

    All indicator math, entry qualification, and composite scoring live in
    scan_core.deep_scan_symbol_from_df — this function only does the live
    yfinance I/O and hands off the resulting DataFrame.
    """
    ind_cfg = params.get("indicators", {})
    interval = ind_cfg.get("candle_interval", "1h")
    lookback = ind_cfg.get("lookback_period", "1mo")

    ticker = _yf_ticker(symbol)
    df = _DATA_PROVIDER.history(ticker, period=lookback, interval=interval)

    return scan_core.deep_scan_symbol_from_df(symbol, df, params)


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
        df = _DATA_PROVIDER.history(ticker, period="1d", interval="1m")
        if df is not None and not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return None


def _interval_seconds(interval: str) -> int:
    return {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)


def _bars_held(pos: dict[str, Any], params: dict[str, Any]) -> int:
    opened_at = pos.get("opened_at")
    if not opened_at:
        return 0
    try:
        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - opened).total_seconds()
        interval = params.get("indicators", {}).get("candle_interval", "1h")
        return max(0, int(elapsed / _interval_seconds(interval)))
    except (TypeError, ValueError):
        return 0


def _review_position(pos: dict[str, Any], params: dict[str, Any], cycles_flat: int) -> dict[str, Any]:
    """Evaluate the 6 exit rules using the live indicator snapshot."""
    symbol = pos.get("symbol", "")
    entry_price = float(pos.get("entry_price", 0))
    current_price = float(pos.get("current_price", 0)) or _fetch_current_price(symbol) or entry_price
    pos = {**pos, "current_price": current_price}

    ind_data = _deep_scan_symbol(symbol, params)
    return scan_core.review_position_from_indicators(
        pos, params, cycles_flat, ind_data, _bars_held(pos, params)
    )


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
# Auto-Execution Helpers (for --auto-exit / --auto-enter)
# ============================================================

_CRYPTO_SYMBOLS_SET = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "ADA", "LINK", "DOT", "LTC",
                       "UNI", "ATOM", "NEAR", "ARB", "OP"}


def _classify_market(symbol: str) -> str:
    """Classify symbol into market type for the API."""
    if symbol.upper() in _CRYPTO_SYMBOLS_SET:
        return "crypto"
    return "us-stock"


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
        return {"error": f"HTTP {e.code}: {e.reason}"}
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
) -> dict[str, Any]:
    """Run the full scan and return JSON-serializable dict.

    When auto_exit=True, any position with verdict "EXIT" is automatically
    closed via POST /api/signals/realtime — no LLM judgment required.

    When auto_enter=True, the top-ranked qualifying setup is automatically
    entered via POST /api/signals/realtime with ATR-based SL/TP and trailing
    stop params — no LLM judgment required.
    """
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

    # ── Auto-exit: close positions with verdict EXIT ──────────────
    auto_exit_results: list[dict] = []
    if auto_exit and token:
        for pos_review in positions_output:
            if pos_review.get("verdict") == "EXIT":
                symbol = pos_review["symbol"]
                side = pos_review.get("side", "long")
                exit_reason = pos_review.get("exit_reason", "exit_rule")
                qty = abs(float(pos_review.get("quantity", 0)))
                if qty <= 0:
                    continue
                market = _classify_market(symbol)
                action = "sell" if side == "long" else "cover"
                result = _api_trade(token, action, symbol, qty, market,
                                   content=f"[Auto-Exit] {exit_reason}")
                auto_exit_results.append({
                    "symbol": symbol,
                    "action": action,
                    "reason": exit_reason,
                    "result": result,
                })

    # ── Auto-enter: enter top-ranked setup ────────────────────────
    auto_enter_result: Optional[dict] = None
    if auto_enter and token and not max_positions_reached and ranked:
        # Check goal status before auto-entering
        can_trade = True
        try:
            goal_url = "http://localhost:8000/api/claw/agents/me/goal"
            goal_req = urllib.request.Request(goal_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(goal_req, timeout=10) as resp:
                goal_data = json.loads(resp.read())
                can_trade = goal_data.get("can_trade", True)
        except Exception:
            pass

        if can_trade:
            best = ranked[0]
            best_sym = best["symbol"]
            # Skip if already holding this symbol
            held_symbols = {p.get("symbol", "").upper() for p in positions_output}
            if best_sym.upper() not in held_symbols:
                sym_data = symbols_output.get(best_sym, {})
                if sym_data and not sym_data.get("error"):
                    entry_price = sym_data.get("price", 0)
                    side = best.get("direction", "long")
                    market = _classify_market(best_sym)

                    # Compute ATR-based SL/TP
                    indicators = sym_data.get("indicators", {})
                    atr = indicators.get("atr_14", 0)
                    if atr <= 0:
                        atr = entry_price * 0.02

                    sl_dist = 1.5 * atr
                    tp_dist = 3.0 * atr
                    if side == "long":
                        sl_price = entry_price - sl_dist
                        tp_price = entry_price + tp_dist
                    else:
                        sl_price = entry_price + sl_dist
                        tp_price = entry_price - tp_dist

                    trail_sl = params.get("exit_rules", {}).get("trailing_sl_pct", 1.0)
                    trail_act = params.get("exit_rules", {}).get("trailing_activation_pct", 1.0)

                    # Goal-aware sizing
                    ps_cfg = params.get("position_sizing", {})
                    progress = 0.0
                    try:
                        portfolio_url = "http://localhost:8000/api/positions"
                        port_req = urllib.request.Request(portfolio_url, headers={"Authorization": f"Bearer {token}"})
                        with urllib.request.urlopen(port_req, timeout=10) as port_resp:
                            port_data = json.loads(port_resp.read())
                            cash = float(port_data.get("cash", 100000.0))
                            equity = cash
                            for p in port_data.get("positions", []):
                                pq = abs(float(p.get("quantity", 0)))
                                pp = float(p.get("current_price", 0)) or float(p.get("entry_price", 0))
                                ps = p.get("side", "long")
                                if ps == "long":
                                    equity += pq * pp
                                else:
                                    equity -= pq * pp
                            if equity > 100000.0:
                                progress = ((equity - 100000.0) / 1000.0) * 100
                    except Exception:
                        equity = 100000.0

                    is_final = progress > 80.0
                    if is_final:
                        lo = ps_cfg.get("approaching_sizing_min_pct", 15)
                        hi = ps_cfg.get("approaching_sizing_max_pct", 25)
                    else:
                        lo = ps_cfg.get("normal_sizing_min_pct", 25)
                        hi = ps_cfg.get("normal_sizing_max_pct", 40)
                    size_pct = (lo + hi) / 2.0

                    notional = equity * (size_pct / 100.0)
                    max_dollar = ps_cfg.get("max_position_dollar_cap")
                    if max_dollar and notional > max_dollar:
                        notional = max_dollar

                    qty = notional / entry_price if entry_price > 0 else 0
                    if qty > 0:
                        action = "buy" if side == "long" else "short"
                        result = _api_trade(
                            token, action, best_sym, qty, market,
                            stop_loss_price=round(sl_price, 6),
                            take_profit_price=round(tp_price, 6),
                            trailing_sl_pct=trail_sl,
                            trailing_activation_pct=trail_act,
                            content=f"[Auto-Enter] {side} {best_sym} score={best.get('score', 0):.1f} size={size_pct:.1f}%",
                        )
                        auto_enter_result = {
                            "symbol": best_sym,
                            "action": action,
                            "result": result,
                        }

    return {
        "scan_time": datetime.now(timezone.utc).isoformat() + "Z",
        "symbols": symbols_output,
        "ranked_setups": ranked,
        "positions": positions_output,
        "daily_pnl": daily_pnl,
        "open_position_count": open_position_count,
        "max_positions_reached": max_positions_reached,
        "auto_exit_results": auto_exit_results if auto_exit else None,
        "auto_enter_result": auto_enter_result if auto_enter else None,
    }


def main():
    parser = argparse.ArgumentParser(description="BlitzTrader deterministic TA scan")
    parser.add_argument("--token", help="Agent auth token (enables position review + API config)")
    parser.add_argument("--config", help="Inline JSON config override")
    parser.add_argument("--symbol", help="Single symbol debug mode")
    parser.add_argument("--backtest", action="store_true", help="Backtest mode (historical replay)")
    parser.add_argument("--from", dest="from_date", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--auto-exit", action="store_true", help="Automatically close positions with EXIT verdict")
    parser.add_argument("--auto-enter", action="store_true", help="Automatically enter top-ranked setup")
    args = parser.parse_args()

    if args.backtest:
        print(json.dumps({"error": "Backtest mode not yet implemented"}, indent=2))
        sys.exit(0)

    result = run_scan(
        token=args.token,
        inline_config=args.config,
        single_symbol=args.symbol,
        auto_exit=args.auto_exit,
        auto_enter=args.auto_enter,
    )
    print(json.dumps(result, indent=2, default=lambda o: bool(o) if isinstance(o, (np.bool_,)) else float(o) if isinstance(o, (np.floating,)) else int(o) if isinstance(o, (np.integer,)) else str(o)))


if __name__ == "__main__":
    main()
