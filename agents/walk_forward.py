"""Walk-forward experiment engine for chronological parameter validation.

Splits historical data into sequential train/test windows, runs backtests on
each window with candidate parameter sets, and scores candidates by
out-of-sample performance. No look-ahead bias — each test window only uses
data that would have been available at that point in time.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from crypto_scan_backtester import CryptoScanBacktester
from backtest_report import BacktestReport

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass
class WalkForwardResult:
    window: WalkForwardWindow
    candidate_id: str
    report: BacktestReport
    score: float
    passed: bool
    fail_reasons: list[str] = field(default_factory=list)


@dataclass
class WalkForwardSummary:
    candidate_id: str
    windows_run: int
    windows_passed: int
    avg_score: float
    avg_return_pct: float
    avg_sharpe: float
    max_drawdown_pct: float
    total_trades: int
    win_rate: float
    passed: bool
    results: list[WalkForwardResult] = field(default_factory=list)


def generate_windows(
    start_date: str,
    end_date: str,
    train_days: int = 90,
    test_days: int = 30,
    step_days: int = 30,
) -> list[WalkForwardWindow]:
    """Generate chronological walk-forward windows.

    Each window has a train period and a subsequent test period.
    Windows step forward by step_days, overlapping train periods.
    """
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    windows: list[WalkForwardWindow] = []
    window_id = 0
    current = start

    while current + timedelta(days=train_days + test_days) <= end:
        train_start = current
        train_end = current + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)

        windows.append(WalkForwardWindow(
            window_id=window_id,
            train_start=train_start.strftime("%Y-%m-%d"),
            train_end=train_end.strftime("%Y-%m-%d"),
            test_start=test_start.strftime("%Y-%m-%d"),
            test_end=test_end.strftime("%Y-%m-%d"),
        ))
        window_id += 1
        current += timedelta(days=step_days)

    return windows


def _score_report(report: BacktestReport) -> float:
    """Score a backtest report. Higher is better.

    Combines return, Sharpe ratio, and penalizes drawdown.
    """
    if report.total_trades == 0:
        return -100.0

    ret = report.total_return_pct
    sharpe = report.sharpe_ratio
    dd = abs(report.max_drawdown_pct)
    win_rate = report.win_rate

    score = (ret * 0.4) + (sharpe * 20.0) - (dd * 0.3) + (win_rate * 0.1)
    return round(score, 2)


def _check_gates(report: BacktestReport, gates: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check whether a report passes promotion gates."""
    fails: list[str] = []
    min_trades = gates.get("min_trades", 5)
    min_return = gates.get("min_return_pct", 0.0)
    max_drawdown = gates.get("max_drawdown_pct", 25.0)
    min_sharpe = gates.get("min_sharpe", -0.5)

    if report.total_trades < min_trades:
        fails.append(f"trades_{report.total_trades}_<{min_trades}")
    if report.total_return_pct < min_return:
        fails.append(f"return_{report.total_return_pct:.1f}_<{min_return}")
    if abs(report.max_drawdown_pct) > max_drawdown:
        fails.append(f"drawdown_{abs(report.max_drawdown_pct):.1f}_>{max_drawdown}")
    if report.sharpe_ratio < min_sharpe:
        fails.append(f"sharpe_{report.sharpe_ratio:.2f}_<{min_sharpe}")

    return len(fails) == 0, fails


DEFAULT_GATES: dict[str, Any] = {
    "min_trades": 5,
    "min_return_pct": 0.0,
    "max_drawdown_pct": 25.0,
    "min_sharpe": -0.5,
    "min_windows_passed": 0.6,
}


def run_walk_forward(
    symbols: list[str],
    base_params: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
    train_days: int = 90,
    test_days: int = 30,
    step_days: int = 30,
    initial_capital: float = 100000.0,
    interval: str = "4h",
    slippage_bps: float = 5.0,
    gates: dict[str, Any] | None = None,
) -> dict[str, WalkForwardSummary]:
    """Run walk-forward experiments for multiple parameter candidates.

    Args:
        symbols: List of symbols to test
        base_params: Base strategy parameters
        candidates: Dict of candidate_id -> override params to merge into base
        start_date, end_date: Overall date range
        train_days, test_days, step_days: Window configuration
        gates: Promotion gates (defaults to DEFAULT_GATES)

    Returns:
        Dict of candidate_id -> WalkForwardSummary
    """
    gates = gates or DEFAULT_GATES
    windows = generate_windows(start_date, end_date, train_days, test_days, step_days)

    if not windows:
        logger.warning("No walk-forward windows generated for %s to %s", start_date, end_date)
        return {}

    summaries: dict[str, WalkForwardSummary] = {}

    for cand_id, override in candidates.items():
        params = copy.deepcopy(base_params)
        from strategy_registry import deep_merge
        params = deep_merge(params, override)

        results: list[WalkForwardResult] = []
        for window in windows:
            try:
                bt = CryptoScanBacktester(
                    symbols=symbols,
                    params=params,
                    start_date=window.test_start,
                    end_date=window.test_end,
                    initial_capital=initial_capital,
                    interval=interval,
                    slippage_bps=slippage_bps,
                )
                report = bt.run()
                score = _score_report(report)
                passed, fails = _check_gates(report, gates)
                results.append(WalkForwardResult(
                    window=window,
                    candidate_id=cand_id,
                    report=report,
                    score=score,
                    passed=passed,
                    fail_reasons=fails,
                ))
            except Exception as exc:
                logger.error("Walk-forward window %d failed for %s: %s", window.window_id, cand_id, exc)

        if not results:
            continue

        windows_passed = sum(1 for r in results if r.passed)
        pass_rate = windows_passed / len(results) if results else 0.0
        avg_score = sum(r.score for r in results) / len(results)
        avg_return = sum(r.report.total_return_pct for r in results) / len(results)
        avg_sharpe = sum(r.report.sharpe_ratio for r in results) / len(results)
        max_dd = max(abs(r.report.max_drawdown_pct) for r in results)
        total_trades = sum(r.report.total_trades for r in results)
        wins = sum(r.report.winning_trades for r in results)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        min_windows_passed = gates.get("min_windows_passed", 0.6)
        overall_passed = pass_rate >= min_windows_passed

        summaries[cand_id] = WalkForwardSummary(
            candidate_id=cand_id,
            windows_run=len(results),
            windows_passed=windows_passed,
            avg_score=round(avg_score, 2),
            avg_return_pct=round(avg_return, 2),
            avg_sharpe=round(avg_sharpe, 2),
            max_drawdown_pct=round(max_dd, 2),
            total_trades=total_trades,
            win_rate=round(win_rate, 1),
            passed=overall_passed,
            results=results,
        )

    return summaries


def summarize_walk_forward(summaries: dict[str, WalkForwardSummary]) -> dict[str, Any]:
    """Produce a JSON-serializable summary of walk-forward results."""
    return {
        cand_id: {
            "windows_run": s.windows_run,
            "windows_passed": s.windows_passed,
            "pass_rate": round(s.windows_passed / s.windows_run if s.windows_run else 0, 3),
            "avg_score": s.avg_score,
            "avg_return_pct": s.avg_return_pct,
            "avg_sharpe": s.avg_sharpe,
            "max_drawdown_pct": s.max_drawdown_pct,
            "total_trades": s.total_trades,
            "win_rate": s.win_rate,
            "passed": s.passed,
            "window_details": [
                {
                    "window_id": r.window.window_id,
                    "test_start": r.window.test_start,
                    "test_end": r.window.test_end,
                    "score": r.score,
                    "passed": r.passed,
                    "return_pct": r.report.total_return_pct,
                    "sharpe": r.report.sharpe_ratio,
                    "max_dd_pct": r.report.max_drawdown_pct,
                    "trades": r.report.total_trades,
                    "fail_reasons": r.fail_reasons,
                }
                for r in s.results
            ],
        }
        for cand_id, s in summaries.items()
    }
