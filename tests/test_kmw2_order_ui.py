"""Live KMW2 recommendations share KMW's layout without inventing executions."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from streamlit.testing.v1 import AppTest

from kmw2_order_ui import dongpa_order_table


SCRIPT = """
import streamlit as st
from kmw2_order_ui import show_order_recommendations
show_order_recommendations(st.session_state.tables,
    waiting=st.session_state.waiting, signal_available=st.session_state.signal_available)
"""


def render(tables, *, waiting=False, signal_available=True):
    app = AppTest.from_string(SCRIPT)
    app.session_state.tables = deepcopy(tables)
    app.session_state.waiting = waiting
    app.session_state.signal_available = signal_available
    before = deepcopy(tables)
    with patch("requests.Session.request", side_effect=AssertionError("Display must not use the network")):
        app.run(timeout=20)
    assert not app.exception
    assert app.session_state.tables == before
    return app


def text(block):
    return "\n".join(element.value for kind in ("subheader", "success", "warning", "info", "caption")
                     for element in block.get(kind))


def tables():
    return {
        "ATR": {"order_date": "2026-11-12", "message": "ATR 돌파 조건을 확인합니다.", "rows": [
            {"매매": "매수", "유형": "MARKET", "기준가격": "시장가 · 시가 확정 후 산정",
             "수량": "시가 확정 후 산정", "조건": "시가 × 1.015 돌파", "상태": "돌파 대기", "기존주문": False}]},
        "R2": {"order_date": "2026-11-12", "message": "R2 별도 예산", "rows": [
            {"매매": "매수", "회차": 1, "종목": "SOXL", "유형": "LOC", "기준가격": "$68.34",
             "수량": 26, "조건": "종가 조건", "상태": "종가 조건 대기", "모드": "SF", "기존주문": False},
            {"매매": "매도", "회차": 2, "유형": "MOC", "기준가격": "종가 시장가", "수량": 3,
             "조건": "보유 기한 도달", "상태": "조건 대기", "매수일": "2026-11-05", "기존주문": False}]},
    }


def test_buy_sell_columns_put_known_r2_before_pending_atr_without_inventing_market_price():
    app = render(tables())
    assert [element.value for element in app.subheader] == [
        "📋 주문일 기준 매매 추천", "🟢 매수 추천", "🔴 매도 추천"]
    buy, sell = app.columns
    buy_text, sell_text = text(buy), text(sell)
    assert "R2 · 1회차 매수 추천" in buy_text and "$68.34" in buy_text and "26주" in buy_text
    assert "ATR70 · 매수 조건 확인 대기" in buy_text
    assert not any("ATR70" in item.value for item in buy.get("success"))
    assert "매수 추천: 1건" in buy_text and "시가 확정 후 산정" in buy_text
    assert "R2 · 2회차 매도 추천" in sell_text and "종가 시장가" in sell_text
    assert "보유 기한 도달" in sell_text and "2026-11-05" in sell_text
    assert not app.button and not app.number_input


def test_saved_partial_and_uncertain_orders_keep_status_and_are_not_counted_as_recommendations():
    value = tables()
    value["R2"]["rows"] = [
        {"매매": "매수", "유형": "LOC", "기준가격": "$60.00", "수량": 10,
         "조건": "원래 주문 유지", "상태": "부분체결 · 체결 3/10주", "기존주문": True},
        {"매매": "매수", "유형": "MARKET", "기준가격": "시장가 · 예산 기준 $70.00", "수량": 4,
         "가격설명": "예산 기준 $70.00", "상태": "접수 여부 확인 필요", "기존주문": True}]
    app = render(value, signal_available=False)
    buy = text(app.columns[0])
    assert "저장된 매수 주문: 2건" in buy and "신규 추천 건수에 포함하지 않으며" in buy
    assert "부분체결 · 체결 3/10주" in buy and "접수 여부 확인 필요" in buy
    assert "시장가 · 예산 기준 $70.00" in buy
    assert "매수금액" not in buy and "$280" not in buy
    assert not app.success
    assert "26주" not in buy and "$68.34" not in buy


def test_empty_waiting_and_unavailable_views_always_keep_both_sides_and_strategy_reasons():
    value = {"ATR": {"rows": [], "message": "변동성 필터 미충족"},
             "R2": {"rows": [], "message": "가용 회차 없음"}}
    for waiting, available in ((False, True), (True, True), (False, False)):
        app = render(value, waiting=waiting, signal_available=available)
        for column, heading in zip(app.columns, ("🟢 매수 추천", "🔴 매도 추천")):
            shown = text(column)
            assert heading in shown
            if heading == "🟢 매수 추천" or waiting or not available:
                assert "ATR70: 변동성 필터 미충족" in shown and "R2: 가용 회차 없음" in shown
            else:
                assert "ATR70: 현재 매도 주문안이 없습니다." in shown
                assert "변동성 필터 미충족" not in shown and "가용 회차 없음" not in shown
        assert not app.success


def test_waiting_state_never_shows_supplied_new_order_as_a_recommendation():
    app = render(tables(), waiting=True)
    shown = text(app)
    assert "실계좌 시작 조건 확인 대기" in shown
    assert "$68.34" not in shown and "26주" not in shown
    assert not app.success


@pytest.mark.parametrize("waiting,signal_available", [(False, True), (False, False), (True, True)])
def test_saved_buy_and_sell_orders_never_show_the_empty_recommendation_notice(waiting, signal_available):
    value = {"ATR": {"rows": []}, "R2": {"rows": [
        {"매매": "매수", "유형": "LOC", "기준가격": "$67.72", "수량": 69,
         "상태": "접수 확인 · 체결 대기", "기존주문": True},
        {"매매": "매도", "유형": "LOC", "기준가격": "$66.25", "수량": 26,
         "상태": "부분체결 · 체결 3/26주", "기존주문": True}]}}
    app = render(value, waiting=waiting, signal_available=signal_available)
    for side, column in zip(("매수", "매도"), app.columns):
        shown = text(column)
        assert f"저장된 {side} 주문: 1건" in shown
        assert f"현재 {side} 추천이 없습니다." not in shown
        assert f"{side} 추천 확인 대기" not in shown
        assert not column.get("success")
    assert "69주" in text(app.columns[0])
    assert "부분체결 · 체결 3/26주" in text(app.columns[1])


def dongpa_plan():
    return {"order_date": "2026-11-16", "mode": "AG", "buy_entry_rules": {"mode": "AG"}, "orders": [
        {"ticker": "SOXL", "side": "BUY", "round": 2, "quantity": 5, "price": "71.20", "order_type": "LOC"},
        {"ticker": "SOXL", "side": "SELL", "round": 1, "quantity": 3, "price": "0", "order_type": "MOC",
         "position_key": "1_2026-11-12"}]}


def test_dongpa_table_preserves_supplied_plan_terms_and_entry_mode_without_mutating_sources():
    plan = dongpa_plan()
    source = {"available_cash": 8750, "1_2026-11-12": {"mode": "SF", "buy_price": 68.5,
                                                       "shares": 3, "sell_threshold": 1.5}}
    original = deepcopy(plan), deepcopy(source)
    with patch("requests.Session.request", side_effect=AssertionError("Presentation cannot query accounts")):
        table = dongpa_order_table(plan, source)
    buy, sell = table["rows"]
    assert (buy["회차"], buy["수량"], buy["기준가격"], buy["모드"]) == (2, 5, "$71.20", "AG")
    assert sell["유형"] == "MOC" and sell["기준가격"] == "종가 시장가" and sell["모드"] == "SF"
    assert sell["매수일"] == "2026-11-12" and "보유 만기" in sell["조건"]
    assert all(row["기존주문"] is False and "표시용 주문안" in row["상태"] for row in table["rows"])
    assert (plan, source) == original


def test_dongpa_and_carried_atr_r2_are_separate_cards_in_the_same_buy_sell_columns():
    source = {"1_2026-11-12": {"mode": "SF", "buy_price": 68.5, "shares": 3}}
    values = {"DONGPA": dongpa_order_table(dongpa_plan(), source),
              "ATR": {"rows": [{"매매": "매도", "유형": "MOO", "기준가격": "시가 시장가", "수량": 4,
                                 "조건": "원래 ATR 익일 시가 청산", "상태": "조건 확인 대기", "기존주문": False}]},
              "R2": {"rows": [{"매매": "매도", "회차": 2, "유형": "LOC", "기준가격": "$102.00", "수량": 10,
                                "조건": "진입 당시 R2 목표가 유지", "상태": "조건 확인 대기", "기존주문": False}]}}
    app = render(values)
    buy, sell = map(text, app.columns)
    assert "기본 동파 · 2회차 매수 추천" in buy and "$71.20" in buy and "5주" in buy
    assert "ATR70" not in "\n".join(item.value for item in app.columns[0].get("success"))
    assert "기본 동파 · 1회차 매도 추천" in sell and "종가 시장가" in sell
    assert "ATR70 매도 추천" in sell and "시가 시장가" in sell and "4주" in sell
    assert "R2 · 2회차 매도 추천" in sell and "$102.00" in sell and "10주" in sell
    assert "진입 당시 R2 목표가 유지" in sell and "매도 추천: 3건" in sell


def test_dongpa_table_respects_empty_reselected_on_plan_and_stale_signal_instead_of_inventing_buy():
    source = {"available_cash": 8750}
    plan = dongpa_plan()
    plan["orders"] = [order for order in plan["orders"] if order["side"] == "SELL"]
    assert all(row["매매"] == "매도" for row in dongpa_order_table(plan, source)["rows"])
    assert dongpa_order_table(dongpa_plan(), source, signal_available=False)["rows"] == []
    values = {"DONGPA": dongpa_order_table(dongpa_plan(), source, signal_available=False),
              "R2": {"rows": [{"매매": "매도", "유형": "LOC", "기준가격": "$102.00", "수량": 10,
                                "상태": "저장된 주문 · 접수 여부 확인 필요", "기존주문": True}]}}
    app = render(values, signal_available=False)
    assert not app.success and "$71.20" not in text(app)
    assert "저장된 매도 주문" in text(app.columns[1]) and "접수 여부 확인 필요" in text(app.columns[1])
