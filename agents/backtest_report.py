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
    hold_hours: float = 0.0
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
            "hold_hours": round(self.hold_hours, 2),
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
    avg_hold_hours: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    per_symbol_stats: dict = field(default_factory=dict)
    interval: str = "1d"
    slippage_bps: float = 0.0
    out_of_sample: bool = False
    exit_attribution: dict = field(default_factory=dict)
    data_coverage: dict = field(default_factory=dict)
    walk_forward_summary: dict = field(default_factory=dict)
    goal_simulation: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)

    def activation_gate(self) -> dict:
        checks = {
            "positive_return": self.total_return_pct > 0,
            "profit_factor": self.profit_factor > 1.15,
            "max_drawdown": self.max_drawdown_pct < 8.0,
            "trade_coverage": self.total_trades >= 100,
            "out_of_sample": self.out_of_sample,
        }
        return {"eligible": all(checks.values()), "checks": checks}

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
            "avg_hold_hours": round(self.avg_hold_hours, 1),
            "profit_factor": round(self.profit_factor, 3),
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "per_symbol_stats": self.per_symbol_stats,
            "interval": self.interval,
            "slippage_bps": self.slippage_bps,
            "out_of_sample": self.out_of_sample,
            "activation_gate": self.activation_gate(),
            "exit_attribution": self.exit_attribution,
            "data_coverage": self.data_coverage,
            "walk_forward_summary": self.walk_forward_summary,
            "goal_simulation": self.goal_simulation,
            "diagnostics": self.diagnostics,
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
        interval: str = "1d",
        slippage_bps: float = 0.0,
        periods_per_year: float = 252.0,
        diagnostics: Optional[dict] = None,
    ) -> "BacktestReport":
        """Compute all performance metrics from raw backtest data.

        periods_per_year scales the Sharpe annualization factor to match the
        bar interval used (e.g. 252 for daily bars, much higher for intraday
        bars where each equity_curve point is a smaller slice of time).
        """
        total_return_pct = ((final_equity - initial_capital) / initial_capital * 100) if initial_capital > 0 else 0.0

        # Sharpe ratio (per-bar returns, annualized using periods_per_year)
        sharpe = 0.0
        if len(equity_curve) > 1:
            period_returns = []
            for i in range(1, len(equity_curve)):
                prev_eq = equity_curve[i - 1]["equity"]
                curr_eq = equity_curve[i]["equity"]
                if prev_eq > 0:
                    period_returns.append((curr_eq - prev_eq) / prev_eq)
            if period_returns:
                avg_ret = sum(period_returns) / len(period_returns)
                variance = sum((r - avg_ret) ** 2 for r in period_returns) / len(period_returns)
                std_ret = math.sqrt(variance) if variance > 0 else 0.001
                sharpe = (avg_ret / std_ret) * math.sqrt(periods_per_year) if std_ret > 0 else 0.0

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
        avg_hold_hours = (sum(t.hold_hours for t in trades) / total_trades) if total_trades > 0 else 0.0

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

        exit_attribution = _compute_exit_attribution(trades)
        data_coverage = _compute_data_coverage(symbols, start_date, end_date, equity_curve)

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
            avg_hold_hours=avg_hold_hours,
            equity_curve=equity_curve,
            trades=[t.to_dict() for t in trades],
            per_symbol_stats=per_symbol,
            interval=interval,
            slippage_bps=slippage_bps,
            exit_attribution=exit_attribution,
            data_coverage=data_coverage,
            diagnostics=diagnostics or {},
        )


def _compute_exit_attribution(trades: list[TradeRecord]) -> dict:
    """Aggregate exit reasons into counts and pnl breakdowns."""
    counts: dict[str, int] = {}
    pnl_by_reason: dict[str, float] = {}
    for t in trades:
        reason = t.reason or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
        pnl_by_reason[reason] = pnl_by_reason.get(reason, 0.0) + t.pnl
    total = len(trades) if trades else 1
    return {
        "counts": counts,
        "pnl_by_reason": {k: round(v, 2) for k, v in pnl_by_reason.items()},
        "pct_by_reason": {k: round(v / total * 100, 1) for k, v in counts.items()},
    }


def _compute_data_coverage(
    symbols: list[str],
    start_date: str,
    end_date: str,
    equity_curve: list[dict],
) -> dict:
    """Compute data coverage metrics for the backtest period."""
    from datetime import datetime, timedelta

    try:
        start_dt = datetime.fromisoformat(start_date.split("T")[0])
        end_dt = datetime.fromisoformat(end_date.split("T")[0])
        requested_days = max(1, (end_dt - start_dt).days)
    except Exception:
        requested_days = 0

    bars = len(equity_curve)
    actual_start = equity_curve[0]["date"] if equity_curve else ""
    actual_end = equity_curve[-1]["date"] if equity_curve else ""

    try:
        actual_start_dt = datetime.fromisoformat(actual_start.split("T")[0])
        actual_end_dt = datetime.fromisoformat(actual_end.split("T")[0])
        actual_days = max(1, (actual_end_dt - actual_start_dt).days)
    except Exception:
        actual_days = 0

    coverage_pct = (actual_days / requested_days * 100) if requested_days > 0 else 0.0

    return {
        "symbols_requested": len(symbols),
        "bars_in_equity_curve": bars,
        "requested_start": start_date,
        "requested_end": end_date,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "requested_days": requested_days,
        "actual_days": actual_days,
        "coverage_pct": round(coverage_pct, 1),
    }
