"""
Backtest API Routes — Endpoints for running strategy backtests.

POST /api/backtest/run      — Run a backtest for a given agent strategy
GET  /api/backtest/strategies — List available strategies for backtesting
"""

import os
import sys
import json
import logging
from dataclasses import replace as dc_replace
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from routes_shared import RouteContext
from database import get_db_connection

logger = logging.getLogger(__name__)

# Add agents directory to path for imports
_AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agents")
if _AGENTS_DIR not in sys.path:
    sys.path.insert(0, _AGENTS_DIR)


class BacktestRequest(BaseModel):
    agent_key: str
    symbols: Optional[list[str]] = None
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 100000.0
    interval: str = "1d"
    slippage_bps: float = 5.0
    goal_target: Optional[float] = None  # Dollar profit target for goal-aware sizing


class WalkForwardRequest(BaseModel):
    agent_key: str
    symbols: list[str]
    start_date: str
    end_date: str
    candidates: dict[str, dict] = {}
    train_days: int = 90
    test_days: int = 30
    step_days: int = 30
    initial_capital: float = 100000.0
    interval: str = "4h"
    slippage_bps: float = 5.0
    gates: Optional[dict] = None


def _get_strategy_registry() -> dict:
    """Build a registry of available agent strategies for backtesting."""
    try:
        from personality import PERSONALITIES
        from strategy_news import NewsHoundAgent
        from strategy_technical import ChartMasterAgent
        from strategy_contrarian import FadeMasterAgent
        from strategy_momentum import BlitzTraderAgent
    except ImportError as e:
        logger.error(f"Failed to import agent modules: {e}")
        return {}

    agent_classes = {
        "newshound": NewsHoundAgent,
        "chartmaster": ChartMasterAgent,
        "fademaster": FadeMasterAgent,
        "blitztrader": BlitzTraderAgent,
    }

    registry = {}
    for key, personality in PERSONALITIES.items():
        agent_class = agent_classes.get(key)
        if agent_class:
            registry[key] = {
                "name": personality.name,
                "tagline": personality.tagline,
                "strategy_type": personality.strategy_type,
                "watchlist": personality.watchlist,
                "risk_tolerance": personality.risk_tolerance,
                "hold_period": personality.hold_period,
                "agent_class": agent_class,
            }
    registry.update({
        "blitzrunner": {
            "name": "BlitzRunner", "tagline": "Deterministic equity momentum runner",
            "strategy_type": "momentum_scalp", "watchlist": ["NVDA", "TSLA", "META", "AMZN"],
            "risk_tolerance": "moderate", "hold_period": "swing", "runner": True,
        },
        "cryptorunner": {
            "name": "CryptoRunner", "tagline": "Deterministic crypto swing runner",
            "strategy_type": "crypto_swing", "watchlist": ["BTC", "ETH", "SOL", "DOGE", "AVAX", "XRP", "LINK"],
            "risk_tolerance": "moderate", "hold_period": "swing", "runner": True,
        },
    })
    return registry


def _load_personality_from_db(agent_key: str, fallback_personality):
    """Load agent config from the database and override the fallback Personality.

    If the agent has a config row in agent_configs, use those values to override
    the hardcoded PERSONALITIES defaults. This ensures the backtest uses the same
    parameters the user configured via the agent manager form.
    """
    try:
        from personality import Personality
        name = fallback_personality.name
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT c.* FROM agent_configs c
               JOIN agents a ON a.id = c.agent_id
               WHERE a.name = ?''',
            (name,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return fallback_personality

        overrides = {}
        if row.get('risk_tolerance') is not None:
            overrides['risk_tolerance'] = row['risk_tolerance']
        if row.get('position_sizing') is not None:
            overrides['position_sizing'] = row['position_sizing']
        if row.get('hold_period') is not None:
            overrides['hold_period'] = row['hold_period']
        if row.get('max_positions') is not None:
            overrides['max_positions'] = row['max_positions']
        if row.get('confidence_threshold') is not None:
            overrides['confidence_threshold'] = row['confidence_threshold']
        if row.get('fomo_resistance') is not None:
            overrides['fomo_resistance'] = row['fomo_resistance']
        if row.get('loss_aversion') is not None:
            overrides['loss_aversion'] = row['loss_aversion']
        if row.get('conviction_multiplier') is not None:
            overrides['conviction_multiplier'] = row['conviction_multiplier']
        if row.get('watchlist_json'):
            try:
                overrides['watchlist'] = json.loads(row['watchlist_json'])
            except (json.JSONDecodeError, TypeError):
                pass

        if not overrides:
            return fallback_personality

        return dc_replace(fallback_personality, **overrides)
    except Exception as e:
        logger.warning(f"Failed to load DB config for {agent_key}: {e}")
        return fallback_personality


def _load_runner_params(name: str, strategy_type: str) -> dict:
    """Resolve stored overrides with the runner's agent-specific defaults."""
    from strategy_registry import effective_params

    stored = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT c.config_json FROM agent_configs c "
            "JOIN agents a ON a.id = c.agent_id WHERE a.name = ?",
            (name,),
        )
        row = cursor.fetchone()
        conn.close()
        if row and row["config_json"]:
            stored = json.loads(row["config_json"]).get("strategy_params", {})
    except Exception as exc:
        logger.warning(f"Failed to load {name} params from DB: {exc}")
    return effective_params(name, strategy_type, stored)


def register_backtest_routes(app: FastAPI, ctx: RouteContext) -> None:

    @app.get("/api/backtest/strategies")
    async def list_backtest_strategies():
        """List available agent strategies for backtesting."""
        registry = _get_strategy_registry()
        if not registry:
            raise HTTPException(status_code=500, detail="Agent modules not available")

        strategies = []
        for key, info in registry.items():
            strategies.append({
                "key": key,
                "name": info["name"],
                "tagline": info["tagline"],
                "strategy_type": info["strategy_type"],
                "watchlist": info["watchlist"],
                "risk_tolerance": info["risk_tolerance"],
                "hold_period": info["hold_period"],
            })
        return {"strategies": strategies}

    @app.post("/api/backtest/run")
    async def run_backtest(req: BacktestRequest):
        """Run a backtest for the specified agent strategy."""
        registry = _get_strategy_registry()
        if not registry:
            raise HTTPException(status_code=500, detail="Agent modules not available")

        if req.agent_key not in registry:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown agent_key: {req.agent_key}. Available: {list(registry.keys())}"
            )

        info = registry[req.agent_key]

        if req.agent_key in {"blitzrunner", "cryptorunner"}:
            import asyncio
            if req.agent_key == "cryptorunner":
                from crypto_scan_backtester import CryptoScanBacktester
                params = _load_runner_params("CryptoRunner", "crypto_swing")
                bt = CryptoScanBacktester(
                    symbols=req.symbols or info["watchlist"], params=params,
                    start_date=req.start_date, end_date=req.end_date,
                    initial_capital=req.initial_capital, interval=req.interval or "4h",
                    slippage_bps=req.slippage_bps,
                )
            else:
                from scan_backtester import ScanBacktester
                params = _load_runner_params("BlitzRunner", "momentum_scalp")
                bt = ScanBacktester(
                    symbols=req.symbols or info["watchlist"], params=params,
                    start_date=req.start_date, end_date=req.end_date,
                    initial_capital=req.initial_capital, interval=req.interval or "1h",
                    slippage_bps=req.slippage_bps, goal_target=req.goal_target,
                )
            report = await asyncio.to_thread(bt.run)
            return {"report": report.to_dict()}

        try:
            from personality import PERSONALITIES
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Agent modules not available: {e}")

        personality = _load_personality_from_db(req.agent_key, PERSONALITIES[req.agent_key])
        symbols = req.symbols or list(personality.watchlist)

        # BlitzTrader uses the ScanBacktester which replays scan_core logic
        # (15-indicator deep scan, Goal Runner state machine) with DB-stored
        # strategy params — full parity with the live agent.
        if req.agent_key == "blitztrader":
            try:
                import scan_core
                from scan_backtester import ScanBacktester
            except ImportError as e:
                raise HTTPException(status_code=500, detail=f"ScanBacktester module not available: {e}")

            params = _load_runner_params("BlitzTrader", "momentum_scalp")
            bt = ScanBacktester(
                symbols=symbols,
                params=params,
                start_date=req.start_date,
                end_date=req.end_date,
                initial_capital=req.initial_capital,
                interval=req.interval,
                slippage_bps=req.slippage_bps,
                goal_target=req.goal_target,
            )
            import asyncio
            report = await asyncio.to_thread(bt.run)
            return {"report": report.to_dict()}

        # Legacy path for other agents
        try:
            from backtester import Backtester
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Backtester module not available: {e}")

        agent_class = info["agent_class"]
        bt = Backtester(
            agent_class=agent_class,
            personality=personality,
            symbols=symbols,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            interval=req.interval,
            slippage_bps=req.slippage_bps,
        )

        import asyncio
        report = await asyncio.to_thread(bt.run)
        return {"report": report.to_dict()}

    @app.post("/api/backtest/diagnose")
    async def diagnose_backtest(report: dict):
        """Generate LLM-powered strategy diagnosis from backtest results."""
        try:
            from llm_client import get_llm_client
        except ImportError:
            return {"available": False, "diagnosis": None}

        llm = get_llm_client()
        if not llm.is_configured:
            return {"available": False, "diagnosis": None}

        r = report
        sym_stats = r.get("per_symbol_stats", {})
        top_syms = sorted(sym_stats.items(), key=lambda x: x[1].get("total_pnl", 0), reverse=True)[:5]

        prompt = (
            f"You are a quantitative trading strategy analyst. Analyze this backtest report and provide a diagnosis.\n\n"
            f"STRATEGY: {r.get('agent_name', 'Unknown')}\n"
            f"PERIOD: {r.get('start_date', '?')} to {r.get('end_date', '?')}\n"
            f"SYMBOLS: {', '.join(r.get('symbols', []))}\n"
            f"INITIAL CAPITAL: ${r.get('initial_capital', 0):,.0f}\n\n"
            f"RESULTS:\n"
            f"  Total Return: {r.get('total_return_pct', 0):.2f}%\n"
            f"  Final Equity: ${r.get('final_equity', 0):,.2f}\n"
            f"  Sharpe Ratio: {r.get('sharpe_ratio', 0):.3f}\n"
            f"  Max Drawdown: {r.get('max_drawdown_pct', 0):.2f}%\n"
            f"  Win Rate: {r.get('win_rate', 0) * 100:.1f}% ({r.get('winning_trades', 0)}W / {r.get('losing_trades', 0)}L)\n"
            f"  Profit Factor: {r.get('profit_factor', 0):.3f}\n"
            f"  Total Trades: {r.get('total_trades', 0)}\n"
            f"  Avg Hold Days: {r.get('avg_hold_days', 0):.1f}\n\n"
            f"TOP SYMBOLS BY P&L:\n"
        )
        for sym, s in top_syms:
            prompt += f"  {sym}: {s.get('trades', 0)} trades, {s.get('win_rate', 0) * 100:.0f}% win, P&L ${s.get('total_pnl', 0):,.0f}\n"

        prompt += (
            f"\nProvide your analysis in this format:\n"
            f"1. VERDICT: One sentence overall assessment (profitable/unprofitable/marginal)\n"
            f"2. STRENGTHS: 2-3 bullet points on what's working\n"
            f"3. WEAKNESSES: 2-3 bullet points on what's broken\n"
            f"4. RECOMMENDATIONS: 2-3 specific, actionable changes with parameter names\n"
            f"Keep it concise and data-driven. No fluff."
        )

        system = (
            "You are a quantitative trading strategy analyst. You analyze backtest results "
            "and provide actionable, specific recommendations. You reference exact parameters "
            "like PROFIT_TARGET_PCT, STOP_LOSS_PCT, confidence_threshold, position_sizing, "
            "conviction_multiplier, and watchlist. Be direct and honest."
        )

        try:
            diagnosis = llm.generate(prompt, system=system, max_tokens=600, temperature=0.4)
            if diagnosis:
                return {"available": True, "diagnosis": diagnosis}
            return {"available": False, "diagnosis": None}
        except Exception as e:
            logger.error(f"LLM diagnosis error: {e}")
            return {"available": False, "diagnosis": None}

    @app.post("/api/backtest/walk-forward")
    async def run_walk_forward(data: WalkForwardRequest):
        """Run walk-forward experiment with multiple parameter candidates."""
        try:
            from walk_forward import run_walk_forward as _run_wf, summarize_walk_forward
            from strategy_registry import effective_params

            base_params = _load_runner_params_by_key(data.agent_key)
            if not base_params:
                raise HTTPException(status_code=400, detail=f"Unknown agent: {data.agent_key}")

            candidates = data.candidates or {"baseline": {}}

            summaries = _run_wf(
                symbols=data.symbols,
                base_params=base_params,
                candidates=candidates,
                start_date=data.start_date,
                end_date=data.end_date,
                train_days=data.train_days,
                test_days=data.test_days,
                step_days=data.step_days,
                initial_capital=data.initial_capital,
                interval=data.interval,
                slippage_bps=data.slippage_bps,
                gates=data.gates,
            )

            return {"summary": summarize_walk_forward(summaries)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Walk-forward error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    def _load_runner_params_by_key(agent_key: str) -> dict | None:
        """Load runner params by agent key (e.g. 'cryptorunner', 'blitzrunner')."""
        key_map = {
            "cryptorunner": ("CryptoRunner", "crypto_swing"),
            "blitzrunner": ("BlitzRunner", "equity_momentum"),
        }
        if agent_key.lower() not in key_map:
            return None
        name, strategy_type = key_map[agent_key.lower()]
        return _load_runner_params(name, strategy_type)

    @app.post("/api/backtest/promote")
    async def promote_candidate(data: dict):
        """Evaluate walk-forward results and preview promotion candidates.

        Body: { agent_key, symbols, start_date, end_date, candidates, ... }
        Returns promotion evaluation without applying anything.
        """
        try:
            from walk_forward import run_walk_forward as _run_wf
            from promotion import evaluate_promotion, promotion_preview

            base_params = _load_runner_params_by_key(data.get("agent_key", ""))
            if not base_params:
                raise HTTPException(status_code=400, detail=f"Unknown agent: {data.get('agent_key')}")

            candidates = data.get("candidates") or {"baseline": {}}
            summaries = _run_wf(
                symbols=data.get("symbols", []),
                base_params=base_params,
                candidates=candidates,
                start_date=data.get("start_date", ""),
                end_date=data.get("end_date", ""),
                initial_capital=data.get("initial_capital", 100000),
                interval=data.get("interval", "4h"),
            )
            evaluations = evaluate_promotion(summaries)
            return {"preview": promotion_preview(evaluations, candidates)}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Promotion evaluation error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/backtest/rollback")
    async def rollback_config(data: dict):
        """Load previous config from local JSON backup for rollback.

        Body: { agent_name }
        Returns the backed-up params without applying them.
        User must confirm via PATCH endpoint to actually roll back.
        """
        try:
            from promotion import rollback_params

            agent_name = data.get("agent_name", "")
            if not agent_name:
                raise HTTPException(status_code=400, detail="agent_name required")

            params = rollback_params(agent_name)
            if params is None:
                raise HTTPException(status_code=404, detail="No backup found for agent")

            return {"strategy_params": params, "agent_name": agent_name}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Rollback error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))
