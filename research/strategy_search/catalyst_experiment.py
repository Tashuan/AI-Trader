#!/usr/bin/env python3
"""Catalyst scoring experiment: use Finnhub historical news + catalyst_tagger
to boost/penalize setups based on news catalysts.

Pre-fetches news for all symbols over the backtest period, classifies with
catalyst_tagger, and runs backtests with catalyst scoring enabled.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
sys.path.insert(0, str(AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from data_cache import CacheOnlyProvider
from execution_simulator import FillConfig
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge
from catalyst_tagger import tag_headline, get_catalyst_bias

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"
CACHE_DIR = RESEARCH_DIR / "catalyst_cache"

SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
START = "2026-03-02"
END = "2026-08-11"
INTERVAL = "30m"


def fetch_finnhub_news(symbol: str, date_from: str, date_to: str) -> list[dict]:
    """Fetch company news from Finnhub for a date range."""
    import requests
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        return []
    resp = requests.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": symbol, "from": date_from, "to": date_to, "token": key},
        timeout=15,
    )
    if not resp.ok:
        return []
    items = resp.json()
    if not isinstance(items, list):
        return []
    return items


def precompute_catalyst_cache():
    """Pre-fetch and cache catalyst tags for all symbols over the backtest period."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "catalyst_tags.json"

    if cache_file.exists():
        with cache_file.open() as f:
            return json.load(f)

    # Fetch news in monthly chunks to stay within rate limits
    start_dt = datetime.fromisoformat(START)
    end_dt = datetime.fromisoformat(END)

    # catalyst_index: {symbol: {date_str: {"bias": ..., "score": ..., "categories": [...]}}}
    catalyst_index: dict[str, dict[str, dict]] = defaultdict(dict)

    for sym in SYMBOLS:
        print(f"Fetching news for {sym}...", flush=True)
        current = start_dt
        while current < end_dt:
            chunk_end = min(current + timedelta(days=30), end_dt)
            date_from = current.strftime("%Y-%m-%d")
            date_to = chunk_end.strftime("%Y-%m-%d")

            items = fetch_finnhub_news(sym, date_from, date_to)
            for item in items:
                headline = item.get("headline", "")
                ts = item.get("datetime", 0)
                if ts:
                    # Finnhub datetime is unix timestamp
                    item_date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                else:
                    continue

                tag = tag_headline(headline, str(ts))
                if tag is None:
                    continue

                # Store tag for this symbol+date
                if item_date not in catalyst_index[sym]:
                    catalyst_index[sym][item_date] = {
                        "tags": [],
                    }
                catalyst_index[sym][item_date]["tags"].append({
                    "category": tag.category,
                    "bias": tag.bias,
                    "confidence": tag.confidence,
                    "headline": headline[:100],
                })

            current = chunk_end
            time.sleep(0.5)  # Rate limit safety

    # Aggregate tags per symbol+date into a single bias
    for sym, dates in catalyst_index.items():
        for date_str, data in dates.items():
            tags = data.get("tags", [])
            # Simple aggregation: count bullish vs bearish
            bullish = sum(1 for t in tags if t["bias"] == "bullish")
            bearish = sum(1 for t in tags if t["bias"] == "bearish")
            if bullish > bearish and bullish > 0:
                bias = "bullish"
            elif bearish > bullish and bearish > 0:
                bias = "bearish"
            else:
                bias = "neutral"
            data["bias"] = bias
            data["bullish_count"] = bullish
            data["bearish_count"] = bearish
            data["total_tags"] = len(tags)

    # Save cache
    result = {sym: dict(dates) for sym, dates in catalyst_index.items()}
    with cache_file.open("w") as f:
        json.dump(result, f, indent=2)
    print(f"Catalyst cache saved: {cache_file}", flush=True)
    return result


def make_catalyst_fn(catalyst_index: dict):
    """Build a catalyst_fn callback for the backtester."""
    def catalyst_fn(symbol: str, date: str) -> dict | None:
        sym_data = catalyst_index.get(symbol, {})
        date_data = sym_data.get(date)
        if date_data is None:
            return None
        return {
            "bias": date_data.get("bias", "neutral"),
            "score": date_data.get("bullish_count", 0) - date_data.get("bearish_count", 0),
            "categories": [],
        }
    return catalyst_fn


def gen_windows(start, end, td=14, tst=14, sd=7):
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    ws = []
    c = s
    wid = 0
    while c + timedelta(days=td + tst) <= e:
        ws.append({"id": wid, "ts": (c + timedelta(days=td)).strftime("%Y-%m-%d"),
                   "te": (c + timedelta(days=td + tst)).strftime("%Y-%m-%d")})
        wid += 1
        c += timedelta(days=sd)
    return ws


def main():
    # Step 1: Pre-compute catalyst cache
    print("Step 1: Pre-computing catalyst cache from Finnhub news...", flush=True)
    catalyst_index = precompute_catalyst_cache()

    # Print summary
    total_dates = sum(len(dates) for dates in catalyst_index.values())
    bullish_dates = sum(1 for sym_dates in catalyst_index.values()
                        for d in sym_dates.values() if d.get("bias") == "bullish")
    bearish_dates = sum(1 for sym_dates in catalyst_index.values()
                        for d in sym_dates.values() if d.get("bias") == "bearish")
    print(f"  Total symbol-dates with catalysts: {total_dates}", flush=True)
    print(f"  Bullish: {bullish_dates} | Bearish: {bearish_dates}", flush=True)

    # Step 2: Run backtests with and without catalyst scoring
    print("\nStep 2: Running backtests...", flush=True)
    provider = CacheOnlyProvider()
    fill_cfg = FillConfig(slippage_bps=5.0, fee_rate=0.001, enable_size_impact=True,
                          enable_vol_widening=True, enable_partial_fills=True,
                          enable_tick_rounding=True, market="us-stock", interval=INTERVAL)
    windows = gen_windows(START, END)

    BASE = {
        "entry_criteria": {"direction_mode": "short"},
        "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5,
                  "entry_trigger_offset_pct": 0.08, "stop_limit_offset_pct": 0.02,
                  "order_expiry_minutes": 180},
        "exit_rules": {"trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5,
                       "exit_mode": "set_and_forget"},
        "premove_filter": {"enabled": True, "max_move_pct": 2.0, "lookback_bars": 8},
        "market_regime": {"enabled": True, "symbol": "SPY", "daily_ema_period": 10,
                          "block_shorts_in_bull": True, "threshold_pct": 0.0,
                          "adaptive_direction": False},
        "discovery": {"catalyst": {"enabled": False}},
    }

    cat_fn = make_catalyst_fn(catalyst_index)

    configs = [
        # Baseline: no catalyst
        ("no_catalyst", deep_merge(BASE, {
            "discovery": {"catalyst": {"enabled": False}},
        }), None),
        # Catalyst on, default params
        ("catalyst_default", deep_merge(BASE, {
            "discovery": {"catalyst": {"enabled": True, "bullish_boost": 1.5,
                                       "bearish_penalty": 0.5, "no_catalyst_penalty": 0.9,
                                       "block_bearish_catalyst": False}},
        }), cat_fn),
        # Catalyst on, block bearish catalyst
        ("catalyst_block_bearish", deep_merge(BASE, {
            "discovery": {"catalyst": {"enabled": True, "bullish_boost": 1.5,
                                       "bearish_penalty": 0.5, "no_catalyst_penalty": 0.9,
                                       "block_bearish_catalyst": True}},
        }), cat_fn),
        # Catalyst on, stronger no-catalyst penalty (only trade with catalysts)
        ("catalyst_strong_filter", deep_merge(BASE, {
            "discovery": {"catalyst": {"enabled": True, "bullish_boost": 2.0,
                                       "bearish_penalty": 0.3, "no_catalyst_penalty": 0.5,
                                       "block_bearish_catalyst": True}},
        }), cat_fn),
        # Catalyst on, wider TP + block bearish
        ("catalyst_wide_tp", deep_merge(BASE, {
            "order": {"sl_atr_multiple": 1.5, "tp_atr_multiple": 4.0,
                      "entry_trigger_offset_pct": 0.08, "stop_limit_offset_pct": 0.02,
                      "order_expiry_minutes": 180},
            "discovery": {"catalyst": {"enabled": True, "bullish_boost": 1.5,
                                       "bearish_penalty": 0.5, "no_catalyst_penalty": 0.9,
                                       "block_bearish_catalyst": True}},
        }), cat_fn),
    ]

    results = []
    for cid, override, cfn in configs:
        print(f"  Running {cid}...", flush=True)
        params = deep_merge(SCALP_DEFAULT_PARAMS, override)
        window_results = []
        for w in windows:
            bt = ScalpScanBacktester(
                symbols=SYMBOLS, params=params,
                start_date=w["ts"], end_date=w["te"],
                initial_capital=100_000, slippage_bps=5.0,
                provider=provider, base_interval=INTERVAL,
                fill_config=fill_cfg, catalyst_fn=cfn,
            )
            r = bt.run()
            window_results.append({
                "ret": r.total_return_pct, "pf": r.profit_factor,
                "trades": r.total_trades, "wr": r.win_rate,
                "pass": r.total_return_pct > 0 and r.profit_factor > 1.0,
            })
        rets = [r["ret"] for r in window_results]
        pfs = [r["pf"] for r in window_results]
        trs = [r["trades"] for r in window_results]
        passed = sum(1 for r in window_results if r["pass"])
        result = {
            "cid": cid, "ret": round(sum(rets), 4),
            "pass": round(passed / len(window_results), 4),
            "passed": passed, "n": len(window_results),
            "pf": round(sum(pfs) / len(pfs), 4) if pfs else 0,
            "trades": sum(trs),
            "wr": round(sum(r["wr"] for r in window_results) / len(window_results), 4),
        }
        results.append(result)
        print(f"    {cid}: ret={result['ret']:+.2f}% pass={result['pass']:.0%} "
              f"({result['passed']}/{result['n']}) trades={result['trades']} "
              f"pf={result['pf']:.3f} wr={result['wr']:.0%}", flush=True)

    results.sort(key=lambda x: x["ret"], reverse=True)
    print(f"\n{'Rank':<5} {'Candidate':<25} {'Return':>8} {'Pass%':>7} {'AvgPF':>7} {'Trades':>7} {'WR':>5}")
    print(f"{'-'*5} {'-'*25} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*5}")
    for i, r in enumerate(results, 1):
        print(f"{i:<5} {r['cid']:<25} {r['ret']:>+7.2f}% {r['pass']:>6.0%} "
              f"{r['pf']:>7.3f} {r['trades']:>7} {r['wr']:>4.0%}")

    out = RESEARCH_DIR / f"run_catalyst_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with out.open("w") as f:
        json.dump({"catalyst_summary": {"total_dates": total_dates,
                                         "bullish": bullish_dates,
                                         "bearish": bearish_dates},
                    "results": results}, f, indent=2)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
