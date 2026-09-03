import inspect
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _chart_payload():
    timestamps = [
        int(datetime(2026, 9, 1, 12, 0).timestamp()),
        int(datetime(2026, 9, 2, 12, 0).timestamp()),
    ]
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "meta": {},
                "indicators": {
                    "quote": [{
                        "open": [100.0, 101.0],
                        "high": [102.0, 103.0],
                        "low": [99.0, 100.0],
                        "close": [101.0, 102.0],
                        "volume": [1_000, 1_100],
                    }],
                },
            }],
        },
    }


class MarketDataResilienceTests(unittest.TestCase):
    def setUp(self):
        self.trader = SOXLQuantTrader(initial_capital=9_000, auto_update_rsi=False)
        self.trader.clear_cache(clear_market_data=True)

    def tearDown(self):
        self.trader.clear_cache(clear_market_data=True)

    @patch("soxl_quant_system.time.sleep", return_value=None)
    @patch("soxl_quant_system.requests.get")
    def test_transient_failures_retry_on_alternate_yahoo_host(self, mock_get, _mock_sleep):
        mock_get.side_effect = [
            _FakeResponse(429, headers={"Retry-After": "0"}),
            _FakeResponse(503),
            _FakeResponse(200, _chart_payload()),
        ]

        result = self.trader.get_stock_data("QQQ", "6mo")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(mock_get.call_count, 3)
        urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertIn("query1.finance.yahoo.com", urls[0])
        self.assertIn("query2.finance.yahoo.com", urls[1])
        self.assertIn("query1.finance.yahoo.com", urls[2])

    @patch("soxl_quant_system.requests.get")
    def test_fresh_market_data_cache_is_shared_between_traders(self, mock_get):
        mock_get.return_value = _FakeResponse(200, _chart_payload())
        first = self.trader.get_stock_data("QQQ", "6mo")
        second_trader = SOXLQuantTrader(initial_capital=10_000, auto_update_rsi=False)
        second = second_trader.get_stock_data("QQQ", "6mo")

        self.assertEqual(mock_get.call_count, 1)
        pd.testing.assert_frame_equal(first, second)

    def test_default_clear_preserves_market_data_and_clears_simulation_only(self):
        cached = pd.DataFrame(
            {"Close": [102.0]}, index=pd.to_datetime(["2026-09-02"])
        )
        self.trader._stock_data_cache["QQQ_6mo"] = (cached, datetime.now())
        self.trader._simulation_cache["simulation"] = ({"ok": True}, datetime.now())
        self.trader._stock_data_fallbacks["QQQ_6mo"] = {"symbol": "QQQ"}

        self.trader.clear_cache()

        self.assertIn("QQQ_6mo", self.trader._stock_data_cache)
        self.assertEqual(self.trader._simulation_cache, {})
        self.assertEqual(self.trader.get_stock_data_fallbacks(), [])

    @patch("soxl_quant_system.time.sleep", return_value=None)
    @patch("soxl_quant_system.requests.get", side_effect=RuntimeError("provider down"))
    def test_provider_failure_uses_recent_cache_that_covers_required_market_date(
        self, mock_get, _mock_sleep
    ):
        cached = pd.DataFrame(
            {"Close": [101.0, 102.0]},
            index=pd.to_datetime(["2026-08-28", "2026-09-02"]),
        )
        self.trader._stock_data_cache["QQQ_6mo"] = (
            cached,
            datetime.now() - timedelta(minutes=10),
        )

        with patch.object(
            self.trader, "_required_cached_market_date", return_value=date(2026, 8, 28)
        ):
            result = self.trader.get_stock_data("QQQ", "6mo")
            cooldown_result = self.trader.get_stock_data("QQQ", "6mo")

        self.assertIsNotNone(result)
        self.assertIsNotNone(cooldown_result)
        # The immediate second preset-style rerun must not create another burst.
        self.assertEqual(mock_get.call_count, 3)
        fallbacks = self.trader.get_stock_data_fallbacks()
        self.assertEqual(len(fallbacks), 1)
        self.assertEqual(fallbacks[0]["latest_date"], "2026-09-02")

    @patch("soxl_quant_system.time.sleep", return_value=None)
    @patch("soxl_quant_system.requests.get", side_effect=RuntimeError("provider down"))
    def test_cache_missing_required_market_date_is_rejected(self, _mock_get, _mock_sleep):
        cached = pd.DataFrame(
            {"Close": [100.0]}, index=pd.to_datetime(["2026-08-27"])
        )
        self.trader._stock_data_cache["QQQ_6mo"] = (
            cached,
            datetime.now() - timedelta(minutes=10),
        )

        with patch.object(
            self.trader, "_required_cached_market_date", return_value=date(2026, 8, 28)
        ):
            result = self.trader.get_stock_data("QQQ", "6mo")

        self.assertIsNone(result)
        self.assertEqual(self.trader.get_stock_data_fallbacks(), [])

    @patch("soxl_quant_system.time.sleep", return_value=None)
    @patch("soxl_quant_system.requests.get")
    def test_http_200_without_required_market_date_is_retried_and_rejected(
        self, mock_get, _mock_sleep
    ):
        mock_get.return_value = _FakeResponse(200, _chart_payload())

        with patch.object(
            self.trader, "_required_cached_market_date", return_value=date(2026, 9, 3)
        ):
            result = self.trader.get_stock_data("QQQ", "6mo")

        self.assertIsNone(result)
        self.assertEqual(mock_get.call_count, 3)
        self.assertNotIn("QQQ_6mo", self.trader._stock_data_cache)

    @patch("soxl_quant_system.requests.get")
    def test_intraday_daily_bar_is_not_reused_after_market_close(self, mock_get):
        mock_get.return_value = _FakeResponse(200, _chart_payload())

        with patch.object(
            self.trader, "get_us_eastern_now", return_value=datetime(2026, 9, 2, 15, 59)
        ), patch.object(self.trader, "is_trading_day", return_value=True):
            before_close = self.trader.get_stock_data("SOXL", "1mo")

        self.assertEqual(before_close.index.max().date(), date(2026, 9, 1))
        self.assertEqual(mock_get.call_count, 1)

        with patch.object(
            self.trader, "get_us_eastern_now", return_value=datetime(2026, 9, 2, 16, 1)
        ), patch.object(self.trader, "is_trading_day", return_value=True):
            after_close = self.trader.get_stock_data("SOXL", "1mo")

        self.assertEqual(after_close.index.max().date(), date(2026, 9, 2))
        # The pre-close cache ended on 9/1, so crossing 16:00 forces a refetch.
        self.assertEqual(mock_get.call_count, 2)

    def test_streamlit_can_disable_constructor_rsi_network_work(self):
        with patch.object(
            SOXLQuantTrader,
            "check_and_update_rsi_data",
            side_effect=AssertionError("constructor must not check RSI"),
        ), patch.object(
            SOXLQuantTrader,
            "update_rsi_reference_file",
            side_effect=AssertionError("constructor must not update RSI"),
        ):
            trader = SOXLQuantTrader(initial_capital=9_000, auto_update_rsi=False)

        self.assertIsNotNone(trader)

    def test_backtest_does_not_request_unused_qqq_dataframe(self):
        source = inspect.getsource(SOXLQuantTrader.run_backtest)
        self.assertNotIn('get_stock_data("QQQ"', source)


if __name__ == "__main__":
    unittest.main()
