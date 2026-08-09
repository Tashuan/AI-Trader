import json
import sqlite3
import unittest
from datetime import datetime, timezone

from scalp_guardrails import GuardrailViolation, validate_entry


class RunnerBudgetTests(unittest.TestCase):
    def _db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT, cash REAL, deposited REAL);
            CREATE TABLE agent_configs (agent_id INTEGER, config_json TEXT, max_positions INTEGER);
            CREATE TABLE positions (agent_id INTEGER, symbol TEXT, side TEXT, quantity REAL, entry_price REAL, current_price REAL);
            CREATE TABLE trading_risk_state (agent_id INTEGER PRIMARY KEY, day_key TEXT, starting_equity REAL, halted INTEGER, halt_reason TEXT, updated_at TEXT);
            CREATE TABLE signals (agent_id INTEGER, market TEXT, symbol TEXT, side TEXT, message_type TEXT, created_at TEXT);
            """
        )
        risk = {"strategy_params": {"risk_controls": {
            "paper_account_budget": 10000,
            "max_trade_notional_pct": 25,
            "daily_loss_halt_pct": 3,
        }}}
        conn.execute("INSERT INTO agents VALUES (1, 'CryptoRunner', 10000, 0)")
        conn.execute("INSERT INTO agent_configs VALUES (1, ?, 3)", (json.dumps(risk),))
        conn.commit()
        return conn

    def test_runner_budget_blocks_new_gross_exposure(self):
        conn = self._db()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        validate_entry(cursor, agent_id=1, market="crypto", symbol="BTC", action="buy", trade_value=2000, now=now)
        cursor.execute("INSERT INTO positions VALUES (1, 'BTC', 'long', 1, 9000, 9000)")
        conn.commit()
        with self.assertRaises(GuardrailViolation) as error:
            validate_entry(cursor, agent_id=1, market="crypto", symbol="ETH", action="buy", trade_value=2000, now=now)
        self.assertIn("budget", str(error.exception).lower())


if __name__ == "__main__":
    unittest.main()
