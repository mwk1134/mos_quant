"""Exercise the real Yahoo adapter and replay with transport-only test doubles."""
from copy import deepcopy
import json
import math
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from banban_research.atr_sessions import exchange_calendar
from banban_strategy import DEFAULT_KMW3_CONFIG, replay
from banban_ui import BanbanMarketDataError, _load_completed_replay
import soxl_quant_system as strategy


def yahoo_history(symbol, dates, *, granularity="1d"):
    quotes = {key: [] for key in ("open", "high", "low", "close", "volume")}
    for index, _ in enumerate(dates):
        base = (100 + index * .025 + 2 * math.sin(index / 10)) if symbol == "SOXL" else (400 + index * .07 + 15 * math.sin(index / 16))
        for key, value in {"open": base, "high": base * 1.03, "low": base * .98,
                           "close": base * 1.002, "volume": 1000000}.items():
            quotes[key].append(value)
    timestamps = [int(pd.Timestamp(day).tz_localize("America/New_York").replace(hour=9, minute=30).timestamp())
                  for day in dates]
    return {"chart": {"result": [{"meta": {"dataGranularity": granularity},
                                   "timestamp": timestamps, "indicators": {"quote": [quotes]}}]}}


def test_completed_replay_requests_full_daily_history_when_yahoo_max_is_monthly():
    dates = exchange_calendar().sessions_in_range("2010-03-11", "2026-11-02").strftime("%Y-%m-%d").tolist()
    daily = {symbol: yahoo_history(symbol, dates) for symbol in ("SOXL", "QQQ")}
    monthly = {symbol: yahoo_history(symbol, dates[::21], granularity="1mo") for symbol in daily}
    calls = []

    def fake_get(url, **kwargs):
        assert url.startswith(("https://query1.finance.yahoo.com/v8/finance/chart/",
                               "https://query2.finance.yahoo.com/v8/finance/chart/"))
        symbol = url.rsplit("/", 1)[-1]
        params = kwargs["params"]
        calls.append((symbol, deepcopy(params)))
        response = Mock(status_code=200)
        response.json.return_value = monthly[symbol] if params.get("range") == "max" else daily[symbol]
        return response

    with patch.dict(strategy._SHARED_STOCK_DATA_CACHE, {}, clear=True), \
            patch.dict(strategy._SHARED_STOCK_DATA_RETRY_AFTER, {}, clear=True), \
            patch("requests.get", side_effect=fake_get), \
            patch("requests.Session.request", side_effect=AssertionError("No real transport")), \
            patch("requests.put", side_effect=AssertionError("No account writes")), \
            patch("banban_ui.replay", wraps=replay) as real_replay:
        result = _load_completed_replay.__wrapped__(json.dumps(DEFAULT_KMW3_CONFIG), "2026-11-02")

    assert {symbol for symbol, _ in calls} == {"SOXL", "QQQ"}
    assert all(params.get("period1") == 0 and params.get("period2") and params["interval"] == "1d"
               for _, params in calls)
    market = real_replay.call_args.args[0]
    for symbol in ("SOXL", "QQQ"):
        frame = market[symbol]
        assert frame.index.strftime("%Y-%m-%d").tolist() == dates
    assert result["end_date"] == "2026-11-02"
    assert result["daily"][0]["date"] == "2026-10-20"
    assert result["daily"][-1]["date"] == "2026-11-02"
    assert result["order_preview"]["session_date"] == "2026-11-03"
    assert result["order_preview"]["ATR"] and result["order_preview"]["R2"]
    assert result["snapshot"]["simulation"] is True


@pytest.mark.parametrize("shortened_symbol", ["SOXL", "QQQ"])
def test_completed_replay_rejects_shortened_history_before_strategy_calculation(shortened_symbol):
    dates = exchange_calendar().sessions_in_range("2010-03-11", "2026-11-02")
    full = pd.DataFrame({"Open": 100.0, "High": 103.0, "Low": 98.0,
                         "Close": 101.0, "Volume": 1000000}, index=dates)
    frames = {symbol: full.copy() for symbol in ("SOXL", "QQQ")}
    frames[shortened_symbol] = full[full.index >= "2011-01-01"].copy()

    def get_history(provider, symbol, period):
        assert period == "15y"
        return frames[symbol]

    with patch("kmw_order_plan._KMWPlanTrader.get_stock_data", autospec=True,
               side_effect=get_history) as get_data, \
            patch("requests.get", side_effect=AssertionError("No network")), \
            patch("banban_ui.replay", side_effect=AssertionError("Do not calculate from another anchor")) as calculate:
        with pytest.raises(BanbanMarketDataError) as failure:
            _load_completed_replay.__wrapped__(json.dumps(DEFAULT_KMW3_CONFIG), "2026-11-02")

    assert failure.value.code == "INCOMPLETE_HISTORY"
    assert "전체 일봉 이력" in str(failure.value)
    assert {call.args[1] for call in get_data.call_args_list} == {"SOXL", "QQQ"}
    calculate.assert_not_called()
