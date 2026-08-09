"""
Backtester — Lightweight backtesting engine for AI-Trader strategies.

Replays historical OHLCV data through any agent's analyze() method.
Uses BacktestAgent mock to intercept I/O calls and track portfolio state in-memory.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

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

    def __init__(self, personality: Personality, initial_capital: float = 100000.0, slippage_bps: float = 0.0):
        super().__init__(personality, api_base="http://localhost:0/api")
        self.cash = initial_capital
        self.portfolio_value = initial_capital
        self._initial_capital = initial_capital
        self._positions: dict[str, dict] = {}  # symbol -> {qty, entry_price, entry_date, side}
        self._closed_trades: list[TradeRecord] = []
        self._current_date: str = ""
        self._price_lookup: dict[str, float] = {}  # symbol -> current price for this sim bar
        # Slippage in basis points, applied adversely: buys fill higher, sells fill lower.
        # Models the real cost of chasing momentum (buying strength, selling weakness).
        self._slippage_bps = slippage_bps
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
            if pos.get("side") == "short":
                # Short-sale proceeds are already included in cash. Only the
                # current cost to cover remains as a liability.
                equity -= price * pos["quantity"]
            else:
                equity += pos["quantity"] * price
        self.portfolio_value = equity
        self.positions = [
            {
                "symbol": sym,
                "quantity": pos["quantity"],
                "entry_price": pos["entry_price"],
                "current_price": self._price_lookup.get(sym, pos["entry_price"]),
                "pnl": (self._price_lookup.get(sym, pos["entry_price"]) - pos["entry_price"]) * pos["quantity"] if pos.get("side", "long") == "long"
                       else (pos["entry_price"] - self._price_lookup.get(sym, pos["entry_price"])) * pos["quantity"],
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
        side = pos.get("side", "long")
        if side == "short":
            pnl = (pos["entry_price"] - price) * pos["quantity"]
        else:
            pnl = (price - pos["entry_price"]) * pos["quantity"]
        return {
            "quantity": pos["quantity"],
            "entry_price": pos["entry_price"],
            "current_price": price,
            "pnl": pnl,
            "side": side,
        }

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

    def execute_trade(self, decision: TradeDecision) -> bool:
        symbol = decision.symbol
        raw_price = self._price_lookup.get(symbol)
        if raw_price is None or raw_price <= 0:
            return False

        qty = decision.quantity
        if qty <= 0:
            return False

        slip = self._slippage_bps / 10000.0

        if decision.action == "buy":
            # Buying momentum fills worse (higher) due to slippage.
            fill_price = raw_price * (1 + slip)
            cost = qty * fill_price
            if cost > self.cash:
                return False
            self.cash -= cost
            self._positions[symbol] = {
                "quantity": qty,
                "entry_price": fill_price,
                "entry_date": self._current_date,
                "side": "long",
            }
            self.trades_made += 1
            return True

        elif decision.action == "short":
            # Shorting into weakness fills worse (lower) due to slippage — we receive less.
            fill_price = raw_price * (1 - slip)
            proceeds = qty * fill_price
            # Reserve the proceeds as collateral (can't spend them)
            if proceeds > self.cash:
                return False
            self.cash += proceeds  # receive short proceeds
            self._positions[symbol] = {
                "quantity": qty,
                "entry_price": fill_price,
                "entry_date": self._current_date,
                "side": "short",
            }
            self.trades_made += 1
            return True

        elif decision.action in ("sell", "cover"):
            pos = self._positions.get(symbol)
            if not pos or pos["quantity"] <= 0:
                return False

            side = pos.get("side", "long")
            sell_qty = min(qty, pos["quantity"])

            if side == "short":
                # Covering: buying back to close short. Fills worse (higher) due to slippage.
                fill_price = raw_price * (1 + slip)
                cost = sell_qty * fill_price
                if cost > self.cash:
                    return False
                self.cash -= cost
                # Short PnL: profit when exit < entry
                pnl = (pos["entry_price"] - fill_price) * sell_qty
                pnl_pct = ((pos["entry_price"] - fill_price) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0
            else:
                # Selling long: fills worse (lower) due to slippage.
                fill_price = raw_price * (1 - slip)
                proceeds = sell_qty * fill_price
                self.cash += proceeds
                pnl = (fill_price - pos["entry_price"]) * sell_qty
                pnl_pct = ((fill_price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0

            entry_date = pos.get("entry_date", "")
            hold_days, hold_hours = self._hold_span(entry_date, self._current_date)

            self._closed_trades.append(TradeRecord(
                symbol=symbol,
                side=side,
                entry_date=entry_date,
                exit_date=self._current_date,
                entry_price=pos["entry_price"],
                exit_price=fill_price,
                quantity=sell_qty,
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
                hold_hours=hold_hours,
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

    # Max lookback yfinance allows per intraday interval (approximate, per Yahoo's limits)
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

    # Approximate bars-per-day used to scale Sharpe annualization for intraday bars.
    # Crypto trades 24/7, equities ~6.5h/day — this uses a 24h approximation since
    # the default watchlist mixes both; treat as a rough scaling factor, not exact.
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

    def __init__(
        self,
        agent_class: type,
        personality: Personality,
        symbols: Optional[list[str]] = None,
        start_date: str = "",
        end_date: str = "",
        initial_capital: float = 100000.0,
        interval: str = "1d",
        slippage_bps: float = 0.0,
    ):
        self.agent_class = agent_class
        self.personality = personality
        self.symbols = symbols or list(personality.watchlist)
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.interval = interval or "1d"
        self.slippage_bps = slippage_bps
        self.market_data = MarketDataClient()

    def _fetch_historical_data(self, symbol: str) -> Optional[dict]:
        """Fetch historical OHLCV data for a symbol via yfinance.

        For daily bars ("1d"), fetches from 1 year before start_date so
        indicators (SMA20, Bollinger Bands, RSI) have enough history.
        For intraday bars, yfinance restricts how far back history is
        available (e.g. ~60 days for 5m/15m/30m, ~7 days for 1m), so the
        fetch window is clamped to that limit and a smaller indicator
        lookback buffer (a few days) is used instead of a full year.
        """
        try:
            import yfinance as yf
        except ImportError:
            return None

        MarketDataClient._suppress_yfinance_logging()

        yf_symbol = self.market_data._normalize_symbol(symbol)
        is_intraday = self.interval != "1d"

        try:
            ticker = yf.Ticker(yf_symbol)
            if is_intraday:
                max_lookback = self._INTRADAY_MAX_LOOKBACK_DAYS.get(self.interval, 60)
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
                df = ticker.history(start=start, end=end, interval=self.interval, auto_adjust=False, raise_errors=False)
            elif not self.start_date:
                df = ticker.history(period="2y", interval="1d", auto_adjust=False, raise_errors=False)
            else:
                # Fetch from 1 year before start_date so indicators have enough
                # history to compute (SMA20, Bollinger Bands, RSI all need 20+ bars).
                fetch_start_dt = datetime.fromisoformat(self.start_date) - timedelta(days=365)
                start = fetch_start_dt.strftime("%Y-%m-%d")
                end_dt = datetime.fromisoformat(self.end_date) + timedelta(days=1) if self.end_date else datetime.now()
                end = end_dt.strftime("%Y-%m-%d")
                df = ticker.history(start=start, end=end, interval="1d", auto_adjust=False, raise_errors=False)
        except Exception as exc:
            logger.warning("Failed to fetch historical data for %s: %s", symbol, exc)
            return None

        if df is None or getattr(df, "empty", True):
            logger.warning("No historical data returned for %s (interval=%s, start=%s, end=%s)",
                           symbol, self.interval, self.start_date, self.end_date)
            return None

        df = df.reset_index()
        return {"df": df, "symbol": symbol}

    @staticmethod
    def _time_col(df) -> str:
        """Return the timestamp column name — 'Datetime' for intraday bars, 'Date' for daily."""
        if "Datetime" in df.columns:
            return "Datetime"
        return "Date"

    def _ts_key(self, x) -> str:
        """Format a pandas timestamp to a string key at the granularity matching self.interval."""
        if hasattr(x, "strftime"):
            if self.interval == "1d":
                return x.strftime("%Y-%m-%d")
            return x.strftime("%Y-%m-%dT%H:%M:%S")
        s = str(x)
        return s[:10] if self.interval == "1d" else s[:19]

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
                interval=self.interval,
                slippage_bps=self.slippage_bps,
            )

        # Determine the simulation timeline from the data (unified across daily/intraday)
        all_ts = set()
        for sym_data in historical.values():
            df = sym_data["df"]
            col = self._time_col(df)
            if col in df.columns:
                for t in df[col]:
                    all_ts.add(self._ts_key(t))

        sorted_ts = sorted(all_ts)
        if self.start_date:
            sorted_ts = [t for t in sorted_ts if t >= self.start_date]
        if self.end_date:
            end_bound = self.end_date if self.interval == "1d" else f"{self.end_date}T23:59:59"
            sorted_ts = [t for t in sorted_ts if t <= end_bound]

        if not sorted_ts:
            return BacktestReport.calculate_metrics(
                agent_name=self.personality.name,
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

        # Create a dynamic class that inherits from the real strategy class
        # and BacktestAgent, so the strategy's analyze() runs with backtest I/O overrides.
        # BacktestAgent must come first in MRO so its I/O methods take precedence,
        # but the strategy class provides analyze() and other strategy logic.
        BacktestStrategy = type(
            'BacktestStrategy',
            (BacktestAgent, self.agent_class),
            {},
        )
        agent = BacktestStrategy(self.personality, self.initial_capital, self.slippage_bps)
        agent.on_start()

        # Pre-resolve each symbol's timestamp column and build O(1) lookup structures.
        # For each symbol we store:
        #   - ts_list: sorted list of ts_keys (for bisect)
        #   - ts_to_idx: dict mapping ts_key -> row index in the df
        # This avoids O(n²) boolean masking on every iteration.
        import bisect
        col_map: dict[str, str] = {}
        ts_list_map: dict[str, list[str]] = {}
        ts_to_idx_map: dict[str, dict[str, int]] = {}
        for sym, sym_data in historical.items():
            df = sym_data["df"]
            col = self._time_col(df)
            col_map[sym] = col
            keys = df[col].apply(self._ts_key).tolist()
            ts_to_idx_map[sym] = {}
            ts_list = []
            for i, k in enumerate(keys):
                ts_to_idx_map[sym][k] = i
                ts_list.append(k)
            ts_list_map[sym] = ts_list

        equity_curve: list[dict] = []
        actual_start = sorted_ts[0]
        actual_end = sorted_ts[-1]
        min_bars = 20 if self.interval == "1d" else min(20, max(5, len(sorted_ts) // 4))

        for sim_ts in sorted_ts:
            agent._current_date = sim_ts

            # Build price lookup for this bar (O(1) per symbol)
            for sym, sym_data in historical.items():
                df = sym_data["df"]
                idx = ts_to_idx_map[sym].get(sim_ts)
                if idx is not None:
                    try:
                        close = float(df.iloc[idx]["Close"])
                        agent._price_lookup[sym] = close
                    except Exception:
                        pass

            # Build TechnicalSnapshot for each symbol using data up to sim_ts (O(log n) bisect)
            snapshots: dict[str, TechnicalSnapshot] = {}
            for sym, sym_data in historical.items():
                df = sym_data["df"]
                ts_list = ts_list_map[sym]
                # Find how many bars are available up to and including sim_ts
                pos = bisect.bisect_right(ts_list, sim_ts)
                if pos < min_bars:
                    continue

                window = df.iloc[:pos]
                closes = window["Close"].squeeze().dropna()
                volumes = window["Volume"].squeeze().dropna() if "Volume" in window else None
                highs = window["High"].squeeze().dropna() if "High" in window else None
                lows = window["Low"].squeeze().dropna() if "Low" in window else None

                if len(closes) < min_bars:
                    continue

                snapshot = MarketDataClient._compute_indicators(sym, closes, highs, lows, volumes)
                if snapshot:
                    snapshots[sym] = snapshot

            if not snapshots:
                # Record equity even on no-data bars
                equity = agent.cash + sum(
                    pos["quantity"] * agent._price_lookup.get(s, pos["entry_price"])
                    for s, pos in agent._positions.items()
                )
                equity_curve.append({"date": sim_ts, "equity": round(equity, 2)})
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
            except Exception as exc:
                logger.warning("analyze() failed for %s at %s: %s",
                               self.personality.name, sim_ts, exc)

            # Restore original method
            agent.market_data.fetch_technical = original_fetch

            # Record equity (handle both long and short positions)
            equity = agent.cash
            for s, pos in agent._positions.items():
                price = agent._price_lookup.get(s, pos["entry_price"])
                if pos.get("side") == "short":
                    # Short: we received entry proceeds already (in cash).
                    # Equity impact = (entry - current) * qty — profit when price drops
                    equity += (pos["entry_price"] - price) * pos["quantity"]
                else:
                    equity += pos["quantity"] * price
            equity_curve.append({"date": sim_ts, "equity": round(equity, 2)})

        # Close any remaining open positions at last available prices
        for sym, pos in list(agent._positions.items()):
            price = agent._price_lookup.get(sym, pos["entry_price"])
            side = pos.get("side", "long")
            if side == "short":
                # Cover short: buy back at current price
                pnl = (pos["entry_price"] - price) * pos["quantity"]
                pnl_pct = ((pos["entry_price"] - price) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0
                agent.cash -= pos["quantity"] * price  # pay to cover
            else:
                pnl = (price - pos["entry_price"]) * pos["quantity"]
                pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"] * 100) if pos["entry_price"] > 0 else 0.0
                agent.cash += pos["quantity"] * price  # receive proceeds from selling

            entry_date = pos.get("entry_date", "")
            hold_days, hold_hours = agent._hold_span(entry_date, actual_end)

            agent._closed_trades.append(TradeRecord(
                symbol=sym,
                side=side,
                entry_date=entry_date,
                exit_date=actual_end,
                entry_price=pos["entry_price"],
                exit_price=price,
                quantity=pos["quantity"],
                pnl=pnl,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
                hold_hours=hold_hours,
                reason="Backtest end — position auto-closed",
            ))
            del agent._positions[sym]

        final_equity = agent.cash
        periods_per_year = self._BARS_PER_DAY.get(self.interval, 1) * 252.0

        return BacktestReport.calculate_metrics(
            agent_name=self.personality.name,
            symbols=self.symbols,
            start_date=actual_start,
            end_date=actual_end,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=equity_curve,
            trades=agent._closed_trades,
            interval=self.interval,
            slippage_bps=self.slippage_bps,
            periods_per_year=periods_per_year,
        )
