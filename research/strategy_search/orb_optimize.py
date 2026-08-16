"""ORB (Opening Range Breakout) parameter optimization sweep.

ORB is the only alternative signal that showed gross edge (+2.59% at zero cost).
This script sweeps parameters to find the optimal configuration:

  - Range period: 5min, 10min, 15min, 30min
  - Min range size: 0.1%, 0.2%, 0.3%, 0.4%
  - Stop distance: 0.3%, 0.4%, 0.6%, 0.8%
  - Target distance: 0.4%, 0.6%, 0.8%, 1.0%, 1.2%
  - Latest entry time: 10:00, 10:30, 11:00
  - Symbol subsets: all 5, top 3 (AAPL+NVDA+TSLA), TSLA only

Usage:
  cd agents
  python3 ../research/strategy_search/orb_optimize.py
  python3 ../research/strategy_search/orb_optimize.py --zero-cost
  python3 ../research/strategy_search/orb_optimize.py --json results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import time as dt_time
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _PROJECT_ROOT / "agents"
sys.path.insert(0, str(_AGENTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT / "research" / "strategy_search"))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import pandas as pd
from data_cache import CachedProvider
from equity_data_providers import AlpacaProvider
from backtest_report import BacktestReport, TradeRecord
from scalp_alt_signals import (
    ORBStrategy, Signal, Position, apply_slippage,
    fetch_1m_data, fetch_prev_closes,
    DEFAULT_SYMBOLS, DEFAULT_START, DEFAULT_END, SLIPPAGE_BPS,
)


# ── ORB Strategy with configurable range period ───────────────────────
class ORBConfigurableStrategy(ORBStrategy):
    """ORB with configurable opening range period and latest entry time."""

    name = "orb_configurable"

    def __init__(self, symbol: str, config: dict):
        super().__init__(symbol, config)
        self.range_end_time = dt_time(
            9, 30 + config.get("range_minutes", 15)
        ) if config.get("range_minutes", 15) < 30 else dt_time(
            10, config.get("range_minutes", 15) - 30
        )
        self.latest_entry = dt_time(*map(int, config.get("latest_entry", "11:00").split(":")))

    def on_bar(self, ts, bar, idx, day_bars):
        if ts.time() < dt_time(9, 30):
            return None
        # Build opening range
        if ts.time() <= self.range_end_time:
            high = float(bar["High"])
            low = float(bar["Low"])
            if self.range_high is None:
                self.range_high = high
                self.range_low = low
            else:
                self.range_high = max(self.range_high, high)
                self.range_low = min(self.range_low, low)
            return None
        if self.entered:
            return None
        if ts.time() > self.latest_entry:
            return None
        if self.range_high is None or self.range_low is None:
            return None
        close = float(bar["Close"])
        range_size_pct = (self.range_high - self.range_low) / self.range_low * 100
        if range_size_pct < self.config.get("min_range_pct", 0.1):
            return None
        if close > self.range_high:
            side = "long"
            entry = close
            stop_dist = entry * self.config.get("stop_pct", 0.4) / 100
            stop = entry - stop_dist
            target = entry + entry * self.config.get("target_pct", 0.6) / 100
        elif close < self.range_low:
            side = "short"
            entry = close
            stop_dist = entry * self.config.get("stop_pct", 0.4) / 100
            stop = entry + stop_dist
            target = entry - entry * self.config.get("target_pct", 0.6) / 100
        else:
            return None
        self.entered = True
        return Signal(self.symbol, side, entry, stop, target, str(ts),
                      f"orb_range_{range_size_pct:.2f}%")


# ── Sweep Grid ─────────────────────────────────────────────────────────
def build_grid() -> list[dict]:
    """Build the parameter sweep grid."""
    configs = []
    for range_min in [5, 10, 15, 30]:
        for min_range in [0.1, 0.2, 0.3]:
            for stop_pct in [0.3, 0.4, 0.6, 0.8]:
                for target_pct in [0.4, 0.6, 0.8, 1.0]:
                    # Skip configs where target < stop (negative RR)
                    if target_pct < stop_pct:
                        continue
                    for latest in ["10:30", "11:00"]:
                        configs.append({
                            "range_minutes": range_min,
                            "min_range_pct": min_range,
                            "stop_pct": stop_pct,
                            "target_pct": target_pct,
                            "latest_entry": latest,
                            "max_positions": 3,
                            "position_pct": 30.0,
                        })
    return configs


# ── Backtest Runner (reused from scalp_alt_signals) ────────────────────
def run_single(
    config: dict,
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    prev_closes: dict,
    capital: float,
    slippage_bps: float,
    fee_rate: float,
) -> dict[str, Any]:
    """Run a single ORB config."""
    max_positions = config.get("max_positions", 3)
    position_pct = config.get("position_pct", 30.0)

    ts_to_idx: dict[str, dict] = {}
    day_groups: dict[str, dict] = {}
    all_dates = set()
    for sym, df in frames.items():
        ts_to_idx[sym] = {ts: i for i, ts in enumerate(df["Timestamp"])}
        day_groups[sym] = {d: g for d, g in df.groupby(df["Timestamp"].dt.date)}
        all_dates.update(day_groups[sym].keys())
    all_dates = sorted(all_dates)

    strategies: dict[str, ORBConfigurableStrategy] = {}
    cash = capital
    positions: dict[str, Position] = {}
    trades: list[TradeRecord] = []
    curve: list[dict] = []
    first_ts = None
    last_ts = None
    last_prices: dict[str, float] = {}

    for date in all_dates:
        for sym in frames:
            if sym not in strategies:
                strategies[sym] = ORBConfigurableStrategy(sym, config)
            strat = strategies[sym]
            if strat.session_date != date:
                strat.reset(date)

        day_data: dict[str, pd.DataFrame] = {}
        for sym in frames:
            day_df = day_groups.get(sym, {}).get(date)
            if day_df is not None and not day_df.empty:
                day_data[sym] = day_df
        if not day_data:
            continue

        day_ts = sorted(set(t for df in day_data.values() for t in df["Timestamp"]))

        for ts in day_ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            prices: dict[str, float] = {}
            highs: dict[str, float] = {}
            lows: dict[str, float] = {}
            bars: dict[str, pd.Series] = {}
            day_bars_map: dict[str, pd.DataFrame] = {}

            for sym, day_df in day_data.items():
                idx = ts_to_idx[sym].get(ts)
                if idx is None:
                    continue
                local_idx = idx - day_df.index[0]
                bar = day_df.iloc[local_idx]
                bars[sym] = bar
                prices[sym] = float(bar["Close"])
                highs[sym] = float(bar["High"])
                lows[sym] = float(bar["Low"])
                day_bars_map[sym] = day_df.iloc[:local_idx + 1]
            last_prices.update(prices)

            # Exit management
            for sym in list(positions.keys()):
                pos = positions[sym]
                if sym not in prices:
                    pos.bars_held += 1
                    continue
                px = prices[sym]
                hi = highs[sym]
                lo = lows[sym]
                pos.bars_held += 1
                exit_px = None
                exit_reason = None
                if pos.side == "long":
                    if lo <= pos.stop_price:
                        exit_px, exit_reason = pos.stop_price, "stop_loss"
                    elif hi >= pos.target_price:
                        exit_px, exit_reason = pos.target_price, "take_profit"
                else:
                    if hi >= pos.stop_price:
                        exit_px, exit_reason = pos.stop_price, "stop_loss"
                    elif lo <= pos.target_price:
                        exit_px, exit_reason = pos.target_price, "take_profit"
                if exit_px is None and ts.time() >= dt_time(15, 55):
                    exit_px, exit_reason = px, "eod_close"
                if exit_px is not None:
                    fill_px = apply_slippage(exit_px, pos.side, False, slippage_bps)
                    fee = fill_px * pos.qty * fee_rate
                    if pos.side == "long":
                        pnl = (fill_px - pos.entry_price) * pos.qty - fee - pos.entry_fee
                        cash += fill_px * pos.qty - fee
                    else:
                        pnl = (pos.entry_price - fill_px) * pos.qty - fee - pos.entry_fee
                        cash -= fill_px * pos.qty + fee
                    pnl_pct = pnl / (pos.entry_price * pos.qty) * 100 if pos.entry_price > 0 else 0
                    hold_hours = pos.bars_held / 60.0
                    trades.append(TradeRecord(
                        symbol=sym, side=pos.side, entry_date=pos.entry_ts,
                        exit_date=str(ts), entry_price=pos.entry_price,
                        exit_price=fill_px, quantity=pos.qty, pnl=pnl,
                        pnl_pct=pnl_pct, hold_days=int(hold_hours / 24),
                        hold_hours=hold_hours, reason=exit_reason,
                    ))
                    del positions[sym]

            # Equity
            equity = cash
            for sym, pos in positions.items():
                px = prices.get(sym, pos.entry_price)
                val = pos.qty * px
                equity += val if pos.side == "long" else -val
            curve.append({"date": str(ts), "equity": round(equity, 2)})

            # Entries
            if len(positions) >= max_positions:
                continue
            if ts.time() >= dt_time(15, 50):
                continue
            signals = []
            for sym in symbols:
                if sym in positions or sym not in bars:
                    continue
                strat = strategies.get(sym)
                if strat is None or strat.session_date != date:
                    continue
                day_df = day_bars_map.get(sym)
                if day_df is None or day_df.empty:
                    continue
                idx = len(day_df) - 1
                signal = strat.on_bar(ts, bars[sym], idx, day_df)
                if signal:
                    signals.append(signal)
            for sig in signals:
                if len(positions) >= max_positions:
                    break
                if sig.symbol in positions:
                    continue
                entry_px = apply_slippage(sig.entry_price, sig.side, True, slippage_bps)
                notional = equity * position_pct / 100.0
                qty = notional / entry_px
                if qty <= 0:
                    continue
                entry_fee = entry_px * qty * fee_rate
                cash -= entry_px * qty + entry_fee if sig.side == "long" else 0
                if sig.side == "short":
                    cash += entry_px * qty - entry_fee
                positions[sig.symbol] = Position(
                    symbol=sig.symbol, side=sig.side, entry_price=entry_px,
                    stop_price=sig.stop_price, target_price=sig.target_price,
                    qty=qty, entry_ts=str(ts), entry_fee=entry_fee,
                )

    # Close remaining
    for sym, pos in list(positions.items()):
        px = last_prices.get(sym, pos.entry_price)
        fill_px = apply_slippage(px, pos.side, False, slippage_bps)
        fee = fill_px * pos.qty * fee_rate
        if pos.side == "long":
            pnl = (fill_px - pos.entry_price) * pos.qty - fee - pos.entry_fee
            cash += fill_px * pos.qty - fee
        else:
            pnl = (pos.entry_price - fill_px) * pos.qty - fee - pos.entry_fee
            cash -= fill_px * pos.qty + fee
        pnl_pct = pnl / (pos.entry_price * pos.qty) * 100 if pos.entry_price > 0 else 0
        hold_hours = pos.bars_held / 60.0
        trades.append(TradeRecord(
            symbol=sym, side=pos.side, entry_date=pos.entry_ts,
            exit_date=str(last_ts), entry_price=pos.entry_price,
            exit_price=fill_px, quantity=pos.qty, pnl=pnl,
            pnl_pct=pnl_pct, hold_days=int(hold_hours / 24),
            hold_hours=hold_hours, reason="backtest_end",
        ))
        del positions[sym]

    report = BacktestReport.calculate_metrics(
        agent_name="orb_sweep", symbols=symbols,
        start_date=str(first_ts) if first_ts else "",
        end_date=str(last_ts) if last_ts else "",
        initial_capital=capital, final_equity=cash,
        equity_curve=curve, trades=trades, interval="1m",
        slippage_bps=slippage_bps, periods_per_year=390 * 252,
    )
    r = report.to_dict()
    return {
        "return_pct": r["total_return_pct"],
        "profit_factor": r["profit_factor"],
        "win_rate": r["win_rate"],
        "total_trades": r["total_trades"],
        "max_drawdown_pct": r["max_drawdown_pct"],
        "sharpe_ratio": r["sharpe_ratio"],
        "avg_hold_hours": r["avg_hold_hours"],
        "final_equity": r["final_equity"],
        "per_symbol": r.get("per_symbol_stats", {}),
    }


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ORB parameter optimization sweep")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--slippage", type=float, default=SLIPPAGE_BPS)
    parser.add_argument("--fee-rate", type=float, default=0.0)
    parser.add_argument("--zero-cost", action="store_true")
    parser.add_argument("--json", default="")
    parser.add_argument("--top", type=int, default=20, help="Show top N results")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    slippage = 0.0 if args.zero_cost else args.slippage
    fee_rate = 0.0 if args.zero_cost else args.fee_rate

    print(f"\nORB Parameter Optimization Sweep")
    print(f"  Symbols:    {', '.join(symbols)}")
    print(f"  Date range: {args.start} → {args.end}")
    print(f"  Capital:    ${args.capital:,.0f}")
    print(f"  Slippage:   {slippage} bps")
    print(f"  Mode:       {'ZERO COST' if args.zero_cost else 'realistic costs'}")

    alpaca = AlpacaProvider()
    if not alpaca.available:
        print("ERROR: Alpaca not configured")
        sys.exit(1)
    provider = CachedProvider(alpaca)

    print(f"\n  Fetching 1m data...")
    frames = fetch_1m_data(symbols, args.start, args.end, provider)
    if not frames:
        sys.exit(1)
    all_dates = sorted(set(d for f in frames.values() for d in f["Timestamp"].dt.date))
    prev_closes = fetch_prev_closes(symbols, all_dates, provider)

    grid = build_grid()
    print(f"  Grid size:  {len(grid)} configs")

    results = []
    t0 = time.time()
    for i, config in enumerate(grid):
        if i > 0 and i % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / i * (len(grid) - i)
            print(f"  [{i}/{len(grid)}] elapsed={elapsed:.0f}s eta={eta:.0f}s")
        result = run_single(
            config=config, symbols=symbols, frames=frames,
            prev_closes=prev_closes, capital=args.capital,
            slippage_bps=slippage, fee_rate=fee_rate,
        )
        result["config"] = config
        results.append(result)

    elapsed = time.time() - t0
    print(f"  Done: {len(grid)} configs in {elapsed:.0f}s ({elapsed/len(grid):.1f}s each)")

    # Sort by return
    results.sort(key=lambda r: r["return_pct"], reverse=True)

    print(f"\n{'='*100}")
    print(f"  TOP {args.top} CONFIGURATIONS (by return)")
    print(f"{'='*100}")
    print(f"  {'#':>3} {'Range':>5} {'MinR%':>5} {'Stop%':>5} {'Tgt%':>5} {'Entry':>5} | "
          f"{'Return':>8} {'PF':>6} {'WR':>5} {'Trades':>6} {'MaxDD':>6} {'Sharpe':>7}")
    print(f"  {'-'*95}")
    for i, r in enumerate(results[:args.top], 1):
        c = r["config"]
        status = "PASS" if r["return_pct"] > 0 and r["profit_factor"] > 1.0 else "FAIL"
        print(f"  {i:3d} {c['range_minutes']:5d} {c['min_range_pct']:5.1f} "
              f"{c['stop_pct']:5.1f} {c['target_pct']:5.1f} {c['latest_entry']:>5} | "
              f"{r['return_pct']:+7.2f}% {r['profit_factor']:6.3f} "
              f"{r['win_rate']:4.0%} {r['total_trades']:6d} "
              f"{r['max_drawdown_pct']:5.2f}% {r['sharpe_ratio']:7.3f} {status}")

    # Show per-symbol breakdown for top 3
    print(f"\n  --- Per-Symbol Breakdown (Top 3) ---")
    for i, r in enumerate(results[:3], 1):
        c = r["config"]
        print(f"\n  #{i} range={c['range_minutes']}min stop={c['stop_pct']}% tgt={c['target_pct']}% "
              f"→ {r['return_pct']:+.2f}% PF={r['profit_factor']:.3f}")
        ps = r.get("per_symbol", {})
        for sym, stats in sorted(ps.items()):
            print(f"    {sym:6s}: {stats['trades']:3d} trades, "
                  f"WR={stats['win_rate']:.0%}, "
                  f"PnL=${stats['total_pnl']:+.2f}, "
                  f"avg={stats['avg_pnl_pct']:+.2f}%")

    if args.json:
        output = {
            "config": {
                "symbols": symbols, "start": args.start, "end": args.end,
                "capital": args.capital, "slippage_bps": slippage,
                "fee_rate": fee_rate, "zero_cost": args.zero_cost,
            },
            "grid_size": len(grid),
            "elapsed_seconds": round(elapsed, 1),
            "top_results": [
                {**{k: v for k, v in r.items() if k != "per_symbol"}, "config": r["config"]}
                for r in results[:50]
            ],
        }
        with open(args.json, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\n  Top 50 results saved to: {args.json}")


if __name__ == "__main__":
    main()
