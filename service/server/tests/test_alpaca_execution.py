import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from alpaca_broker import AlpacaBroker


class AlpacaExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = AlpacaBroker(
            api_key="key",
            secret_key="secret",
            managed_enabled=True,
        )

    def test_order_result_maps_terminal_and_pending_states(self):
        self.assertEqual(AlpacaBroker._order_result({"status": "filled", "filled_qty": "2"})["status"], "filled")
        self.assertEqual(AlpacaBroker._order_result({"status": "new"})["status"], "pending")
        self.assertEqual(AlpacaBroker._order_result({"status": "canceled"})["status"], "cancelled")

    @patch.object(AlpacaBroker, "find_order_by_client_order_id")
    @patch.object(AlpacaBroker, "submit_order")
    def test_execute_order_reuses_existing_client_order(self, submit_order, find_order):
        existing = {
            "id": "existing-order",
            "client_order_id": "ai-trader:21:1",
            "status": "filled",
            "filled_qty": "3",
            "filled_avg_price": "100.25",
        }
        find_order.return_value = existing
        result = self.broker.execute_order(
            symbol="NVDA",
            quantity=3,
            action="buy",
            client_order_id="ai-trader:21:1",
            poll_timeout=0,
        )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["alpaca_order_id"], "existing-order")
        submit_order.assert_not_called()

    @patch.object(AlpacaBroker, "_request")
    def test_submit_oco_order_builds_linked_protective_exits(self, request):
        request.return_value = {"id": "oco-order", "status": "new"}
        result = self.broker.submit_oco_order(
            "NVDA", 3, "sell", 95.0, 110.0, "ai-trader:pending-exit:21:7",
        )
        self.assertEqual(result["id"], "oco-order")
        body = request.call_args.kwargs["json_body"]
        self.assertEqual(body["order_class"], "oco")
        self.assertEqual(body["side"], "sell")
        self.assertEqual(body["stop_loss"]["stop_price"], "95.0")
        self.assertEqual(body["take_profit"]["limit_price"], "110.0")

    @patch.object(AlpacaBroker, "find_order_by_client_order_id")
    @patch.object(AlpacaBroker, "submit_order")
    def test_execute_close_reuses_existing_client_order(self, submit_order, find_order):
        existing = {
            "id": "close-order",
            "client_order_id": "ai-trader:21:2",
            "status": "filled",
            "filled_qty": "3",
            "filled_avg_price": "101.0",
        }
        find_order.return_value = existing
        result = self.broker.execute_close(
            symbol="NVDA",
            quantity=3,
            side="long",
            client_order_id="ai-trader:21:2",
        )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["alpaca_order_id"], "close-order")
        submit_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
