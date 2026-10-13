"""Transport tests use fake brokerage/GitHub objects and synthetic credentials."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import sqlite3
import sys
import threading
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

import preset_snapshot_sync as sync
from preset_snapshot_refresh import refresh_preset_snapshot as real_refresh

NOW = datetime(2026, 10, 13, 16, 58, tzinfo=ZoneInfo("Asia/Seoul"))
ENV = {"MERITZ_ACCOUNT_PRESET": "JEH", "MERITZ_APP_KEY": "synthetic-jeh-key",
       "MERITZ_APP_SECRET": "synthetic-jeh-secret", "MERITZ_MAC_ADDRESS": "001122AABBCC",
       "GITHUB_TOKEN": "synthetic-github-token"}


def history(**changes):
    return {"ovrs_stck_symb_iscd": "SOXL", "oder_date": "20261012", "oder_no": "196695",
            "seln_byov_clcd": "2", "cntg_dqty": "56", "cntg_crnc_prc": "128.4",
            "cntg_camt": "7190.4", "cntg_ymdh": "2026-10-13 05:00:07",
            "ovrs_stck_oder_prc": "144.79", "cntg_no": "0", **changes}


def document():
    return {"JEH": {"as_of_date": "2026-10-09", "marker": "baseline",
                    "pending_buy": {"order_date": "2026-10-12", "round": 6, "quantity": 56}},
            "KMW": {"marker": "first-account"}, "KMW2": {"marker": "second-account"},
            "_preset_configs": {"JEH": {"mode": "original"}}, "unknown_metadata": [1, {"safe": True}]}


def setup(monkeypatch, documents=None, outcomes=None):
    documents = [document()] if documents is None else documents
    outcomes = ["saved"] if outcomes is None else outcomes
    git = Mock()
    git.read.side_effect = [(deepcopy(value), f"sha-{index}") for index, value in enumerate(documents)]
    git.save.side_effect = outcomes
    factory = Mock(return_value=git)
    monkeypatch.setattr(sync, "_GithubSnapshots", factory)
    client = Mock()
    client.holdings.return_value = [{"ovrs_stck_symb_iscd": "SOXL", "rmnd_dqty": 217,
                                  "rmnd_clcd": "1", "loan_dtl_type_code": "0000"}]
    client.orders_history.return_value = [history()]
    client.orders_today.return_value = []
    broker = Mock(return_value=client)
    monkeypatch.setattr(sync.MeritzClient, "from_env", broker)
    rules = Mock(side_effect=lambda doc, preset, days: {day: {"mode": "AG", "buy_threshold": 3.6,
                        "sell_threshold": 3.5, "max_hold_days": 7, "strategy_name": "기본"} for day in days})
    monkeypatch.setattr(sync, "_historical_buy_rules", rules)
    def refreshed(original, **facts):
        value = deepcopy(original)
        value["JEH"].update(as_of_date=facts["basis_date"], marker="confirmed", factual_quantity=facts["broker_quantity"])
        return value
    refresher = Mock(side_effect=refreshed)
    monkeypatch.setattr(sync, "refresh_preset_snapshot", refresher)
    planner = Mock(return_value={"preset": "JEH", "status": "ready", "snapshot_fresh": True,
                                "basis_date": "2026-10-12", "order_date": "2026-10-13", "orders": [
                                    {"side": "BUY", "round": 7, "quantity": 20, "price": "132.15"},
                                    {"side": "SELL", "round": 6, "quantity": 56, "price": "132.90"}]})
    monkeypatch.setattr(sync, "build_kmw_plan", planner)
    return git, client, factory, broker, refresher, planner, rules


def run(tmp_path):
    return sync.refresh_and_load_snapshots(preset="JEH", env=ENV, ledger_path=tmp_path / "missing.sqlite3", now=NOW)


def test_confirmed_average_and_notional_not_requested_price():
    fills = sync.normalize_confirmed_fills([history(), history(), history(cntg_dqty=0, oder_no=None),
                                             history(ovrs_stck_symb_iscd="AAPL")])
    assert fills == [{"order_id": "196695", "trade_date": "2026-10-12", "side": "BUY", "quantity": 56,
                      "price": "128.4", "notional": "7190.4"}]


@pytest.mark.parametrize("changes", [
    {"cntg_dqty": None}, {"cntg_dqty": "56.5"}, {"cntg_dqty": -1}, {"cntg_dqty": True},
    {"cntg_crnc_prc": None}, {"cntg_crnc_prc": "NaN"}, {"cntg_crnc_prc": 0},
    {"cntg_camt": "7191.4"}, {"oder_no": None}, {"oder_no": 0}, {"seln_byov_clcd": "0"},
    {"oder_date": "20260231"}, {"cntg_ymdh": "2026-10-12 05:00:07"},
])
def test_unproven_facts_fail_closed_without_using_limit_price(changes):
    with pytest.raises(sync.SnapshotRefreshBlocked):
        sync.normalize_confirmed_fills([history(**changes)])


def test_conflicting_cumulative_totals_are_not_summed_or_guessed():
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        sync.normalize_confirmed_fills([history(), history(cntg_dqty=55, cntg_camt=7062)])
    assert caught.value.code == "CONFLICTING_EXECUTION"


def test_refresh_isolated_account_reads_only_and_fresh_plan_before_save(monkeypatch, tmp_path):
    git, client, factory, broker, refresher, planner, rules = setup(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-unrelated-account-token")
    monkeypatch.setenv("MERITZ_APP_KEY", "synthetic-unrelated-account-key")
    before = deepcopy(ENV)
    refreshed, source = run(tmp_path)
    assert ENV == before
    assert os.environ["GITHUB_TOKEN"] == "synthetic-unrelated-account-token"
    assert os.environ["MERITZ_APP_KEY"] == "synthetic-unrelated-account-key"
    assert factory.call_args.args == (ENV["GITHUB_TOKEN"],)
    assert broker.call_args.kwargs["env"] == ENV
    assert broker.call_args.kwargs["session"].trust_env is False
    assert source == sync.SOURCE
    assert refreshed["JEH"]["as_of_date"] == "2026-10-12"
    assert refreshed["JEH"]["pending_buy"] == {"round": 7, "quantity": 20, "target_price": 132.15,
        "order_date": "2026-10-13", "basis_date": "2026-10-12", "mode": "AG", "buy_threshold": 3.6,
        "sell_threshold": 3.5, "max_hold_days": 7, "strategy_name": "기본"}
    for key in document():
        if key != "JEH":
            assert refreshed[key] == document()[key]
    assert [call[0] for call in client.method_calls] == ["holdings", "orders_history", "orders_today"]
    client.orders_history.assert_called_once_with("20261009", "20261012")
    assert refresher.call_args.kwargs["broker_quantity"] == 217
    assert refresher.call_args.kwargs["fills"][0]["price"] == "128.4"
    assert planner.call_count == 1
    assert git.save.call_count == 1
    assert not (tmp_path / "missing.sqlite3").exists()


def test_sha_conflict_recomputes_latest_doc_keeps_other_changes_and_reuses_facts(monkeypatch, tmp_path):
    changed = document()
    changed["KMW2"]["marker"] = "concurrent-other-account-update"
    changed["JEH"]["manual_metadata"] = "preserved"
    git, client, _, _, refresher, _, _ = setup(monkeypatch, [document(), changed], ["conflict", "saved"])
    refreshed, _ = run(tmp_path)
    assert git.save.call_count == 2
    assert git.save.call_args_list[0].args[1] == "sha-0"
    assert git.save.call_args_list[1].args[1] == "sha-1"
    assert refresher.call_count == 2
    assert refreshed["KMW2"] == changed["KMW2"]
    assert refreshed["JEH"]["manual_metadata"] == "preserved"
    for method in [client.holdings, client.orders_history, client.orders_today]:
        assert method.call_count == 1


def factual_document():
    rules = {"mode": "AG", "buy_threshold": 2.0, "sell_threshold": 2.8,
             "max_hold_days": 6, "strategy_name": "공세"}
    return {"JEH": {"1_2026-10-08": {"round": 1, "shares": 100, "buy_price": 100.0,
                    "amount": 10000.0, **rules},
        "as_of_date": "2026-10-09", "available_cash": 10000.0, "compound_seed": 20000.0,
        "compound_reference_seed": 20000.0, "compound_profit_rate": 0.70, "compound_loss_rate": 0.20,
        "compound_settlements": [], "processed_seed_dates": [], "ma_alignment_switch_enabled": True,
        "pending_buy": {"round": 2, "order_date": "2026-10-12", "quantity": 56,
                        "target_price": 144.79, "basis_date": "2026-10-09", **rules}},
        "KMW": {"marker": "first"}, "KMW2": {"marker": "second"},
        "_preset_configs": {"JEH": {"initial_capital": 20000.0, "session_start_date": "2026-10-01", "seed_increases": []}}}


def test_real_cas_reconciliation_does_not_double_apply_concurrent_same_account_fill(monkeypatch, tmp_path):
    git, client, _, _, _, planner, rules = setup(monkeypatch)
    monkeypatch.setattr(sync, "refresh_preset_snapshot", real_refresh)
    client.holdings.return_value = [{"ovrs_stck_symb_iscd": "SOXL", "rmnd_dqty": 156,
                                  "rmnd_clcd": "1", "loan_dtl_type_code": "0000"}]
    initial = factual_document()
    git.read.return_value = (deepcopy(initial), "sha-initial")
    git.read.side_effect = None
    concurrent = None
    def concurrent_write(candidate, sha, preset):
        nonlocal concurrent
        concurrent = deepcopy(candidate)
        concurrent["KMW2"]["marker"] = "simultaneous-other-preset-change"
        git.read.return_value = (concurrent, "sha-concurrent")
        return "conflict"
    git.save.side_effect = concurrent_write
    result, _ = run(tmp_path)
    assert result == concurrent
    assert result["JEH"]["2_2026-10-12"]["shares"] == 56
    assert result["JEH"]["2_2026-10-12"]["buy_price"] == 128.4
    assert result["JEH"]["available_cash"] == 2809.6
    assert result["JEH"]["broker_sync"]["executions"]["2026-10-12:196695"]["quantity"] == 56
    assert result["JEH"]["compound_trading_days"] == 1
    assert result["KMW2"]["marker"] == "simultaneous-other-preset-change"
    assert git.save.call_count == 1
    assert git.read.call_count == 2
    assert client.orders_history.call_count == 1
    # Only the new pending recommendation needs fresh rules; the already
    # reconciled BUY never repeats historical calculations after a conflict.
    assert [call.args[2] for call in rules.call_args_list] == [{"2026-10-13"}, {"2026-10-13"}]


def test_frozen_pending_rules_survive_changed_current_config_without_history_recalculation(monkeypatch):
    original = factual_document()
    calculator = Mock(side_effect=AssertionError("historical rules must not replace frozen pending rules"))
    monkeypatch.setattr(sync, "_historical_buy_rules", calculator)
    rules = sync._needed_buy_rules(original, "JEH", sync.normalize_confirmed_fills([history()]))
    assert rules["2026-10-12"]["sell_threshold"] == 2.8
    assert rules["2026-10-12"]["strategy_name"] == "공세"
    calculator.assert_not_called()


def test_current_planner_entry_metadata_is_frozen_without_duplicate_market_lookup(monkeypatch, tmp_path):
    _, _, _, _, _, planner, rules = setup(monkeypatch)
    metadata = {"mode": "AG", "buy_threshold": 16.0, "sell_threshold": 6.8,
                "max_hold_days": 7, "strategy_name": "이평 정렬"}
    planner.return_value.update(mode="AG", buy_entry_rules=metadata)
    refreshed, _ = run(tmp_path)
    pending = refreshed["JEH"]["pending_buy"]
    assert {key: pending[key] for key in metadata} == metadata
    assert pending["quantity"] == 20
    assert pending["target_price"] == 132.15
    assert pending["basis_date"] == "2026-10-12"
    assert [call.args[2] for call in rules.call_args_list] == [{"2026-10-12"}]


@pytest.mark.parametrize("changes", [{"mode": "UNKNOWN"}, {"max_hold_days": 0},
    {"buy_threshold": "NaN"}, {"sell_threshold": None}, {"strategy_name": "bad\nname"}])
def test_invalid_current_entry_metadata_blocks_save_without_silent_historical_fallback(monkeypatch, tmp_path, changes):
    git, _, _, _, _, planner, rules = setup(monkeypatch)
    planner.return_value["buy_entry_rules"] = {"mode": "AG", "buy_threshold": 16.0,
        "sell_threshold": 6.8, "max_hold_days": 7, "strategy_name": "이평 정렬", **changes}
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "BUY_RULES_MISSING"
    git.save.assert_not_called()
    assert all(call.args[2] != {"2026-10-13"} for call in rules.call_args_list)


@pytest.mark.parametrize("day", ["20261008", "20261013"])
def test_history_outside_requested_range_cannot_advance_snapshot(monkeypatch, tmp_path, day):
    git, client, *_ = setup(monkeypatch)
    client.orders_history.return_value = [history(oder_date=day, cntg_ymdh=None)]
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "INVALID_BROKER_FACT"
    git.save.assert_not_called()


def test_conflict_maximum_three_write_attempts(monkeypatch, tmp_path):
    git, client, *_ = setup(monkeypatch, [document()] * 3, ["conflict"] * 3)
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "SNAPSHOT_CONFLICT"
    assert git.save.call_count == 3
    assert git.read.call_count == 3
    assert client.orders_history.call_count == 1


@pytest.mark.parametrize("bad_plan", [
    {"preset": "KMW", "status": "ready", "snapshot_fresh": True, "basis_date": "2026-10-12"},
    {"preset": "JEH", "status": "ready", "snapshot_fresh": False, "basis_date": "2026-10-12"},
    {"preset": "JEH", "status": "skipped", "snapshot_fresh": True, "basis_date": "2026-10-12"},
    {"preset": "JEH", "status": "ready", "snapshot_fresh": True, "basis_date": "2026-10-09"},
])
def test_unverified_fresh_plan_cannot_save(monkeypatch, tmp_path, bad_plan):
    git, _, _, _, _, planner, _ = setup(monkeypatch)
    planner.return_value = bad_plan
    with pytest.raises(sync.SnapshotRefreshBlocked):
        run(tmp_path)
    git.save.assert_not_called()


def test_changed_other_preset_blocks_save(monkeypatch, tmp_path):
    git, _, _, _, refresher, _, _ = setup(monkeypatch)
    def incorrect(document, **facts):
        document["KMW"] = {"stolen": True}
        return document
    refresher.side_effect = incorrect
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "PRESET_MISMATCH"
    git.save.assert_not_called()


def test_open_current_soxl_order_blocks_save(monkeypatch, tmp_path):
    git, client, *_ = setup(monkeypatch)
    client.orders_today.return_value = [{"iscd": "SOXL.AX", "odqt": 20, "cntg_qty": 0,
                                      "cncl_cnfr_qty": 0, "ovrs_stck_oder_stat_cls_code": "0", "oder_no": 77}]
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "UNRESOLVED_ORDER"
    git.save.assert_not_called()


@pytest.mark.parametrize("changes", [{"rmnd_clcd": "2"}, {"loan_dtl_type_code": "0001"}, {"rmnd_dqty": "217.5"}])
def test_nonordinary_holdings_cannot_be_saved_as_whole_share_positions(monkeypatch, tmp_path, changes):
    git, client, *_ = setup(monkeypatch)
    client.holdings.return_value[0].update(changes)
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert caught.value.code == "INVALID_BROKER_FACT"
    git.save.assert_not_called()


def test_account_mismatch_blocks_before_github_or_broker(monkeypatch, tmp_path):
    git, _, factory, broker, *_ = setup(monkeypatch)
    wrong = {**ENV, "MERITZ_ACCOUNT_PRESET": "KMW2"}
    with pytest.raises(sync.SnapshotRefreshBlocked):
        sync.refresh_and_load_snapshots(preset="JEH", env=wrong, ledger_path=tmp_path / "missing", now=NOW)
    factory.assert_not_called()
    broker.assert_not_called()


def test_historical_cursor_history_range_does_not_skip_partial_orders(monkeypatch, tmp_path):
    existing = document()
    existing["JEH"]["as_of_date"] = "2026-10-12"
    existing["JEH"]["broker_sync"] = {"baseline_date": "2026-10-09", "executions": {}}
    _, client, *_ = setup(monkeypatch, [existing])
    run(tmp_path)
    client.orders_history.assert_called_once_with("20261009", "20261012")


def test_exception_body_and_credentials_are_never_exposed(monkeypatch, tmp_path, capsys):
    git, client, *_ = setup(monkeypatch)
    sentinel = "synthetic-private-exception-token"
    client.holdings.side_effect = RuntimeError(sentinel)
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        run(tmp_path)
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)
    assert sentinel not in "".join(capsys.readouterr())
    git.save.assert_not_called()


def make_ledger(path, *, state="DIRECT_ACCEPTED", quantity=56, side="BUY"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE orders(account TEXT,order_date TEXT,payload TEXT,state TEXT,response TEXT)")
    order = {"ticker": "SOXL", "side": side, "round": 6, "position_key": "6_2026-10-12", "quantity": quantity}
    response = {"data": {"oder_no": 196695}}
    db.execute("INSERT INTO orders VALUES(?,?,?,?,?)", (sync.account_id("JEH", env=ENV), "2026-10-12",
               json.dumps(order), state, json.dumps(response)))
    db.commit()
    db.close()


def test_read_only_ledger_maps_actual_receipt_to_lot(tmp_path):
    path = tmp_path / "orders.sqlite3"
    make_ledger(path)
    before = path.read_bytes()
    mappings, unresolved = sync._ledger_facts(path, sync.account_id("JEH", env=ENV))
    fills = sync._attach_lot_identifiers(sync.normalize_confirmed_fills([history()]), mappings)
    assert fills[0]["position_key"] == "6_2026-10-12"
    assert fills[0]["round"] == 6
    assert unresolved == []
    assert path.read_bytes() == before


def test_unknown_ledger_submission_blocks_all_broker_reads(monkeypatch, tmp_path):
    git, _, _, broker, *_ = setup(monkeypatch)
    path = tmp_path / "orders.sqlite3"
    make_ledger(path, state="UNKNOWN")
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        sync.refresh_and_load_snapshots(preset="JEH", env=ENV, ledger_path=path, now=NOW)
    assert caught.value.code == "UNRESOLVED_ORDER"
    broker.assert_not_called()
    git.save.assert_not_called()


class FakeResponse:
    def __init__(self, value, status=200):
        self.value, self.status_code = value, status
    def json(self):
        return self.value


def github_read_response(document):
    content = sync._document_bytes(document)
    return FakeResponse({"sha": sync._blob_sha(content), "content": sync.base64.b64encode(content).decode("ascii"), "encoding": "base64"})


def github(monkeypatch):
    session = Mock()
    monkeypatch.setattr(sync.requests, "Session", Mock(return_value=session))
    return sync._GithubSnapshots("synthetic-github-token"), session


def test_github_fixed_origin_ref_and_sha_validation(monkeypatch):
    git, session = github(monkeypatch)
    session.get.return_value = github_read_response(document())
    loaded, sha = git.read()
    assert loaded == document()
    assert sha == sync._blob_sha(sync._document_bytes(loaded))
    assert session.get.call_args.args == (sync.CONTENTS_URL,)
    assert session.get.call_args.kwargs["params"] == {"ref": "main"}
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.trust_env is False


def test_ambiguous_timeout_is_confirmed_by_get_without_second_write(monkeypatch):
    git, session = github(monkeypatch)
    session.put.side_effect = requests.Timeout("synthetic-private-github-response")
    session.get.return_value = github_read_response(document())
    assert git.save(document(), "prior-sha", "JEH") == "saved"
    assert session.put.call_count == 1
    assert session.get.call_count == 1


def test_unconfirmed_timeout_stops_without_blind_retry(monkeypatch):
    git, session = github(monkeypatch)
    session.put.side_effect = requests.Timeout("synthetic-private-github-response")
    changed = document()
    changed["JEH"]["marker"] = "other-write"
    session.get.return_value = github_read_response(changed)
    with pytest.raises(sync.SnapshotRefreshBlocked) as caught:
        git.save(document(), "prior-sha", "JEH")
    assert caught.value.code == "SNAPSHOT_SAVE_UNCONFIRMED"
    assert "synthetic-private" not in str(caught.value)
    assert session.put.call_count == 1
    assert session.get.call_count == 1


def test_success_commit_and_blob_sha_receipt_is_verified(monkeypatch):
    git, session = github(monkeypatch)
    expected = sync._blob_sha(sync._document_bytes(document()))
    session.put.return_value = FakeResponse({"content": {"sha": expected}, "commit": {"sha": "a" * 40}})
    assert git.save(document(), "prior-sha", "JEH") == "saved"
    payload = session.put.call_args.kwargs["json"]
    assert payload["sha"] == "prior-sha"
    assert payload["branch"] == "main"
    assert payload["message"] == "Refresh confirmed JEH preset holdings"
    assert session.put.call_args.args == (sync.CONTENTS_URL,)
    assert session.put.call_args.kwargs["allow_redirects"] is False
    session.get.assert_not_called()


def test_sha_conflict_is_reported_for_read_recompute_not_direct_retry(monkeypatch):
    git, session = github(monkeypatch)
    session.put.return_value = FakeResponse({"message": "synthetic-private-conflict"}, 409)
    assert git.save(document(), "prior-sha", "JEH") == "conflict"
    assert session.put.call_count == 1
    session.get.assert_not_called()


def test_historical_rules_use_preopen_and_actual_active_configuration(monkeypatch):
    trader = Mock()
    trader._snapshot_to_positions_and_state.return_value = ([], None, None)
    trader.get_daily_recommendation.return_value = {"mode": "AG", "basis_date": "2026-10-09",
        "strategy_name": "이평 정렬", "active_config": {"buy_threshold": 16.0, "sell_threshold": 6.8, "max_hold_days": 7}}
    constructor = Mock(return_value=trader)
    monkeypatch.setattr(sync, "_KMWPlanTrader", constructor)
    monkeypatch.setattr(sync, "_kmw_config", Mock(return_value={"initial_capital": 10000,
        "session_start_date": "2025-10-30", "ma_alignment_switch_enabled": True, "seed_increases": []}))
    original = document()
    original["JEH"].update(available_cash=10000, processed_seed_dates=[])
    rules = sync._historical_buy_rules(original, "JEH", {"2026-10-12"})
    assert rules == {"2026-10-12": {"mode": "AG", "buy_threshold": 16.0, "sell_threshold": 6.8,
                                   "max_hold_days": 7, "strategy_name": "이평 정렬"}}
    instant = constructor.call_args.kwargs["now"]
    assert instant.isoformat() == "2026-10-12T08:00:00-04:00"
    assert constructor.call_args.kwargs["expected_basis_date"].isoformat() == "2026-10-09"
    trader.set_ma_alignment_switch.assert_called_once_with(True)
    trader.get_daily_recommendation.assert_called_once_with(skip_simulate=True, preserve_snapshot_shares=True)


def test_parallel_plan_and_entry_calculations_serialize_and_restore_process_streams(monkeypatch, capsys):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    planner_entered, release_planner = threading.Event(), threading.Event()
    history_attempted, history_entered = threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    active = maximum = 0
    rules = {"mode": "AG", "buy_threshold": 3.6, "sell_threshold": 3.5,
             "max_hold_days": 7, "strategy_name": "기본"}

    def enter():
        nonlocal active, maximum
        with counter_lock:
            active += 1
            maximum = max(maximum, active)

    def leave():
        nonlocal active
        with counter_lock:
            active -= 1

    def planning(*args, **kwargs):
        enter()
        try:
            print("synthetic-plan-stdout")
            print("synthetic-plan-stderr", file=sys.stderr)
            planner_entered.set()
            assert release_planner.wait(2), "test must release the serialized calculation"
            return {"preset": "JEH", "status": "ready", "snapshot_fresh": True,
                    "basis_date": "2026-10-12", "order_date": "2026-10-13", "mode": "AG",
                    "buy_entry_rules": rules, "orders": [{"side": "BUY", "round": 7,
                    "quantity": 20, "price": "132.15"}]}
        finally:
            leave()

    trader = Mock()
    trader._snapshot_to_positions_and_state.return_value = ([], None, None)
    def recommendation(**kwargs):
        enter()
        try:
            print("synthetic-entry-stdout")
            print("synthetic-entry-stderr", file=sys.stderr)
            return {"mode": "AG", "basis_date": "2026-10-09", "strategy_name": "기본",
                    "active_config": rules}
        finally:
            leave()
    trader.get_daily_recommendation.side_effect = recommendation
    def constructor(**kwargs):
        history_entered.set()
        return trader
    monkeypatch.setattr(sync, "_KMWPlanTrader", Mock(side_effect=constructor))
    monkeypatch.setattr(sync, "_kmw_config", Mock(return_value={"initial_capital": 10000,
        "session_start_date": "2025-10-30", "ma_alignment_switch_enabled": True, "seed_increases": []}))
    monkeypatch.setattr(sync, "build_kmw_plan", planning)
    monkeypatch.setattr(sync, "refresh_preset_snapshot", lambda doc, **facts: deepcopy(doc))
    source = factual_document()
    def history_calculation():
        history_attempted.set()
        return sync._historical_buy_rules(source, "JEH", {"2026-10-12"})
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(sync._candidate, source, preset="JEH", basis=sync._day("2026-10-12"),
                                fills=[], quantity=100, unresolved=[], rules={}, now=NOW)
        assert planner_entered.wait(2)
        second = executor.submit(history_calculation)
        try:
            assert history_attempted.wait(2)
            assert not history_entered.wait(0.05), "entry calculation must wait for the shared market lock"
        finally:
            release_planner.set()
        assert first.result(timeout=2)["JEH"]["pending_buy"]["quantity"] == 20
        assert second.result(timeout=2)["2026-10-12"]["mode"] == "AG"
    assert maximum == 1
    assert active == 0
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    captured = capsys.readouterr()
    assert "synthetic-plan" not in captured.out + captured.err
    assert "synthetic-entry" not in captured.out + captured.err


def test_failed_quiet_calculation_restores_streams_and_releases_market_lock(monkeypatch):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    monkeypatch.setattr(sync, "refresh_preset_snapshot", lambda doc, **facts: deepcopy(doc))
    monkeypatch.setattr(sync, "build_kmw_plan", Mock(side_effect=RuntimeError("synthetic-market-failure")))
    with pytest.raises(RuntimeError):
        sync._candidate(factual_document(), preset="JEH", basis=sync._day("2026-10-12"),
                        fills=[], quantity=100, unresolved=[], rules={}, now=NOW)
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
    with ThreadPoolExecutor(max_workers=1) as executor:
        def subsequent_calculation():
            with sync._MARKET_CALCULATION_LOCK:
                return "lock released"
        assert executor.submit(subsequent_calculation).result(timeout=2) == "lock released"
