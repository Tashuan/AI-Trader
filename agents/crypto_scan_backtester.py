"""Historical replay for CryptoRunner's crypto-specific scan and exits."""

from __future__ import annotations

import copy
import logging
from dataclasses import replace
import math
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import crypto_scan_core as core
from backtest_report import BacktestReport, TradeRecord
from execution_simulator import FillConfig, simulate_entry, simulate_exit
from strategy_registry import position_notional
from arena_market_data import ArenaMarketDataProvider, get_arena_market_data

logger = logging.getLogger(__name__)


class CryptoScanBacktester:
    """Replay CryptoRunner's 4h scan, gates, protective exits, and sizing."""

    def __init__(
        self,
        symbols: list[str],
        params: dict,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 10000.0,
        interval: str = "4h",
        slippage_bps: float = 5.0,
        fee_rate: float = 0.001,
        provider: ArenaMarketDataProvider | None = None,
        goal_target: float | None = None,
        goal_max_loss: float | None = None,
        fill_config: FillConfig | None = None,
    ):
        self.symbols = symbols
        self.params = copy.deepcopy(params)
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.interval = interval or "4h"
        self.slippage_bps = slippage_bps
        self.fee_rate = fee_rate
        self.fill_config = fill_config or FillConfig.from_legacy(
            slippage_bps=slippage_bps, fee_rate=fee_rate,
            market="crypto", interval=self.interval,
        )
        self.provider = provider or get_arena_market_data()
        self.goal_target = goal_target
        self.goal_max_loss = goal_max_loss
        self.goal_active = goal_target is not None or goal_max_loss is not None

    def _fetch(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            start = datetime.fromisoformat(self.start_date) - timedelta(days=45) if self.start_date else None
            end = datetime.fromisoformat(self.end_date) + timedelta(days=1) if self.end_date else None
            kwargs = {"auto_adjust": False, "raise_errors": False}
            if start:
                kwargs["start"] = start.strftime("%Y-%m-%d")
            else:
                kwargs["period"] = "3mo"
            if end:
                kwargs["end"] = end.strftime("%Y-%m-%d")
            df = self.provider.history(core.yf_ticker(symbol), interval=self.interval, **kwargs)
        except Exception as exc:
            logger.warning("Failed to fetch historical data for %s: %s", symbol, exc)
            return None
        if df is None or df.empty:
            logger.warning("No historical data returned for %s (interval=%s, start=%s, end=%s)",
                           symbol, self.interval, self.start_date, self.end_date)
            return None
        df = df.reset_index()
        col = "Datetime" if "Datetime" in df.columns else "Date"
        df[col] = pd.to_datetime(df[col], utc=True)
        return df.sort_values(col).reset_index(drop=True)

    @staticmethod
    def _time_col(df: pd.DataFrame) -> str:
        return "Datetime" if "Datetime" in df.columns else "Date"

    def _daily_gate(self, symbol: str, ts, daily: dict[str, pd.DataFrame], direction: str) -> bool:
        cfg = self.params.get("entry_criteria", {})
        if not cfg.get("require_daily_trend_agreement", True):
            return True
        frame = daily.get(symbol)
        if frame is None or frame.empty:
            return False
        col = self._time_col(frame)
        prior = frame[frame[col] < pd.Timestamp(ts).normalize()]
        if len(prior) < 21:
            return False
        close = float(prior["Close"].iloc[-1])
        sma20 = float(prior["Close"].rolling(20).mean().iloc[-1])
        return close > sma20 if direction == "long" else close < sma20

    def _btc_regime(self, ts, daily: dict[str, pd.DataFrame]) -> str:
        frame = daily.get("BTC")
        if frame is None or frame.empty:
            return "neutral"
        col = self._time_col(frame)
        prior = frame[frame[col] < pd.Timestamp(ts).normalize()]
        if len(prior) < 22:
            return "neutral"
        close = float(prior["Close"].iloc[-1])
        ema21 = float(prior["Close"].ewm(span=21).mean().iloc[-1])
        return "bullish" if close > ema21 else "bearish"

    def _scan(self, symbol: str, frame: pd.DataFrame, idx: int, daily: dict[str, pd.DataFrame]) -> dict:
        window = frame.iloc[: idx + 1].tail(540)
        result = core.deep_scan_symbol_from_df(symbol, window, self.params)
        if not result.get("qualifies_for_entry"):
            return result
        ts = window.iloc[-1][self._time_col(window)]
        direction = result.get("entry_direction", "long")
        cfg = self.params.get("entry_criteria", {})
        if not self._daily_gate(symbol, ts, daily, direction):
            result["qualifies_for_entry"] = False
            result["entry_veto_reason"] = "daily_trend_disagreement"
        elif symbol != "BTC" and cfg.get("require_btc_regime_ok_for_alts", True):
            regime = core.classify_regime(daily.get("BTC"), ts, self.params)
            result["btc_regime"] = regime
            allowed, reason = core.regime_filter_entry(symbol, direction, regime, self.params)
            if not allowed:
                result["qualifies_for_entry"] = False
                result["entry_veto_reason"] = reason
        elif symbol == "BTC" and cfg.get("btc_self_filter", True):
            regime = core.classify_regime(daily.get("BTC"), ts, self.params)
            result["btc_regime"] = regime
            allowed, reason = core.regime_filter_entry(symbol, direction, regime, self.params)
            if not allowed:
                result["qualifies_for_entry"] = False
                result["entry_veto_reason"] = reason
        if result.get("qualifies_for_entry"):
            min_adv = float(cfg.get("min_avg_dollar_volume", 500000))
            adv = float((window["Close"] * window["Volume"]).mean())
            if adv < min_adv:
                result["qualifies_for_entry"] = False
                result["entry_veto_reason"] = "liquidity_floor_not_met"
        return result

    def run(self) -> BacktestReport:
        historical: dict[str, pd.DataFrame] = {}
        daily: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            frame = self._fetch(symbol)
            if frame is None:
                continue
            historical[symbol] = frame
            daily_frame = self._fetch_daily(symbol)
            if daily_frame is not None:
                daily[symbol] = daily_frame
        if not historical:
            return self._empty_report()

        columns = {s: self._time_col(frame) for s, frame in historical.items()}
        indexes = {s: {ts: i for i, ts in enumerate(frame[columns[s]])} for s, frame in historical.items()}
        timeline = sorted(set().union(*(set(indexes[s]) for s in historical)))
        if self.start_date:
            timeline = [ts for ts in timeline if ts >= pd.Timestamp(self.start_date, tz="UTC")]
        if self.end_date:
            timeline = [ts for ts in timeline if ts <= pd.Timestamp(self.end_date, tz="UTC") + pd.Timedelta(days=1)]
        if not timeline:
            return self._empty_report()

        params = copy.deepcopy(self.params)
        exit_cfg = params.setdefault("exit_rules", {})
        bars_per_day = 6 if self.interval == "4h" else 24
        exit_cfg["_stagnation_cycles"] = max(1, round(exit_cfg.get("stagnation_hours", 8) / 24 * bars_per_day))
        exit_cfg["_momentum_death_grace_bars"] = max(1, round(exit_cfg.get("momentum_death_grace_hours", 32) / 24 * bars_per_day))
        max_positions = int(params.get("position_sizing", {}).get("max_positions", 3))
        cash = self.initial_capital
        positions: dict[str, dict] = {}
        cooldown: dict[str, int] = {}
        losses = 0
        trades: list[TradeRecord] = []
        curve: list[dict] = []

        # Goal state — tracks whether the agent would halt in live mode
        goal_can_open = True
        goal_status = "active"
        goal_halt_ts = None
        goal_reason = None

        for ts in timeline:
            for symbol in list(cooldown):
                cooldown[symbol] -= 1
                if cooldown[symbol] <= 0:
                    del cooldown[symbol]
            prices: dict[str, float] = {}
            highs: dict[str, float] = {}
            lows: dict[str, float] = {}
            current_bars: dict[str, object] = {}
            scans: dict[str, dict] = {}
            for symbol, frame in historical.items():
                idx = indexes[symbol].get(ts)
                if idx is None:
                    continue
                row = frame.iloc[idx]
                current_bars[symbol] = row
                prices[symbol] = float(row["Close"])
                highs[symbol] = float(row["High"])
                lows[symbol] = float(row["Low"])
                if idx >= 30:
                    scans[symbol] = self._scan(symbol, frame, idx, daily)

            for symbol, pos in list(positions.items()):
                if symbol not in prices:
                    continue
                exit_px, reason = self._protective_exit(pos, highs[symbol], lows[symbol])
                if exit_px is None:
                    review = core.review_position_from_indicators(
                        {"symbol": symbol, "side": pos["side"], "entry_price": pos["entry"], "current_price": prices[symbol]},
                        params,
                        pos["flat"],
                        scans.get(symbol, {}),
                        pos["held"],
                    )
                    if review["verdict"] == "EXIT":
                        exit_px, reason = prices[symbol], review["exit_reason"] or "rule_exit"
                    else:
                        pos["held"] += 1
                        pnl_pct = self._pnl_pct(pos, prices[symbol])
                        threshold = float(exit_cfg.get("stagnation_threshold_pct", 1.0))
                        pos["flat"] = pos["flat"] + 1 if abs(pnl_pct) < threshold else 0
                if exit_px is not None:
                    cash, close_pnl, filled_qty = self._close(
                        pos, exit_px, ts, cash, trades, reason, current_bars.get(symbol),
                    )
                    losses = 0 if close_pnl > 0 else losses + 1
                    cooldown[symbol] = max(1, round(params.get("switch_logic", {}).get("reentry_cooldown_hours", 8) / 4))
                    if pos["qty"] <= 1e-12:
                        del positions[symbol]

            equity = self._equity(cash, positions, prices)
            gross = self._gross(positions, prices)

            # Goal checks — simulate live agent's goal-aware behavior
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

            # Final-stretch sizing — reduce position size when >80% to goal
            final_stretch = False
            if self.goal_active and self.goal_target and self.goal_target > 0:
                progress = ((equity - self.initial_capital) / self.goal_target) * 100
                final_stretch = progress > 80.0

            ranked = sorted(
                ((data.get("composite_score", 0), symbol, data) for symbol, data in scans.items()
                 if data.get("qualifies_for_entry") and symbol not in positions and symbol not in cooldown),
                reverse=True,
            )
            minimum = params.get("entry_criteria", {}).get("min_signals", 5)
            if losses >= params.get("position_sizing", {}).get("consecutive_loss_threshold", 3):
                minimum = params.get("position_sizing", {}).get("consecutive_loss_min_signals", minimum)
            for _, symbol, data in ranked:
                if not goal_can_open:
                    break
                if len(positions) >= max_positions:
                    break
                directional = max(data.get("signal_count", {}).get("bullish", 0), data.get("signal_count", {}).get("bearish", 0))
                if directional < minimum:
                    continue
                cor_ok, cor_reason = core.check_correlation_exposure(symbol, positions, params)
                if not cor_ok:
                    continue
                if symbol != "BTC":
                    btc_ok, btc_reason = core.check_btc_slot_reservation(positions, params, max_positions)
                    if not btc_ok:
                        continue
                entry = prices.get(symbol, 0)
                side = data.get("entry_direction", "long")
                if entry <= 0:
                    continue
                stop, target, trail_pct, trail_activation = core.compute_atr_sl_tp(entry, side, data, params)
                stop_distance = abs((stop - entry) / entry) * 100
                notional = position_notional(equity, stop_distance, gross, params)
                # Goal final-stretch sizing — reduce notional when approaching target
                if final_stretch:
                    notional *= 0.5
                if notional <= 0:
                    continue
                requested_qty = notional / entry
                fill = simulate_entry(
                    entry, side, requested_qty, symbol, self.fill_config,
                    current_bars.get(symbol),
                )
                if fill.fill_qty <= 0:
                    continue
                filled_notional = fill.fill_price * fill.fill_qty
                cash -= filled_notional + fill.fee
                positions[symbol] = {
                    "symbol": symbol, "side": side, "entry": fill.fill_price,
                    "qty": fill.fill_qty, "margin": filled_notional,
                    "entry_fee": fill.fee, "stop": stop, "target": target,
                    "trail_pct": trail_pct, "trail_activation": trail_activation,
                    "peak": fill.fill_price, "trough": fill.fill_price,
                    "trail": False, "held": 0, "flat": 0, "entry_date": str(ts),
                }
                gross += filled_notional

            curve.append({"date": str(ts), "equity": round(self._equity(cash, positions, prices), 2)})

        # Close remaining positions at the final simulated bar, not the last
        # fetched row (which may be outside the requested end date).
        final_ts = timeline[-1]
        for symbol, pos in list(positions.items()):
            frame = historical[symbol]
            final_idx = indexes[symbol].get(final_ts)
            price = (
                float(frame.iloc[final_idx]["Close"])
                if final_idx is not None
                else pos["entry"]
            )
            cash, _, filled_qty = self._close(
                pos, price, final_ts, cash, trades, "Backtest end", frame.iloc[final_idx] if final_idx is not None else None, True,
            )
            if pos["qty"] > 1e-12:
                logger.warning("Partial final close for %s: %.6f remaining", symbol, pos["qty"])
        report = BacktestReport.calculate_metrics(
            agent_name="CryptoRunner",
            symbols=self.symbols,
            start_date=str(timeline[0]),
            end_date=str(timeline[-1]),
            initial_capital=self.initial_capital,
            final_equity=cash,
            equity_curve=curve,
            trades=trades,
            interval=self.interval,
            slippage_bps=self.slippage_bps,
            periods_per_year=6 * 365,
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

    def _fetch_daily(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            frame = self.provider.history(core.yf_ticker(symbol), period="1y", interval="1d", auto_adjust=False, raise_errors=False).reset_index()
            if frame is None or frame.empty:
                return None
            col = self._time_col(frame)
            frame[col] = pd.to_datetime(frame[col], utc=True)
            return frame.sort_values(col).reset_index(drop=True)
        except Exception as exc:
            logger.warning("Failed to fetch daily data for %s: %s", symbol, exc)
            return None

    def _protective_exit(self, pos: dict, high: float, low: float) -> tuple[Optional[float], Optional[str]]:
        if pos["side"] == "long":
            pos["peak"] = max(pos["peak"], high)
            if low <= pos["stop"]:
                return pos["stop"], "stop_loss"
            if high >= pos["target"]:
                return pos["target"], "take_profit"
            if not pos["trail"] and (pos["peak"] - pos["entry"]) / pos["entry"] * 100 >= pos["trail_activation"]:
                pos["trail"] = True
            if pos["trail"] and low <= pos["peak"] * (1 - pos["trail_pct"] / 100):
                return pos["peak"] * (1 - pos["trail_pct"] / 100), "trailing_stop"
        else:
            pos["trough"] = min(pos["trough"], low)
            if high >= pos["stop"]:
                return pos["stop"], "stop_loss"
            if low <= pos["target"]:
                return pos["target"], "take_profit"
            if not pos["trail"] and (pos["entry"] - pos["trough"]) / pos["entry"] * 100 >= pos["trail_activation"]:
                pos["trail"] = True
            if pos["trail"] and high >= pos["trough"] * (1 + pos["trail_pct"] / 100):
                return pos["trough"] * (1 + pos["trail_pct"] / 100), "trailing_stop"
        return None, None

    @staticmethod
    def _pnl_pct(pos: dict, price: float) -> float:
        return ((price - pos["entry"]) / pos["entry"] * 100) if pos["side"] == "long" else ((pos["entry"] - price) / pos["entry"] * 100)

    @staticmethod
    def _gross(positions: dict, prices: dict) -> float:
        return sum(pos["qty"] * prices.get(symbol, pos["entry"]) for symbol, pos in positions.items())

    def _equity(self, cash: float, positions: dict, prices: dict) -> float:
        total = cash
        for symbol, pos in positions.items():
            price = prices.get(symbol, pos["entry"])
            pnl = (price - pos["entry"]) * pos["qty"] if pos["side"] == "long" else (pos["entry"] - price) * pos["qty"]
            total += pos["margin"] + pnl
        return total

    def _close(self, pos: dict, price: float, ts, cash: float,
               trades: list[TradeRecord], reason: str, bar=None,
               force_full: bool = False) -> tuple[float, float, float]:
        """Close as much as the shared deterministic fill model permits."""
        config = replace(self.fill_config, enable_partial_fills=False) if force_full else self.fill_config
        result = simulate_exit(
            price, pos["side"], pos["qty"], pos["symbol"], config, bar,
        )
        qty = min(result.fill_qty, pos["qty"])
        if qty <= 0:
            return cash, 0.0, 0.0
        entry_fee = pos.get("entry_fee", 0.0) * qty / pos["qty"]
        gross_pnl = ((result.fill_price - pos["entry"]) if pos["side"] == "long"
                     else (pos["entry"] - result.fill_price)) * qty
        net_pnl = gross_pnl - result.fee - entry_fee
        cash += pos["margin"] * qty / pos["qty"] + gross_pnl - result.fee
        hold_hours = max(0.0, (pd.Timestamp(ts) - pd.Timestamp(pos["entry_date"])).total_seconds() / 3600)
        trades.append(TradeRecord(
            symbol=pos.get("symbol", ""), side=pos["side"], entry_date=pos["entry_date"],
            exit_date=str(ts), entry_price=pos["entry"], exit_price=result.fill_price,
            quantity=qty, pnl=net_pnl,
            pnl_pct=(net_pnl / (pos["margin"] * qty / pos["qty"]) * 100)
            if pos["margin"] else 0,
            hold_days=int(hold_hours // 24), hold_hours=hold_hours, reason=reason,
        ))
        pos["qty"] -= qty
        pos["margin"] = max(0.0, pos["margin"] - pos["margin"] * qty / (pos["qty"] + qty))
        pos["entry_fee"] = max(0.0, pos.get("entry_fee", 0.0) - entry_fee)
        return cash, net_pnl, qty

    def _empty_report(self) -> BacktestReport:
        return BacktestReport.calculate_metrics(
            agent_name="CryptoRunner", symbols=self.symbols,
            start_date=self.start_date or "N/A", end_date=self.end_date or "N/A",
            initial_capital=self.initial_capital, final_equity=self.initial_capital,
            equity_curve=[], trades=[], interval=self.interval, slippage_bps=self.slippage_bps,
        )
