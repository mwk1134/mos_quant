import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from kmw_autotrade import (
    OrderBlocked, OrderLedger, active_reservation, cap_quantities, load_env, main,
    next_run, preflight, reservation_window, select_side, submit_plan, validate_plan,
)
from meritz_api import AmbiguousSubmission, MeritzAPIError


NOW = datetime(2026, 10, 12, 17, 1, tzinfo=ZoneInfo("Asia/Seoul"))


def plan():
    return {
        "preset": "KMW", "status": "ready", "order_date": "2026-10-12",
        "basis_date": "2026-10-09", "expected_basis_date": "2026-10-09",
        "snapshot_date": "2026-10-09", "snapshot_fresh": True,
        "snapshot_positions": [{"quantity": 10}],
        "orders": [
            {"side": "SELL", "ticker": "SOXL", "order_type": "LOC",
             "quantity": 10, "price": "110.00", "round": 1, "position_key": "1_2026-10-08"},
            {"side": "BUY", "ticker": "SOXL", "order_type": "LOC",
             "quantity": 5, "price": "100.00", "round": 2, "position_key": "2_2026-10-12"},
        ],
    }


def broker():
    client = Mock()
    client.holdings.return_value = [{"iscd": "SOXL.AX", "rmnd_clcd": "1",
                                    "loan_dtl_type_code": "0000", "rmnd_dqty": "10.0000",
                                    "seln_able_dqty": "10", "ovrs_exch_vndr_idno": "0066"}]
    client.exchanges.return_value = [{"ovrs_exch_vndr_idno": "0066", "ovrs_stck_exch_idno": "AX", "onln_yn": "Y"}]
    client.quote.return_value = {"iscd": "SOXL", "shnu_aspr_unit": "0.01", "seln_aspr_unit": "0.01",
                                 "shnu_oder_qty_unit": "1", "seln_oder_qty_unit": "1"}
    client.orders_today.return_value = []
    client.reserved_orders.return_value = []
    client.order_estimate.return_value = [{"crcd": "USD", "oder_crnc_oder_able_camt": "1000", "othr_crnc_evdn_camt": "0"}]
    client.submit_reserved_order.return_value = {"rsp_cd": "5620", "data": {"warn_cls_code": "0", "oder_rsev_mst_no": 12}}
    return client


def test_reservation_time_uses_both_dst_and_standard_time():
    reservation_window(NOW, "2026-10-12")
    reservation_window(datetime(2026, 12, 1, 17, 1, tzinfo=NOW.tzinfo), "2026-12-01")
    with pytest.raises(OrderBlocked):
        reservation_window(NOW.replace(hour=21, minute=30), "2026-10-12")
    with pytest.raises(OrderBlocked):
        reservation_window(NOW.replace(hour=8), "2026-10-12")


def test_next_daily_run_is_always_in_korea():
    assert next_run(NOW.replace(hour=16), NOW.time()).date() == NOW.date()
    assert next_run(NOW, NOW.time()).day == 13


def test_duplicate_and_changed_recommendation_are_not_resent(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.db")
    second_process = OrderLedger(tmp_path / "orders.db")
    ledger.claim("account", plan())
    updated = plan()
    updated["orders"][0]["price"] = "111.00"
    with pytest.raises(OrderBlocked):
        second_process.claim("account", updated)
    assert len(ledger.rows("account")) == 2


def test_timeout_keeps_partial_batch_and_blocks_future_submissions(tmp_path):
    client = broker()
    client.submit_reserved_order.side_effect = [client.submit_reserved_order.return_value, AmbiguousSubmission("timeout")]
    ledger = OrderLedger(tmp_path / "orders.db")
    with pytest.raises(AmbiguousSubmission):
        submit_plan(client, plan(), ledger, "account", "0066")
    assert [r["state"] for r in ledger.rows("account")] == ["RESERVED", "UNKNOWN"]
    with pytest.raises(OrderBlocked):
        ledger.unresolved("account")
    assert client.submit_reserved_order.call_count == 2


def test_first_rejection_stops_remaining_orders(tmp_path):
    client = broker()
    client.submit_reserved_order.side_effect = MeritzAPIError("rejected", response={"rsp_cd": "0760"})
    ledger = OrderLedger(tmp_path / "orders.db")
    with pytest.raises(MeritzAPIError):
        submit_plan(client, plan(), ledger, "account", "0066")
    assert [r["state"] for r in ledger.rows("account")] == ["REJECTED", "NOT_SENT"]
    assert client.submit_reserved_order.call_count == 1


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(preset="JEH"),
    lambda p: p.update(snapshot_date="2026-10-08"),
    lambda p: p["orders"][1].update(quantity=0),
    lambda p: p["orders"][1].update(quantity=True),
    lambda p: p["orders"][1].update(price="NaN"),
])
def test_unsafe_or_unintended_plans_are_blocked(mutation):
    p = plan()
    mutation(p)
    with pytest.raises(OrderBlocked):
        validate_plan(p, NOW)


@pytest.mark.parametrize("sell_kind,sell_price", [("LOC", "99.00"), ("MOC", "0")])
@pytest.mark.parametrize("route", ["direct", "reserved"])
def test_independent_close_orders_preserve_terms_without_comparing_prices(sell_kind, sell_price, route):
    p = plan()
    p["orders"][0].update(order_type=sell_kind, price=sell_price)
    original = deepcopy(p)
    validate_plan(p, NOW, order_route=route)
    assert p == original


def test_preflight_verifies_holdings_sellable_cash_and_units(monkeypatch):
    monkeypatch.delenv("MERITZ_SOXL_EXCHANGE_CODE", raising=False)
    monkeypatch.delenv("MERITZ_SOXL_PRICE_TICK", raising=False)
    client = broker()
    checked = preflight(client, plan(), NOW, Decimal("0.005"))
    assert checked["required_usd"] == "502.50"
    client.holdings.return_value[0]["rmnd_dqty"] = "9"
    with pytest.raises(OrderBlocked, match="broker holds"):
        preflight(client, plan(), NOW, Decimal("0.005"))
    client.holdings.return_value[0]["rmnd_dqty"] = "10"
    client.order_estimate.return_value[0]["oder_crnc_oder_able_camt"] = "501"
    with pytest.raises(OrderBlocked, match="buying power"):
        preflight(client, plan(), NOW, Decimal("0.005"))


def test_zero_quote_tick_requires_verified_configuration(monkeypatch):
    monkeypatch.delenv("MERITZ_SOXL_EXCHANGE_CODE", raising=False)
    monkeypatch.delenv("MERITZ_SOXL_PRICE_TICK", raising=False)
    client = broker()
    client.quote.return_value.update(shnu_aspr_unit="0", seln_aspr_unit="0")
    with pytest.raises(OrderBlocked, match="omitted an order unit"):
        preflight(client, plan(), NOW, Decimal("0.005"))
    monkeypatch.setenv("MERITZ_SOXL_PRICE_TICK", "0.01")
    assert preflight(client, plan(), NOW, Decimal("0.005"))["exchange_code"] == "0066"


def test_active_reservation_is_blocked(monkeypatch):
    monkeypatch.delenv("MERITZ_SOXL_EXCHANGE_CODE", raising=False)
    monkeypatch.delenv("MERITZ_SOXL_PRICE_TICK", raising=False)
    client = broker()
    client.reserved_orders.return_value = [{"iscd": "SOXL.AX", "cncl_able_yn": "Y", "rsev_cncl_yn": ""}]
    with pytest.raises(OrderBlocked, match="reservation already exists"):
        preflight(client, plan(), NOW, Decimal("0.005"))


def test_noncancelable_reservation_is_not_assumed_inactive(monkeypatch):
    monkeypatch.delenv("MERITZ_SOXL_EXCHANGE_CODE", raising=False)
    monkeypatch.delenv("MERITZ_SOXL_PRICE_TICK", raising=False)
    client = broker()
    client.reserved_orders.return_value = [{"iscd": "SOXL.AX", "cncl_able_yn": "N", "rsev_cncl_yn": ""}]
    with pytest.raises(OrderBlocked, match="reservation already exists"):
        preflight(client, plan(), NOW, Decimal("0.005"))
    assert active_reservation({"oder_fin_date": "20261009"}, "20261012") is False
    assert active_reservation({"odqt": "10", "acml_cntg_qty": "10"}, "20261012") is True


def test_preview_never_authenticates_or_submits(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text('{"KMW": {}}')
    with patch("kmw_autotrade.build_kmw_plan", return_value=plan()), \
         patch("kmw_autotrade.MeritzClient.from_env", side_effect=AssertionError("broker called")):
        assert main(["--snapshot-file", str(path)]) == 0


@pytest.mark.parametrize("current,order_date,error", [
    (datetime(2026, 10, 13, 8, 41, tzinfo=NOW.tzinfo), "2026-10-13", "today's US trading session"),
    (datetime(2026, 10, 12, 23, 1, tzinfo=NOW.tzinfo), "2026-10-12", "pre-open recommendation"),
])
@pytest.mark.parametrize("mode", ["--submit", "--check-account"])
def test_cli_displayable_plan_outside_window_stops_before_broker_reads_or_orders(
        tmp_path, monkeypatch, capsys, current, order_date, error, mode):
    path = tmp_path / "snapshot.json"
    path.write_text('{"KMW": {}}')
    displayed = plan()
    displayed["order_date"] = order_date
    client = broker()
    monkeypatch.setenv("MERITZ_ACCOUNT_PRESET", "KMW")
    monkeypatch.setenv("MERITZ_APP_KEY", "fake-cli-key")
    # Keep all plan and notification files isolated; no real account or network
    # call is needed to prove the command rejects a currently visible plan.
    monkeypatch.setattr("kmw_autotrade.ROOT", tmp_path)
    monkeypatch.setattr("kmw_autotrade.MeritzClient.from_env", Mock(return_value=client))
    monkeypatch.setattr("kmw_autotrade.configured_notifier", lambda: Mock())
    with patch("kmw_autotrade.build_kmw_plan", return_value=displayed), \
         patch("kmw_autotrade.datetime") as frozen_clock:
        frozen_clock.now.side_effect = lambda tz=None: current.astimezone(tz) if tz else current
        assert main([mode, "--snapshot-file", str(path), "--ledger", str(tmp_path / "orders.db")]) == 1
    assert error in capsys.readouterr().err
    assert client.mock_calls == []
    ledger = OrderLedger(tmp_path / "orders.db")
    try:
        assert ledger.rows("account") == []
        assert ledger.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    finally:
        ledger.db.close()


def test_stale_preview_cannot_be_combined_with_live_options():
    assert main(["--allow-stale-preview", "--submit"]) == 1


def test_cap_changes_every_order_without_changing_portfolio():
    p = plan()
    capped = cap_quantities(p, 1)
    assert [o["quantity"] for o in capped["orders"]] == [1, 1]
    assert p["orders"][0]["quantity"] == 10
    assert capped["snapshot_positions"][0]["quantity"] == 10


def test_explicit_side_selection_preserves_holdings_and_allows_a_single_side_test():
    p = plan()
    p["warnings"] = []
    p["orders"][1]["price"] = "111.00"
    selected = select_side(p, "BUY")
    assert len(selected["orders"]) == 1
    assert len(p["orders"]) == 2
    assert selected["snapshot_positions"] == p["snapshot_positions"]
    validate_plan(selected, NOW)


def test_environment_file_does_not_execute_shell(tmp_path, monkeypatch):
    path = tmp_path / "env"
    path.write_text("MERITZ_APP_KEY='$(touch /tmp/never-execute)'\n")
    load_env(path)
    assert __import__("os").environ["MERITZ_APP_KEY"] == "$(touch /tmp/never-execute)"
    monkeypatch.delenv("MERITZ_APP_KEY")
