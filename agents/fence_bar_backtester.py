"""Historical replay engine for the standalone Fence Bar strategy."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from arena_market_data import ArenaMarketDataProvider, get_arena_market_data
from backtest_report import BacktestReport, TradeRecord
from fence_bar_strategy import FENCE_BAR_DEFAULTS, FenceBarStrategy
from premarket_scanner import DEFAULT_CONFIG as PREMARKET_DEFAULT_CONFIG, scan as scan_premarket
from strategy_lab import deep_merge

logger = logging.getLogger(__name__)


class FenceBarBacktester:
    """Replay one-symbol-per-session Fence Bar trades on completed 5m bars."""

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
            raise ValueError("Fence Bar requires at least one symbol")
        self.symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        self.params = deep_merge(FENCE_BAR_DEFAULTS, params or {})
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = float(initial_capital)
        self.slippage_bps = float(slippage_bps)
        self.fee_rate = float(fee_rate)
        self.provider = provider or get_arena_market_data()

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
            logger.warning("Failed to fetch Fence Bar data for %s: %s", symbol, exc)
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
        for column in ("Open", "High", "Low", "Close", "Volume"):
            if column not in frame.columns:
                return None
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Timestamp")
        frame = frame[(frame["Timestamp"].dt.time >= datetime.strptime("09:30", "%H:%M").time()) &
                      (frame["Timestamp"].dt.time <= datetime.strptime("16:00", "%H:%M").time())]
        if self.start_date:
            frame = frame[frame["Timestamp"].dt.date >= datetime.fromisoformat(self.start_date).date()]
        if self.end_date:
            frame = frame[frame["Timestamp"].dt.date <= datetime.fromisoformat(self.end_date).date()]
        return frame.reset_index(drop=True)

    @staticmethod
    def _choose_symbol(frames: dict[str, pd.DataFrame], date) -> str | None:
        """Choose one symbol using only its first opening bar for that session."""
        candidates = []
        for symbol, frame in frames.items():
            day = frame[frame["Timestamp"].dt.date == date]
            if day.empty:
                continue
            first = day.iloc[0]
            candidates.append((float(first["Close"]) * float(first.get("Volume", 0)), symbol))
        return max(candidates)[1] if candidates else None

    def _choose_premarket_symbol(self, date) -> tuple[str | None, dict]:
        """Select the top premarket-ranked symbol before the regular open."""
        cfg = self.params.get("premarket", {})
        from premarket_replay import PremarketReplayProvider

        provider = PremarketReplayProvider(str(date), interval=str(cfg.get("interval", "5m")))
        provider.prepare(self.symbols, period=str(cfg.get("history_period", "3mo")))
        scanner_config = deep_merge(PREMARKET_DEFAULT_CONFIG, cfg.get("scanner", {}))
        scanner_config["min_score"] = float(cfg.get("min_score", scanner_config["min_score"]))
        result = scan_premarket(
            scanner_config,
            provider=provider,
            symbols=self.symbols,
            mover_fetcher=provider.mover_fetcher,
            news_fetcher=provider.news_fetcher if cfg.get("use_news", False) else None,
        )
        watchlist = result.get("watchlist", [])
        if cfg.get("require_monitor", True):
            watchlist = [candidate for candidate in watchlist if candidate["status"] == "monitor"]
        watchlist = [candidate for candidate in watchlist if candidate["score"] >= scanner_config["min_score"]]
        if not watchlist:
            return None, {
                "date": str(date),
                "candidate_count": result.get("candidate_count", 0),
                "monitor_count": result.get("monitor_count", 0),
            }
        selected = watchlist[0]
        return selected["symbol"], {
            "date": str(date),
            "symbol": selected["symbol"],
            "score": selected["score"],
            "status": selected["status"],
            "change_pct": selected["change_pct"],
            "relative_volume": selected["relative_volume"],
            "spread_pct": selected["spread_pct"],
            "candidate_count": result.get("candidate_count", 0),
            "monitor_count": result.get("monitor_count", 0),
        }

    def _fill(self, price: float, side: str, entry: bool) -> float:
        impact = self.slippage_bps / 10_000
        if side == "long":
            return price * (1 + impact if entry else 1 - impact)
        return price * (1 - impact if entry else 1 + impact)

    def _check_exit(self, position: dict, bar: pd.Series, timestamp, params: dict) -> tuple[float | None, str]:
        """Check all exit conditions for a position on one bar.

        Returns (exit_price, reason) or (None, "").
        Supports: fixed stop, fixed target, trailing stop, time-based exit,
        force exit at session end.
        """
        side = position["side"]
        exit_cfg = params.get("exit", {})
        exit_mode = exit_cfg.get("mode", "fixed_sl_tp")
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])

        # ── Fixed stop/target (always active as a hard stop) ──
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

        # ── Trailing stop ──
        if exit_mode == "trailing":
            trail_pct = float(exit_cfg.get("trailing_pct", 0.3))
            trail_act_pct = float(exit_cfg.get("trailing_activation_pct", 0.3))
            entry = position["entry_price"]
            if side == "long":
                position["peak"] = max(position.get("peak", entry), high)
                gain_pct = (position["peak"] - entry) / entry * 100
                if gain_pct >= trail_act_pct:
                    trail_level = position["peak"] * (1 - trail_pct / 100)
                    if low <= trail_level:
                        return trail_level, "trailing_stop"
            else:
                position["trough"] = min(position.get("trough", entry), low)
                gain_pct = (entry - position["trough"]) / entry * 100
                if gain_pct >= trail_act_pct:
                    trail_level = position["trough"] * (1 + trail_pct / 100)
                    if high >= trail_level:
                        return trail_level, "trailing_stop"

        # ── Time-based exit ──
        if exit_mode in ("time_based", "trailing"):
            max_bars = int(exit_cfg.get("max_bars", 0))
            if max_bars > 0 and position.get("bars_held", 0) >= max_bars:
                return close, "time_exit"

        # ── Force exit at session end ──
        force_time = datetime.strptime(params["session"]["force_exit"], "%H:%M").time()
        if timestamp.time() >= force_time:
            return close, "force_exit"

        return None, ""

    def run(self) -> BacktestReport:
        frames = {symbol: self._fetch(symbol) for symbol in self.symbols}
        frames = {symbol: frame for symbol, frame in frames.items() if frame is not None and not frame.empty}
        if not frames:
            return BacktestReport.calculate_metrics(
                agent_name="Fence Bar", symbols=self.symbols, start_date=self.start_date,
                end_date=self.end_date, initial_capital=self.initial_capital,
                final_equity=self.initial_capital, equity_curve=[], trades=[], interval="5m",
                slippage_bps=self.slippage_bps, periods_per_year=252 * 78,
                diagnostics={"error": "no historical data"},
            )

        all_dates = sorted({date for frame in frames.values() for date in frame["Timestamp"].dt.date})
        cash = self.initial_capital
        equity_curve: list[dict] = []
        trades: list[TradeRecord] = []
        premarket_enabled = bool(self.params.get("premarket", {}).get("enabled", False))
        diagnostics = {"sessions": 0, "selected_sessions": 0, "fence_rejected": 0,
                       "breakouts": 0, "retests": 0, "entries": 0,
                       "selection": "premarket-scanner" if premarket_enabled else "first-bar-dollar-volume",
                       "premarket_enabled": premarket_enabled, "premarket_skipped": 0,
                       "premarket_selections": []}
        actual_start = all_dates[0].isoformat()
        actual_end = all_dates[-1].isoformat()

        for date in all_dates:
            diagnostics["sessions"] += 1
            if premarket_enabled:
                try:
                    symbol, selection = self._choose_premarket_symbol(date)
                except Exception as exc:
                    logger.warning("Premarket selection failed for %s: %s", date, exc)
                    symbol, selection = None, {"date": str(date), "error": str(exc)}
                diagnostics["premarket_selections"].append(selection)
                if not symbol:
                    diagnostics["premarket_skipped"] += 1
                    continue
            else:
                symbol = self._choose_symbol(frames, date)
            if not symbol:
                continue
            diagnostics["selected_sessions"] += 1
            day = frames[symbol][frames[symbol]["Timestamp"].dt.date == date].reset_index(drop=True)
            strategy = FenceBarStrategy(symbol, self.params)
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
                    equity = cash
                    risk_budget = equity * float(self.params["risk"]["risk_per_trade_pct"]) / 100
                    quantity = risk_budget / signal.risk_per_share
                    quantity = min(quantity, equity * 0.25 / entry)
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
                    close = float(bar["Close"])
                    marked += position["quantity"] * close if position["side"] == "long" else -position["quantity"] * close
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
            agent_name="Fence Bar", symbols=self.symbols, start_date=actual_start,
            end_date=actual_end, initial_capital=self.initial_capital, final_equity=cash,
            equity_curve=equity_curve, trades=trades, interval="5m",
            slippage_bps=self.slippage_bps, periods_per_year=252 * 78, diagnostics=diagnostics,
        )
        report.diagnostics["avg_r"] = round(sum(t.pnl for t in trades) / max(1, len(trades)), 2)
        return report
