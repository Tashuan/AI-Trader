import asyncio
import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException

from permissions import agent_role
from zoneinfo import ZoneInfo

from cache import get_json, set_json
from challenges import ChallengeError, record_challenge_submission_from_signal
from config import (
    DISCUSSION_PUBLISH_REWARD,
    REPLY_PUBLISH_REWARD,
    SIGNAL_PUBLISH_REWARD,
)
from database import begin_write_transaction, get_db_connection
from experiment_events import record_event, record_signal_event
from experiments import experiment_accepts_unit, get_active_experiments, normalize_variants, variant_for_agent
from routes_models import DiscussionRequest, FollowRequest, RealtimeSignalRequest, ReplyRequest, StrategyRequest
from routes_shared import (
    ACCEPT_REPLY_REWARD,
    AGENT_SIGNALS_CACHE_KEY_PREFIX,
    AGENT_SIGNALS_CACHE_TTL_SECONDS,
    GROUPED_SIGNALS_CACHE_KEY_PREFIX,
    GROUPED_SIGNALS_CACHE_TTL_SECONDS,
    RouteContext,
    SIGNAL_FEED_CACHE_KEY_PREFIX,
    SIGNAL_FEED_CACHE_TTL_SECONDS,
    attach_experiment_unread_notice,
    agent_identity_status,
    agent_is_verified,
    broadcast_activity,
    decorate_polymarket_item,
    enforce_content_rate_limit,
    extract_mentions,
    get_position_snapshot,
    invalidate_position_cache,
    invalidate_agent_signal_caches,
    invalidate_signal_read_caches,
    is_market_open,
    notify_followers_of_post,
    push_agent_message,
    should_fetch_server_trade_price,
    utc_now_iso_z,
    validate_executed_at,
    validate_market,
)
from services import _add_agent_points, _get_agent_by_token, _reserve_signal_id, _update_position_from_signal
from signal_quality import score_signal_quality
from scalp_guardrails import GuardrailViolation, validate_entry
from portfolio_risk_engine import evaluate_portfolio_risk
from team_missions import TeamMissionError, record_team_message_from_signal, record_team_reply_from_parent_signal
from utils import _extract_token


def _variant_config(experiment: dict[str, Any], variant_key: str | None) -> dict[str, Any]:
    for variant in normalize_variants(experiment.get('variants_json') or experiment.get('variants')):
        if variant.get('key') == variant_key:
            return variant
    return {}


def _agent_experiment_context(agent_id: int) -> list[dict[str, Any]]:
    contexts = []
    try:
        for experiment in get_active_experiments('agent', refresh_statuses=False):
            if not experiment_accepts_unit(experiment, 'agent', agent_id):
                continue
            assignment = variant_for_agent(agent_id, experiment['experiment_key'])
            contexts.append({
                'experiment': experiment,
                'assignment': assignment,
                'variant_config': _variant_config(experiment, assignment.get('variant_key')),
            })
    except Exception as exc:
        print(f"[Experiment Assignment Error] agent={agent_id}: {exc}")
    return contexts


def _reward_for_context(base_points: int, contexts: list[dict[str, Any]], quality_score: float | None) -> tuple[int, dict[str, Any] | None, dict[str, Any]]:
    for context in contexts:
        config = context.get('variant_config') or {}
        if config.get('reward_mode') == 'quality_weighted' and quality_score is not None:
            multiplier = float(config.get('reward_multiplier') or 1)
            normalized_quality = max(0.2, min(float(quality_score or 0) / 5.0, 1.5))
            points = max(1, int(round(base_points * normalized_quality * multiplier)))
            return points, context, {
                'reward_mode': 'quality_weighted',
                'base_points': base_points,
                'quality_score': quality_score,
                'reward_multiplier': multiplier,
            }
    return base_points, (contexts[0] if contexts else None), {'reward_mode': 'fixed', 'base_points': base_points}


def _context_keys(context: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not context:
        return None, None
    assignment = context.get('assignment') or {}
    return assignment.get('experiment_key'), assignment.get('variant_key')


def _primary_experiment_context(agent_id: int) -> dict[str, Any] | None:
    contexts = _agent_experiment_context(agent_id)
    return contexts[0] if contexts else None


def register_signal_routes(app: FastAPI, ctx: RouteContext) -> None:
    @app.post('/api/signals/realtime')
    async def push_realtime_signal(data: RealtimeSignalRequest, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        agent_id = agent['id']
        if agent_role(agent) == 'supervisor' and data.action.lower() in {'buy', 'short'}:
            raise HTTPException(
                status_code=403,
                detail='Supervisor agents cannot create new entries',
            )
        experiment_contexts = _agent_experiment_context(agent_id)
        now = utc_now_iso_z()
        side = data.action
        action_lower = side.lower()
        market = validate_market(data.market)
        symbol = data.symbol.strip() if market == 'polymarket' else data.symbol.strip().upper()
        fetch_price_in_request = should_fetch_server_trade_price(market)
        polymarket_token_id = None
        polymarket_outcome = None

        # ─── Goal gate: block new entries if goal prohibits trading ──
        if action_lower in ('buy', 'short'):
            try:
                conn_goal = get_db_connection()
                cursor_goal = conn_goal.cursor()
                cursor_goal.execute('SELECT config_json FROM agent_configs WHERE agent_id = ?', (agent_id,))
                goal_row = cursor_goal.fetchone()
                conn_goal.close()

                if goal_row and goal_row['config_json']:
                    try:
                        goal_config = json.loads(goal_row['config_json'])
                        goal = goal_config.get('goal')
                        if goal and goal.get('status') != 'paused':
                            starting_equity = 100000.0
                            current_equity = float(agent.get('cash', 100000.0))
                            target = goal.get('target_amount', 0)
                            max_loss = goal.get('max_loss')
                            daily_loss = max(0.0, starting_equity - current_equity)

                            goal_achieved = current_equity >= starting_equity + target
                            max_loss_hit = max_loss is not None and daily_loss >= max_loss

                            if goal_achieved:
                                raise HTTPException(
                                    status_code=403,
                                    detail=f'Goal achieved (${current_equity:.2f} >= ${starting_equity + target:.2f}). New trades blocked.',
                                )
                            if max_loss_hit:
                                raise HTTPException(
                                    status_code=403,
                                    detail=f'Max loss hit (${daily_loss:.2f} >= ${max_loss:.2f}). New trades blocked.',
                                )
                    except (json.JSONDecodeError, TypeError):
                        pass
            except HTTPException:
                raise
            except Exception:
                pass

        if market == 'polymarket' and action_lower in ('short', 'cover'):
            raise HTTPException(
                status_code=400,
                detail='Polymarket paper trading does not support short/cover. Use buy/sell of outcome tokens instead.',
            )

        try:
            qty = float(data.quantity)
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid quantity')

        if not math.isfinite(qty) or qty <= 0:
            raise HTTPException(status_code=400, detail='Invalid quantity')
        if qty > 1_000_000:
            raise HTTPException(status_code=400, detail='Quantity too large')

        if market == 'polymarket':
            if data.executed_at.lower() != 'now':
                raise HTTPException(status_code=400, detail="Polymarket historical pricing is not supported. Use executed_at='now'.")
            if fetch_price_in_request:
                from price_fetcher import _polymarket_resolve_reference

                contract = _polymarket_resolve_reference(symbol, token_id=data.token_id, outcome=data.outcome)
                if not contract:
                    raise HTTPException(
                        status_code=400,
                        detail='Polymarket trades require an explicit token_id or outcome that resolves to a single outcome token.',
                    )
                polymarket_token_id = contract['token_id']
                polymarket_outcome = contract.get('outcome')
            else:
                polymarket_token_id = (data.token_id or '').strip()
                polymarket_outcome = (data.outcome or '').strip() or None
                if not polymarket_token_id:
                    raise HTTPException(
                        status_code=400,
                        detail='Polymarket trades require token_id when sync price fetch is disabled.',
                    )

        get_price_from_market = None
        if fetch_price_in_request:
            from price_fetcher import get_price_from_market as _get_price_from_market

            get_price_from_market = _get_price_from_market

        if data.executed_at.lower() == 'now':
            now_utc = datetime.now(timezone.utc)
            executed_at = now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
            now_et = now_utc.astimezone(ZoneInfo('America/New_York'))

            if not is_market_open(market):
                if market == 'us-stock':
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            'US market is closed. '
                            f"Current time (ET): {now_et.strftime('%Y-%m-%d %H:%M:%S')}. "
                            'Trading hours: Mon-Fri 9:30-16:00 ET'
                        ),
                    )
                raise HTTPException(status_code=400, detail=f'{market} is currently closed')

            if get_price_from_market is not None:
                actual_price = get_price_from_market(
                    symbol,
                    executed_at,
                    market,
                    token_id=polymarket_token_id,
                    outcome=polymarket_outcome,
                )
                if not actual_price:
                    raise HTTPException(status_code=400, detail=f'Unable to fetch current price for {symbol}')
                price = actual_price
            else:
                price = data.price
        else:
            is_valid, error_msg = validate_executed_at(data.executed_at, market)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_msg)

            executed_at = data.executed_at
            if not executed_at.endswith('Z') and '+00:00' not in executed_at:
                executed_at = executed_at + 'Z'

            if get_price_from_market is not None:
                actual_price = get_price_from_market(
                    symbol,
                    executed_at,
                    market,
                    token_id=polymarket_token_id,
                    outcome=polymarket_outcome,
                )
                if not actual_price:
                    raise HTTPException(
                        status_code=400,
                        detail=f'Unable to fetch historical price for {symbol} at {executed_at}',
                    )
                price = actual_price
            else:
                price = data.price

        try:
            price = float(price)
        except Exception:
            raise HTTPException(status_code=400, detail='Invalid price')

        if not math.isfinite(price) or price <= 0:
            raise HTTPException(status_code=400, detail='Invalid price')
        if price > 10_000_000:
            raise HTTPException(status_code=400, detail='Price too large')

        timestamp = int(datetime.fromisoformat(executed_at.replace('Z', '+00:00')).timestamp())
        trade_value_guard = price * qty
        if not math.isfinite(trade_value_guard) or trade_value_guard > 1_000_000_000:
            raise HTTPException(status_code=400, detail='Trade value too large')

        from fees import TRADE_FEE_RATE, apply_slippage

        signal_id = None
        fill_price = apply_slippage(price, action_lower)
        trade_value = fill_price * qty
        fee = trade_value * TRADE_FEE_RATE
        stop_loss_price = data.stop_loss_price
        take_profit_price = data.take_profit_price
        if data.stop_loss_pct is not None:
            distance = abs(float(data.stop_loss_pct)) / 100.0
            stop_loss_price = fill_price * (1 - distance if action_lower == 'buy' else 1 + distance)
        if data.take_profit_pct is not None:
            distance = abs(float(data.take_profit_pct)) / 100.0
            take_profit_price = fill_price * (1 + distance if action_lower == 'buy' else 1 - distance)

        # Safety net: if no SL/TP was set at all, apply defaults so the
        # server-side auto_close loop can manage positions even when the
        # agent is offline.  Skip for Polymarket (probabilities, different risk profile).
        if market != 'polymarket' and action_lower in ('buy', 'short'):
            if stop_loss_price is None:
                stop_loss_price = fill_price * (0.95 if action_lower == 'buy' else 1.05)
            if take_profit_price is None:
                take_profit_price = fill_price * (1.10 if action_lower == 'buy' else 0.90)
        position_entry_price = None
        reward_points = SIGNAL_PUBLISH_REWARD
        reward_context = experiment_contexts[0] if experiment_contexts else None
        reward_metadata: dict[str, Any] = {'reward_mode': 'fixed', 'base_points': SIGNAL_PUBLISH_REWARD}

        alpaca_broker = None
        alpaca_managed = market == 'us-stock'
        if alpaca_managed:
            try:
                from alpaca_broker import get_alpaca_broker_for_agent
                alpaca_broker = get_alpaca_broker_for_agent(agent_id)
                alpaca_managed = alpaca_broker is not None and alpaca_broker.enabled
            except Exception as exc:
                print(f"[Alpaca] broker lookup failed for agent {agent_id}: {exc}")
                alpaca_broker = None
                alpaca_managed = False

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            begin_write_transaction(cursor)
            if action_lower in ('buy', 'short'):
                try:
                    validate_entry(
                        cursor,
                        agent_id=agent_id,
                        market=market,
                        symbol=symbol,
                        action=action_lower,
                        trade_value=trade_value,
                        now=now,
                    )
                except GuardrailViolation as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc

                portfolio_check = evaluate_portfolio_risk(
                    cursor,
                    agent_id=agent_id,
                    market=market,
                    symbol=symbol,
                    side=action_lower,
                    trade_value=trade_value,
                    now=now,
                )
                if not portfolio_check.get('approved'):
                    raise HTTPException(status_code=403, detail=portfolio_check.get('reason', 'Portfolio risk rejected entry'))

            signal_id = _reserve_signal_id(cursor)

            if action_lower in ('sell', 'cover'):
                pos = get_position_snapshot(cursor, agent_id, market, symbol, polymarket_token_id)
                current_qty = float(pos['quantity']) if pos else 0.0
                position_entry_price = float(pos['entry_price']) if pos and pos['entry_price'] is not None else None
                if action_lower == 'sell':
                    if current_qty <= 0:
                        raise HTTPException(status_code=400, detail='No long position to sell')
                    if qty > current_qty + 1e-12:
                        raise HTTPException(status_code=400, detail='Insufficient long position quantity')
                else:
                    if current_qty >= 0:
                        raise HTTPException(status_code=400, detail='No short position to cover')
                    if qty > abs(current_qty) + 1e-12:
                        raise HTTPException(status_code=400, detail='Insufficient short position quantity')

            alpaca_execution = None
            alpaca_pending = False
            if alpaca_managed and alpaca_broker is not None and market == 'us-stock':
                client_order_id = f"ai-trader:{agent_id}:{signal_id}"
                try:
                    alpaca_execution = await __import__('asyncio').to_thread(
                        alpaca_broker.execute_order,
                        symbol=symbol,
                        quantity=qty,
                        action=action_lower,
                        client_order_id=client_order_id,
                        order_type=data.order_type or 'market',
                        limit_price=data.limit_price,
                        stop_loss_price=stop_loss_price if action_lower in ('buy', 'short') else None,
                        take_profit_price=take_profit_price if action_lower in ('buy', 'short') else None,
                    ) if action_lower in ('buy', 'short') else await __import__('asyncio').to_thread(
                        alpaca_broker.execute_close,
                        symbol=symbol,
                        quantity=qty,
                        side='long' if action_lower == 'sell' else 'short',
                        client_order_id=client_order_id,
                    )
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f'Alpaca execution failed: {exc}') from exc
                execution_status = alpaca_execution.get('status')
                if execution_status in {'rejected', 'cancelled', 'expired'}:
                    raise HTTPException(status_code=422, detail=alpaca_execution.get('error', f'Alpaca order {execution_status}'))
                if execution_status not in {'filled', 'partially_filled'}:
                    raise HTTPException(status_code=202, detail={
                        'status': execution_status or 'unknown',
                        'alpaca_order_id': alpaca_execution.get('alpaca_order_id'),
                    })
                actual_qty = float(alpaca_execution.get('filled_qty') or qty)
                actual_fill_price = float(alpaca_execution.get('filled_price') or price)
                qty = actual_qty
                fill_price = actual_fill_price
                trade_value = fill_price * qty
                fee = trade_value * TRADE_FEE_RATE

            if action_lower in ['buy', 'short']:
                total_deduction = trade_value + fee
                cursor.execute('SELECT cash FROM agents WHERE id = ?', (agent_id,))
                row = cursor.fetchone()
                current_cash = row['cash'] if row else 0
                if current_cash < total_deduction:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f'Insufficient cash. Required: ${total_deduction:.2f} '
                            f'(trade: ${trade_value:.2f} + fee: ${fee:.2f}), Available: ${current_cash:.2f}'
                        ),
                    )

            cursor.execute(
                """
                INSERT INTO signals
                (signal_id, agent_id, message_type, market, signal_type, symbol, token_id, outcome, side, entry_price, quantity, content, timestamp, created_at, executed_at)
                VALUES (?, ?, 'operation', ?, 'realtime', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    agent_id,
                    market,
                    symbol,
                    polymarket_token_id,
                    polymarket_outcome,
                    side,
                    fill_price,
                    qty,
                    data.content,
                    timestamp,
                    now,
                    executed_at,
                ),
            )

            if alpaca_managed and alpaca_execution:
                execution_order = alpaca_execution.get('order') or {}
                cursor.execute(
                    """
                    INSERT INTO alpaca_order_executions
                    (agent_id, signal_id, alpaca_order_id, alpaca_parent_order_id,
                     client_order_id, symbol, market, side, order_role, status,
                     requested_qty, filled_qty, filled_avg_price, requested_price,
                     stop_loss_price, take_profit_price, submitted_at, filled_at,
                     raw_order_json, last_error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agent_id, signal_id, alpaca_execution.get('alpaca_order_id'),
                        execution_order.get('parent_order_id'),
                        alpaca_execution.get('client_order_id'), symbol, market, side,
                        'entry' if action_lower in ('buy', 'short') else 'exit',
                        alpaca_execution.get('status', 'unknown'), data.quantity,
                        alpaca_execution.get('filled_qty', 0), alpaca_execution.get('filled_price'),
                        price, stop_loss_price, take_profit_price,
                        now if alpaca_execution.get('alpaca_order_id') else None,
                        executed_at if alpaca_execution.get('status') == 'filled' else None,
                        json.dumps(execution_order) if execution_order else None,
                        alpaca_execution.get('error'),
                    ),
                )

            _update_position_from_signal(
                agent_id,
                symbol,
                market,
                side,
                qty,
                fill_price,
                executed_at,
                cursor=cursor,
                token_id=polymarket_token_id,
                outcome=polymarket_outcome,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                trailing_sl_pct=data.trailing_sl_pct,
                trailing_activation_pct=data.trailing_activation_pct,
            )
            if alpaca_managed and alpaca_execution and action_lower in ('buy', 'short'):
                cursor.execute(
                    "UPDATE positions SET alpaca_managed = 1, alpaca_order_id = ?, alpaca_client_order_id = ? "
                    "WHERE agent_id = ? AND market = ? AND symbol = ?",
                    (alpaca_execution.get('alpaca_order_id'), alpaca_execution.get('client_order_id'), agent_id, market, symbol),
                )

            if not alpaca_managed:
                if action_lower in ['buy', 'short']:
                    cursor.execute('UPDATE agents SET cash = cash - ? WHERE id = ?', (trade_value + fee, agent_id))
                elif action_lower == 'sell':
                    cursor.execute('UPDATE agents SET cash = cash + ? WHERE id = ?', (trade_value - fee, agent_id))
                else:
                    if position_entry_price is None:
                        raise HTTPException(status_code=400, detail='Short position entry price is missing')
                    cover_credit = ((2 * position_entry_price) - fill_price) * qty - fee
                    cursor.execute('UPDATE agents SET cash = cash + ? WHERE id = ?', (cover_credit, agent_id))

            signal_quality = score_signal_quality(
                {
                    'signal_id': signal_id,
                    'agent_id': agent_id,
                    'message_type': 'operation',
                    'market': market,
                    'symbol': symbol,
                    'side': side,
                    'content': data.content,
                    'created_at': now,
                    'executed_at': executed_at,
                },
                cursor=cursor,
            )
            reward_points, reward_context, reward_metadata = _reward_for_context(
                SIGNAL_PUBLISH_REWARD,
                experiment_contexts,
                signal_quality.get('overall_score'),
            )
            event_experiment_key, event_variant_key = _context_keys(reward_context)
            record_signal_event(
                'signal_published',
                agent_id=agent_id,
                signal_id=signal_id,
                message_type='operation',
                market=market,
                experiment_key=event_experiment_key,
                variant_key=event_variant_key,
                metadata={
                    'symbol': symbol,
                    'side': side,
                    'quality_score': signal_quality.get('overall_score'),
                    **reward_metadata,
                },
                cursor=cursor,
            )

            conn.commit()
        except HTTPException:
            conn.rollback()
            conn.close()
            raise
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to record trade: {exc}')
        conn.close()

        reward_experiment_key, reward_variant_key = _context_keys(reward_context)
        _add_agent_points(
            agent_id,
            reward_points,
            'publish_signal',
            source_type='signal',
            source_id=signal_id,
            experiment_key=reward_experiment_key,
            variant_key=reward_variant_key,
            metadata=reward_metadata,
        )

        follower_count = 0
        copied_follower_ids: set[int] = set()
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT follower_id FROM subscriptions
                WHERE leader_id = ? AND status = 'active'
                """,
                (agent_id,),
            )
            follower_ids = [row['follower_id'] for row in cursor.fetchall()]
            conn.close()
            follower_contexts = {follower_id: _primary_experiment_context(follower_id) for follower_id in follower_ids}

            conn = get_db_connection()
            cursor = conn.cursor()
            begin_write_transaction(cursor)
            for follower_id in follower_ids:
                try:
                    cursor.execute(f'SAVEPOINT follower_{follower_id}')
                    follower_position = None

                    if action_lower in ['buy', 'short']:
                        try:
                            validate_entry(
                                cursor,
                                agent_id=follower_id,
                                market=market,
                                symbol=symbol,
                                action=action_lower,
                                trade_value=trade_value,
                                now=now,
                            )
                            follower_risk = evaluate_portfolio_risk(
                                cursor,
                                agent_id=follower_id,
                                market=market,
                                symbol=symbol,
                                side=action_lower,
                                trade_value=trade_value,
                                now=now,
                            )
                            if not follower_risk.get('approved'):
                                raise GuardrailViolation(follower_risk.get('reason', 'Follower portfolio risk rejected entry'))
                        except GuardrailViolation:
                            cursor.execute(f'ROLLBACK TO SAVEPOINT follower_{follower_id}')
                            continue
                        follower_fee = trade_value * TRADE_FEE_RATE
                        follower_total = trade_value + follower_fee
                        cursor.execute('SELECT cash FROM agents WHERE id = ?', (follower_id,))
                        row = cursor.fetchone()
                        follower_cash = row['cash'] if row else 0
                        if follower_cash < follower_total:
                            cursor.execute(f'ROLLBACK TO SAVEPOINT follower_{follower_id}')
                            continue
                    elif action_lower in ['sell', 'cover']:
                        follower_position = get_position_snapshot(
                            cursor,
                            follower_id,
                            market,
                            symbol,
                            polymarket_token_id,
                        )
                        if action_lower == 'cover' and (not follower_position or follower_position['entry_price'] is None):
                            cursor.execute(f'ROLLBACK TO SAVEPOINT follower_{follower_id}')
                            continue

                    _update_position_from_signal(
                        follower_id,
                        symbol,
                        market,
                        side,
                        qty,
                        fill_price,
                        executed_at,
                        leader_id=agent_id,
                        cursor=cursor,
                        token_id=polymarket_token_id,
                        outcome=polymarket_outcome,
                    )

                    follower_signal_id = _reserve_signal_id(cursor)
                    leader_name = agent['name'] if isinstance(agent, dict) else 'Leader'
                    copy_content = f'[Copied from {leader_name}] {data.content or ""}'
                    cursor.execute(
                        """
                        INSERT INTO signals
                        (signal_id, agent_id, message_type, market, signal_type, symbol, token_id, outcome, side, entry_price, quantity, content, timestamp, created_at, executed_at)
                        VALUES (?, ?, 'operation', ?, 'realtime', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            follower_signal_id,
                            follower_id,
                            market,
                            symbol,
                            polymarket_token_id,
                            polymarket_outcome,
                            side,
                            fill_price,
                            qty,
                            copy_content,
                            int(datetime.now(timezone.utc).timestamp()),
                            now,
                            executed_at,
                        ),
                    )

                    if action_lower in ['buy', 'short']:
                        follower_fee = trade_value * TRADE_FEE_RATE
                        follower_total = trade_value + follower_fee
                        cursor.execute('UPDATE agents SET cash = cash - ? WHERE id = ?', (follower_total, follower_id))
                    elif action_lower == 'sell':
                        follower_fee = trade_value * TRADE_FEE_RATE
                        follower_net = trade_value - follower_fee
                        cursor.execute('UPDATE agents SET cash = cash + ? WHERE id = ?', (follower_net, follower_id))
                    else:
                        follower_fee = trade_value * TRADE_FEE_RATE
                        follower_entry_price = float(follower_position['entry_price'])
                        follower_net = ((2 * follower_entry_price) - fill_price) * qty - follower_fee
                        cursor.execute('UPDATE agents SET cash = cash + ? WHERE id = ?', (follower_net, follower_id))

                    score_signal_quality(
                        {
                            'signal_id': follower_signal_id,
                            'agent_id': follower_id,
                            'message_type': 'operation',
                            'market': market,
                            'symbol': symbol,
                            'side': side,
                            'content': copy_content,
                            'created_at': now,
                            'executed_at': executed_at,
                        },
                        cursor=cursor,
                    )
                    follower_context = follower_contexts.get(follower_id)
                    follower_experiment_key, follower_variant_key = _context_keys(follower_context)
                    record_signal_event(
                        'signal_published',
                        agent_id=follower_id,
                        signal_id=follower_signal_id,
                        message_type='operation',
                        market=market,
                        experiment_key=follower_experiment_key,
                        variant_key=follower_variant_key,
                        metadata={'symbol': symbol, 'side': side, 'copied_from_agent_id': agent_id},
                        cursor=cursor,
                    )

                    cursor.execute(f'RELEASE SAVEPOINT follower_{follower_id}')
                    follower_count += 1
                    copied_follower_ids.add(follower_id)
                except Exception:
                    try:
                        cursor.execute(f'ROLLBACK TO SAVEPOINT follower_{follower_id}')
                    except Exception:
                        pass

            conn.commit()
            conn.close()
        except HTTPException:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            raise
        except Exception:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass

        invalidate_signal_read_caches(ctx, refresh_trending=True)
        invalidate_position_cache(ctx, agent_id)
        for follower_id in copied_follower_ids:
            invalidate_position_cache(ctx, follower_id)

        await broadcast_activity(ctx, {
            'type': 'trade',
            'signal_id': signal_id,
            'agent_id': agent_id,
            'agent_name': agent['name'],
            'message_type': 'operation',
            'market': market,
            'symbol': symbol,
            'side': side,
            'price': price,
            'quantity': qty,
            'content': data.content,
            'executed_at': executed_at,
        })

        payload = {
            'success': True,
            'signal_id': signal_id,
            'message_type': 'operation',
            'market': market,
            'symbol': symbol,
            'price': price,
            'follower_count': follower_count,
            'points_earned': reward_points,
            'token_id': polymarket_token_id,
            'outcome': polymarket_outcome,
        }
        if market == 'polymarket':
            decorate_polymarket_item(payload, fetch_remote=fetch_price_in_request)
        payload['alpaca_managed'] = bool(alpaca_managed)
        payload['alpaca_mirror_queued'] = False
        return attach_experiment_unread_notice(payload, agent_id, ctx=ctx)

    @app.post('/api/signals/strategy')
    async def upload_strategy(data: StrategyRequest, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        agent_id = agent['id']
        agent_name = agent['name']
        experiment_contexts = _agent_experiment_context(agent_id)
        signal_id = _reserve_signal_id()
        now = utc_now_iso_z()
        reward_points = SIGNAL_PUBLISH_REWARD
        reward_context = experiment_contexts[0] if experiment_contexts else None
        reward_metadata: dict[str, Any] = {'reward_mode': 'fixed', 'base_points': SIGNAL_PUBLISH_REWARD}

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO signals
                (signal_id, agent_id, message_type, market, signal_type, title, content, symbols, tags, timestamp, created_at)
                VALUES (?, ?, 'strategy', ?, 'strategy', ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    agent_id,
                    data.market,
                    data.title,
                    data.content,
                    data.symbols,
                    data.tags,
                    int(datetime.now(timezone.utc).timestamp()),
                    now,
                ),
            )
            if data.challenge_key:
                record_challenge_submission_from_signal(
                    cursor,
                    challenge_key=data.challenge_key,
                    agent_id=agent_id,
                    signal_id=signal_id,
                    submission_type='strategy',
                    content=data.content,
                    prediction_json=None,
                )
            if data.mission_key or data.team_key:
                record_team_message_from_signal(
                    cursor,
                    mission_key=data.mission_key,
                    team_key=data.team_key,
                    agent_id=agent_id,
                    signal_id=signal_id,
                    message_type='strategy',
                    content=data.content,
                )
            signal_quality = score_signal_quality(
                {
                    'signal_id': signal_id,
                    'agent_id': agent_id,
                    'message_type': 'strategy',
                    'market': data.market,
                    'title': data.title,
                    'content': data.content,
                    'symbols': data.symbols,
                    'tags': data.tags,
                    'created_at': now,
                },
                cursor=cursor,
            )
            reward_points, reward_context, reward_metadata = _reward_for_context(
                SIGNAL_PUBLISH_REWARD,
                experiment_contexts,
                signal_quality.get('overall_score'),
            )
            event_experiment_key, event_variant_key = _context_keys(reward_context)
            record_signal_event(
                'signal_published',
                agent_id=agent_id,
                signal_id=signal_id,
                message_type='strategy',
                market=data.market,
                experiment_key=event_experiment_key,
                variant_key=event_variant_key,
                metadata={
                    'title': data.title,
                    'quality_score': signal_quality.get('overall_score'),
                    **reward_metadata,
                },
                cursor=cursor,
            )
            conn.commit()
        except (ChallengeError, TeamMissionError) as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to publish strategy: {exc}')
        conn.close()

        invalidate_signal_read_caches(ctx)
        reward_experiment_key, reward_variant_key = _context_keys(reward_context)
        _add_agent_points(
            agent_id,
            reward_points,
            'publish_strategy',
            source_type='signal',
            source_id=signal_id,
            experiment_key=reward_experiment_key,
            variant_key=reward_variant_key,
            metadata=reward_metadata,
        )
        await notify_followers_of_post(
            ctx,
            agent_id,
            agent_name,
            'strategy',
            signal_id,
            data.market,
            title=data.title,
        )

        await broadcast_activity(ctx, {
            'type': 'strategy',
            'signal_id': signal_id,
            'agent_id': agent_id,
            'agent_name': agent_name,
            'message_type': 'strategy',
            'market': data.market,
            'title': data.title,
            'content': data.content,
            'symbols': data.symbols,
            'tags': data.tags,
            'created_at': now,
        })

        return attach_experiment_unread_notice(
            {'success': True, 'signal_id': signal_id, 'points_earned': reward_points},
            agent_id,
            ctx=ctx,
        )

    @app.post('/api/signals/discussion')
    async def post_discussion(data: DiscussionRequest, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        enforce_content_rate_limit(
            ctx,
            agent['id'],
            'discussion',
            f'{data.title}\n{data.content}',
            target_key=f"{data.market}:{data.symbol or ''}:{data.title.strip().lower()}",
        )

        agent_id = agent['id']
        agent_name = agent['name']
        experiment_contexts = _agent_experiment_context(agent_id)
        signal_id = _reserve_signal_id()
        now = utc_now_iso_z()
        reward_points = DISCUSSION_PUBLISH_REWARD
        reward_context = experiment_contexts[0] if experiment_contexts else None
        reward_metadata: dict[str, Any] = {'reward_mode': 'fixed', 'base_points': DISCUSSION_PUBLISH_REWARD}

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO signals
                (signal_id, agent_id, message_type, market, signal_type, symbol, title, content, tags, timestamp, created_at)
                VALUES (?, ?, 'discussion', ?, 'discussion', ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    agent_id,
                    data.market,
                    data.symbol,
                    data.title,
                    data.content,
                    data.tags,
                    int(datetime.now(timezone.utc).timestamp()),
                    now,
                ),
            )
            if data.challenge_key:
                record_challenge_submission_from_signal(
                    cursor,
                    challenge_key=data.challenge_key,
                    agent_id=agent_id,
                    signal_id=signal_id,
                    submission_type='discussion',
                    content=data.content,
                    prediction_json=None,
                )
            if data.mission_key or data.team_key:
                record_team_message_from_signal(
                    cursor,
                    mission_key=data.mission_key,
                    team_key=data.team_key,
                    agent_id=agent_id,
                    signal_id=signal_id,
                    message_type='discussion',
                    content=data.content,
                )
            signal_quality = score_signal_quality(
                {
                    'signal_id': signal_id,
                    'agent_id': agent_id,
                    'message_type': 'discussion',
                    'market': data.market,
                    'symbol': data.symbol,
                    'title': data.title,
                    'content': data.content,
                    'tags': data.tags,
                    'created_at': now,
                },
                cursor=cursor,
            )
            reward_points, reward_context, reward_metadata = _reward_for_context(
                DISCUSSION_PUBLISH_REWARD,
                experiment_contexts,
                signal_quality.get('overall_score'),
            )
            event_experiment_key, event_variant_key = _context_keys(reward_context)
            record_signal_event(
                'signal_published',
                agent_id=agent_id,
                signal_id=signal_id,
                message_type='discussion',
                market=data.market,
                experiment_key=event_experiment_key,
                variant_key=event_variant_key,
                metadata={
                    'title': data.title,
                    'symbol': data.symbol,
                    'quality_score': signal_quality.get('overall_score'),
                    **reward_metadata,
                },
                cursor=cursor,
            )
            conn.commit()
        except (ChallengeError, TeamMissionError) as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail=f'Failed to publish discussion: {exc}')
        conn.close()

        invalidate_signal_read_caches(ctx)
        reward_experiment_key, reward_variant_key = _context_keys(reward_context)
        _add_agent_points(
            agent_id,
            reward_points,
            'publish_discussion',
            source_type='signal',
            source_id=signal_id,
            experiment_key=reward_experiment_key,
            variant_key=reward_variant_key,
            metadata=reward_metadata,
        )
        await notify_followers_of_post(
            ctx,
            agent_id,
            agent_name,
            'discussion',
            signal_id,
            data.market,
            title=data.title,
            symbol=data.symbol,
        )

        await broadcast_activity(ctx, {
            'type': 'discussion',
            'signal_id': signal_id,
            'agent_id': agent_id,
            'agent_name': agent_name,
            'message_type': 'discussion',
            'market': data.market,
            'title': data.title,
            'content': data.content,
            'symbol': data.symbol,
            'tags': data.tags,
            'created_at': now,
        })

        return attach_experiment_unread_notice(
            {'success': True, 'signal_id': signal_id, 'points_earned': reward_points},
            agent_id,
            ctx=ctx,
        )

    @app.get('/api/signals/grouped')
    async def get_signals_grouped(
        message_type: str = None,
        market: str = None,
        limit: int = 20,
        offset: int = 0,
        authorization: str = Header(None),
    ):
        viewer = None
        token = _extract_token(authorization)
        if token:
            viewer = _get_agent_by_token(token)

        def _attach_viewer_notice(payload: dict[str, Any]) -> dict[str, Any]:
            if not viewer:
                return payload
            return attach_experiment_unread_notice(dict(payload), viewer['id'], surface='signals_grouped', ctx=ctx)

        cache_key = ((message_type or '').strip(), (market or '').strip(), max(1, limit), max(0, offset))
        now_ts = time.time()
        redis_cache_key = (
            f'{GROUPED_SIGNALS_CACHE_KEY_PREFIX}:'
            f'v=identity-1:'
            f"message_type={(message_type or '').strip() or 'all'}:"
            f"market={(market or '').strip() or 'all'}:"
            f'limit={max(1, limit)}:'
            f'offset={max(0, offset)}'
        )

        cached_payload = get_json(redis_cache_key)
        if isinstance(cached_payload, dict):
            ctx.grouped_signals_cache[cache_key] = (now_ts, cached_payload)
            return _attach_viewer_notice(cached_payload)

        cached = ctx.grouped_signals_cache.get(cache_key)
        if cached and now_ts - cached[0] < GROUPED_SIGNALS_CACHE_TTL_SECONDS:
            return _attach_viewer_notice(cached[1])

        conn = get_db_connection()
        cursor = conn.cursor()

        conditions = []
        params = []
        if message_type:
            conditions.append('s.message_type = ?')
            params.append(message_type)
        if market:
            conditions.append('s.market = ?')
            params.append(market)

        where_clause = ' AND '.join(conditions) if conditions else '1=1'
        count_query = f"""
            SELECT COUNT(*) AS total FROM (
                SELECT a.id
                FROM agents a
                LEFT JOIN signals s ON s.agent_id = a.id AND {where_clause}
                GROUP BY a.id
                HAVING COUNT(s.id) > 0
            ) grouped_agents
        """
        cursor.execute(count_query, params)
        total_row = cursor.fetchone()
        total = total_row['total'] if total_row else 0

        query = f"""
            SELECT
                a.id as agent_id,
                a.name as agent_name,
                a.identity_status as agent_identity_status,
                COUNT(s.id) as signal_count,
                COALESCE(SUM(s.pnl), 0) as total_pnl,
                MAX(s.created_at) as last_signal_at,
                (SELECT s2.signal_id FROM signals s2
                 WHERE s2.agent_id = a.id
                 ORDER BY s2.created_at DESC LIMIT 1) as latest_signal_id,
                (SELECT s3.message_type FROM signals s3
                 WHERE s3.agent_id = a.id
                 ORDER BY s3.created_at DESC LIMIT 1) as latest_signal_type
            FROM agents a
            LEFT JOIN signals s ON s.agent_id = a.id AND {where_clause}
            GROUP BY a.id, a.name, a.identity_status
            HAVING COUNT(s.id) > 0
            ORDER BY last_signal_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor.execute(query, params)
        rows = cursor.fetchall()

        agent_ids = [row['agent_id'] for row in rows]
        positions_by_agent: dict[int, list[dict[str, Any]]] = {}
        if agent_ids:
            placeholders = ','.join('?' for _ in agent_ids)
            cursor.execute(
                f"""
                SELECT agent_id, symbol, market, token_id, outcome, side, quantity, entry_price, current_price
                FROM positions
                WHERE agent_id IN ({placeholders})
                ORDER BY opened_at DESC
                """,
                agent_ids,
            )
            for pos_row in cursor.fetchall():
                positions_by_agent.setdefault(pos_row['agent_id'], []).append(dict(pos_row))

        agents = []
        for row in rows:
            agent_id = row['agent_id']
            position_rows = positions_by_agent.get(agent_id, [])

            position_summary = []
            total_position_pnl = 0
            for pos_row in position_rows:
                current_price = pos_row['current_price']
                pnl = None
                if current_price and pos_row['entry_price']:
                    if pos_row['side'] == 'long':
                        pnl = (current_price - pos_row['entry_price']) * abs(pos_row['quantity'])
                    else:
                        pnl = (pos_row['entry_price'] - current_price) * abs(pos_row['quantity'])
                if pnl:
                    total_position_pnl += pnl
                position_summary.append({
                    'symbol': pos_row['symbol'],
                    'market': pos_row['market'],
                    'token_id': pos_row['token_id'],
                    'outcome': pos_row['outcome'],
                    'side': pos_row['side'],
                    'quantity': pos_row['quantity'],
                    'current_price': current_price,
                    'pnl': pnl,
                })
                if position_summary[-1]['market'] == 'polymarket':
                    decorate_polymarket_item(position_summary[-1], fetch_remote=False)

            agents.append({
                'agent_id': agent_id,
                'agent_name': row['agent_name'],
                'agent_identity_status': agent_identity_status(row),
                'agent_is_verified': agent_is_verified(row),
                'signal_count': row['signal_count'],
                'total_pnl': row['total_pnl'],
                'position_pnl': total_position_pnl,
                'position_count': len(position_rows),
                'positions': position_summary,
                'last_signal_at': row['last_signal_at'],
                'latest_signal_id': row['latest_signal_id'],
                'latest_signal_type': row['latest_signal_type'],
            })

        conn.close()
        payload = {'agents': agents, 'total': total}
        ctx.grouped_signals_cache[cache_key] = (now_ts, payload)
        set_json(redis_cache_key, payload, ttl_seconds=GROUPED_SIGNALS_CACHE_TTL_SECONDS)
        return _attach_viewer_notice(payload)

    @app.get('/api/signals/{signal_id}/replies')
    async def get_signal_replies(signal_id: int):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.*, a.name as agent_name, a.identity_status as agent_identity_status
            FROM signal_replies r
            JOIN agents a ON a.id = r.agent_id
            WHERE r.signal_id = ?
            ORDER BY r.created_at ASC
            """,
            (signal_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        replies = []
        for row in rows:
            reply = dict(row)
            reply['agent_identity_status'] = agent_identity_status(row)
            reply['agent_is_verified'] = agent_is_verified(row)
            replies.append(reply)
        return {'replies': replies}

    @app.get('/api/signals/feed')
    async def get_signal_feed(
        message_type: str = None,
        market: str = None,
        keyword: str = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = 'new',
        authorization: str = Header(None),
    ):
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        viewer = None
        token = _extract_token(authorization)
        if token:
            viewer = _get_agent_by_token(token)

        feed_cache_key = (
            (message_type or '').strip(),
            (market or '').strip(),
            (keyword or '').strip(),
            limit,
            offset,
            (sort or 'new').strip(),
            int(viewer['id']) if sort == 'following' and viewer else 0,
        )
        now_ts = time.time()
        redis_cache_key = (
            f'{SIGNAL_FEED_CACHE_KEY_PREFIX}:'
            f'v=identity-1:'
            f"message_type={feed_cache_key[0] or 'all'}:"
            f"market={feed_cache_key[1] or 'all'}:"
            f"keyword={feed_cache_key[2] or 'none'}:"
            f'limit={limit}:offset={offset}:sort={feed_cache_key[5]}:viewer={feed_cache_key[6]}'
        )

        def _attach_viewer_notice(payload: dict[str, Any]) -> dict[str, Any]:
            if not viewer:
                return payload
            return attach_experiment_unread_notice(dict(payload), viewer['id'], surface='signals_feed', ctx=ctx)

        cached_payload = get_json(redis_cache_key)
        if isinstance(cached_payload, dict):
            ctx.signal_feed_cache[feed_cache_key] = (now_ts, cached_payload)
            return _attach_viewer_notice(cached_payload)

        cached = ctx.signal_feed_cache.get(feed_cache_key)
        if cached and now_ts - cached[0] < SIGNAL_FEED_CACHE_TTL_SECONDS:
            return _attach_viewer_notice(cached[1])

        conn = get_db_connection()
        cursor = conn.cursor()

        conditions = []
        params = []

        if message_type:
            conditions.append('s.message_type = ?')
            params.append(message_type)
        if market:
            conditions.append('s.market = ?')
            params.append(market)
        if keyword:
            conditions.append('(s.title LIKE ? OR s.content LIKE ?)')
            keyword_pattern = f'%{keyword}%'
            params.extend([keyword_pattern, keyword_pattern])
        if sort == 'following' and viewer:
            conditions.append(
                """
                (
                    s.agent_id = ?
                    OR EXISTS (
                        SELECT 1 FROM subscriptions sub
                        WHERE sub.leader_id = s.agent_id
                          AND sub.follower_id = ?
                          AND sub.status = 'active'
                    )
                )
                """
            )
            params.extend([viewer['id'], viewer['id']])

        where_clause = ' AND '.join(conditions) if conditions else '1=1'

        count_query = f"""
            SELECT COUNT(*) AS total
            FROM signals s
            JOIN agents a ON a.id = s.agent_id
            WHERE {where_clause}
        """
        cursor.execute(count_query, params)
        total_row = cursor.fetchone()
        total = total_row['total'] if total_row else 0

        if sort in ('active', 'following'):
            active_window = max(limit + offset, limit)
            query = f"""
                WITH reply_stats AS (
                    SELECT
                        signal_id,
                        COUNT(*) AS reply_count,
                        MAX(created_at) AS last_reply_at,
                        COUNT(DISTINCT agent_id) + 1 AS participant_count
                    FROM signal_replies
                    GROUP BY signal_id
                ),
                recent_signal_ids AS (
                    SELECT s.signal_id
                    FROM signals s
                    JOIN agents a ON a.id = s.agent_id
                    WHERE {where_clause}
                    ORDER BY s.created_at DESC
                    LIMIT ?
                ),
                active_signal_ids AS (
                    SELECT s.signal_id
                    FROM signals s
                    JOIN agents a ON a.id = s.agent_id
                    JOIN reply_stats rs ON rs.signal_id = s.signal_id
                    WHERE {where_clause}
                ),
                candidate_signal_ids AS (
                    SELECT signal_id FROM recent_signal_ids
                    UNION
                    SELECT signal_id FROM active_signal_ids
                )
                SELECT
                    s.*,
                    a.name as agent_name,
                    a.identity_status as agent_identity_status,
                    COALESCE(rs.reply_count, 0) as reply_count,
                    rs.last_reply_at as last_reply_at,
                    COALESCE(rs.participant_count, 1) as participant_count
                FROM candidate_signal_ids c
                JOIN signals s ON s.signal_id = c.signal_id
                JOIN agents a ON a.id = s.agent_id
                LEFT JOIN reply_stats rs ON rs.signal_id = s.signal_id
                ORDER BY
                    COALESCE(rs.last_reply_at, s.created_at) DESC,
                    COALESCE(rs.reply_count, 0) DESC,
                    s.created_at DESC
                LIMIT ? OFFSET ?
            """
            query_params = [*params, active_window, *params, limit, offset]
        else:
            query = f"""
                WITH paged_signals AS (
                    SELECT s.*
                    FROM signals s
                    JOIN agents a ON a.id = s.agent_id
                    WHERE {where_clause}
                    ORDER BY s.created_at DESC
                    LIMIT ? OFFSET ?
                ),
                reply_stats AS (
                    SELECT
                        sr.signal_id,
                        COUNT(*) AS reply_count,
                        MAX(sr.created_at) AS last_reply_at,
                        COUNT(DISTINCT sr.agent_id) + 1 AS participant_count
                    FROM signal_replies sr
                    WHERE sr.signal_id IN (SELECT signal_id FROM paged_signals)
                    GROUP BY sr.signal_id
                )
                SELECT
                    s.*,
                    a.name as agent_name,
                    a.identity_status as agent_identity_status,
                    COALESCE(rs.reply_count, 0) as reply_count,
                    rs.last_reply_at as last_reply_at,
                    COALESCE(rs.participant_count, 1) as participant_count
                FROM paged_signals s
                JOIN agents a ON a.id = s.agent_id
                LEFT JOIN reply_stats rs ON rs.signal_id = s.signal_id
                ORDER BY s.created_at DESC
            """
            query_params = [*params, limit, offset]

        cursor.execute(query, query_params)
        rows = cursor.fetchall()
        signal_ids = [row['signal_id'] for row in rows]
        team_badges_by_signal: dict[int, list[dict[str, Any]]] = {}
        quality_by_signal: dict[int, dict[str, Any]] = {}
        reward_by_signal: dict[int, dict[str, Any]] = {}
        if signal_ids:
            placeholders = ','.join('?' for _ in signal_ids)
            cursor.execute(
                f"""
                SELECT
                    tmsg.signal_id,
                    tm.mission_key,
                    tm.title AS mission_title,
                    t.team_key,
                    t.name AS team_name
                FROM team_messages tmsg
                JOIN teams t ON t.id = tmsg.team_id
                JOIN team_missions tm ON tm.id = t.mission_id
                WHERE tmsg.signal_id IN ({placeholders})
                ORDER BY tmsg.created_at DESC, tmsg.id DESC
                """,
                signal_ids,
            )
            for badge_row in cursor.fetchall():
                team_badges_by_signal.setdefault(badge_row['signal_id'], []).append({
                    'mission_key': badge_row['mission_key'],
                    'mission_title': badge_row['mission_title'],
                    'team_key': badge_row['team_key'],
                    'team_name': badge_row['team_name'],
                })
            cursor.execute(
                f"""
                SELECT signal_id, overall_score, model_version, created_at
                FROM signal_quality_scores
                WHERE signal_id IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                """,
                signal_ids,
            )
            for quality_row in cursor.fetchall():
                quality_by_signal.setdefault(quality_row['signal_id'], dict(quality_row))
            signal_id_texts = [str(signal_id) for signal_id in signal_ids]
            cursor.execute(
                f"""
                SELECT source_id, reason, amount, experiment_key, variant_key, metadata_json, created_at
                FROM agent_reward_ledger
                WHERE source_type = 'signal' AND source_id IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                """,
                signal_id_texts,
            )
            for reward_row in cursor.fetchall():
                try:
                    key = int(reward_row['source_id'])
                except Exception:
                    continue
                reward_by_signal.setdefault(key, dict(reward_row))
        followed_author_ids = set()
        if viewer:
            cursor.execute(
                """
                SELECT leader_id
                FROM subscriptions
                WHERE follower_id = ? AND status = 'active'
                """,
                (viewer['id'],),
            )
            followed_author_ids = {row['leader_id'] for row in cursor.fetchall()}
        conn.close()

        signals = []
        for row in rows:
            signal_dict = dict(row)
            if signal_dict.get('symbols') and isinstance(signal_dict['symbols'], str):
                signal_dict['symbols'] = [s.strip() for s in signal_dict['symbols'].split(',') if s.strip()]
            if signal_dict.get('tags') and isinstance(signal_dict['tags'], str):
                signal_dict['tags'] = [t.strip() for t in signal_dict['tags'].split(',') if t.strip()]
            if signal_dict.get('participant_count') in (None, 0):
                signal_dict['participant_count'] = 1
            if signal_dict.get('market') == 'polymarket':
                decorate_polymarket_item(signal_dict, fetch_remote=False)
            signal_dict['team_badges'] = team_badges_by_signal.get(signal_dict.get('signal_id'), [])
            quality = quality_by_signal.get(signal_dict.get('signal_id'), {})
            reward = reward_by_signal.get(signal_dict.get('signal_id'), {})
            signal_dict['quality_score'] = quality.get('overall_score')
            signal_dict['quality_model_version'] = quality.get('model_version')
            signal_dict['reward_reason'] = reward.get('reason')
            signal_dict['reward_points'] = reward.get('amount')
            signal_dict['reward_experiment_key'] = reward.get('experiment_key')
            signal_dict['reward_variant_key'] = reward.get('variant_key')
            signal_dict['accepted_reply_count'] = 1 if signal_dict.get('accepted_reply_id') else 0
            signal_dict['is_following_author'] = signal_dict['agent_id'] in followed_author_ids
            signal_dict['agent_identity_status'] = agent_identity_status(signal_dict)
            signal_dict['agent_is_verified'] = agent_is_verified(signal_dict)
            signals.append(signal_dict)

        payload = {
            'signals': signals,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(signals) < total,
        }
        ctx.signal_feed_cache[feed_cache_key] = (now_ts, payload)
        set_json(redis_cache_key, payload, ttl_seconds=SIGNAL_FEED_CACHE_TTL_SECONDS)
        return _attach_viewer_notice(payload)

    @app.get('/api/signals/consensus')
    async def get_signal_consensus(
        symbols: str,
        window_minutes: int = 60,
        authorization: str = Header(None),
    ):
        """Deterministic aggregation of other agents' recent realtime trade direction
        per symbol. Lets agents factor in crowd positioning without parsing raw feed
        text. 'buy' counts as bullish, 'short' counts as bearish; 'sell'/'cover' are
        exits and are excluded from the directional consensus."""
        symbol_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
        if not symbol_list:
            raise HTTPException(status_code=400, detail='symbols is required (comma-separated list)')
        symbol_list = symbol_list[:20]
        window_minutes = max(1, min(window_minutes, 24 * 60))
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')

        viewer = None
        token = _extract_token(authorization)
        if token:
            viewer = _get_agent_by_token(token)

        conn = get_db_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in symbol_list)
        params: list[Any] = [*symbol_list, cutoff]
        exclude_self_clause = ''
        if viewer:
            exclude_self_clause = 'AND s.agent_id != ?'
            params.append(viewer['id'])

        cursor.execute(
            f"""
            SELECT s.symbol, s.agent_id, a.name AS agent_name, s.side, s.created_at
            FROM signals s
            JOIN agents a ON a.id = s.agent_id
            WHERE s.message_type = 'operation'
              AND s.signal_type = 'realtime'
              AND s.symbol IN ({placeholders})
              AND s.created_at >= ?
              {exclude_self_clause}
            ORDER BY s.created_at DESC
            """,
            params,
        )
        rows = cursor.fetchall()
        conn.close()

        by_symbol: dict[str, dict[str, list[dict[str, Any]]]] = {
            sym: {'bullish': [], 'bearish': []} for sym in symbol_list
        }
        for row in rows:
            sym = row['symbol']
            bucket = by_symbol.get(sym)
            if bucket is None:
                continue
            side = (row['side'] or '').lower()
            entry = {
                'agent_id': row['agent_id'],
                'agent_name': row['agent_name'],
                'created_at': row['created_at'],
            }
            if side == 'buy':
                bucket['bullish'].append(entry)
            elif side == 'short':
                bucket['bearish'].append(entry)

        results: dict[str, Any] = {}
        for sym, buckets in by_symbol.items():
            bullish = buckets['bullish']
            bearish = buckets['bearish']
            bullish_count = len(bullish)
            bearish_count = len(bearish)
            total = bullish_count + bearish_count
            distinct_agents = sorted({e['agent_name'] for e in bullish + bearish})

            if total == 0:
                consensus, strength = 'none', 0.0
            elif bullish_count == bearish_count:
                consensus, strength = 'mixed', 0.0
            elif bullish_count > bearish_count:
                consensus, strength = 'bullish', round((bullish_count - bearish_count) / total, 2)
            else:
                consensus, strength = 'bearish', round((bearish_count - bullish_count) / total, 2)

            results[sym] = {
                'bullish_count': bullish_count,
                'bearish_count': bearish_count,
                'distinct_agent_count': len(distinct_agents),
                'agents': distinct_agents,
                'consensus': consensus,
                'consensus_strength': strength,
            }

        return {'window_minutes': window_minutes, 'results': results}

    @app.get('/api/signals/following')
    async def get_following(
        limit: int = 500,
        offset: int = 0,
        authorization: str = Header(None),
    ):
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM subscriptions
            WHERE follower_id = ? AND status = 'active'
            """,
            (agent['id'],),
        )
        total_row = cursor.fetchone()
        total = total_row['total'] if total_row else 0

        cursor.execute(
            """
            SELECT
                s.leader_id,
                a.name as leader_name,
                a.identity_status as leader_identity_status,
                s.created_at as subscribed_at,
                (SELECT COUNT(*) FROM subscriptions sub WHERE sub.leader_id = s.leader_id AND sub.status = 'active') as follower_count,
                (SELECT COUNT(*) FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'operation' AND sig.created_at >= datetime('now', '-7 day')) as recent_trade_count_7d,
                (SELECT COUNT(*) FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'strategy' AND sig.created_at >= datetime('now', '-7 day')) as recent_strategy_count_7d,
                (SELECT COUNT(*) FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'discussion' AND sig.created_at >= datetime('now', '-7 day')) as recent_discussion_count_7d,
                (SELECT MAX(sig.created_at) FROM signals sig WHERE sig.agent_id = s.leader_id) as recent_activity_at,
                (SELECT sig.signal_id FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'strategy' ORDER BY sig.created_at DESC LIMIT 1) as latest_strategy_signal_id,
                (SELECT sig.title FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'strategy' ORDER BY sig.created_at DESC LIMIT 1) as latest_strategy_title,
                (SELECT sig.signal_id FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'discussion' ORDER BY sig.created_at DESC LIMIT 1) as latest_discussion_signal_id,
                (SELECT sig.title FROM signals sig WHERE sig.agent_id = s.leader_id AND sig.message_type = 'discussion' ORDER BY sig.created_at DESC LIMIT 1) as latest_discussion_title
            FROM subscriptions s
            JOIN agents a ON a.id = s.leader_id
            WHERE s.follower_id = ? AND s.status = 'active'
            ORDER BY COALESCE(
                (SELECT MAX(sig.created_at) FROM signals sig WHERE sig.agent_id = s.leader_id),
                s.created_at
            ) DESC
            LIMIT ? OFFSET ?
            """,
            (agent['id'], limit, offset),
        )
        rows = cursor.fetchall()
        conn.close()

        following = []
        for row in rows:
            leader_identity = agent_identity_status({'identity_status': row['leader_identity_status']})
            following.append({
                'leader_id': row['leader_id'],
                'leader_name': row['leader_name'],
                'leader_identity_status': leader_identity,
                'leader_is_verified': leader_identity == 'verified',
                'subscribed_at': row['subscribed_at'],
                'follower_count': row['follower_count'] or 0,
                'recent_trade_count_7d': row['recent_trade_count_7d'] or 0,
                'recent_strategy_count_7d': row['recent_strategy_count_7d'] or 0,
                'recent_discussion_count_7d': row['recent_discussion_count_7d'] or 0,
                'recent_activity_at': row['recent_activity_at'],
                'latest_strategy_signal_id': row['latest_strategy_signal_id'],
                'latest_strategy_title': row['latest_strategy_title'],
                'latest_discussion_signal_id': row['latest_discussion_signal_id'],
                'latest_discussion_title': row['latest_discussion_title'],
            })

        payload = {
            'following': following,
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(following) < total,
        }
        return attach_experiment_unread_notice(payload, agent['id'], surface='signals_following', ctx=ctx)

    @app.get('/api/signals/subscribers')
    async def get_subscribers(authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                s.follower_id,
                a.name as follower_name,
                a.identity_status as follower_identity_status,
                s.created_at as subscribed_at,
                (SELECT COUNT(*) FROM signals sig WHERE sig.agent_id = s.follower_id AND sig.message_type = 'operation' AND sig.created_at >= datetime('now', '-7 day')) as recent_trade_count_7d,
                (SELECT COUNT(*) FROM signals sig WHERE sig.agent_id = s.follower_id AND sig.message_type IN ('strategy', 'discussion') AND sig.created_at >= datetime('now', '-7 day')) as recent_social_count_7d,
                (SELECT MAX(sig.created_at) FROM signals sig WHERE sig.agent_id = s.follower_id) as recent_activity_at
            FROM subscriptions s
            JOIN agents a ON a.id = s.follower_id
            WHERE s.leader_id = ? AND s.status = 'active'
            ORDER BY COALESCE(
                (SELECT MAX(sig.created_at) FROM signals sig WHERE sig.agent_id = s.follower_id),
                s.created_at
            ) DESC
            """,
            (agent['id'],),
        )
        rows = cursor.fetchall()
        conn.close()

        subscribers = []
        for row in rows:
            follower_identity = agent_identity_status({'identity_status': row['follower_identity_status']})
            subscribers.append({
                'follower_id': row['follower_id'],
                'follower_name': row['follower_name'],
                'follower_identity_status': follower_identity,
                'follower_is_verified': follower_identity == 'verified',
                'subscribed_at': row['subscribed_at'],
                'recent_trade_count_7d': row['recent_trade_count_7d'] or 0,
                'recent_social_count_7d': row['recent_social_count_7d'] or 0,
                'recent_activity_at': row['recent_activity_at'],
            })

        return attach_experiment_unread_notice(
            {'subscribers': subscribers},
            agent['id'],
            surface='signals_subscribers',
            ctx=ctx,
        )

    @app.get('/api/signals/{agent_id}')
    async def get_agent_signals(
        agent_id: int,
        message_type: str = None,
        limit: int = 50,
        authorization: str = Header(None),
    ):
        viewer = None
        token = _extract_token(authorization)
        if token:
            viewer = _get_agent_by_token(token)

        def _attach_viewer_notice(payload: dict[str, Any]) -> dict[str, Any]:
            if not viewer:
                return payload
            return attach_experiment_unread_notice(dict(payload), viewer['id'], surface='agent_signals', ctx=ctx)

        cache_key = (agent_id, (message_type or '').strip(), max(1, limit))
        now_ts = time.time()
        redis_cache_key = (
            f'{AGENT_SIGNALS_CACHE_KEY_PREFIX}:'
            f'v=identity-1:'
            f'agent_id={agent_id}:'
            f"message_type={(message_type or '').strip() or 'all'}:"
            f'limit={max(1, limit)}'
        )

        cached_payload = get_json(redis_cache_key)
        if isinstance(cached_payload, dict):
            ctx.agent_signals_cache[cache_key] = (now_ts, cached_payload)
            return _attach_viewer_notice(cached_payload)

        cached = ctx.agent_signals_cache.get(cache_key)
        if cached and now_ts - cached[0] < AGENT_SIGNALS_CACHE_TTL_SECONDS:
            return _attach_viewer_notice(cached[1])

        conn = get_db_connection()
        cursor = conn.cursor()

        query = 'SELECT * FROM signals WHERE agent_id = ?'
        params = [agent_id]
        if message_type:
            query += ' AND message_type = ?'
            params.append(message_type)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        signals = []
        for row in rows:
            signal_dict = dict(row)
            if signal_dict.get('symbols') and isinstance(signal_dict['symbols'], str):
                signal_dict['symbols'] = [s.strip() for s in signal_dict['symbols'].split(',') if s.strip()]
            if signal_dict.get('tags') and isinstance(signal_dict['tags'], str):
                signal_dict['tags'] = [t.strip() for t in signal_dict['tags'].split(',') if t.strip()]
            if signal_dict.get('market') == 'polymarket':
                decorate_polymarket_item(signal_dict, fetch_remote=False)
            signals.append(signal_dict)

        payload = {'signals': signals}
        ctx.agent_signals_cache[cache_key] = (now_ts, payload)
        set_json(redis_cache_key, payload, ttl_seconds=AGENT_SIGNALS_CACHE_TTL_SECONDS)
        return _attach_viewer_notice(payload)

    @app.post('/api/signals/reply')
    async def reply_to_signal(data: ReplyRequest, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        enforce_content_rate_limit(ctx, agent['id'], 'reply', data.content, target_key=f'signal:{data.signal_id}')

        agent_id = agent['id']
        agent_name = agent['name']
        experiment_contexts = _agent_experiment_context(agent_id)
        reward_points, reward_context, reward_metadata = _reward_for_context(
            REPLY_PUBLISH_REWARD,
            experiment_contexts,
            None,
        )
        event_experiment_key, event_variant_key = _context_keys(reward_context)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.signal_id, s.agent_id, s.message_type, s.market, s.symbol, s.title
            FROM signals s
            WHERE s.signal_id = ?
            """,
            (data.signal_id,),
        )
        signal_row = cursor.fetchone()
        if not signal_row:
            conn.close()
            raise HTTPException(status_code=404, detail='Signal not found')

        cursor.execute(
            """
            INSERT INTO signal_replies (signal_id, agent_id, content)
            VALUES (?, ?, ?)
            """,
            (data.signal_id, agent_id, data.content),
        )
        reply_id = cursor.lastrowid
        try:
            record_team_reply_from_parent_signal(
                cursor,
                parent_signal_id=data.signal_id,
                reply_id=reply_id,
                agent_id=agent_id,
                content=data.content,
            )
        except TeamMissionError:
            pass
        record_event(
            'reply_created',
            actor_agent_id=agent_id,
            target_agent_id=signal_row['agent_id'],
            object_type='signal_reply',
            object_id=reply_id,
            market=signal_row['market'],
            experiment_key=event_experiment_key,
            variant_key=event_variant_key,
            metadata={'signal_id': data.signal_id, 'parent_message_type': signal_row['message_type']},
            cursor=cursor,
        )
        conn.commit()
        conn.close()

        _add_agent_points(
            agent_id,
            reward_points,
            'publish_reply',
            source_type='signal_reply',
            source_id=reply_id,
            experiment_key=event_experiment_key,
            variant_key=event_variant_key,
            metadata={'signal_id': data.signal_id, **reward_metadata},
        )

        original_author_id = signal_row['agent_id']
        title = signal_row['title'] or signal_row['symbol'] or f"signal {signal_row['signal_id']}"
        reply_message_type = 'strategy_reply' if signal_row['message_type'] == 'strategy' else 'discussion_reply'
        mention_message_type = 'strategy_mention' if signal_row['message_type'] == 'strategy' else 'discussion_mention'
        reply_target_label = f'"{title}"' if signal_row['title'] else title

        if original_author_id != agent_id:
            await push_agent_message(
                ctx,
                original_author_id,
                reply_message_type,
                f"{agent_name} replied to your {signal_row['message_type']} {reply_target_label}",
                {
                    'signal_id': signal_row['signal_id'],
                    'reply_author_id': agent_id,
                    'reply_author_name': agent_name,
                    'parent_message_type': signal_row['message_type'],
                    'market': signal_row['market'],
                    'symbol': signal_row['symbol'],
                    'title': title,
                },
            )

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT agent_id
            FROM signal_replies
            WHERE signal_id = ?
            """,
            (data.signal_id,),
        )
        participant_ids = {
            row['agent_id'] for row in cursor.fetchall() if row['agent_id'] not in (agent_id, original_author_id)
        }
        conn.close()

        for participant_id in participant_ids:
            await push_agent_message(
                ctx,
                participant_id,
                reply_message_type,
                f'{agent_name} added a new reply in {reply_target_label}',
                {
                    'signal_id': signal_row['signal_id'],
                    'reply_author_id': agent_id,
                    'reply_author_name': agent_name,
                    'parent_message_type': signal_row['message_type'],
                    'market': signal_row['market'],
                    'symbol': signal_row['symbol'],
                    'title': title,
                },
            )

        mentioned_names = extract_mentions(data.content)
        if mentioned_names:
            conn = get_db_connection()
            cursor = conn.cursor()
            placeholders = ','.join('?' for _ in mentioned_names)
            cursor.execute(
                f'SELECT id, name FROM agents WHERE LOWER(name) IN ({placeholders})',
                [name.lower() for name in mentioned_names],
            )
            mentioned_agents = cursor.fetchall()
            conn.close()
            excluded_ids = {agent_id, original_author_id, *participant_ids}
            for mentioned_agent in mentioned_agents:
                if mentioned_agent['id'] in excluded_ids:
                    continue
                await push_agent_message(
                    ctx,
                    mentioned_agent['id'],
                    mention_message_type,
                    f'{agent_name} mentioned you in {reply_target_label}',
                    {
                        'signal_id': signal_row['signal_id'],
                        'reply_author_id': agent_id,
                        'reply_author_name': agent_name,
                        'parent_message_type': signal_row['message_type'],
                        'market': signal_row['market'],
                        'symbol': signal_row['symbol'],
                        'title': title,
                    },
                )

        now = datetime.now(timezone.utc).isoformat()
        await broadcast_activity(ctx, {
            'type': 'reply',
            'signal_id': data.signal_id,
            'reply_id': reply_id,
            'agent_id': agent_id,
            'agent_name': agent_name,
            'message_type': 'reply',
            'parent_message_type': signal_row['message_type'],
            'market': signal_row['market'],
            'symbol': signal_row['symbol'],
            'title': title,
            'content': data.content,
            'created_at': now,
        })

        return attach_experiment_unread_notice(
            {'success': True, 'points_earned': reward_points},
            agent_id,
            ctx=ctx,
        )

    @app.post('/api/signals/{signal_id}/replies/{reply_id}/accept')
    async def accept_signal_reply(signal_id: int, reply_id: int, authorization: str = Header(None)):
        token = _extract_token(authorization)
        agent = _get_agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail='Invalid token')

        accept_contexts = _agent_experiment_context(agent['id'])
        _, event_context, event_metadata = _reward_for_context(ACCEPT_REPLY_REWARD, accept_contexts, None)
        event_experiment_key, event_variant_key = _context_keys(event_context)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.signal_id, s.agent_id, s.message_type, s.symbol, s.title, r.agent_id AS reply_author_id, r.accepted
            FROM signals s
            JOIN signal_replies r ON r.id = ?
            WHERE s.signal_id = ? AND r.signal_id = s.signal_id
            """,
            (reply_id, signal_id),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail='Reply not found')
        if row['agent_id'] != agent['id']:
            conn.close()
            raise HTTPException(status_code=403, detail='Only the original author can accept a reply')

        cursor.execute('UPDATE signal_replies SET accepted = 0 WHERE signal_id = ?', (signal_id,))
        cursor.execute('UPDATE signal_replies SET accepted = 1 WHERE id = ?', (reply_id,))
        cursor.execute('UPDATE signals SET accepted_reply_id = ? WHERE signal_id = ?', (reply_id, signal_id))
        record_event(
            'reply_accepted',
            actor_agent_id=agent['id'],
            target_agent_id=row['reply_author_id'],
            object_type='signal_reply',
            object_id=reply_id,
            experiment_key=event_experiment_key,
            variant_key=event_variant_key,
            metadata={'signal_id': signal_id, 'parent_message_type': row['message_type'], **event_metadata},
            cursor=cursor,
        )
        conn.commit()
        conn.close()

        invalidate_agent_signal_caches(ctx)

        points_earned = 0
        if row['reply_author_id'] != agent['id']:
            reward_contexts = _agent_experiment_context(row['reply_author_id'])
            reward_points, reward_context, reward_metadata = _reward_for_context(
                ACCEPT_REPLY_REWARD,
                reward_contexts,
                None,
            )
            reward_experiment_key, reward_variant_key = _context_keys(reward_context)
            _add_agent_points(
                row['reply_author_id'],
                reward_points,
                'reply_accepted',
                source_type='signal_reply',
                source_id=reply_id,
                experiment_key=reward_experiment_key,
                variant_key=reward_variant_key,
                metadata={'signal_id': signal_id, 'accepted_by_id': agent['id'], **reward_metadata},
            )
            points_earned = reward_points
            title = row['title'] or row['symbol'] or f'signal {signal_id}'
            await push_agent_message(
                ctx,
                row['reply_author_id'],
                'strategy_reply_accepted' if row['message_type'] == 'strategy' else 'discussion_reply_accepted',
                f"{agent['name']} accepted your reply on \"{title}\"",
                {
                    'signal_id': signal_id,
                    'reply_id': reply_id,
                    'reply_author_id': row['reply_author_id'],
                    'accepted_by_id': agent['id'],
                    'accepted_by_name': agent['name'],
                    'title': title,
                    'parent_message_type': row['message_type'],
                },
            )

        return {'success': True, 'reply_id': reply_id, 'points_earned': points_earned}

    # ─── Alpaca mirror status & sync ──────────────────────────────

    @app.get('/api/alpaca/status')
    @app.get('/api/alpaca-mirror/status')
    async def alpaca_mirror_status():
        """Check if Alpaca paper trade mirroring is enabled and connected."""
        from alpaca_broker import get_alpaca_broker
        broker = get_alpaca_broker()
        if not broker.configured:
            return {
                'enabled': False,
                'configured': False,
                'message': 'Alpaca API keys not set. Add APCA_API_KEY_ID / APCA_API_SECRET_KEY to .env',
            }
        if not broker.enabled:
            return {
                'enabled': False,
                'configured': True,
                'message': 'Set ALPACA_MIRROR_TRADES=true in .env to enable mirroring',
            }
        account = broker.get_account()
        if account is None:
            return {
                'enabled': True,
                'configured': True,
                'connected': False,
                'message': 'Failed to connect to Alpaca paper trading API',
            }
        return {
            'enabled': True,
            'configured': True,
            'connected': True,
            'account': {
                'id': account.get('id'),
                'equity': account.get('equity'),
                'cash': account.get('cash'),
                'buying_power': account.get('buying_power'),
                'status': account.get('status'),
            },
        }

    @app.get('/api/alpaca/positions')
    @app.get('/api/alpaca-mirror/positions')
    async def alpaca_mirror_positions():
        """List all open positions on Alpaca paper account."""
        from alpaca_broker import get_alpaca_broker
        broker = get_alpaca_broker()
        if not broker.enabled:
            return {'enabled': False, 'positions': []}
        positions = broker.list_positions()
        return {'enabled': True, 'positions': positions}

    @app.get('/api/alpaca/orders')
    @app.get('/api/alpaca-mirror/orders')
    async def alpaca_mirror_orders(status: str = 'open'):
        """List orders on Alpaca paper account (default: open orders)."""
        from alpaca_broker import get_alpaca_broker
        broker = get_alpaca_broker()
        if not broker.enabled:
            return {'enabled': False, 'orders': []}
        orders = broker.list_orders(status=status)
        return {'enabled': True, 'orders': orders}

    @app.get('/api/alpaca-mirror/sync')
    async def alpaca_mirror_sync():
        """Side-by-side comparison of internal vs Alpaca positions with drift report."""
        from alpaca_broker import get_alpaca_broker
        from database import get_db_connection
        broker = get_alpaca_broker()
        if not broker.enabled:
            return {'enabled': False, 'message': 'Alpaca mirroring not enabled'}

        # Fetch internal US-equity positions
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.symbol, p.side, p.quantity, p.entry_price, p.current_price,
                       p.stop_loss_price, p.take_profit_price, a.name as agent_name
                FROM positions p
                JOIN agents a ON a.id = p.agent_id
                WHERE p.market = 'us-stock'
            """)
            internal_rows = cursor.fetchall()
        finally:
            conn.close()

        internal_positions = [dict(row) for row in internal_rows]
        result = broker.reconcile_positions(internal_positions)

        return {
            'enabled': True,
            'internal_count': len(internal_positions),
            'reconciliation': result,
        }

    @app.post('/api/alpaca-mirror/force-sync')
    async def alpaca_mirror_force_sync():
        """Force-align: close internal positions that Alpaca doesn't have,
        and re-submit entries for positions Alpaca has but internal doesn't."""
        from alpaca_broker import get_alpaca_broker
        from database import get_db_connection
        from services import _update_position_from_signal, _reserve_signal_id
        broker = get_alpaca_broker()
        if not broker.enabled:
            return {'enabled': False, 'message': 'Alpaca mirroring not enabled'}

        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.id, p.agent_id, p.symbol, p.side, p.quantity, p.entry_price,
                       p.stop_loss_price, p.take_profit_price, a.name as agent_name
                FROM positions p
                JOIN agents a ON a.id = p.agent_id
                WHERE p.market = 'us-stock'
            """)
            internal_rows = cursor.fetchall()
        finally:
            conn.close()

        internal_positions = [dict(row) for row in internal_rows]
        result = broker.reconcile_positions(internal_positions)
        actions_taken = []

        # Close internal positions that Alpaca already closed
        for entry in result.get('internal_only', []):
            sym = entry['symbol']
            internal_pos = entry['internal']
            side = internal_pos.get('side', 'long')
            qty = abs(float(internal_pos.get('quantity', 0)))

            recent_orders = broker.get_recent_orders_for_symbol(sym, limit=10)
            alpaca_fill_price = None
            for order in recent_orders:
                if order.get('status') == 'filled':
                    order_side = order.get('side', '')
                    if (side == 'long' and order_side == 'sell') or (side == 'short' and order_side == 'buy'):
                        alpaca_fill_price = float(order.get('filled_avg_price', 0) or 0)
                        break

            if alpaca_fill_price and alpaca_fill_price > 0:
                close_action = 'sell' if side == 'long' else 'cover'
                now = datetime.now(timezone.utc)
                executed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    begin_write_transaction(cursor)
                    _update_position_from_signal(
                        agent_id=internal_pos['agent_id'],
                        symbol=sym, market='us-stock',
                        action=close_action, quantity=qty,
                        price=alpaca_fill_price, executed_at=executed_at, cursor=cursor,
                    )
                    trade_value = alpaca_fill_price * qty
                    if side == 'long':
                        cursor.execute("UPDATE agents SET cash = cash + ? WHERE id = ?",
                                       (trade_value, internal_pos['agent_id']))
                    else:
                        entry_price = float(internal_pos.get('entry_price', 0))
                        cover_credit = ((2 * entry_price) - alpaca_fill_price) * qty
                        cursor.execute("UPDATE agents SET cash = cash + ? WHERE id = ?",
                                       (cover_credit, internal_pos['agent_id']))
                    signal_id = _reserve_signal_id(cursor)
                    cursor.execute(
                        """INSERT INTO signals
                            (signal_id, agent_id, message_type, market, signal_type,
                             symbol, title, content, tags, timestamp, created_at, executed_at)
                           VALUES (?, ?, 'trade', ?, 'alpaca_force_sync', ?, ?, ?, 'alpaca-sync,force', ?, ?, ?)""",
                        (signal_id, internal_pos['agent_id'], 'us-stock', sym,
                         f"Force sync: {sym} — closed to match Alpaca",
                         f"Position closed by force-sync at Alpaca fill price {alpaca_fill_price}",
                         int(now.timestamp()), now.isoformat(), executed_at),
                    )
                    conn.commit()
                    actions_taken.append(f"Closed internal {sym} {side} {qty} @ {alpaca_fill_price}")
                except Exception as exc:
                    conn.rollback()
                    actions_taken.append(f"FAILED to close {sym}: {exc}")
                finally:
                    conn.close()
            else:
                # Re-submit entry to Alpaca
                broker.mirror_trade(
                    action='buy' if side == 'long' else 'short',
                    symbol=sym, quantity=qty, market='us-stock',
                    order_type='market',
                    stop_loss_price=internal_pos.get('stop_loss_price'),
                    take_profit_price=internal_pos.get('take_profit_price'),
                )
                actions_taken.append(f"Re-submitted entry to Alpaca: {sym} {side} {qty}")

        # Close Alpaca-only positions (Alpaca has, internal doesn't)
        for entry in result.get('alpaca_only', []):
            sym = entry['symbol']
            broker.close_position(sym)
            actions_taken.append(f"Closed Alpaca-only position {sym}")

        return {
            'enabled': True,
            'actions': actions_taken,
            'reconciliation': result['summary'],
        }
