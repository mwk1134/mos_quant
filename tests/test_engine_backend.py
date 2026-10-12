"""Engine adapter tests: fake broker, fake snapshots, and no external network."""
from copy import deepcopy
from datetime import datetime
import importlib
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import kmw_engine_backend as backend_module
from kmw_autotrade import OrderLedger, account_id
from kmw_engine_backend import EngineBlocked, KMWBackend
from meritz_api import AmbiguousSubmission, MeritzAPIError
from telegram_notify import TelegramNotificationError


NOW = datetime(2026, 10, 12, 17, 1, tzinfo=ZoneInfo("Asia/Seoul"))


def plan():
    quantities = [41, 89, 78, 102, 115]
    positions = [{"round": i, "position_key": f"{i}_2026-10-0{i + 4}", "quantity": qty}
                 for i, qty in enumerate(quantities, 1)]
    sells = [{"ticker": "SOXL", "side": "SELL", "quantity": qty,
              "price": f"{154 + i}.65", "order_type": "LOC", "round": i,
              "position_key": positions[i - 1]["position_key"], "reason": "target_price"}
             for i, qty in enumerate(quantities, 1)]
    buy = {"ticker": "SOXL", "side": "BUY", "quantity": 142, "price": "144.79",
           "order_type": "LOC", "round": 6, "position_key": "6_2026-10-12",
           "reason": "daily_recommendation"}
    return {"preset": "KMW", "status": "ready", "order_date": "2026-10-12",
            "generated_at": NOW.isoformat(), "basis_date": "2026-10-09",
            "expected_basis_date": "2026-10-09", "snapshot_date": "2026-10-09",
            "snapshot_fresh": True, "snapshot_positions": positions, "warnings": [],
            "orders": sells + [buy]}


def receipt(number):
    return {"rsp_cd": "0001", "data": {"warn_cls_code": "0", "oder_no": number}}


def broker():
    client = Mock()
    client.holdings.return_value = [{"iscd": "SOXL.AX", "rmnd_clcd": "1", "loan_dtl_type_code": "0000",
                                    "rmnd_dqty": "425", "seln_able_dqty": "425", "ovrs_exch_vndr_idno": "0066"}]
    client.exchanges.return_value = [{"ovrs_exch_vndr_idno": "0066", "onln_yn": "Y", "ovrs_stck_exch_idno": "AX"}]
    client.quote.return_value = {"iscd": "SOXL", "shnu_aspr_unit": "0.01", "seln_aspr_unit": "0.01",
                                "shnu_oder_qty_unit": 1, "seln_oder_qty_unit": 1}
    client.orders_today.return_value = []
    client.reserved_orders.return_value = []
    client.order_estimate.return_value = [{"crcd": "USD", "othr_crnc_evdn_camt": "0",
                                          "oder_crnc_oder_able_camt": "49000"}]
    client.submit_order.side_effect = [receipt(1000 + i) for i in range(6)]
    return client


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A real network request is forbidden in engine backend tests")
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("requests.get", forbidden)


@pytest.fixture
def setup_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("MERITZ_APP_KEY", "fake-engine-key")
    monkeypatch.setenv("MERITZ_APP_SECRET", "fake-engine-secret")
    monkeypatch.setenv("MERITZ_MAC_ADDRESS", "AABBCCDDEEFF")
    monkeypatch.setenv("MERITZ_ACCOUNT_PRESET", "KMW")
    monkeypatch.delenv("MERITZ_SOXL_EXCHANGE_CODE", raising=False)
    monkeypatch.delenv("MERITZ_SOXL_PRICE_TICK", raising=False)
    notifier = Mock()
    notifier.send.return_value = 123
    client = broker()
    monkeypatch.setattr(backend_module, "configured_notifier", lambda: notifier)
    create_client = Mock(return_value=client)
    monkeypatch.setattr(backend_module.MeritzClient, "from_env", create_client)
    engine = KMWBackend(env_file=None, ledger_path=tmp_path / "orders.db", clock=lambda: NOW)
    return engine, client, notifier, create_client


def ledger_rows(engine):
    ledger = OrderLedger(engine.ledger_path)
    try:
        return ledger.rows(account_id())
    finally:
        ledger.db.close()


def test_import_and_constructor_are_inert(tmp_path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Unexpected external initialization"))
    monkeypatch.setattr("kmw_autotrade.load_env", forbidden)
    monkeypatch.setattr("kmw_autotrade.load_snapshots", forbidden)
    monkeypatch.setattr("kmw_autotrade.configured_notifier", forbidden)
    monkeypatch.setattr("meritz_api.MeritzClient.from_env", forbidden)
    spec = importlib.util.spec_from_file_location("test_inert_kmw_backend", backend_module.__file__)
    inert_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inert_module)
    instance = inert_module.KMWBackend(env_file=tmp_path / "absent.env", ledger_path=tmp_path / "unused.db")
    assert instance.ledger_path == tmp_path / "unused.db"
    assert not instance.ledger_path.exists()
    forbidden.assert_not_called()


def test_preview_uses_latest_webapp_source_and_never_loads_credentials(tmp_path, monkeypatch):
    snapshot = {"KMW": {"test": "snapshot"}}
    snapshots = Mock(return_value=(snapshot, "github:latest/main/KMW"))
    make_plan = Mock(return_value=plan())
    forbidden = Mock(side_effect=AssertionError("Broker or credentials touched by preview"))
    monkeypatch.setattr(backend_module, "load_snapshots", snapshots)
    monkeypatch.setattr(backend_module, "build_kmw_plan", make_plan)
    monkeypatch.setattr(backend_module, "load_env", forbidden)
    monkeypatch.setattr(backend_module, "configured_notifier", forbidden)
    monkeypatch.setattr(backend_module.MeritzClient, "from_env", forbidden)
    engine = backend_module.KMWBackend(env_file=tmp_path / "absent.env", ledger_path=tmp_path / "unused.db")
    result = engine.preview(NOW)
    snapshots.assert_called_once_with()
    make_plan.assert_called_once_with(snapshot, now=NOW)
    assert result["order_route"] == "direct"
    assert result["snapshot_source"] == "github:latest/main/KMW"
    assert len(result["orders"]) == 6
    json.dumps(result, allow_nan=False)
    forbidden.assert_not_called()
    assert not engine.ledger_path.exists()


def test_six_recommendations_send_six_separate_direct_calls_unchanged(setup_backend):
    engine, client, notifier, _ = setup_backend
    p = plan()
    unchanged = deepcopy(p)
    result = engine.execute(p, NOW, should_continue=lambda: True)
    assert result["state"] == "DIRECT_ACCEPTED"
    assert len(result["orders"]) == client.submit_order.call_count == 6
    for call, order in zip(client.submit_order.call_args_list, p["orders"]):
        assert call.kwargs == {"side": order["side"].lower(), "symbol": "SOXL", "exchange_vendor_code": "0066",
                               "quantity": order["quantity"], "price": order["price"], "order_type": order["order_type"]}
    assert p == unchanged
    client.submit_reserved_order.assert_not_called()
    assert [row["state"] for row in ledger_rows(engine)] == ["DIRECT_ACCEPTED"] * 6
    assert [row["order_no"] for row in result["orders"]] == [str(1000 + i) for i in range(6)]
    assert result["preflight"]["held_quantity"] == "425"
    assert result["notification_pending"] == 0
    notifier.send.assert_called_once()
    text = notifier.send.call_args.args[0]
    assert "매수 142주" in text and "매도 115주" in text
    assert "접수 완료" in text and "체결 완료 알림이 아닙니다" in text


def test_preview_explains_current_crossing_prices_without_changing_six_rows(tmp_path, monkeypatch):
    p = plan()
    p["orders"][4]["price"] = "144.65"
    original_orders = deepcopy(p["orders"])
    monkeypatch.setattr(backend_module, "load_snapshots", Mock(return_value=({"KMW": {}}, "test")))
    monkeypatch.setattr(backend_module, "build_kmw_plan", Mock(return_value=p))
    result = KMWBackend(env_file=None, ledger_path=tmp_path / "unused.db").preview(NOW)
    assert "매수 LOC $144.79와 5회차 매도 LOC $144.65가 교차해 전체 전송이 차단됩니다." in result["warnings"]
    assert result["orders"] == original_orders and len(result["orders"]) == 6


def test_crossing_batch_blocks_all_orders_before_any_broker_client(setup_backend):
    engine, client, notifier, create_client = setup_backend
    p = plan()
    p["orders"][4]["price"] = "144.65"
    result = engine.execute(p, NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "CROSSING_ORDERS"
    assert "전체 주문" in result["error"]
    create_client.assert_not_called()
    client.submit_order.assert_not_called()
    notifier.send.assert_called_once()
    assert "전송 중단" in notifier.send.call_args.args[0]
    assert result["notification_pending"] == 0
    assert ledger_rows(engine) == []


def test_crossing_notification_config_failure_stays_blocked_without_broker(tmp_path, monkeypatch):
    p = plan()
    p["orders"][4]["price"] = "144.65"
    create_client = Mock(side_effect=AssertionError("Broker initialized after local block"))
    monkeypatch.setattr(backend_module.MeritzClient, "from_env", create_client)
    engine = KMWBackend(env_file=tmp_path / "absent.env", ledger_path=tmp_path / "unused.db")
    result = engine.execute(p, NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "CROSSING_ORDERS"
    assert result["notification_status"] == "UNAVAILABLE"
    create_client.assert_not_called()
    assert not engine.ledger_path.exists()


def test_actual_holdings_mismatch_blocks_all_orders_and_notifies(setup_backend):
    engine, client, notifier, _ = setup_backend
    client.holdings.return_value[0]["rmnd_dqty"] = "426"
    result = engine.execute(plan(), NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "HOLDINGS_MISMATCH"
    assert result["orders"] == []
    client.submit_order.assert_not_called()
    notifier.send.assert_called_once()
    assert ledger_rows(engine) == []


@pytest.mark.parametrize("field,value", [("snapshot_date", "2026-10-08"), ("snapshot_fresh", False)])
def test_stale_portfolio_blocks_before_credentials_or_broker(setup_backend, field, value):
    engine, client, _, create_client = setup_backend
    p = plan()
    p[field] = value
    result = engine.execute(p, NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "STALE_SNAPSHOT"
    create_client.assert_not_called()
    client.submit_order.assert_not_called()


def test_existing_one_share_test_identity_is_preserved_and_blocks_full_batch(setup_backend):
    engine, client, notifier, create_client = setup_backend
    p = plan()
    test = deepcopy(p)
    test["orders"] = [deepcopy(p["orders"][-1])]
    test["orders"][0]["quantity"] = 1
    ledger = OrderLedger(engine.ledger_path)
    try:
        identity = ledger.claim(account_id(), test)[0]
        body = receipt(92152)
        body["replacement_audit"] = {"private": "must-not-leak"}
        ledger.update(identity, "DIRECT_ACCEPTED", body)
        before = ledger.rows(account_id())
    finally:
        ledger.db.close()
    result = engine.execute(p, NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "ALREADY_ATTEMPTED"
    assert "1주" in result["error"] and result["orders"][0]["order_no"] == "92152"
    assert ledger_rows(engine) == before
    assert "must-not-leak" not in json.dumps(result)
    assert result["orders"][0]["order"]["quantity"] == 1  # The receipt reports the original submitted quantity.
    assert p["orders"][-1]["quantity"] == 142  # The new recommendation remains intact.
    assert before[0]["order"]["quantity"] == 1  # The original test is retained in durable history.
    create_client.assert_not_called()
    client.submit_order.assert_not_called()
    notifier.send.assert_called_once()


def test_unknown_after_first_order_preserves_partial_batch_and_stops_retry(setup_backend, capsys):
    engine, client, notifier, _ = setup_backend
    private = {"data": {"warn_msg": "credential-must-not-leak"}, "rsp_cd": "0000"}
    client.submit_order.side_effect = [receipt(1234), AmbiguousSubmission("credential-must-not-leak", response=private)]
    result = engine.execute(plan(), NOW)
    assert result["state"] == "UNKNOWN" and result["error_code"] == "UNKNOWN_SUBMISSION"
    assert result["orders"][0]["state"] == "DIRECT_ACCEPTED"
    assert result["orders"][0]["order_no"] == "1234"
    assert result["not_sent_count"] == 4
    rows = ledger_rows(engine)
    assert [row["state"] for row in rows] == ["DIRECT_ACCEPTED", "UNKNOWN"] + ["NOT_SENT"] * 4
    assert rows[1]["response"] == private
    retry = engine.execute(plan(), NOW)
    assert retry["error_code"] == "UNRESOLVED_ORDER"
    assert client.submit_order.call_count == 2
    text = notifier.send.call_args_list[0].args[0]
    assert "주문번호 1234" in text and "접수 여부 불확실" in text
    assert "credential-must-not-leak" not in json.dumps(result) + text + capsys.readouterr().out


def test_rejected_second_order_preserves_first_and_reports_safe_error(setup_backend):
    engine, client, notifier, _ = setup_backend
    client.submit_order.side_effect = [receipt(1234), MeritzAPIError("private-url-and-secret", response={"rsp_cd": "0760"})]
    result = engine.execute(plan(), NOW)
    assert result["state"] == "REJECTED" and result["error_code"] == "BROKER_ERROR"
    assert result["orders"][0]["order_no"] == "1234"
    assert [row["state"] for row in ledger_rows(engine)] == ["DIRECT_ACCEPTED", "REJECTED"] + ["NOT_SENT"] * 4
    assert "private-url-and-secret" not in json.dumps(result) + notifier.send.call_args.args[0]
    assert client.submit_order.call_count == 2


def test_unexpected_error_after_sending_transition_is_unknown_and_not_retried(setup_backend, monkeypatch):
    engine, client, notifier, _ = setup_backend
    def failed_update(client, p, ledger, account, exchange_code, **kwargs):
        identities = ledger.claim(account, p)
        ledger.update(identities[0], "SENDING")
        raise RuntimeError("private-local-database-error")
    monkeypatch.setattr(backend_module, "submit_plan", failed_update)
    result = engine.execute(plan(), NOW)
    assert result["state"] == "UNKNOWN" and result["error_code"] == "UNKNOWN_SUBMISSION"
    rows = ledger_rows(engine)
    assert next(row for row in rows if row["order"]["round"] == 1)["state"] == "SENDING"
    assert [row["state"] for row in rows].count("PLANNED") == 5
    retry = engine.execute(plan(), NOW)
    assert retry["state"] == "BLOCKED" and retry["error_code"] == "UNRESOLVED_ORDER"
    client.submit_order.assert_not_called()
    client.submit_reserved_order.assert_not_called()
    assert "private-local-database-error" not in json.dumps(result)
    assert notifier.send.call_count == 2  # UNKNOWN first, then a separate retry-block notice.
    assert "접수 여부 불확실" in notifier.send.call_args_list[0].args[0]


def test_user_stop_before_execution_never_reads_credentials_or_network(setup_backend, monkeypatch):
    engine, client, notifier, create_client = setup_backend
    load = Mock(side_effect=AssertionError("Credentials loaded after stop"))
    monkeypatch.setattr(engine, "_load_credentials", load)
    result = engine.execute(plan(), NOW, should_continue=lambda: False)
    assert result["state"] == "STOPPED" and result["error_code"] == "USER_STOPPED"
    load.assert_not_called()
    create_client.assert_not_called()
    notifier.send.assert_not_called()
    assert not engine.ledger_path.exists()


def test_user_stop_after_first_order_marks_only_remaining_not_sent(setup_backend):
    engine, client, notifier, _ = setup_backend
    enabled = True
    def first_order(**kwargs):
        nonlocal enabled
        enabled = False
        return receipt(1234)
    client.submit_order.side_effect = first_order
    result = engine.execute(plan(), NOW, should_continue=lambda: enabled)
    assert result["state"] == "STOPPED" and result["error_code"] == "USER_STOPPED"
    assert result["not_sent_count"] == 5
    assert result["orders"][0]["order_no"] == "1234"
    assert [row["state"] for row in ledger_rows(engine)] == ["DIRECT_ACCEPTED"] + ["NOT_SENT"] * 5
    assert client.submit_order.call_count == 1
    notifier.send.assert_called_once()


def test_cutoff_during_preflight_blocks_before_any_submission(setup_backend):
    engine, client, notifier, _ = setup_backend
    current = NOW
    engine._clock = lambda: current
    def slow_preflight_read():
        nonlocal current
        current = NOW.replace(hour=22, minute=30)  # 09:30 America/New_York.
        return [{"crcd": "USD", "othr_crnc_evdn_camt": "0", "oder_crnc_oder_able_camt": "49000"}]
    client.order_estimate.side_effect = slow_preflight_read
    result = engine.execute(plan(), NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "OUTSIDE_ORDER_WINDOW"
    assert result["not_sent_count"] == 6
    client.submit_order.assert_not_called()
    assert ledger_rows(engine) == []
    notifier.send.assert_called_once()


def test_cutoff_after_first_submission_keeps_one_accepted_and_five_not_sent(setup_backend):
    engine, client, notifier, _ = setup_backend
    current = NOW
    engine._clock = lambda: current
    def slow_order(**kwargs):
        nonlocal current
        current = NOW.replace(hour=22, minute=30)
        return receipt(1234)
    client.submit_order.side_effect = slow_order
    result = engine.execute(plan(), NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "OUTSIDE_ORDER_WINDOW"
    assert result["orders"][0]["order_no"] == "1234"
    assert result["not_sent_count"] == 5
    assert [row["state"] for row in ledger_rows(engine)] == ["DIRECT_ACCEPTED"] + ["NOT_SENT"] * 5
    assert client.submit_order.call_count == 1
    notifier.send.assert_called_once()
    assert "주문번호 1234" in notifier.send.call_args.args[0]


def test_notification_failure_keeps_all_six_accepted_and_never_resends(setup_backend):
    engine, client, notifier, _ = setup_backend
    notifier.send.side_effect = TelegramNotificationError("private-token", delivery_unknown=True)
    result = engine.execute(plan(), NOW)
    assert result["state"] == "DIRECT_ACCEPTED" and len(result["orders"]) == 6
    assert result["notification_pending"] == 1
    assert [row["state"] for row in ledger_rows(engine)] == ["DIRECT_ACCEPTED"] * 6
    assert client.submit_order.call_count == 6
    assert "private-token" not in json.dumps(result)


def test_local_orders_are_read_only_and_omit_private_responses(setup_backend, monkeypatch):
    engine, _, _, _ = setup_backend
    p = plan()
    ledger = OrderLedger(engine.ledger_path)
    try:
        identities = ledger.claim(account_id(), p)
        body = receipt(92152)
        body["private"] = "fake-secret"
        ledger.update(identities[0], "DIRECT_ACCEPTED", body)
    finally:
        ledger.db.close()
    forbidden = Mock(side_effect=AssertionError("Status accessed credentials or broker"))
    monkeypatch.setattr(engine, "_load_credentials", forbidden)
    monkeypatch.setattr(backend_module.MeritzClient, "from_env", forbidden)
    monkeypatch.setattr(backend_module, "configured_notifier", forbidden)
    result = engine.local_orders("20261012")
    assert len(result) == 6 and next(row for row in result if row["order_no"] == "92152")["state"] == "DIRECT_ACCEPTED"
    assert result[0]["order_date"] == "2026-10-12"
    assert engine.local_orders("2026-10-13") == []
    assert "fake-secret" not in json.dumps(result) and "account" not in json.dumps(result)
    forbidden.assert_not_called()


def test_missing_local_history_does_not_create_database(tmp_path):
    engine = KMWBackend(env_file=tmp_path / "absent.env", ledger_path=tmp_path / "not-created.db")
    assert engine.local_orders() == []
    assert not engine.ledger_path.exists()


def test_poll_status_reads_normalized_soxl_fills_and_never_orders_or_notifies(setup_backend):
    engine, client, notifier, _ = setup_backend
    base = {"oder_date": "20261012", "iscd": "SOXL.AX", "seln_byov_clcd": "2", "aspr_type_code": "5",
            "odqt": 142, "cntg_qty": 0, "cncl_cnfr_qty": 0, "ovrs_stck_oder_prc": "144.79000000",
            "ovrs_stck_oder_stat_cls_code": "0", "private": "must-not-leak"}
    client.orders_today.return_value = [
        {**base, "oder_no": 1},
        {**base, "oder_no": 2, "cntg_qty": 12},
        {**base, "oder_no": 3, "cntg_qty": 142},
        {**base, "oder_no": 4, "cncl_cnfr_qty": 142},
        {**base, "oder_no": 5, "seln_byov_clcd": "1", "aspr_type_code": "7", "ovrs_stck_oder_prc": "0"},
        {**base, "oder_no": 6, "ovrs_stck_oder_stat_cls_code": "2"},
        {**base, "oder_no": 7, "iscd": "AAPL.OQ"},
    ]
    result = engine.poll_status(NOW)
    assert result["state"] == "OK" and result["checked_at"] == NOW.isoformat()
    assert [row["state"] for row in result["orders"]] == ["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "OPEN", "REJECTED"]
    assert result["orders"][0]["side"] == "BUY" and result["orders"][0]["price"] == "144.79000000"
    assert result["orders"][4]["side"] == "SELL" and result["orders"][4]["order_type"] == "MOC"
    assert "must-not-leak" not in json.dumps(result)
    client.submit_order.assert_not_called()
    client.submit_reserved_order.assert_not_called()
    client.holdings.assert_not_called()
    notifier.send.assert_not_called()
    assert not engine.ledger_path.exists()


def test_poll_status_network_failure_is_safe(setup_backend):
    engine, client, notifier, _ = setup_backend
    client.orders_today.side_effect = RuntimeError("https://private-token.example/credential")
    result = engine.poll_status(NOW)
    assert result["state"] == "ERROR" and result["orders"] == []
    assert "private-token" not in json.dumps(result)
    notifier.send.assert_not_called()


def test_bad_time_and_missing_credentials_fail_without_raw_details(tmp_path):
    engine = KMWBackend(env_file=tmp_path / "absent.env", ledger_path=tmp_path / "orders.db")
    with pytest.raises(EngineBlocked, match="시간대"):
        engine.preview(NOW.replace(tzinfo=None))
    result = engine.execute(plan(), NOW)
    assert result["state"] == "BLOCKED" and result["error_code"] == "LOCAL_CONFIGURATION"
    assert "absent.env" not in json.dumps(result)
    assert not engine.ledger_path.exists()


def test_preview_error_does_not_expose_transport_message(tmp_path, monkeypatch):
    monkeypatch.setattr(backend_module, "load_snapshots", Mock(side_effect=RuntimeError("https://token.example/secret")))
    engine = KMWBackend(env_file=None, ledger_path=tmp_path / "unused.db")
    with pytest.raises(backend_module.EngineBlocked) as stopped:
        engine.preview(NOW)
    assert "token.example" not in str(stopped.value)
    assert not engine.ledger_path.exists()
