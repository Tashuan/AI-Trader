#!/usr/bin/env python3
"""Re-evaluation experiment: test new backtester features in isolation and combination.

Tests:
1. Short-only baseline (new defaults off) — confirm old behavior
2. Short-only + adaptive exit only
3. Short-only + tape reading only
4. Short-only + adaptive exit (30m-tuned phases)
5. Short-only + adaptive exit (60m-tuned phases)
6. Short-only + adaptive exit + tape reading (both on, current default)
7. Short-only + daily discovery
8. Short-only + adaptive exit + daily discovery
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
sys.path.insert(0, str(AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from execution_simulator import FillConfig
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge
from backtest_discovery import make_discovery_fn

RESEARCH_DIR = REPO_ROOT / "research" / "strategy_search"

SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]
START_DATE = "2026-03-02"
END_DATE = "2026-08-11"
INTERVAL = "30m"
SLIPPAGE = 5.0
FEE_RATE = 0.001
CAPITAL = 100_000.0
TRAIN_DAYS = 14
TEST_DAYS = 14
STEP_DAYS = 7


def build_provider():
    alpaca = AlpacaProvider()
    if not alpaca.available:
        raise RuntimeError("Alpaca provider not available")
    provider = CachedProvider(alpaca)
    return provider, "cached-alpaca"


def build_fill_config():
    return FillConfig(
        slippage_bps=SLIPPAGE, fee_rate=FEE_RATE,
        enable_size_impact=True, enable_vol_widening=True,
        enable_partial_fills=True, enable_tick_rounding=True,
        market="us-stock", interval=INTERVAL,
    )


def generate_windows(start, end, train_days=TRAIN_DAYS, test_days=TEST_DAYS, step_days=STEP_DAYS):
    from datetime import datetime as dt, timedelta
    s, e = dt.fromisoformat(start), dt.fromisoformat(end)
    windows = []
    current = s
    wid = 0
    while current + timedelta(days=train_days + test_days) <= e:
        windows.append({
            "window_id": wid,
            "test_start": (current + timedelta(days=train_days)).strftime("%Y-%m-%d"),
            "test_end": (current + timedelta(days=train_days + test_days)).strftime("%Y-%m-%d"),
        })
        wid += 1
        current += timedelta(days=step_days)
    return windows


def run_candidate(cid, override, provider, label, fill_cfg,
                  discovery_mode="static", max_symbols=10):
    params = deep_merge(SCALP_DEFAULT_PARAMS, override)
    windows = generate_windows(START_DATE, END_DATE)
    if not windows:
        return {"error": "No windows", "candidate_id": cid}

    discovery_fn = make_discovery_fn(mode=discovery_mode, provider=provider,
                                     max_symbols=max_symbols, interval=INTERVAL)

    results = []
    for w in windows:
        bt = ScalpScanBacktester(
            symbols=SYMBOLS, params=params,
            start_date=w["test_start"], end_date=w["test_end"],
            initial_capital=CAPITAL, slippage_bps=SLIPPAGE,
            provider=provider, base_interval=INTERVAL,
            fill_config=fill_cfg,
            discovery_fn=discovery_fn,
        )
        report = bt.run()
        results.append({
            "window_id": w["window_id"],
            "return_pct": report.total_return_pct,
            "profit_factor": report.profit_factor,
            "total_trades": report.total_trades,
            "win_rate": report.win_rate,
            "passed": report.total_return_pct > 0 and report.profit_factor > 1.0,
        })

    returns = [r["return_pct"] for r in results]
    pfs = [r["profit_factor"] for r in results]
    trades = [r["total_trades"] for r in results]
    passed = sum(1 for r in results if r["passed"])

    return {
        "candidate_id": cid,
        "discovery": discovery_mode,
        "total_return_pct": round(sum(returns), 4),
        "pass_rate": round(passed / len(results), 4),
        "windows_passed": passed,
        "num_windows": len(results),
        "avg_pf": round(sum(pfs) / len(pfs), 4) if pfs else 0,
        "total_trades": sum(trades),
        "avg_win_rate": round(sum(r["win_rate"] for r in results) / len(results), 4),
    }


# ── Base override (short-only with cap2_spy10 filters) ──────────────
BASE = {
    "entry_criteria": {"direction_mode": "short"},
    "order": {
        "sl_atr_multiple": 1.5, "tp_atr_multiple": 2.5,
        "entry_trigger_offset_pct": 0.08, "stop_limit_offset_pct": 0.02,
        "order_expiry_minutes": 180,
    },
    "exit_rules": {
        "trailing_sl_pct": 0.4, "trailing_activation_pct": 0.5,
        "exit_mode": "set_and_forget",
    },
    "premove_filter": {
        "enabled": True, "max_move_pct": 2.0, "lookback_bars": 8,
    },
    "market_regime": {
        "enabled": True, "symbol": "SPY", "daily_ema_period": 10,
        "block_shorts_in_bull": True, "threshold_pct": 0.0,
    },
}

# ── Candidates ──────────────────────────────────────────────────────
CANDIDATES = [
    # 1. Pure baseline: disable all new features
    ("baseline_no_new", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "exit_rules": {"adaptive_exit": False},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "static"),

    # 2. Adaptive exit only (default 15/45 min phases)
    ("adaptive_exit_only", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "static"),

    # 3. Tape reading only
    ("tape_reading_only", deep_merge(BASE, {
        "exit_rules": {"adaptive_exit": False},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "static"),

    # 4. Adaptive exit with 30m-tuned phases (phase1=30, phase2=90)
    ("adaptive_exit_30m", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
        "exit_rules": {"phase1_minutes": 30, "phase2_minutes": 90},
    }), "static"),

    # 5. Adaptive exit with 60m-tuned phases (phase1=60, phase2=180)
    ("adaptive_exit_60m", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
        "exit_rules": {"phase1_minutes": 60, "phase2_minutes": 180},
    }), "static"),

    # 6. Both new features on (current default behavior)
    ("both_new_on", deep_merge(BASE, {
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "static"),

    # 7. Daily discovery
    ("daily_discovery", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "exit_rules": {"adaptive_exit": False},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "daily"),

    # 8. Adaptive exit + daily discovery
    ("adaptive_exit_daily", deep_merge(BASE, {
        "indicators": {"tape_reading": {"enabled": False}},
        "discovery": {"catalyst": {"enabled": False}},
        "market_regime": {"adaptive_direction": False},
    }), "daily"),
]


def main():
    provider, label = build_provider()
    fill_cfg = build_fill_config()

    results = []
    for cid, override, disc_mode in CANDIDATES:
        print(f"\n--- Running {cid} (discovery={disc_mode}) ---", file=sys.stderr)
        r = run_candidate(cid, override, provider, label, fill_cfg,
                          discovery_mode=disc_mode)
        results.append(r)
        print(f"  Return: {r['total_return_pct']:+.2f}% | Pass: {r['pass_rate']:.0%} | "
              f"Trades: {r['total_trades']} | AvgPF: {r['avg_pf']:.3f}", file=sys.stderr)

    results.sort(key=lambda x: x["total_return_pct"], reverse=True)

    print(f"\n{'='*80}")
    print(f"  {'Rank':<5} {'Candidate':<22} {'Return':>8} {'Pass%':>7} {'AvgPF':>7} {'Trades':>7} {'WinRate':>8}")
    print(f"  {'-'*5} {'-'*22} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*8}")
    for i, r in enumerate(results, 1):
        print(f"  {i:<5} {r['candidate_id']:<22} {r['total_return_pct']:>+7.2f}% "
              f"{r['pass_rate']:>6.0%} {r['avg_pf']:>7.3f} {r['total_trades']:>7} "
              f"{r['avg_win_rate']:>7.0%}")
    print(f"{'='*80}")

    # Save
    out = RESEARCH_DIR / f"run_reeval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with out.open("w") as f:
        json.dump({"experiment": "reeval_new_features", "results": results}, f, indent=2)
    print(f"\nSaved to: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
