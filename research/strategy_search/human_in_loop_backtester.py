#!/usr/bin/env python3
"""Backtest the Fence Bar strategy with the four StockBoy human-in-the-loop
supervisor decisions applied deterministically.

This lets us measure the value of the detectors on the same 5m bar data the
live system would see, instead of relying on the 16-trade MFE hand-waving.
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
sys.path.insert(0, str(AGENTS_DIR))

import pandas as pd

from fence_bar_backtester import FenceBarBacktester
from backtest_report import BacktestReport, TradeRecord

logger = logging.getLogger(__name__)


class HumanInLoopBacktester(FenceBarBacktester):
    """FenceBarBacktester with the four StockBoy supervisor decisions.

    Decisions:
      1. Vol filter override: on catalyst days (earnings, big gap, high VIX)
         lower the ATR threshold from 1.8% to 1.2%.
      2. Entry veto: skip a signal if the fence bar lacks volume/spread quality.
      3. Breakeven stop: once MFE >= 0.5% stalls 15min, raise stop to entry.
      4. Early exit: once MFE >= 0.5% stalls 30min and drifts back, close.
    """

    @property
    def agent_name(self) -> str:
        return "Fence Bar + HITL"

    def __init__(self, *args, hitl_enabled: dict[str, bool] | None = None, hitl_thresholds: dict[str, float] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hitl = hitl_enabled or {
            "vol_override": True,
            "entry_veto": True,
            "breakeven": True,
            "early_exit": True,
        }
        self.hitl_thresholds = hitl_thresholds or {
            "mfe_breakeven_pct": 0.5,
            "mfe_breakeven_entry_minutes": 10,
            "mfe_breakeven_stall_minutes": 15,
            "mfe_early_pct": 0.5,
            "mfe_early_stall_minutes": 30,
            "mfe_early_after_time": "11:00",
        }
        self._catalyst_dates: set[str] | None = None
        self._spy_daily: pd.DataFrame | None = None

    # ── Decision 1: vol filter override ──────────────────────────────────

    def _vol_filter_passes(self, date) -> bool:
        if not self.hitl["vol_override"]:
            return super()._vol_filter_passes(date)

        cfg = self.params.get("vol_filter", {})
        if not cfg.get("enabled", True):
            return True

        base_atr = float(cfg.get("spy_atr_threshold", 1.8))
        base_vol = float(cfg.get("spy_vol_threshold", 1.0))
        override_atr = base_atr * 0.667  # 1.8 -> 1.2
        override = self._catalyst_day(date)

        if self._vol_map is None:
            self._vol_map = self._build_vol_map()
        info = self._vol_map.get(date)
        if info is None:
            return False

        if override:
            atr_ok = info["atr20"] >= override_atr
        else:
            atr_ok = info["atr20"] >= base_atr
        return info["vol20"] >= base_vol and atr_ok

    def _catalyst_day(self, date) -> bool:
        """Approximate catalyst detector from backtest data: big SPY gap or
        any symbol in the universe gapping > 3%."""
        if self._spy_daily is None:
            self._spy_daily = self._load_spy_daily()

        row = self._spy_daily[self._spy_daily["Date"] == date]
        if not row.empty:
            gap = abs(float(row.iloc[0].get("gap_pct", 0)))
            if gap > 1.5:
                return True
            # Very high-vol day counts as a catalyst
            if float(row.iloc[0].get("Vol20", 0)) > 2.0:
                return True
        return False

    def _load_spy_daily(self) -> pd.DataFrame:
        start = (datetime.fromisoformat(self.start_date) - timedelta(days=40)).strftime("%Y-%m-%d") if self.start_date else "2024-09-01"
        spy = self.provider.history("SPY", interval="1d", start=start, end=self.end_date or "2026-08-11")
        if spy is None or spy.empty:
            return pd.DataFrame()
        spy = spy.reset_index() if spy.index.name else spy
        col = "Datetime" if "Datetime" in spy.columns else "Date"
        spy[col] = pd.to_datetime(spy[col])
        spy["Date"] = spy[col].dt.date
        spy["gap_pct"] = (spy["Open"] - spy["Close"].shift(1)) / spy["Close"].shift(1) * 100
        spy["Vol20"] = spy["Close"].pct_change().rolling(20).std() * 100
        return spy

    # ── Decision 2: entry veto ──────────────────────────────────────────

    def _veto_entry(self, day: pd.DataFrame, signal_index: int, signal) -> tuple[bool, list[str]]:
        if not self.hitl["entry_veto"]:
            return False, []

        # The fence bar is the first bar of the session (09:30-09:35)
        fence_bar = day.iloc[0]
        fence_volume = float(fence_bar["Volume"])

        # Average 5m volume over the prior 3 days for the same symbol
        avg_vol = self._avg_5m_volume(signal.symbol, fence_bar["Timestamp"])
        volume_ratio = fence_volume / avg_vol if avg_vol and avg_vol > 0 else 1.0

        # Close position in fence bar (the first 5m bar of the session)
        h = float(fence_bar["High"])
        l = float(fence_bar["Low"])
        c = float(fence_bar["Close"])
        close_pos = (c - l) / (h - l) if h > l else 0.5

        reasons = []
        if volume_ratio < 1.5:
            reasons.append(f"fence_volume_ratio={volume_ratio:.2f}")

        if signal.side == "long" and close_pos < 0.60:
            reasons.append(f"long_close_pos={close_pos:.2f}")
        elif signal.side == "short" and close_pos > 0.40:
            reasons.append(f"short_close_pos={close_pos:.2f}")

        return bool(reasons), reasons

    def _avg_5m_volume(self, symbol: str, timestamp: pd.Timestamp) -> float:
        """Average 5m first-bar volume over the prior 3 trading days."""
        try:
            start = (timestamp - timedelta(days=10)).strftime("%Y-%m-%d")
            end = timestamp.strftime("%Y-%m-%d")
            frame = self.provider.history(symbol, interval="5m", start=start, end=end)
            if frame is None or frame.empty:
                return 0.0
            frame = frame.copy().reset_index()
            time_col = "Datetime" if "Datetime" in frame.columns else "Date"
            frame[time_col] = pd.to_datetime(frame[time_col])
            if getattr(frame[time_col].dt, "tz", None) is not None:
                frame[time_col] = frame[time_col].dt.tz_convert("America/New_York").dt.tz_localize(None)
            else:
                frame[time_col] = frame[time_col].dt.tz_localize(None)
            frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0)
            # First bar of each day
            first_bars = frame.groupby(frame[time_col].dt.date).first()
            # Take the last 3 days, excluding the current day
            past = [d for d in first_bars.index if d < timestamp.date()][-3:]
            if not past:
                return 0.0
            return float(first_bars.loc[past, "Volume"].mean())
        except Exception as e:
            logger.debug("_avg_5m_volume failed for %s: %s", symbol, e)
            return 0.0

    # ── Decisions 3 & 4: breakeven stop + early exit ────────────────────

    def _check_exit(self, position: dict, bar: pd.Series, timestamp, params: dict) -> tuple[float | None, str]:
        side = position["side"]
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])

        # Track MFE since entry
        if "mfe" not in position:
            position["mfe"] = position["entry_price"]
            position["mfe_ts"] = position["entry_timestamp"]
            position["be_triggered"] = False
            position["stop_breakeven"] = False

        if side == "long":
            if high > position["mfe"]:
                position["mfe"] = high
                position["mfe_ts"] = timestamp
        else:
            if low < position["mfe"]:
                position["mfe"] = low
                position["mfe_ts"] = timestamp

        mfe_pct = abs(position["mfe"] - position["entry_price"]) / position["entry_price"] * 100
        minutes_since_entry = (timestamp - position["entry_timestamp"]).total_seconds() / 60
        minutes_since_mfe = (timestamp - position["mfe_ts"]).total_seconds() / 60

        # Breakeven stop (Decision 3)
        if self.hitl["breakeven"] and not position["stop_breakeven"]:
            if (mfe_pct >= self.hitl_thresholds["mfe_breakeven_pct"]
                    and minutes_since_entry >= self.hitl_thresholds["mfe_breakeven_entry_minutes"]
                    and minutes_since_mfe >= self.hitl_thresholds["mfe_breakeven_stall_minutes"]):
                position["stop_breakeven"] = True
                position["stop"] = position["entry_price"]
                logger.debug("HITL: moved stop to breakeven at %s", position["entry_price"])

        # Early exit (Decision 4)
        if self.hitl["early_exit"]:
            if (mfe_pct >= self.hitl_thresholds["mfe_early_pct"]
                    and minutes_since_mfe >= self.hitl_thresholds["mfe_early_stall_minutes"]
                    and self._drifting_back(position, close, side)):
                after_time = datetime.strptime(self.hitl_thresholds["mfe_early_after_time"], "%H:%M").time()
                if timestamp.time() >= after_time:
                    logger.debug("HITL: early exit at %s", close)
                    return close, "early_exit"

        # Let the base exit logic run with possibly modified stop
        return super()._check_exit(position, bar, timestamp, params)

    def _drifting_back(self, position: dict, close: float, side: str) -> bool:
        """Return True if the last close is closer to entry than to MFE."""
        mfe = position["mfe"]
        entry = position["entry_price"]
        dist_to_mfe = abs(mfe - close)
        dist_to_entry = abs(close - entry)
        return dist_to_entry < dist_to_mfe

    # ── Main run loop injection ─────────────────────────────────────────

    def run(self) -> BacktestReport:
        # Override run to inject entry-veto logic at signal time.
        # Copy the base run() and insert veto check where the signal is taken.
        premarket_enabled = bool(self.params.get("premarket", {}).get("enabled", False))
        if premarket_enabled:
            return self._run_premarket()
        return self._run_standard()

    def _run_standard(self) -> BacktestReport:
        frames = {sym: self._fetch(sym) for sym in self.symbols}
        frames = {sym: f for sym, f in frames.items() if f is not None and not f.empty}
        if not frames:
            return BacktestReport.calculate_metrics(
                agent_name=self.agent_name, symbols=self.symbols, start_date=self.start_date,
                end_date=self.end_date, initial_capital=self.initial_capital,
                final_equity=self.initial_capital, equity_curve=[], trades=[], interval="5m",
                slippage_bps=self.slippage_bps, periods_per_year=252 * 78,
                diagnostics={"error": "no historical data"},
            )

        all_dates = sorted({d for f in frames.values() for d in f["Timestamp"].dt.date})
        cash = self.initial_capital
        equity_curve: list[dict] = []
        trades: list[TradeRecord] = []
        diagnostics = {
            "sessions": 0, "vol_filtered": 0, "selected_sessions": 0,
            "entries": 0, "entries_vetoed": 0, "no_symbol": 0,
            "breakeven_stops": 0, "early_exits": 0,
        }
        actual_start = all_dates[0].isoformat()
        actual_end = all_dates[-1].isoformat()

        for date in all_dates:
            diagnostics["sessions"] += 1
            if not self._vol_filter_passes(date):
                diagnostics["vol_filtered"] += 1
                continue

            symbol = self._choose_symbol(frames, date)
            if not symbol:
                diagnostics["no_symbol"] += 1
                continue
            diagnostics["selected_sessions"] += 1
            day = frames[symbol][frames[symbol]["Timestamp"].dt.date == date].reset_index(drop=True)
            strategy = self.create_strategy(symbol, date=date, day=day)
            position = None

            for index, bar in day.iterrows():
                timestamp = bar["Timestamp"]
                if position is not None and index > position["entry_index"]:
                    position["bars_held"] = position.get("bars_held", 0) + 1
                    exit_price, reason = self._check_exit(position, bar, timestamp, self.params)
                    if reason == "early_exit":
                        diagnostics["early_exits"] += 1
                    if "stop_breakeven" in position and position.get("stop_breakeven"):
                        diagnostics["breakeven_stops"] += 1
                    if exit_price is not None:
                        side = position["side"]
                        fill = self._fill(exit_price, side, False)
                        gross = ((fill - position["entry_price"]) if side == "long" else (position["entry_price"] - fill)) * position["quantity"]
                        exit_fee = abs(fill * position["quantity"]) * self.fee_rate
                        pnl = gross - position["entry_fee"] - exit_fee
                        cash += (fill * position["quantity"] if side == "long" else -fill * position["quantity"]) - exit_fee
                        hold_hours = (timestamp - position["entry_timestamp"]).total_seconds() / 3600
                        trades.append(TradeRecord(
                            symbol=symbol, side=side, entry_date=str(position["entry_timestamp"]),
                            exit_date=str(timestamp), entry_price=position["entry_price"],
                            exit_price=fill, quantity=position["quantity"], pnl=pnl,
                            pnl_pct=pnl / (position["entry_price"] * position["quantity"]) * 100,
                            hold_days=int(hold_hours // 24), hold_hours=hold_hours, reason=reason,
                        ))
                        position = None

                signal = strategy.on_bar(timestamp, bar, index)
                if signal is not None and position is None:
                    veto, veto_reasons = self._veto_entry(day, index, signal)
                    if veto:
                        diagnostics["entries_vetoed"] += 1
                        logger.debug("HITL vetoed entry: %s", veto_reasons)
                        continue
                    entry = self._fill(signal.entry_price, signal.side, True)
                    risk_budget = cash * float(self.params["risk"]["risk_per_trade_pct"]) / 100
                    quantity = risk_budget / signal.risk_per_share
                    quantity = min(quantity, cash * 0.25 / entry)
                    if quantity > 0:
                        entry_fee = entry * quantity * self.fee_rate
                        cash -= entry * quantity + entry_fee if signal.side == "long" else -entry * quantity + entry_fee
                        position = {
                            "side": signal.side, "entry_price": entry, "stop": signal.stop_price,
                            "target": signal.target_price, "quantity": quantity, "entry_fee": entry_fee,
                            "entry_timestamp": timestamp, "entry_index": index,
                        }
                        diagnostics["entries"] += 1

                marked = cash
                if position is not None:
                    close_px = float(bar["Close"])
                    marked += position["quantity"] * close_px if position["side"] == "long" else -position["quantity"] * close_px
                equity_curve.append({"date": str(timestamp), "equity": round(marked, 2)})

            if position is not None:
                bar = day.iloc[-1]
                fill = self._fill(float(bar["Close"]), position["side"], False)
                gross = ((fill - position["entry_price"]) if position["side"] == "long" else (position["entry_price"] - fill)) * position["quantity"]
                exit_fee = abs(fill * position["quantity"]) * self.fee_rate
                pnl = gross - position["entry_fee"] - exit_fee
                cash += (fill * position["quantity"] if position["side"] == "long" else -fill * position["quantity"]) - exit_fee
                hold_hours = (bar["Timestamp"] - position["entry_timestamp"]).total_seconds() / 3600
                trades.append(TradeRecord(
                    symbol=position.get("symbol", symbol), side=position["side"], entry_date=str(position["entry_timestamp"]),
                    exit_date=str(bar["Timestamp"]), entry_price=position["entry_price"], exit_price=fill,
                    quantity=position["quantity"], pnl=pnl,
                    pnl_pct=pnl / (position["entry_price"] * position["quantity"]) * 100,
                    hold_days=int(hold_hours // 24), hold_hours=hold_hours, reason="session_end",
                ))

        report = BacktestReport.calculate_metrics(
            agent_name=self.agent_name, symbols=self.symbols, start_date=actual_start,
            end_date=actual_end, initial_capital=self.initial_capital, final_equity=cash,
            equity_curve=equity_curve, trades=trades, interval="5m",
            slippage_bps=self.slippage_bps, periods_per_year=252 * 78, diagnostics=diagnostics,
        )
        report.diagnostics["avg_r"] = round(sum(t.pnl for t in trades) / max(1, len(trades)), 2)
        return report

    def _run_premarket(self) -> BacktestReport:
        # Premarket path unchanged — HITL decisions still fire through run loop.
        return super().run()
