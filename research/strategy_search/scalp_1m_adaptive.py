"""ScalpScan 1m adaptive-direction backtest — the test that was never run.

Prior ScalpScan batches only tested:
  - Short-only direction on 30m bars in a bull market
  - Static 5-10 symbols
  - 1.5% target / 1.0% stop (large targets)

This script tests what the system was actually designed for:
  - 1m entry timeframe (resampled to 5m/15m for pattern/trend)
  - Adaptive direction (long in bull regime, short in bear, both in neutral)
  - Small ATR multiples for tight scalping targets
  - Zero-commission fills (realistic for modern US equity brokers)
  - Multiple concurrent positions

Usage:
  cd agents
  python3 ../research/strategy_search/scalp_1m_adaptive.py
  python3 ../research/strategy_search/scalp_1m_adaptive.py --json results.json
  python3 ../research/strategy_search/scalp_1m_adaptive.py --symbols NVDA,TSLA --start 2026-07-01
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from execution_simulator import FillConfig
from scalp_scan_backtester import ScalpScanBacktester
from scalp_scan_core import SCALP_DEFAULT_PARAMS
from strategy_registry import deep_merge


# ── Symbols ────────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL", "AMD", "META"]

# ── Date range ─────────────────────────────────────────────────────────
# ~2 months of 1m data. Alpaca paginates at 10k bars/page (~25 trading days).
# 2 months = ~42 trading days = ~16k bars per symbol = 2 pages each.
DEFAULT_START = "2026-06-15"
DEFAULT_END = "2026-08-16"


# ── Risk controls (not in SCALP_DEFAULT_PARAMS, added per-experiment) ──
RISK_CONTROLS = {
    "sizing_mode": "notional",
    "paper_account_budget": 100_000.0,
    "risk_per_trade_pct": 1.0,
    "max_trade_notional_pct": 50.0,
    "max_open_risk_pct": 5.0,
    "max_gross_exposure_pct": 100.0,
    "max_position_dollar_cap": None,
    "daily_loss_halt_pct": 5.0,
    "loss_streak_size_cut_pct": 50.0,
}


def build_candidates() -> dict[str, dict[str, Any]]:
    """Build the experiment matrix for 1m adaptive scalping.

    Each candidate overrides SCALP_DEFAULT_PARAMS via deep_merge.
    All candidates use:
      - base_interval="1m" (passed to backtester, not in params)
      - direction_mode="adaptive" (default, uses SPY regime)
      - risk_controls with notional sizing
      - Zero-commission fills (fee_rate=0.0)
    """
    base = {
        "risk_controls": RISK_CONTROLS,
        "position_sizing": {
            "max_positions": 3,
            "max_pending_orders": 5,
            "normal_sizing_min_pct": 20,
            "normal_sizing_max_pct": 30,
        },
    }

    return {
        # 1. Baseline 1m adaptive — default ATR multiples (sl=1.5, tp=2.5)
        "baseline_1m": deep_merge(base, {
            "order": {"order_expiry_minutes": 30},
        }),

        # 2. Tight ATR — smaller targets/stops for scalping
        "tight_atr": deep_merge(base, {
            "order": {
                "sl_atr_multiple": 1.0,
                "tp_atr_multiple": 1.5,
                "order_expiry_minutes": 30,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.3,
                "trailing_activation_pct": 0.4,
            },
        }),

        # 3. Very tight ATR — micro scalping
        "very_tight_atr": deep_merge(base, {
            "order": {
                "sl_atr_multiple": 0.5,
                "tp_atr_multiple": 1.0,
                "order_expiry_minutes": 15,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.2,
                "trailing_activation_pct": 0.3,
                "stagnation_minutes": 5,
            },
        }),

        # 4. Frequent entries — lower thresholds for more trades
        "frequent": deep_merge(base, {
            "entry_criteria": {
                "min_signals": 2,
                "min_signal_families": 1,
                "min_vol_ratio": 1.2,
            },
            "order": {
                "sl_atr_multiple": 1.0,
                "tp_atr_multiple": 1.5,
                "order_expiry_minutes": 30,
            },
        }),

        # 5. Tight + frequent — combine small targets with more entries
        "tight_frequent": deep_merge(base, {
            "entry_criteria": {
                "min_signals": 2,
                "min_signal_families": 1,
                "min_vol_ratio": 1.2,
            },
            "order": {
                "sl_atr_multiple": 1.0,
                "tp_atr_multiple": 1.5,
                "order_expiry_minutes": 20,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.3,
                "trailing_activation_pct": 0.4,
                "stagnation_minutes": 8,
            },
        }),

        # 6. Long-only (bull market bias) — adaptive but force longs
        "long_only_1m": deep_merge(base, {
            "entry_criteria": {"direction_mode": "long"},
            "order": {
                "sl_atr_multiple": 1.0,
                "tp_atr_multiple": 1.5,
                "order_expiry_minutes": 30,
            },
            "exit_rules": {
                "trailing_sl_pct": 0.3,
                "trailing_activation_pct": 0.4,
            },
        }),
    }


def build_fill_config(interval: str, slippage_bps: float, fee_rate: float) -> FillConfig:
    return FillConfig(
        slippage_bps=slippage_bps,
        fee_rate=fee_rate,
        enable_size_impact=True,
        enable_vol_widening=True,
        enable_partial_fills=True,
        enable_tick_rounding=True,
        enable_quote_side_pricing=True,
        market="us-stock",
        interval=interval,
    )


def run_experiment(
    candidate_id: str,
    override: dict[str, Any],
    symbols: list[str],
    start: str,
    end: str,
    provider,
    capital: float,
    slippage_bps: float,
    fee_rate: float,
) -> dict[str, Any]:
    """Run a single backtest configuration."""
    params = deep_merge(SCALP_DEFAULT_PARAMS, override)
    fill_cfg = build_fill_config("1m", slippage_bps, fee_rate)

    bt = ScalpScanBacktester(
        symbols=symbols,
        params=params,
        start_date=start,
        end_date=end,
        initial_capital=capital,
        slippage_bps=slippage_bps,
        provider=provider,
        base_interval="1m",
        fill_config=fill_cfg,
    )

    t0 = time.time()
    report = bt.run()
    elapsed = time.time() - t0

    report_dict = report.to_dict()
    diag = report_dict.get("diagnostics", {})

    return {
        "candidate_id": candidate_id,
        "return_pct": report_dict["total_return_pct"],
        "profit_factor": report_dict["profit_factor"],
        "win_rate": report_dict["win_rate"],
        "total_trades": report_dict["total_trades"],
        "winning_trades": report_dict["winning_trades"],
        "losing_trades": report_dict["losing_trades"],
        "max_drawdown_pct": report_dict["max_drawdown_pct"],
        "sharpe_ratio": report_dict["sharpe_ratio"],
        "avg_hold_hours": report_dict["avg_hold_hours"],
        "final_equity": report_dict["final_equity"],
        "per_symbol": report_dict.get("per_symbol_stats", {}),
        "diagnostics": {
            "scan_bars": diag.get("scan_bars", 0),
            "mtf_qualified": diag.get("mtf_qualified", 0),
            "setup_qualified": diag.get("setup_qualified", 0),
            "orders_placed": diag.get("orders_placed", 0),
            "orders_filled": diag.get("orders_filled", 0),
            "orders_expired": diag.get("orders_expired", 0),
            "same_bar_exit_skipped": diag.get("same_bar_exit_skipped", 0),
            "ambiguous_bars": diag.get("ambiguous_bars", 0),
            "exit_counts": diag.get("exit_counts", {}),
        },
        "elapsed_seconds": round(elapsed, 1),
        "trades": report_dict.get("trades", []),
    }


def print_summary(result: dict[str, Any]) -> None:
    cid = result["candidate_id"]
    ret = result["return_pct"]
    pf = result["profit_factor"]
    wr = result["win_rate"]
    trades = result["total_trades"]
    dd = result["max_drawdown_pct"]
    sharpe = result["sharpe_ratio"]
    hold_h = result["avg_hold_hours"]
    diag = result["diagnostics"]

    status = "PASS" if ret > 0 and pf > 1.0 else "FAIL"
    print(f"\n{'='*70}")
    print(f"  {cid}  [{status}]")
    print(f"{'='*70}")
    print(f"  Return:      {ret:+.2f}%")
    print(f"  Profit Factor: {pf:.3f}")
    print(f"  Win Rate:    {wr:.1%}  ({trades} trades)")
    print(f"  Max DD:      {dd:.2f}%")
    print(f"  Sharpe:      {sharpe:.3f}")
    print(f"  Avg Hold:    {hold_h:.1f}h")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Runtime:     {result['elapsed_seconds']:.1f}s")
    print(f"  --- Diagnostics ---")
    print(f"  Bars scanned:    {diag['scan_bars']:,}")
    print(f"  MTF qualified:   {diag['mtf_qualified']:,}")
    print(f"  Setup qualified: {diag['setup_qualified']:,}")
    print(f"  Orders placed:   {diag['orders_placed']:,}")
    print(f"  Orders filled:   {diag['orders_filled']:,}")
    print(f"  Orders expired:  {diag['orders_expired']:,}")
    print(f"  Exit reasons:    {diag['exit_counts']}")

    # Per-symbol breakdown
    ps = result.get("per_symbol", {})
    if ps:
        print(f"  --- Per-Symbol ---")
        for sym, stats in sorted(ps.items()):
            print(f"    {sym:6s}: {stats['trades']:3d} trades, "
                  f"WR={stats['win_rate']:.0%}, "
                  f"PnL=${stats['total_pnl']:+.2f}, "
                  f"avg={stats['avg_pnl_pct']:+.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ScalpScan 1m adaptive-direction backtest"
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--slippage", type=float, default=2.0,
                        help="Slippage in bps (default: 2 = 0.02%)")
    parser.add_argument("--fee-rate", type=float, default=0.0,
                        help="Fee rate (default: 0.0 = zero commission)")
    parser.add_argument("--json", default="", help="Save full results to JSON")
    parser.add_argument("--candidate", default="", help="Run single candidate by ID")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"\nScalpScan 1m Adaptive Backtest")
    print(f"  Symbols:    {', '.join(symbols)}")
    print(f"  Date range: {args.start} → {args.end}")
    print(f"  Capital:    ${args.capital:,.0f}")
    print(f"  Slippage:   {args.slippage} bps")
    print(f"  Fee rate:   {args.fee_rate}")

    # Provider setup
    alpaca = AlpacaProvider()
    if not alpaca.available:
        print("ERROR: Alpaca API keys not configured. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env")
        sys.exit(1)
    provider = CachedProvider(alpaca)
    print(f"  Provider:   CachedProvider(AlpacaProvider)")

    candidates = build_candidates()
    if args.candidate:
        if args.candidate not in candidates:
            print(f"ERROR: Unknown candidate '{args.candidate}'. Available: {list(candidates.keys())}")
            sys.exit(1)
        candidates = {args.candidate: candidates[args.candidate]}

    results = []
    for cid, override in candidates.items():
        print(f"\n>>> Running {cid}...")
        result = run_experiment(
            candidate_id=cid,
            override=override,
            symbols=symbols,
            start=args.start,
            end=args.end,
            provider=provider,
            capital=args.capital,
            slippage_bps=args.slippage,
            fee_rate=args.fee_rate,
        )
        print_summary(result)
        results.append(result)

    # Final ranking
    print(f"\n{'='*70}")
    print(f"  FINAL RANKING (by return)")
    print(f"{'='*70}")
    ranked = sorted(results, key=lambda r: r["return_pct"], reverse=True)
    for i, r in enumerate(ranked, 1):
        status = "PASS" if r["return_pct"] > 0 and r["profit_factor"] > 1.0 else "FAIL"
        print(f"  {i}. {r['candidate_id']:20s} | "
              f"ret={r['return_pct']:+7.2f}% | "
              f"PF={r['profit_factor']:5.3f} | "
              f"WR={r['win_rate']:.0%} | "
              f"trades={r['total_trades']:4d} | "
              f"DD={r['max_drawdown_pct']:5.2f}% | "
              f"{status}")

    if args.json:
        output = {
            "config": {
                "symbols": symbols,
                "start": args.start,
                "end": args.end,
                "capital": args.capital,
                "slippage_bps": args.slippage,
                "fee_rate": args.fee_rate,
                "base_interval": "1m",
                "direction_mode": "adaptive",
            },
            "ranking": [
                {
                    "rank": i + 1,
                    "candidate_id": r["candidate_id"],
                    "return_pct": r["return_pct"],
                    "profit_factor": r["profit_factor"],
                    "win_rate": r["win_rate"],
                    "total_trades": r["total_trades"],
                    "max_drawdown_pct": r["max_drawdown_pct"],
                    "sharpe_ratio": r["sharpe_ratio"],
                    "avg_hold_hours": r["avg_hold_hours"],
                }
                for i, r in enumerate(ranked)
            ],
            "results": results,
        }
        with open(args.json, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nFull results saved to: {args.json}")


if __name__ == "__main__":
    main()
