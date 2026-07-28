"""
Backtester — Lightweight backtesting engine for AI-Trader strategies.

Replays historical OHLCV data through any agent's analyze() method.
Uses BacktestAgent mock to intercept I/O calls and track portfolio state in-memory.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from base_agent import BaseAgent, TradeDecision
from market_data import MarketDataClient, TechnicalSnapshot
from personality import Personality
from backtest_report import BacktestReport, TradeRecord


class BacktestAgent(BaseAgent):
    """Mock agent that runs real strategy logic but tracks state in-memory.

    Overrides all I/O methods (connect, fetch_portfolio, execute_trade,
    publish_strategy, heartbeat, engage_community) so the strategy's
    analyze() method runs unchanged against historical data.
    """

    def __init__(self, personality: Personality, initial_capital: float = 100000.0):
        super().__init__(personality, api_base="http://localhost:0/api")
        self.cash = initial_capital
        self.portfolio_value = initial_capital
        self._initial_capital = initial_capital
        self._positions: dict[str, dict] = {}  # symbol -> {qty, entry_price, entry_date, side}
        self._closed_trades: list[TradeRecord] = []
        self._current_date: str = ""
        self._price_lookup: dict[str, float] = {}  # symbol -> current price for this sim day
        self.logger = logging.getLogger(f"Backtest:{personality.name}")
        self.logger.handlers = [logging.StreamHandler()]
        self.logger.setLevel(logging.WARNING)
        self.logger.propagate = False

    def connect(self) -> bool:
        return True

    def fetch_portfolio(self) -> dict:
        equity = self.cash
        for sym, pos in self._positions.items():
            price = self._price_lookup.get(sym, pos["entry_price"])
            equity += pos["quantity"] * price
        self.portfolio_value = equity
        self.cash = self.cash  # already tracked
        self.positions = [
            {
                "symbol": sym,
                "quantity": pos["quantity"],
                "entry_price": pos["entry_price"],
                "current_price": self._price_lookup.get(sym, pos["entry_price"]),
                "pnl": (self._price_lookup.get(sym, pos["entry_price"]) - pos["entry_price"]) * pos["quantity"],
                "side": pos.get("side", "long"),
            }
            for sym, pos in self._positions.items()
        ]
        return {"portfolio_value": equity, "cash": self.cash}

    def has_position(self, symbol: str) -> bool:
        pos = self._positions.get(symbol)
        return pos is not None and pos["quantity"] > 0

    def get_position(self, symbol: str) -> dict:
        pos = self._positions.get(symbol)
        if not pos:
            return {"quantity": 0, "entry_price": 0, "pnl": 0, "side": "long"}
        price = self._price_lookup.get(symbol, pos["entry_price"])
        return {
            "quantity": pos["quantity"],
            "entry_price": pos["entry_price"],
            "current_price": price,
            "pnl": (price - pos["entry_price"]) * pos["quantity"],
            "side": pos.get("side", "long"),
        }

    def execute_trade(self, decision: TradeDecision) -> bool:
        symbol = decision.symbol
        price = self._price_lookup.get(symbol)
        if price is None or price <= 0:
            return False

        qty = decision.quantity
        if qty <= 0:
            return False

        if decision.action in ("buy", "short"):
            cost = qty * price
            if cost > self.cash:
                return False
            self.cash -= cost
            self._positions[symbol] = {
                "quantity": qty,
                "entry_price": price,
                "entry_date": self._current_date,
                "side": "long" if decision.action == "buy" else "short",
            }
            self.trades_made += 1
            return True

        elif decision.action in ("sell", "cover"):
            pos = self._positions.get(symbol)
            if not pos or pos["quantity"] <= 0:
                return False

            sell_qty = min(qty, pos["quantity"])
            proceeds = sell_qty * price
            self.cash += proceeds

            pnl = (price - pos["entry_price"]) * sell_qty
            pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0

            entry_date = pos.get("entry_date", "")
            hold_days = 0
            if entry_date and self._current_date:
                try:
                    d1 = datetime.fromisoformat(entry_date.split("T")[0])
                    d2 = datetime.fromisoformat(self._current_date.split("T")[0])
                    hold_days = (d2 - d1).days
                except Exception:
                    pass

            self._closed_trades.append(TradeRecord(
                symbol=symbol,
                side=pos.get("side", "long"),
                entry_date=entry_date,
                exit_date=self._current_date,
                entry_price=pos["entry_price"],
                exit_price=price,
                quantity=sell_qty,
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
                reason=decision.reason[:200] if decision.reason else "",
            ))

            if sell_qty >= pos["quantity"]:
                del self._positions[symbol]
            else:
                pos["quantity"] -= sell_qty

            self.trades_made += 1
            return True

        return False

    def publish_strategy(self, **kwargs) -> bool:
        return True

    def publish_discussion(self, **kwargs) -> bool:
        return True

    def heartbeat(self) -> dict:
        return {}

    def engage_community(self, max_replies: int = 2) -> None:
        pass

    def _report_state(self, state: str, detail: str = "", symbol: str = "", confidence: float = -1.0):
        pass


class Backtester:
    """Backtesting engine that replays historical data through a strategy.

    Fetches historical OHLCV data, constructs TechnicalSnapshot for each
    trading day using data up to that day, calls agent.analyze(), and
    simulates trade execution at that day's close price.
    """

    def __init__(
        self,
        agent_class: type,
        personality: Personality,
        symbols: Optional[list[str]] = None,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 100000.0,
    ):
        self.agent_class = agent_class
        self.personality = personality
        self.symbols = symbols or list(personality.watchlist)
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.market_data = MarketDataClient()

    def _fetch_historical_data(self, symbol: str) -> Optional[dict]:
        """Fetch historical OHLCV data for a symbol via yfinance."""
        try:
            import yfinance as yf
        except ImportError:
            return None

        MarketDataClient._suppress_yfinance_logging()

        yf_symbol = self.market_data._normalize_symbol(symbol)

        period = "max" if not self.start_date else None
        try:
            ticker = yf.Ticker(yf_symbol)
            if period:
                df = ticker.history(period="2y", interval="1d", auto_adjust=False, raise_errors=False)
            else:
                # Fetch from 1 year before start_date so indicators have enough
                # history to compute (SMA20, Bollinger Bands, RSI all need 20+ bars).
                fetch_start_dt = datetime.fromisoformat(self.start_date) - timedelta(days=365)
                start = fetch_start_dt.strftime("%Y-%m-%d")
                end_dt = datetime.fromisoformat(self.end_date) + timedelta(days=1) if self.end_date else datetime.now()
                end = end_dt.strftime("%Y-%m-%d")
                df = ticker.history(start=start, end=end, interval="1d", auto_adjust=False, raise_errors=False)
        except Exception:
            return None

        if df is None or getattr(df, "empty", True):
            return None

        df = df.reset_index()
        return {"df": df, "symbol": symbol}

    def run(self) -> BacktestReport:
        """Execute the backtest and return a performance report."""
        # Fetch all historical data upfront
        historical: dict[str, dict] = {}
        for sym in self.symbols:
            data = self._fetch_historical_data(sym)
            if data:
                historical[sym] = data

        if not historical:
            return BacktestReport.calculate_metrics(
                agent_name=self.personality.name,
                symbols=self.symbols,
                start_date=self.start_date or "N/A",
                end_date=self.end_date or "N/A",
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                equity_curve=[],
                trades=[],
            )

        # Determine date range from the data
        all_dates = set()
        for sym_data in historical.values():
            df = sym_data["df"]
            if "Date" in df.columns:
                for d in df["Date"]:
                    all_dates.add(d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10])

        sorted_dates = sorted(all_dates)
        if self.start_date:
            sorted_dates = [d for d in sorted_dates if d >= self.start_date]
        if self.end_date:
            sorted_dates = [d for d in sorted_dates if d <= self.end_date]

        if not sorted_dates:
            return BacktestReport.calculate_metrics(
                agent_name=self.personality.name,
                symbols=self.symbols,
                start_date=self.start_date or "N/A",
                end_date=self.end_date or "N/A",
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                equity_curve=[],
                trades=[],
            )

        # Create a dynamic class that inherits from the real strategy class
        # and BacktestAgent, so the strategy's analyze() runs with backtest I/O overrides.
        # BacktestAgent must come first in MRO so its I/O methods take precedence,
        # but the strategy class provides analyze() and other strategy logic.
        BacktestStrategy = type(
            'BacktestStrategy',
            (BacktestAgent, self.agent_class),
            {},
        )
        agent = BacktestStrategy(self.personality, self.initial_capital)
        agent.on_start()

        equity_curve: list[dict] = []
        actual_start = sorted_dates[0]
        actual_end = sorted_dates[-1]

        for sim_date in sorted_dates:
            agent._current_date = sim_date

            # Build price lookup for this date
            for sym, sym_data in historical.items():
                df = sym_data["df"]
                date_col = "Date" if "Date" in df.columns else df.index.name or "Date"
                try:
                    if "Date" in df.columns:
                        mask = df["Date"].apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) == sim_date
                    else:
                        mask = df.index.to_series().apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) == sim_date
                    matching = df[mask]
                    if not matching.empty:
                        row = matching.iloc[-1]
                        close = float(row["Close"]) if "Close" in row else 0.0
                        agent._price_lookup[sym] = close
                except Exception:
                    pass

            # Build TechnicalSnapshot for each symbol using data up to sim_date
            snapshots: dict[str, TechnicalSnapshot] = {}
            for sym, sym_data in historical.items():
                df = sym_data["df"]
                try:
                    if "Date" in df.columns:
                        mask = df["Date"].apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) <= sim_date
                    else:
                        mask = df.index.to_series().apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)[:10]) <= sim_date
                    window = df[mask]
                except Exception:
                    continue

                if len(window) < 20:
                    continue

                closes = window["Close"].squeeze().dropna()
                volumes = window["Volume"].squeeze().dropna() if "Volume" in window else None
                highs = window["High"].squeeze().dropna() if "High" in window else None
                lows = window["Low"].squeeze().dropna() if "Low" in window else None

                if len(closes) < 20:
                    continue

                snapshot = MarketDataClient._compute_indicators(sym, closes, highs, lows, volumes)
                if snapshot:
                    snapshots[sym] = snapshot

            if not snapshots:
                # Record equity even on no-data days
                equity = agent.cash + sum(
                    pos["quantity"] * agent._price_lookup.get(s, pos["entry_price"])
                    for s, pos in agent._positions.items()
                )
                equity_curve.append({"date": sim_date, "equity": round(equity, 2)})
                continue

            # Override market_data.fetch_technical to return our precomputed snapshots
            original_fetch = agent.market_data.fetch_technical
            def _mock_fetch(sym, _snapshots=snapshots):
                return _snapshots.get(sym)
            agent.market_data.fetch_technical = _mock_fetch

            # Also override fetch_news to return empty (no historical news)
            agent.market_data.fetch_news = lambda limit=20: []

            # Update portfolio state before analyze
            agent.fetch_portfolio()

            # Run the strategy
            try:
                decisions = agent.analyze()
                for decision in decisions:
                    agent.execute_trade(decision)
            except Exception:
                pass

            # Restore original method
            agent.market_data.fetch_technical = original_fetch

            # Record equity
            equity = agent.cash + sum(
                pos["quantity"] * agent._price_lookup.get(s, pos["entry_price"])
                for s, pos in agent._positions.items()
            )
            equity_curve.append({"date": sim_date, "equity": round(equity, 2)})

        # Close any remaining open positions at last available prices
        for sym, pos in list(agent._positions.items()):
            price = agent._price_lookup.get(sym, pos["entry_price"])
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0

            entry_date = pos.get("entry_date", "")
            hold_days = 0
            if entry_date and actual_end:
                try:
                    d1 = datetime.fromisoformat(entry_date.split("T")[0])
                    d2 = datetime.fromisoformat(actual_end.split("T")[0])
                    hold_days = (d2 - d1).days
                except Exception:
                    pass

            agent._closed_trades.append(TradeRecord(
                symbol=sym,
                side=pos.get("side", "long"),
                entry_date=entry_date,
                exit_date=actual_end,
                entry_price=pos["entry_price"],
                exit_price=price,
                quantity=pos["quantity"],
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
                reason="Backtest end — position auto-closed",
            ))
            agent.cash += pos["quantity"] * price
            del agent._positions[sym]

        final_equity = agent.cash

        return BacktestReport.calculate_metrics(
            agent_name=self.personality.name,
            symbols=self.symbols,
            start_date=actual_start,
            end_date=actual_end,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=equity_curve,
            trades=agent._closed_trades,
        )
