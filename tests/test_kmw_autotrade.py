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
    OrderBlocked, OrderLedger, account_id, active_reservation, cap_quantities, load_env, load_snapshots, main,
    notify_order_result, read_env,
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


def test_legacy_order_identity_is_exact_and_kmw2_has_a_distinct_identity():
    import hashlib
    original = plan()
    order = original["orders"][0]
    values = ["account", "KMW", original["order_date"], order["ticker"], order["side"],
              order["round"], order["position_key"]]
    legacy = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    assert OrderLedger.identity("account", original, order) == legacy
    second = {**original, "preset": "KMW2"}
    assert OrderLedger.identity("account", second, order) != legacy
    third = {**original, "preset": "JEH"}
    fourth = {**original, "preset": "JEH2"}
    assert len({legacy, OrderLedger.identity("account", second, order),
                OrderLedger.identity("account", third, order),
                OrderLedger.identity("account", fourth, order)}) == 4
    with pytest.raises(OrderBlocked, match="KMW, KMW2, JEH"):
        OrderLedger.identity("account", {**original, "preset": "UNREGISTERED"}, order)


def test_account_marker_must_match_the_selected_plan_without_exposing_key():
    credentials = {"MERITZ_ACCOUNT_PRESET": "KMW2", "MERITZ_APP_KEY": "fake-key"}
    assert account_id("KMW2", env=credentials)
    with pytest.raises(OrderBlocked, match="MERITZ_ACCOUNT_PRESET=KMW") as caught:
        account_id("KMW", env=credentials)
    assert "fake-key" not in str(caught.value)


def test_kmw2_plan_requires_explicit_preset_selection():
    second = {**plan(), "preset": "KMW2"}
    with pytest.raises(OrderBlocked, match="ready KMW"):
        validate_plan(second, NOW)
    validate_plan(second, NOW, preset="KMW2")
    with pytest.raises(OrderBlocked, match="ready KMW2"):
        validate_plan(plan(), NOW, preset="KMW2")


def test_jeh_plan_requires_its_own_selection_and_account_marker():
    third = {**plan(), "preset": "JEH"}
    validate_plan(third, NOW, preset="JEH")
    with pytest.raises(OrderBlocked, match="ready KMW"):
        validate_plan(third, NOW)
    with pytest.raises(OrderBlocked, match="ready JEH"):
        validate_plan(plan(), NOW, preset="JEH")
    credentials = {"MERITZ_ACCOUNT_PRESET": "JEH", "MERITZ_APP_KEY": "fake-third-account-key"}
    assert account_id("JEH", env=credentials)
    for wrong in ("KMW", "KMW2"):
        with pytest.raises(OrderBlocked, match=f"MERITZ_ACCOUNT_PRESET={wrong}") as caught:
            account_id(wrong, env=credentials)
        assert "fake-third-account-key" not in str(caught.value)


def test_jeh_stale_display_plan_cannot_pass_order_validation():
    third = {**plan(), "preset": "JEH", "snapshot_fresh": False}
    with pytest.raises(OrderBlocked, match="JEH snapshot is stale"):
        validate_plan(third, NOW, preset="JEH")


def test_jeh2_plan_requires_its_own_account_and_fresh_snapshot():
    fourth = {**plan(), "preset": "JEH2"}
    validate_plan(fourth, NOW, preset="JEH2")
    credentials = {"MERITZ_ACCOUNT_PRESET": "JEH2", "MERITZ_APP_KEY": "fake-fourth-account-key"}
    assert account_id("JEH2", env=credentials)
    for wrong in ("KMW", "KMW2", "JEH"):
        with pytest.raises(OrderBlocked, match=f"ready {wrong}"):
            validate_plan(fourth, NOW, preset=wrong)
        with pytest.raises(OrderBlocked, match=f"MERITZ_ACCOUNT_PRESET={wrong}") as caught:
            account_id(wrong, env=credentials)
        assert "fake-fourth-account-key" not in str(caught.value)
    with pytest.raises(OrderBlocked, match="JEH2 snapshot is stale"):
        validate_plan({**fourth, "snapshot_fresh": False}, NOW, preset="JEH2")


def test_jeh2_credentials_and_ledger_paths_are_separate_from_existing_accounts(tmp_path, monkeypatch):
    from kmw_autotrade import default_env_file, default_ledger_path

    monkeypatch.setattr("kmw_autotrade.ROOT", tmp_path)
    presets = ("KMW", "KMW2", "JEH", "JEH2")
    assert len({default_env_file(preset) for preset in presets}) == 4
    assert len({default_ledger_path(preset) for preset in presets}) == 4
    assert default_env_file("JEH2") == tmp_path / ".env.meritz.jeh2"
    assert default_ledger_path("JEH2") == tmp_path / ".autotrade/orders_jeh2.sqlite3"


def test_snapshot_loader_never_substitutes_the_other_account(tmp_path):
    path = tmp_path / "snapshots.json"
    path.write_text('{"KMW": {"marker": "first"}}')
    with pytest.raises(OrderBlocked, match="KMW2 snapshot is missing"):
        load_snapshots(path, preset="KMW2")
    path.write_text('{"KMW": {"marker": "first"}, "KMW2": {"marker": "second"}}')
    assert load_snapshots(path, preset="KMW2")[0]["KMW2"]["marker"] == "second"


def test_isolated_environment_parser_does_not_change_existing_account(tmp_path, monkeypatch):
    monkeypatch.setenv("MERITZ_APP_KEY", "fake-existing-key")
    path = tmp_path / "second.env"
    path.write_text("MERITZ_ACCOUNT_PRESET=KMW2\nMERITZ_APP_KEY=fake-second-key\n")
    assert read_env(path) == {"MERITZ_ACCOUNT_PRESET": "KMW2", "MERITZ_APP_KEY": "fake-second-key"}
    assert __import__("os").environ["MERITZ_APP_KEY"] == "fake-existing-key"
    path.write_text("MERITZ_APP_KEY=one\nMERITZ_APP_KEY=two\n")
    with pytest.raises(OrderBlocked, match="Duplicate"):
        read_env(path)


def test_preflight_uses_account_mapping_instead_of_other_accounts_exchange_settings(monkeypatch):
    monkeypatch.setenv("MERITZ_SOXL_EXCHANGE_CODE", "wrong-other-account-code")
    monkeypatch.setenv("MERITZ_SOXL_PRICE_TICK", "invalid-other-account-tick")
    second = {**plan(), "preset": "KMW2"}
    checked = preflight(broker(), second, NOW, Decimal("0.005"), preset="KMW2", env={})
    assert checked["exchange_code"] == "0066"


def test_notification_events_are_preset_scoped_even_when_no_orders(tmp_path, monkeypatch):
    import hashlib
    ledger = OrderLedger(tmp_path / "outbox.db")
    source = {"MERITZ_APP_KEY": "fake-shared-key"}
    for preset in ("KMW", "KMW2", "JEH", "JEH2"):
        empty = {**plan(), "preset": preset, "orders": []}
        notify_order_result(ledger, None, {"plan": empty}, error=ValueError("blocked"), env=source)
    account = hashlib.sha256(source["MERITZ_APP_KEY"].encode()).hexdigest()
    legacy_event = [account, plan()["order_date"], [], "BLOCKED"]
    legacy = hashlib.sha256(json.dumps(legacy_event, sort_keys=True).encode()).hexdigest()
    rows = ledger.db.execute("SELECT event_key,text FROM notifications ORDER BY created_at").fetchall()
    assert len(rows) == 4 and rows[0][0] == legacy
    assert rows[0][1].startswith("KMW 자동매매") and rows[1][1].startswith("KMW2 자동매매") and rows[2][1].startswith("JEH 자동매매") and rows[3][1].startswith("JEH2 자동매매")
    ledger.db.close()


@pytest.mark.parametrize("preset", ["KMW2", "JEH", "JEH2"])
def test_kmw2_cli_preview_needs_no_credentials_and_selects_its_own_snapshot(tmp_path, monkeypatch, capsys, preset):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"KMW": {}, preset: {}}))
    monkeypatch.setattr("kmw_autotrade.ROOT", tmp_path)
    monkeypatch.setenv("MERITZ_APP_KEY", "fake-first-account-key")
    second = {**plan(), "preset": preset, "warnings": []}
    with patch("kmw_autotrade.build_kmw_plan", return_value=second) as planner, \
         patch("kmw_autotrade.MeritzClient.from_env", side_effect=AssertionError("broker called")):
        assert main(["--preset", preset, "--snapshot-file", str(path)]) == 0
    assert planner.call_args.kwargs["preset"] == preset
    assert f'"preset": "{preset}"' in capsys.readouterr().out
    assert __import__("os").environ["MERITZ_APP_KEY"] == "fake-first-account-key"
    assert (tmp_path / f".autotrade/plans_{preset.lower()}/2026-10-12.json").exists()
    assert not (tmp_path / ".autotrade/plans/2026-10-12.json").exists()


@pytest.mark.parametrize("preset", ["KMW2", "JEH", "JEH2"])
def test_kmw2_cli_missing_credentials_cannot_use_inherited_first_account(tmp_path, monkeypatch, capsys, preset):
    monkeypatch.setattr("kmw_autotrade.ROOT", tmp_path)
    monkeypatch.setenv("MERITZ_ACCOUNT_PRESET", "KMW")
    monkeypatch.setenv("MERITZ_APP_KEY", "fake-first-account-key")
    forbidden = Mock(side_effect=AssertionError("Missing KMW2 credentials reached a broker"))
    monkeypatch.setattr("kmw_autotrade.MeritzClient.from_env", forbidden)
    assert main(["--preset", preset, "--check-account"]) == 1
    assert f"{preset} credential file is missing" in capsys.readouterr().err
    forbidden.assert_not_called()


def test_new_ledger_is_private_and_new_receipts_mask_sensitive_fields(tmp_path):
    import stat

    path = tmp_path / "orders.db"
    ledger = OrderLedger(path, secrets=("synthetic-known-key",))
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        identity = ledger.claim("account", plan())[0]
        response = {"rsp_cd": "5620", "data": {"oder_rsev_mst_no": 118082,
                    "app_secret": "synthetic-private"}, "rsp_msg": "contains synthetic-known-key"}
        ledger.update(identity, "RESERVED", response)
        stored = json.loads(ledger.db.execute("SELECT response FROM orders WHERE identity=?", (identity,)).fetchone()[0])
        assert stored["rsp_cd"] == "5620" and stored["data"]["oder_rsev_mst_no"] == 118082
        assert stored["data"]["app_secret"] == "[REDACTED]"
        assert "synthetic-known-key" not in json.dumps(stored)
        assert response["data"]["app_secret"] == "synthetic-private"
    finally:
        ledger.db.close()


def test_old_ledger_rows_are_masked_when_read_without_rewriting_receipts(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.db", secrets=("synthetic-known-key",))
    try:
        identity = ledger.claim("account", plan())[0]
        original = json.dumps({"rsp_cd": "3174", "access_token": "legacy-private-token",
                               "rsp_msg": "contains synthetic-known-key"})
        ledger.db.execute("UPDATE orders SET state='REJECTED',response=? WHERE identity=?", (original, identity))
        ledger.db.commit()
        rows = ledger.rows("account")
        assert rows[0]["response"]["access_token"] == "[REDACTED]"
        assert "synthetic-known-key" not in json.dumps(rows)
        assert ledger.db.execute("SELECT response FROM orders WHERE identity=?", (identity,)).fetchone()[0] == original
    finally:
        ledger.db.close()


def test_plan_files_and_stdout_mask_registered_credentials(tmp_path, monkeypatch, capsys):
    import stat
    from kmw_autotrade import _write_private_plan, print_plan

    monkeypatch.setenv("MERITZ_APP_KEY", "synthetic-known-key")
    original = {"order_no": 118082, "price": "100.00", "app_secret": "synthetic-private",
                "diagnostic": "contains synthetic-known-key"}
    print_plan(original)
    output = capsys.readouterr().out
    assert "synthetic-known-key" not in output and "synthetic-private" not in output
    path = tmp_path / "plans/2026-10-12.json"
    _write_private_plan(path, original)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved["order_no"] == 118082 and saved["price"] == "100.00"
    assert "synthetic-known-key" not in path.read_text()
    assert original["app_secret"] == "synthetic-private"


def test_cli_uses_fixed_error_for_arbitrary_transport_exception(monkeypatch, capsys):
    private = "synthetic-private-token https://example.test/?credential=hidden"
    monkeypatch.setattr("kmw_autotrade.run_once", Mock(side_effect=RuntimeError(private)))
    assert main([]) == 1
    output = capsys.readouterr().err
    assert private not in output and "synthetic-private-token" not in output
    assert "Execution could not be completed" in output


def test_notification_outbox_masks_new_messages_and_old_reads_without_rewriting(tmp_path):
    ledger = OrderLedger(tmp_path / "orders.db", secrets=("synthetic-known-bot-token",))
    try:
        ledger.queue_notification("new", "Order 118082 contains synthetic-known-bot-token")
        ledger.queue_notification("old", "placeholder")
        legacy = "Legacy message synthetic-known-bot-token"
        ledger.db.execute("UPDATE notifications SET text=? WHERE event_key='old'", (legacy,))
        ledger.db.commit()
        pending = ledger.pending_notifications()
        assert "synthetic-known-bot-token" not in json.dumps(pending)
        assert "118082" in pending[0]["text"]
        assert ledger.db.execute("SELECT text FROM notifications WHERE event_key='old'").fetchone()[0] == legacy
    finally:
        ledger.db.close()
