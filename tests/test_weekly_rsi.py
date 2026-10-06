import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from soxl_quant_system import SOXLQuantTrader
from update_rsi_data import RSIDataUpdater
from us_market_calendar import is_us_equity_trading_day
from weekly_rsi import (
    RSI_CALCULATION_VERSION,
    calculate_completed_weekly_rsi,
    latest_completed_week_end,
    merge_rsi_reference,
)


def market_data(end):
    dates = pd.bdate_range("2026-01-02", end)
    dates = dates[[is_us_equity_trading_day(day.date()) for day in dates]]
    closes = pd.Series(
        [100 + i * 0.15 + (i % 7) * 0.5 for i in range(len(dates))],
        index=dates,
        dtype=float,
    )
    return pd.DataFrame({
        "Open": closes,
        "High": closes,
        "Low": closes,
        "Close": closes,
        "Volume": 1_000_000,
    })


def reference_row(end, rsi):
    end_date = pd.Timestamp(end)
    start_date = end_date - pd.Timedelta(days=4)
    return {
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
        "week": int(start_date.isocalendar().week),
        "rsi": rsi,
    }


def reference_rows(reference):
    return {
        row["end"]: row
        for year, data in reference.items()
        if year != "metadata"
        for row in data["weeks"]
    }


class CompletedWeeklyRSITests(unittest.TestCase):
    def test_last_fourteen_changes_use_simple_average(self):
        changes = [8, -4, 2, -1, 3, -2, 1, -3, 4, -1, 2, -2, 3, -1, 1, -2]
        closes = [100]
        for change in changes:
            closes.append(closes[-1] + change)
        dates = pd.date_range("2026-01-02", periods=len(closes), freq="W-FRI")
        session_dates = []
        for week_end in dates:
            last_session = week_end
            while not is_us_equity_trading_day(last_session.date()):
                last_session -= pd.Timedelta(days=1)
            session_dates.append(last_session)
        data = pd.DataFrame({"Close": closes}, index=session_dates)

        result = calculate_completed_weekly_rsi(
            data, as_of=dates[-1].to_pydatetime().replace(hour=17)
        )

        # The last 14 changes contain total gains of 16 and total losses of 12.
        self.assertAlmostEqual(result.iloc[-1], 100 * 16 / (16 + 12))
        delta = data["Close"].diff()
        smoothed_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        smoothed_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        smoothed_rsi = 100 - 100 / (1 + smoothed_gain / smoothed_loss)
        self.assertGreater(abs(result.iloc[-1] - smoothed_rsi.iloc[-1]), 1)

    def test_friday_preclose_excludes_partial_week(self):
        data = market_data("2026-10-02")
        data.loc[pd.Timestamp("2026-10-02"), "Close"] = 10_000

        result = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 10, 2, 15, 59)
        )

        self.assertEqual(result.index[-1], pd.Timestamp("2026-09-25"))
        prior_only = calculate_completed_weekly_rsi(
            data.loc[:"2026-09-25"], as_of=datetime(2026, 10, 2, 15, 59)
        )
        pd.testing.assert_series_equal(result, prior_only)

    def test_friday_close_includes_final_friday_candle(self):
        result = calculate_completed_weekly_rsi(
            market_data("2026-10-02"), as_of=datetime(2026, 10, 2, 16)
        )
        self.assertEqual(result.index[-1], pd.Timestamp("2026-10-02"))

    def test_thursday_only_provider_does_not_complete_regular_friday(self):
        result = calculate_completed_weekly_rsi(
            market_data("2026-10-01"), as_of=datetime(2026, 10, 2, 17)
        )
        self.assertEqual(result.index[-1], pd.Timestamp("2026-09-25"))

    def test_missing_friday_remains_invalid_when_next_monday_is_present(self):
        data = market_data("2026-10-05").drop(pd.Timestamp("2026-10-02"))
        result = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 10, 6, 12)
        )
        self.assertTrue(pd.notna(result.loc["2026-09-25"]))
        self.assertTrue(pd.isna(result.loc["2026-10-02"]))

    def test_missing_week_requires_fourteen_valid_weekly_changes_to_recover(self):
        data = market_data("2027-01-22").drop(pd.Timestamp("2026-10-02"))
        result = calculate_completed_weekly_rsi(
            data, as_of=datetime(2027, 1, 22, 17)
        )

        self.assertTrue(pd.isna(result.loc["2026-10-02"]))
        self.assertTrue(pd.isna(result.loc["2026-10-09"]))
        # Changes from 10/16 through 1/15 provide the next complete 14-week window.
        self.assertTrue(pd.isna(result.loc["2027-01-08"]))
        self.assertTrue(pd.notna(result.loc["2027-01-15"]))
        self.assertTrue(pd.notna(result.loc["2027-01-22"]))

    def test_friday_holiday_uses_thursday_close_and_friday_label(self):
        data = market_data("2026-07-02")
        before = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 7, 2, 15, 59)
        )
        after = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 7, 2, 16)
        )
        holiday = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 7, 3, 12)
        )

        self.assertEqual(before.index[-1], pd.Timestamp("2026-06-26"))
        self.assertEqual(after.index[-1], pd.Timestamp("2026-07-03"))
        pd.testing.assert_series_equal(after, holiday)

    def test_aware_clock_uses_eastern_close_boundary(self):
        seoul = ZoneInfo("Asia/Seoul")
        self.assertEqual(
            latest_completed_week_end(datetime(2026, 10, 3, 4, 59, tzinfo=seoul)),
            pd.Timestamp("2026-09-25"),
        )
        self.assertEqual(
            latest_completed_week_end(datetime(2026, 10, 3, 5, 0, tzinfo=seoul)),
            pd.Timestamp("2026-10-02"),
        )

    def test_aware_provider_timestamps_convert_to_eastern_market_dates(self):
        data = market_data("2026-10-02")
        expected = calculate_completed_weekly_rsi(
            data, as_of=datetime(2026, 10, 2, 17)
        )
        # UTC Saturday 01:00 still represents Friday evening in New York.
        aware_data = data.copy()
        aware_data.index = (aware_data.index + pd.Timedelta(hours=25)).tz_localize("UTC")
        result = calculate_completed_weekly_rsi(
            aware_data, as_of=datetime(2026, 10, 2, 17)
        )
        pd.testing.assert_series_equal(result, expected)


class RSIReferenceMergeTests(unittest.TestCase):
    def test_legacy_wrong_year_bucket_cannot_replace_canonical_historical_value(self):
        dates = pd.date_range("2025-08-01", "2026-10-02", freq="W-FRI")
        values = pd.Series([50 + i / 10 for i in range(len(dates))], index=dates)
        existing = {
            "2025": {"weeks": [reference_row("2025-08-01", 70.51)]},
            "2026": {"weeks": [
                reference_row("2025-08-01", 1.02),
                reference_row("2025-08-08", 9.99),
            ]},
        }

        merged = merge_rsi_reference(existing, values, as_of=datetime(2026, 10, 6))
        rows = reference_rows(merged)

        self.assertEqual(rows["2025-08-01"]["rsi"], 70.51)
        self.assertEqual(rows["2025-08-08"]["rsi"], 50.1)
        self.assertTrue(all(row["end"].startswith("2025") for row in merged["2025"]["weeks"]))
        self.assertTrue(all(row["end"].startswith("2026") for row in merged["2026"]["weeks"]))

    def test_same_iso_week_number_in_different_years_preserves_both_end_dates(self):
        values = pd.Series(
            [55.0, 65.0], index=pd.to_datetime(["2025-01-03", "2026-01-02"])
        )
        existing = {"2026": {"weeks": [
            reference_row("2025-01-03", 10),
            reference_row("2026-01-02", 20),
        ]}}

        merged = merge_rsi_reference(existing, values, as_of=datetime(2026, 1, 3))

        self.assertEqual(merged["2025"]["weeks"][0]["week"], 1)
        self.assertEqual(merged["2026"]["weeks"][0]["week"], 1)
        self.assertEqual(set(reference_rows(merged)), {"2025-01-03", "2026-01-02"})

    def test_fills_older_missing_week_and_refreshes_latest_twelve(self):
        dates = pd.date_range("2026-06-26", periods=15, freq="W-FRI")
        values = pd.Series([40 + i for i in range(15)], index=dates, dtype=float)
        existing = {"2026": {"weeks": [
            reference_row(end, 7.0)
            for i, end in enumerate(dates)
            if i != 2
        ]}}

        merged = merge_rsi_reference(existing, values, as_of=datetime(2026, 10, 6))
        rows = reference_rows(merged)

        self.assertEqual(len(rows), 15)
        self.assertEqual(rows[dates[0].strftime("%Y-%m-%d")]["rsi"], 7.0)
        self.assertEqual(rows[dates[2].strftime("%Y-%m-%d")]["rsi"], 42.0)
        for end, value in values.tail(12).items():
            self.assertEqual(rows[end.strftime("%Y-%m-%d")]["rsi"], value)
        self.assertEqual(merged["metadata"]["last_completed_week_end"], "2026-10-02")
        self.assertEqual(merged["metadata"]["calculation_version"], RSI_CALCULATION_VERSION)


class TraderWeeklyRSITests(unittest.TestCase):
    def setUp(self):
        self.trader = SOXLQuantTrader(initial_capital=100_000, auto_update_rsi=False)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.reference_path = Path(self.temp_dir.name) / "weekly_rsi_reference.json"
        self.path_patch = patch.object(
            self.trader, "_resolve_data_path", return_value=self.reference_path
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def write_reference(self, reference):
        self.reference_path.write_text(json.dumps(reference), encoding="utf-8")

    def read_reference(self):
        return json.loads(self.reference_path.read_text(encoding="utf-8"))

    def check_reference(self, now=datetime(2026, 10, 6, 12)):
        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=now),
            patch.object(self.trader, "get_stock_data", side_effect=AssertionError("network forbidden")),
            redirect_stdout(StringIO()),
        ):
            return self.trader.check_and_update_rsi_data()

    def current_reference(self):
        return {
            "2026": {"weeks": [
                reference_row("2026-09-25", 50.98),
                reference_row("2026-10-02", 62.74),
            ]},
            "metadata": {
                "last_updated": "2026-10-06",
                "last_completed_week_end": "2026-10-02",
                "calculation_version": RSI_CALCULATION_VERSION,
            },
        }

    def test_reference_is_current_with_version_and_two_exact_completed_weeks(self):
        self.write_reference(self.current_reference())
        self.assertTrue(self.check_reference())

    def test_last_updated_alone_does_not_make_legacy_reference_current(self):
        reference = self.current_reference()
        reference["metadata"].pop("calculation_version")
        self.write_reference(reference)
        self.assertFalse(self.check_reference())

    def test_current_metadata_does_not_hide_missing_recent_week(self):
        reference = self.current_reference()
        reference["2026"]["weeks"].pop(0)
        self.write_reference(reference)
        self.assertFalse(self.check_reference())

    def test_reference_from_previous_week_is_stale_even_if_saved_today(self):
        reference = self.current_reference()
        reference["2026"]["weeks"].pop()
        reference["metadata"]["last_completed_week_end"] = "2026-09-25"
        self.write_reference(reference)
        self.assertFalse(self.check_reference())

    def test_updater_saves_only_completed_weeks_before_friday_close(self):
        self.write_reference({"2026": {"weeks": [reference_row("2026-10-02", 99)]}})
        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 2, 15, 59)),
            patch.object(self.trader, "get_stock_data", return_value=market_data("2026-10-02")),
            redirect_stdout(StringIO()),
        ):
            self.assertTrue(self.trader.update_rsi_reference_file())

        saved = self.read_reference()
        self.assertNotIn("2026-10-02", reference_rows(saved))
        self.assertEqual(saved["metadata"]["last_completed_week_end"], "2026-09-25")
        self.assertTrue(self.check_reference(datetime(2026, 10, 2, 15, 59)))
        self.assertFalse(self.check_reference(datetime(2026, 10, 2, 16)))

    def test_updater_after_friday_close_requires_provider_friday_row(self):
        previous_reference = {
            "2026": {"weeks": [
                reference_row("2026-09-18", 50.03),
                reference_row("2026-09-25", 50.98),
            ]},
            "metadata": {"calculation_version": RSI_CALCULATION_VERSION},
        }
        self.write_reference(previous_reference)
        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 2, 17)),
            patch.object(self.trader, "get_stock_data", return_value=market_data("2026-10-01")),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(self.trader.update_rsi_reference_file())

        self.assertEqual(self.read_reference(), previous_reference)
        self.assertFalse(self.check_reference(datetime(2026, 10, 2, 17)))

    def test_updater_rejects_missing_friday_despite_newer_monday_data(self):
        previous_reference = self.current_reference()
        self.write_reference(previous_reference)
        data = market_data("2026-10-05").drop(pd.Timestamp("2026-10-02"))

        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 6, 12)),
            patch.object(self.trader, "get_stock_data", return_value=data),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(self.trader.update_rsi_reference_file())

        self.assertEqual(self.read_reference(), previous_reference)

    def test_date_lookup_omits_missing_friday_and_affected_following_week(self):
        data = market_data("2026-10-16").drop(pd.Timestamp("2026-10-02"))
        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 19, 12)),
            patch.object(self.trader, "get_stock_data", return_value=data) as mock_data,
            redirect_stdout(StringIO()),
        ):
            result = self.trader.calculate_weekly_rsi_for_dates([
                datetime(2026, 9, 25),
                datetime(2026, 10, 2),
                datetime(2026, 10, 9),
                datetime(2026, 10, 16),
            ])

        self.assertEqual(set(result), {"2026-09-25"})
        mock_data.assert_called_once_with("QQQ", "15y")

    def test_photo_values_recover_stale_safe_mode_for_october_sixth(self):
        reference = self.current_reference()
        reference["2026"]["weeks"].insert(0, reference_row("2026-09-18", 50.03))
        self.write_reference(reference)
        self.trader.current_mode = "SF"
        self.trader.current_week_friday = datetime(2026, 10, 9)

        with (
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 6, 12)),
            patch.object(self.trader, "get_stock_data", return_value=market_data("2026-10-05")),
            patch.object(self.trader, "calculate_weekly_rsi_for_dates", side_effect=AssertionError("network forbidden")),
            redirect_stdout(StringIO()),
        ):
            result = self.trader.get_daily_recommendation(skip_simulate=True)

        self.assertNotIn("error", result)
        self.assertEqual(result["mode"], "AG")
        self.assertEqual(result["qqq_one_week_ago_rsi"], 62.74)
        self.assertEqual(result["qqq_two_weeks_ago_rsi"], 50.98)
        self.assertEqual(self.trader.current_mode, "AG")


class StandaloneRSIUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.reference_path = Path(self.temp_dir.name) / "weekly_rsi_reference.json"
        self.updater = RSIDataUpdater(str(self.reference_path))

    def test_missing_friday_with_newer_monday_preserves_existing_reference(self):
        existing = {
            "2026": {"description": "2026년 주간 RSI", "weeks": [
                reference_row("2026-09-25", 50.98),
                reference_row("2026-10-02", 62.74),
            ]},
            "metadata": {
                "last_updated": "2026-10-05",
                "calculation_version": RSI_CALCULATION_VERSION,
            },
        }
        self.reference_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        original_bytes = self.reference_path.read_bytes()
        data = market_data("2026-10-05").drop(pd.Timestamp("2026-10-02"))

        with (
            patch.object(self.updater, "get_stock_data", return_value=data) as mock_data,
            patch("weekly_rsi.eastern_now", return_value=datetime(2026, 10, 6, 12)),
            patch("update_rsi_data.latest_completed_week_end", return_value=pd.Timestamp("2026-10-02")),
            redirect_stdout(StringIO()),
        ):
            self.assertFalse(self.updater.update_rsi_data())

        self.assertEqual(self.reference_path.read_bytes(), original_bytes)
        mock_data.assert_called_once_with("QQQ", "max")

    def test_monthly_full_history_falls_back_to_daily_and_preserves_eastern_dates(self):
        timestamps = [
            int(datetime(2026, 9, day, 20, tzinfo=timezone.utc).timestamp())
            for day in (1, 2)
        ]

        def payload(granularity, closes):
            return {"chart": {"result": [{
                "timestamp": timestamps,
                "meta": {"dataGranularity": granularity},
                "indicators": {"quote": [{
                    "open": closes,
                    "high": closes,
                    "low": closes,
                    "close": closes,
                    "volume": [1_000, 1_100],
                }]},
            }]}}

        monthly_response = Mock(status_code=200)
        monthly_response.json.return_value = payload("1mo", [901.0, 902.0])
        daily_response = Mock(status_code=200)
        daily_response.json.return_value = payload("1d", [101.0, 102.0])

        with (
            patch("update_rsi_data.requests.get", side_effect=[monthly_response, daily_response]) as mock_get,
            redirect_stdout(StringIO()),
        ):
            result = self.updater.get_stock_data("QQQ", "max")

        self.assertIsNotNone(result)
        self.assertEqual(result["Close"].tolist(), [101.0, 102.0])
        self.assertEqual(result.index.tolist(), [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02")])
        self.assertEqual(mock_get.call_count, 2)
        full_params = mock_get.call_args_list[0].kwargs["params"]
        self.assertEqual(full_params["period1"], 0)
        self.assertEqual(full_params["interval"], "1d")
        self.assertEqual(full_params["events"], "history")
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"range": "2y", "interval": "1d"})


if __name__ == "__main__":
    unittest.main()
