"""Mixed dashboard tables rendered in an offline fake DOM; no order calls."""
import pytest

from kmw_engine_dashboard import _HTML
from test_dashboard_transport import NODE, STATUS, frontend

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed for offline dashboard rendering")


def render(plan):
    status = {**STATUS, "preset": "KMW2", "plan": plan}
    code = """return {
      mixedHidden:$('mixed-orders').hidden,legacyHidden:$('legacy-orders').hidden,
      atr:$('atr-orders').children.map(row=>row.children.map(cell=>cell.textContent)),
      r2:$('r2-orders').children.map(row=>row.children.map(cell=>cell.textContent)),
      legacy:$('orders').children.map(row=>row.children.map(cell=>cell.textContent)),
      atrStatus:$('atr-order-status').textContent,r2Status:$('r2-order-status').textContent,
      atrMessage:$('atr-order-message').textContent,r2Message:$('r2-order-message').textContent,
      mixedNoteHidden:$('mixed-execution-note').hidden,legacyTimingHidden:$('legacy-timing-note').hidden,
      summary:$('plan-status').textContent};"""
    result = frontend(code, initial_response={"json": status}, preset="KMW2")
    assert result["calls"] == []
    return result["result"]


def order(strategy, kind, side="BUY", **changes):
    return {"strategy": strategy, "order_type": kind, "side": side, "ticker": "SOXL",
            "round": 1, "quantity": 3, "price": "105.00", **changes}


def mixed_plan(**changes):
    return {"preset": "KMW2", "status": "ready", "atr70_r2_managed": True,
            "orders": [], "warnings": [], **changes}


def test_both_strategies_have_distinct_tables_and_market_price_is_not_reserve_price():
    atr, r2 = order("ATR", "MARKET", price="113.30"), order("R2", "LOC")
    ui = render(mixed_plan(orders=[atr, r2]))
    assert ui["mixedHidden"] is False and ui["legacyHidden"] is True
    assert ui["atr"][0][:6] == ["매수", "SOXL", 1, 3, "MARKET", "시장가"]
    assert ui["r2"][0][:6] == ["매수", "SOXL", 1, 3, "LOC", "105.00"]
    assert "113.30" not in str(ui["atr"])
    assert "ATR70 주문표" in _HTML and "R2 주문표" in _HTML
    assert ui["mixedNoteHidden"] is False and ui["legacyTimingHidden"] is True


def test_static_waiting_atr_row_does_not_invent_an_executable_quantity():
    atr = order("ATR", "MARKET", quantity=None, price=None, display_only=True,
                condition="정규장 시가의 1.015배 돌파", status="조건 대기")
    r2 = order("R2", "LOC", quantity=8)
    tables = {"ATR": {"orders": [atr], "status": "돌파 감시", "message": "정규장 체결을 기다립니다."},
              "R2": {"orders": [r2], "status": "신규 매수", "message": "R2 독립 예산"}}
    ui = render(mixed_plan(orders=[r2], strategy_order_tables=tables))
    assert ui["atr"][0][3] == "조건 충족 후 산정"
    assert ui["atr"][0][5] == "시장가"
    assert "1.015" in ui["atr"][0][6]
    assert ui["r2"][0][3] == 8
    assert ui["atrStatus"] == "돌파 감시" and ui["r2Status"] == "신규 매수"
    assert ui["atrMessage"] == "정규장 체결을 기다립니다."


def test_moo_moc_prices_and_empty_strategy_are_always_visible():
    ui = render(mixed_plan(orders=[order("ATR", "MOO", "SELL", price="0"),
                                  order("R2", "MOC", "SELL", price="0")]))
    assert ui["atr"][0][5] == "시가" and ui["r2"][0][5] == "종가"
    empty = render(mixed_plan())
    assert empty["atr"] == [["현재 ATR70 주문이 없습니다."]]
    assert empty["r2"] == [["현재 R2 주문이 없습니다."]]
    assert empty["mixedHidden"] is False


def test_switch_off_keeps_previously_accepted_buy_visible():
    accepted = order("R2", "LOC", existing_order=True, status="ACCEPTED", broker_order_id="202")
    ui = render(mixed_plan(mixed_enabled=False, strategy_order_tables={
        "ATR": {"orders": [], "status": "신규 매수 중지"},
        "R2": {"orders": [accepted], "status": "기존 접수 주문"}}))
    assert ui["r2"][0][0] == "매수"
    assert "주문 접수 완료" in ui["r2"][0][6]
    assert "주문번호 202" in ui["r2"][0][6]
    assert ui["r2Status"] == "기존 접수 주문"


def test_legacy_preserves_its_original_single_table_and_timing_note():
    legacy = {"preset": "KMW2", "status": "ready", "orders": [order("R2", "LOC")], "warnings": []}
    ui = render(legacy)
    assert ui["legacyHidden"] is False and ui["mixedHidden"] is True
    assert ui["legacy"][0] == ["매수", "SOXL", 1, 3, "LOC", "105.00"]
    assert ui["mixedNoteHidden"] is True and ui["legacyTimingHidden"] is False
