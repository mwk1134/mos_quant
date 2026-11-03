"""Side-grouped daily recommendation cards precede collapsible portfolio information."""
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from test_banban_ui import sample_report, recommendation_groups, column_text, assert_waiting_recommendations


@pytest.mark.parametrize("historical", [False, True])
def test_daily_buy_sell_cards_precede_assets_and_collapsed_holdings(historical):
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
                          if node.type == "subheader" and node.value == "📋 주문일 기준 매매 추천")
    buy, sell = recommendation_groups(app)
    group_indices = [nodes.index(buy), nodes.index(sell)]
    asset_metrics = [index for index, node in enumerate(nodes) if node.type == "metric"]
    assert asset_metrics
    assert recommendation < min(group_indices)
    assert max(group_indices) < min(asset_metrics)
    assert [node.value for node in nodes if node.type == "subheader"][:3] == [
        "📋 주문일 기준 매매 추천", "🟢 매수 추천", "🔴 매도 추천"]

    portfolios = [node for node in nodes if node.type == "expander"
                  and any(child.type == "markdown" and "모의 보유분" in child.value for child in node)]
    assert len(portfolios) == 1
    assert portfolios[0].proto.expanded is False
    assert recommendation < nodes.index(portfolios[0])
    if historical:
        assert_waiting_recommendations(app)
        assert "$110.00" not in column_text(sell) and "200주" not in column_text(sell)
    else:
        assert "ATR70" in column_text(buy)
        assert "10주" in column_text(buy) and "200주" not in column_text(buy)
        assert "200주" in column_text(sell) and "10주" not in column_text(sell)
