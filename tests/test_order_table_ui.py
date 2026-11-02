"""Responsive order presentation preserves facts and escapes all display input."""
from copy import deepcopy
from html.parser import HTMLParser
import re
from unittest.mock import patch

import pytest

from order_table_ui import ORDER_TABLE_CSS, render_order_table_html


class Parsed(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def row(**changes):
    result = {"매매": "매수", "유형": "LOC", "기준가격": "$100.00", "수량": 10,
              "조건·상태": "종가가 주문가 이하이면 매수 · 종가 조건 대기",
              "조건": "종가가 주문가 이하이면 매수", "상태": "종가 조건 대기", "기존주문": False}
    result.update(changes)
    return result


def table(rows=None, **changes):
    result = {"label": "R2 주문표", "message": "전략별 예산으로 별도 주문합니다.", "source": "최신 표시용 주문안",
              "order_date": "2026-11-02", "rows": [row()] if rows is None else rows}
    result.update(changes)
    return result


@pytest.mark.parametrize("strategy,label", [("ATR", "ATR70 주문표"), ("R2", "R2 주문표")])
def test_strategy_board_retains_identity_and_keeps_condition_and_status_separate(strategy, label):
    markup = render_order_table_html(table(label=label), strategy)
    parsed = Parsed(markup)
    section = next(attrs for tag, attrs in parsed.elements if tag == "section")
    assert section["data-order-strategy"] == strategy
    assert section["aria-label"] == label
    assert "조건" in parsed.text and "상태" in parsed.text
    assert "종가가 주문가 이하이면 매수" in parsed.text
    assert "종가 조건 대기" in parsed.text
    assert "$100.00" in parsed.text and "10" in parsed.text


def test_rendering_is_deterministic_and_has_no_account_or_input_side_effects():
    value = table(rows=[row(), row(**{"매매": "매도", "수량": 5})])
    before = deepcopy(value)
    with patch("requests.Session.request", side_effect=AssertionError("No brokerage or network allowed")), \
            patch("requests.get", side_effect=AssertionError("No network allowed")), \
            patch("builtins.open", side_effect=AssertionError("No file writes or reads allowed")):
        first = render_order_table_html(value, "R2")
        second = render_order_table_html(value, "R2")
    assert first == second and value == before
    assert not any(tag in {"script", "iframe", "input", "button", "form"} for tag, _ in Parsed(first).elements)


def test_all_user_or_broker_text_is_escaped_in_content_and_attributes():
    hostile = '<script>alert("unsafe")</script><img src=x onerror="unsafe"> & "quote"'
    value = table(rows=[row(**{"매매": hostile, "유형": hostile, "기준가격": hostile, "수량": hostile,
                               "조건": hostile, "상태": hostile})], label=hostile, message=hostile, source=hostile)
    markup = render_order_table_html(value, 'ATR" onmouseover="unsafe')
    parsed = Parsed(markup)
    assert "<script>" not in markup and "<img" not in markup
    assert "&lt;script&gt;" in markup and "&amp;" in markup and "&quot;" in markup
    assert not any(tag in {"script", "img"} for tag, _ in parsed.elements)
    assert not any(key.startswith("on") for _, attrs in parsed.elements for key in attrs)
    section = next(attrs for tag, attrs in parsed.elements if tag == "section")
    assert section["aria-label"] == hostile
    assert section["data-order-strategy"] == 'ATR" onmouseover="unsafe'
    assert hostile in parsed.text


def test_market_order_separates_budget_estimate_from_market_execution_price():
    markup = render_order_table_html(table(rows=[row(**{
        "유형": "MARKET", "기준가격": "시장가 · 예산 기준 $110.00", "가격설명": "예산 기준 $110.00",
        "조건": "정규장 돌파 확인", "상태": "접수 확인 · 체결 대기", "기존주문": True})]), "ATR")
    parsed = Parsed(markup)
    assert "시장가" in parsed.text
    assert "예산 기준 $110.00" in parsed.text
    assert "저장 주문" in parsed.text
    assert "주문안" not in parsed.text
    assert "$0" not in markup


@pytest.mark.parametrize("kind,description", [("MOO", "시가 시장가"), ("MOC", "종가 시장가")])
def test_open_and_close_market_orders_keep_their_execution_description(kind, description):
    markup = render_order_table_html(table(rows=[row(**{"매매": "매도", "유형": kind, "기준가격": description})]), "ATR")
    parsed = Parsed(markup)
    assert kind in parsed.text and description in parsed.text
    assert "$0" not in markup


def test_unknown_open_and_quantity_remain_pending_instead_of_becoming_zero():
    markup = render_order_table_html(table(rows=[row(**{
        "유형": "MARKET", "기준가격": "시장가 · 시가 확정 후 산정", "가격설명": "시가 확정 후 산정",
        "수량": "시가 확정 후 산정", "조건": "정규장 시가 × 1.015 이상 실시간 체결 확인",
        "상태": "시가·돌파 확인 대기"})], label="ATR70 주문표"), "ATR")
    parsed = Parsed(markup)
    assert "미정" in parsed.text and "시가 확정 후 산정" in parsed.text
    assert "시가·돌파 확인 대기" in parsed.text
    assert "$0" not in markup and "0주" not in markup


def test_existing_partial_buy_keeps_actual_order_quantity_and_us_order_date_visible():
    markup = render_order_table_html(table(rows=[row(**{
        "수량": 10, "기존주문": True, "조건": "저장된 주문 기록 · 접수와 체결은 별도 확인",
        "상태": "저장된 주문 · 부분체결 · 체결 3/10주 · 미국 주문일 2026-11-02"})],
        source="저장된 접수내역", message="스위치 OFF · 기접수 주문은 유지됩니다."), "R2")
    parsed = Parsed(markup)
    assert "저장 주문" in parsed.text and "10" in parsed.text
    assert any("부분체결" in text and "3/10주" in text and "2026-11-02" in text for text in parsed.text)
    assert "스위치 OFF · 기접수 주문은 유지됩니다." in parsed.text
    assert "저장된 접수내역" in parsed.text
    assert "주문안" not in parsed.text


@pytest.mark.parametrize("status", ["접수 확인 중", "접수 여부 확인 필요"])
def test_pending_or_unknown_saved_order_is_not_badged_as_confirmed_accepted(status):
    markup = render_order_table_html(table(rows=[row(**{"기존주문": True, "상태": status})]), "R2")
    parsed = Parsed(markup)
    assert "저장 주문" in parsed.text and status in parsed.text
    assert "기접수" not in parsed.text and "주문안" not in parsed.text


@pytest.mark.parametrize("message", ["기존 보유분 전량 매도 확인 대기", "전일 ATR 필터 미충족 · 신규 주문 없음",
                                    "최신 신호 확인 불가 · 저장된 접수내역만 표시", "스위치 OFF · 신규 매수 추천 제외"])
def test_empty_board_preserves_the_reason_for_no_new_order(message):
    markup = render_order_table_html(table(rows=[{"매매": "대기", "수량": "-", "조건·상태": message}], message=message), "ATR")
    parsed = Parsed(markup)
    assert message in parsed.text and "현재 주문이 없습니다." in parsed.text
    assert not any(tag == "article" for tag, _ in parsed.elements)


def test_multiple_lot_orders_are_not_summed_into_one_order():
    markup = render_order_table_html(table(rows=[row(**{"매매": "매도", "수량": 5000}),
                                                row(**{"매매": "매도", "수량": 6000})]), "R2")
    parsed = Parsed(markup)
    assert sum(tag == "article" for tag, _ in parsed.elements) == 2
    assert "매도 2건" in parsed.text
    assert "5,000" in parsed.text and "6,000" in parsed.text and "11,000" not in parsed.text


def test_long_messages_are_complete_and_mobile_styles_allow_wrapping_without_ellipsis():
    long_condition = "정규장 시가와 실제 체결을 확인한 뒤 전략별 예산으로 주문합니다. " * 80
    long_status = "접수 확인 필요: " + "매우긴주문식별자" * 80
    markup = render_order_table_html(table(rows=[row(**{"조건": long_condition, "상태": long_status})], message=long_condition), "ATR")
    parsed = Parsed(markup)
    assert long_condition in parsed.text and long_status in parsed.text
    assert re.search(r"@media\s*\([^)]*max-width\s*:\s*\d+px", ORDER_TABLE_CSS)
    assert re.search(r"@container\s*\([^)]*max-width\s*:\s*\d+px", ORDER_TABLE_CSS)
    assert "container-type:inline-size" in ORDER_TABLE_CSS
    assert "overflow-wrap:anywhere" in ORDER_TABLE_CSS
    assert "white-space:normal" in ORDER_TABLE_CSS
    assert "minmax(0,1fr)" in ORDER_TABLE_CSS
    assert "text-overflow:ellipsis" not in ORDER_TABLE_CSS
