"""KMW3 uses actual exchange close boundaries without changing live presets."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from banban_ui import _BanbanMarketDataTrader, _completed_basis_date


ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize(("instant", "expected"), [
    ("2026-11-03T08:00:00-05:00", "2026-11-02"),  # Pre-open.
    ("2026-11-03T15:59:59-05:00", "2026-11-02"),
    ("2026-11-03T16:00:00-05:00", "2026-11-03"),
    ("2026-03-09T19:59:59+00:00", "2026-03-06"),  # After spring DST.
    ("2026-03-09T20:00:00+00:00", "2026-03-09"),
    ("2026-11-02T20:59:59+00:00", "2026-10-30"),  # After autumn DST.
    ("2026-11-02T21:00:00+00:00", "2026-11-02"),
    ("2026-11-07T12:00:00-05:00", "2026-11-06"),  # Weekend.
    ("2026-11-01T12:00:00-05:00", "2026-10-30"),  # DST Sunday.
    ("2026-11-26T12:00:00-05:00", "2026-11-25"),  # Full holiday.
    ("2026-11-27T12:59:59-05:00", "2026-11-25"),
    ("2026-11-27T13:00:00-05:00", "2026-11-27"),  # Early close.
    ("2026-12-24T18:00:00+00:00", "2026-12-24"),
    ("2026-11-03T01:00:00+00:00", "2026-11-02"),  # UTC and ET dates differ.
])
def test_completed_basis_uses_actual_session_close(instant, expected):
    now = datetime.fromisoformat(instant)
    assert _completed_basis_date(now) == expected
    assert _completed_basis_date(now.astimezone(ET)) == expected


def _frame(dates):
    return pd.DataFrame({"Open": 100.0, "High": 102.0, "Low": 99.0,
                         "Close": 101.0, "Volume": 1000.0}, index=pd.to_datetime(dates))


@pytest.mark.parametrize(("instant", "expected_basis", "expected_rows"), [
    ("2026-11-27T12:59:59-05:00", "2026-11-25", ["2026-11-25"]),
    ("2026-11-27T13:00:00-05:00", "2026-11-27", ["2026-11-25", "2026-11-27"]),
    ("2026-11-27T14:00:00-05:00", "2026-11-27", ["2026-11-25", "2026-11-27"]),
])
def test_provider_retains_early_close_but_excludes_partial_and_future_rows(
        instant, expected_basis, expected_rows):
    provider = _BanbanMarketDataTrader(now=datetime.fromisoformat(instant),
        expected_basis_date=date.fromisoformat(expected_basis), initial_capital=1)
    original = _frame(["2026-11-25", "2026-11-27", "2026-11-30"])
    before = original.copy(deep=True)
    result = provider._drop_unfinished_daily_bar(original)
    assert result.index.strftime("%Y-%m-%d").tolist() == expected_rows
    pd.testing.assert_frame_equal(original, before)


def test_provider_excludes_partial_normal_session_bar():
    provider = _BanbanMarketDataTrader(now=datetime(2026, 11, 3, 15, 59, tzinfo=ET),
        expected_basis_date=date(2026, 11, 2), initial_capital=1)
    result = provider._drop_unfinished_daily_bar(_frame(["2026-11-02", "2026-11-03"]))
    assert result.index.strftime("%Y-%m-%d").tolist() == ["2026-11-02"]
