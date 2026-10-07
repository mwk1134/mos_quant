import json
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class SnapshotModeCacheTests(unittest.TestCase):
    def setUp(self):
        self.trader = SOXLQuantTrader(initial_capital=100_000, auto_update_rsi=False)

    def test_real_reference_corrects_entry_week_without_using_current_mode(self):
        reference_path = Path(__file__).resolve().parents[1] / "data" / "weekly_rsi_reference.json"
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        october_week = datetime(2026, 10, 9)
        self.assertEqual(
            self.trader.get_rsi_from_reference(datetime(2026, 10, 2), reference),
            62.74,
        )
        self.assertEqual(
            self.trader.get_rsi_from_reference(datetime(2026, 9, 25), reference),
            50.98,
        )
        self.trader.current_mode = "AG"
        positions = [
            {"round": 1, "buy_date": datetime(2026, 10, 5), "mode": "SF"},
            {"round": 2, "buy_date": datetime(2026, 8, 31), "mode": "AG"},
        ]
        with (
            redirect_stdout(StringIO()),
            patch.object(self.trader, "get_stock_data", side_effect=AssertionError("unexpected network fallback")),
            patch.object(
                self.trader,
                "calculate_weekly_rsi_for_dates",
                side_effect=AssertionError("unexpected RSI fallback"),
            ),
        ):
            self.assertEqual(
                self.trader._calculate_week_mode_recursive_with_reference(october_week, reference),
                ("AG", True),
            )
            self.trader._revalidate_position_modes(positions, rsi_ref_data=reference)

        self.assertEqual(positions[0]["mode"], "AG")
        self.assertEqual(positions[0]["sell_threshold"], 3.5)
        self.assertEqual(positions[0]["max_hold_days"], 7)
        self.assertEqual(positions[1]["mode"], "SF")
        self.assertEqual(positions[1]["sell_threshold"], 1.1)
        self.assertEqual(positions[1]["max_hold_days"], 35)
        self.assertEqual(self.trader.current_mode, "AG")

    def snapshot(self):
        return {
            "1_2026-10-05": {
                "shares": 29,
                "buy_price": 164.27,
                "amount": 4_763.83,
                "round": 1,
                "mode": "AG",
                "buy_threshold": 4.4,
                "sell_threshold": 5.2,
                "max_hold_days": 12,
            },
            "available_cash": 95_236.17,
            "as_of_date": "2026-10-05",
        }

    def assert_changed_state_replays(self, changed_snapshot):
        original_snapshot = self.snapshot()
        dates = pd.bdate_range("2026-10-02", "2026-10-06")
        closes = pd.Series([163.87, 164.27, 164.27], index=dates)
        market_data = pd.DataFrame({
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": 1_000_000,
        })

        def replay(*args, **kwargs):
            self.trader.positions = deepcopy(kwargs["initial_positions"])
            self.trader.available_cash = kwargs["initial_cash"]
            self.trader.current_round = len(self.trader.positions) + 1
            return {"from_snapshot": True}

        with (
            redirect_stdout(StringIO()),
            patch.object(self.trader, "get_latest_trading_day", return_value=datetime(2026, 10, 6)),
            patch.object(self.trader, "get_stock_data", return_value=market_data),
            patch.object(self.trader, "_revalidate_position_modes"),
            patch.object(self.trader, "run_backtest", side_effect=replay) as run_backtest,
        ):
            self.trader.simulate_from_snapshot_to_today(original_snapshot, "2026-10-05")
            self.trader.simulate_from_snapshot_to_today(original_snapshot, "2026-10-05")
            self.assertEqual(run_backtest.call_count, 1)

            self.trader.simulate_from_snapshot_to_today(changed_snapshot, "2026-10-05")
            self.assertEqual(run_backtest.call_count, 2)

        self.assertEqual(run_backtest.call_args.kwargs["initial_cash"], changed_snapshot["available_cash"])
        self.assertEqual(self.trader.available_cash, changed_snapshot["available_cash"])
        expected_position = changed_snapshot["1_2026-10-05"]
        for field in ("shares", "buy_price", "amount", "buy_threshold", "sell_threshold", "max_hold_days"):
            with self.subTest(field=field):
                self.assertEqual(self.trader.positions[0][field], expected_position[field])

    def test_same_checkpoint_replays_when_frozen_entry_rules_change(self):
        changed_snapshot = self.snapshot()
        changed_snapshot["1_2026-10-05"].update({
            "buy_threshold": 4.5,
            "sell_threshold": 6.2,
            "max_hold_days": 13,
        })

        self.assert_changed_state_replays(changed_snapshot)

    def test_same_checkpoint_replays_when_confirmed_cash_changes(self):
        changed_snapshot = self.snapshot()
        changed_snapshot["available_cash"] += 100.0

        self.assert_changed_state_replays(changed_snapshot)


if __name__ == "__main__":
    unittest.main()
