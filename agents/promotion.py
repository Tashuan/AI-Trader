"""Promotion gates and rollback for test-to-agent parameter promotion.

Evaluates walk-forward results against configurable gates, promotes winning
candidates to the live agent DB via the existing PATCH endpoint, and supports
rollback to the previous config using local JSON backups.

Promotion is NEVER automatic — it requires explicit user confirmation.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from walk_forward import WalkForwardSummary, DEFAULT_GATES

logger = logging.getLogger(__name__)


PROMOTION_GATES: dict[str, Any] = {
    "min_trades": 5,
    "min_return_pct": 0.0,
    "max_drawdown_pct": 25.0,
    "min_sharpe": -0.5,
    "min_windows_passed": 0.6,
    "min_avg_score": -10.0,
}


@dataclass
class PromotionEvaluation:
    candidate_id: str
    passed: bool
    gate_results: dict[str, bool]
    fail_reasons: list[str] = field(default_factory=list)
    summary: WalkForwardSummary | None = None


def evaluate_promotion(
    summaries: dict[str, WalkForwardSummary],
    gates: dict[str, Any] | None = None,
) -> dict[str, PromotionEvaluation]:
    """Evaluate all candidates against promotion gates.

    Returns dict of candidate_id -> PromotionEvaluation.
    """
    gates = gates or PROMOTION_GATES
    evaluations: dict[str, PromotionEvaluation] = {}

    for cand_id, summary in summaries.items():
        gate_results: dict[str, bool] = {}
        fails: list[str] = []

        min_trades = gates.get("min_trades", 5)
        ok = summary.total_trades >= min_trades
        gate_results["min_trades"] = ok
        if not ok:
            fails.append(f"total_trades_{summary.total_trades}_<{min_trades}")

        min_return = gates.get("min_return_pct", 0.0)
        ok = summary.avg_return_pct >= min_return
        gate_results["min_return_pct"] = ok
        if not ok:
            fails.append(f"avg_return_{summary.avg_return_pct:.1f}_<{min_return}")

        max_dd = gates.get("max_drawdown_pct", 25.0)
        ok = summary.max_drawdown_pct <= max_dd
        gate_results["max_drawdown_pct"] = ok
        if not ok:
            fails.append(f"max_dd_{summary.max_drawdown_pct:.1f}_>{max_dd}")

        min_sharpe = gates.get("min_sharpe", -0.5)
        ok = summary.avg_sharpe >= min_sharpe
        gate_results["min_sharpe"] = ok
        if not ok:
            fails.append(f"avg_sharpe_{summary.avg_sharpe:.2f}_<{min_sharpe}")

        min_windows = gates.get("min_windows_passed", 0.6)
        pass_rate = summary.windows_passed / summary.windows_run if summary.windows_run else 0
        ok = pass_rate >= min_windows
        gate_results["min_windows_passed"] = ok
        if not ok:
            fails.append(f"pass_rate_{pass_rate:.2f}_<{min_windows}")

        min_score = gates.get("min_avg_score", -10.0)
        ok = summary.avg_score >= min_score
        gate_results["min_avg_score"] = ok
        if not ok:
            fails.append(f"avg_score_{summary.avg_score:.1f}_<{min_score}")

        evaluations[cand_id] = PromotionEvaluation(
            candidate_id=cand_id,
            passed=len(fails) == 0,
            gate_results=gate_results,
            fail_reasons=fails,
            summary=summary,
        )

    return evaluations


def select_best_candidate(
    evaluations: dict[str, PromotionEvaluation],
) -> str | None:
    """Select the best passing candidate by avg_score. Returns candidate_id or None."""
    passing = [(cand_id, ev) for cand_id, ev in evaluations.items() if ev.passed]
    if not passing:
        return None
    passing.sort(key=lambda x: x[1].summary.avg_score if x[1].summary else -999, reverse=True)
    return passing[0][0]


def promotion_preview(
    evaluations: dict[str, PromotionEvaluation],
    candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Generate a JSON-serializable preview of promotion candidates."""
    best_id = select_best_candidate(evaluations)
    return {
        "best_candidate": best_id,
        "candidates": {
            cand_id: {
                "passed": ev.passed,
                "gate_results": ev.gate_results,
                "fail_reasons": ev.fail_reasons,
                "avg_score": ev.summary.avg_score if ev.summary else None,
                "avg_return_pct": ev.summary.avg_return_pct if ev.summary else None,
                "avg_sharpe": ev.summary.avg_sharpe if ev.summary else None,
                "windows_passed": ev.summary.windows_passed if ev.summary else 0,
                "windows_run": ev.summary.windows_run if ev.summary else 0,
                "params_preview": candidates.get(cand_id, {}),
            }
            for cand_id, ev in evaluations.items()
        },
    }


def rollback_params(
    agent_name: str,
    config_backup_path: str | None = None,
) -> dict[str, Any] | None:
    """Load previous params from local JSON backup for rollback.

    Returns the effective_strategy_params dict if valid, or None.
    Does NOT write to DB — caller must use the existing PATCH endpoint
    after explicit user confirmation.
    """
    import sys
    import os
    _server_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "service", "server",
    )
    if _server_dir not in sys.path:
        sys.path.insert(0, _server_dir)

    from config_backup import restore_agent_config
    return restore_agent_config(agent_name, config_backup_path)
