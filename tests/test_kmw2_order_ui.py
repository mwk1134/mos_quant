"""Live KMW2 recommendations share KMW's layout without inventing executions."""
from copy import deepcopy
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


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
    assert "저장된 매수 주문 2건" in buy and "신규 추천 건수에 포함하지 않습니다" in buy
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
