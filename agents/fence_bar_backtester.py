"""Historical replay engine for the standalone Fence Bar strategy.

Refactored to subclass VolFilteredBacktester for shared infrastructure
(data fetching, fill model, exit logic, vol filter, run loop).
Preserves premarket scanner support as an override of the symbol selection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from arena_market_data import ArenaMarketDataProvider
from backtest_report import BacktestReport, TradeRecord
from fence_bar_strategy import FENCE_BAR_DEFAULTS, FenceBarStrategy
from premarket_scanner import DEFAULT_CONFIG as PREMARKET_DEFAULT_CONFIG, scan as scan_premarket
from strategy_lab import deep_merge
from vol_filter_base import VolFilteredBacktester

logger = logging.getLogger(__name__)


class FenceBarBacktester(VolFilteredBacktester):
    """Replay one-symbol-per-session Fence Bar trades on completed 5m bars."""

    @property
    def agent_name(self) -> str:
        return "Fence Bar"

    @property
    def default_params(self) -> dict[str, Any]:
        return FENCE_BAR_DEFAULTS

    def create_strategy(self, symbol: str, date=None, day=None):
        return FenceBarStrategy(symbol, self.params)

    # ── Premarket symbol selection (preserved from original) ────

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
            watchlist = [c for c in watchlist if c["status"] == "monitor"]
        watchlist = [c for c in watchlist if c["score"] >= scanner_config["min_score"]]
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

    def run(self) -> BacktestReport:
        """Override run() to support premarket scanner symbol selection."""
        premarket_enabled = bool(self.params.get("premarket", {}).get("enabled", False))
        if not premarket_enabled:
            return super().run()

        # Premarket path: same loop but uses _choose_premarket_symbol
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
                       "entries": 0, "no_symbol": 0, "premarket_skipped": 0,
                       "premarket_selections": [],
                       "selection": "premarket-scanner"}
        actual_start = all_dates[0].isoformat()
        actual_end = all_dates[-1].isoformat()

        for date in all_dates:
            diagnostics["sessions"] += 1
            if not self._vol_filter_passes(date):
                diagnostics["vol_filtered"] += 1
                continue
            try:
                symbol, selection = self._choose_premarket_symbol(date)
            except Exception as exc:
                logger.warning("Premarket selection failed for %s: %s", date, exc)
                symbol, selection = None, {"date": str(date), "error": str(exc)}
            diagnostics["premarket_selections"].append(selection)
            if not symbol:
                diagnostics["premarket_skipped"] += 1
                continue
            diagnostics["selected_sessions"] += 1
            if symbol not in frames:
                continue
            day = frames[symbol][frames[symbol]["Timestamp"].dt.date == date].reset_index(drop=True)
            strategy = self.create_strategy(symbol)
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
