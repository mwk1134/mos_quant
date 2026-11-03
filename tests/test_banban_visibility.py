"""Daily orders remain prominent above collapsible portfolio information."""
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from test_banban_ui import sample_report


@pytest.mark.parametrize("historical", [False, True])
def test_daily_order_tables_precede_assets_and_collapsed_holdings(historical):
    report = sample_report()
    report["order_preview"]["ATR"][0]["status"] = "FILTER_BLOCKED"
    report["order_preview"]["R2"].insert(0, {"side": "SELL", "order_type": "LOC",
        "quantity": 200, "price": 110.0, "round": 1, "status": "CONDITIONAL"})
    stored = {**report["snapshot"], "banban_report": {key: value for key, value in report.items() if key != "snapshot"}}
    script = f"""
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
st.session_state.active_preset = 'KMW3'
st.session_state.positions_snapshot = {stored!r}
show_workspace(DEFAULT_KMW3_CONFIG)
"""
    loader_options = {"side_effect": ValueError("offline")} if historical else {"return_value": report}
    with patch("banban_ui.load_replay", **loader_options), \
            patch("banban_ui._completed_basis_date", return_value="2026-11-02"), \
            patch("requests.Session.request", side_effect=AssertionError("No network")):
        app = AppTest.from_string(script).run(timeout=20)

    assert not app.exception and not app.error
    today = next(tab for tab in app.tabs if tab.label == "📊 오늘의 매매")
    nodes = list(today)
    recommendation = next(index for index, node in enumerate(nodes)
                          if node.type == "subheader" and node.value == "매수·매도 추천")
    order_tables = [(index, node.value) for index, node in enumerate(nodes)
                    if node.type == "dataframe" and "유형" in node.value.columns]
    asset_metrics = [index for index, node in enumerate(nodes) if node.type == "metric"]
    assert len(order_tables) == 2
    assert asset_metrics
    assert recommendation < min(index for index, _ in order_tables)
    assert max(index for index, _ in order_tables) < min(asset_metrics)
    assert [node.value for node in nodes if node.type == "subheader"][:3] == [
        "매수·매도 추천", "ATR70 주문표", "R2 주문표"]

    portfolios = [node for node in nodes if node.type == "expander"
                  and any(child.type == "markdown" and "모의 보유분" in child.value for child in node)]
    assert len(portfolios) == 1
    assert portfolios[0].proto.expanded is False
    assert recommendation < nodes.index(portfolios[0])
    if historical:
        assert all(table["매매"].tolist() == ["대기"] for _, table in order_tables)
        assert all(table["기준가격"].tolist() == ["-"] for _, table in order_tables)
        assert all(table["수량"].tolist() == ["-"] for _, table in order_tables)
    else:
        assert order_tables[0][1]["매매"].tolist() == ["대기"]
        assert order_tables[1][1]["매매"].tolist() == ["매도", "매수"]
        assert order_tables[1][1]["수량"].tolist() == [200, 10]
