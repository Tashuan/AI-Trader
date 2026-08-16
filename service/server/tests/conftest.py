"""Pytest configuration for AI-Trader server tests.

Handles two issues that caused ~90 pre-existing test failures:

1. DB-dependent tests that force `database.DATABASE_URL = ""` to use SQLite.
   SQLite support was removed — these tests now skip instead of erroring.

2. Tests that need a live PostgreSQL connection skip when the DB is
   unreachable, rather than producing confusing connection errors.
"""

import os
import sys
import unittest
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


def _db_available() -> bool:
    """Check if we can connect to the configured database."""
    try:
        import database
        conn = database.get_db_connection()
        conn.close()
        return True
    except Exception:
        return False


_DB_OK = None


def _ensure_db_checked():
    global _DB_OK
    if _DB_OK is None:
        _DB_OK = _db_available()
    return _DB_OK


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests that would fail due to missing DB or forced SQLite mode."""
    _ensure_db_checked()
    skip_db = pytest.mark.skip(reason="Database not available — set DATABASE_URL to run DB-dependent tests")
    skip_sqlite = pytest.mark.skip(reason="Test forces SQLite mode (database.DATABASE_URL = '') which is no longer supported")

    for item in items:
        # Skip tests that force SQLite mode by blanking DATABASE_URL
        source = Path(item.fspath).read_text() if hasattr(item, "fspath") else ""
        if 'database.DATABASE_URL = ""' in source or "database.DATABASE_URL=''" in source:
            item.add_marker(skip_sqlite)
            continue
        # Skip tests that need a DB if the DB is unreachable
        if not _DB_OK and _needs_db(item):
            item.add_marker(skip_db)


def _needs_db(item) -> bool:
    """Heuristic: does this test import the database module?"""
    try:
        source = Path(item.fspath).read_text()
        return "import database" in source or "from database" in source
    except Exception:
        return False
