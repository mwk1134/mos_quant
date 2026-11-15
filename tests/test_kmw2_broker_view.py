"""Strategy read views verify full account ownership without real transport."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

import atr70_r2 as core
from atr70_r2_runtime import MixedBlocked
from kmw2_broker_view import legacy_owned_ids, make_broker_view, mixed_owned_ids
from kmw_autotrade import OrderLedger
from meritz_api import SubmissionStopped


NOW = datetime(2026, 11, 13, 8, tzinfo=ZoneInfo("America/New_York"))
ACCOUNT = "synthetic-view-account"


@pytest.fixture(autouse=True)
def no_network():
    with patch("requests.Session.request", side_effect=AssertionError("No real broker or network")):
        yield


def snapshot():
    state = core.initial_state(session_date="2026-11-12", broker_cash="2000", broker_shares=0,
                              open_order_count=0, broker_confirmed=True, fee_rate="0.005")
    buy = core._intent("ATR", "BUY", 7, "100", "MARKET", "2026-11-12", "ATR:2026-11-12",
                       "ATR_BREAKOUT", frozen_rules=deepcopy(core.ATR_RULES), exit_session="2026-11-13")
    state = core.register_order(state, buy)
    state = core.mark_order(state, buy["intent_id"], broker_order_id="101", status="ACCEPTED")
    state = core.reconcile_fills(state, [{"order_id": buy["intent_id"], "broker_order_id": "101",
        "cumulative_quantity": 7, "cumulative_notional": "700", "cumulative_fee": "3.5", "status": "FILLED"}])
    sell = core._intent("ATR", "SELL", 7, "0", "MOO", "2026-11-13", "ATR:2026-11-12", "ATR_EXIT")
    state = core.register_order(state, sell)
    state = core.mark_order(state, sell["intent_id"], broker_order_id="102", status="ACCEPTED")
    return {"KMW2": {"as_of_date": "2026-11-12", "available_cash": 300,
        "strategyTransition": {"mode": "DONGPA"}, "atr70_r2": state,
        "1_2026-11-12": {"round": 1, "shares": 3, "buy_price": 100, "mode": "SF"},
        "broker_sync": {"baseline_date": "2026-11-11", "executions": {"2026-11-12:201": {
            "trade_date": "2026-11-12", "side": "BUY", "quantity": 3, "position_key": "1_2026-11-12"}}},
        "strategyHistory": [{"mixed_state": {"orders": {"old": {
            "session_date": "2026-10-30", "broker_order_id": "901", "side": "SELL",
            "quantity": 2, "cumulative_quantity": 2, "status": "FILLED"}}}}]},
        "_preset_configs": {"KMW2": {"session_start_date": "2026-11-12"}}}


def order(number="102", *, day="20261113", side="1", quantity=7, filled=0, **changes):
    return {"iscd": "SOXL", "oder_date": day, "oder_no": number, "orgl_oder_no": "0",
            "seln_byov_clcd": side, "odqt": quantity, "cntg_qty": filled,
            "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0, "ovrs_stck_oder_stat_cls_code": "0",
            "aspr_type_code": "6", **changes}


def fill(number, *, day="20261112", side="2", quantity=3, price="100"):
    return {"iscd": "SOXL", "oder_date": day, "oder_no": number, "seln_byov_clcd": side,
            "cntg_dqty": quantity, "cntg_crnc_prc": price, "cntg_camt": str(Decimal(price) * quantity)}


class Broker:
    def __init__(self):
        self.quantity = 10
        self.sellable = 8
        self.today = [order()]
        self.history = [fill("101", quantity=7), fill("201"),
                        fill("901", day="20261030", side="1", quantity=2)]
        self.reservations = []
        self.usd = "5000"
        self.collateral = "0"
        self.submissions = []

    def holdings(self):
        return [{"iscd": "SOXL", "rmnd_dqty": self.quantity, "seln_able_dqty": self.sellable,
                 "rmnd_clcd": "1", "loan_dtl_type_code": "0000", "ovrs_exch_vndr_idno": "0066"}]

    def orders_today(self, *args, **kwargs):
        return deepcopy(self.today)

    def orders_history(self, start, end, *args, **kwargs):
        return deepcopy([row for row in self.history if start <= row["oder_date"] <= end])

    def reserved_orders(self, *args, **kwargs):
        return deepcopy(self.reservations)

    def order_estimate(self, integrated_margin=False):
        assert integrated_margin is False
        return [{"crcd": "USD", "oder_crnc_oder_able_camt": self.usd,
                 "othr_crnc_evdn_camt": self.collateral, "unchanged": "broker field"}]

    def currencies(self):
        return [{"crcd": "USD", "frcr_amt": self.usd, "estm_frcr_amt": self.usd}]

    def _submit(self, kind, kwargs):
        self.submissions.append((kind, deepcopy(kwargs)))
        return {"data": {"oder_no": "test-receipt"}}

    def submit_order(self, **kwargs):
        return self._submit("submit_order", kwargs)

    def submit_mixed_order(self, **kwargs):
        return self._submit("submit_mixed_order", kwargs)

    def submit_reserved_order(self, **kwargs):
        return self._submit("submit_reserved_order", kwargs)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "view.sqlite3"
    value = OrderLedger(path)
    try:
        yield value, path
    finally:
        value.db.close()


def view(broker, document, ledger_path, strategy="DONGPA", **kwargs):
    return make_broker_view(broker, document, strategy=strategy, ledger_path=ledger_path,
                            account=ACCOUNT, now=NOW, **kwargs)


def add_receipt(ledger, *, number="202", side="SELL", quantity=3, state="DIRECT_ACCEPTED", mixed=False):
    payload = {"ticker": "SOXL", "side": side, "quantity": quantity,
               "round": 1, "position_key": "1_2026-11-12", **({"intent_id": "mixed-identity"} if mixed else {})}
    plan = {"preset": "KMW2", "order_date": "2026-11-13", "orders": [payload]}
    identity = ledger.claim(ACCOUNT, plan)[0]
    key = "oder_rsev_mst_no" if state == "RESERVED" else "oder_no"
    ledger.update(identity, state, {"data": {key: number}})


def test_two_views_keep_exact_owned_facts_and_inventory_sum_without_mutating_input(ledger):
    database, path = ledger
    add_receipt(database)
    source, broker = snapshot(), Broker()
    broker.today.append(order("202", quantity=3, aspr_type_code="5"))
    original = deepcopy(source), deepcopy(broker.__dict__)
    mixed, dongpa = view(broker, source, path, "MIXED"), view(broker, source, path)
    assert mixed.holdings()[0]["rmnd_dqty"] == 7
    assert dongpa.holdings()[0]["rmnd_dqty"] == 3
    assert mixed.holdings()[0]["seln_able_dqty"] == 7 and dongpa.holdings()[0]["seln_able_dqty"] == 3
    assert [row["oder_no"] for row in mixed.orders_today()] == ["102"]
    assert [row["oder_no"] for row in dongpa.orders_today()] == ["202"]
    assert [row["oder_no"] for row in mixed.orders_history("20261001", "20261113")] == ["101"]
    assert [row["oder_no"] for row in dongpa.orders_history("20261001", "20261113")] == ["201"]
    assert ("2026-10-30", "901") in mixed_owned_ids(source["KMW2"])
    assert legacy_owned_ids(source, path, ACCOUNT) == {("2026-11-12", "201"), ("2026-11-13", "202")}
    assert source == original[0] and broker.__dict__ == original[1]
    projected = dongpa.holdings()
    projected[0]["rmnd_dqty"] = 999
    assert broker.quantity == 10 and dongpa.holdings()[0]["rmnd_dqty"] == 3


def test_usd_estimate_caps_strategy_cash_and_keeps_real_cash_separate(ledger):
    _, path = ledger
    source, broker = snapshot(), Broker()
    mixed, dongpa = view(broker, source, path, "MIXED"), view(broker, source, path)
    assert Decimal(mixed.order_estimate()[0]["oder_crnc_oder_able_camt"]) == Decimal("1296.5")
    assert Decimal(dongpa.order_estimate()[0]["oder_crnc_oder_able_camt"]) == Decimal("300")
    assert dongpa.order_estimate()[0]["unchanged"] == "broker field"
    broker.usd = "42.75"
    assert dongpa.order_estimate()[0]["oder_crnc_oder_able_camt"] == "42.75"
    assert dongpa.currencies() == broker.currencies()
    broker.collateral = "1"
    with pytest.raises(MixedBlocked) as blocked:
        dongpa.order_estimate()
    assert blocked.value.code == "BUYING_POWER"
    with pytest.raises(MixedBlocked):
        dongpa.order_estimate(integrated_margin=True)


@pytest.mark.parametrize("case", ["holding", "incomplete_projection", "projection_total", "foreign_open", "foreign_reservation"])
def test_incomplete_ownership_or_inventory_blocks_view_creation(ledger, case):
    _, path = ledger
    source, broker = snapshot(), Broker()
    kwargs = {}
    if case == "holding":
        broker.quantity = 11
    elif case == "incomplete_projection":
        kwargs["mixed_quantity"] = 7
    elif case == "projection_total":
        kwargs.update(mixed_quantity=7, legacy_quantity=4)
    elif case == "foreign_open":
        broker.today.append(order("999", side="2", quantity=1, aspr_type_code="5"))
    else:
        broker.reservations = [{"iscd": "SOXL", "rsev_cncl_yn": "N"}]
    with pytest.raises(MixedBlocked):
        view(broker, source, path, **kwargs)


def test_projected_inventory_overrides_require_both_quantities_and_exact_broker_total(ledger):
    _, path = ledger
    source, broker = snapshot(), Broker()
    broker.quantity = 11
    projected = view(broker, source, path, mixed_quantity=7, legacy_quantity=4)
    assert projected.holdings()[0]["rmnd_dqty"] == 4
    broker.quantity = 12
    with pytest.raises(MixedBlocked) as blocked:
        projected.orders_history("20261112", "20261113")
    assert blocked.value.code == "HOLDINGS_MISMATCH"


@pytest.mark.parametrize("case", ["foreign", "wrong_side", "overfill", "archive_changed"])
def test_all_confirmed_fills_are_verified_before_strategy_filtering(ledger, case):
    _, path = ledger
    source, broker = snapshot(), Broker()
    projected = view(broker, source, path)
    if case == "foreign":
        broker.history.append(fill("999", quantity=1))
    elif case == "wrong_side":
        broker.history[0]["seln_byov_clcd"] = "1"
    elif case == "overfill":
        broker.history[0] = fill("101", quantity=8)
    else:
        broker.history[-1] = fill("901", day="20261030", side="1", quantity=1)
    with pytest.raises(MixedBlocked):
        projected.orders_history("20261001", "20261113")


def test_unknown_baseline_day_fill_is_checkpointed_but_later_unmapped_manual_fill_is_not_owned(ledger):
    _, path = ledger
    source, broker = snapshot(), Broker()
    source["KMW2"]["manual_buy_confirmations"] = [
        {"trade_date": "2026-11-12", "quantity": 1, "price": "100"}]
    broker.history.append(fill("800", day="20261111", quantity=1))
    projected = view(broker, source, path)
    assert [row["oder_no"] for row in projected.orders_history("20261111", "20261113")] == ["201"]
    broker.history.append(fill("801", quantity=1))
    with pytest.raises(MixedBlocked) as blocked:
        projected.orders_history("20261111", "20261113")
    assert blocked.value.code == "FOREIGN_FILL"


def test_unknown_sqlite_submission_blocks_all_views_and_mixed_payload_is_never_legacy_owned(ledger):
    database, path = ledger
    add_receipt(database, number="888", mixed=True)
    assert ("2026-11-13", "888") not in legacy_owned_ids(snapshot(), path, ACCOUNT)
    identity = database.db.execute("SELECT identity FROM orders").fetchone()[0]
    database.update(identity, "UNKNOWN")
    broker = Broker()
    broker.holdings = Mock(side_effect=AssertionError("Block before brokerage reads"))
    with pytest.raises(MixedBlocked) as blocked:
        view(broker, snapshot(), path)
    assert blocked.value.code == "UNRESOLVED_ORDER"


def test_cancellation_child_inherits_parent_ownership_without_changing_actual_ids(ledger):
    database, path = ledger
    add_receipt(database)
    source, broker = snapshot(), Broker()
    terms = {"ovrs_exch_vndr_idno": "0066", "aspr_type_code": "5", "oder_cond_cls_code": "7",
             "ovrs_stck_oder_prc": "110", "oder_rctf_cncl_cls_code": "0"}
    parent = order("202", quantity=3, **terms)
    child = {**parent, "oder_no": "203", "orgl_oder_no": "202", "cncl_cnfr_qty": 3,
             "oder_rctf_cncl_cls_code": "2"}
    broker.today += [parent, child]
    projected = view(broker, source, path)
    result = projected.orders_today()
    assert [row["oder_no"] for row in result] == ["202", "203"]
    assert result[0]["cncl_cnfr_qty"] == 3
    assert broker.today[1]["cncl_cnfr_qty"] == 0


@pytest.mark.parametrize("change", ["wrong_side", "wrong_quantity", "cross_strategy_parent"])
def test_owned_open_order_cannot_change_terms_or_claim_another_strategy_parent(ledger, change):
    database, path = ledger
    source, broker = snapshot(), Broker()
    if change == "wrong_side":
        broker.today[0]["seln_byov_clcd"] = "2"
    elif change == "wrong_quantity":
        broker.today[0]["odqt"] = 6
    else:
        add_receipt(database, number="202", quantity=7)
        broker.today.append(order("202", orgl_oder_no="102", quantity=7))
    with pytest.raises(MixedBlocked):
        view(broker, source, path)


def test_total_open_and_reservation_sell_quantity_cannot_exceed_account_inventory(ledger):
    database, path = ledger
    add_receipt(database, number="301", quantity=4, state="RESERVED")
    broker = Broker()
    broker.reservations = [{"iscd": "SOXL", "oder_rsev_mst_date": "20261113", "oder_rsev_mst_no": "301",
                            "rsev_cncl_yn": "N", "seln_byov_clcd": "1", "odqt": 4}]
    with pytest.raises(MixedBlocked) as blocked:
        view(broker, snapshot(), path)
    assert blocked.value.code == "SELLABLE"


@pytest.mark.parametrize("method", ["submit_order", "submit_mixed_order", "submit_reserved_order"])
def test_submission_kwargs_delegate_unchanged_and_callback_stops_before_transport(ledger, method):
    _, path = ledger
    source, broker = snapshot(), Broker()
    parameters = {"side": "buy", "symbol": "SOXL", "quantity": 3, "price": "12.34",
                  "exchange_vendor_code": "0066", "order_type": "LOC", "extra": {"source": "original"}}
    before = deepcopy(parameters)

    def guard(request):
        request["extra"]["source"] = "guard mutation"
        return True

    projected = view(broker, source, path, before_submission=guard)
    getattr(projected, method)(**parameters)
    assert broker.submissions == [(method, before)] and parameters == before
    stopped = view(broker, source, path, before_submission=lambda request: False)
    with pytest.raises(SubmissionStopped):
        getattr(stopped, method)(**parameters)
    assert len(broker.submissions) == 1


def test_plain_nonhybrid_client_passes_through_without_broker_or_file_access(tmp_path):
    broker = Mock()
    assert view(broker, {"KMW2": {}}, tmp_path / "absent.sqlite3") is broker
    broker.assert_not_called()
