import unittest
from datetime import datetime
from unittest.mock import patch

from soxl_quant_system import SOXLQuantTrader


class PendingBuyQuantityTests(unittest.TestCase):
    def setUp(self):
        with patch.object(SOXLQuantTrader, "check_and_update_rsi_data", return_value=True):
            self.trader = SOXLQuantTrader(initial_capital=100_000)
        self.trader.set_profit_loss_compounding(enabled=True)
        self.trader.compound_reference_seed = 82_000
        self.trader.available_cash = 100_000
        self.trader.current_round = 7
        self.trader.current_mode = "SF"

    def test_displayed_pending_quantity_is_used_for_matching_fill(self):
        self.trader._pending_buy_recommendation = {
            "round": 7,
            "order_date": "2026-07-27",
            "quantity": 94,
        }

        bought = self.trader.execute_buy(
            target_price=141.60,
            actual_price=128.15,
            current_date=datetime(2026, 7, 27),
            mode="SF",
        )

        self.assertTrue(bought)
        self.assertEqual(self.trader.positions[-1]["shares"], 94)
        self.assertAlmostEqual(self.trader.positions[-1]["amount"], 94 * 128.15)

    def test_pending_quantity_does_not_apply_to_another_date(self):
        self.trader._pending_buy_recommendation = {
            "round": 7,
            "order_date": "2026-07-28",
            "quantity": 94,
        }

        bought = self.trader.execute_buy(
            target_price=141.60,
            actual_price=128.15,
            current_date=datetime(2026, 7, 27),
            mode="SF",
        )

        self.assertTrue(bought)
        expected = int((82_000 * 0.140) / 141.60)
        self.assertEqual(self.trader.positions[-1]["shares"], expected)

    def test_existing_actual_position_wins_over_stale_pending_metadata(self):
        snapshot = {
            "7_2026-07-27": {
                "shares": 90,
                "buy_price": 128.15,
                "amount": 90 * 128.15,
                "round": 7,
                "mode": "SF",
            },
            "pending_buy": {
                "round": 7,
                "order_date": "2026-07-27",
                "quantity": 94,
            },
        }

        self.assertIsNone(self.trader._pending_buy_from_snapshot(snapshot))

    def test_empty_snapshot_never_falls_back_to_full_resimulation(self):
        with patch.object(
            self.trader,
            "simulate_from_start_to_today",
            side_effect=AssertionError("full resimulation must not run"),
        ):
            result = self.trader.simulate_from_snapshot_to_today(
                {},
                "2025-08-27",
                quiet=True,
            )

        self.assertIn("error", result)
        self.assertIn("스냅샷", result["error"])


if __name__ == "__main__":
    unittest.main()
