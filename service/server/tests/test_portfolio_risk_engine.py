"""Unit tests for portfolio_risk_engine.evaluate_portfolio_risk."""

import os
import sqlite3
import unittest
from datetime import datetime, timezone


class PortfolioRiskEngineTests(unittest.TestCase):
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
                current_price REAL,
                opened_at TEXT
            );
            CREATE TABLE portfolio_risk_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_key TEXT NOT NULL,
                starting_equity REAL NOT NULL,
                halted INTEGER NOT NULL DEFAULT 0,
                halt_reason TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        for i in range(5):
            cur.execute(
                "INSERT INTO agents (name, cash, deposited) VALUES (?, 2000, 0)",
                (f"agent_{i}",),
            )
        conn.commit()
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _insert_state(self, conn, starting_equity=10000.0, halted=0, halt_reason=None):
        conn.execute(
            "INSERT INTO portfolio_risk_state (day_key, starting_equity, halted, halt_reason, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._day_key(), starting_equity, halted, halt_reason, self._now()),
        )
        conn.commit()

    def _insert_position(self, conn, agent_id, symbol, side, qty, price):
        conn.execute(
            "INSERT INTO positions (agent_id, symbol, side, quantity, entry_price, current_price, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (agent_id, symbol, side, qty, price, price, self._now()),
            # noqa
        )
        conn.commit()

    def test_valid_entry_passes(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=1, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertTrue(result["approved"])
        self.assertEqual(result["trade_value"], 100.0)

    def test_crowding_rejection(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn)
        for agent_id in (1, 2, 3):
            self._insert_position(conn, agent_id, "BTC", "long", 0.1, 50000)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=4, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("Crowding", result["reason"])

    def test_symbol_concentration_rejection(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=10000.0)
        self._insert_position(conn, 1, "BTC", "long", 0.036, 50000)
        self._insert_position(conn, 2, "BTC", "short", 0.036, 50000)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=3, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("Symbol concentration", result["reason"])

    def test_gross_vs_net_exposure(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=10000.0)
        self._insert_position(conn, 1, "BTC", "long", 0.02, 50000)
        self._insert_position(conn, 2, "BTC", "short", 0.02, 50000)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=3, market="crypto", symbol="BTC",
            side="buy", trade_value=1600.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("Symbol concentration", result["reason"])
        self.assertAlmostEqual(
            result["checks"]["symbol_concentration"]["existing_gross"],
            2000.0, places=1,
        )

    def test_sector_concentration_rejection(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=10000.0)
        self._insert_position(conn, 1, "BTC", "long", 0.052, 50000)
        self._insert_position(conn, 2, "ETH", "long", 2.5, 1000)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=3, market="crypto", symbol="SOL",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("Sector concentration", result["reason"])

    def test_unknown_sector_rejection(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=10000.0)
        # $1001 existing + $50 new = $1051 > 10% of $10000
        self._insert_position(conn, 1, "XYZ", "long", 1001, 1.0)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=2, market="us-stock", symbol="XYZ",
            side="buy", trade_value=50.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("Sector concentration", result["reason"])
        self.assertIn("unknown", result["reason"].lower())

    def test_portfolio_daily_loss_halt(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=10000.0)
        conn.execute("UPDATE agents SET cash = 1880 WHERE id IN (1,2,3,4,5)")
        conn.commit()
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=1, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("daily loss", result["reason"].lower())
        cur.execute(
            "SELECT halted, halt_reason FROM portfolio_risk_state WHERE day_key = ?",
            (self._day_key(),),
        )
        row = cur.fetchone()
        self.assertEqual(int(row["halted"]), 1)

    def test_exits_bypass(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn)
        cur = conn.cursor()
        for action in ("sell", "cover"):
            result = evaluate_portfolio_risk(
                cur, agent_id=1, market="crypto", symbol="BTC",
                side=action, trade_value=100.0, now=self._now(),
            )
            self.assertTrue(result["approved"])

    def test_disabled_engine_passes(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        old = os.environ.get("PORTFOLIO_RISK_ENABLED")
        os.environ["PORTFOLIO_RISK_ENABLED"] = "0"
        try:
            conn = self._make_db()
            cur = conn.cursor()
            result = evaluate_portfolio_risk(
                cur, agent_id=1, market="crypto", symbol="BTC",
                side="buy", trade_value=100.0, now=self._now(),
            )
            self.assertTrue(result["approved"])
            self.assertTrue(result["checks"].get("disabled"))
        finally:
            if old is None:
                os.environ.pop("PORTFOLIO_RISK_ENABLED", None)
            else:
                os.environ["PORTFOLIO_RISK_ENABLED"] = old

    def test_env_var_overrides(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        old_crowding = os.environ.get("PORTFOLIO_MAX_CROWDING")
        os.environ["PORTFOLIO_MAX_CROWDING"] = "10"
        try:
            conn = self._make_db()
            self._insert_state(conn)
            for agent_id in (1, 2, 3):
                self._insert_position(conn, agent_id, "BTC", "long", 0.001, 50000)
            cur = conn.cursor()
            result = evaluate_portfolio_risk(
                cur, agent_id=4, market="crypto", symbol="BTC",
                side="buy", trade_value=10.0, now=self._now(),
            )
            self.assertTrue(result["approved"])
        finally:
            if old_crowding is None:
                os.environ.pop("PORTFOLIO_MAX_CROWDING", None)
            else:
                os.environ["PORTFOLIO_MAX_CROWDING"] = old_crowding

    def test_fail_closed_on_error(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        class BoomCursor:
            def execute(self, *a, **k):
                raise RuntimeError("DB exploded")
            def fetchone(self):
                raise RuntimeError("DB exploded")

        result = evaluate_portfolio_risk(
            BoomCursor(), agent_id=1, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertFalse(result["approved"])
        self.assertIn("System Error", result["reason"])

    def test_daily_rollover(self):
        from portfolio_risk_engine import _ensure_daily_state

        conn = self._make_db()
        cur = conn.cursor()
        state = _ensure_daily_state(cur, self._now())
        self.assertEqual(state["halted"], 0)
        self.assertIsNotNone(state["starting_equity"])
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM portfolio_risk_state WHERE day_key = ?",
            (self._day_key(),),
        )
        self.assertEqual(int(cur.fetchone()["cnt"]), 1)

    def test_dynamic_capital_base(self):
        from portfolio_risk_engine import evaluate_portfolio_risk

        conn = self._make_db()
        self._insert_state(conn, starting_equity=20000.0)
        # Increase cash to match starting_equity so daily loss check doesn't fire
        conn.execute("UPDATE agents SET cash = 4000 WHERE id IN (1,2,3,4,5)")
        conn.commit()
        self._insert_position(conn, 1, "BTC", "long", 0.12, 50000)
        cur = conn.cursor()
        result = evaluate_portfolio_risk(
            cur, agent_id=2, market="crypto", symbol="BTC",
            side="buy", trade_value=100.0, now=self._now(),
        )
        self.assertTrue(result["approved"], f"Should pass with dynamic capital: {result}")

    def test_sector_mapping_hot_reload(self):
        import portfolio_risk_engine as pre

        pre._SECTOR_CACHE = None
        pre._SECTOR_MTIME = 0.0
        m1 = pre._load_sector_map()
        self.assertEqual(m1.get("BTC"), "crypto")
        pre._SECTOR_CACHE = None
        pre._SECTOR_MTIME = 0.0
        m2 = pre._load_sector_map()
        self.assertEqual(m2.get("BTC"), "crypto")
        self.assertEqual(m2.get("NVDA"), "tech")


if __name__ == "__main__":
    unittest.main()
