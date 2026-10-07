import unittest
from datetime import datetime
from unittest.mock import patch

from app import _serialize_compound_settlements, _should_auto_save_snapshot
from soxl_quant_system import SOXLQuantTrader


class SnapshotAutoSaveTests(unittest.TestCase):
    def test_corrected_entry_rules_are_saved_on_non_trading_day(self):
        position = {
            "round": 1,
            "shares": 35,
            "buy_price": 164.27000427246094,
            "mode": "AG",
            "buy_threshold": 3.5,
            "sell_threshold": 1.1,
            "max_hold_days": 35,
        }
        previous = {
            "1_2026-10-05": position,
            "available_cash": 107_739.64813629151,
            "as_of_date": "2026-10-05",
        }
        current = dict(previous)
        current["1_2026-10-05"] = {
            **position,
            "buy_threshold": 3.6,
            "sell_threshold": 3.5,
            "max_hold_days": 7,
        }

        with patch("app._is_market_trading_day", return_value=False):
            self.assertTrue(_should_auto_save_snapshot(previous, current))
            self.assertFalse(_should_auto_save_snapshot(current, dict(current)))

    def test_removed_lot_is_saved_on_non_trading_day(self):
        retained_position = {
            "round": 3,
            "buy_date": "2026-09-25",
            "buy_price": 151.4499969482422,
            "shares": 76,
        }
        previous = {
            "2_2026-09-24": {
                "round": 2,
                "buy_date": "2026-09-24",
                "buy_price": 146.3300018310547,
                "shares": 86,
            },
            "3_2026-09-25": retained_position,
            "available_cash": 79_416.45778656006,
            "as_of_date": "2026-09-25",
        }
        current = {
            "3_2026-09-25": retained_position,
            "available_cash": 92_441.15778656006,
            "as_of_date": "2026-09-25",
        }

        with patch("app._is_market_trading_day", return_value=False):
            self.assertTrue(_should_auto_save_snapshot(previous, current))

    def test_pending_compounding_settlement_survives_snapshot_round_trip(self):
        snapshot_settlements = _serialize_compound_settlements([{
            "trade_date": datetime(2026, 9, 25, 16, 0),
            "settlement_date": datetime(2026, 10, 2, 16, 0),
            "pnl": 100.0,
        }])
        trader = SOXLQuantTrader(initial_capital=1_000, auto_update_rsi=False)
        trader.set_profit_loss_compounding(enabled=True, profit_rate=0.7)
        trader._restore_compounding_from_snapshot({
            "compound_seed": 1_000.0,
            "compound_reference_seed": 1_000.0,
            "compound_settlements": snapshot_settlements,
        })

        self.assertEqual(
            trader.compound_settlements[0]["settlement_date"],
            datetime(2026, 10, 2),
        )
        trader._process_compounding_for_date(datetime(2026, 10, 2))
        self.assertEqual(trader.compound_seed, 1_070.0)
        self.assertEqual(trader.compound_settlements, [])


if __name__ == "__main__":
    unittest.main()
