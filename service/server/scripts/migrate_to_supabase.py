#!/usr/bin/env python3
"""
Migrate data from local SQLite to Supabase (PostgreSQL).

Usage:
    1. Set DATABASE_URL in .env to your Supabase connection string
    2. Run: python3 service/server/scripts/migrate_to_supabase.py

The script:
  - Reads all rows from the local SQLite database
  - Ensures the schema exists in Supabase (via init_database)
  - Inserts rows in dependency order (parents before children)
  - Skips tables that already have data in the target (unless --force)
"""

from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

# Ensure service/server is on the path
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

from dotenv import load_dotenv

env_path = _SERVER_DIR.parent.parent / ".env"
load_dotenv(env_path)

from config import DATABASE_URL
from database import (
    get_db_connection,
    using_postgres,
    init_database,
)

# Tables in foreign-key dependency order (parents first)
TABLE_ORDER = [
    "agents",
    "agent_leaderboard_exclusions",
    "agent_messages",
    "agent_tasks",
    "listings",
    "orders",
    "arbitrators",
    "dispute_votes",
    "users",
    "points_transactions",
    "user_tokens",
    "rate_limits",
    "signal_sequence",
    "signals",
    "signal_replies",
    "subscriptions",
    "positions",
    "polymarket_settlements",
    "experiment_events",
    "experiments",
    "experiment_assignments",
    "agent_reward_ledger",
    "challenges",
    "challenge_participants",
    "challenge_teams",
    "challenge_team_members",
    "challenge_team_trades",
    "challenge_team_submissions",
    "challenge_submission_votes",
    "challenge_submissions",
    "challenge_trades",
    "challenge_results",
    "signal_predictions",
    "signal_quality_scores",
    "agent_metric_snapshots",
    "network_edges",
    "team_missions",
    "teams",
    "team_mission_participants",
    "team_members",
    "team_messages",
    "team_submissions",
    "team_contributions",
    "team_results",
    "market_news_snapshots",
    "macro_signal_snapshots",
    "etf_flow_snapshots",
    "stock_analysis_snapshots",
    "profit_history",
    "agent_configs",
    "agent_stats",
    "agent_trade_log",
    "agent_states",
    "agent_relationships",
    "agent_memories",
]

# Tables that are internal to SQLite and should NOT be migrated
SKIP_TABLES = {"sqlite_sequence", "sqlite_master"}


def get_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return [r[0] for r in cursor.fetchall()]


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cursor = conn.execute(f'PRAGMA table_info("{table}")')
    return [r[1] for r in cursor.fetchall()]


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    columns: list[str],
    force: bool,
) -> int:
    """Migrate a single table. Returns number of rows inserted."""
    pg_cursor = pg_conn.cursor()

    # Check if target already has data
    if not force:
        pg_cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        existing = pg_cursor.fetchone()
        existing_count = existing[0] if isinstance(existing, (tuple, list)) else existing["count"]
        if existing_count and int(existing_count) > 0:
            print(f"  [SKIP] {table}: target already has {existing_count} rows (use --force to overwrite)")
            return 0

    # Read all rows from SQLite
    col_list = ", ".join(f'"{c}"' for c in columns)
    cursor = sqlite_conn.execute(f'SELECT {col_list} FROM "{table}"')
    rows = cursor.fetchall()
    if not rows:
        print(f"  [EMPTY] {table}: 0 rows")
        return 0

    # Build INSERT statement using %s placeholders (raw psycopg)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'

    # Insert in batches of 500
    batch_size = 500
    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = [tuple(r) for r in rows[i : i + batch_size]]
        pg_cursor.executemany(insert_sql, batch)
        inserted += len(batch)

    print(f"  [OK]   {table}: {inserted} rows migrated")
    return inserted


def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Set it in .env to your Supabase connection string.")
        sys.exit(1)

    if not using_postgres():
        print("ERROR: DATABASE_URL is set but not detected as PostgreSQL. Check your connection string.")
        sys.exit(1)

    force = "--force" in sys.argv

    # Locate SQLite DB
    db_path = os.getenv("DB_PATH", str(_SERVER_DIR / "service" / "server" / "data" / "clawtrader.db"))
    if not os.path.exists(db_path):
        print(f"ERROR: SQLite DB not found at {db_path}")
        sys.exit(1)

    print(f"Source (SQLite): {db_path}")
    print(f"Target (Postgres/Supabase): {DATABASE_URL[:60]}...")
    print()

    # Initialize schema in Supabase
    print("Step 1: Ensuring schema exists in Supabase...")
    init_database()
    print("  Schema ready.")
    print()

    # Connect to both databases
    print("Step 2: Migrating data...")
    sqlite_conn = sqlite3.connect(db_path)
    sqlite_conn.row_factory = sqlite3.Row

    # Use raw psycopg connection to bypass DatabaseCursor wrapper
    import psycopg
    from psycopg.rows import dict_row
    pg_conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    pg_conn.autocommit = False

    # Disable FK constraints for bulk load (standard Postgres approach)
    setup_cursor = pg_conn.cursor()
    setup_cursor.execute("SET session_replication_role = 'replica'")
    pg_conn.commit()

    sqlite_tables = set(get_sqlite_tables(sqlite_conn))
    total_migrated = 0

    try:
        for table in TABLE_ORDER:
            if table not in sqlite_tables:
                continue
            columns = get_table_columns(sqlite_conn, table)
            if not columns:
                continue
            total_migrated += migrate_table(sqlite_conn, pg_conn, table, columns, force)

        # Re-enable FK constraints
        pg_conn.cursor().execute("SET session_replication_role = 'origin'")
        pg_conn.commit()
        print()
        print(f"Migration complete! {total_migrated} rows migrated to Supabase.")
    except Exception as exc:
        pg_conn.rollback()
        print(f"\nERROR during migration: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
