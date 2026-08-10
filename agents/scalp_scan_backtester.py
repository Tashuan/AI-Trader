"""Historical replay engine for ScalpRunner's 4-step scalp process.

Replays 1m/5m/15m OHLCV data through the exact scalp_scan_core logic used by
the live agent. Models stop-limit pre-positioning: a setup generates an entry
level, the order is "pending" for the next bar, and fills if price touches that
level. Exits are ATR-based SL/TP plus an optional trailing stop.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import scalp_scan_core as core
from backtest_report import BacktestReport, TradeRecord
from execution_simulator import FillConfig, FillResult, simulate_entry, simulate_exit
from market_data import MarketDataProvider, get_default_equity_provider
from strategy_registry import position_notional

logger = logging.getLogger(__name__)


_INTRADAY_LIMITS = {
    "1m": 7,
    "5m": 60,
    "15m": 60,
    "30m": 60,
}

_BAR_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
}

_SESSION_BARS_PER_DAY = {
    interval: (390 / minutes) for interval, minutes in _BAR_MINUTES.items()
}

# Resample rules: base interval → (mid TF, high TF)
_RESAMPLE_MAP = {
    "1m": ("5min", "15min"),
    "5m": ("15min", "30min"),
    "15m": ("30min", "1H"),
}

# Min bars needed at the highest TF (30 for indicator precompute).
# Base-TF lookback must be large enough to produce that after resample.
_MIN_HIGH_TF_BARS = 30
_BASE_LOOKBACK = {
    "1m": 200,   # 200 1m → 13 15m bars (ok, 15m only needs 10 in _trend_direction)
    "5m": 600,   # 600 5m → ~100 30m bars (covers market gaps, yields 30+)
    "15m": 400,  # 400 15m → ~100 1H bars
}


class ScalpScanBacktester:
    """Replay ScalpRunner 4-step logic through 1m/5m/15m historical data.

    Parameters
    ----------
    symbols : list[str]
        Watchlist / universe to replay.
    params : dict
        Effective ScalpRunner parameters (from strategy_registry).
    start_date : str
        YYYY-MM-DD start of simulation.
    end_date : str
        YYYY-MM-DD end of simulation.
    initial_capital : float
        Starting portfolio value.
    slippage_bps : float
        Slippage applied to fills (adverse direction).
    provider : MarketDataProvider | None
        Optional data provider; defaults to YFinanceProvider.
    goal_target : float | None
        Dollar profit target for goal-aware sizing.
    goal_max_loss : float | None
        Dollar max loss for goal halt.
    base_interval : str
        Base timeframe for the simulation timeline. "1m" fetches 1m bars
        and resamples to 5m/15m. "5m" fetches 5m bars and resamples to
        15m/30m — useful when 1m data isn't available (e.g. cache-only).
    """

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 100_000.0,
        slippage_bps: float = 2.0,
        provider: MarketDataProvider | None = None,
        goal_target: float | None = None,
        goal_max_loss: float | None = None,
        base_interval: str = "1m",
        fill_simulator=None,
        fill_config: FillConfig | None = None,
    ):
        self.symbols = symbols
        self.params = params
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.slippage_bps = slippage_bps
        self.provider = provider or get_default_equity_provider()
        self.goal_target = goal_target
        self.goal_max_loss = goal_max_loss
        self.goal_active = goal_target is not None or goal_max_loss is not None
        self.base_interval = base_interval
        self.bar_minutes = _BAR_MINUTES.get(base_interval, 5)
        self.fill_simulator = fill_simulator
        self.fill_config = fill_config or FillConfig.from_legacy(
            slippage_bps=slippage_bps, fee_rate=0.001,
            market="us-stock", interval=base_interval,
        )

    @staticmethod
    def _new_diagnostics() -> dict:
        return {
            "scan_bars": 0,
            "scan_errors": 0,
            "mtf_qualified": 0,
            "entry_rejected": 0,
            "trend_rejected": 0,
            "score_rejected": 0,
            "liquidity_rejected": 0,
            "setup_qualified": 0,
            "orders_placed": 0,
            "orders_filled": 0,
            "orders_expired": 0,
            "pending_at_end": 0,
            "same_bar_exit_skipped": 0,
            "exit_counts": Counter(),
            "fills_with_tick_data": 0,
            "fills_fallback_bar_close": 0,
            "avg_fill_slippage_bps": 0.0,
            "avg_spread_bps": 0.0,
        }

    def _finalize_diagnostics(self, diagnostics: dict) -> dict:
        result = dict(diagnostics)
        result["exit_counts"] = dict(result.get("exit_counts", {}))
        if self.fill_simulator is not None:
            fs = self.fill_simulator.stats
            result["fills_with_tick_data"] = fs.get("fills_with_tick_data", 0)
            result["fills_fallback_bar_close"] = fs.get("fills_fallback_bar_close", 0)
            result["avg_fill_slippage_bps"] = round(fs.get("avg_fill_slippage_bps", 0.0), 2)
            result["avg_spread_bps"] = round(fs.get("avg_spread_bps", 0.0), 2)
        return result

    # ─── Data fetching ─────────────────────────────────────────────

    def _fetch(self, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        """Fetch historical OHLCV for a single timeframe."""
        try:
            now = datetime.now()
            max_lookback = _INTRADAY_LIMITS.get(interval, 7)

            # CacheOnlyProvider and CachedProvider don't need lookback clamping —
            # they serve whatever is on disk. Only clamp for live APIs.
            is_cache = type(self.provider).__name__ in ("CacheOnlyProvider", "CachedProvider")

            if self.start_date:
                req_start = datetime.fromisoformat(self.start_date) - timedelta(days=3)
                if is_cache:
                    start = req_start.strftime("%Y-%m-%d")
                else:
                    earliest_allowed = now - timedelta(days=max_lookback - 1)
                    start = max(req_start, earliest_allowed).strftime("%Y-%m-%d")
            else:
                start = (now - timedelta(days=max_lookback - 1)).strftime("%Y-%m-%d")

            if is_cache and self.end_date:
                end = (datetime.fromisoformat(self.end_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            elif not self.end_date:
                end = now.strftime("%Y-%m-%d")
            else:
                end = min(
                    datetime.fromisoformat(self.end_date) + timedelta(days=1),
                    now,
                ).strftime("%Y-%m-%d")

            df = self.provider.history(symbol, start=start, end=end,
                                       interval=interval, auto_adjust=False, raise_errors=False)
        except Exception as exc:
            logger.warning("Failed to fetch %s for %s: %s", interval, symbol, exc)
            return None

        if df is None or df.empty:
            return None

        df = df.reset_index()
        col = "Datetime" if "Datetime" in df.columns else "Date"
        df[col] = pd.to_datetime(df[col], utc=True)
        df = df.sort_values(col).reset_index(drop=True)
        return df

    @staticmethod
    def _time_col(df: pd.DataFrame) -> str:
        return "Datetime" if "Datetime" in df.columns else "Date"

    # ─── Multi-timeframe window builder ────────────────────────────

    def _build_mtf_window(self, frames: dict[str, pd.DataFrame], ts: pd.Timestamp,
                          lookback: int = 200) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """For a given base-TF timestamp, build 3 MTF windows ending at that bar."""
        df_base = frames.get("base")
        if df_base is None or df_base.empty:
            return None, None, None

        col = self._time_col(df_base)
        idx = df_base.index[df_base[col] == ts].tolist()
        if not idx:
            return None, None, None
        end_idx = idx[-1]
        start_idx = max(0, end_idx - lookback + 1)

        w_base = df_base.iloc[start_idx:end_idx + 1].copy()
        if w_base.empty or len(w_base) < 30:
            return None, None, None

        mid_rule, high_rule = _RESAMPLE_MAP.get(self.base_interval, ("5min", "15min"))
        w_mid = self._resample_window(w_base, mid_rule, lookback)
        w_high = self._resample_window(w_base, high_rule, lookback)

        # Map to the labels the core logic expects: (1m=entry, 5m=pattern, 15m=trend)
        return w_base, w_mid, w_high

    @staticmethod
    def _resample_window(df_1m: pd.DataFrame, rule: str, lookback: int) -> Optional[pd.DataFrame]:
        """Resample a 1m window to 5m or 15m."""
        col = "Datetime" if "Datetime" in df_1m.columns else "Date"
        agg = df_1m.set_index(col).resample(rule).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna()
        if agg.empty or len(agg) < 10:
            return None
        agg = agg.reset_index()
        return agg.tail(lookback)

    # ─── Scan at a single bar ─────────────────────────────────────

    def _scan_symbol(self, symbol: str, df_1m: pd.DataFrame, df_5m: pd.DataFrame,
                     df_15m: pd.DataFrame) -> Optional[dict]:
        """Run the 4-step scalp analysis on one bar's data windows."""
        try:
            pre = core.precompute_indicators_multi_tf(df_1m, df_5m, df_15m, self.params)
            if pre.get("1m") is None:
                return None

            bar_idx = len(df_1m) - 1
            mtf = core.deep_scan_multi_tf(symbol, pre, bar_idx, self.params)
            if not mtf.get("qualifies_for_entry"):
                return mtf

            # Fib / S/R / breakout on 5m window
            swings = core.detect_swing_highs_lows(df_5m, self.params.get("levels", {}).get("sr_lookback_bars", 50))
            swing_highs = swings.get("swing_highs", [])
            swing_lows = swings.get("swing_lows", [])

            direction = mtf.get("entry_direction", "long")
            fib_levels = {}
            fib_extensions = {}
            if swing_highs and swing_lows:
                recent_high = max(p for _, p in swing_highs[-3:])
                recent_low = min(p for _, p in swing_lows[-3:])
                fib_levels = core.compute_fib_retracement(recent_high, recent_low, direction)
                fib_extensions = core.compute_fib_extension(recent_high, recent_low, direction)

            sr = core.detect_support_resistance(
                df_5m,
                lookback=self.params.get("levels", {}).get("sr_lookback_bars", 50),
                min_touches=self.params.get("levels", {}).get("sr_min_touches", 2),
                tolerance_pct=self.params.get("levels", {}).get("sr_tolerance_pct", 0.15),
            )
            breakout = core.detect_breakout_level(df_5m, sr, self.params)
            pattern = core.detect_pattern(df_5m)

            # Liquidity — compute from 5m frame only (no quote/level2 in backtest)
            quote = {
                "bid": float(df_1m["Close"].iloc[-1]),
                "ask": float(df_1m["Close"].iloc[-1]),
            }
            liq = core.liquidity_score(quote, None, df_5m, self.params)

            setup = core.score_scalp_setup(mtf, fib_levels, sr, breakout, pattern, liq, self.params)
            return {
                "mtf": mtf,
                "setup": setup,
                "fib_levels": fib_levels,
                "fib_extensions": fib_extensions,
                "sr_levels": sr,
                "breakout": breakout,
                "pattern": pattern,
                "liquidity": liq,
                "price": float(df_1m["Close"].iloc[-1]),
            }
        except Exception as exc:
            logger.debug("Scan failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _record_scan_diagnostic(scan: Optional[dict], diagnostics: dict) -> None:
        diagnostics["scan_bars"] += 1
        if not scan:
            diagnostics["scan_errors"] += 1
            return

        mtf = scan.get("mtf", {})
        setup = scan.get("setup")
        if mtf.get("qualifies_for_entry"):
            diagnostics["mtf_qualified"] += 1
        else:
            if mtf.get("trend_agrees") is False:
                diagnostics["trend_rejected"] += 1
            else:
                diagnostics["entry_rejected"] += 1
            return

        if not setup:
            return
        if setup.get("score", 0) < 4.0:
            diagnostics["score_rejected"] += 1
        if not scan.get("liquidity", {}).get("passes", False):
            diagnostics["liquidity_rejected"] += 1
        if setup.get("qualifies"):
            diagnostics["setup_qualified"] += 1

    # ─── Sizing and helpers ───────────────────────────────────────

    def _sizing_pct(self, equity: float, consecutive_losses: int) -> float:
        ps = self.params.get("position_sizing", {})
        lo = ps.get("normal_sizing_min_pct", 5)
        hi = ps.get("normal_sizing_max_pct", 10)
        size_pct = (lo + hi) / 2.0
        threshold = ps.get("consecutive_loss_threshold", 3)
        if consecutive_losses >= threshold:
            cut = ps.get("consecutive_loss_size_cut_pct", 50)
            size_pct *= (1.0 - cut / 100.0)
        return size_pct

    # ─── Main run ──────────────────────────────────────────────────

    def run(self) -> BacktestReport:
        """Execute the multi-timeframe scalp backtest."""
        if self.fill_simulator is not None:
            self.fill_simulator.reset_stats()
        # Fetch base-interval data for all symbols (primary simulation timeline)
        historical: dict[str, pd.DataFrame] = {}
        for sym in self.symbols:
            df = self._fetch(sym, self.base_interval)
            if df is not None and not df.empty:
                historical[sym] = df

        if not historical:
            return self._empty_report()

        col_map = {s: self._time_col(df) for s, df in historical.items()}
        all_ts = sorted(set().union(*(set(df[col_map[s]]) for s, df in historical.items())))

        if self.start_date:
            all_ts = [t for t in all_ts if t >= pd.Timestamp(self.start_date, tz="UTC")]
        if self.end_date:
            all_ts = [t for t in all_ts if t <= pd.Timestamp(self.end_date, tz="UTC") + pd.Timedelta(days=1)]
        if not all_ts:
            return self._empty_report()

        configured_lookback = self.params.get("timeframes", {}).get("lookback_bars", 0)
        lookback = max(
            int(configured_lookback or 0),
            _BASE_LOOKBACK.get(self.base_interval, 200),
        )
        diagnostics = self._new_diagnostics()
        diagnostics.update({
            "base_interval": self.base_interval,
            "bar_minutes": self.bar_minutes,
            "session_bars_per_day": _SESSION_BARS_PER_DAY.get(self.base_interval, 78),
            "sharpe_periods_per_year": _SESSION_BARS_PER_DAY.get(self.base_interval, 78) * 252,
        })

        cash = self.initial_capital
        positions: dict[str, dict] = {}
        pending: dict[str, dict] = {}      # symbol -> pending order
        cooldown: dict[str, int] = {}
        trades: list[TradeRecord] = []
        curve: list[dict] = []
        losses = 0

        goal_can_open = True
        goal_status = "active"
        goal_halt_ts = None
        goal_reason = None

        ps_cfg = self.params.get("position_sizing", {})
        max_positions = int(ps_cfg.get("max_positions", 3))
        max_pending = int(ps_cfg.get("max_pending_orders", 5))

        for i, ts in enumerate(all_ts):
            # Decrement cooldowns
            for sym in list(cooldown):
                cooldown[sym] -= 1
                if cooldown[sym] <= 0:
                    del cooldown[sym]

            # Current bar price data
            prices: dict[str, float] = {}
            highs: dict[str, float] = {}
            lows: dict[str, float] = {}
            current_bars: dict[str, object] = {}
            scans: dict[str, dict] = {}

            for sym, df in historical.items():
                col = col_map[sym]
                idx = df.index[df[col] == ts].tolist()
                if not idx:
                    continue
                row = df.iloc[idx[-1]]
                current_bars[sym] = row
                prices[sym] = float(row["Close"])
                highs[sym] = float(row["High"])
                lows[sym] = float(row["Low"])

                w1m, w5m, w15m = self._build_mtf_window({"base": df}, ts, lookback)
                if w1m is not None and w5m is not None and w15m is not None:
                    scan = self._scan_symbol(sym, w1m, w5m, w15m)
                    self._record_scan_diagnostic(scan, diagnostics)
                    if scan:
                        scans[sym] = scan

            # ── Expire stale orders before checking fills ─────────────
            expiry_minutes = float(self.params.get("order", {}).get("order_expiry_minutes", 30))
            for sym, po in list(pending.items()):
                placed_at = pd.Timestamp(po.get("placed_at", ts))
                age_minutes = (pd.Timestamp(ts) - placed_at).total_seconds() / 60.0
                if age_minutes >= expiry_minutes:
                    del pending[sym]
                    diagnostics["orders_expired"] += 1

            # ── Fill pending orders (intrabar stop-limit trigger) ─────
            filled_this_bar: set[str] = set()
            for sym, po in list(pending.items()):
                if sym not in prices:
                    continue
                entry = po["entry_level"]
                hi, lo = highs[sym], lows[sym]
                side = po["side"]
                triggered = (side == "long" and hi >= entry) or (side == "short" and lo <= entry)

                if triggered:
                    fill_price = entry
                    fill_qty = po["qty"]
                    fee = 0.0
                    if self.fill_simulator is not None:
                        # Tick-level data decides whether and where the order fills.
                        tick_result = self.fill_simulator.simulate_entry(
                            sym, ts, entry, side, "stop_limit")
                        if tick_result["filled"]:
                            fill_price = tick_result["fill_price"]
                        else:
                            continue
                        fee = fill_price * fill_qty * self.fill_config.fee_rate
                    else:
                        result = simulate_entry(
                            entry, side, fill_qty, sym, self.fill_config,
                            current_bars.get(sym),
                        )
                        fill_price, fill_qty, fee = result.fill_price, result.fill_qty, result.fee
                        if fill_qty <= 0:
                            del pending[sym]
                            continue

                    cost = fill_qty * fill_price
                    cash += (-cost - fee) if side == "long" else cost - fee
                    positions[sym] = {
                        "symbol": sym, "side": side, "entry_price": fill_price,
                        "qty": fill_qty, "entry_fee": fee, "sl": po["sl"],
                        "tp": po["tp"], "trail_sl_pct": po["trail_sl_pct"],
                        "trail_act_pct": po["trail_act_pct"], "peak": fill_price,
                        "trough": fill_price, "trailing_active": False,
                        "entry_date": str(ts), "bars_held": 0,
                    }
                    del pending[sym]
                    filled_this_bar.add(sym)
                    diagnostics["orders_filled"] += 1

            # ── Position exit management ─────────────────────────────
            for sym, pos in list(positions.items()):
                if sym in filled_this_bar:
                    diagnostics["same_bar_exit_skipped"] += 1
                    continue
                if sym not in prices:
                    continue
                px = prices[sym]
                hi, lo = highs.get(sym, px), lows.get(sym, px)
                side = pos["side"]
                entry = pos["entry_price"]
                exit_px = None
                exit_reason = None

                if side == "long":
                    if lo <= pos["sl"]:
                        exit_px, exit_reason = pos["sl"], "stop_loss"
                    elif hi >= pos["tp"]:
                        exit_px, exit_reason = pos["tp"], "take_profit"
                    else:
                        pos["peak"] = max(pos["peak"], hi)
                        act_pct = (pos["peak"] - entry) / entry * 100
                        if not pos["trailing_active"] and act_pct >= pos["trail_act_pct"]:
                            pos["trailing_active"] = True
                        if pos["trailing_active"]:
                            trail = pos["peak"] * (1 - pos["trail_sl_pct"] / 100)
                            if lo <= trail:
                                exit_px, exit_reason = trail, "trailing_stop"
                else:
                    if hi >= pos["sl"]:
                        exit_px, exit_reason = pos["sl"], "stop_loss"
                    elif lo <= pos["tp"]:
                        exit_px, exit_reason = pos["tp"], "take_profit"
                    else:
                        pos["trough"] = min(pos["trough"], lo)
                        act_pct = (entry - pos["trough"]) / entry * 100
                        if not pos["trailing_active"] and act_pct >= pos["trail_act_pct"]:
                            pos["trailing_active"] = True
                        if pos["trailing_active"]:
                            trail = pos["trough"] * (1 + pos["trail_sl_pct"] / 100)
                            if hi >= trail:
                                exit_px, exit_reason = trail, "trailing_stop"

                # Active mode exit review
                if exit_px is None and self.params.get("exit_rules", {}).get("exit_mode") == "active":
                    minutes_held = pos["bars_held"] * self.bar_minutes
                    ind_data = scans.get(sym, {}).get("mtf", {}).get("indicators", {})
                    review = core.review_scalp_position(
                        {"pnl_pct": 0, "side": side}, self.params, minutes_held, ind_data,
                    )
                    if review.get("verdict") == "EXIT":
                        exit_px, exit_reason = px, review.get("exit_reason", "active_exit")

                if exit_px is not None:
                    tick_fill_price = None
                    # Use tick-level exit fill if simulator is available.
                    if self.fill_simulator is not None:
                        entry_ts = pd.Timestamp(pos.get("entry_date", str(ts)))
                        exit_result = self.fill_simulator.simulate_exit(
                            sym, entry_ts, pos["sl"], pos["tp"], side,
                            trailing_stop=exit_px if exit_reason == "trailing_stop" else None,
                            max_bars=pos.get("bars_held", 78) + 1,
                        )
                        if exit_result["used_tick_data"]:
                            exit_px = exit_result["exit_price"]
                            tick_fill_price = exit_px
                            exit_reason = exit_result["exit_reason"]
                    cash, pnl, _ = self._close_position(
                        pos, exit_px, ts, cash, trades, exit_reason,
                        current_bars.get(sym), tick_fill_price,
                    )
                    diagnostics["exit_counts"][exit_reason or "unknown"] += 1
                    losses = 0 if pnl > 0 else losses + 1
                    cooldown[sym] = 3
                    if pos["qty"] <= 1e-12:
                        del positions[sym]
                else:
                    pos["bars_held"] += 1

            # ── Equity and goal checks ───────────────────────────────
            equity = cash
            gross = 0.0
            for sym, pos in positions.items():
                px = prices.get(sym, pos["entry_price"])
                val = pos["qty"] * px
                gross += val
                if pos["side"] == "long":
                    equity += val
                else:
                    equity -= val

            pending_gross = sum(po["qty"] * po["entry_level"] for po in pending.values())

            if self.goal_active and goal_can_open:
                pnl_dollars = equity - self.initial_capital
                if self.goal_target is not None and pnl_dollars >= self.goal_target:
                    goal_can_open = False
                    goal_status = "achieved"
                    goal_halt_ts = str(ts)
                    goal_reason = f"target_reached_${pnl_dollars:.2f}"
                elif self.goal_max_loss is not None and pnl_dollars <= -self.goal_max_loss:
                    goal_can_open = False
                    goal_status = "max_loss_hit"
                    goal_halt_ts = str(ts)
                    goal_reason = f"max_loss_hit_${pnl_dollars:.2f}"

            curve.append({"date": str(ts), "equity": round(equity, 2)})

            # ── Place new pending orders ─────────────────────────────
            if not goal_can_open:
                continue

            available = (max_positions + max_pending) - len(positions) - len(pending)
            if available <= 0 or not scans:
                continue

            ranked = sorted(
                [self._setup_from_scan(s, d) for s, d in scans.items()
                 if d and d.get("setup", {}).get("qualifies")
                 and s not in positions and s not in pending and s not in cooldown],
                key=lambda x: x["score"],
                reverse=True,
            )

            for setup in ranked:
                if available <= 0:
                    break
                sym = setup["symbol"]
                if sym in positions or sym in pending or sym in cooldown:
                    continue

                entry = setup["entry_level"]
                sl = setup["sl_level"]
                if entry <= 0 or sl <= 0:
                    continue

                stop_distance = abs((sl - entry) / entry) * 100
                notional = position_notional(
                    equity, stop_distance, gross + pending_gross, self.params,
                )
                if notional <= 0:
                    continue

                qty = notional / entry

                exit_cfg = self.params.get("exit_rules", {})
                pending[sym] = {
                    "symbol": sym,
                    "side": setup["direction"],
                    "entry_level": entry,
                    "sl": sl,
                    "tp": setup["tp_level"],
                    "qty": qty,
                    "trail_sl_pct": exit_cfg.get("trailing_sl_pct", 0.5),
                    "trail_act_pct": exit_cfg.get("trailing_activation_pct", 0.8),
                    "placed_at": str(ts),
                }
                diagnostics["orders_placed"] += 1
                available -= 1

        diagnostics["pending_at_end"] = len(pending)
        sample_days = (all_ts[-1] - all_ts[0]).total_seconds() / 86400 if len(all_ts) > 1 else 0
        if sample_days < 20 or len(trades) < 100:
            diagnostics["sample_warning"] = "Short sample: use multiple windows and at least 100 trades before promotion."

        # ── Close remaining positions at final bar ─────────────────
        final_ts = all_ts[-1]
        for sym, pos in list(positions.items()):
            df = historical[sym]
            col = col_map[sym]
            final_idx = df.index[df[col] == final_ts].tolist()
            final_bar = df.iloc[final_idx[-1]] if final_idx else None
            px = float(final_bar["Close"]) if final_bar is not None else pos["entry_price"]
            cash, _, _ = self._close_position(
                pos, px, final_ts, cash, trades, "Backtest end", final_bar, None, True,
            )
            diagnostics["exit_counts"]["Backtest end"] += 1
            if pos["qty"] <= 1e-12:
                del positions[sym]

        report = BacktestReport.calculate_metrics(
            agent_name="ScalpRunner",
            symbols=self.symbols,
            start_date=str(all_ts[0]),
            end_date=str(all_ts[-1]),
            initial_capital=self.initial_capital,
            final_equity=cash,
            equity_curve=curve,
            trades=trades,
            interval=self.base_interval,
            slippage_bps=self.slippage_bps,
            periods_per_year=_SESSION_BARS_PER_DAY.get(self.base_interval, 78) * 252,
            diagnostics=self._finalize_diagnostics(diagnostics),
        )
        if self.goal_active:
            report.goal_simulation = {
                "target_amount": self.goal_target,
                "max_loss": self.goal_max_loss,
                "status": goal_status,
                "halt_timestamp": goal_halt_ts,
                "halt_reason": goal_reason,
                "final_pnl": round(cash - self.initial_capital, 2),
                "goal_achieved": goal_status == "achieved",
                "trades_before_halt": len([t for t in trades if goal_halt_ts is None or t.entry_date <= goal_halt_ts]),
            }
        return report

    # ─── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _setup_from_scan(symbol: str, scan: dict) -> dict:
        setup = scan.get("setup", {})
        return {
            "symbol": symbol,
            "score": setup.get("score", 0),
            "direction": setup.get("direction", "long"),
            "entry_level": setup.get("entry_level", 0),
            "sl_level": setup.get("sl_level", 0),
            "tp_level": setup.get("tp_level", 0),
            "atr": setup.get("atr", 0),
            "pattern_type": setup.get("pattern_type", "none"),
            "breakout_level": setup.get("breakout_level", 0),
            "reason": setup.get("reason", ""),
        }

    def _close_position(self, pos: dict, price: float, ts, cash: float,
                        trades: list[TradeRecord], reason: str, bar=None,
                        precomputed_price: float | None = None,
                        force_full: bool = False) -> tuple[float, float, float]:
        config = replace(self.fill_config, enable_partial_fills=False) if force_full else self.fill_config
        result = (
            FillResult(
                precomputed_price, pos["qty"],
                precomputed_price * pos["qty"] * config.fee_rate,
                0.0, False,
            )
            if precomputed_price is not None else simulate_exit(
                price, pos["side"], pos["qty"], pos["symbol"], config, bar,
            )
        )
        qty = min(result.fill_qty, pos["qty"])
        if qty <= 0:
            return cash, 0.0, 0.0
        entry_fee = pos.get("entry_fee", 0.0) * qty / pos["qty"]
        gross_pnl = ((result.fill_price - pos["entry_price"]) if pos["side"] == "long"
                     else (pos["entry_price"] - result.fill_price)) * qty
        pnl = gross_pnl - result.fee - entry_fee
        cash += (qty * result.fill_price - result.fee
                 if pos["side"] == "long"
                 else -(qty * result.fill_price + result.fee))
        entry_dt = pd.Timestamp(pos.get("entry_date", str(ts)))
        exit_dt = pd.Timestamp(ts)
        hold_hours = max(0.0, (exit_dt - entry_dt).total_seconds() / 3600)
        pnl_pct = pnl / (pos["entry_price"] * qty) * 100 if pos["entry_price"] > 0 else 0.0
        trades.append(TradeRecord(
            symbol=pos.get("symbol", ""), side=pos["side"],
            entry_date=str(pos.get("entry_date", "")), exit_date=str(ts),
            entry_price=pos["entry_price"], exit_price=result.fill_price,
            quantity=qty, pnl=pnl, pnl_pct=pnl_pct, hold_days=int(hold_hours / 24),
            hold_hours=hold_hours, reason=reason[:200] if reason else "",
        ))
        pos["qty"] -= qty
        pos["entry_fee"] = max(0.0, pos.get("entry_fee", 0.0) - entry_fee)
        return cash, pnl, qty

    def _empty_report(self) -> BacktestReport:
        return BacktestReport.calculate_metrics(
            agent_name="ScalpRunner",
            symbols=self.symbols,
            start_date=self.start_date or "N/A",
            end_date=self.end_date or "N/A",
            initial_capital=self.initial_capital,
            final_equity=self.initial_capital,
            equity_curve=[],
            trades=[],
            interval=self.base_interval,
            slippage_bps=self.slippage_bps,
            periods_per_year=_SESSION_BARS_PER_DAY.get(self.base_interval, 78) * 252,
            diagnostics=self._finalize_diagnostics(self._new_diagnostics()),
        )
