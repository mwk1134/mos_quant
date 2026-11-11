"""A missing daily close must never borrow a different session's quote."""
from datetime import date, datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from kmw_order_plan import _KMWPlanTrader
from tests.test_banban_market_data import yahoo_history
import soxl_quant_system as strategy


@pytest.mark.parametrize(("basis", "now", "tick", "accepted"), [
    ("2026-11-11", "2026-11-12T12:00:00-05:00", "2026-11-12T12:00:00-05:00", False),
    ("2026-11-11", "2026-11-11T16:05:00-05:00", "2026-11-11T16:00:00-05:00", True),
    ("2026-11-27", "2026-11-27T13:05:00-05:00", "2026-11-27T13:00:00-05:00", True),
    ("2026-11-27", "2026-11-27T13:05:00-05:00", "2026-11-27T16:00:00-05:00", False),
    ("2026-11-11", "2026-11-11T16:05:00-05:00", None, False),
])
def test_metadata_close_requires_same_completed_session(basis, now, tick, accepted):
    body = yahoo_history("SOXL", [basis])
    result = body["chart"]["result"][0]
    result["indicators"]["quote"][0]["close"][-1] = None
    result["meta"].update(regularMarketPrice=101, gmtoffset=-18000)
    if tick:
        result["meta"]["regularMarketTime"] = int(datetime.fromisoformat(tick).timestamp())
    response = Mock(status_code=200)
    response.json.return_value = body
    provider = _KMWPlanTrader(now=datetime.fromisoformat(now),
                              expected_basis_date=date.fromisoformat(basis), initial_capital=1)
    with patch.dict(strategy._SHARED_STOCK_DATA_CACHE, {}, clear=True), \
            patch.dict(strategy._SHARED_STOCK_DATA_RETRY_AFTER, {}, clear=True), \
            patch("requests.get", return_value=response), patch("time.sleep"):
        if accepted:
            frame = provider.get_stock_data("SOXL", "15y")
            assert frame.index[-1].date().isoformat() == basis
            assert frame.iloc[-1]["Close"] == 101
            assert pd.notna(frame.iloc[-1]["Close"])
        else:
            with pytest.raises(ValueError):
                provider.get_stock_data("SOXL", "15y")
