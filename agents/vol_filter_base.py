"""Reusable vol-filtered backtester base for 5m day-trading strategies.

Provides the shared infrastructure that FenceBarBacktester, VWAPMagnetBacktester,
FirstPullbackBacktester, and FakeoutFadeBacktester all use:
  - 5m data fetching via any ArenaMarketDataProvider
  - SPY volatility regime filter (skip low-vol days)
  - Per-session symbol selection (first-bar dollar volume)
  - Position management with slippage + fee modeling
  - Fixed SL/TP exit logic + force exit at session end
  - BacktestReport output via calculate_metrics

Subclasses provide strategy-specific logic by overriding:
  - create_strategy(symbol) -> object with on_bar(timestamp, bar, index) -> EntrySignal | None
  - agent_name (property)
  - default_params (property, for deep_merge with override)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from arena_market_data import ArenaMarketDataProvider, get_arena_market_data
from backtest_report import BacktestReport, TradeRecord
from strategy_lab import deep_merge

logger = logging.getLogger(__name__)

BASE_DEFAULTS: dict[str, Any] = {
    "session": {
        "timezone": "America/New_York",
        "market_open": "09:30",
        "force_exit": "15:55",
    },
    "risk": {
        "risk_per_trade_pct": 0.50,
        "max_trades_per_day": 1,
    },
    "exit": {
        "mode": "fixed_sl_tp",
        "max_bars": 0,
    },
    "vol_filter": {
        "enabled": True,
        "mode": "window",  # "window" = check once at start_date, "day" = check each day
        "spy_vol_threshold": 1.0,
        "spy_atr_threshold": 1.2,
    },
}


class VolFilteredBacktester:
    """Base class for vol-filtered 5m day-trading strategy backtesters."""

    def __init__(
        self,
        symbols: list[str],
        params: dict | None = None,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 100_000.0,
        slippage_bps: float = 5.0,
        fee_rate: float = 0.001,
        provider: ArenaMarketDataProvider | None = None,
    ):
        if not symbols:
            raise ValueError("Backtester requires at least one symbol")
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        base = deep_merge(BASE_DEFAULTS, self.default_params)
        self.params = deep_merge(base, params or {})
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = float(initial_capital)
        self.slippage_bps = float(slippage_bps)
        self.fee_rate = float(fee_rate)
        self.provider = provider or get_arena_market_data()
        self._vol_map: dict | None = None
        self._previous_close_cache: dict[tuple[str, object], float | None] = {}
        self._previous_levels_cache: dict[tuple[str, object], dict[str, float] | None] = {}
        self._spy_5m_cache: pd.DataFrame | None = None

    # ── Subclass hooks ──────────────────────────────────────────

    @property
    def agent_name(self) -> str:
        return "VolFiltered"

    @property
    def default_params(self) -> dict[str, Any]:
        return {}

    def create_strategy(self, symbol: str, date=None, day: pd.DataFrame | None = None):
        """Return a strategy object with on_bar(timestamp, bar, index)."""
        raise NotImplementedError

    def _previous_close(self, symbol: str, date) -> float | None:
        key = (symbol, date)
        if key in self._previous_close_cache:
            return self._previous_close_cache[key]
        try:
            end = pd.Timestamp(date).strftime("%Y-%m-%d")
            start = (pd.Timestamp(date) - timedelta(days=10)).strftime("%Y-%m-%d")
            frame = self.provider.history(symbol, interval="1d", start=start, end=end)
            if frame is None or frame.empty:
                value = None
            else:
                frame = frame.reset_index() if frame.index.name else frame
                frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
                value = float(frame["Close"].dropna().iloc[-1])
        except Exception:
            value = None
        self._previous_close_cache[key] = value
        return value

    def _previous_day_levels(self, symbol: str, date) -> dict[str, float] | None:
        key = (symbol, date)
        if key in self._previous_levels_cache:
            return self._previous_levels_cache[key]
        try:
            end = pd.Timestamp(date).strftime("%Y-%m-%d")
            start = (pd.Timestamp(date) - timedelta(days=10)).strftime("%Y-%m-%d")
            frame = self.provider.history(symbol, interval="1d", start=start, end=end)
            if frame is None or frame.empty:
                value = None
            else:
                frame = frame.reset_index() if frame.index.name else frame
                frame = frame.dropna(subset=["High", "Low", "Close"])
                if frame.empty:
                    value = None
                else:
                    row = frame.iloc[-1]
                    value = {key: float(row[key]) for key in ("High", "Low", "Close")}
        except Exception:
            value = None
        self._previous_levels_cache[key] = value
        return value

    def _spy_opening_return(self, date) -> float | None:
        if self._spy_5m_cache is None:
            self._spy_5m_cache = self._fetch("SPY")
        if self._spy_5m_cache is None:
            return None
        day = self._spy_5m_cache[self._spy_5m_cache["Timestamp"].dt.date == date]
        day = day[(day["Timestamp"].dt.time >= datetime.strptime("09:30", "%H:%M").time()) &
                  (day["Timestamp"].dt.time <= datetime.strptime("09:45", "%H:%M").time())]
        if day.empty or float(day.iloc[0]["Open"]) <= 0:
            return None
        return (float(day.iloc[-1]["Close"]) / float(day.iloc[0]["Open"]) - 1) * 100

    # ── Data fetching (same as FenceBarBacktester._fetch) ────────

    def _fetch(self, symbol: str) -> pd.DataFrame | None:
        try:
            start = self.start_date
            end = (datetime.fromisoformat(self.end_date) + timedelta(days=1)).strftime("%Y-%m-%d") if self.end_date else ""
            if start:
                start = (datetime.fromisoformat(start) - timedelta(days=3)).strftime("%Y-%m-%d")
            kwargs = {"interval": "5m", "auto_adjust": False, "raise_errors": False}
            if start:
                kwargs.update(start=start, end=end)
            else:
                kwargs["period"] = "60d"
            frame = self.provider.history(symbol, **kwargs)
        except Exception as exc:
            logger.warning("Failed to fetch 5m data for %s: %s", symbol, exc)
            return None
        if frame is None or frame.empty:
            return None
        frame = frame.copy().reset_index()
        time_col = "Datetime" if "Datetime" in frame.columns else "Date"
        frame[time_col] = pd.to_datetime(frame[time_col], errors="coerce")
        frame = frame.dropna(subset=[time_col]).rename(columns={time_col: "Timestamp"})
        if getattr(frame["Timestamp"].dt, "tz", None) is not None:
            frame["Timestamp"] = frame["Timestamp"].dt.tz_convert("America/New_York").dt.tz_localize(None)
        else:
            frame["Timestamp"] = frame["Timestamp"].dt.tz_localize(None)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in frame.columns:
                return None
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Timestamp")
        frame = frame[(frame["Timestamp"].dt.time >= datetime.strptime("09:30", "%H:%M").time()) &
                      (frame["Timestamp"].dt.time <= datetime.strptime("16:00", "%H:%M").time())]
        if self.start_date:
            frame = frame[frame["Timestamp"].dt.date >= datetime.fromisoformat(self.start_date).date()]
        if self.end_date:
            frame = frame[frame["Timestamp"].dt.date <= datetime.fromisoformat(self.end_date).date()]
        return frame.reset_index(drop=True)

    # ── Symbol selection (same as FenceBarBacktester._choose_symbol) ──

    @staticmethod
    def _choose_symbol(frames: dict[str, pd.DataFrame], date) -> str | None:
        candidates = []
        for symbol, frame in frames.items():
            day = frame[frame["Timestamp"].dt.date == date]
            if day.empty:
                continue
            first = day.iloc[0]
            candidates.append((float(first["Close"]) * float(first.get("Volume", 0)), symbol))
        return max(candidates)[1] if candidates else None

    # ── Fill model (same as FenceBarBacktester._fill) ────────────

    def _fill(self, price: float, side: str, entry: bool) -> float:
        impact = self.slippage_bps / 10_000
        if side == "long":
            return price * (1 + impact if entry else 1 - impact)
        return price * (1 - impact if entry else 1 + impact)

    # ── Exit logic (same as FenceBarBacktester._check_exit) ──────

    def _check_exit(self, position: dict, bar: pd.Series, timestamp, params: dict) -> tuple[float | None, str]:
        side = position["side"]
        exit_cfg = params.get("exit", {})
        exit_mode = exit_cfg.get("mode", "fixed_sl_tp")
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])

        if side == "long":
            if low <= position["stop"]:
                return position["stop"], "stop_loss"
            if exit_mode == "fixed_sl_tp" and high >= position["target"]:
                return position["target"], "take_profit"
        else:
            if high >= position["stop"]:
                return position["stop"], "stop_loss"
            if exit_mode == "fixed_sl_tp" and low <= position["target"]:
                return position["target"], "take_profit"

        if exit_mode == "trailing":
            trail_pct = float(exit_cfg.get("trailing_pct", 0.3))
            trail_act_pct = float(exit_cfg.get("trailing_activation_pct", 0.3))
            entry_px = position["entry_price"]
            if side == "long":
                position["peak"] = max(position.get("peak", entry_px), high)
                gain_pct = (position["peak"] - entry_px) / entry_px * 100
                if gain_pct >= trail_act_pct:
                    trail_level = position["peak"] * (1 - trail_pct / 100)
                    if low <= trail_level:
                        return trail_level, "trailing_stop"
            else:
                position["trough"] = min(position.get("trough", entry_px), low)
                gain_pct = (entry_px - position["trough"]) / entry_px * 100
                if gain_pct >= trail_act_pct:
                    trail_level = position["trough"] * (1 + trail_pct / 100)
                    if high >= trail_level:
                        return trail_level, "trailing_stop"

        if exit_mode in ("time_based", "trailing"):
            max_bars = int(exit_cfg.get("max_bars", 0))
            if max_bars > 0 and position.get("bars_held", 0) >= max_bars:
                return close, "time_exit"

        force_time = datetime.strptime(params["session"]["force_exit"], "%H:%M").time()
        if timestamp.time() >= force_time:
            return close, "force_exit"

        return None, ""

    # ── Volatility filter ───────────────────────────────────────

    def _build_vol_map(self) -> dict:
        cfg = self.params.get("vol_filter", {})
        if not cfg.get("enabled", True):
            return {}
        try:
            start = (datetime.fromisoformat(self.start_date) - timedelta(days=40)).strftime("%Y-%m-%d") if self.start_date else "2024-09-01"
            spy = self.provider.history("SPY", interval="1d", start=start, end=self.end_date or "2026-08-11")
        except Exception:
            return {}
        if spy is None or spy.empty:
            return {}
        spy = spy.reset_index() if spy.index.name else spy
        col = "Datetime" if "Datetime" in spy.columns else "Date"
        spy[col] = pd.to_datetime(spy[col])
        spy["Vol20"] = spy["Close"].pct_change().rolling(20).std() * 100
        spy["ATR_pct"] = (spy["High"] - spy["Low"]) / spy["Close"] * 100
        spy["ATR20"] = spy["ATR_pct"].rolling(20).mean()
        spy["Date"] = spy[col].dt.date
        return {row["Date"]: {"vol20": row["Vol20"], "atr20": row["ATR20"]}
                for _, row in spy.iterrows() if not pd.isna(row["Vol20"])}

    def _vol_filter_passes(self, date) -> bool:
        cfg = self.params.get("vol_filter", {})
        if not cfg.get("enabled", True):
            return True
        mode = cfg.get("mode", "window")
        if mode == "window":
            # Window mode: check once at the start_date of this backtest run.
            # Cache the result so we don't re-check every day.
            if not hasattr(self, "_window_vol_pass"):
                if self._vol_map is None:
                    self._vol_map = self._build_vol_map()
                # Find the closest date in the vol map on or before start_date
                start_d = datetime.fromisoformat(self.start_date).date() if self.start_date else date
                info = self._vol_map.get(start_d)
                if info is None:
                    # Try finding the closest available date
                    available = sorted(self._vol_map.keys())
                    for d in reversed(available):
                        if d <= start_d:
                            info = self._vol_map[d]
                            break
                if info is None:
                    self._window_vol_pass = False
                else:
                    vt = float(cfg.get("spy_vol_threshold", 1.0))
                    at = float(cfg.get("spy_atr_threshold", 1.2))
                    self._window_vol_pass = info["vol20"] >= vt and info["atr20"] >= at
            return self._window_vol_pass
        else:
            # Day mode: check each individual day
            if self._vol_map is None:
                self._vol_map = self._build_vol_map()
            info = self._vol_map.get(date)
            if info is None:
                return False
            vt = float(cfg.get("spy_vol_threshold", 1.0))
            at = float(cfg.get("spy_atr_threshold", 1.2))
            return info["vol20"] >= vt and info["atr20"] >= at

    # ── Main run loop ───────────────────────────────────────────

    def run(self) -> BacktestReport:
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
        diagnostics = {"sessions": 0, "vol_filtered": 0, "selected_sessions": 0,
                       "entries": 0, "no_symbol": 0,
                       "vol_filter_passed": self._vol_filter_passes(all_dates[0])}
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
                    symbol=symbol, side=position["side"], entry_date=str(position["entry_timestamp"]),
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
