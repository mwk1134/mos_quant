"""Offline integration at the legacy/mixed boundary and atomic state store."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

import atr70_r2 as core
import atr70_r2_adapter as adapter
from kmw_order_plan import _KMWPlanTrader, build_kmw_plan
from preset_snapshot_refresh import SnapshotRefreshBlocked, refresh_preset_snapshot
from tests.test_kmw_order_plan import NOW, recommendation, two_preset_snapshots
from us_market_calendar import is_us_equity_trading_day


def rows(basis="2026-10-09"):
    output = []
    day = date(2024, 1, 2)
    while day.isoformat() <= basis:
        if is_us_equity_trading_day(day):
            price = Decimal(50) + len(output) * Decimal("0.01")
            output.append({"date": day.isoformat(), "open": str(price), "high": str(price + 1),
                           "low": str(price - 1), "close": str(price)})
        day += timedelta(days=1)
    return output


def active_state():
    return core.initial_state(session_date="2026-10-09", broker_cash="10000", broker_shares=0,
                              open_order_count=0, broker_confirmed=True, fee_rate="0.005")


def document(state=None):
    result = two_preset_snapshots()
    result["_preset_configs"]["KMW2"] = {core.MIXED_SWITCH_KEY: True}
    if state is not None:
        result["KMW2"].pop("1_2026-10-06")
        result["KMW2"][core.STATE_KEY] = deepcopy(state)
        result["KMW2"]["broker_sync"] = {"baseline_date": "2026-10-09", "executions": {}}
    return result


class FakeGitHub:
    def __init__(self, value):
        self.document = deepcopy(value)
        self.writes = []

    def read(self):
        return deepcopy(self.document), "a" * 40

    def save(self, value, sha, preset):
        self.writes.append((deepcopy(value), sha, preset))
        self.document = deepcopy(value)
        return "saved"


def store(value, loader=None):
    github = FakeGitHub(value)
    return adapter.MixedSnapshotStore({}, github=github, market_loader=loader or (lambda now, basis: (rows(basis), rows(basis)))), github


def test_enabling_mix_drains_only_existing_holdings_and_preserves_sell_terms():
    source = two_preset_snapshots()
    with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
        old = build_kmw_plan(source, NOW, preset="KMW2")
        source["_preset_configs"]["KMW2"] = {core.MIXED_SWITCH_KEY: True}
        original = deepcopy(source)
        new = build_kmw_plan(source, NOW, preset="KMW2")
    assert new["mixed_phase"] == "WAITING_FLAT"
    assert new["buy_budget"] == 0
    assert new["orders"] == [order for order in old["orders"] if order["side"] == "SELL"]
    assert new["snapshot_positions"] == old["snapshot_positions"]
    assert source == original


def test_mixed_flag_never_changes_another_account():
    source = document()
    with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
        plan = build_kmw_plan(source, NOW, preset="KMW")
    assert any(order["side"] == "BUY" for order in plan["orders"])
    assert not plan.get("atr70_r2_managed")


def test_active_preview_cannot_fall_back_to_legacy_recommendation():
    source = document(active_state())
    with patch.object(_KMWPlanTrader, "get_daily_recommendation", side_effect=AssertionError("legacy path")), \
            patch.object(adapter, "load_market_rows", return_value=(rows(), rows())):
        plan = build_kmw_plan(source, NOW, preset="KMW2")
    assert plan["atr70_r2_managed"] is True
    assert plan["strategy_name"] == core.STRATEGY_ID
    assert plan["mode"] == "MIXED"
    assert all(order["strategy"] == "R2" for order in plan["orders"])


@pytest.mark.parametrize("bad", ["true", 1, None, []])
def test_malformed_switch_is_rejected(bad):
    source = document()
    source["_preset_configs"]["KMW2"][core.MIXED_SWITCH_KEY] = bad
    with pytest.raises(ValueError):
        adapter.mixed_requested(source)


def test_market_history_requires_every_completed_session_and_exact_basis():
    valid = rows()
    assert adapter._validated_rows(valid, "2026-10-09") == valid
    for changed in (valid[:-1], valid[:30] + valid[31:], valid + [valid[-1]]):
        with pytest.raises(ValueError):
            adapter._validated_rows(changed, "2026-10-09")


def test_r2_rules_use_completed_data_and_frozen_entry_exit():
    state = core.advance_r2_session(active_state(), "2026-10-12")
    orders = adapter.build_r2_orders(state, session_date="2026-10-12", daily_rows=rows(), qqq_rows=rows())
    buy = next(order for order in orders if order["side"] == "BUY")
    assert buy["frozen_rules"] == core.r2_rules("SF", True)
    intent = core.build_runtime_plan(state, session_date="2026-10-12", previous_session="2026-10-09",
                                     next_session="2026-10-13", r2_orders=orders)["orders"][0]
    state = core.register_order(state, intent, "71")
    state = core.reconcile_fills(state, [{"order_id": intent["intent_id"], "broker_order_id": "71",
        "cumulative_quantity": intent["quantity"], "cumulative_notional": str(Decimal("50") * intent["quantity"]),
        "cumulative_fee": "0", "status": "FILLED"}])
    exits = adapter.build_r2_orders(state, session_date="2026-10-13", daily_rows=[], qqq_rows=[], enabled=False)
    assert len(exits) == 1
    assert exits[0]["price"] == "50.75"
    assert exits[0]["order_type"] == "LOC"


def test_store_market_failure_preserves_exit_context_and_does_not_write():
    value = document(active_state())
    saved, github = store(value, loader=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    context = saved.read(NOW)
    assert context["market_error"]
    assert context["state"] == value["KMW2"][core.STATE_KEY]
    assert context["daily_rows"] == []
    assert github.writes == []


def test_state_cas_preserves_other_accounts_and_latest_configuration():
    saved, github = store(document(active_state()))
    context = saved.read(NOW)
    github.document["KMW"]["extra_new_metadata"] = "keep"
    github.document["_preset_configs"]["KMW2"]["ma_alignment_switch_enabled"] = True
    updated = core.advance_r2_session(context["state"], "2026-10-12")
    saved.save(updated, NOW)
    assert github.document["KMW"]["extra_new_metadata"] == "keep"
    assert github.document["_preset_configs"]["KMW2"]["ma_alignment_switch_enabled"] is True
    assert github.document["KMW2"][core.STATE_KEY] == updated
    assert github.document["KMW2"]["available_cash"] == 10000
    writes = len(github.writes)
    saved.save(updated, NOW)
    assert len(github.writes) == writes


@pytest.mark.parametrize("change", ["state", "switch"])
def test_state_cas_stops_before_overwriting_a_concurrent_runtime_or_switch(change):
    saved, github = store(document(active_state()))
    context = saved.read(NOW)
    if change == "state":
        github.document["KMW2"][core.STATE_KEY] = core.advance_r2_session(context["state"], "2026-10-12")
    else:
        github.document["_preset_configs"]["KMW2"][core.MIXED_SWITCH_KEY] = False
    updated = core.advance_r2_session(context["state"], "2026-10-12")
    with pytest.raises(ValueError, match="changed"):
        saved.save(updated, NOW)
    assert github.writes == []


def test_legacy_refresher_cannot_reinterpret_mixed_positions_or_cash():
    source = document(active_state())
    with pytest.raises(SnapshotRefreshBlocked) as failure:
        refresh_preset_snapshot(source, preset="KMW2", basis_date="2026-10-09", fills=[], broker_quantity=0)
    assert failure.value.code == "MIXED_MANAGED"


def test_preview_has_independent_atr_and_r2_tables_without_inventing_live_atr_quantity():
    source = document(active_state())
    with patch.object(adapter, "load_market_rows", return_value=(rows(), rows())):
        plan = build_kmw_plan(source, NOW, preset="KMW2")
    tables = plan["strategy_order_tables"]
    assert set(tables) == {"ATR", "R2"}
    atr_buy = next(order for order in tables["ATR"]["orders"] if order["side"] == "BUY")
    r2_buy = next(order for order in tables["R2"]["orders"] if order["side"] == "BUY")
    assert atr_buy["quantity"] is None and atr_buy["reference_price"] is None
    assert "1.015" in atr_buy["condition"]
    assert r2_buy["quantity"] > 0 and Decimal(r2_buy["reference_price"]) > 0
    assert atr_buy["display_only"] is r2_buy["display_only"] is True
    assert all(order["strategy"] == "R2" for order in plan["orders"])


def test_switch_off_hides_new_buy_candidates_but_retains_already_accepted_buy():
    state = active_state()
    raw = {"ticker": "SOXL", "side": "BUY", "quantity": 1, "price": "55", "order_type": "LOC",
           "round": 1, "lot_id": "R2:2026-10-12", "frozen_rules": core.r2_rules("SF", True)}
    intent = core.build_runtime_plan(state, session_date="2026-10-12", previous_session="2026-10-09",
                                     next_session="2026-10-13", r2_orders=[raw])["orders"][0]
    state = core.register_order(state, intent, "812")
    source = document(state)
    source["_preset_configs"]["KMW2"][core.MIXED_SWITCH_KEY] = False
    with patch.object(adapter, "load_market_rows", return_value=(rows(), rows())):
        plan = build_kmw_plan(source, NOW, preset="KMW2")
    assert not any(order["side"] == "BUY" for order in plan["orders"])
    display = plan["strategy_order_tables"]
    assert not any(order["side"] == "BUY" for order in display["ATR"]["orders"])
    accepted = next(order for order in display["R2"]["orders"] if order["side"] == "BUY")
    assert accepted["broker_order_id"] == "812"
    assert accepted["existing_order"] is True
    assert "기접수 주문" in display["R2"]["message"]


def test_unknown_order_never_appears_as_a_second_new_buy_recommendation():
    state = active_state()
    raw = {"side": "BUY", "quantity": 1, "price": "55", "order_type": "LOC", "round": 1,
           "lot_id": "R2:2026-10-12", "frozen_rules": core.r2_rules("SF", True)}
    intent = core.build_runtime_plan(state, session_date="2026-10-12", previous_session="2026-10-09",
                                     next_session="2026-10-13", r2_orders=[raw])["orders"][0]
    state = core.mark_order(core.register_order(state, intent), intent["intent_id"], status="UNKNOWN")
    tables = adapter.strategy_order_tables(state, session_date="2026-10-12", enabled=True,
        runtime_plan={"phase": "UNKNOWN", "orders": []}, r2_orders=[raw], daily_rows=rows())
    assert tables["R2"]["orders"][0]["status"] == "접수 여부 확인 필요"
    assert len(tables["R2"]["orders"]) == 1
    assert not any(order["side"] == "BUY" for order in tables["ATR"]["orders"])


def test_filter_miss_keeps_r2_order_table_independent():
    state = active_state()
    raw = adapter.build_r2_orders(state, session_date="2026-10-12", daily_rows=rows(), qqq_rows=rows())
    with patch.object(adapter, "atr70_signal", return_value={"ready": True, "passed": False, "basis_date": "2026-10-09"}):
        tables = adapter.strategy_order_tables(state, session_date="2026-10-12", enabled=True,
            runtime_plan={"phase": "READY", "orders": []}, r2_orders=raw, daily_rows=rows())
    assert tables["ATR"]["status"] == "FILTER_BLOCKED"
    assert not tables["ATR"]["orders"]
    assert any(order["side"] == "BUY" for order in tables["R2"]["orders"])


def test_previous_session_unresolved_buy_remains_visible_until_reconciled():
    state = active_state()
    raw = {"side": "BUY", "quantity": 1, "price": "55", "order_type": "LOC", "round": 1,
           "lot_id": "R2:2026-10-09", "frozen_rules": core.r2_rules("SF", True)}
    intent = core.build_runtime_plan(state, session_date="2026-10-09", previous_session="2026-10-08",
                                     next_session="2026-10-12", r2_orders=[raw])["orders"][0]
    state = core.register_order(state, intent, "991")
    tables = adapter.strategy_order_tables(state, session_date="2026-10-12", enabled=True,
        runtime_plan={"phase": "READY", "orders": []}, r2_orders=[raw], daily_rows=rows())
    assert len(tables["R2"]["orders"]) == 1
    assert tables["R2"]["orders"][0]["broker_order_id"] == "991"
    assert tables["R2"]["orders"][0]["existing_order"] is True


def test_atr_exit_preview_after_moo_cutoff_uses_market_and_never_recommends_duplicate_entry():
    state = active_state()
    intent = next(order for order in core.build_runtime_plan(state, session_date="2026-10-09",
        previous_session="2026-10-08", next_session="2026-10-12", regular_open="50", last_price="51",
        daily_rows=rows("2026-10-08"))["orders"] if order["strategy"] == "ATR")
    state = core.register_order(state, intent, "992")
    state = core.reconcile_fills(state, [{"order_id": intent["intent_id"], "broker_order_id": "992",
        "cumulative_quantity": intent["quantity"], "cumulative_notional": str(Decimal("51") * intent["quantity"]),
        "cumulative_fee": "0", "status": "FILLED"}])
    source = document(state)
    with patch.object(adapter, "load_market_rows", return_value=(rows(), rows())):
        plan = build_kmw_plan(source, NOW.replace(hour=23, minute=0), preset="KMW2")
    atr_rows = plan["strategy_order_tables"]["ATR"]["orders"]
    assert any(order["side"] == "SELL" and order["order_type"] == "MARKET" for order in atr_rows)
    assert not any(order["side"] == "BUY" for order in atr_rows)
