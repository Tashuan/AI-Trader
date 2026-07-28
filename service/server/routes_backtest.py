"""
Backtest API Routes — Endpoints for running strategy backtests.

POST /api/backtest/run      — Run a backtest for a given agent strategy
GET  /api/backtest/strategies — List available strategies for backtesting
"""

import os
import sys
import logging
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from routes_shared import RouteContext

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
    return registry


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
        agent_class = info["agent_class"]

        try:
            from personality import PERSONALITIES
            from backtester import Backtester
        except ImportError as e:
            raise HTTPException(status_code=500, detail=f"Backtester module not available: {e}")

        personality = PERSONALITIES[req.agent_key]
        symbols = req.symbols or list(personality.watchlist)

        bt = Backtester(
            agent_class=agent_class,
            personality=personality,
            symbols=symbols,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
        )

        report = bt.run()
        return {"report": report.to_dict()}
