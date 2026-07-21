"""
Tasks Module

后台任务管理
"""

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# Global trending cache (shared with routes)
trending_cache: list = []
_last_profit_history_prune_at: float = time.time()
_profit_history_prune_task: Optional[asyncio.Task] = None
_TRENDING_CACHE_KEY = "trending:top20"
_SUPPORTED_PRICE_MARKETS = {"crypto", "polymarket", "us-stock"}
_PRICE_FAILURES: Dict[tuple[str, str, str, str], dict[str, float]] = {}
_PRICE_FAILURE_CACHE_KEY_PREFIX = "position_price_failures"
_POLYMARKET_SETTLEMENT_RECHECK_AFTER: Dict[tuple[str, str, str], float] = {}
_POLYMARKET_UPDOWN_RE = re.compile(r"^(btc|eth|sol|xrp)-updown-(5m|15m|1h|4h)-(\d+)$", re.IGNORECASE)
_profit_history_prune_lock = threading.Lock()
_POLYMARKET_SETTLEMENT_CURSOR = 0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_csv_set(name: str, default: str = "") -> set[str]:
    raw = os.getenv(name, default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _normalize_task_market(market: str | None) -> str:
    try:
        from routes_shared import normalize_market

        return normalize_market(market)
    except Exception:
        return (market or "").strip().lower()


def _price_failure_retry_after(key: tuple[str, str, str, str], now_ts: float) -> float:
    state = _PRICE_FAILURES.get(key)
    if not state:
        try:
            from cache import get_json

            cached = get_json(_price_failure_cache_key(key))
            if isinstance(cached, dict):
                state = {
                    "count": float(cached.get("count") or 0),
                    "retry_after": float(cached.get("retry_after") or 0),
                    "last_failed_at": float(cached.get("last_failed_at") or 0),
                }
                if state["retry_after"] > now_ts:
                    _PRICE_FAILURES[key] = state
        except Exception:
            state = None
    if not state:
        return 0.0
    return max(0.0, float(state.get("retry_after", 0.0)) - now_ts)


def _price_failure_cache_key(key: tuple[str, str, str, str]) -> str:
    payload = json.dumps(list(key), separators=(",", ":"), sort_keys=False)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"{_PRICE_FAILURE_CACHE_KEY_PREFIX}:{digest}"


def _record_price_failure(key: tuple[str, str, str, str], now_ts: float) -> int:
    base_s = _env_int("POSITION_PRICE_FAILURE_COOLDOWN_SECONDS", 3600, minimum=60)
    max_s = _env_int("POSITION_PRICE_FAILURE_MAX_COOLDOWN_SECONDS", 21600, minimum=base_s)
    previous = _PRICE_FAILURES.get(key, {})
    count = int(previous.get("count", 0)) + 1
    cooldown_s = min(max_s, base_s * (2 ** min(count - 1, 6)))
    state = {
        "count": float(count),
        "retry_after": now_ts + cooldown_s,
        "last_failed_at": now_ts,
    }
    _PRICE_FAILURES[key] = state
    try:
        from cache import set_json

        set_json(_price_failure_cache_key(key), state, ttl_seconds=cooldown_s + 300)
    except Exception:
        pass
    return cooldown_s


def _clear_price_failure(key: tuple[str, str, str, str]) -> None:
    _PRICE_FAILURES.pop(key, None)
    try:
        from cache import delete

        delete(_price_failure_cache_key(key))
    except Exception:
        pass


def _format_price_key(key: tuple[str, str, str, str]) -> str:
    symbol, market, token_id, outcome = key
    suffix = f", token={token_id}" if token_id else ""
    if outcome:
        suffix = f"{suffix}, outcome={outcome}" if suffix else f", outcome={outcome}"
    return f"{symbol} ({market}{suffix})"


def _polymarket_updown_expired_seconds(symbol: str | None, now_ts: float) -> Optional[float]:
    match = _POLYMARKET_UPDOWN_RE.match((symbol or "").strip())
    if not match:
        return None
    try:
        end_ts = int(match.group(3))
    except Exception:
        return None
    return now_ts - float(end_ts)


def _backfill_polymarket_position_metadata() -> None:
    """Best-effort backfill for legacy Polymarket positions missing token_id/outcome."""
    from database import get_db_connection
    from price_fetcher import _polymarket_resolve_reference

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, symbol, token_id, outcome
            FROM positions
            WHERE market = 'polymarket' AND (token_id IS NULL OR token_id = '')
        """)
        rows = cursor.fetchall()
        if not rows:
            conn.close()
            return

        updated = 0
        skipped = 0
        for row in rows:
            outcome = row["outcome"]
            if not outcome:
                skipped += 1
                continue
            contract = _polymarket_resolve_reference(row["symbol"], outcome=outcome)
            if not contract or not contract.get("token_id"):
                skipped += 1
                continue
            cursor.execute("""
                UPDATE positions
                SET token_id = ?, outcome = COALESCE(outcome, ?)
                WHERE id = ?
            """, (contract["token_id"], contract.get("outcome"), row["id"]))
            updated += 1

        if updated > 0:
            conn.commit()
            print(f"[Polymarket Backfill] Updated {updated} legacy positions; skipped={skipped}")
        else:
            conn.rollback()
    finally:
        conn.close()


def _update_trending_cache():
    """Update trending cache - calculates from positions table."""
    from cache import set_json
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get symbols ranked by holder count with current prices
    cursor.execute("""
        SELECT symbol, market, token_id, outcome, COUNT(DISTINCT agent_id) as holder_count
        FROM positions
        GROUP BY symbol, market, token_id, outcome
        ORDER BY holder_count DESC
        LIMIT 20
    """)
    rows = cursor.fetchall()

    updated_trending: list[dict[str, Any]] = []
    for row in rows:
        # Get current price from positions table
        cursor.execute("""
            SELECT current_price FROM positions
            WHERE symbol = ? AND market = ? AND COALESCE(token_id, '') = COALESCE(?, '')
            LIMIT 1
        """, (row["symbol"], row["market"], row["token_id"]))
        price_row = cursor.fetchone()

        updated_trending.append({
            "symbol": row["symbol"],
            "market": row["market"],
            "token_id": row["token_id"],
            "outcome": row["outcome"],
            "holder_count": row["holder_count"],
            "current_price": price_row["current_price"] if price_row else None
        })

    conn.close()
    trending_cache.clear()
    trending_cache.extend(updated_trending)
    refresh_interval = max(60, _env_int("POSITION_REFRESH_INTERVAL", 900, minimum=60) * 2)
    set_json(_TRENDING_CACHE_KEY, trending_cache, ttl_seconds=refresh_interval)


def _prune_profit_history() -> None:
    if not _profit_history_prune_lock.acquire(blocking=False):
        print("[Profit History] Prune already running; skipped")
        return
    try:
        _prune_profit_history_unlocked()
    finally:
        _profit_history_prune_lock.release()


def _prune_profit_history_unlocked() -> None:
    """Tier profit history into high-resolution, 15m, hourly, and daily retention."""
    from database import get_db_connection, using_postgres

    full_resolution_hours = _env_int("PROFIT_HISTORY_FULL_RESOLUTION_HOURS", 24, minimum=1)
    fifteen_min_window_days = _env_int(
        "PROFIT_HISTORY_15M_WINDOW_DAYS",
        _env_int("PROFIT_HISTORY_COMPACT_WINDOW_DAYS", 7, minimum=1),
        minimum=1,
    )
    hourly_window_days = _env_int("PROFIT_HISTORY_HOURLY_WINDOW_DAYS", 30, minimum=fifteen_min_window_days)
    daily_window_days = _env_int("PROFIT_HISTORY_DAILY_WINDOW_DAYS", 365, minimum=hourly_window_days)
    bucket_minutes = _env_int("PROFIT_HISTORY_COMPACT_BUCKET_MINUTES", 15, minimum=1)

    if full_resolution_hours >= fifteen_min_window_days * 24:
        full_resolution_hours = max(1, fifteen_min_window_days * 24 - 1)

    now = datetime.now(timezone.utc)
    daily_cutoff = (now - timedelta(days=daily_window_days)).isoformat().replace("+00:00", "Z")
    hourly_cutoff = (now - timedelta(days=hourly_window_days)).isoformat().replace("+00:00", "Z")
    fifteen_min_cutoff = (now - timedelta(days=fifteen_min_window_days)).isoformat().replace("+00:00", "Z")
    full_resolution_cutoff = (now - timedelta(hours=full_resolution_hours)).isoformat().replace("+00:00", "Z")

    deleted_old = 0
    deleted_15m = 0
    deleted_hourly = 0
    deleted_daily = 0

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("DELETE FROM profit_history WHERE recorded_at < ?", (daily_cutoff,))
        deleted_old = cursor.rowcount if cursor.rowcount is not None else 0
        conn.commit()

        if using_postgres():
            if full_resolution_cutoff > fifteen_min_cutoff:
                cursor.execute("""
                    WITH ranked AS (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY
                                    agent_id,
                                    date_trunc('hour', recorded_at::timestamptz)
                                    + floor(extract(minute FROM recorded_at::timestamptz) / ?) * (? || ' minutes')::interval
                                ORDER BY recorded_at DESC, id DESC
                            ) AS rn
                        FROM profit_history
                        WHERE recorded_at >= ? AND recorded_at < ?
                    )
                    DELETE FROM profit_history ph
                    USING ranked
                    WHERE ph.id = ranked.id AND ranked.rn > 1
                """, (bucket_minutes, bucket_minutes, fifteen_min_cutoff, full_resolution_cutoff))
                deleted_15m = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()

            if fifteen_min_cutoff > hourly_cutoff:
                cursor.execute("""
                    WITH ranked AS (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY agent_id, date_trunc('hour', recorded_at::timestamptz)
                                ORDER BY recorded_at DESC, id DESC
                            ) AS rn
                        FROM profit_history
                        WHERE recorded_at >= ? AND recorded_at < ?
                    )
                    DELETE FROM profit_history ph
                    USING ranked
                    WHERE ph.id = ranked.id AND ranked.rn > 1
                """, (hourly_cutoff, fifteen_min_cutoff))
                deleted_hourly = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()

            if hourly_cutoff > daily_cutoff:
                cursor.execute("""
                    WITH ranked AS (
                        SELECT
                            id,
                            ROW_NUMBER() OVER (
                                PARTITION BY agent_id, date_trunc('day', recorded_at::timestamptz)
                                ORDER BY recorded_at DESC, id DESC
                            ) AS rn
                        FROM profit_history
                        WHERE recorded_at >= ? AND recorded_at < ?
                    )
                    DELETE FROM profit_history ph
                    USING ranked
                    WHERE ph.id = ranked.id AND ranked.rn > 1
                """, (daily_cutoff, hourly_cutoff))
                deleted_daily = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()
        else:
            if full_resolution_cutoff > fifteen_min_cutoff:
                cursor.execute("""
                    DELETE FROM profit_history
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY
                                        agent_id,
                                        strftime('%Y-%m-%dT%H', recorded_at),
                                        CAST(strftime('%M', recorded_at) AS INTEGER) / ?
                                    ORDER BY recorded_at DESC, id DESC
                                ) AS rn
                            FROM profit_history
                            WHERE recorded_at >= ? AND recorded_at < ?
                        ) ranked
                        WHERE rn > 1
                    )
                """, (bucket_minutes, fifteen_min_cutoff, full_resolution_cutoff))
                deleted_15m = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()

            if fifteen_min_cutoff > hourly_cutoff:
                cursor.execute("""
                    DELETE FROM profit_history
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY agent_id, strftime('%Y-%m-%dT%H', recorded_at)
                                    ORDER BY recorded_at DESC, id DESC
                                ) AS rn
                            FROM profit_history
                            WHERE recorded_at >= ? AND recorded_at < ?
                        ) ranked
                        WHERE rn > 1
                    )
                """, (hourly_cutoff, fifteen_min_cutoff))
                deleted_hourly = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()

            if hourly_cutoff > daily_cutoff:
                cursor.execute("""
                    DELETE FROM profit_history
                    WHERE id IN (
                        SELECT id
                        FROM (
                            SELECT
                                id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY agent_id, strftime('%Y-%m-%d', recorded_at)
                                    ORDER BY recorded_at DESC, id DESC
                                ) AS rn
                            FROM profit_history
                            WHERE recorded_at >= ? AND recorded_at < ?
                        ) ranked
                        WHERE rn > 1
                    )
                """, (daily_cutoff, hourly_cutoff))
                deleted_daily = cursor.rowcount if cursor.rowcount is not None else 0
                conn.commit()

        total_deleted = deleted_old + deleted_15m + deleted_hourly + deleted_daily
        if total_deleted:
            print(
                "[Profit History] Pruned history: "
                f"deleted_old={deleted_old} "
                f"compacted_15m={deleted_15m} "
                f"compacted_hourly={deleted_hourly} "
                f"compacted_daily={deleted_daily}"
            )
            if not using_postgres() and _env_bool("PROFIT_HISTORY_VACUUM_AFTER_PRUNE", True):
                min_deleted = _env_int("PROFIT_HISTORY_VACUUM_MIN_DELETED_ROWS", 50000, minimum=1)
                if total_deleted >= min_deleted:
                    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    cursor.execute("VACUUM")
                    print("[Profit History] SQLite VACUUM completed after prune")
    finally:
        conn.close()


def _maybe_prune_profit_history() -> None:
    global _last_profit_history_prune_at

    prune_interval = _env_int("PROFIT_HISTORY_PRUNE_INTERVAL_SECONDS", 3600)
    if prune_interval <= 0:
        return

    now = time.time()
    if now - _last_profit_history_prune_at < prune_interval:
        return

    _last_profit_history_prune_at = now
    _prune_profit_history()


def _profit_history_prune_done(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[Profit History] Async prune failed: {exc}")


def _maybe_schedule_profit_history_prune() -> None:
    global _last_profit_history_prune_at, _profit_history_prune_task

    prune_interval = _env_int("PROFIT_HISTORY_PRUNE_INTERVAL_SECONDS", 3600)
    if prune_interval <= 0:
        return

    if _profit_history_prune_task is not None and not _profit_history_prune_task.done():
        return

    now = time.time()
    if now - _last_profit_history_prune_at < prune_interval:
        return

    _last_profit_history_prune_at = now
    _profit_history_prune_task = asyncio.create_task(
        asyncio.to_thread(_prune_profit_history),
        name="ai-trader:profit_history_prune",
    )
    _profit_history_prune_task.add_done_callback(_profit_history_prune_done)
    print("[Profit History] Scheduled async prune")


async def update_position_prices():
    """Background task to update position prices every 2 minutes."""
    from database import get_db_connection
    from price_fetcher import get_price_from_market, price_fetch_logging

    # Get max parallel requests from environment variable
    max_parallel = _env_int("MAX_PARALLEL_PRICE_FETCH", 2, minimum=1)
    verbose_fetch = _env_bool("POSITION_PRICE_VERBOSE_FETCH_LOGS", False)
    refresh_priced_markets = _env_csv_set("POSITION_PRICE_REFRESH_PRICED_MARKETS", "crypto,us-stock")

    # Wait a bit on startup before first update
    await asyncio.sleep(_env_int("POSITION_PRICE_STARTUP_DELAY_SECONDS", 15, minimum=0))

    while True:
        try:
            _backfill_polymarket_position_metadata()
            conn = get_db_connection()
            try:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        symbol,
                        market,
                        token_id,
                        outcome,
                        COUNT(*) AS position_count,
                        SUM(CASE WHEN current_price IS NULL THEN 1 ELSE 0 END) AS null_price_count
                    FROM positions
                    GROUP BY symbol, market, token_id, outcome
                """)
                unique_positions = cursor.fetchall()
            finally:
                conn.close()

            now_ts = time.time()
            candidates = []
            skipped_unsupported = 0
            skipped_cooldown = 0
            skipped_expired_polymarket = 0
            skipped_priced = 0
            skipped_cooldown_by_market: Counter[str] = Counter()
            skipped_priced_by_market: Counter[str] = Counter()
            skipped_unsupported_by_market: Counter[str] = Counter()

            for row in unique_positions:
                symbol = row["symbol"]
                original_market = row["market"]
                market = _normalize_task_market(original_market)
                token_id = row["token_id"] or ""
                outcome = row["outcome"] or ""
                null_price_count = int(row["null_price_count"] or 0)

                if market not in _SUPPORTED_PRICE_MARKETS:
                    skipped_unsupported += 1
                    skipped_unsupported_by_market[str(original_market or "-")] += 1
                    continue

                if null_price_count <= 0 and market not in refresh_priced_markets:
                    skipped_priced += 1
                    skipped_priced_by_market[market] += 1
                    continue

                if market == "polymarket":
                    expired_s = _polymarket_updown_expired_seconds(symbol, now_ts)
                    if expired_s is not None and expired_s > _env_int("POLYMARKET_UPDOWN_PRICE_GRACE_SECONDS", 900, minimum=60):
                        skipped_expired_polymarket += 1
                        continue

                key = (str(symbol or ""), market, str(token_id), str(outcome))
                remaining = _price_failure_retry_after(key, now_ts)
                if remaining > 0:
                    skipped_cooldown += 1
                    skipped_cooldown_by_market[market] += 1
                    continue

                candidates.append({
                    "symbol": symbol,
                    "db_market": original_market,
                    "market": market,
                    "token_id": token_id,
                    "outcome": outcome,
                    "key": key,
                })

            print(
                "[Price Update] candidates="
                f"{len(candidates)}/{len(unique_positions)} "
                f"skipped_unsupported={skipped_unsupported} "
                f"skipped_priced={skipped_priced} "
                f"skipped_expired_polymarket={skipped_expired_polymarket} "
                f"skipped_cooldown={skipped_cooldown}"
            )
            if skipped_unsupported_by_market:
                print(f"[Price Update] Unsupported markets skipped: {dict(skipped_unsupported_by_market.most_common())}")
            if skipped_priced_by_market:
                print(f"[Price Update] Already-priced markets skipped: {dict(skipped_priced_by_market.most_common())}")
            if skipped_cooldown_by_market:
                print(f"[Price Update] Failure cooldown skips by market: {dict(skipped_cooldown_by_market.most_common())}")

            # Semaphore to control concurrency
            semaphore = asyncio.Semaphore(max_parallel)

            async def fetch_price(row: dict[str, Any]):
                symbol = row["symbol"]
                market = row["market"]
                db_market = row["db_market"]
                token_id = row["token_id"]
                outcome = row["outcome"]
                key = row["key"]

                async with semaphore:
                    # Run synchronous function in thread pool
                    # Use UTC time for consistent pricing timestamps
                    now = datetime.now(timezone.utc)
                    executed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                    with price_fetch_logging(verbose_fetch):
                        price = await asyncio.to_thread(
                            get_price_from_market, symbol, executed_at, market, token_id, outcome
                        )

                return {
                    "symbol": symbol,
                    "db_market": db_market,
                    "market": market,
                    "token_id": token_id,
                    "outcome": outcome,
                    "key": key,
                    "price": price,
                }

            # Fetch prices in parallel, then write them back in one short transaction.
            results = await asyncio.gather(*[fetch_price(row) for row in candidates])
            updates = [
                (item["price"], item["symbol"], item["db_market"], item["token_id"])
                for item in results
                if item["price"] is not None
            ]
            failed = [item for item in results if item["price"] is None]
            failures_by_market: Counter[str] = Counter(item["market"] for item in failed)
            success_by_market: Counter[str] = Counter(item["market"] for item in results if item["price"] is not None)

            cooldown_samples: list[str] = []
            for item in results:
                if item["price"] is not None:
                    _clear_price_failure(item["key"])
                    continue
                cooldown_s = _record_price_failure(item["key"], now_ts)
                if len(cooldown_samples) < 8:
                    cooldown_samples.append(f"{_format_price_key(item['key'])} retry_in={cooldown_s}s")

            if updates:
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    cursor.executemany("""
                        UPDATE positions
                        SET current_price = ?
                        WHERE symbol = ? AND market = ? AND COALESCE(token_id, '') = COALESCE(?, '')
                    """, updates)
                    conn.commit()
                finally:
                    conn.close()

            print(
                "[Price Update] summary "
                f"requested={len(results)} updated={len(updates)} failed={len(failed)} "
                f"success_by_market={dict(success_by_market.most_common())} "
                f"failed_by_market={dict(failures_by_market.most_common())}"
            )
            if cooldown_samples:
                print(f"[Price Update] Failure cooldown samples: {cooldown_samples}")

            # Update trending cache (no additional API call, uses same data)
            _update_trending_cache()

        except Exception as e:
            print(f"[Price Update Error] {e}")

        # Wait interval from environment variable (default: 2 minutes = 120 seconds)
        refresh_interval = _env_int("POSITION_REFRESH_INTERVAL", 120, minimum=60)
        print(f"[Price Update] Next update in {refresh_interval} seconds")
        await asyncio.sleep(refresh_interval)


async def auto_close_positions_loop():
    """Background task to auto-close positions when stop-loss or take-profit thresholds are hit.

    Runs after each price update cycle. Checks all open positions that have
    stop_loss_price or take_profit_price set, and closes them if the current
    price has crossed the threshold.

    For real-time markets (crypto, us-stock), fetches fresh prices on each
    check instead of relying on potentially stale DB values from the
    background price updater.
    """
    from database import get_db_connection
    from services import _update_position_from_signal
    from price_fetcher import get_price_from_market
    from routes_shared import normalize_market

    _realtime_markets = {"crypto", "us-stock"}

    await asyncio.sleep(_env_int("AUTO_CLOSE_STARTUP_DELAY_SECONDS", 30, minimum=0))

    while True:
        try:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT p.id, p.agent_id, p.symbol, p.market, p.token_id, p.outcome,
                           p.side, p.quantity, p.entry_price, p.current_price,
                           p.stop_loss_price, p.take_profit_price, p.opened_at,
                           a.name as agent_name
                    FROM positions p
                    JOIN agents a ON a.id = p.agent_id
                    WHERE p.stop_loss_price IS NOT NULL OR p.take_profit_price IS NOT NULL
                """)
                rows = cursor.fetchall()
            finally:
                conn.close()

            if not rows:
                await asyncio.sleep(_env_int("AUTO_CLOSE_CHECK_INTERVAL", 15, minimum=5))
                continue

            closed_count = 0
            for row in rows:
                symbol = row["symbol"]
                market = row["market"]
                normalized_market = normalize_market(market)

                # Fetch fresh price for real-time markets to avoid acting on
                # stale DB values between background refresh cycles.
                if normalized_market in _realtime_markets:
                    now_utc = datetime.now(timezone.utc)
                    executed_at_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    try:
                        fetched_price = await asyncio.to_thread(
                            get_price_from_market,
                            symbol,
                            executed_at_str,
                            market,
                            row["token_id"],
                            row["outcome"],
                        )
                        current_price = fetched_price if fetched_price is not None else row["current_price"]
                    except Exception as fetch_err:
                        print(f"[Auto-Close] Fresh price fetch failed for {symbol}: {fetch_err}, using DB price")
                        current_price = row["current_price"]
                else:
                    current_price = row["current_price"]

                if current_price is None:
                    continue

                side = row["side"]
                stop_loss = row["stop_loss_price"]
                take_profit = row["take_profit_price"]
                quantity = abs(row["quantity"])
                agent_name = row["agent_name"] or "unknown"

                should_close = False
                close_reason = ""

                if side == "long":
                    if stop_loss is not None and current_price <= stop_loss:
                        should_close = True
                        close_reason = f"stop-loss hit ({current_price:.4f} <= {stop_loss:.4f})"
                    elif take_profit is not None and current_price >= take_profit:
                        should_close = True
                        close_reason = f"take-profit hit ({current_price:.4f} >= {take_profit:.4f})"
                elif side == "short":
                    if stop_loss is not None and current_price >= stop_loss:
                        should_close = True
                        close_reason = f"stop-loss hit ({current_price:.4f} >= {stop_loss:.4f})"
                    elif take_profit is not None and current_price <= take_profit:
                        should_close = True
                        close_reason = f"take-profit hit ({current_price:.4f} <= {take_profit:.4f})"

                if not should_close:
                    continue

                now = datetime.now(timezone.utc)
                executed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                close_action = "sell" if side == "long" else "cover"

                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    _update_position_from_signal(
                        agent_id=row["agent_id"],
                        symbol=symbol,
                        market=market,
                        action=close_action,
                        quantity=quantity,
                        price=current_price,
                        executed_at=executed_at,
                        cursor=cursor,
                        token_id=row["token_id"],
                        outcome=row["outcome"],
                    )

                    trade_value = current_price * quantity
                    if side == "long":
                        cursor.execute(
                            "UPDATE agents SET cash = cash + ? WHERE id = ?",
                            (trade_value, row["agent_id"]),
                        )
                    else:
                        entry_price = row["entry_price"]
                        cover_credit = ((2 * entry_price) - current_price) * quantity
                        # Borrow fee for short positions
                        borrow_fee = 0.0
                        try:
                            from fees import compute_borrow_fee
                            opened_dt = datetime.fromisoformat(row["opened_at"].replace('Z', '+00:00'))
                            now_dt = datetime.now(timezone.utc)
                            days_held = max(0.0, (now_dt - opened_dt).total_seconds() / 86400.0)
                            position_value = abs(row["quantity"]) * entry_price
                            borrow_fee = compute_borrow_fee(symbol, position_value, days_held)
                        except Exception:
                            pass
                        cursor.execute(
                            "UPDATE agents SET cash = cash + ? WHERE id = ?",
                            (cover_credit - borrow_fee, row["agent_id"]),
                        )
                        if borrow_fee > 0:
                            print(f"[Auto-Close] Borrow fee {symbol}: ${borrow_fee:.4f}")

                    try:
                        import uuid
                        signal_id = str(uuid.uuid4())
                        cursor.execute(
                            """
                            INSERT INTO signals
                                (signal_id, agent_id, message_type, market, signal_type,
                                 symbol, title, content, tags, timestamp, created_at)
                            VALUES (?, ?, 'trade', ?, 'auto_close', ?, ?, ?, 'auto-close,stop-loss,take-profit', ?, ?)
                            """,
                            (
                                signal_id,
                                row["agent_id"],
                                market,
                                symbol,
                                f"Auto-close: {symbol} — {close_reason}",
                                f"Position auto-closed by worker: {close_reason}. Agent: {agent_name}, side: {side}, qty: {quantity}, entry: {row['entry_price']}, exit: {current_price}",
                                int(now.timestamp()),
                                now.isoformat(),
                            ),
                        )
                    except Exception as sig_err:
                        print(f"[Auto-Close] Signal insert failed for {symbol}: {sig_err}")

                    try:
                        import json as _json
                        cursor.execute(
                            """
                            SELECT id FROM trading_decision_log
                            WHERE agent_id = ? AND symbol = ? AND action IN ('buy', 'short')
                              AND closing_log_id IS NULL
                            ORDER BY created_at DESC LIMIT 1
                            """,
                            (row["agent_id"], symbol),
                        )
                        prior_entry = cursor.fetchone()
                        cursor.execute(
                            """
                            INSERT INTO trading_decision_log
                            (agent_id, action, market, symbol, reason, metadata_json, closing_log_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["agent_id"],
                                close_action,
                                market,
                                symbol,
                                f"Auto-close: {close_reason}",
                                _json.dumps({
                                    "price": float(current_price),
                                    "quantity": float(quantity),
                                    "trade_value": float(current_price * quantity),
                                    "entry_price": float(row["entry_price"]) if row["entry_price"] else None,
                                    "stop_loss_price": float(stop_loss) if stop_loss else None,
                                    "take_profit_price": float(take_profit) if take_profit else None,
                                    "trigger": "auto_close",
                                }),
                                prior_entry["id"] if prior_entry else None,
                                now.isoformat(),
                            ),
                        )
                        exit_log_id = cursor.lastrowid
                        if prior_entry:
                            cursor.execute(
                                "UPDATE trading_decision_log SET closing_log_id = ? WHERE id = ?",
                                (exit_log_id, prior_entry["id"]),
                            )
                    except Exception as log_err:
                        print(f"[Auto-Close] Decision log insert failed for {symbol}: {log_err}")

                    conn.commit()
                    closed_count += 1
                    print(f"[Auto-Close] {agent_name} {symbol} {side} — {close_reason} — closed at {current_price}")
                finally:
                    conn.close()

            if closed_count:
                print(f"[Auto-Close] Processed {closed_count} position(s) this cycle")

        except Exception as e:
            print(f"[Auto-Close Error] {e}")

        check_interval = _env_int("AUTO_CLOSE_CHECK_INTERVAL", 15, minimum=5)
        await asyncio.sleep(check_interval)


async def refresh_market_news_snapshots_loop():
    """Background task to refresh market-news snapshots on a fixed interval."""
    from market_intel import refresh_market_news_snapshots

    refresh_interval = _env_int("MARKET_NEWS_REFRESH_INTERVAL", 3600, minimum=300)

    # Give the API a moment to start before hitting external providers.
    await asyncio.sleep(3)

    while True:
        try:
            result = await asyncio.to_thread(refresh_market_news_snapshots)
            print(
                "[Market Intel] Refreshed market news snapshots: "
                f"inserted={result.get('inserted_categories', 0)} "
                f"errors={len(result.get('errors', {}))}"
            )
            for category, error in (result.get("errors") or {}).items():
                print(f"[Market Intel] {category} refresh failed: {error}")
        except Exception as e:
            print(f"[Market Intel Error] {e}")

        print(f"[Market Intel] Next market news refresh in {refresh_interval} seconds")
        await asyncio.sleep(refresh_interval)


async def refresh_macro_signal_snapshots_loop():
    """Background task to refresh macro signal snapshots on a fixed interval."""
    from market_intel import refresh_macro_signal_snapshot

    refresh_interval = _env_int("MACRO_SIGNAL_REFRESH_INTERVAL", 3600, minimum=300)

    await asyncio.sleep(6)

    while True:
        try:
            result = await asyncio.to_thread(refresh_macro_signal_snapshot)
            print(
                "[Market Intel] Refreshed macro signal snapshot: "
                f"verdict={result.get('verdict')} "
                f"signals={result.get('total_count', 0)}"
            )
        except Exception as e:
            print(f"[Macro Signal Error] {e}")

        print(f"[Market Intel] Next macro signal refresh in {refresh_interval} seconds")
        await asyncio.sleep(refresh_interval)


async def refresh_etf_flow_snapshots_loop():
    """Background task to refresh ETF flow snapshots on a fixed interval."""
    from market_intel import refresh_etf_flow_snapshot

    refresh_interval = _env_int("ETF_FLOW_REFRESH_INTERVAL", 3600, minimum=300)

    await asyncio.sleep(9)

    while True:
        try:
            result = await asyncio.to_thread(refresh_etf_flow_snapshot)
            print(
                "[Market Intel] Refreshed ETF flow snapshot: "
                f"direction={result.get('direction')} "
                f"tracked={result.get('tracked_count', 0)}"
            )
        except Exception as e:
            print(f"[ETF Flow Error] {e}")

        print(f"[Market Intel] Next ETF flow refresh in {refresh_interval} seconds")
        await asyncio.sleep(refresh_interval)


async def refresh_stock_analysis_snapshots_loop():
    """Background task to refresh featured stock-analysis snapshots."""
    from market_intel import refresh_stock_analysis_snapshots

    refresh_interval = _env_int("STOCK_ANALYSIS_REFRESH_INTERVAL", 7200, minimum=600)

    await asyncio.sleep(12)

    while True:
        try:
            result = await asyncio.to_thread(refresh_stock_analysis_snapshots)
            print(
                "[Market Intel] Refreshed stock analysis snapshots: "
                f"inserted={result.get('inserted_symbols', 0)} "
                f"errors={len(result.get('errors', {}))}"
            )
        except Exception as e:
            print(f"[Stock Analysis Error] {e}")

        print(f"[Market Intel] Next stock analysis refresh in {refresh_interval} seconds")
        await asyncio.sleep(refresh_interval)


async def periodic_token_cleanup():
    """Periodically clean up expired tokens."""
    from utils import cleanup_expired_tokens

    while True:
        try:
            await asyncio.sleep(3600)  # Every hour
            deleted = cleanup_expired_tokens()
            if deleted > 0:
                print(f"[Token Cleanup] Cleaned up {deleted} expired tokens")
        except Exception as e:
            print(f"[Token Cleanup Error] {e}")


def _record_profit_history_once() -> int:
    from database import get_db_connection, using_postgres

    started_at = time.monotonic()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    initial_capital = 100000.0
    max_abs_profit = 1e12
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if using_postgres():
            cursor.execute("""
                WITH agent_values AS (
                    SELECT
                        a.id AS agent_id,
                        COALESCE(a.cash, 0) AS cash,
                        COALESCE(a.deposited, 0) AS deposited,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN p.current_price IS NULL THEN p.entry_price * ABS(p.quantity)
                                    WHEN p.side = 'long' THEN p.current_price * ABS(p.quantity)
                                    ELSE (2 * p.entry_price - p.current_price) * ABS(p.quantity)
                                END
                            ),
                            0
                        ) AS position_value
                    FROM agents a
                    LEFT JOIN positions p ON p.agent_id = a.id
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM agent_leaderboard_exclusions ale
                        WHERE ale.agent_id = a.id
                          AND COALESCE(ale.active, 1) = 1
                    )
                    GROUP BY a.id, a.cash, a.deposited
                ),
                calculated AS (
                    SELECT
                        agent_id,
                        cash,
                        position_value,
                        cash + position_value AS total_value,
                        cash + position_value - (? + deposited) AS raw_profit
                    FROM agent_values
                ),
                inserted AS (
                    INSERT INTO profit_history (agent_id, total_value, cash, position_value, profit, recorded_at)
                    SELECT
                        agent_id,
                        total_value,
                        cash,
                        position_value,
                        CASE
                            WHEN ABS(raw_profit) > ? THEN
                                CASE WHEN raw_profit > 0 THEN ? ELSE -? END
                            ELSE raw_profit
                        END AS profit,
                        ?
                    FROM calculated
                    RETURNING 1
                )
                SELECT
                    (SELECT COUNT(*) FROM inserted) AS inserted_count,
                    (SELECT COUNT(*) FROM calculated WHERE ABS(raw_profit) > ?) AS clamped_count
            """, (initial_capital, max_abs_profit, max_abs_profit, max_abs_profit, now, max_abs_profit))
            row = cursor.fetchone()
            inserted_count = int(row["inserted_count"] or 0) if row else 0
            clamped_count = int(row["clamped_count"] or 0) if row else 0
        else:
            cursor.execute("""
                WITH agent_values AS (
                    SELECT
                        a.id AS agent_id,
                        COALESCE(a.cash, 0) AS cash,
                        COALESCE(a.deposited, 0) AS deposited,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN p.current_price IS NULL THEN p.entry_price * ABS(p.quantity)
                                    WHEN p.side = 'long' THEN p.current_price * ABS(p.quantity)
                                    ELSE (2 * p.entry_price - p.current_price) * ABS(p.quantity)
                                END
                            ),
                            0
                        ) AS position_value
                    FROM agents a
                    LEFT JOIN positions p ON p.agent_id = a.id
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM agent_leaderboard_exclusions ale
                        WHERE ale.agent_id = a.id
                          AND COALESCE(ale.active, 1) = 1
                    )
                    GROUP BY a.id, a.cash, a.deposited
                ),
                calculated AS (
                    SELECT
                        agent_id,
                        cash,
                        position_value,
                        cash + position_value AS total_value,
                        cash + position_value - (? + deposited) AS raw_profit
                    FROM agent_values
                )
                INSERT INTO profit_history (agent_id, total_value, cash, position_value, profit, recorded_at)
                SELECT
                    agent_id,
                    total_value,
                    cash,
                    position_value,
                    CASE
                        WHEN ABS(raw_profit) > ? THEN
                            CASE WHEN raw_profit > 0 THEN ? ELSE -? END
                        ELSE raw_profit
                    END AS profit,
                    ?
                FROM calculated
            """, (initial_capital, max_abs_profit, max_abs_profit, max_abs_profit, now))
            inserted_count = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
            clamped_count = 0
            if inserted_count == 0:
                cursor.execute("""
                    SELECT COUNT(*) AS agent_count
                    FROM agents a
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM agent_leaderboard_exclusions ale
                        WHERE ale.agent_id = a.id
                          AND COALESCE(ale.active, 1) = 1
                    )
                """)
                row = cursor.fetchone()
                inserted_count = int(row["agent_count"] or 0) if row else 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    elapsed_s = time.monotonic() - started_at
    if clamped_count:
        print(f"[Profit History] Clamped absurd profit for {clamped_count} agents to ±{max_abs_profit}")
    print(f"[Profit History] Recorded profit for {inserted_count} agents in {elapsed_s:.2f}s")
    return inserted_count


async def record_profit_history():
    """Record profit history for all agents."""
    print("[Profit History] Task starting...")
    await asyncio.sleep(_env_int("PROFIT_HISTORY_STARTUP_DELAY_SECONDS", 90, minimum=0))

    while True:
        try:
            recorded_count = await asyncio.to_thread(_record_profit_history_once)
            if recorded_count:
                _maybe_schedule_profit_history_prune()
        except Exception as e:
            print(f"[Profit History Error] {e}")

        # Record at the same interval as position refresh (controlled by POSITION_REFRESH_INTERVAL)
        refresh_interval = _env_int("PROFIT_HISTORY_RECORD_INTERVAL", _env_int("POSITION_REFRESH_INTERVAL", 900, minimum=60), minimum=300)
        await asyncio.sleep(refresh_interval)


async def settle_polymarket_positions():
    """
    Background task to auto-settle resolved Polymarket positions.

    When a Polymarket market resolves, Gamma exposes `resolved` and `settlementPrice`.
    We treat each held outcome token as explicit spot-like inventory:
    - proceeds = quantity * settlementPrice
    - credit proceeds to agent cash
    - record an immutable settlement ledger entry
    - delete the position
    """
    from database import get_db_connection
    from price_fetcher import _polymarket_resolve
    global _POLYMARKET_SETTLEMENT_CURSOR

    # Wait a bit on startup before first settle pass
    await asyncio.sleep(_env_int("POLYMARKET_SETTLE_STARTUP_DELAY_SECONDS", 180, minimum=0))

    while True:
        try:
            interval_s = _env_int("POLYMARKET_SETTLE_INTERVAL", 300, minimum=60)
        except Exception:
            interval_s = 300

        try:
            _backfill_polymarket_position_metadata()
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT symbol, token_id, outcome, COUNT(*) AS position_count
                    FROM positions
                    WHERE market = 'polymarket' AND token_id IS NOT NULL AND token_id != ''
                    GROUP BY symbol, token_id, outcome
                """)
                contract_rows = cursor.fetchall()
                cursor.execute("SELECT COUNT(*) AS count FROM positions WHERE market = 'polymarket'")
                total_positions = int(cursor.fetchone()["count"] or 0)
            finally:
                conn.close()

            settled = 0
            skipped = 0
            cash_updates: dict[int, float] = {}
            settlement_rows: list[tuple[Any, ...]] = []
            delete_rows: list[tuple[int]] = []

            resolution_by_contract: dict[tuple[str, str, str], Optional[dict[str, Any]]] = {}
            due_contracts: list[tuple[str, str, str]] = []
            now_ts = time.time()
            max_contracts = _env_int("POLYMARKET_SETTLE_MAX_CONTRACTS_PER_RUN", 25, minimum=1)
            unresolved_recheck_s = _env_int("POLYMARKET_SETTLE_UNRESOLVED_RECHECK_SECONDS", 3600, minimum=interval_s)

            for row in contract_rows:
                token_id = row["token_id"]
                contract_key = (
                    str(row["symbol"] or ""),
                    str(token_id or ""),
                    str(row["outcome"] or ""),
                )
                if contract_key in resolution_by_contract:
                    continue
                retry_after = _POLYMARKET_SETTLEMENT_RECHECK_AFTER.get(contract_key, 0.0)
                if retry_after > now_ts:
                    resolution_by_contract[contract_key] = None
                    continue
                resolution_by_contract[contract_key] = None
                due_contracts.append(contract_key)

            due_contracts.sort()
            if len(due_contracts) > max_contracts:
                start = _POLYMARKET_SETTLEMENT_CURSOR % len(due_contracts)
                rotated = due_contracts[start:] + due_contracts[:start]
                unique_contracts = rotated[:max_contracts]
                _POLYMARKET_SETTLEMENT_CURSOR = (start + max_contracts) % len(due_contracts)
            else:
                unique_contracts = due_contracts
                _POLYMARKET_SETTLEMENT_CURSOR = 0

            for symbol, token_id, outcome in unique_contracts:
                resolution = await asyncio.to_thread(_polymarket_resolve, symbol, token_id=token_id, outcome=outcome)
                resolution_by_contract[(symbol, token_id, outcome)] = resolution
                if not resolution or not resolution.get("resolved"):
                    _POLYMARKET_SETTLEMENT_RECHECK_AFTER[(symbol, token_id, outcome)] = now_ts + unresolved_recheck_s
                else:
                    _POLYMARKET_SETTLEMENT_RECHECK_AFTER.pop((symbol, token_id, outcome), None)

            skipped_recheck = 0
            resolved_contracts = {
                contract_key: resolution
                for contract_key, resolution in resolution_by_contract.items()
                if resolution and resolution.get("resolved") and resolution.get("settlementPrice") is not None
            }
            for row in contract_rows:
                contract_key = (str(row["symbol"] or ""), str(row["token_id"] or ""), str(row["outcome"] or ""))
                position_count = int(row["position_count"] or 0)
                if contract_key in resolved_contracts:
                    continue
                skipped += position_count
                if _POLYMARKET_SETTLEMENT_RECHECK_AFTER.get(contract_key, 0.0) > now_ts:
                    skipped_recheck += position_count

            if resolved_contracts:
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    for (symbol, token_id, outcome), resolution in resolved_contracts.items():
                        cursor.execute(
                            """
                            SELECT id, agent_id, symbol, token_id, outcome, quantity, entry_price
                            FROM positions
                            WHERE market = 'polymarket' AND symbol = ? AND token_id = ? AND COALESCE(outcome, '') = COALESCE(?, '')
                            """,
                            (symbol, token_id, outcome),
                        )
                        for row in cursor.fetchall():
                            pos_id = row["id"]
                            agent_id = row["agent_id"]
                            qty = row["quantity"] or 0
                            settlement_price = resolution.get("settlementPrice")
                            proceeds = float(f"{(abs(qty) * float(settlement_price)):.6f}")
                            cash_updates[agent_id] = float(f"{cash_updates.get(agent_id, 0.0) + proceeds:.6f}")
                            settlement_rows.append((
                                pos_id,
                                agent_id,
                                row["symbol"],
                                row["token_id"],
                                row["outcome"],
                                qty,
                                row["entry_price"],
                                settlement_price,
                                proceeds,
                                resolution.get("market_slug"),
                                resolution.get("resolved_outcome"),
                                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                json.dumps(resolution),
                            ))
                            delete_rows.append((pos_id,))
                            settled += 1
                finally:
                    conn.close()

            if settlement_rows:
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    cursor.executemany(
                        "UPDATE agents SET cash = cash + ? WHERE id = ?",
                        [(proceeds, agent_id) for agent_id, proceeds in cash_updates.items()],
                    )
                    cursor.executemany("""
                        INSERT INTO polymarket_settlements
                        (position_id, agent_id, symbol, token_id, outcome, quantity, entry_price, settlement_price, proceeds, market_slug, resolved_outcome, resolved_at, source_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, settlement_rows)
                    cursor.executemany("DELETE FROM positions WHERE id = ?", delete_rows)
                    conn.commit()
                finally:
                    conn.close()

            print(
                "[Polymarket Settler] "
                f"positions={total_positions} unique_contracts={len(contract_rows)} contracts_due={len(due_contracts)} "
                f"contracts_checked={len(unique_contracts)} "
                f"settled={settled} skipped={skipped} recheck_cooldown={skipped_recheck}"
            )

        except Exception as e:
            print(f"[Polymarket Settler Error] {e}")

        await asyncio.sleep(interval_s)


async def settle_challenges_loop():
    """Background task to settle active challenges after their end time."""
    from challenges import settle_due_challenges

    await asyncio.sleep(15)

    while True:
        interval_s = _env_int("CHALLENGE_SETTLE_INTERVAL", 120, minimum=30)
        try:
            settled = await asyncio.to_thread(settle_due_challenges)
            if settled:
                print(f"[Challenge Settler] settled={len(settled)}")
        except Exception as e:
            print(f"[Challenge Settler Error] {e}")

        await asyncio.sleep(interval_s)


async def form_team_missions_loop():
    """Background task to form teams for active missions with enough participants."""
    from team_missions import form_due_team_missions

    await asyncio.sleep(18)

    while True:
        interval_s = _env_int("TEAM_MISSION_FORM_INTERVAL", 180, minimum=30)
        try:
            formed = await asyncio.to_thread(form_due_team_missions)
            if formed:
                print(f"[Team Mission Former] formed_missions={len(formed)}")
        except Exception as e:
            print(f"[Team Mission Former Error] {e}")

        await asyncio.sleep(interval_s)


async def score_team_contributions_loop():
    """Background task to score new team messages/submissions into contribution records."""
    from team_missions import score_team_contributions

    await asyncio.sleep(22)

    while True:
        interval_s = _env_int("TEAM_CONTRIBUTION_SCORE_INTERVAL", 180, minimum=30)
        try:
            result = await asyncio.to_thread(score_team_contributions)
            if result.get("inserted"):
                print(f"[Team Contribution Scorer] inserted={result['inserted']}")
        except Exception as e:
            print(f"[Team Contribution Scorer Error] {e}")

        await asyncio.sleep(interval_s)


async def settle_team_missions_loop():
    """Background task to settle team missions after their submission deadline."""
    from team_missions import settle_due_team_missions

    await asyncio.sleep(26)

    while True:
        interval_s = _env_int("TEAM_MISSION_SETTLE_INTERVAL", 180, minimum=30)
        try:
            settled = await asyncio.to_thread(settle_due_team_missions)
            if settled:
                print(f"[Team Mission Settler] settled={len(settled)}")
        except Exception as e:
            print(f"[Team Mission Settler Error] {e}")

        await asyncio.sleep(interval_s)


async def score_signal_quality_loop():
    """Background task to extract structured predictions and score signal quality."""
    from signal_quality import score_unscored_signals

    await asyncio.sleep(30)

    while True:
        interval_s = _env_int("SIGNAL_QUALITY_SCORE_INTERVAL", 240, minimum=30)
        try:
            result = await asyncio.to_thread(score_unscored_signals)
            if result.get("inserted"):
                print(f"[Signal Quality Scorer] inserted={result['inserted']}")
        except Exception as e:
            print(f"[Signal Quality Scorer Error] {e}")

        await asyncio.sleep(interval_s)


async def refresh_agent_metric_snapshots_loop():
    """Background task to snapshot agent metrics for experiment analysis."""
    from experiment_metrics import refresh_agent_metric_snapshots

    await asyncio.sleep(34)

    while True:
        interval_s = _env_int("AGENT_METRIC_SNAPSHOT_INTERVAL", 900, minimum=60)
        window_days = _env_int("AGENT_METRIC_SNAPSHOT_WINDOW_DAYS", 7, minimum=1)
        try:
            result = await asyncio.to_thread(refresh_agent_metric_snapshots, window_days)
            print(f"[Agent Metric Snapshots] inserted={result.get('inserted', 0)} window={result.get('window_key')}")
        except Exception as e:
            print(f"[Agent Metric Snapshots Error] {e}")

        await asyncio.sleep(interval_s)


async def build_network_edges_loop():
    """Background task to rebuild agent interaction network edges."""
    from experiment_metrics import build_network_edges

    await asyncio.sleep(38)

    while True:
        interval_s = _env_int("NETWORK_EDGES_BUILD_INTERVAL", 900, minimum=60)
        try:
            result = await asyncio.to_thread(build_network_edges)
            print(f"[Network Edges] inserted={result.get('inserted', 0)}")
        except Exception as e:
            print(f"[Network Edges Error] {e}")

        await asyncio.sleep(interval_s)


async def limit_order_processor_loop():
    """Background task to check open limit orders against current prices and fill if conditions are met."""
    from database import get_db_connection
    from services import _update_position_from_signal
    from price_fetcher import get_price_from_market
    from routes_shared import normalize_market
    from fees import compute_fill_price, TRADE_FEE_RATE

    await asyncio.sleep(_env_int("LIMIT_ORDER_STARTUP_DELAY_SECONDS", 30, minimum=0))

    while True:
        try:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, agent_id, symbol, market, token_id, outcome, side, quantity,
                           limit_price, stop_loss_price, take_profit_price, time_in_force,
                           expires_at, content, created_at
                    FROM limit_orders
                    WHERE status = 'open'
                      AND (expires_at IS NULL OR expires_at > datetime('now'))
                """)
                orders = cursor.fetchall()
            finally:
                conn.close()

            if not orders:
                await asyncio.sleep(_env_int("LIMIT_ORDER_CHECK_INTERVAL", 15, minimum=5))
                continue

            filled_count = 0
            for order in orders:
                symbol = order["symbol"]
                market = order["market"]
                normalized_market = normalize_market(market)
                side = order["side"]
                limit_price = order["limit_price"]
                qty = order["quantity"]

                # Fetch current price
                now_utc = datetime.now(timezone.utc)
                executed_at_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                try:
                    current_price = await asyncio.to_thread(
                        get_price_from_market,
                        symbol,
                        executed_at_str,
                        market,
                        order["token_id"],
                        order["outcome"],
                    )
                except Exception:
                    current_price = None

                if current_price is None:
                    continue

                # Check if limit order would fill
                would_fill = False
                if side in ("buy",):
                    would_fill = current_price <= limit_price
                elif side in ("short",):
                    would_fill = current_price >= limit_price

                if not would_fill:
                    continue

                # Compute realistic fill price
                order_value = current_price * qty
                fill_price = compute_fill_price(
                    mid_price=current_price,
                    action=side,
                    market=market,
                    symbol=symbol,
                    order_value=order_value,
                )
                trade_value = fill_price * qty
                fee = trade_value * TRADE_FEE_RATE

                # Execute the fill
                conn = get_db_connection()
                try:
                    cursor = conn.cursor()
                    begin_write_transaction(cursor)

                    # Check cash for buy/short
                    if side in ("buy", "short"):
                        cursor.execute("SELECT cash FROM agents WHERE id = ?", (order["agent_id"],))
                        row = cursor.fetchone()
                        if row and row["cash"] < trade_value + fee:
                            print(f"[Limit Order] Insufficient cash for {symbol} order {order['id']}, skipping")
                            conn.rollback()
                            continue

                    _update_position_from_signal(
                        agent_id=order["agent_id"],
                        symbol=symbol,
                        market=market,
                        action=side,
                        quantity=qty,
                        price=fill_price,
                        executed_at=executed_at_str,
                        cursor=cursor,
                        token_id=order["token_id"],
                        outcome=order["outcome"],
                        stop_loss_price=order["stop_loss_price"],
                        take_profit_price=order["take_profit_price"],
                    )

                    # Update cash
                    if side in ("buy", "short"):
                        cursor.execute(
                            "UPDATE agents SET cash = cash - ? WHERE id = ?",
                            (trade_value + fee, order["agent_id"]),
                        )
                    elif side == "sell":
                        cursor.execute(
                            "UPDATE agents SET cash = cash + ? WHERE id = ?",
                            (trade_value - fee, order["agent_id"]),
                        )

                    # Mark order as filled
                    cursor.execute(
                        """UPDATE limit_orders
                           SET status = 'filled', filled_quantity = ?, filled_price = ?,
                               updated_at = datetime('now')
                           WHERE id = ?""",
                        (qty, fill_price, order["id"]),
                    )

                    # Log signal
                    import uuid as _uuid
                    signal_id = str(_uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO signals
                            (signal_id, agent_id, message_type, market, signal_type, symbol,
                             token_id, outcome, side, entry_price, quantity, content, timestamp, created_at, executed_at)
                        VALUES (?, ?, 'operation', ?, 'limit_fill', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (signal_id, order["agent_id"], market, symbol, order["token_id"],
                         order["outcome"], side, fill_price, qty, order["content"],
                         int(now_utc.timestamp()), now_utc.isoformat(), executed_at_str),
                    )

                    conn.commit()
                    filled_count += 1
                    print(f"[Limit Order] Filled {symbol} {side} {qty} @ {fill_price} (limit {limit_price})")
                except Exception as exc:
                    conn.rollback()
                    print(f"[Limit Order] Fill failed for {symbol} order {order['id']}: {exc}")
                finally:
                    conn.close()

            if filled_count:
                print(f"[Limit Order] Processed {filled_count} fill(s) this cycle")

        except Exception as e:
            print(f"[Limit Order Processor Error] {e}")

        await asyncio.sleep(_env_int("LIMIT_ORDER_CHECK_INTERVAL", 15, minimum=5))


BACKGROUND_TASK_REGISTRY = {
    "prices": update_position_prices,
    "auto_close": auto_close_positions_loop,
    "limit_orders": limit_order_processor_loop,
    "profit_history": record_profit_history,
    "polymarket_settlement": settle_polymarket_positions,
    "challenge_settlement": settle_challenges_loop,
    "team_mission_form": form_team_missions_loop,
    "team_contribution_score": score_team_contributions_loop,
    "team_mission_settlement": settle_team_missions_loop,
    "signal_quality_score": score_signal_quality_loop,
    "agent_metric_snapshots": refresh_agent_metric_snapshots_loop,
    "network_edges": build_network_edges_loop,
    "market_news": refresh_market_news_snapshots_loop,
    "macro_signals": refresh_macro_signal_snapshots_loop,
    "etf_flows": refresh_etf_flow_snapshots_loop,
    "stock_analysis": refresh_stock_analysis_snapshots_loop,
}


DEFAULT_BACKGROUND_TASKS = ",".join(BACKGROUND_TASK_REGISTRY.keys())


def background_tasks_enabled_for_api() -> bool:
    """Background tasks run by default in the API server process."""
    return _env_bool("AI_TRADER_API_BACKGROUND_TASKS", True)


_running_tasks: dict[str, asyncio.Task] = {}


def register_running_task(name: str, task: asyncio.Task) -> None:
    _running_tasks[name] = task


def get_background_task_status() -> dict:
    enabled = get_enabled_background_task_names()
    running: list[dict] = []
    for name, task in _running_tasks.items():
        running.append({
            "name": name,
            "done": task.done(),
            "cancelled": task.cancelled(),
        })
    return {
        "enabled_by_default": background_tasks_enabled_for_api(),
        "enabled_tasks": enabled,
        "running": running,
        "active_count": sum(1 for t in _running_tasks.values() if not t.done()),
        "total_count": len(_running_tasks),
    }


def get_enabled_background_task_names() -> list[str]:
    raw = os.getenv("AI_TRADER_BACKGROUND_TASKS", DEFAULT_BACKGROUND_TASKS)
    names = [item.strip() for item in raw.split(",") if item.strip()]
    return [name for name in names if name in BACKGROUND_TASK_REGISTRY]


def start_background_tasks(logger: Optional[Any] = None) -> list[asyncio.Task]:
    started: list[asyncio.Task] = []
    for name in get_enabled_background_task_names():
        task_func = BACKGROUND_TASK_REGISTRY[name]
        if logger:
            logger.info("Starting background task: %s", name)
        task = asyncio.create_task(task_func(), name=f"ai-trader:{name}")
        register_running_task(name, task)
        started.append(task)
    return started
