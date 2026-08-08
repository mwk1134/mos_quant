import unittest
from datetime import date, datetime
from unittest.mock import patch

from soxl_quant_system import SOXLQuantTrader
from us_market_calendar import (
    is_us_equity_trading_day,
    us_equity_market_holidays,
)


class USMarketCalendarTests(unittest.TestCase):
    def setUp(self):
        with patch.object(SOXLQuantTrader, "check_and_update_rsi_data", return_value=True):
            self.trader = SOXLQuantTrader(initial_capital=100_000)

    def test_2026_official_full_day_closures(self):
        expected = {
            date(2026, 1, 1),
            date(2026, 1, 19),
            date(2026, 2, 16),
            date(2026, 4, 3),
            date(2026, 5, 25),
            date(2026, 6, 19),
            date(2026, 7, 3),
            date(2026, 9, 7),
            date(2026, 11, 26),
            date(2026, 12, 25),
        }
        self.assertTrue(expected.issubset(us_equity_market_holidays(2026)))
        for holiday in expected:
            self.assertFalse(is_us_equity_trading_day(holiday), holiday.isoformat())

    def test_special_closure_is_not_a_trading_day(self):
        self.assertFalse(is_us_equity_trading_day(date(2025, 1, 9)))

    def test_saturday_new_year_is_not_observed_on_preceding_friday(self):
        self.assertTrue(is_us_equity_trading_day(date(2027, 12, 31)))

    def test_custom_runtime_closure_is_respected(self):
        self.trader.us_holidays.add("2026-08-10")
        self.assertFalse(self.trader.is_trading_day(datetime(2026, 8, 10)))

    def test_sf_stop_loss_date_skips_2026_independence_day_closure(self):
        buy_date = datetime(2026, 7, 1)
        self.assertEqual(
            self.trader.calculate_stop_loss_date(buy_date, 35),
            "08.20.(목)",
        )
        self.assertEqual(
            self.trader.get_trading_date_after(buy_date, 35),
            datetime(2026, 8, 20),
        )

    def test_ag_stop_loss_date_uses_same_calendar(self):
        self.assertEqual(
            self.trader.calculate_stop_loss_date(datetime(2026, 7, 1), 7),
            "07.13.(월)",
        )

    def test_holding_day_count_matches_stop_loss_boundary(self):
        buy_date = datetime(2026, 7, 1)
        self.assertEqual(
            self.trader.count_trading_days(buy_date, datetime(2026, 8, 19)),
            34,
        )
        self.assertEqual(
            self.trader.count_trading_days(buy_date, datetime(2026, 8, 20)),
            35,
        )

    def test_latest_trading_day_on_holiday_returns_previous_session(self):
        with patch.object(
            self.trader,
            "get_us_eastern_now",
            return_value=datetime(2026, 7, 3, 12, 0),
        ):
            self.assertEqual(
                self.trader.get_latest_trading_day(),
                datetime(2026, 7, 2),
            )

    def test_latest_trading_day_before_and_after_close(self):
        with patch.object(
            self.trader,
            "get_us_eastern_now",
            return_value=datetime(2026, 7, 6, 15, 59),
        ):
            self.assertEqual(
                self.trader.get_latest_trading_day(),
                datetime(2026, 7, 2),
            )

        with patch.object(
            self.trader,
            "get_us_eastern_now",
            return_value=datetime(2026, 7, 6, 16, 0),
        ):
            self.assertEqual(
                self.trader.get_latest_trading_day(),
                datetime(2026, 7, 6),
            )


if __name__ == "__main__":
    unittest.main()
