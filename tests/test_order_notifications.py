from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kmw_autotrade import OrderBlocked, OrderLedger, main, run_once, submit_plan
from meritz_api import AmbiguousSubmission, MeritzAPIError
from telegram_notify import TelegramNotificationError


def plan():
    return {"preset": "KMW", "order_date": "2026-10-12", "generated_at": "2026-10-12T17:01:00+09:00",
            "orders": [{"ticker": "SOXL", "side": "BUY", "quantity": 1, "price": "144.79",
                        "order_type": "LOC", "round": 6, "position_key": "6_2026-10-12"}]}


def arguments(tmp_path, **updates):
    fields = dict(submit=True, test_notification=False, retry_notifications=False,
                  ledger=tmp_path / "orders.db")
    fields.update(updates)
    return SimpleNamespace(**fields)


def execute_callback(client, path, p=None, after_success_error=None):
    def execute(args, notification_context):
        p_plan = p or plan()
        notification_context["plan"] = p_plan
        ledger = OrderLedger(path)
        try:
            result = submit_plan(client, p_plan, ledger, "account", "0066", notification_context)
            notification_context.update(results=result, completed=True)
            if after_success_error:
                raise after_success_error
        finally:
            ledger.db.close()
    return execute


def accepted_client():
    client = Mock()
    client.submit_reserved_order.return_value = {"rsp_cd": "5620", "data": {"warn_cls_code": "0", "oder_rsev_mst_no": 123}}
    return client


def notification_rows(path):
    ledger = OrderLedger(path)
    try:
        return ledger.db.execute("SELECT state,message_id,text,last_error FROM notifications").fetchall()
    finally:
        ledger.db.close()


def test_accepted_orders_send_one_reservation_notification(tmp_path):
    args = arguments(tmp_path)
    client, notifier = accepted_client(), Mock()
    notifier.send.return_value = 77
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=execute_callback(client, args.ledger)):
        run_once(args)
    rows = notification_rows(args.ledger)
    assert rows[0][:2] == ("SENT", 77)
    assert "예약주문 접수 완료" in rows[0][2]
    assert "예약번호 123" in rows[0][2]
    assert "체결 완료 알림이 아닙니다" in rows[0][2]
    assert client.submit_reserved_order.call_count == notifier.send.call_count == 1


def test_notification_timeout_does_not_change_orders_and_retry_sends_only_messages(tmp_path, capsys):
    args = arguments(tmp_path)
    client, notifier = accepted_client(), Mock()
    notifier.send.side_effect = TelegramNotificationError("safe error", delivery_unknown=True)
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=execute_callback(client, args.ledger)):
        run_once(args)
    ledger = OrderLedger(args.ledger)
    assert ledger.rows("account")[0]["state"] == "RESERVED"
    assert notification_rows(args.ledger)[0][0] == "UNKNOWN"
    notifier.send.side_effect = None
    notifier.send.return_value = 78
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=AssertionError("order runner called")), \
         patch("kmw_autotrade.MeritzClient.from_env", side_effect=AssertionError("broker called")):
        run_once(arguments(tmp_path, submit=False, retry_notifications=True))
    assert notification_rows(args.ledger)[0][:2] == ("SENT", 78)
    assert ledger.rows("account")[0]["state"] == "RESERVED"
    assert client.submit_reserved_order.call_count == 1
    assert "safe error" not in capsys.readouterr().err


def test_unconfigured_notifications_remain_pending_for_later_setup(tmp_path):
    args = arguments(tmp_path)
    client = accepted_client()
    with patch("kmw_autotrade.configured_notifier", return_value=None), \
         patch("kmw_autotrade._run_once", side_effect=execute_callback(client, args.ledger)):
        run_once(args)
    assert notification_rows(args.ledger)[0][0] == "PENDING"
    assert OrderLedger(args.ledger).rows("account")[0]["state"] == "RESERVED"


def test_partial_submission_reports_success_and_unknown_without_resending(tmp_path):
    args = arguments(tmp_path)
    p = plan()
    p["orders"].append({**p["orders"][0], "side": "SELL", "round": 1, "position_key": "1_2026-10-05"})
    client, notifier = accepted_client(), Mock()
    client.submit_reserved_order.side_effect = [client.submit_reserved_order.return_value, AmbiguousSubmission("unconfirmed")]
    notifier.send.return_value = 79
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=execute_callback(client, args.ledger, p)):
        with pytest.raises(AmbiguousSubmission):
            run_once(args)
    text = notification_rows(args.ledger)[0][2]
    assert "접수 여부 불확실" in text
    assert "예약번호 123" in text
    assert "불확실: 매도" in text
    assert [r["state"] for r in OrderLedger(args.ledger).rows("account")] == ["RESERVED", "UNKNOWN"]
    assert client.submit_reserved_order.call_count == 2


def test_preflight_failure_sends_no_false_success_or_raw_error(tmp_path):
    args = arguments(tmp_path)
    notifier = Mock()
    notifier.send.return_value = 80
    def stopped(args, notification_context):
        notification_context["plan"] = plan()
        raise OrderBlocked("do not forward this private detail")
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=stopped):
        with pytest.raises(OrderBlocked):
            run_once(args)
    text = notification_rows(args.ledger)[0][2]
    assert "전송 중단" in text
    assert "접수 완료" not in text
    assert "private detail" not in text
    assert OrderLedger(args.ledger).rows("account") == []


def test_stdout_error_after_success_still_reports_reservation_success(tmp_path):
    args = arguments(tmp_path)
    client, notifier = accepted_client(), Mock()
    notifier.send.return_value = 81
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=execute_callback(client, args.ledger, after_success_error=BrokenPipeError())):
        with pytest.raises(BrokenPipeError):
            run_once(args)
    assert "예약주문 접수 완료" in notification_rows(args.ledger)[0][2]


def test_outbox_deduplicates_events_and_serializes_two_senders(tmp_path):
    a, b = OrderLedger(tmp_path / "orders.db"), OrderLedger(tmp_path / "orders.db")
    assert a.queue_notification("event", "text")
    assert not b.queue_notification("event", "changed text")
    assert a.claim_notification("event")
    assert not b.claim_notification("event", retry=True)
    a.update_notification("event", "SENT", message_id=1)
    assert not b.claim_notification("event", retry=True)
    assert not b.pending_notifications()


def test_notification_test_never_reads_market_or_broker(tmp_path):
    notifier = Mock()
    notifier.send.return_value = 82
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=AssertionError("orders executed")), \
         patch("kmw_autotrade.MeritzClient.from_env", side_effect=AssertionError("broker called")):
        assert main(["--test-notification", "--ledger", str(tmp_path / "unused.db")]) == 0
    assert not (tmp_path / "unused.db").exists()
    assert "주문 접수나 체결을 의미하지 않습니다" in notifier.send.call_args.args[0]


def test_preview_and_status_do_not_send_notifications(tmp_path):
    with patch("kmw_autotrade.configured_notifier", side_effect=AssertionError("notifier called")), \
         patch("kmw_autotrade._run_once") as execute:
        run_once(arguments(tmp_path, submit=False))
        assert execute.call_count == 1


def test_failed_notification_retry_has_nonzero_exit_and_no_orders(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.db")
    ledger.queue_notification("event", "text")
    notifier = Mock()
    notifier.send.side_effect = TelegramNotificationError("failure")
    with patch("kmw_autotrade.configured_notifier", return_value=notifier), \
         patch("kmw_autotrade._run_once", side_effect=AssertionError("orders executed")):
        assert main(["--retry-notifications", "--ledger", str(tmp_path / "orders.db")]) == 1
    assert notification_rows(tmp_path / "orders.db")[0][0] == "FAILED"
