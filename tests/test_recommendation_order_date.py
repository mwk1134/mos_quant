import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class RecommendationOrderDateTests(unittest.TestCase):
    def setUp(self):
        with patch.object(SOXLQuantTrader, "check_and_update_rsi_data", return_value=True):
            self.trader = SOXLQuantTrader(initial_capital=21_199)
        self.trader.set_profit_loss_compounding(enabled=True)
        self.trader.compound_reference_seed = 21_199
        self.trader.current_round = 4
        self.trader.current_mode = "AG"
        self.trader.current_week_friday = datetime(2026, 8, 21)

        dates = pd.bdate_range("2026-01-02", "2026-08-20")
        closes = pd.Series(range(100, 100 + len(dates)), index=dates, dtype=float)
        self.market_data = pd.DataFrame({
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": 1_000_000,
        })

    def recommendation(self, market_closed):
        with (
            patch.object(self.trader, "get_today_date", return_value=datetime(2026, 8, 20, 17, 0)),
            patch.object(self.trader, "is_regular_session_closed_now", return_value=market_closed),
            patch.object(self.trader, "get_stock_data", return_value=self.market_data.copy()),
            patch.object(self.trader, "get_rsi_from_reference", return_value=60.0),
            patch.object(self.trader, "_is_mode_case_matched", return_value=(False, None)),
            patch.object(
                self.trader,
                "_calculate_week_mode_recursive_with_reference",
                return_value=("AG", True),
            ),
        ):
            return self.trader.get_daily_recommendation(skip_simulate=True)

    def test_before_close_recommendation_is_for_same_trading_day(self):
        result = self.recommendation(market_closed=False)

        self.assertNotIn("error", result)
        self.assertEqual(result["date"], "2026-08-20")
        self.assertEqual(result["basis_date"], "2026-08-19")
        self.assertEqual(result["buy_order_date"], "2026-08-20")

    def test_after_close_recommendation_is_for_next_trading_day(self):
        result = self.recommendation(market_closed=True)

        self.assertNotIn("error", result)
        self.assertEqual(result["date"], "2026-08-20")
        self.assertEqual(result["basis_date"], "2026-08-20")
        self.assertEqual(result["buy_order_date"], "2026-08-21")


if __name__ == "__main__":
    unittest.main()
