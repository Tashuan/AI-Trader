"""
Backtest Report — Performance metrics dataclass for backtesting results.
"""

from dataclasses import dataclass, field
from typing import Optional
import math


@dataclass
class TradeRecord:
    """A single completed trade (entry + exit)."""
    symbol: str
    side: str  # "long" or "short"
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    hold_days: int
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "entry_price": round(self.entry_price, 4),
            "exit_price": round(self.exit_price, 4),
            "quantity": round(self.quantity, 6),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "hold_days": self.hold_days,
            "reason": self.reason,
        }


@dataclass
class BacktestReport:
    """Performance metrics from a backtest run."""
    agent_name: str
    symbols: list[str]
    start_date: str
    end_date: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_hold_days: float
    profit_factor: float
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    per_symbol_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "symbols": self.symbols,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": round(self.initial_capital, 2),
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate": round(self.win_rate, 4),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_hold_days": round(self.avg_hold_days, 1),
            "profit_factor": round(self.profit_factor, 3),
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "per_symbol_stats": self.per_symbol_stats,
        }

    @staticmethod
    def calculate_metrics(
        agent_name: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float,
        final_equity: float,
        equity_curve: list[dict],
        trades: list[TradeRecord],
    ) -> "BacktestReport":
        """Compute all performance metrics from raw backtest data."""
        total_return_pct = ((final_equity - initial_capital) / initial_capital * 100) if initial_capital > 0 else 0.0

        # Sharpe ratio (daily returns, annualized)
        sharpe = 0.0
        if len(equity_curve) > 1:
            daily_returns = []
            for i in range(1, len(equity_curve)):
                prev_eq = equity_curve[i - 1]["equity"]
                curr_eq = equity_curve[i]["equity"]
                if prev_eq > 0:
                    daily_returns.append((curr_eq - prev_eq) / prev_eq)
            if daily_returns:
                avg_ret = sum(daily_returns) / len(daily_returns)
                variance = sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns)
                std_ret = math.sqrt(variance) if variance > 0 else 0.001
                sharpe = (avg_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0

        # Max drawdown
        max_dd = 0.0
        peak = initial_capital
        for point in equity_curve:
            eq = point["equity"]
            if eq > peak:
                peak = eq
            dd = ((peak - eq) / peak * 100) if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Trade stats
        total_trades = len(trades)
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        win_rate = (len(winning) / total_trades) if total_trades > 0 else 0.0

        gross_profit = sum(t.pnl for t in winning)
        gross_loss = abs(sum(t.pnl for t in losing))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        avg_hold = (sum(t.hold_days for t in trades) / total_trades) if total_trades > 0 else 0.0

        # Per-symbol stats
        per_symbol: dict[str, dict] = {}
        for sym in symbols:
            sym_trades = [t for t in trades if t.symbol == sym]
            sym_wins = [t for t in sym_trades if t.pnl > 0]
            sym_pnl = sum(t.pnl for t in sym_trades)
            per_symbol[sym] = {
                "trades": len(sym_trades),
                "wins": len(sym_wins),
                "win_rate": round(len(sym_wins) / len(sym_trades), 4) if sym_trades else 0.0,
                "total_pnl": round(sym_pnl, 2),
                "avg_pnl_pct": round(sum(t.pnl_pct for t in sym_trades) / len(sym_trades), 2) if sym_trades else 0.0,
            }

        return BacktestReport(
            agent_name=agent_name,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            win_rate=win_rate,
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_hold_days=avg_hold,
            profit_factor=profit_factor if profit_factor != float("inf") else 999.0,
            equity_curve=equity_curve,
            trades=[t.to_dict() for t in trades],
            per_symbol_stats=per_symbol,
        )
