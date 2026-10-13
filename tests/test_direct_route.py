"""Direct LOC routing, receipts, and duplicate protection without live calls."""
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from kmw_autotrade import (
    OrderBlocked, OrderLedger, direct_order_window, main, notify_order_result,
    submit_plan, validate_plan,
)
from meritz_api import AmbiguousSubmission, BASE_URL, MeritzAPIError, MeritzClient
from telegram_notify import format_order_notification


NOW = datetime(2026, 10, 12, 17, 1, tzinfo=ZoneInfo("Asia/Seoul"))
RECEIPT = {"rsp_cd": "0001", "data": {"warn_cls_code": "0", "oder_no": 92152}}


def plan():
    return {
        "preset": "KMW", "status": "ready", "order_date": "2026-10-12",
        "generated_at": NOW.isoformat(), "basis_date": "2026-10-09",
        "expected_basis_date": "2026-10-09", "snapshot_date": "2026-10-09",
        "snapshot_fresh": True, "snapshot_positions": [], "warnings": [],
        "orders": [{"ticker": "SOXL", "side": "BUY", "quantity": 1,
                    "price": "144.79", "order_type": "LOC", "round": 6,
                    "position_key": "6_2026-10-12"}],
    }


def response(body):
    result = Mock()
    result.status_code = 200
    result.json.return_value = body
    return result


def test_direct_submission_posts_ordinary_endpoint_and_records_order_receipt(tmp_path):
    transport = Mock(spec=requests.Session)
    transport.request.side_effect = [
        response({"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 3600}),
        response(RECEIPT),
    ]
    client = MeritzClient("fake-key", "fake-secret", "aabbccddeeff", session=transport)
    ledger = OrderLedger(tmp_path / "orders.db")
    original = plan()
    unchanged = deepcopy(original)
    context = {}
    try:
        results = submit_plan(client, original, ledger, "account", "0066", context, order_route="direct")
        assert transport.request.call_args.args == ("POST", BASE_URL + "/trading/v1/overseas/orders/buy")
        assert transport.request.call_args.kwargs["json"]["oder_cls_code"] == "32"
        assert results[0]["state"] == ledger.rows("account")[0]["state"] == "DIRECT_ACCEPTED"
        assert context["order_route"] == "direct"
        assert original == unchanged
        text = format_order_notification(original, results)
        assert "LOC 주문 접수 완료" in text and "주문번호 92152" in text
        assert "예약번호" not in text and "예약 접수" not in text
        assert "체결 완료 알림이 아닙니다" in text
    finally:
        ledger.db.close()


def test_order_route_change_cannot_resend_same_identity(tmp_path):
    client = Mock()
    client.submit_order.return_value = RECEIPT
    ledger = OrderLedger(tmp_path / "orders.db")
    try:
        submit_plan(client, plan(), ledger, "account", "0066", order_route="direct")
        with pytest.raises(OrderBlocked, match="already attempted"):
            submit_plan(client, plan(), ledger, "account", "0066", order_route="reserved")
        client.submit_reserved_order.assert_not_called()
        assert client.submit_order.call_count == 1
    finally:
        ledger.db.close()


def test_direct_unknown_stops_batch_and_does_not_fall_back_to_reservation(tmp_path):
    client = Mock()
    client.submit_order.side_effect = AmbiguousSubmission("unconfirmed")
    ledger = OrderLedger(tmp_path / "orders.db")
    p = plan()
    p["orders"].append({**p["orders"][0], "round": 7, "position_key": "7_2026-10-12"})
    context = {}
    try:
        with pytest.raises(AmbiguousSubmission):
            submit_plan(client, p, ledger, "account", "0066", context, order_route="direct")
        assert [row["state"] for row in ledger.rows("account")] == ["UNKNOWN", "NOT_SENT"]
        assert context["not_sent_count"] == 1
        client.submit_reserved_order.assert_not_called()
        with pytest.raises(OrderBlocked, match="unknown result"):
            ledger.unresolved("account")
        text = format_order_notification(p, [], error=RuntimeError(), failed_order=p["orders"][0],
                                         failed_state="UNKNOWN", order_route="direct")
        assert "LOC 주문 접수 여부 불확실" in text
        assert "예약" not in text
    finally:
        ledger.db.close()


def test_direct_rejection_never_falls_back_to_reservation(tmp_path):
    client = Mock()
    client.submit_order.side_effect = MeritzAPIError("rejected", response={"rsp_cd": "0760"})
    ledger = OrderLedger(tmp_path / "orders.db")
    try:
        with pytest.raises(MeritzAPIError):
            submit_plan(client, plan(), ledger, "account", "0066", order_route="direct")
        assert ledger.rows("account")[0]["state"] == "REJECTED"
        client.submit_reserved_order.assert_not_called()
    finally:
        ledger.db.close()


def test_direct_gate_uses_market_day_and_does_not_apply_reservation_cutoff():
    late_premarket = NOW.replace(hour=22, minute=0)  # 09:00 ET, beyond reservation cutoff.
    with patch("kmw_autotrade.reservation_window", side_effect=AssertionError("reservation gate called")):
        validate_plan(plan(), late_premarket, order_route="direct")
    direct_order_window(datetime(2026, 12, 1, 17, 1, tzinfo=NOW.tzinfo), "2026-12-01")
    with pytest.raises(OrderBlocked, match="pre-open"):
        direct_order_window(NOW.replace(hour=22, minute=30), "2026-10-12")
    with pytest.raises(OrderBlocked, match="trading session"):
        direct_order_window(NOW, "2026-10-13")
    with pytest.raises(OrderBlocked, match="trading session"):
        direct_order_window(datetime(2026, 10, 11, 17, 1, tzinfo=NOW.tzinfo), "2026-10-11")


def test_direct_notification_event_deduplicates_without_changing_order_state(tmp_path):
    client, notifier = Mock(), Mock()
    client.submit_order.return_value = RECEIPT
    notifier.send.return_value = 123
    ledger = OrderLedger(tmp_path / "orders.db")
    context = {"plan": plan()}
    try:
        results = submit_plan(client, context["plan"], ledger, "account", "0066", context, order_route="direct")
        context.update(results=results, completed=True)
        notify_order_result(ledger, notifier, context)
        notify_order_result(ledger, notifier, context)
        assert notifier.send.call_count == 1
        assert "LOC 주문 접수 완료" in notifier.send.call_args.args[0]
        assert ledger.rows("account")[0]["state"] == "DIRECT_ACCEPTED"
        assert ledger.db.execute("SELECT state FROM notifications").fetchall() == [("SENT",)]
    finally:
        ledger.db.close()


@pytest.mark.parametrize("route_arg, expected", [([], "direct"), (["--order-route", "reserved"], "reserved")])
def test_cli_passes_selected_route_to_preflight_and_submit(tmp_path, route_arg, expected):
    client = Mock()
    client.submit_order.return_value = RECEIPT
    client.submit_reserved_order.return_value = {"rsp_cd": "5620", "data": {"warn_cls_code": "0", "oder_rsev_mst_no": 99}}
    with patch("kmw_autotrade.ROOT", tmp_path), \
         patch("kmw_autotrade.load_snapshots", return_value=({"KMW": {}}, "test")), \
         patch("kmw_autotrade.build_kmw_plan", return_value=plan()), \
         patch("kmw_autotrade.account_id", return_value="account"), \
         patch("kmw_autotrade.MeritzClient.from_env", return_value=client), \
         patch("kmw_autotrade.preflight", return_value={"exchange_code": "0066"}) as check, \
         patch("kmw_autotrade.configured_notifier", return_value=None), \
         patch("kmw_autotrade.datetime", wraps=datetime) as frozen_clock:
        frozen_clock.now.side_effect = lambda tz=None: NOW.astimezone(tz) if tz else NOW
        assert main(["--submit", "--ledger", str(tmp_path / "orders.db"), *route_arg]) == 0
    assert check.call_args.kwargs["order_route"] == expected
    assert client.submit_order.call_count == (1 if expected == "direct" else 0)
    assert client.submit_reserved_order.call_count == (1 if expected == "reserved" else 0)
    ledger = OrderLedger(tmp_path / "orders.db")
    try:
        state = "DIRECT_ACCEPTED" if expected == "direct" else "RESERVED"
        assert ledger.rows("account")[0]["state"] == state
    finally:
        ledger.db.close()
