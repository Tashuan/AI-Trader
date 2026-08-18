"""ORB Options Strategy Sweep Harness

Fetches 1m equity data once for the full SCANNER_UNIVERSE, then runs
all 10 test categories from the optimization plan against the same
in-memory data. Each test variant produces a result row with:
  - total_return_pct, profit_factor, win_rate, max_drawdown_pct,
  - total_trades, worst_day_pnl, avg_win, avg_loss

The baseline (locked):
  - dynamic discovery (top 8 pre-market movers from SCANNER_UNIVERSE)
  - max_positions=5
  - 2-bar close confirmation
  - 5min ORB
  - stop 1.0% / target 1.5%
  - latest entry 10:30
  - 10% equity per trade
  - 10-minute no-stop window (confirmation_minutes=10)

Usage:
  cd agents
  python3 ../research/strategy_search/orb_sweep.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
import time as time_mod
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "research" / "strategy_search"))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from scalp_alt_signals import fetch_1m_data, fetch_prev_closes
from orb_options_bs_backtester import (
    run_bs_options_backtest, IVCache, bs_price,
)
from orb_options_backtester import ORB_CONFIG, STRIKE_STEPS
from orb_strategy import IntrabarPolicy

# ── Universe ───────────────────────────────────────────────────────────

SCANNER_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "AMD", "META", "AMZN", "MSFT", "GOOGL",
    "NFLX", "INTC", "MU", "BA", "DIS", "BABA", "COIN", "MARA", "RIOT",
    "SOFI", "AAL", "UAL", "F", "GM", "NIO", "XPEV", "PLUG", "DKNG",
    "SPOT", "SNAP", "PINS", "ROKU", "ZM", "SQ", "SHOP",
]

CHEAP_VOLATILE = {"MARA", "RIOT", "PLUG", "NIO", "XPEV"}

# ── Baseline config ────────────────────────────────────────────────────

BASELINE = {
    "range_minutes": 5,
    "stop_pct": 1.0,
    "target_pct": 1.5,
    "latest_entry": "10:30",
    "max_positions": 5,
    "position_pct": 10.0,
    "confirmation_bars": 2,        # 2-bar close confirmation
    "confirmation_minutes": 10,    # 10-min no-stop window
    "circuit_breaker": 3,
    "min_range_width_pct": 0.0,
    "skip_first_post_range_bar": False,
    "range_end_policy": "inclusive",
    "strategy_mode": "symmetric_otm",
}

BASELINE_DISCOVERY = {
    "universe": SCANNER_UNIVERSE,
    "max_symbols": 8,
    "min_change_pct": 1.0,
}


def fetch_premarket_changes(symbols: list[str], start: str, end: str, provider, prev_closes: dict) -> dict:
    """Build discovery inputs from the last 04:00-09:29 ET bar only."""
    padded_start = (datetime.fromisoformat(start) - timedelta(days=3)).strftime("%Y-%m-%d")
    padded_end = (datetime.fromisoformat(end) + timedelta(days=1)).strftime("%Y-%m-%d")
    changes: dict = {}
    for symbol in symbols:
        try:
            raw = provider.history(symbol, start=padded_start, end=padded_end,
                                   interval="1m", auto_adjust=False, raise_errors=False)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        frame = raw.reset_index() if raw.index.name else raw.copy()
        col = "Datetime" if "Datetime" in frame.columns else "Date"
        frame[col] = pd.to_datetime(frame[col], utc=True).dt.tz_convert("America/New_York")
        frame = frame[(frame[col].dt.time >= dt_time(4, 0)) &
                      (frame[col].dt.time < dt_time(9, 30))]
        for day, group in frame.groupby(frame[col].dt.date):
            prev = prev_closes.get((symbol, day))
            if prev is None or prev <= 0:
                continue
            premarket_close = float(group.sort_values(col).iloc[-1]["Close"])
            changes.setdefault(day, {})[symbol] = (premarket_close - prev) / prev * 100
    return changes


def freeze_discovery_symbols(premarket_changes: dict, config: dict) -> dict:
    """Freeze scanner-ranked symbols before the trading replay begins."""
    universe = config.get("universe", SCANNER_UNIVERSE)
    max_symbols = config.get("max_symbols", 8)
    min_change = config.get("min_change_pct", 1.0)
    excluded = set(config.get("exclude_symbols", []))
    frozen = {}
    for day, changes in premarket_changes.items():
        ranked = sorted(
            ((symbol, abs(float(change))) for symbol, change in changes.items()
             if symbol in universe and symbol not in excluded
             and abs(float(change)) >= min_change),
            key=lambda item: item[1], reverse=True,
        )
        frozen[day] = [symbol for symbol, _ in ranked[:max_symbols]]
    return frozen


# ── Test definitions ───────────────────────────────────────────────────

def build_tests() -> list[dict]:
    """Build all 10 test categories. Each test is a dict with:
    - name: short label
    - category: test category number
    - config: ORB_CONFIG overrides
    - discovery: discovery_config overrides (or None for baseline)
    - description: what we're testing
    """
    tests = []

    # ── Test 1: Baseline + live 10-min no-stop window ──────────────
    tests.append({
        "name": "T1_baseline_10min",
        "category": 1,
        "config": dict(BASELINE),
        "discovery": dict(BASELINE_DISCOVERY),
        "desc": "Baseline with 10-min no-stop window (real current rules)",
    })

    # ── Test 2: Confirmation window variants ───────────────────────
    for grace in [0, 5, 10, 15]:
        cfg = dict(BASELINE)
        cfg["confirmation_minutes"] = grace
        tests.append({
            "name": f"T2_grace{grace}min",
            "category": 2,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"No-stop grace window = {grace} min",
        })

    # ── Test 3: Entry confirmation bars ────────────────────────────
    for bars in [1, 2, 3]:
        cfg = dict(BASELINE)
        cfg["confirmation_bars"] = bars
        tests.append({
            "name": f"T3_confirm{bars}bar",
            "category": 3,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"{bars}-bar consecutive close confirmation",
        })

    # ── Test 4: Max positions ──────────────────────────────────────
    for mx in [3, 5, 6, 8]:
        cfg = dict(BASELINE)
        cfg["max_positions"] = mx
        tests.append({
            "name": f"T4_maxpos{mx}",
            "category": 4,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"Max {mx} concurrent positions",
        })

    # ── Test 5: Stop / target ──────────────────────────────────────
    stop_target_grid = [
        (0.75, 1.5),  # tight R:R
        (1.0, 1.0),
        (1.0, 1.5),   # baseline
        (1.0, 2.0),
        (1.25, 2.0),  # wide R:R
    ]
    for sl, tp in stop_target_grid:
        cfg = dict(BASELINE)
        cfg["stop_pct"] = sl
        cfg["target_pct"] = tp
        tests.append({
            "name": f"T5_sl{sl}_tp{tp}",
            "category": 5,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"Stop {sl}% / Target {tp}%",
        })

    # ── Test 6: Range length ───────────────────────────────────────
    for rmin in [5, 10, 15]:
        cfg = dict(BASELINE)
        cfg["range_minutes"] = rmin
        tests.append({
            "name": f"T6_range{rmin}min",
            "category": 6,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"{rmin}-min opening range",
        })

    # ── Test 7: Latest entry cutoff ────────────────────────────────
    for cutoff in ["10:00", "10:30", "11:00"]:
        cfg = dict(BASELINE)
        cfg["latest_entry"] = cutoff
        tests.append({
            "name": f"T7_entry{cutoff}",
            "category": 7,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"Latest entry = {cutoff}",
        })

    # ── Test 8: Min range width filter ─────────────────────────────
    for minw in [0.0, 0.3, 0.5, 0.8]:
        cfg = dict(BASELINE)
        cfg["min_range_width_pct"] = minw
        tests.append({
            "name": f"T8_minwidth{minw}",
            "category": 8,
            "config": cfg,
            "discovery": dict(BASELINE_DISCOVERY),
            "desc": f"Min range width = {minw}% of price",
        })

    # ── Test 9: Discovery quality ──────────────────────────────────
    # 9a: top 6 / 8 / 10 movers
    for top_n in [6, 8, 10]:
        disc = dict(BASELINE_DISCOVERY)
        disc["max_symbols"] = top_n
        tests.append({
            "name": f"T9a_top{top_n}",
            "category": 9,
            "config": dict(BASELINE),
            "discovery": disc,
            "desc": f"Discovery: top {top_n} movers",
        })
    # 9b: min pre-market change
    for minc in [1.0, 1.5, 2.0]:
        disc = dict(BASELINE_DISCOVERY)
        disc["min_change_pct"] = minc
        tests.append({
            "name": f"T9b_minchg{minc}",
            "category": 9,
            "config": dict(BASELINE),
            "discovery": disc,
            "desc": f"Discovery: min change {minc}%",
        })
    # 9c: drop cheap/volatile names
    disc = dict(BASELINE_DISCOVERY)
    disc["exclude_symbols"] = list(CHEAP_VOLATILE)
    tests.append({
        "name": "T9c_no_cheap",
        "category": 9,
        "config": dict(BASELINE),
        "discovery": disc,
        "desc": "Discovery: exclude MARA/RIOT/PLUG/NIO/XPEV",
    })

    # ── Test 10: Skip first post-range bar ─────────────────────────
    cfg = dict(BASELINE)
    cfg["skip_first_post_range_bar"] = True
    tests.append({
        "name": "T10_skip_first_bar",
        "category": 10,
        "config": cfg,
        "discovery": dict(BASELINE_DISCOVERY),
        "desc": "Never enter on 09:35 (first post-range bar)",
    })

    return tests


# ── Result extraction ──────────────────────────────────────────────────

def extract_metrics(result: dict) -> dict:
    """Extract key metrics from a backtest result dict."""
    trades = result.get("trades", [])
    # Compute worst-day P&L
    day_pnl: dict[str, float] = {}
    for t in trades:
        if isinstance(t, dict):
            exit_date = str(t.get("exit_date", ""))[:10]
            pnl = float(t.get("pnl", 0))
        else:
            exit_date = str(t.exit_date)[:10]
            pnl = float(t.pnl)
        day_pnl[exit_date] = day_pnl.get(exit_date, 0) + pnl
    worst_day = min(day_pnl.values()) if day_pnl else 0.0
    # Avg win / avg loss
    wins = [float(t.get("pnl", 0) if isinstance(t, dict) else t.pnl) for t in trades
            if (float(t.get("pnl", 0) if isinstance(t, dict) else t.pnl)) > 0]
    losses = [float(t.get("pnl", 0) if isinstance(t, dict) else t.pnl) for t in trades
              if (float(t.get("pnl", 0) if isinstance(t, dict) else t.pnl)) <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "return_pct": result["total_return_pct"],
        "profit_factor": result["profit_factor"],
        "win_rate": result["win_rate"],
        "max_dd_pct": result["max_drawdown_pct"],
        "total_trades": result["total_trades"],
        "worst_day": worst_day,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "final_equity": result["final_equity"],
    }


# ── Main sweep runner ──────────────────────────────────────────────────

def run_sweep(start: str, end: str, quick: bool = False, output_file: str = ""):
    """Fetch data once, run all tests, print results table."""
    from orb_options_backtester import ORB_CONFIG as DEFAULT_ORB

    tests = build_tests()
    if quick:
        # Quick mode: only run baseline + one variant per category
        quick_names = {"T1_baseline_10min", "T2_grace0min", "T2_grace10min",
                       "T3_confirm1bar", "T3_confirm2bar", "T4_maxpos3",
                       "T4_maxpos5", "T5_sl1.0_tp1.5", "T5_sl0.75_tp1.5",
                       "T6_range5min", "T6_range10min", "T7_entry10:30",
                       "T7_entry11:00", "T8_minwidth0.0", "T8_minwidth0.5",
                       "T9a_top8", "T9a_top10", "T9c_no_cheap", "T10_skip_first_bar"}
        tests = [t for t in tests if t["name"] in quick_names]

    print(f"\n{'='*80}")
    print(f"  ORB Options Strategy Sweep")
    print(f"{'='*80}")
    print(f"  Date range:    {start} → {end}")
    print(f"  Tests to run:  {len(tests)}")
    print(f"  Universe:      {len(SCANNER_UNIVERSE)} symbols")
    print(f"  Baseline:      5min ORB, 2-bar confirm, 10-min no-stop, "
          f"sl1.0/tp1.5, 10% pos, max5, top-8 discovery")
    print()

    # ── Fetch data once ────────────────────────────────────────────
    print("  Fetching 1m equity data for full universe...")
    t0 = time_mod.time()
    provider = CachedProvider(AlpacaProvider())
    frames = fetch_1m_data(SCANNER_UNIVERSE, start, end, provider)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    print(f"  Fetching prev closes...")
    prev_closes = fetch_prev_closes(SCANNER_UNIVERSE, all_dates, provider)
    premarket_changes = fetch_premarket_changes(
        SCANNER_UNIVERSE, start, end, provider, prev_closes
    )
    frozen_discovery = freeze_discovery_symbols(
        premarket_changes, BASELINE_DISCOVERY
    )
    fetch_elapsed = time_mod.time() - t0
    print(f"  Data fetched in {fetch_elapsed:.1f}s "
          f"({len(frames)} symbols, {len(all_dates)} trading days)")
    print(f"  Premarket discovery inputs: {len(premarket_changes)} days")
    print(f"  Frozen scanner lists: {len(frozen_discovery)} days")
    print()

    # ── IV cache (use default 50% — Schwab is blocked) ─────────────
    iv_cache = IVCache()
    for sym in SCANNER_UNIVERSE:
        iv_cache._iv[sym] = 0.50
    print("  IV: Using default 50% for all symbols (Schwab blocked)")
    print()

    # ── Run all tests ──────────────────────────────────────────────
    results = []
    t_start = time_mod.time()

    for i, test in enumerate(tests):
        config = {**DEFAULT_ORB, **test["config"]}
        discovery = test.get("discovery")

        t0 = time_mod.time()
        result = run_bs_options_backtest(
            symbols=SCANNER_UNIVERSE if discovery else list(config.get("symbols", SCANNER_UNIVERSE)),
            frames=frames,
            prev_closes=prev_closes,
            iv_cache=iv_cache,
            capital=10000.0,
            config=config,
            start_date=start,
            end_date=end,
            strike_offset=1,
            dte_min=2,
            dte_max=14,
            confirmation_minutes=config.get("confirmation_minutes", 10),
            circuit_breaker=config.get("circuit_breaker", 3),
            min_entry_time="09:30",
            discovery_config=discovery,
            premarket_changes=premarket_changes if discovery else None,
            frozen_symbols=frozen_discovery if discovery else None,
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
            option_slippage_bps=50.0,
            option_spread_bps=100.0,
            contract_fee=0.65,
        )
        elapsed = time_mod.time() - t0
        metrics = extract_metrics(result)
        metrics["name"] = test["name"]
        metrics["category"] = test["category"]
        metrics["desc"] = test["desc"]
        metrics["elapsed"] = elapsed
        results.append(metrics)

        status = "PASS" if metrics["return_pct"] > 0 else "FAIL"
        print(f"  [{i+1}/{len(tests)}] {test['name']:25s} "
              f"ret={metrics['return_pct']:+7.2f}%  "
              f"PF={metrics['profit_factor']:.3f}  "
              f"WR={metrics['win_rate']:.0%}  "
              f"DD={metrics['max_dd_pct']:.1f}%  "
              f"trades={metrics['total_trades']:3d}  "
              f"worst_day=${metrics['worst_day']:+,.0f}  "
              f"({elapsed:.1f}s)")

    total_elapsed = time_mod.time() - t_start
    print(f"\n  Total sweep time: {total_elapsed:.1f}s")
    print()

    # ── Results table ──────────────────────────────────────────────
    print_results_table(results, tests)

    # ── Save to JSON ───────────────────────────────────────────────
    if output_file:
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Results saved to: {output_file}")

    return results


def print_results_table(results: list[dict], tests: list[dict]):
    """Print a formatted comparison table grouped by category."""
    baseline = next((r for r in results if r["category"] == 1), None)
    baseline_ret = baseline["return_pct"] if baseline else 0.0
    baseline_pf = baseline["profit_factor"] if baseline else 0.0
    baseline_wd = baseline["worst_day"] if baseline else 0.0

    print(f"{'='*120}")
    print(f"  RESULTS TABLE (baseline: ret={baseline_ret:+.2f}%, "
          f"PF={baseline_pf:.3f}, worst_day=${baseline_wd:+,.0f})")
    print(f"{'='*120}")
    print(f"  {'Name':25s} {'Return%':>8s} {'PF':>6s} {'WR':>5s} {'MaxDD%':>7s} "
          f"{'Trades':>7s} {'WorstDay$':>10s} {'AvgWin$':>8s} {'AvgLoss$':>9s}  Description")
    print(f"  {'-'*25} {'-'*8} {'-'*6} {'-'*5} {'-'*7} {'-'*7} {'-'*10} {'-'*8} {'-'*9}  {'-'*40}")

    current_cat = 0
    for r in results:
        if r["category"] != current_cat:
            current_cat = r["category"]
            print(f"  {'─'*118}")
        delta = r["return_pct"] - baseline_ret
        marker = " ★" if r["return_pct"] > baseline_ret and r["worst_day"] >= baseline_wd else ""
        print(f"  {r['name']:25s} {r['return_pct']:+8.2f} {r['profit_factor']:6.3f} "
              f"{r['win_rate']:5.0%} {r['max_dd_pct']:7.1f} {r['total_trades']:7d} "
              f"{r['worst_day']:+10.0f} {r['avg_win']:+8.0f} {r['avg_loss']:+9.0f}  "
              f"{r['desc']}{marker}")

    print(f"  {'─'*118}")
    print(f"  ★ = beats baseline on return AND worst-day (no worse drawdown)")
    print()

    # ── Winners summary ────────────────────────────────────────────
    winners = [r for r in results
               if r["category"] != 1
               and r["return_pct"] > baseline_ret
               and r["worst_day"] >= baseline_wd
               and r["total_trades"] >= 15]
    if winners:
        print(f"  WINNERS (beat baseline on return + worst-day, ≥15 trades):")
        winners.sort(key=lambda x: x["return_pct"], reverse=True)
        for w in winners[:10]:
            print(f"    {w['name']:25s} ret={w['return_pct']:+.2f}%  "
                  f"PF={w['profit_factor']:.3f}  trades={w['total_trades']}  "
                  f"worst_day=${w['worst_day']:+,.0f}")
    else:
        print(f"  No configs beat baseline on both return AND worst-day with ≥15 trades.")
    print()


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ORB Options Strategy Sweep")
    parser.add_argument("--start", default="2026-07-21",
                        help="Start date (default: 2026-07-21)")
    parser.add_argument("--end", default="2026-08-17",
                        help="End date (default: 2026-08-17)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: fewer variants per category")
    parser.add_argument("--json", type=str, default="",
                        help="Save results to JSON file")
    args = parser.parse_args()

    run_sweep(args.start, args.end, quick=args.quick, output_file=args.json)


if __name__ == "__main__":
    main()
