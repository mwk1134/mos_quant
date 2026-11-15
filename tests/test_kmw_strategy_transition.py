"""Pure mixed-to-original handoffs from synthetic, reconciled ledgers."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import atr70_r2 as mixed
from kmw_strategy_transition import TransitionBlocked, build_dongpa_handoff


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 13, 17, tzinfo=ET)
BASIS = "2026-11-13"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The handoff cannot access a broker or network")
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def order(side, day, *, strategy="R2"):
    result = {"intent_id": f"{strategy}-{side}-{day}", "strategy": strategy, "ticker": "SOXL",
              "side": side, "quantity": 5, "price": "100" if side == "BUY" else "110",
              "order_type": "LOC", "session_date": day, "lot_id": "mixed-lot",
              "position_key": "mixed-lot", "round": 1,
              "event": "R2_ENTRY" if side == "BUY" else "R2_EXIT"}
    if side == "BUY":
        result["frozen_rules"] = mixed.r2_rules("SF")
    return result


def fill(state, intent, notional, fee):
    state = mixed.register_order(state, intent)
    return mixed.reconcile_fills(state, [{"order_id": intent["intent_id"],
        "broker_order_id": "11" if intent["side"] == "BUY" else "12",
        "cumulative_quantity": intent["quantity"], "cumulative_notional": notional,
        "cumulative_fee": fee, "status": "FILLED"}])


def flat_state():
    state = mixed.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=0,
                                open_order_count=0, broker_confirmed=True, fee_rate="0.0005")
    state["unallocated_cost_cash"] = "0.54"
    state = fill(state, order("BUY", "2026-11-12"), "500", "0.25")
    return fill(state, order("SELL", "2026-11-13"), "550", "0.275")


def document(state=None):
    state = flat_state() if state is None else state
    return {"KMW2": {"atr70_r2": deepcopy(state), "atr70_r2_mix_enabled": False,
                     "atr70_r2_status": {"state": "WAITING"}, "mixed_updated_at": "older",
                     "available_cash": 12345.0, "compound_seed": 80000.0,
                     "compound_reference_seed": 79000.0, "compound_profit_rate": .7,
                     "compound_loss_rate": .2, "compound_trading_days": 22,
                     "compound_settlements": [{"trade_date": "2026-11-06", "settlement_date": "2026-11-13", "pnl": 500}],
                     "processed_seed_dates": ["2026-11-10"], "as_of_date": BASIS,
                     "cash_snapshot_date": "2026-11-11", "pending_buy": {"round": 7},
                     "manual_buy_confirmations": [{"trade_date": "2026-11-10"}],
                     "broker_sync": {"version": 1, "baseline_date": "2026-10-12", "executions": {"old": {}}},
                     "maxMdd": {"percent": 30}, "custom_note": "preserve"},
            "KMW": {"available_cash": 111}, "JEH": {"available_cash": 222},
            "_preset_configs": {"KMW2": {"initial_capital": 67612.0, "session_start_date": "2026-04-29",
                "atr70_r2_mix_enabled": False, "ma_alignment_switch_enabled": False,
                "position_edits": {}, "seed_increases": [{"date": "2026-11-10", "amount": 1000},
                    {"date": "2026-12-01", "amount": 2000}]}, "KMW": {"initial_capital": 111}}}


def handoff(source, *, now=NOW, basis=BASIS, broker_cash=None, expected_state=None):
    state = source["KMW2"]["atr70_r2"]
    if broker_cash is None:
        broker_cash = Decimal(state["atr"]["cash"]) + Decimal(state["r2"]["cash"]) + Decimal("0.54")
    return build_dongpa_handoff(source, expected_state=state if expected_state is None else expected_state,
                               basis_date=basis, now=now, broker_cash=broker_cash)


def test_flat_handoff_uses_current_cash_and_keeps_surplus_outside_new_budget():
    source = document()
    before = deepcopy(source)
    result = handoff(source)
    actual = result["KMW2"]
    assert actual["available_cash"] == actual["compound_seed"] == actual["compound_reference_seed"] == 74055.475
    assert actual["compound_settlements"] == [] and actual["compound_trading_days"] == 0
    assert actual["compound_profit_rate"] == .7 and actual["compound_loss_rate"] == .2
    assert actual["as_of_date"] == actual["cash_snapshot_date"] == BASIS
    assert actual["broker_sync"] == {"version": 1, "baseline_date": BASIS, "executions": {}}
    assert actual["processed_seed_dates"] == ["2026-11-10"]
    assert actual["strategyTransition"]["mode"] == "DONGPA"
    assert Decimal(actual["strategyTransition"]["transferred_cash"]) == Decimal("74055.475")
    assert actual["strategyTransition"]["outside_budget_cash"] == "0.540"
    assert all(key not in actual for key in ("atr70_r2", "atr70_r2_status", "pending_buy", "manual_buy_confirmations", "mixed_updated_at"))
    assert actual["maxMdd"] == source["KMW2"]["maxMdd"] and actual["custom_note"] == "preserve"
    assert result["_preset_configs"] == source["_preset_configs"]
    assert {key: value for key, value in result.items() if key != "KMW2"} == {
        key: value for key, value in source.items() if key != "KMW2"}
    assert source == before
    archive = actual["strategyHistory"][0]
    assert archive["mixed_state"] == source["KMW2"]["atr70_r2"]
    assert archive["snapshot_before"]["compound_settlements"] == source["KMW2"]["compound_settlements"]
    assert archive["snapshot_before"]["broker_sync"] == source["KMW2"]["broker_sync"]
    assert archive["snapshot_before"]["manual_buy_confirmations"] == source["KMW2"]["manual_buy_confirmations"]
    assert archive["config_before"] == source["_preset_configs"]["KMW2"]


def test_archive_preserves_prior_history_without_nesting_it_and_id_is_deterministic():
    source = document()
    source["KMW2"]["strategyHistory"] = [{"id": "previous-strategy"}]
    first = handoff(source)
    later = handoff(source, now=NOW.replace(hour=18))
    history = first["KMW2"]["strategyHistory"]
    assert history[0] == source["KMW2"]["strategyHistory"][0] and len(history) == 2
    assert "strategyHistory" not in history[-1]["snapshot_before"]
    assert first["KMW2"]["strategyTransition"]["id"] == later["KMW2"]["strategyTransition"]["id"]
    no_prior_history = deepcopy(source)
    del no_prior_history["KMW2"]["strategyHistory"]
    assert handoff(no_prior_history)["KMW2"]["strategyTransition"]["id"] != first["KMW2"]["strategyTransition"]["id"]
    with pytest.raises(TransitionBlocked) as blocked:
        build_dongpa_handoff(first, expected_state=source["KMW2"]["atr70_r2"], basis_date=BASIS, now=NOW, broker_cash="80000")
    assert blocked.value.code == "STATE_CHANGED"


def test_next_legacy_refresh_cannot_replay_archived_fills_or_old_profit_settlements():
    from preset_snapshot_refresh import refresh_preset_snapshot

    source = document()
    result = handoff(source)
    original_cash = result["KMW2"]["available_cash"]
    fills = [{"order_id": "11", "trade_date": "2026-11-12", "side": "BUY", "quantity": 5,
              "price": "100", "notional": "500", "fees": "0.25"},
             {"order_id": "12", "trade_date": "2026-11-13", "side": "SELL", "quantity": 5,
              "price": "110", "notional": "550", "fees": "0.275"}]
    refreshed = refresh_preset_snapshot(result, preset="KMW2", basis_date="2026-11-16", fills=fills,
                                        broker_quantity=0)
    assert refreshed["KMW2"]["available_cash"] == original_cash
    assert refreshed["KMW2"]["compound_seed"] == refreshed["KMW2"]["compound_reference_seed"] == original_cash
    assert refreshed["KMW2"]["compound_settlements"] == []
    assert refreshed["KMW2"]["compound_trading_days"] == 1
    assert refreshed["KMW2"]["broker_sync"]["executions"] == {}
    assert refreshed["KMW2"]["strategyHistory"] == result["KMW2"]["strategyHistory"]


@pytest.mark.parametrize("status", ["PLANNED", "ACCEPTED", "OPEN", "SENDING", "UNKNOWN"])
def test_pending_or_unknown_order_cannot_be_archived(status):
    state = mixed.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=0,
                                open_order_count=0, broker_confirmed=True)
    intent = order("BUY", "2026-11-12")
    state = mixed.register_order(state, intent)
    state = mixed.mark_order(state, intent["intent_id"], status=status, broker_order_id="101")
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(document(state))
    assert blocked.value.code == "UNRESOLVED_ORDER"


def test_owned_shares_and_pending_rebalance_block_handoff():
    state = mixed.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=0,
                                open_order_count=0, broker_confirmed=True)
    state = fill(state, order("BUY", "2026-11-12"), "500", "0.25")
    with pytest.raises(TransitionBlocked) as held:
        handoff(document(state))
    assert held.value.code == "MIXED_HOLDINGS"
    source = document()
    source["KMW2"]["atr70_r2"]["pending_rebalance"] = {"month": "2026-11"}
    with pytest.raises(TransitionBlocked) as pending:
        handoff(source)
    assert pending.value.code == "PENDING_REBALANCE"


def test_current_session_fills_wait_until_their_session_is_complete():
    source = document()
    source["KMW2"]["as_of_date"] = "2026-11-12"
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(source, basis="2026-11-12", now=NOW.replace(hour=10))
    assert blocked.value.code == "INCOMPLETE_ORDER_SESSION"


@pytest.mark.parametrize("status", ["CANCELLED", "REJECTED"])
def test_terminal_unfilled_orders_can_be_archived(status):
    state = mixed.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=0,
                                open_order_count=0, broker_confirmed=True)
    intent = order("BUY", "2026-11-12")
    state = mixed.register_order(state, intent)
    state = mixed.mark_order(state, intent["intent_id"], status=status)
    assert handoff(document(state))["KMW2"]["available_cash"] == 74006.0


def test_legacy_holdings_or_unprocessed_seed_block_active_handoff():
    source = document()
    source["KMW2"]["1_2026-11-10"] = {"shares": 1, "buy_price": 100, "round": 1}
    with pytest.raises(TransitionBlocked) as held:
        handoff(source)
    assert held.value.code == "LEGACY_HOLDINGS"
    source = document()
    source["KMW2"]["processed_seed_dates"] = []
    with pytest.raises(TransitionBlocked) as seed:
        handoff(source)
    assert seed.value.code == "UNPROCESSED_SEED"


def test_waiting_cancellation_preserves_legacy_positions_cash_and_compounding():
    waiting = mixed.initial_state(session_date="2026-11-12", broker_cash="74006", broker_shares=2,
                                  open_order_count=0, broker_confirmed=False)
    source = document(waiting)
    source["KMW2"]["1_2026-11-10"] = {"shares": 2, "buy_price": 100, "round": 1}
    source["KMW2"]["processed_seed_dates"] = []
    result = build_dongpa_handoff(source, expected_state=waiting, basis_date=BASIS, now=NOW, broker_cash=None)
    expected = deepcopy(source["KMW2"])
    for key in ("atr70_r2", "atr70_r2_status", "mixed_updated_at"):
        expected.pop(key)
    assert {key: value for key, value in result["KMW2"].items()
            if key not in ("strategyHistory", "strategyTransition")} == expected
    assert result["KMW2"]["strategyTransition"]["cancelled_before_activation"] is True
    assert result["KMW2"]["strategyTransition"]["transferred_cash"] == "0"


@pytest.mark.parametrize("value,code", [("74055.47", "INSUFFICIENT_SETTLED_CASH"),
    (None, "INVALID_CASH"), (True, "INVALID_CASH"), ("NaN", "INVALID_CASH"), ("-1", "INVALID_CASH")])
def test_broker_cash_must_cover_only_the_strategy_owned_amount(value, code):
    source = document()
    with pytest.raises(TransitionBlocked) as blocked:
        build_dongpa_handoff(source, expected_state=source["KMW2"]["atr70_r2"], basis_date=BASIS, now=NOW, broker_cash=value)
    assert blocked.value.code == code


def test_source_state_or_switch_change_blocks_before_handoff():
    source = document()
    expected = deepcopy(source["KMW2"]["atr70_r2"])
    source["KMW2"]["atr70_r2"]["revision"] = 2
    with pytest.raises(TransitionBlocked) as changed:
        handoff(source, expected_state=expected)
    assert changed.value.code == "STATE_CHANGED"
    source = document()
    source["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] = True
    with pytest.raises(TransitionBlocked) as enabled:
        handoff(source)
    assert enabled.value.code == "SWITCH_ENABLED"


@pytest.mark.parametrize("basis,now,code", [
    ("2026-11-3", NOW, "INVALID_BASIS_DATE"),
    ("2026-11-14", NOW.replace(day=14), "INVALID_BASIS_DATE"),
    (BASIS, NOW.replace(tzinfo=None), "INVALID_TIME"),
    (BASIS, NOW.replace(hour=15), "BASIS_NOT_COMPLETED"),
])
def test_handoff_requires_canonical_completed_session_and_aware_time(basis, now, code):
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(document(), basis=basis, now=now)
    assert blocked.value.code == code


def test_early_close_and_older_history_keep_the_actual_completion_boundary():
    now = datetime(2026, 11, 27, 13, tzinfo=ET)
    result = handoff(document(), basis="2026-11-27", now=now)
    assert result["KMW2"]["as_of_date"] == "2026-11-27"
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(document(), basis="2026-11-12")
    assert blocked.value.code == "INVALID_BASIS_DATE"


def test_empty_owned_budget_or_invalid_history_blocks_without_input_changes():
    empty = mixed.initial_state(session_date="2026-11-12", broker_cash="0", broker_shares=0,
                                open_order_count=0, broker_confirmed=True)
    source = document(empty)
    before = deepcopy(source)
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(source)
    assert blocked.value.code == "EMPTY_STRATEGY_CASH" and source == before
    source = document()
    source["KMW2"]["strategyHistory"] = {}
    with pytest.raises(TransitionBlocked) as invalid:
        handoff(source)
    assert invalid.value.code == "INVALID_HISTORY"


@pytest.mark.parametrize("cash", ["1e999", "1e-999"])
def test_unrepresentable_legacy_capital_fails_before_returning_a_document(cash):
    state = mixed.initial_state(session_date="2026-11-12", broker_cash=cash, broker_shares=0,
                                open_order_count=0, broker_confirmed=True)
    source = document(state)
    with pytest.raises(TransitionBlocked) as blocked:
        handoff(source, broker_cash=cash)
    assert blocked.value.code == "INVALID_CASH"
