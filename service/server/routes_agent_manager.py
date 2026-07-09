"""
Agent Manager Routes

Full CRUD + stats + file generation for managing AI trading agents.
Stores agent configuration in the database and optionally generates
instruction markdown files and personality entries.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException

from database import get_db_connection
from routes_models import AgentConfigCreate, AgentConfigUpdate
from routes_shared import RouteContext, utc_now_iso_z
from services import _get_agent_by_id, _get_agent_by_name, _get_agent_by_token
from utils import _extract_token, hash_password

# Project root (two levels up from service/server)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AGENTS_DIR = _PROJECT_ROOT / "agents"

STRATEGY_TEMPLATES = {
    "news_sentiment": {
        "label": "News Sentiment",
        "description": "Trades based on news headlines and sentiment analysis",
        "strategy_file": "strategy_news.py",
        "default_watchlist": ["BTC", "ETH", "SOL", "NVDA", "AAPL", "TSLA"],
        "default_voice": "analytical and news-driven, cites specific stories",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.55,
    },
    "technical_analysis": {
        "label": "Technical Analysis",
        "description": "Trades based on chart patterns, RSI, Bollinger Bands, and volume",
        "strategy_file": "strategy_technical.py",
        "default_watchlist": ["BTC", "ETH", "SOL", "NVDA", "AAPL", "AMZN", "MSFT", "TSLA"],
        "default_voice": "precise, indicator-focused, references specific levels",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.65,
    },
    "contrarian": {
        "label": "Contrarian / Fade",
        "description": "Fades market extremes — buys panic, sells euphoria",
        "strategy_file": "strategy_contrarian.py",
        "default_watchlist": ["BTC", "ETH", "SOL", "DOGE", "NVDA", "TSLA", "AMD"],
        "default_voice": "contrarian, provocative, challenges consensus",
        "default_risk": "aggressive",
        "default_hold": "scalp",
        "default_confidence": 0.60,
    },
    "momentum": {
        "label": "Momentum / Trend Following",
        "description": "Follows breakouts and trends with trailing stops",
        "strategy_file": "strategy_technical.py",
        "default_watchlist": ["BTC", "ETH", "SOL", "AVAX", "NVDA", "TSLA", "META", "AMZN"],
        "default_voice": "trend-focused, momentum-confirming, trailing stops",
        "default_risk": "aggressive",
        "default_hold": "position",
        "default_confidence": 0.70,
    },
    "momentum_scalp": {
        "label": "Momentum Scalp (Blitz)",
        "description": "Reckless momentum scalper — chases volume spikes and breakouts, in and out in minutes",
        "strategy_file": "strategy_momentum.py",
        "default_watchlist": ["BTC", "ETH", "SOL", "DOGE", "NVDA", "TSLA", "AMD", "META", "AMZN", "AVAX"],
        "default_voice": "fast-talking, hyperactive, references speed and momentum constantly",
        "default_risk": "degen",
        "default_hold": "scalp",
        "default_confidence": 0.35,
    },
    "copy_trader": {
        "label": "Copy Trader",
        "description": "Follows and copies the best performing agents on the platform",
        "strategy_file": None,
        "default_watchlist": ["BTC", "ETH", "SOL", "NVDA", "AAPL"],
        "default_voice": "data-driven, references other agents' performance",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.50,
    },
    "stat_arb": {
        "label": "Statistical Arbitrage / Pairs",
        "description": "Trades correlated asset pairs when spread deviates beyond statistical norms",
        "strategy_file": None,
        "default_watchlist": ["BTC", "ETH", "SOL", "NVDA", "AMD", "AAPL", "MSFT"],
        "default_voice": "mathematical, correlation-focused, references z-scores and convergence",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.65,
    },
    "event_driven": {
        "label": "Event-Driven / Calendar",
        "description": "Pre-positions around scheduled catalysts — earnings, FOMC, CPI, crypto events",
        "strategy_file": None,
        "default_watchlist": ["BTC", "ETH", "SOL", "NVDA", "AAPL", "TSLA", "META", "AMZN", "MSFT", "GOOGL"],
        "default_voice": "calendar-aware, catalyst-focused, references specific events and dates",
        "default_risk": "moderate",
        "default_hold": "scalp",
        "default_confidence": 0.60,
    },
    "range": {
        "label": "Range / Grid Trading",
        "description": "Trades established ranges — buys support, sells resistance, exits on range breaks",
        "strategy_file": None,
        "default_watchlist": ["BTC", "ETH", "SOL", "DOGE", "NVDA", "AAPL", "MSFT", "AMZN", "META", "AMD"],
        "default_voice": "patient, range-focused, references ADX and support/resistance levels",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.60,
    },
    "custom": {
        "label": "Custom",
        "description": "Define your own strategy — no preset template",
        "strategy_file": None,
        "default_watchlist": ["BTC", "ETH", "SOL"],
        "default_voice": "",
        "default_risk": "moderate",
        "default_hold": "swing",
        "default_confidence": 0.60,
    },
}

RISK_TOLERANCES = ["conservative", "moderate", "aggressive", "degen"]
POSITION_SIZINGS = ["small", "medium", "large", "yolo"]
HOLD_PERIODS = ["scalp", "swing", "position", "long-term"]
EMOJI_FREQUENCIES = ["none", "rare", "frequent", "excessive"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _row_to_config(row) -> Dict[str, Any]:
    """Convert a DB row to a config dict with parsed JSON fields."""
    result = dict(row)
    for key in ('watchlist_json', 'quirks_json', 'config_json'):
        if result.get(key):
            try:
                result[key.replace('_json', '')] = json.loads(result[key])
            except (json.JSONDecodeError, TypeError):
                result[key.replace('_json', '')] = []
        else:
            result[key.replace('_json', '')] = [] if key != 'config_json' else {}
        result.pop(key, None)
    result['publishes_reasoning'] = bool(result.get('publishes_reasoning', 1))
    result['trash_talk'] = bool(result.get('trash_talk', 0))
    result['auto_start'] = bool(result.get('auto_start', 0))
    return result


def _ensure_agent_stats(agent_id: int, cursor) -> None:
    """Create a stats row if it doesn't exist."""
    cursor.execute('SELECT id FROM agent_stats WHERE agent_id = ?', (agent_id,))
    if not cursor.fetchone():
        cursor.execute(
            'INSERT INTO agent_stats (agent_id, last_stat_update) VALUES (?, ?)',
            (agent_id, _now_iso()),
        )


def _generate_instruction_md(config: Dict[str, Any]) -> str:
    """Generate AGENT_INSTRUCTIONS_{Name}.md content from config."""
    name = config['name']
    tagline = config.get('tagline', '')
    bio = config.get('bio', '')
    risk = config.get('risk_tolerance', 'moderate')
    hold = config.get('hold_period', 'swing')
    max_pos = config.get('max_positions', 6)
    strategy_type = config.get('strategy_type', 'custom')
    watchlist = config.get('watchlist', [])
    voice = config.get('voice', '')
    emoji_freq = config.get('emoji_frequency', 'rare')
    publishes_reasoning = config.get('publishes_reasoning', True)
    trash_talk = config.get('trash_talk', False)
    quirks = config.get('quirks', [])
    email = config.get('email', f"{name.lower()}@agent.dev")
    password = config.get('password', f"{name.lower()}_pass_2026")

    template = STRATEGY_TEMPLATES.get(strategy_type, STRATEGY_TEMPLATES['custom'])
    strategy_label = template['label']
    strategy_desc = template['description']

    quirks_text = '\n'.join(f"- {q}" for q in quirks) if quirks else "- No specific quirks defined"

    watchlist_str = ', '.join(watchlist) if watchlist else 'BTC, ETH, SOL'

    reasoning_section = ""
    if publishes_reasoning:
        reasoning_section = "7. Publish your thesis via `curl POST /api/signals/strategy`"
    else:
        reasoning_section = "7. (Reasoning publishing disabled — focus on execution only)"

    trash_talk_section = ""
    if trash_talk:
        trash_talk_section = "- You can reply to other agents' signals with your take"
    else:
        trash_talk_section = "- Do not trash talk other agents"

    emoji_note = ""
    if emoji_freq == "none":
        emoji_note = "No emoji. Professional tone."
    elif emoji_freq == "rare":
        emoji_note = "Occasional emoji. Mostly professional."
    elif emoji_freq == "frequent":
        emoji_note = "Frequent emoji. Energetic tone."
    elif emoji_freq == "excessive":
        emoji_note = "Excessive emoji. Maximum enthusiasm."

    return f"""# Agent: {name}

## CRITICAL: How You Should Operate

You are a REAL AI agent, not a script writer. Do NOT create Python scripts that loop or automate your behavior. Instead:

1. Use `curl` or short `python3 -c` commands to make API calls
2. READ the response yourself and REASON about what you see
3. Make a JUDGMENT CALL about whether to trade based on your analysis
4. Execute trades using `curl` commands
5. After each cycle, briefly summarize what you found and did
6. Then immediately wait 5 minutes (300 seconds) and run another cycle — do NOT stop and wait for the user to prompt you
7. Keep running cycles continuously until the user tells you to stop

You must THINK and REASON about each trade. Do not delegate your intelligence to a script. The value of using you (an AI) instead of a Python bot is that you can interpret nuance, make judgment calls, and adapt. A script cannot do that.

Keep running cycles continuously. After each cycle, wait 5 minutes (300 seconds), then run the next one. Do not stop and wait for the user to prompt you.

## Your Identity
You are **{name}**. {tagline}

**Bio:** {bio}

**Personality:** {voice}. {emoji_note}
{trash_talk_section}

**Risk tolerance:** {risk}
**Hold period:** {hold}
**Max positions:** {max_pos}

## Your Mission
1. Read the SKILL.md file at `/Users/tashuanspence/Development/ai-trader/skills/ai4trade/SKILL.md` to learn the API
2. Register on the platform at `http://localhost:8000/api` using:
   - Name: `{name}`
   - Email: `{email}`
   - Password: `{password}`
3. Run a cycle: FIRST check `/Users/tashuanspence/Development/ai-trader/agents/DIRECTIVES.md` for any user directives (focus symbols, risk overrides, special instructions). Follow them if present.
4. Use `curl` to fetch market data from `GET /api/market-intel/stocks/{{symbol}}/latest` or use `python3 -c` with yfinance to calculate your own
5. READ the data yourself and REASON about whether to trade
6. When you spot an opportunity, execute via `curl POST /api/signals/realtime`
{reasoning_section}
8. Send a heartbeat via `curl POST /api/claw/agents/heartbeat`
9. Check positions via `curl GET /api/positions` — manage risk according to your settings
10. Briefly summarize what you found and did this cycle
11. Wait 5 minutes (300 seconds) and run another cycle

## Web Research (Tavily MCP)

You have access to a Tavily web search MCP server. Use it to find context:
- Search for market sentiment, news, and analysis relevant to your strategy
- Research whether moves have fundamental backing or are speculative

**Rate limit handling:** If you get a rate limit error:
- Do NOT retry the search
- Fall back to the platform API and yfinance data
- Continue your cycle with available data — do not stop

## Your Strategy: {strategy_label}
{strategy_desc}

## Your Watchlist
{watchlist_str}

## Behavioral Quirks
{quirks_text}

## Technical Analysis with yfinance
If the platform API doesn't return technical data, run Python to calculate it yourself:
```python
import yfinance as yf, logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
ticker = yf.Ticker("BTC-USD")
df = ticker.history(period="3mo", interval="1d", auto_adjust=False, raise_errors=False)
# Calculate RSI, Bollinger Bands, returns
```

## Important
- You are trading with **paper money** — this is a simulation
- Always explain your reasoning in your trade signals
- Check `GET /api/signals/feed` to see what others are doing
"""


def _write_instruction_file(name: str, content: str) -> str:
    """Write the AGENT_INSTRUCTIONS_{Name}.md file. Returns the file path."""
    file_path = _AGENTS_DIR / f"AGENT_INSTRUCTIONS_{name}.md"
    file_path.write_text(content, encoding='utf-8')
    return str(file_path)


def _compute_agent_stats(agent_id: int, cursor) -> Dict[str, Any]:
    """Compute live stats from existing tables (signals, positions, profit_history)."""
    # Trade count from signals (operation type, realtime)
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM signals WHERE agent_id = ? AND message_type = 'operation'",
        (agent_id,),
    )
    total_trades = cursor.fetchone()['cnt']

    # Signal counts by type
    cursor.execute(
        "SELECT message_type, COUNT(*) as cnt FROM signals WHERE agent_id = ? GROUP BY message_type",
        (agent_id,),
    )
    signal_counts = {row['message_type']: row['cnt'] for row in cursor.fetchall()}
    total_signals = signal_counts.get('operation', 0)
    total_strategies = signal_counts.get('strategy', 0)
    total_discussions = signal_counts.get('discussion', 0)

    # Reply count
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM signal_replies WHERE agent_id = ?",
        (agent_id,),
    )
    total_replies = cursor.fetchone()['cnt']

    # Current positions
    cursor.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(quantity * entry_price), 0) as pos_value FROM positions WHERE agent_id = ?",
        (agent_id,),
    )
    pos_row = cursor.fetchone()
    position_count = pos_row['cnt']
    position_value = pos_row['pos_value']

    # Profit history - latest entry
    cursor.execute(
        "SELECT total_value, cash, position_value, profit, recorded_at FROM profit_history WHERE agent_id = ? ORDER BY recorded_at DESC LIMIT 1",
        (agent_id,),
    )
    profit_row = cursor.fetchone()
    current_value = profit_row['total_value'] if profit_row else 100000.0
    current_cash = profit_row['cash'] if profit_row else 100000.0
    current_profit = profit_row['profit'] if profit_row else 0.0

    # Max drawdown from profit history
    cursor.execute(
        "SELECT MAX(profit) as max_profit, MIN(profit) as min_profit FROM profit_history WHERE agent_id = ?",
        (agent_id,),
    )
    dd_row = cursor.fetchone()
    max_drawdown = 0.0
    if dd_row and dd_row['max_profit'] is not None and dd_row['min_profit'] is not None:
        if dd_row['max_profit'] > 0:
            max_drawdown = dd_row['max_profit'] - dd_row['min_profit']

    # Last trade timestamp
    cursor.execute(
        "SELECT executed_at FROM signals WHERE agent_id = ? AND message_type = 'operation' ORDER BY executed_at DESC LIMIT 1",
        (agent_id,),
    )
    last_trade_row = cursor.fetchone()
    last_trade_at = last_trade_row['executed_at'] if last_trade_row else None

    # Followers
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM subscriptions WHERE leader_id = ? AND status = 'active'",
        (agent_id,),
    )
    follower_count = cursor.fetchone()['cnt']

    # Following
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM subscriptions WHERE follower_id = ? AND status = 'active'",
        (agent_id,),
    )
    following_count = cursor.fetchone()['cnt']

    return {
        'total_trades': total_trades,
        'total_signals': total_signals,
        'total_strategies': total_strategies,
        'total_discussions': total_discussions,
        'total_replies': total_replies,
        'position_count': position_count,
        'position_value': position_value,
        'current_value': current_value,
        'current_cash': current_cash,
        'current_profit': current_profit,
        'max_drawdown': max_drawdown,
        'last_trade_at': last_trade_at,
        'follower_count': follower_count,
        'following_count': following_count,
    }


def register_agent_manager_routes(app: FastAPI, ctx: RouteContext) -> None:
    """Register all agent manager routes."""

    @app.get('/api/agents/manage/templates')
    async def get_strategy_templates():
        return {'templates': STRATEGY_TEMPLATES}

    @app.get('/api/agents/manage/options')
    async def get_form_options():
        return {
            'risk_tolerances': RISK_TOLERANCES,
            'position_sizings': POSITION_SIZINGS,
            'hold_periods': HOLD_PERIODS,
            'emoji_frequencies': EMOJI_FREQUENCIES,
        }

    @app.post('/api/agents/manage/create')
    async def create_agent(data: AgentConfigCreate, authorization: str = Header(None)):
        agent_name = data.name.strip()
        if not agent_name:
            raise HTTPException(status_code=400, detail='Agent name is required')

        # Check for existing agent
        existing = _get_agent_by_name(agent_name)
        if existing:
            raise HTTPException(status_code=400, detail='Agent name already exists')

        # Apply template defaults if strategy_type is set
        template = STRATEGY_TEMPLATES.get(data.strategy_type, {})
        watchlist = data.watchlist or template.get('default_watchlist', ['BTC', 'ETH', 'SOL'])
        voice = data.voice or template.get('default_voice', '')
        email = data.email or f"{agent_name.lower()}@agent.dev"
        password = data.password or f"{agent_name.lower()}_pass_2026"

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Create the agent in the agents table
            password_hash = hash_password(password)
            cursor.execute(
                """
                INSERT INTO agents (name, email, password_hash, cash, deposited)
                VALUES (?, ?, ?, ?, 0)
                """,
                (agent_name, email, password_hash, data.initial_cash),
            )
            agent_id = cursor.lastrowid

            # Issue token
            agent_token = secrets.token_urlsafe(32)
            cursor.execute('UPDATE agents SET token = ? WHERE id = ?', (agent_token, agent_id))

            # Create config row
            watchlist_json = json.dumps(watchlist)
            quirks_json = json.dumps(data.quirks or [])
            cursor.execute(
                """
                INSERT INTO agent_configs (
                    agent_id, tagline, bio, risk_tolerance, position_sizing,
                    hold_period, max_positions, confidence_threshold, fomo_resistance,
                    loss_aversion, conviction_multiplier, voice, emoji_frequency,
                    publishes_reasoning, trash_talk, strategy_type, watchlist_json,
                    quirks_json, status, auto_start, poll_interval, api_base, initial_cash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id, data.tagline, data.bio, data.risk_tolerance, data.position_sizing,
                    data.hold_period, data.max_positions, data.confidence_threshold,
                    data.fomo_resistance, data.loss_aversion, data.conviction_multiplier,
                    voice, data.emoji_frequency,
                    1 if data.publishes_reasoning else 0, 1 if data.trash_talk else 0,
                    data.strategy_type, watchlist_json, quirks_json,
                    'inactive', 1 if data.auto_start else 0, data.poll_interval,
                    data.api_base, data.initial_cash,
                ),
            )

            # Create stats row
            _ensure_agent_stats(agent_id, cursor)

            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to create agent: {exc}')
        conn.close()

        # Optionally generate instruction file
        file_path = None
        if data.generate_files:
            config_for_file = {
                'name': agent_name,
                'tagline': data.tagline,
                'bio': data.bio,
                'risk_tolerance': data.risk_tolerance,
                'hold_period': data.hold_period,
                'max_positions': data.max_positions,
                'strategy_type': data.strategy_type,
                'watchlist': watchlist,
                'voice': voice,
                'emoji_frequency': data.emoji_frequency,
                'publishes_reasoning': data.publishes_reasoning,
                'trash_talk': data.trash_talk,
                'quirks': data.quirks or [],
                'email': email,
                'password': password,
            }
            try:
                md_content = _generate_instruction_md(config_for_file)
                file_path = _write_instruction_file(agent_name, md_content)
            except Exception as exc:
                file_path = f'Error generating file: {exc}'

        return {
            'success': True,
            'agent_id': agent_id,
            'name': agent_name,
            'token': agent_token,
            'email': email,
            'instruction_file': file_path,
            'message': f'Agent {agent_name} created successfully',
        }

    @app.get('/api/agents/manage/list')
    async def list_agents(
        status: Optional[str] = None,
        strategy_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        authorization: str = Header(None),
    ):

        conn = get_db_connection()
        cursor = conn.cursor()

        query = """
            SELECT a.id, a.name, a.email, a.cash, a.points, a.reputation_score,
                   a.created_at, a.updated_at,
                   c.tagline, c.bio, c.risk_tolerance, c.position_sizing,
                   c.hold_period, c.max_positions, c.strategy_type,
                   c.watchlist_json, c.quirks_json, c.status, c.auto_start,
                   c.poll_interval, c.api_base, c.voice, c.emoji_frequency,
                   c.publishes_reasoning, c.trash_talk, c.confidence_threshold,
                   c.fomo_resistance, c.loss_aversion, c.conviction_multiplier,
                   c.initial_cash
            FROM agents a
            LEFT JOIN agent_configs c ON c.agent_id = a.id
        """
        conditions = []
        params: list = []
        if status:
            conditions.append("c.status = ?")
            params.append(status)
        if strategy_type:
            conditions.append("c.strategy_type = ?")
            params.append(strategy_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY a.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        agents = []
        for row in rows:
            config = _row_to_config(row)
            # Get live stats
            stats = _compute_agent_stats(row['id'], cursor)
            agents.append({
                **config,
                **stats,
            })

        # Total count
        count_query = "SELECT COUNT(*) as cnt FROM agents a LEFT JOIN agent_configs c ON c.agent_id = a.id"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        cursor.execute(count_query, params[:-2])
        total = cursor.fetchone()['cnt']

        conn.close()
        return {'agents': agents, 'total': total}

    @app.get('/api/agents/manage/{agent_id}')
    async def get_agent_detail(agent_id: int, authorization: str = Header(None)):

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT a.*, c.*
            FROM agents a
            LEFT JOIN agent_configs c ON c.agent_id = a.id
            WHERE a.id = ?
            """,
            (agent_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail='Agent not found')

        config = _row_to_config(row)
        stats = _compute_agent_stats(agent_id, cursor)

        # Get recent trades
        cursor.execute(
            """
            SELECT signal_id, market, symbol, side, entry_price, quantity, content,
                   executed_at, created_at
            FROM signals
            WHERE agent_id = ? AND message_type = 'operation'
            ORDER BY executed_at DESC
            LIMIT 50
            """,
            (agent_id,),
        )
        recent_trades = [dict(r) for r in cursor.fetchall()]

        # Get current positions
        cursor.execute(
            """
            SELECT symbol, market, side, quantity, entry_price, current_price, opened_at
            FROM positions WHERE agent_id = ?
            ORDER BY opened_at DESC
            """,
            (agent_id,),
        )
        positions = [dict(r) for r in cursor.fetchall()]

        # Get profit history (last 30 entries)
        cursor.execute(
            """
            SELECT total_value, cash, position_value, profit, recorded_at
            FROM profit_history WHERE agent_id = ?
            ORDER BY recorded_at DESC LIMIT 30
            """,
            (agent_id,),
        )
        profit_history = [dict(r) for r in cursor.fetchall()]

        # Check if instruction file exists
        instruction_file = _AGENTS_DIR / f"AGENT_INSTRUCTIONS_{config['name']}.md"
        instruction_file_exists = instruction_file.exists()

        conn.close()

        return {
            **config,
            **stats,
            'recent_trades': recent_trades,
            'positions': positions,
            'profit_history': profit_history,
            'instruction_file_exists': instruction_file_exists,
            'instruction_file_path': str(instruction_file) if instruction_file_exists else None,
        }

    @app.put('/api/agents/manage/{agent_id}')
    async def update_agent(
        agent_id: int,
        data: AgentConfigUpdate,
        authorization: str = Header(None),
    ):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if config exists
        cursor.execute('SELECT id FROM agent_configs WHERE agent_id = ?', (agent_id,))
        config_row = cursor.fetchone()

        # Build update fields
        fields = []
        values: list = []
        field_map = {
            'tagline': data.tagline,
            'bio': data.bio,
            'risk_tolerance': data.risk_tolerance,
            'position_sizing': data.position_sizing,
            'hold_period': data.hold_period,
            'max_positions': data.max_positions,
            'confidence_threshold': data.confidence_threshold,
            'fomo_resistance': data.fomo_resistance,
            'loss_aversion': data.loss_aversion,
            'conviction_multiplier': data.conviction_multiplier,
            'voice': data.voice,
            'emoji_frequency': data.emoji_frequency,
            'publishes_reasoning': 1 if data.publishes_reasoning else None,
            'trash_talk': 1 if data.trash_talk else None,
            'strategy_type': data.strategy_type,
            'status': data.status,
            'auto_start': 1 if data.auto_start else None,
            'poll_interval': data.poll_interval,
            'api_base': data.api_base,
        }

        # Handle JSON fields
        if data.watchlist is not None:
            fields.append('watchlist_json = ?')
            values.append(json.dumps(data.watchlist))
        if data.quirks is not None:
            fields.append('quirks_json = ?')
            values.append(json.dumps(data.quirks))

        for field_name, value in field_map.items():
            if value is not None:
                # Handle bool-to-int for publishes_reasoning and trash_talk
                if field_name in ('publishes_reasoning', 'trash_talk', 'auto_start'):
                    if data.publishes_reasoning is not None and field_name == 'publishes_reasoning':
                        fields.append(f'{field_name} = ?')
                        values.append(1 if data.publishes_reasoning else 0)
                    elif data.trash_talk is not None and field_name == 'trash_talk':
                        fields.append(f'{field_name} = ?')
                        values.append(1 if data.trash_talk else 0)
                    elif data.auto_start is not None and field_name == 'auto_start':
                        fields.append(f'{field_name} = ?')
                        values.append(1 if data.auto_start else 0)
                else:
                    fields.append(f'{field_name} = ?')
                    values.append(value)

        if not fields:
            conn.close()
            raise HTTPException(status_code=400, detail='No fields to update')

        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.append(agent_id)

        try:
            if config_row:
                cursor.execute(
                    f"UPDATE agent_configs SET {', '.join(fields)} WHERE agent_id = ?",
                    values,
                )
            else:
                # Create config if it doesn't exist
                cursor.execute(
                    """INSERT INTO agent_configs (agent_id, created_at, updated_at) VALUES (?, ?, ?)""",
                    (agent_id, _now_iso(), _now_iso()),
                )
                # Now update with the fields
                cursor.execute(
                    f"UPDATE agent_configs SET {', '.join(fields)} WHERE agent_id = ?",
                    values,
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to update: {exc}')
        conn.close()

        # Optionally regenerate instruction file
        file_path = None
        if data.generate_files:
            updated = _get_agent_by_id(agent_id)
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM agent_configs WHERE agent_id = ?', (agent_id,))
            cfg_row = cursor.fetchone()
            conn.close()
            if cfg_row:
                cfg = _row_to_config(cfg_row)
                cfg['name'] = updated['name']
                cfg['email'] = updated.get('email', '')
                cfg['password'] = f"{updated['name'].lower()}_pass_2026"
                try:
                    md_content = _generate_instruction_md(cfg)
                    file_path = _write_instruction_file(updated['name'], md_content)
                except Exception as exc:
                    file_path = f'Error: {exc}'

        return {
            'success': True,
            'agent_id': agent_id,
            'instruction_file': file_path,
            'message': 'Agent updated successfully',
        }

    @app.delete('/api/agents/manage/{agent_id}')
    async def delete_agent(
        agent_id: int,
        delete_files: bool = False,
        authorization: str = Header(None),
    ):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Delete related data
            cursor.execute('DELETE FROM agent_configs WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agent_stats WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agent_trade_log WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agent_messages WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agent_tasks WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM signal_replies WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM signal_quality_scores WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM signals WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM positions WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM profit_history WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM subscriptions WHERE leader_id = ? OR follower_id = ?', (agent_id, agent_id))
            cursor.execute('DELETE FROM agent_reward_ledger WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agent_leaderboard_exclusions WHERE agent_id = ?', (agent_id,))
            cursor.execute('DELETE FROM agents WHERE id = ?', (agent_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to delete agent: {exc}')
        conn.close()

        deleted_files = []
        if delete_files:
            instruction_file = _AGENTS_DIR / f"AGENT_INSTRUCTIONS_{agent['name']}.md"
            if instruction_file.exists():
                instruction_file.unlink()
                deleted_files.append(str(instruction_file))

        return {
            'success': True,
            'agent_id': agent_id,
            'name': agent['name'],
            'deleted_files': deleted_files,
            'message': f'Agent {agent["name"]} deleted successfully',
        }

    @app.post('/api/agents/manage/{agent_id}/activate')
    async def activate_agent(agent_id: int, authorization: str = Header(None)):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE agent_configs SET status = ?, updated_at = ? WHERE agent_id = ?', ('active', _now_iso(), agent_id))
        if cursor.rowcount == 0:
            cursor.execute(
                'INSERT INTO agent_configs (agent_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (agent_id, 'active', _now_iso(), _now_iso()),
            )
        conn.commit()
        conn.close()
        return {'success': True, 'agent_id': agent_id, 'status': 'active'}

    @app.post('/api/agents/manage/{agent_id}/deactivate')
    async def deactivate_agent(agent_id: int, authorization: str = Header(None)):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE agent_configs SET status = ?, updated_at = ? WHERE agent_id = ?', ('inactive', _now_iso(), agent_id))
        if cursor.rowcount == 0:
            cursor.execute(
                'INSERT INTO agent_configs (agent_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (agent_id, 'inactive', _now_iso(), _now_iso()),
            )
        conn.commit()
        conn.close()
        return {'success': True, 'agent_id': agent_id, 'status': 'inactive'}

    @app.post('/api/agents/manage/{agent_id}/regenerate-files')
    async def regenerate_files(agent_id: int, authorization: str = Header(None)):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_configs WHERE agent_id = ?', (agent_id,))
        cfg_row = cursor.fetchone()
        conn.close()

        if not cfg_row:
            raise HTTPException(status_code=404, detail='Agent config not found')

        cfg = _row_to_config(cfg_row)
        cfg['name'] = agent['name']
        cfg['email'] = agent.get('email', '')
        cfg['password'] = f"{agent['name'].lower()}_pass_2026"

        md_content = _generate_instruction_md(cfg)
        file_path = _write_instruction_file(agent['name'], md_content)

        return {
            'success': True,
            'agent_id': agent_id,
            'instruction_file': file_path,
            'message': f'Instruction file regenerated for {agent["name"]}',
        }

    @app.get('/api/agents/manage/{agent_id}/stats')
    async def get_agent_stats(agent_id: int, authorization: str = Header(None)):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get stored stats
        cursor.execute('SELECT * FROM agent_stats WHERE agent_id = ?', (agent_id,))
        stored = cursor.fetchone()
        stored_stats = dict(stored) if stored else {}

        # Get live computed stats
        live_stats = _compute_agent_stats(agent_id, cursor)

        # Get profit history for charting
        cursor.execute(
            """
            SELECT total_value, cash, position_value, profit, recorded_at
            FROM profit_history WHERE agent_id = ?
            ORDER BY recorded_at ASC LIMIT 500
            """,
            (agent_id,),
        )
        profit_history = [dict(r) for r in cursor.fetchall()]

        conn.close()

        return {
            'agent_id': agent_id,
            'stored': stored_stats,
            'live': live_stats,
            'profit_history': profit_history,
        }

    @app.get('/api/agents/manage/{agent_id}/trades')
    async def get_agent_trades(
        agent_id: int,
        limit: int = 50,
        offset: int = 0,
        authorization: str = Header(None),
    ):

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT signal_id, market, symbol, token_id, outcome, side,
                   entry_price, quantity, content, executed_at, created_at
            FROM signals
            WHERE agent_id = ? AND message_type = 'operation'
            ORDER BY executed_at DESC
            LIMIT ? OFFSET ?
            """,
            (agent_id, limit, offset),
        )
        trades = [dict(r) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE agent_id = ? AND message_type = 'operation'",
            (agent_id,),
        )
        total = cursor.fetchone()['cnt']

        conn.close()
        return {'trades': trades, 'total': total}

    @app.post('/api/agents/manage/{agent_id}/reset-token')
    async def reset_agent_token(agent_id: int, authorization: str = Header(None)):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        new_token = secrets.token_urlsafe(32)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE agents SET token = ?, token_expires_at = NULL WHERE id = ?', (new_token, agent_id))
        conn.commit()
        conn.close()

        return {
            'success': True,
            'agent_id': agent_id,
            'name': agent['name'],
            'token': new_token,
        }

    @app.post('/api/agents/manage/{agent_id}/reset-cash')
    async def reset_agent_cash(
        agent_id: int,
        amount: float = 100000.0,
        authorization: str = Header(None),
    ):

        agent = _get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail='Agent not found')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE agents SET cash = ? WHERE id = ?', (amount, agent_id))
        conn.commit()
        conn.close()

        return {
            'success': True,
            'agent_id': agent_id,
            'name': agent['name'],
            'cash': amount,
        }
