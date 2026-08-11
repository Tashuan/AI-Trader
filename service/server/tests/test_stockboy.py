import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from routes import create_app
from stockboy_models import StockBoyActionRequest, StockBoyActionResponse
from stockboy_policy import PolicyViolation, validate_action


class StockBoyPolicyTests(unittest.TestCase):
    def _position(self, agent_name="BlitzRunner"):
        return {
            "agent_name": agent_name,
            "quantity": 1.0,
            "entry_price": 100.0,
            "side": "long",
            "stop_loss_price": 95.0,
        }

    def test_policy_rejects_entries(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="entry-attempt",
            runner_key="blitztrader",
            action_type="buy",
            target_position_id=123,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(request, supervisor_enabled=True, actions_enabled=True, paper_only=True)
        self.assertEqual(context.exception.category, "no_entry")

    def test_policy_rejects_non_controlled_owner(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="other-owner-close",
            runner_key="blitztrader",
            action_type="close_position",
            target_position_id=123,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_position=self._position("OtherAgent"),
                current_price=110.0,
                current_price_age_seconds=1.0,
            )
        self.assertEqual(context.exception.category, "ownership_mismatch")

    def test_policy_rejects_stale_price_for_reduction(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="stale-close",
            runner_key="blitztrader",
            action_type="close_position",
            target_position_id=123,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_position=self._position(),
                current_price=110.0,
                current_price_age_seconds=1000.0,
            )
        self.assertEqual(context.exception.category, "stale_price")

    def test_policy_rejects_loosened_long_stop(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="loosen-stop",
            runner_key="blitztrader",
            action_type="set_stop",
            target_position_id=123,
            stop_loss_price=90.0,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_position=self._position(),
            )
        self.assertEqual(context.exception.category, "stop_loosened")

    def test_policy_rejects_cancel_order_wrong_owner(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="cancel-other",
            runner_key="blitztrader",
            action_type="cancel_order",
            target_order_id=42,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_order={"agent_name": "OtherAgent", "status": "PENDING"},
            )
        self.assertEqual(context.exception.category, "ownership_mismatch")

    def test_policy_rejects_cancel_order_not_pending(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="cancel-filled",
            runner_key="blitztrader",
            action_type="cancel_order",
            target_order_id=42,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_order={"agent_name": "BlitzRunner", "status": "FILLED"},
            )
        self.assertEqual(context.exception.category, "invalid_order_state")

    def test_policy_rejects_cancel_order_missing(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="cancel-missing",
            runner_key="blitztrader",
            action_type="cancel_order",
            target_order_id=42,
        )
        with self.assertRaises(PolicyViolation) as context:
            validate_action(
                request,
                supervisor_enabled=True,
                actions_enabled=True,
                paper_only=True,
                target_order=None,
            )
        self.assertEqual(context.exception.category, "missing_target")

    def test_policy_accepts_cancel_order_correct_owner(self) -> None:
        request = StockBoyActionRequest(
            idempotency_key="cancel-ok",
            runner_key="blitztrader",
            action_type="cancel_order",
            target_order_id=42,
        )
        validate_action(
            request,
            supervisor_enabled=True,
            actions_enabled=True,
            paper_only=True,
            target_order={"agent_name": "BlitzRunner", "status": "PENDING"},
        )


class StockBoyRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    @patch("routes_stockboy.status")
    @patch("routes_stockboy.build_snapshot")
    @patch("routes_stockboy.require_capability")
    def test_supervisor_capability_is_required_for_actions(self, require_capability, build_snapshot, mgr_status) -> None:
        from stockboy_models import StockBoySnapshot, StockBoySupervisorStatus, StockBoyPortfolioOverview

        require_capability.side_effect = HTTPException(status_code=403, detail="Insufficient permissions")
        response = self.client.post(
            "/api/stockboy/action",
            headers={"Authorization": "Bearer ordinary-token"},
            json={
                "idempotency_key": "unauthorized-close",
                "runner_key": "blitztrader",
                "action_type": "close_position",
                "target_position_id": 123,
            },
        )
        self.assertEqual(response.status_code, 403)

        require_capability.side_effect = None
        require_capability.return_value = {"id": 1, "name": "StockBoy", "role": "supervisor"}
        mgr_status.return_value = {"running": True}
        build_snapshot.return_value = StockBoySnapshot(
            timestamp="2026-08-10T12:00:00Z",
            supervisor=StockBoySupervisorStatus(enabled=True, running=True, controlled_runners=["blitztrader", "cryptorunner", "scalprunner"]),
            portfolio=StockBoyPortfolioOverview(),
        )
        response = self.client.get("/api/stockboy/snapshot", headers={"Authorization": "Bearer stockboy-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["supervisor"]["mode"], "paper")

    @patch("routes_stockboy.require_capability")
    def test_status_requires_supervisor_auth(self, require_capability) -> None:
        require_capability.side_effect = HTTPException(status_code=401, detail="Invalid token")
        response = self.client.get("/api/stockboy/status")
        self.assertEqual(response.status_code, 401)

    @patch("routes_stockboy.require_capability")
    def test_snapshot_requires_supervisor_auth(self, require_capability) -> None:
        require_capability.side_effect = HTTPException(status_code=401, detail="Invalid token")
        response = self.client.get("/api/stockboy/snapshot")
        self.assertEqual(response.status_code, 401)

    @patch("routes_stockboy.execute_action")
    @patch("routes_stockboy.require_capability")
    def test_action_route_returns_idempotent_service_result(self, require_capability, execute_action) -> None:
        require_capability.return_value = {"id": 1, "name": "StockBoy", "role": "supervisor"}
        execute_action.return_value = StockBoyActionResponse(
            success=True, action_id=9, status="executed", message="Idempotent replay",
        )
        response = self.client.post(
            "/api/stockboy/action",
            headers={"Authorization": "Bearer stockboy-token"},
            json={
                "idempotency_key": "close-position-once",
                "runner_key": "blitztrader",
                "action_type": "partial_close",
                "target_position_id": 123,
                "quantity": 0.25,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Idempotent replay")
        execute_action.assert_called_once()


if __name__ == "__main__":
    unittest.main()
