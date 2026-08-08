import unittest
from unittest.mock import patch

from soxl_quant_system import SOXLQuantTrader


class SnapshotResumeDateTests(unittest.TestCase):
    def setUp(self):
        with patch.object(SOXLQuantTrader, "check_and_update_rsi_data", return_value=True):
            self.trader = SOXLQuantTrader(initial_capital=9_000)

    def test_runtime_checkpoint_wins_over_latest_position_buy_date(self):
        snapshot = {
            "1_2026-07-23": {
                "shares": 10,
                "buy_price": 100.0,
                "amount": 1_000.0,
                "round": 1,
                "mode": "SF",
            },
            "available_cash": 47_806.0,
            "as_of_date": "2026-08-07",
        }

        positions, resume_date, available_cash = self.trader._snapshot_to_positions_and_state(snapshot)

        self.assertEqual(len(positions), 1)
        self.assertEqual(resume_date, "2026-08-07")
        self.assertEqual(available_cash, 47_806.0)

    def test_legacy_snapshot_still_resumes_from_latest_buy_date(self):
        snapshot = {
            "1_2026-07-23": {
                "shares": 10,
                "buy_price": 100.0,
                "amount": 1_000.0,
                "round": 1,
                "mode": "SF",
            },
            "available_cash": 47_806.0,
        }

        _, resume_date, _ = self.trader._snapshot_to_positions_and_state(snapshot)

        self.assertEqual(resume_date, "2026-07-23")

    def test_cash_only_snapshot_uses_exact_runtime_checkpoint(self):
        snapshot = {
            "available_cash": 47_806.0,
            "as_of_date": "2026-08-07",
        }

        positions, resume_date, available_cash = self.trader._snapshot_to_positions_and_state(snapshot)

        self.assertEqual(positions, [])
        self.assertEqual(resume_date, "2026-08-07")
        self.assertEqual(available_cash, 47_806.0)


if __name__ == "__main__":
    unittest.main()
