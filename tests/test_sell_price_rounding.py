import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch

import pandas as pd

from soxl_quant_system import SOXLQuantTrader, normalize_order_price


class SellPriceRoundingTests(unittest.TestCase):
    def setUp(self):
        self.trader = SOXLQuantTrader(initial_capital=100_000, auto_update_rsi=False)
        self.position = {
            "round": 2,
            "buy_date": datetime(2026, 9, 24),
            "buy_price": 146.3300018310547,
            "shares": 86,
            "amount": 86 * 146.3300018310547,
            "mode": "AG",
        }
        self.provider_close = 151.4499969482422

    def test_order_price_uses_financial_half_up_rounding(self):
        self.assertEqual(normalize_order_price(151.445), 151.45)

    def test_target_and_close_are_compared_at_the_displayed_cent(self):
        self.trader.positions = [self.position]

        recommendations, debug = self.trader.check_sell_conditions(
            pd.Series({"Close": self.provider_close}),
            datetime(2026, 9, 25),
            prev_close=146.3300018310547,
            return_debug_info=True,
        )

        self.assertEqual(self.trader.calculate_position_sell_price(self.position), 151.45)
        self.assertEqual(debug[0]["target_sell_price"], 151.45)
        self.assertEqual(debug[0]["current_close"], 151.45)
        self.assertTrue(debug[0]["meets_target_price"])
        self.assertTrue(recommendations[0]["will_sell"])
        self.assertFalse(recommendations[0]["show_in_recommendation_list"])

    def test_reconcile_removes_the_filled_position(self):
        self.trader.positions = [self.position]
        self.trader.available_cash = 1_000.0
        history = pd.DataFrame(
            {"Close": [self.provider_close]},
            index=pd.to_datetime(["2026-09-25"]),
        )

        with (
            patch.object(self.trader, "get_today_date", return_value=datetime(2026, 9, 28)),
            patch.object(self.trader, "is_regular_session_closed_now", return_value=True),
        ):
            self.trader.reconcile_positions_with_close_history(history)

        self.assertEqual(self.trader.positions, [])
        self.assertAlmostEqual(
            self.trader.available_cash,
            1_000.0 + self.position["shares"] * 151.45,
        )

    def test_reconcile_keeps_the_replacement_lot_bought_on_sell_date(self):
        replacement = {
            "round": 3,
            "buy_date": datetime(2026, 9, 25),
            "buy_price": self.provider_close,
            "shares": 76,
            "amount": 76 * self.provider_close,
            "mode": "AG",
        }
        self.trader.positions = [self.position, replacement]
        history = pd.DataFrame(
            {"Close": [self.provider_close]},
            index=pd.to_datetime(["2026-09-25"]),
        )

        with (
            patch.object(self.trader, "get_today_date", return_value=datetime(2026, 9, 28)),
            patch.object(self.trader, "is_regular_session_closed_now", return_value=True),
        ):
            self.trader.reconcile_positions_with_close_history(history)

        self.assertEqual(self.trader.positions, [replacement])

    def test_a_close_one_cent_below_target_does_not_sell(self):
        self.trader.positions = [self.position]

        recommendations = self.trader.check_sell_conditions(
            pd.Series({"Close": 151.44}),
            datetime(2026, 9, 25),
            prev_close=146.3300018310547,
        )

        self.assertEqual(normalize_order_price(151.4499969482422), 151.45)
        self.assertFalse(recommendations[0]["will_sell"])

    def test_console_recommendation_uses_the_same_sell_target(self):
        self.trader.positions = [self.position]
        self.trader.current_mode = "AG"
        recommendation = {
            "date": "2026-09-28",
            "mode": "AG",
            "qqq_one_week_ago_rsi": 55.0,
            "qqq_two_weeks_ago_rsi": 54.0,
            "soxl_current_price": self.provider_close,
            "can_buy": False,
            "active_config": self.trader.ag_config,
            "sell_recommendations": [],
            "portfolio": {
                "positions_count": 1,
                "total_invested": self.position["amount"],
                "total_position_value": self.position["shares"] * self.provider_close,
                "unrealized_pnl": 0.0,
                "available_cash": 0.0,
                "total_portfolio_value": self.position["shares"] * self.provider_close,
            },
        }

        output = StringIO()
        with (
            patch.object(self.trader, "get_today_date", return_value=datetime(2026, 9, 28)),
            redirect_stdout(output),
        ):
            self.trader.print_recommendation(recommendation)

        self.assertIn("목표가 $151.45", output.getvalue())


if __name__ == "__main__":
    unittest.main()
