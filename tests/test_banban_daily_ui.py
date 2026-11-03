"""Recommendation refresh behavior across open tabs, dates, and data recovery."""
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import soxl_quant_system as strategy
from test_banban_ui import sample_report


WORKSPACE = """
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
st.session_state.active_preset = 'KMW3'
show_workspace(DEFAULT_KMW3_CONFIG)
"""


def dated_report(basis, next_session, quantity):
    report = sample_report()
    report["end_date"] = report["snapshot"]["as_of_date"] = report["daily"][0]["date"] = basis
    report["order_preview"]["session_date"] = next_session
    report["order_preview"]["R2"][0]["quantity"] = quantity
    return report


def test_open_workspace_registers_periodic_refresh_and_rolls_to_new_session():
    script = """
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
st.session_state.active_preset = 'KMW3'
ctx = get_script_run_ctx()
original_enqueue = ctx.enqueue
st.session_state._refresh_intervals = []
def capture(message, *args, **kwargs):
    if message.HasField('auto_rerun'):
        st.session_state._refresh_intervals.append(message.auto_rerun.interval)
    return original_enqueue(message, *args, **kwargs)
ctx.enqueue = capture
try:
    show_workspace(DEFAULT_KMW3_CONFIG)
finally:
    ctx.enqueue = original_enqueue
"""
    reports = {"2026-10-20": dated_report("2026-10-20", "2026-10-21", 10),
               "2026-10-21": dated_report("2026-10-21", "2026-10-22", 20)}
    basis = ["2026-10-20"]

    def load(config_json, basis_date):
        return deepcopy(reports[basis_date])

    with patch("banban_ui._completed_basis_date", side_effect=lambda: basis[0]), \
            patch("banban_ui._load_completed_replay", side_effect=load) as loader:
        app = AppTest.from_string(script).run(timeout=20)
        assert not app.exception and not app.error
        assert any(0 < interval <= 60 for interval in app.session_state._refresh_intervals)
        assert any("2026-10-21" in caption.value for caption in app.caption)
        basis[0] = "2026-10-21"
        app.run(timeout=20)

    assert not app.exception and not app.error
    assert [call.args[1] for call in loader.call_args_list] == ["2026-10-20", "2026-10-21"]
    assert app.session_state.positions_snapshot["as_of_date"] == "2026-10-21"
    order_tables = [frame.value for frame in app.dataframe if "유형" in frame.value.columns]
    assert order_tables[1]["수량"].tolist() == [20]
    assert any("2026-10-22" in caption.value for caption in app.caption)
    assert not any("미국 주문일 2026-10-21" in caption.value for caption in app.caption)


def test_manual_refresh_clears_strategy_quotes_and_failure_cooldowns():
    now = datetime.now()
    cache = {"SOXL_15y": ("old-soxl", now), "QQQ_15y": ("old-qqq", now),
             "AAPL_1mo": ("unrelated", now)}
    cooldowns = {key: now + timedelta(minutes=1) for key in cache}
    seen = []

    def load(config):
        seen.append((deepcopy(strategy._SHARED_STOCK_DATA_CACHE), deepcopy(strategy._SHARED_STOCK_DATA_RETRY_AFTER)))
        return sample_report()

    with patch.dict(strategy._SHARED_STOCK_DATA_CACHE, cache, clear=True), \
            patch.dict(strategy._SHARED_STOCK_DATA_RETRY_AFTER, cooldowns, clear=True), \
            patch("banban_ui._completed_basis_date", return_value="2026-10-20"), \
            patch("banban_ui.load_replay", side_effect=load):
        app = AppTest.from_string(WORKSPACE).run(timeout=20)
        next(button for button in app.button if button.key == "kmw3_refresh").click().run(timeout=20)

    assert not app.exception and not app.error
    assert len(seen) == 2
    quotes, retry_after = seen[-1]
    assert "SOXL_15y" not in quotes and "QQQ_15y" not in quotes
    assert "SOXL_15y" not in retry_after and "QQQ_15y" not in retry_after
    assert quotes["AAPL_1mo"] == cache["AAPL_1mo"]
    assert retry_after["AAPL_1mo"] == cooldowns["AAPL_1mo"]


def test_periodic_retry_recovers_recommendations_after_initial_data_failure():
    from banban_ui import BanbanMarketDataError

    failure = BanbanMarketDataError("DAILY_DATA_UNAVAILABLE", "SOXL 확정 일봉 확인 대기")
    with patch("banban_ui._completed_basis_date", return_value="2026-10-20"), \
            patch("banban_ui._load_completed_replay", side_effect=[failure, sample_report()]) as loader, \
            patch("requests.Session.request", side_effect=AssertionError("No network")):
        app = AppTest.from_string(WORKSPACE).run(timeout=20)
        assert not app.exception and not app.error
        assert not app.metric
        waiting = [frame.value for frame in app.dataframe if "유형" in frame.value.columns]
        assert len(waiting) == 2
        assert all(frame["매매"].tolist() == ["대기"] for frame in waiting)
        app.run(timeout=20)

    assert not app.exception and not app.error
    assert loader.call_count == 2
    assert app.session_state.positions_snapshot["as_of_date"] == "2026-10-20"
    order_tables = [frame.value for frame in app.dataframe if "유형" in frame.value.columns]
    assert order_tables[1]["수량"].tolist() == [10]
    assert order_tables[1]["기준가격"].tolist() == ["$101.50"]


def test_registered_workspace_cannot_refresh_after_switching_to_another_preset():
    script = """
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
if 'active_preset' not in st.session_state:
    st.session_state.active_preset = 'KMW3'
show_workspace(DEFAULT_KMW3_CONFIG)
"""
    account_snapshot = {"available_cash": 1234.0, "as_of_date": "2026-10-20",
                        "1_2026-10-20": {"shares": 5, "buy_price": 100.0}}
    with patch("banban_ui.load_replay", return_value=sample_report()) as loader:
        app = AppTest.from_string(script).run(timeout=20)
        assert not app.exception and not app.error
        loader.assert_called_once()
        loader.reset_mock()
        app.session_state.active_preset = "KMW"
        app.session_state.positions_snapshot = deepcopy(account_snapshot)
        app.run(timeout=20)

    assert not app.exception and not app.error
    loader.assert_not_called()
    assert app.session_state.positions_snapshot == account_snapshot
    assert not any("KMW3" in heading.value for heading in app.subheader)
    assert not app.dataframe
