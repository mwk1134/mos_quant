"""Shared-account handoffs from synthetic cumulative executions only."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import atr70_r2 as core
from atr70_r2_runtime import MixedBlocked
from kmw_autotrade import OrderLedger
from kmw2_hybrid import is_hybrid, mixed_owned_ids, sync_hybrid


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 16, 8, tzinfo=ET)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No live network or broker operation is permitted")
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def intent(side, day, *, quantity=5, number="11", price="100"):
    value = {"intent_id": f"R2-{side}-{number}", "strategy": "R2", "ticker": "SOXL",
             "side": side, "quantity": quantity, "price": price, "order_type": "LOC",
             "session_date": day, "lot_id": "R2:2026-11-12:0", "position_key": "R2:2026-11-12:0",
             "round": 1, "event": "R2_ENTRY" if side == "BUY" else "R2_EXIT"}
    if side == "BUY":
        value["frozen_rules"] = core.r2_rules("SF")
    return value


def buy_state():
    state = core.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=0,
        open_order_count=0, broker_confirmed=True, fee_rate="0.005")
    state["unallocated_cost_cash"] = "0.54"
    order = intent("BUY", "2026-11-12")
    state = core.register_order(state, order)
    return core.reconcile_fills(state, [{"order_id": order["intent_id"], "broker_order_id": "11",
        "cumulative_quantity": 5, "cumulative_notional": "500", "cumulative_fee": "2.500",
        "fee_source": "CONFIGURED_RESERVE", "status": "FILLED"}])


def document(state=None):
    return {"KMW2": {"atr70_r2": buy_state() if state is None else deepcopy(state),
                "atr70_r2_mix_enabled": False, "as_of_date": "2026-11-13", "available_cash": 73503.5,
                "compound_seed": 80000.0, "compound_reference_seed": 79000.0,
                "compound_trading_days": 22,
                "compound_settlements": [{"trade_date": "2026-11-06", "settlement_date": "2026-11-13", "pnl": 500}],
                "processed_seed_dates": [], "pending_buy": {"round": 7},
                "manual_buy_confirmations": [{"old": True}],
                "broker_sync": {"version": 1, "baseline_date": "2026-11-11", "executions": {}},
                "custom": {"preserve": True}},
            "KMW": {"cash": 111}, "JEH": {"cash": 222},
            "_preset_configs": {"KMW2": {"initial_capital": 74006.0, "session_start_date": "2026-11-12",
                "atr70_r2_mix_enabled": False, "ma_alignment_switch_enabled": False,
                "seed_increases": [], "position_edits": {}}}}


def history(side, number, day, quantity, price):
    return {"iscd": "SOXL", "oder_date": day.replace("-", ""), "oder_no": number,
            "seln_byov_clcd": "2" if side == "BUY" else "1", "cntg_dqty": quantity,
            "cntg_crnc_prc": str(price), "cntg_camt": str(Decimal(str(price)) * quantity)}


class Broker:
    def __init__(self):
        self.quantity = 5
        self.history = [history("BUY", "11", "2026-11-12", 5, 100)]
        self.today = []
        self.reservations = []
        self.orderable = "73504.04"
        self.collateral = "0"
        self.submissions = []

    def holdings(self):
        return ([{"iscd": "SOXL", "rmnd_dqty": self.quantity, "seln_able_dqty": self.quantity,
                  "rmnd_clcd": "1", "loan_dtl_type_code": "0000", "ovrs_exch_vndr_idno": "0066"}]
                if self.quantity else [])

    def orders_history(self, start, end):
        return [deepcopy(row) for row in self.history if start <= row["oder_date"] <= end]

    def orders_today(self):
        return deepcopy(self.today)

    def reserved_orders(self, *args, **kwargs):
        return deepcopy(self.reservations)

    def order_estimate(self, **kwargs):
        return [{"crcd": "USD", "oder_crnc_oder_able_camt": self.orderable,
                 "othr_crnc_evdn_camt": self.collateral}]

    def currencies(self):
        return [{"crcd": "USD", "frcr_amt": self.orderable, "estm_frcr_amt": self.orderable}]

    def submit_order(self, **kwargs):
        self.submissions.append(kwargs)
        raise AssertionError("Coordinator must not submit")

    submit_mixed_order = submit_order


class Github:
    def __init__(self, doc):
        self.document = deepcopy(doc)
        self.saves = []
        self.conflicts = []

    def save(self, candidate, sha, preset):
        self.saves.append((deepcopy(candidate), sha, preset))
        if self.conflicts:
            self.document = self.conflicts.pop(0)
            return "conflict"
        self.document = deepcopy(candidate)
        return "saved"

    def read(self):
        return deepcopy(self.document), "fresh-sha"


def sync(source, broker, tmp_path, *, github=None, now=NOW, should_continue=None):
    github = Github(source) if github is None else github
    return sync_hybrid(source, "sha", github=github, client=broker,
        ledger_path=tmp_path / "orders.sqlite3", account="account", now=now,
        should_continue=should_continue)


def test_first_off_keeps_owned_lots_and_frozen_rules_transfers_only_free_cash(tmp_path):
    source, broker = document(), Broker()
    before = deepcopy(source)
    actual, result = sync(source, broker, tmp_path)
    snapshot, state = actual["KMW2"], actual["KMW2"]["atr70_r2"]
    assert result["state"] == "DONGPA_STARTED" and result["allow_legacy_schedule"] is True
    assert is_hybrid(snapshot) and result["mixed_quantity"] == 5 and result["legacy_quantity"] == 0
    assert state["r2"]["lots"] == source["KMW2"]["atr70_r2"]["r2"]["lots"]
    assert snapshot["available_cash"] == snapshot["compound_seed"] == snapshot["compound_reference_seed"] == 73503.5
    assert Decimal(state["atr"]["cash"]) + Decimal(state["r2"]["cash"]) == 0
    assert sum(Decimal(item["amount"]) for item in state["cash_transfers"]) == Decimal("73503.500")
    assert snapshot["compound_settlements"] == [] and snapshot["compound_trading_days"] == 0
    assert snapshot["broker_sync"]["baseline_date"] == snapshot["as_of_date"] == "2026-11-13"
    assert "pending_buy" not in snapshot and "manual_buy_confirmations" not in snapshot
    assert snapshot["strategyHistory"][0]["snapshot_before"]["compound_seed"] == 80000
    assert "strategyHistory" not in snapshot["strategyHistory"][0]["snapshot_before"]
    assert actual["_preset_configs"] == source["_preset_configs"]
    assert {k: v for k, v in actual.items() if k != "KMW2"} == {k: v for k, v in source.items() if k != "KMW2"}
    assert before == source and broker.submissions == []
    core.validate_state(state)


def test_repeat_is_idempotent_and_does_not_reset_dongpa_seed(tmp_path):
    first, _ = sync(document(), Broker(), tmp_path)
    first["KMW2"]["compound_seed"] = 73523.5
    second, result = sync(first, Broker(), tmp_path)
    assert result["state"] == "HYBRID_ACTIVE"
    assert second == first


def test_sale_proceeds_are_full_capital_credit_without_dongpa_profit_settlement(tmp_path):
    broker = Broker()
    first, _ = sync(document(), broker, tmp_path)
    old = first["KMW2"]["atr70_r2"]
    sale = intent("SELL", "2026-11-16", number="12", price="110")
    first["KMW2"]["atr70_r2"] = core.register_order(old, sale, broker_order_id="12")
    broker.history.append(history("SELL", "12", "2026-11-16", 5, 110))
    broker.quantity = 0
    broker.orderable = "74051.29"
    final, result = sync(first, broker, tmp_path, now=NOW.replace(hour=17))
    snapshot = final["KMW2"]
    assert result["state"] == "DONGPA_RESUMED" and result["handled"] is False
    assert "atr70_r2" not in snapshot
    assert snapshot["available_cash"] == snapshot["compound_seed"] == snapshot["compound_reference_seed"] == 74050.75
    assert snapshot["compound_settlements"] == []
    assert Decimal(snapshot["strategyTransition"]["transferred_cash"]) == Decimal("74050.750")
    assert snapshot["strategyHistory"][-1]["mixed_state"]["r2"]["settlements"]
    assert ("2026-11-16", "12") in mixed_owned_ids(snapshot)
    assert final["_preset_configs"]["KMW2"]["initial_capital"] == 74006


def test_buy_reservation_is_not_transferred_and_later_cancel_releases_cash(tmp_path):
    source, broker = document(), Broker()
    state = source["KMW2"]["atr70_r2"]
    pending = intent("BUY", "2026-11-16", quantity=2, number="13")
    pending["lot_id"] = pending["position_key"] = "R2:2026-11-16:1"
    state = core.register_order(state, pending, broker_order_id="13")
    source["KMW2"]["atr70_r2"] = state
    broker.today = [{"iscd": "SOXL", "oder_date": "20261116", "oder_no": "13",
        "odqt": 2, "cntg_qty": 0, "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0,
        "seln_byov_clcd": "2", "ovrs_stck_oder_stat_cls_code": "0", "aspr_type_code": "5"}]
    broker.orderable = "73303.04"
    first, _ = sync(source, broker, tmp_path)
    assert first["KMW2"]["available_cash"] == 73302.5
    assert first["KMW2"]["atr70_r2"]["r2"]["cash"] == "201.000"
    broker.today[0]["cncl_cnfr_qty"] = 2
    broker.orderable = "73504.04"
    second, _ = sync(first, broker, tmp_path)
    assert second["KMW2"]["available_cash"] == 73503.5


def test_usd_orderable_caps_credit_and_leaves_untransferred_cash_owned(tmp_path):
    broker = Broker()
    broker.orderable = "1000"
    actual, _ = sync(document(), broker, tmp_path)
    assert actual["KMW2"]["available_cash"] == actual["KMW2"]["compound_seed"] == 1000
    assert sum(Decimal(actual["KMW2"]["atr70_r2"][leg]["cash"]) for leg in ("atr", "r2")) == Decimal("72503.500")


@pytest.mark.parametrize("change,code", [
    ("foreign_fill", "FOREIGN_FILL"), ("unmatched_holdings", "HOLDINGS_MISMATCH"),
    ("foreign_open", "FOREIGN_ORDER"), ("collateral", "BUYING_POWER"),
])
def test_unknown_account_changes_fail_closed_without_write(tmp_path, change, code):
    source, broker = document(), Broker()
    if change == "foreign_fill":
        broker.history.append(history("BUY", "999", "2026-11-13", 1, 100))
        broker.quantity += 1
    elif change == "unmatched_holdings":
        broker.quantity += 1
    elif change == "foreign_open":
        broker.today = [{"iscd": "SOXL", "oder_date": "20261116", "oder_no": "999", "odqt": 1,
            "cntg_qty": 0, "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0, "seln_byov_clcd": "2",
            "ovrs_stck_oder_stat_cls_code": "0", "aspr_type_code": "5"}]
    else:
        broker.collateral = "1"
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, broker, tmp_path, github=github)
    assert error.value.code == code
    assert github.saves == [] and broker.submissions == []


def test_conflict_reloads_recomputes_and_preserves_other_presets(tmp_path):
    source = document()
    github = Github(source)
    changed = deepcopy(source)
    changed["JEH"]["cash"] = 333
    github.conflicts = [changed]
    actual, _ = sync(source, Broker(), tmp_path, github=github)
    assert actual["JEH"]["cash"] == 333
    assert len(github.saves) == 2 and github.saves[-1][1] == "fresh-sha"
    assert len(actual["KMW2"]["atr70_r2"]["cash_transfers"]) == 2


def test_conflict_new_on_request_prevents_stale_off_transition(tmp_path):
    source = document()
    github = Github(source)
    changed = deepcopy(source)
    changed["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] = True
    github.conflicts = [changed]
    with pytest.raises(MixedBlocked) as error:
        sync(source, Broker(), tmp_path, github=github)
    assert error.value.code == "SWITCH_ENABLED"
    assert len(github.saves) == 1 and github.document == changed


def test_unprocessed_past_seed_blocks_and_future_seed_remains(tmp_path):
    source = document()
    source["_preset_configs"]["KMW2"]["seed_increases"] = [{"date": "2026-11-12", "amount": 100}]
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, Broker(), tmp_path, github=github)
    assert error.value.code == "SEED_CHECKPOINT_MISMATCH" and github.saves == []
    source["_preset_configs"]["KMW2"]["seed_increases"] = [{"date": "2026-12-01", "amount": 100}]
    actual, _ = sync(source, Broker(), tmp_path)
    assert actual["_preset_configs"] == source["_preset_configs"]


def test_waiting_off_keeps_legacy_book_without_broker_reads(tmp_path):
    source = document(core.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=1,
        open_order_count=0, broker_confirmed=False))
    broker = Mock()
    actual, result = sync(source, broker, tmp_path)
    assert result["state"] == "DONGPA_RESUMED" and "atr70_r2" not in actual["KMW2"]
    for field in ("available_cash", "compound_seed", "compound_settlements", "broker_sync", "pending_buy"):
        assert actual["KMW2"][field] == source["KMW2"][field]
    assert broker.mock_calls == []


def test_stopped_attempt_never_reads_broker_or_saves(tmp_path):
    source, broker = document(), Mock()
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, broker, tmp_path, github=github, should_continue=lambda: False)
    assert error.value.code == "USER_STOPPED" and github.saves == [] and broker.mock_calls == []


def ledger_buy(tmp_path):
    order = {"ticker": "SOXL", "side": "BUY", "quantity": 2, "price": "100", "order_type": "LOC",
             "round": 1, "position_key": "1_2026-11-16"}
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    identity = ledger.claim("account", {"preset": "KMW2", "order_date": "2026-11-16", "orders": [order]})[0]
    ledger.update(identity, "DIRECT_ACCEPTED", {"data": {"oder_no": "21"}})
    ledger.db.close()


def test_new_dongpa_fill_uses_only_legacy_mapping_and_full_frozen_rules(tmp_path):
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    ledger_buy(tmp_path)
    rules = {"mode": "SF", "buy_threshold": 3.5, "sell_threshold": 1.1,
             "max_hold_days": 35, "strategy_name": "기본"}
    source["KMW2"]["pending_buy"] = {"round": 1, "quantity": 2, "order_date": "2026-11-16", **rules}
    broker.history.append(history("BUY", "21", "2026-11-16", 2, 100))
    broker.quantity = 7
    broker.orderable = "73304.04"
    actual, result = sync(source, broker, tmp_path, now=NOW.replace(hour=17))
    assert result["mixed_quantity"] == 5 and result["legacy_quantity"] == 2
    assert actual["KMW2"]["1_2026-11-16"] == {**rules, "round": 1, "shares": 2,
                                              "buy_price": 100.0, "amount": 200.0}
    assert actual["KMW2"]["available_cash"] == 73303.5
    assert set(actual["KMW2"]["broker_sync"]["executions"]) == {"2026-11-16:21"}


def test_current_session_legacy_fill_blocks_new_legacy_but_retains_old_exits(tmp_path):
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    ledger_buy(tmp_path)
    broker.history.append(history("BUY", "21", "2026-11-16", 2, 100))
    broker.quantity = 7
    broker.orderable = "73304.04"
    actual, result = sync(source, broker, tmp_path, now=NOW.replace(hour=15))
    assert result["state"] == "HYBRID_ACTIVE" and result["allow_legacy_schedule"] is False
    assert result["mixed_quantity"] == 5 and result["legacy_quantity"] == 2
    assert result["legacy_snapshot_quantity"] == 0
    assert actual["KMW2"]["as_of_date"] == "2026-11-13" and "1_2026-11-16" not in actual["KMW2"]
    assert actual["KMW2"]["atr70_r2"]["r2"]["lots"][0]["shares"] == 5


def test_pending_rebalance_keeps_all_old_cash_without_blocking_frozen_exit_book(tmp_path):
    source = document()
    source["KMW2"]["atr70_r2"]["pending_rebalance"] = core.plan_monthly_rebalance(
        source["KMW2"]["atr70_r2"], session_date="2026-12-01", valuation_price="100")
    actual, result = sync(source, Broker(), tmp_path)
    assert result["state"] == "DONGPA_STARTED" and result["mixed_quantity"] == 5
    assert actual["KMW2"]["available_cash"] == 0
    assert actual["KMW2"]["compound_seed"] == .01
    assert "cash_transfers" not in actual["KMW2"]["atr70_r2"]
    assert actual["KMW2"]["atr70_r2"]["pending_rebalance"] == source["KMW2"]["atr70_r2"]["pending_rebalance"]


def test_on_during_existing_hybrid_keeps_old_book_and_new_legacy_holdings(tmp_path):
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    source["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] = True
    actual, result = sync(source, broker, tmp_path)
    assert result["state"] == "HYBRID_ACTIVE" and result["allow_legacy_schedule"]
    assert actual == source and actual["KMW2"]["available_cash"] == 73503.5


def test_same_session_flat_old_book_is_retained_until_its_close(tmp_path):
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    sale = intent("SELL", "2026-11-16", number="12", price="110")
    source["KMW2"]["atr70_r2"] = core.register_order(source["KMW2"]["atr70_r2"], sale, broker_order_id="12")
    broker.history.append(history("SELL", "12", "2026-11-16", 5, 110))
    broker.quantity, broker.orderable = 0, "74051.29"
    actual, result = sync(source, broker, tmp_path, now=NOW.replace(hour=10))
    assert result["state"] == "HYBRID_ACTIVE" and "atr70_r2" in actual["KMW2"]
    assert result["mixed_quantity"] == 0 and actual["KMW2"]["available_cash"] == 74050.75
    assert actual["KMW2"]["as_of_date"] == "2026-11-13"


def test_actual_mixed_fill_regression_blocks_before_saving(tmp_path):
    source, broker = document(), Broker()
    broker.history[0]["cntg_dqty"] = 4
    broker.history[0]["cntg_camt"] = "400"
    broker.quantity = 4
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, broker, tmp_path, github=github)
    assert error.value.code == "FILL_MISMATCH" and github.saves == []


def test_repeated_conflicts_have_bounded_recomputation_without_double_credit(tmp_path):
    source = document()
    github = Github(source)
    github.conflicts = [deepcopy(source) for _ in range(3)]
    with pytest.raises(MixedBlocked) as error:
        sync(source, Broker(), tmp_path, github=github)
    assert error.value.code == "SNAPSHOT_CONFLICT" and len(github.saves) == 3
    assert github.document == source


def test_broker_inventory_change_after_credit_check_prevents_save(tmp_path):
    source, broker = document(), Broker()
    original_estimate = broker.order_estimate
    def racing_estimate(**kwargs):
        broker.quantity += 1
        return original_estimate(**kwargs)
    broker.order_estimate = racing_estimate
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, broker, tmp_path, github=github)
    assert error.value.code == "HOLDINGS_MISMATCH" and github.saves == []


def test_missing_credit_counterpart_cannot_repeat_or_forget_a_transfer(tmp_path):
    source, _ = sync(document(), Broker(), tmp_path)
    source["KMW2"]["strategyTransition"]["capital_credits"].pop()
    github = Github(source)
    with pytest.raises(MixedBlocked) as error:
        sync(source, Broker(), tmp_path, github=github)
    assert error.value.code == "CASH_TRANSFER_MISMATCH" and github.saves == []


def test_current_legacy_projection_survives_store_read_and_second_runtime_reconcile(tmp_path):
    from atr70_r2_adapter import MixedSnapshotStore
    from atr70_r2_runtime import run_mixed_tick
    from kmw2_hybrid import make_broker_view
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    ledger_buy(tmp_path)
    broker.history.append(history("BUY", "21", "2026-11-16", 2, 100))
    broker.quantity, broker.orderable = 7, "73304.04"
    moment = NOW.replace(hour=15)
    actual, result = sync(source, broker, tmp_path, now=moment)
    github = Github(actual)
    store = MixedSnapshotStore({}, github=github)
    context = store.read(moment)
    assert context["hybrid_managed"] and context["legacy_shares"] == 0
    client = make_broker_view(broker, actual, strategy="MIXED", ledger_path=tmp_path / "orders.sqlite3",
        account="account", now=moment, mixed_quantity=result["mixed_quantity"],
        legacy_quantity=result["legacy_quantity"])
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    try:
        run = run_mixed_tick(client, context["state"], context,
            lambda state: store.save(state, moment), ledger, now=moment, enabled=False,
            account="account", exchange_code="0066", should_continue=lambda: False)
    finally:
        ledger.db.close()
    assert run["handled"] and run["orders"] == []
    assert github.document["KMW2"]["as_of_date"] == "2026-11-13"
    assert github.document["KMW2"]["available_cash"] == actual["KMW2"]["available_cash"]
    assert github.document["KMW2"]["atr70_r2"]["r2"]["lots"][0]["shares"] == 5
    assert broker.submissions == []


def test_frozen_ledger_entry_rules_restore_new_lot_without_pending_or_market_lookup(tmp_path, monkeypatch):
    from preset_snapshot_sync import _frozen_buy_rules
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    rules = {"mode": "AG", "buy_threshold": 3.6, "sell_threshold": 3.5,
             "max_hold_days": 7, "strategy_name": "기본"}
    order = {"ticker": "SOXL", "side": "BUY", "quantity": 2, "price": "100", "order_type": "LOC",
             "round": 1, "position_key": "1_2026-11-16", "entry_rules": _frozen_buy_rules(rules)}
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    try:
        identity = ledger.claim("account", {"preset": "KMW2", "order_date": "2026-11-16", "orders": [order]})[0]
        ledger.update(identity, "DIRECT_ACCEPTED", {"data": {"oder_no": "21"}})
    finally:
        ledger.db.close()
    monkeypatch.setattr("preset_snapshot_sync._historical_buy_rules",
                        Mock(side_effect=AssertionError("Frozen receipt must avoid historical prices")))
    broker.history.append(history("BUY", "21", "2026-11-16", 2, 100))
    broker.quantity, broker.orderable = 7, "73304.04"
    actual, _ = sync(source, broker, tmp_path, now=NOW.replace(hour=17))
    assert actual["KMW2"]["1_2026-11-16"]["mode"] == "AG"
    assert all(actual["KMW2"]["1_2026-11-16"][key] == value for key, value in rules.items())


def test_server_legacy_refresh_after_archive_excludes_old_mixed_sale_forever(tmp_path, monkeypatch):
    import preset_snapshot_sync as synchronizer
    broker = Broker()
    source, _ = sync(document(), broker, tmp_path)
    sale = intent("SELL", "2026-11-16", number="12", price="110")
    source["KMW2"]["atr70_r2"] = core.register_order(source["KMW2"]["atr70_r2"], sale, broker_order_id="12")
    broker.history.append(history("SELL", "12", "2026-11-16", 5, 110))
    broker.quantity, broker.orderable = 0, "74051.29"
    archived, result = sync(source, broker, tmp_path, now=NOW.replace(hour=17))
    assert result["state"] == "DONGPA_RESUMED"
    github = Github(archived)
    github.close = Mock()
    monkeypatch.setattr(synchronizer, "_GithubSnapshots", lambda token: github)
    monkeypatch.setattr(synchronizer, "validate_account_configuration", lambda *args, **kwargs: None)
    monkeypatch.setattr(synchronizer, "account_id", lambda *args, **kwargs: "account")
    monkeypatch.setattr(synchronizer.MeritzClient, "from_env", lambda **kwargs: broker)
    monkeypatch.setattr(synchronizer, "build_kmw_plan", lambda doc, **kwargs: {
        "preset": "KMW2", "status": "ready", "snapshot_fresh": True,
        "basis_date": "2026-11-17", "order_date": "2026-11-18", "orders": []})
    refreshed, _ = synchronizer.refresh_and_load_snapshots(preset="KMW2",
        env={"GITHUB_TOKEN": "synthetic-test-token"}, ledger_path=tmp_path / "orders.sqlite3",
        now=datetime(2026, 11, 17, 17, tzinfo=ET))
    assert refreshed["KMW2"]["available_cash"] == refreshed["KMW2"]["compound_seed"] == 74050.75
    assert refreshed["KMW2"]["broker_sync"]["executions"] == {}
    assert refreshed["KMW2"]["compound_settlements"] == []
    assert refreshed["KMW2"]["as_of_date"] == "2026-11-17"
    assert refreshed["KMW2"]["strategyHistory"] == archived["KMW2"]["strategyHistory"]


def test_future_active_configuration_waits_without_fabricating_transfer_or_reading_broker(tmp_path):
    state = core.initial_state(session_date="2026-11-17", broker_cash="74006", broker_shares=0,
        open_order_count=0, broker_confirmed=True)
    source = document(state)
    before = deepcopy(source)
    broker, github = Mock(), Github(source)
    actual, result = sync(source, broker, tmp_path, github=github)
    assert result["state"] == "WAITING_ACTIVATION_SESSION"
    assert result["handled"] and result["allow_legacy_schedule"] is False and result["orders"] == []
    assert actual == source == before and github.saves == [] and broker.mock_calls == []
    assert "cash_transfers" not in actual["KMW2"]["atr70_r2"]


def test_future_off_wait_rechecks_flag_and_transfers_only_after_actual_us_start_day(tmp_path):
    state = core.initial_state(session_date="2026-11-17", broker_cash="74006", broker_shares=0,
        open_order_count=0, broker_confirmed=True)
    source = document(state)
    source["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] = True
    with pytest.raises(MixedBlocked) as error:
        sync(source, Mock(), tmp_path)
    assert error.value.code == "SWITCH_ENABLED"
    source["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] = False
    broker = Broker()
    broker.history, broker.quantity, broker.orderable = [], 0, "74006.54"
    actual, result = sync(source, broker, tmp_path, now=datetime(2026, 11, 17, 8, tzinfo=ET))
    assert result["state"] == "DONGPA_RESUMED"
    assert actual["KMW2"]["available_cash"] == actual["KMW2"]["compound_seed"] == 74006
    archived = actual["KMW2"]["strategyHistory"][-1]["mixed_state"]
    assert all(item["session_date"] == "2026-11-17" for item in archived["cash_transfers"])
