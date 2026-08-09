"""
scan_backtester.py — Historical replay engine for BlitzTrader Goal Runner.

Replays historical OHLCV data through scan_core.deep_scan_symbol_from_df and
scan_core.review_position_from_indicators, implementing the single-position
Goal Runner state machine: goal-aware sizing, 6-rule exit review, switch logic,
consecutive-loss circuit breaker, cycles_flat tracking, reentry cooldown.

Uses the exact same strategy params and indicator math as the live agent, so
backtest results reflect what the live agent would have done.
"""

import bisect
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import scan_core
from backtest_report import BacktestReport, TradeRecord
from market_data import MarketDataProvider, YFinanceProvider

logger = logging.getLogger(__name__)


# Reuse yfinance lookback limits from the existing backtester
_INTRADAY_MAX_LOOKBACK_DAYS = {
    "1m": 7,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "60m": 730,
    "1h": 730,
    "90m": 60,
    "4h": 730,
}

_BARS_PER_DAY = {
    "1d": 1,
    "4h": 6,
    "1h": 24,
    "60m": 24,
    "30m": 48,
    "15m": 96,
    "5m": 288,
    "2m": 720,
    "1m": 1440,
}

# Minimum bars needed for deep_scan_symbol_from_df to produce valid results
_MIN_BARS = 30


class ScanBacktester:
    """Replay historical data through scan_core logic with Goal Runner state machine.

    Parameters
    ----------
    symbols : list[str]
        Watchlist to replay (backtest universe).
    params : dict
        Strategy params (merged DEFAULT_PARAMS + DB overrides).
    start_date : str
        YYYY-MM-DD start of simulation period.
    end_date : str
        YYYY-MM-DD end of simulation period.
    initial_capital : float
        Starting portfolio value.
    interval : str
        Candle interval (e.g. "1h", "15m", "1d").
    slippage_bps : float
        Slippage in basis points applied adversely to fills.
    goal_target : float | None
        Dollar profit target for goal-aware sizing. If None, defaults to 10% of initial_capital.
    """

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 100_000.0,
        interval: str = "1h",
        slippage_bps: float = 0.0,
        goal_target: Optional[float] = None,
        goal_max_loss: Optional[float] = None,
        provider: MarketDataProvider | None = None,
    ):
        self.symbols = symbols
        self.params = params
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.interval = interval or "1h"
        self.slippage_bps = slippage_bps
        self.goal_target = goal_target if goal_target is not None else initial_capital * 0.10
        self.goal_max_loss = goal_max_loss
        self.goal_active = goal_target is not None or goal_max_loss is not None
        self.provider = provider or YFinanceProvider()

    # ─── Historical data fetching ──────────────────────────────────

    def _fetch_historical_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Fetch historical OHLCV for a symbol via yfinance, reset_index'd."""
        try:
            yf_symbol = scan_core.yf_ticker(symbol)
            is_intraday = self.interval != "1d"

            if is_intraday:
                max_lookback = _INTRADAY_MAX_LOOKBACK_DAYS.get(self.interval, 60)
                now = datetime.now()
                requested_start = (
                    datetime.fromisoformat(self.start_date) - timedelta(days=5)
                    if self.start_date else now - timedelta(days=max_lookback)
                )
                earliest_allowed = now - timedelta(days=max_lookback - 1)
                fetch_start_dt = max(requested_start, earliest_allowed)
                start = fetch_start_dt.strftime("%Y-%m-%d")
                end_dt = (
                    datetime.fromisoformat(self.end_date) + timedelta(days=1)
                    if self.end_date else now
                )
                end = min(end_dt, now).strftime("%Y-%m-%d")
                df = self.provider.history(yf_symbol, start=start, end=end, interval=self.interval, auto_adjust=False, raise_errors=False)
            elif not self.start_date:
                df = self.provider.history(yf_symbol, period="2y", interval="1d", auto_adjust=False, raise_errors=False)
            else:
                fetch_start_dt = datetime.fromisoformat(self.start_date) - timedelta(days=365)
                start = fetch_start_dt.strftime("%Y-%m-%d")
                end_dt = datetime.fromisoformat(self.end_date) + timedelta(days=1) if self.end_date else datetime.now()
                end = end_dt.strftime("%Y-%m-%d")
                df = self.provider.history(yf_symbol, start=start, end=end, interval="1d", auto_adjust=False, raise_errors=False)
        except Exception as exc:
            logger.warning("Failed to fetch historical data for %s: %s", symbol, exc)
            return None

        if df is None or getattr(df, "empty", True):
            logger.warning("No historical data returned for %s (interval=%s, start=%s, end=%s)",
                           symbol, self.interval, self.start_date, self.end_date)
            return None

        df = df.reset_index()
        return df

    @staticmethod
    def _time_col(df: pd.DataFrame) -> str:
        if "Datetime" in df.columns:
            return "Datetime"
        return "Date"

    def _ts_key(self, x) -> str:
        if hasattr(x, "strftime"):
            if self.interval == "1d":
                return x.strftime("%Y-%m-%d")
            return x.strftime("%Y-%m-%dT%H:%M:%S")
        s = str(x)
        return s[:10] if self.interval == "1d" else s[:19]

    # ─── Goal-aware sizing ──────────────────────────────────────────

    def _goal_progress(self, equity: float) -> float:
        """Return goal progress percentage (0-100+)."""
        if self.goal_target <= 0:
            return 0.0
        return ((equity - self.initial_capital) / self.goal_target) * 100

    def _sizing_pct(self, equity: float, consecutive_losses: int) -> tuple[float, bool]:
        """Return (position_size_pct, is_final_stretch) based on goal phase.

        Uses the midpoint of the configured range for each phase.
        """
        ps = self.params.get("position_sizing", {})
        progress = self._goal_progress(equity)

        # Final stretch: within 20% of target (progress > 80)
        is_final_stretch = progress > 80.0

        if is_final_stretch:
            lo = ps.get("approaching_sizing_min_pct", 15)
            hi = ps.get("approaching_sizing_max_pct", 25)
        else:
            lo = ps.get("normal_sizing_min_pct", 25)
            hi = ps.get("normal_sizing_max_pct", 40)

        size_pct = (lo + hi) / 2.0

        # Consecutive loss circuit breaker
        threshold = ps.get("consecutive_loss_threshold", 3)
        if consecutive_losses >= threshold:
            cut = ps.get("consecutive_loss_size_cut_pct", 50)
            size_pct *= (1.0 - cut / 100.0)

        return size_pct, is_final_stretch

    def _min_signals_for_entry(self, consecutive_losses: int) -> int:
        """Return minimum signal count required for entry (raised after consecutive losses)."""
        ps = self.params.get("position_sizing", {})
        threshold = ps.get("consecutive_loss_threshold", 3)
        if consecutive_losses >= threshold:
            return ps.get("consecutive_loss_min_signals", 5)
        return self.params.get("entry_criteria", {}).get("min_signals", 4)

    # ─── Main run ───────────────────────────────────────────────────

    def run(self) -> BacktestReport:
        """Execute the backtest and return a performance report."""
        # 1. Fetch all historical data upfront
        historical: dict[str, pd.DataFrame] = {}
        for sym in self.symbols:
            df = self._fetch_historical_data(sym)
            if df is not None and not df.empty:
                historical[sym] = df

        if not historical:
            return self._empty_report()

        # 1b. Precompute all indicator series once per symbol — O(n) per symbol
        precomputed: dict[str, dict] = {}
        for sym, df in historical.items():
            precomputed[sym] = scan_core.precompute_indicators(df, self.params)

        # 2. Build unified timeline
        all_ts: set[str] = set()
        col_map: dict[str, str] = {}
        ts_list_map: dict[str, list[str]] = {}
        ts_to_idx_map: dict[str, dict[str, int]] = {}

        for sym, df in historical.items():
            col = self._time_col(df)
            col_map[sym] = col
            keys = df[col].apply(self._ts_key).tolist()
            ts_to_idx_map[sym] = {}
            ts_list = []
            for i, k in enumerate(keys):
                ts_to_idx_map[sym][k] = i
                ts_list.append(k)
            ts_list_map[sym] = ts_list
            all_ts.update(ts_list)

        sorted_ts = sorted(all_ts)
        if self.start_date:
            sorted_ts = [t for t in sorted_ts if t >= self.start_date]
        if self.end_date:
            end_bound = self.end_date if self.interval == "1d" else f"{self.end_date}T23:59:59"
            sorted_ts = [t for t in sorted_ts if t <= end_bound]

        if not sorted_ts:
            return self._empty_report()

        # 3. State variables
        cash = self.initial_capital
        positions: dict[str, dict] = {}  # symbol -> position dict
        closed_trades: list[TradeRecord] = []
        equity_curve: list[dict] = []
        consecutive_losses = 0
        reentry_cooldown: dict[str, int] = {}  # symbol -> remaining cooldown bars
        actual_start = sorted_ts[0]
        actual_end = sorted_ts[-1]

        # Goal halt state
        goal_can_open = True
        goal_status = "active"
        goal_halt_ts = None
        goal_reason = None

        exit_cfg = self.params.get("exit_rules", {})
        stagnation_threshold = exit_cfg.get("stagnation_threshold_pct", 0.3)
        switch_cfg = self.params.get("switch_logic", {})
        switch_threshold_pct = switch_cfg.get("switch_score_threshold_pct", 20)
        switch_require_profitable = switch_cfg.get("switch_require_profitable", True)
        reentry_cooldown_cycles = switch_cfg.get("reentry_cooldown_cycles", 3)
        ps_cfg = self.params.get("position_sizing", {})
        max_dollar_cap = ps_cfg.get("max_position_dollar_cap")
        max_positions = ps_cfg.get("max_positions", 1)
        # Per-position capital allocation: split equity evenly across max_positions
        per_pos_fraction = 1.0 / max_positions if max_positions > 0 else 1.0

        # 4. Simulation loop
        for bar_idx, sim_ts in enumerate(sorted_ts):
            # Decrement reentry cooldowns
            for sym in list(reentry_cooldown.keys()):
                if reentry_cooldown[sym] > 0:
                    reentry_cooldown[sym] -= 1
                if reentry_cooldown[sym] <= 0:
                    del reentry_cooldown[sym]

            # Build price lookup for this bar (close, high, low)
            price_lookup: dict[str, float] = {}
            bar_high: dict[str, float] = {}
            bar_low: dict[str, float] = {}
            for sym, df in historical.items():
                idx = ts_to_idx_map[sym].get(sim_ts)
                if idx is not None:
                    try:
                        row = df.iloc[idx]
                        price_lookup[sym] = float(row["Close"])
                        bar_high[sym] = float(row["High"])
                        bar_low[sym] = float(row["Low"])
                    except Exception:
                        pass

            # Compute deep scan for all symbols at this bar (from precomputed — O(1) per symbol)
            scans: dict[str, dict] = {}
            for sym in historical:
                idx = ts_to_idx_map[sym].get(sim_ts)
                if idx is None or idx < _MIN_BARS:
                    continue
                scan_result = scan_core.deep_scan_from_precomputed(sym, precomputed[sym], idx, self.params)
                if not scan_result.get("error"):
                    scans[sym] = scan_result

            # Current equity (including all open positions mark-to-market)
            current_equity = cash
            for pos_sym, pos in positions.items():
                px = price_lookup.get(pos_sym, pos["entry_price"])
                if pos["side"] == "long":
                    current_equity += pos["qty"] * px
                else:
                    current_equity -= pos["qty"] * px

            # ── Position management (loop over all open positions) ───────
            to_close: list[tuple[str, float, str]] = []  # (symbol, exit_px, reason)
            for pos_sym, pos in list(positions.items()):
                px = price_lookup.get(pos_sym, pos["entry_price"])
                hi = bar_high.get(pos_sym, px)
                lo = bar_low.get(pos_sym, px)
                side = pos["side"]
                entry = pos["entry_price"]

                # ── Intrabar stop-loss check ────────────────────────────
                sl_pct = exit_cfg.get("stop_loss_pct", -2.0)
                intrabar_stop_exit = False
                stop_exit_price = px

                if side == "long":
                    stop_level = entry * (1 + sl_pct / 100.0)
                    if lo <= stop_level:
                        intrabar_stop_exit = True
                        stop_exit_price = stop_level
                else:
                    stop_level = entry * (1 - sl_pct / 100.0)
                    if hi >= stop_level:
                        intrabar_stop_exit = True
                        stop_exit_price = stop_level

                # ── Trailing stop check ────────────────────────────────
                trail_sl_pct = exit_cfg.get("trailing_sl_pct", 0)
                trail_act_pct = exit_cfg.get("trailing_activation_pct", 0)
                trailing_exit = False
                trail_exit_price = px

                if trail_sl_pct > 0 and trail_act_pct > 0:
                    if side == "long":
                        if hi > pos["peak_price"]:
                            pos["peak_price"] = hi
                        peak_pnl = ((hi - entry) / entry) * 100 if entry else 0
                        if not pos["trailing_active"] and peak_pnl >= trail_act_pct:
                            pos["trailing_active"] = True
                        if pos["trailing_active"]:
                            trail_price = pos["peak_price"] * (1 - trail_sl_pct / 100.0)
                            if lo <= trail_price:
                                trailing_exit = True
                                trail_exit_price = trail_price
                    else:
                        if lo < pos["trough_price"]:
                            pos["trough_price"] = lo
                        trough_pnl = ((entry - lo) / entry) * 100 if entry else 0
                        if not pos["trailing_active"] and trough_pnl >= trail_act_pct:
                            pos["trailing_active"] = True
                        if pos["trailing_active"]:
                            trail_price = pos["trough_price"] * (1 + trail_sl_pct / 100.0)
                            if hi >= trail_price:
                                trailing_exit = True
                                trail_exit_price = trail_price

                # Determine exit action
                if intrabar_stop_exit:
                    to_close.append((pos_sym, stop_exit_price, f"stop_loss_{sl_pct}%"))
                elif trailing_exit:
                    to_close.append((pos_sym, trail_exit_price, "trailing_stop"))
                else:
                    # 6-rule exit review on close
                    pos_review = {
                        "symbol": pos_sym,
                        "side": side,
                        "entry_price": entry,
                        "current_price": px,
                    }
                    ind_data = scans.get(pos_sym, {})
                    if not ind_data and pos_sym in precomputed:
                        idx = ts_to_idx_map[pos_sym].get(sim_ts)
                        if idx is not None and idx >= _MIN_BARS:
                            ind_data = scan_core.deep_scan_from_precomputed(pos_sym, precomputed[pos_sym], idx, self.params)

                    review = scan_core.review_position_from_indicators(
                        pos_review, self.params, pos["cycles_flat"], ind_data, pos.get("bars_held", 0)
                    )
                    if review["verdict"] == "EXIT":
                        to_close.append((pos_sym, px, review["exit_reason"] or "exit_rule"))
                    else:
                        # Update cycles_flat and bars_held
                        pos["bars_held"] = pos.get("bars_held", 0) + 1
                        pnl_for_stagnation = ((px - entry) / entry * 100) if side == "long" else ((entry - px) / entry * 100)
                        if abs(pnl_for_stagnation) < stagnation_threshold:
                            pos["cycles_flat"] += 1
                        else:
                            pos["cycles_flat"] = 0

            # Process closes
            for close_sym, exit_px, exit_reason in to_close:
                pos = positions[close_sym]
                self._close_position(pos, exit_px, sim_ts, exit_reason, closed_trades)
                slip = self.slippage_bps / 10000.0
                exit_fill = exit_px * (1 - slip if pos["side"] == "long" else 1 + slip)
                if pos["side"] == "long":
                    pnl = (exit_fill - pos["entry_price"]) * pos["qty"]
                    cash += pos["qty"] * exit_fill
                else:
                    pnl = (pos["entry_price"] - exit_fill) * pos["qty"]
                    cash -= pos["qty"] * exit_fill

                if pnl > 0:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1

                reentry_cooldown[close_sym] = reentry_cooldown_cycles
                del positions[close_sym]

            # ── Switch logic (only when single-position) ──────────────
            if max_positions == 1 and len(positions) == 1 and switch_threshold_pct > 0 and scans:
                pos_sym = next(iter(positions))
                pos = positions[pos_sym]
                px = price_lookup.get(pos_sym, pos["entry_price"])
                side = pos["side"]
                entry = pos["entry_price"]
                ranked = self._rank_setups(scans, consecutive_losses)
                if ranked:
                    best = ranked[0]
                    entry_score = pos["entry_score"]
                    if entry_score > 0:
                        improvement = ((best["score"] - entry_score) / entry_score) * 100
                        can_switch = True
                        if switch_require_profitable:
                            if side == "long":
                                can_switch = px > entry
                            else:
                                can_switch = px < entry
                        if improvement > switch_threshold_pct and can_switch and best["symbol"] != pos_sym:
                            self._close_position(pos, px, sim_ts, f"switch_to_{best['symbol']}", closed_trades)
                            slip = self.slippage_bps / 10000.0
                            exit_fill = px * (1 - slip if side == "long" else 1 + slip)
                            if side == "long":
                                pnl = (exit_fill - entry) * pos["qty"]
                                cash += pos["qty"] * exit_fill
                            else:
                                pnl = (entry - exit_fill) * pos["qty"]
                                cash -= pos["qty"] * exit_fill

                            if pnl > 0:
                                consecutive_losses = 0
                            else:
                                consecutive_losses += 1

                            reentry_cooldown[pos_sym] = reentry_cooldown_cycles
                            del positions[pos_sym]

                            # Enter new setup
                            new_sym = best["symbol"]
                            new_px = price_lookup.get(new_sym, best.get("price", 0))
                            if new_px > 0 and new_sym not in reentry_cooldown:
                                new_pos = self._enter_position(
                                    new_sym, best, new_px, sim_ts,
                                    cash * per_pos_fraction, current_equity, consecutive_losses,
                                )
                                if new_pos:
                                    cost = new_pos["qty"] * new_pos["entry_price"]
                                    if new_pos["side"] == "long":
                                        cash -= cost
                                    else:
                                        cash += cost
                                    positions[new_sym] = new_pos

            # ── Goal halt check ──────────────────────────────────────
            if self.goal_active and goal_can_open:
                pnl_dollars = current_equity - self.initial_capital
                if self.goal_target is not None and pnl_dollars >= self.goal_target:
                    goal_can_open = False
                    goal_status = "achieved"
                    goal_halt_ts = sim_ts
                    goal_reason = f"target_reached_${pnl_dollars:.2f}"
                elif self.goal_max_loss is not None and pnl_dollars <= -self.goal_max_loss:
                    goal_can_open = False
                    goal_status = "max_loss_hit"
                    goal_halt_ts = sim_ts
                    goal_reason = f"max_loss_hit_${pnl_dollars:.2f}"

            # ── Entry logic (fill up to max_positions) ────────────────
            available_slots = max_positions - len(positions)
            if available_slots > 0 and scans and goal_can_open:
                ranked = self._rank_setups(scans, consecutive_losses)
                for best in ranked:
                    if available_slots <= 0:
                        break
                    sym = best["symbol"]
                    if sym in positions or sym in reentry_cooldown:
                        continue
                    px = price_lookup.get(sym, best.get("price", 0))
                    if px <= 0:
                        continue
                    # Allocate capital per slot
                    alloc = current_equity * per_pos_fraction
                    new_pos = self._enter_position(
                        sym, best, px, sim_ts,
                        alloc, current_equity, consecutive_losses,
                    )
                    if new_pos:
                        cost = new_pos["qty"] * new_pos["entry_price"]
                        if new_pos["side"] == "long":
                            cash -= cost
                        else:
                            cash += cost
                        positions[sym] = new_pos
                        available_slots -= 1

            # ── Record equity ────────────────────────────────────
            equity = cash
            for pos_sym, pos in positions.items():
                px = price_lookup.get(pos_sym, pos["entry_price"])
                if pos["side"] == "long":
                    equity += pos["qty"] * px
                else:
                    equity -= pos["qty"] * px
            equity_curve.append({"date": sim_ts, "equity": round(equity, 2)})

        # 5. Close any remaining open positions at the final simulated bar.
        # Do not use the last fetched row: it may be outside the requested range.
        slip = self.slippage_bps / 10000.0
        for pos_sym, pos in list(positions.items()):
            final_idx = ts_to_idx_map.get(pos_sym, {}).get(actual_end)
            px = (
                float(historical[pos_sym].iloc[final_idx]["Close"])
                if final_idx is not None
                else pos["entry_price"]
            )
            exit_fill = px * (1 - slip if pos["side"] == "long" else 1 + slip)

            self._close_position(
                pos, px, actual_end,
                "Backtest end — position auto-closed", closed_trades,
            )
            if pos["side"] == "long":
                cash += pos["qty"] * exit_fill
            else:
                cash -= pos["qty"] * exit_fill
            del positions[pos_sym]

        final_equity = cash
        periods_per_year = _BARS_PER_DAY.get(self.interval, 1) * 252.0

        report = BacktestReport.calculate_metrics(
            agent_name="BlitzTrader",
            symbols=self.symbols,
            start_date=actual_start,
            end_date=actual_end,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=equity_curve,
            trades=closed_trades,
            interval=self.interval,
            slippage_bps=self.slippage_bps,
            periods_per_year=periods_per_year,
        )
        if self.goal_active:
            report.goal_simulation = {
                "target_amount": self.goal_target,
                "max_loss": self.goal_max_loss,
                "status": goal_status,
                "halt_timestamp": goal_halt_ts,
                "halt_reason": goal_reason,
                "final_pnl": round(final_equity - self.initial_capital, 2),
                "goal_achieved": goal_status == "achieved",
                "trades_before_halt": len([t for t in closed_trades if goal_halt_ts is None or t.entry_date <= goal_halt_ts]),
            }
        return report

    # ─── Helpers ────────────────────────────────────────────────────

    def _rank_setups(self, scans: dict[str, dict], consecutive_losses: int) -> list[dict]:
        """Rank qualifying setups by composite score, applying consecutive-loss filter."""
        min_signals = self._min_signals_for_entry(consecutive_losses)
        min_families = self.params.get("entry_criteria", {}).get("min_signal_families", 2)
        min_vol = self.params.get("entry_criteria", {}).get("min_vol_ratio", 1.5)

        ranked = []
        for sym, data in scans.items():
            if not data.get("qualifies_for_entry"):
                continue
            # Apply raised bar after consecutive losses
            sig_count = max(data.get("signal_count", {}).get("bullish", 0), data.get("signal_count", {}).get("bearish", 0))
            if consecutive_losses >= self.params.get("position_sizing", {}).get("consecutive_loss_threshold", 3):
                if sig_count < min_signals:
                    continue
            ranked.append({
                "symbol": sym,
                "score": data.get("composite_score", 0),
                "direction": data.get("entry_direction", "long"),
                "price": data.get("price", 0),
            })
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def _enter_position(
        self,
        symbol: str,
        setup: dict,
        price: float,
        ts: str,
        cash: float,
        equity: float,
        consecutive_losses: int,
    ) -> Optional[dict]:
        """Create a new position with goal-aware sizing and slippage."""
        size_pct, is_final_stretch = self._sizing_pct(equity, consecutive_losses)
        notional = equity * (size_pct / 100.0)

        # Apply dollar cap if configured
        ps_cfg = self.params.get("position_sizing", {})
        max_dollar = ps_cfg.get("max_position_dollar_cap")
        if max_dollar is not None and notional > max_dollar:
            notional = max_dollar

        # Apply slippage buffer
        slip_buffer = ps_cfg.get("slippage_buffer_pct", 0.1) / 100.0
        side = setup.get("direction", "long")

        if side == "long":
            fill_price = price * (1 + slip_buffer)
        else:
            fill_price = price * (1 - slip_buffer)

        # Also apply configured slippage bps
        slip_bps = self.slippage_bps / 10000.0
        if side == "long":
            fill_price *= (1 + slip_bps)
        else:
            fill_price *= (1 - slip_bps)

        if fill_price <= 0:
            return None

        qty = notional / fill_price
        if qty <= 0:
            return None

        # Determine take-profit threshold for final stretch
        exit_cfg = self.params.get("exit_rules", {})
        if is_final_stretch:
            tp_pct = exit_cfg.get("take_profit_pct", 2.0)  # final_stretch_tp_pct handled at exit review
        else:
            tp_pct = exit_cfg.get("take_profit_pct", 2.0)

        return {
            "symbol": symbol,
            "side": side,
            "entry_price": fill_price,
            "entry_date": ts,
            "qty": qty,
            "entry_score": setup.get("score", 0),
            "cycles_flat": 0,
            "bars_held": 0,
            "peak_price": fill_price,
            "trough_price": fill_price,
            "trailing_active": False,
        }

    def _close_position(
        self,
        pos: dict,
        price: float,
        ts: str,
        reason: str,
        closed_trades: list[TradeRecord],
    ) -> None:
        """Record a closed trade."""
        slip_bps = self.slippage_bps / 10000.0
        side = pos["side"]

        if side == "long":
            fill_price = price * (1 - slip_bps)
            pnl = (fill_price - pos["entry_price"]) * pos["qty"]
        else:
            fill_price = price * (1 + slip_bps)
            pnl = (pos["entry_price"] - fill_price) * pos["qty"]

        pnl_pct = ((fill_price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0
        if side == "short":
            pnl_pct = ((pos["entry_price"] - fill_price) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0

        hold_days, hold_hours = self._hold_span(pos.get("entry_date", ""), ts)

        closed_trades.append(TradeRecord(
            symbol=pos["symbol"],
            side=side,
            entry_date=pos.get("entry_date", ""),
            exit_date=ts,
            entry_price=pos["entry_price"],
            exit_price=fill_price,
            quantity=pos["qty"],
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_days=hold_days,
            hold_hours=hold_hours,
            reason=reason[:200] if reason else "",
        ))

    @staticmethod
    def _hold_span(entry_ts: str, exit_ts: str) -> tuple[int, float]:
        """Return (hold_days, hold_hours) between two ISO-ish timestamps."""
        if not entry_ts or not exit_ts:
            return 0, 0.0
        try:
            d1 = datetime.fromisoformat(entry_ts)
            d2 = datetime.fromisoformat(exit_ts)
        except Exception:
            try:
                d1 = datetime.fromisoformat(entry_ts.split("T")[0])
                d2 = datetime.fromisoformat(exit_ts.split("T")[0])
            except Exception:
                return 0, 0.0
        delta = d2 - d1
        return delta.days, round(delta.total_seconds() / 3600.0, 2)

    def _empty_report(self) -> BacktestReport:
        return BacktestReport.calculate_metrics(
            agent_name="BlitzTrader",
            symbols=self.symbols,
            start_date=self.start_date or "N/A",
            end_date=self.end_date or "N/A",
            initial_capital=self.initial_capital,
            final_equity=self.initial_capital,
            equity_curve=[],
            trades=[],
            interval=self.interval,
            slippage_bps=self.slippage_bps,
        )
