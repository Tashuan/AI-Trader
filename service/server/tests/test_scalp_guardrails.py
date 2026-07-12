"""Unit tests for scalp_guardrails.validate_entry."""

import os
import sqlite3
import unittest
from datetime import datetime, timezone


class ScalpGuardrailsTests(unittest.TestCase):
    def _make_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                cash REAL DEFAULT 100000.0,
                deposited REAL DEFAULT 0.0
            );
            CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'us-stock',
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL
            );
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                quantity REAL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE trading_risk_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL UNIQUE,
                day_key TEXT NOT NULL,
                starting_equity REAL NOT NULL,
                halted INTEGER NOT NULL DEFAULT 0,
                halt_reason TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE trading_decision_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                market TEXT,
                symbol TEXT,
                reason TEXT NOT NULL,
                metadata_json TEXT,
                closing_log_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        cur.execute(
            "INSERT INTO agents (name, cash, deposited) VALUES (?, 10000, 10000)",
            ("test_agent",),
        )
        conn.commit()
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def test_exit_actions_bypass_guardrails(self):
        from scalp_guardrails import validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        result = validate_entry(
            cur,
            agent_id=1,
            market="us-stock",
            symbol="AAPL",
            action="sell",
            trade_value=500,
            now=self._now(),
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "exit_or_non_entry")

    def test_valid_entry_passes(self):
        from scalp_guardrails import validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        result = validate_entry(
            cur,
            agent_id=1,
            market="us-stock",
            symbol="AAPL",
            action="buy",
            trade_value=500,
            now=self._now(),
        )
        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["equity"], 10000.0, places=2)

    def test_oversized_entry_rejected(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        with self.assertRaises(GuardrailViolation) as ctx:
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="AAPL",
                action="buy",
                trade_value=5000,  # 50% of 10k, well above 10% default
                now=self._now(),
            )
        self.assertIn("scalp allocation limit", str(ctx.exception))

    def test_max_positions_rejected(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        # Insert 15 positions (default max)
        for i in range(15):
            cur.execute(
                "INSERT INTO positions (agent_id, symbol, market, side, quantity, entry_price) VALUES (1, ?, 'us-stock', 'long', 10, 100)",
                (f"SYM{i}",),
            )
        conn.commit()
        with self.assertRaises(GuardrailViolation) as ctx:
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="NEW",
                action="buy",
                trade_value=100,
                now=self._now(),
            )
        self.assertIn("Maximum open positions", str(ctx.exception))

    def test_daily_loss_halt(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        # Simulate 4% daily loss (above 3% default)
        # starting_equity=10000, current equity=9600 => 4% loss
        day_key = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        cur.execute(
            "INSERT INTO trading_risk_state (agent_id, day_key, starting_equity, halted, updated_at) VALUES (1, ?, 10000, 0, ?)",
            (day_key, self._now()),
        )
        # Reduce cash to simulate loss
        cur.execute("UPDATE agents SET cash = 9600 WHERE id = 1")
        conn.commit()
        with self.assertRaises(GuardrailViolation) as ctx:
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="AAPL",
                action="buy",
                trade_value=100,
                now=self._now(),
            )
        self.assertIn("Daily loss", str(ctx.exception))

    def test_halted_state_blocks_new_entries(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        day_key = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        cur.execute(
            "INSERT INTO trading_risk_state (agent_id, day_key, starting_equity, halted, halt_reason, updated_at) VALUES (1, ?, 10000, 1, 'Manual halt', ?)",
            (day_key, self._now()),
        )
        conn.commit()
        with self.assertRaises(GuardrailViolation) as ctx:
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="AAPL",
                action="buy",
                trade_value=100,
                now=self._now(),
            )
        self.assertIn("Manual halt", str(ctx.exception))

    def test_cooldown_active(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        # Insert a recent signal (within cooldown window)
        now_iso = self._now()
        cur.execute(
            "INSERT INTO signals (agent_id, message_type, market, symbol, side, entry_price, quantity, created_at) VALUES (1, 'operation', 'us-stock', 'AAPL', 'buy', 150, 10, ?)",
            (now_iso,),
        )
        conn.commit()
        with self.assertRaises(GuardrailViolation) as ctx:
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="AAPL",
                action="buy",
                trade_value=100,
                now=now_iso,
            )
        self.assertIn("cooldown", str(ctx.exception).lower())

    def test_non_finite_trade_value_rejected(self):
        from scalp_guardrails import GuardrailViolation, validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        with self.assertRaises(GuardrailViolation):
            validate_entry(
                cur,
                agent_id=1,
                market="us-stock",
                symbol="AAPL",
                action="buy",
                trade_value=float("nan"),
                now=self._now(),
            )

    def test_cooldown_is_direction_aware(self):
        """A prior 'buy' should NOT block a 'short' on the same symbol."""
        from scalp_guardrails import validate_entry

        conn = self._make_db()
        cur = conn.cursor()
        now_iso = self._now()
        # Insert a recent BUY signal
        cur.execute(
            "INSERT INTO signals (agent_id, message_type, market, symbol, side, entry_price, quantity, created_at) VALUES (1, 'operation', 'us-stock', 'AAPL', 'buy', 150, 10, ?)",
            (now_iso,),
        )
        conn.commit()
        # A SHORT on the same symbol should pass (different direction)
        result = validate_entry(
            cur,
            agent_id=1,
            market="us-stock",
            symbol="AAPL",
            action="short",
            trade_value=100,
            now=now_iso,
        )
        self.assertTrue(result["allowed"])


if __name__ == "__main__":
    unittest.main()
