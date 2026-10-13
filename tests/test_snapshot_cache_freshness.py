"""A historical rules query must not make a fresh plan reuse Friday-only data."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from kmw_order_plan import _KMWPlanTrader


def test_current_plan_refuses_weekly_safe_cache_after_a_historical_query():
    friday = pd.DataFrame({"Close": [600.0]}, index=pd.to_datetime(["2026-10-09"]))
    monday = pd.DataFrame({"Close": [600.0, 602.0]}, index=pd.to_datetime(["2026-10-09", "2026-10-12"]))
    zone = ZoneInfo("America/New_York")
    historical = _KMWPlanTrader(now=datetime(2026, 10, 12, 8, tzinfo=zone), expected_basis_date=date(2026, 10, 9))
    current = _KMWPlanTrader(now=datetime(2026, 10, 13, 8, tzinfo=zone), expected_basis_date=date(2026, 10, 12))
    for ticker in ["QQQ", "SOXL"]:
        assert historical._stock_data_covers_required_market_date(ticker, friday)
        assert not current._stock_data_covers_required_market_date(ticker, friday)
        assert current._stock_data_covers_required_market_date(ticker, monday)
