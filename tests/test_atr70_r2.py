from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

import atr70_r2 as core


def active(cash="10000", day="2026-10-01", fee="0.0005"):
    return core.initial_state(session_date=day, broker_cash=cash, broker_shares=0,
                              open_order_count=0, broker_confirmed=True, fee_rate=fee)


def intent(strategy="R2", side="BUY", quantity=5, price="12", day="2026-10-02", lot="r2-lot", **extra):
    order = {"intent_id": f"{lot}:{side}:{day}", "strategy": strategy, "ticker": "SOXL",
             "side": side, "quantity": quantity, "price": price, "order_type": "LOC",
             "session_date": day, "lot_id": lot, "position_key": lot, "round": 1,
             "event": "R2_ENTRY" if side == "BUY" else "R2_EXIT"}
    if side == "BUY":
        order["frozen_rules"] = core.r2_rules("SF") if strategy == "R2" else deepcopy(core.ATR_RULES)
    if strategy == "ATR":
        order["order_type"] = "MARKET" if side == "BUY" else "MOO"
        if side == "BUY":
            order["exit_session"] = "2026-10-05"
    return {**order, **extra}


def fill(order, quantity, notional, fee="0", *, status=None, broker=None):
    return {"order_id": order["intent_id"], "broker_order_id": broker or order["intent_id"],
            "cumulative_quantity": quantity, "cumulative_notional": str(notional), "cumulative_fee": str(fee),
            "status": status or ("FILLED" if quantity == order["quantity"] else "OPEN")}


def filled(state, order, notional, fee="0"):
    state = core.register_order(state, order)
    return core.reconcile_fills(state, [fill(order, order["quantity"], notional, fee)])


def history(count=323, start="2025-07-01", close="100"):
    start_date = date.fromisoformat(start)
    return [{"date": (start_date + timedelta(days=index)).isoformat(), "open": close,
             "high": str(Decimal(close) + 1), "low": str(Decimal(close) - 1), "close": close}
            for index in range(count)]


def plan(state, **kwargs):
    return core.build_runtime_plan(state, session_date="2026-10-02", previous_session="2026-10-01",
                                   next_session="2026-10-05", **kwargs)


@pytest.mark.parametrize("shares,orders,confirmed", [(1, 0, True), (0, 1, True), (0, 0, False)])
def test_legacy_assets_orders_and_unconfirmed_snapshot_wait_for_flat(shares, orders, confirmed):
    state = core.initial_state(session_date="2026-10-01", broker_cash="10000", broker_shares=shares,
                              open_order_count=orders, broker_confirmed=confirmed)
    assert state["phase"] == "WAITING_FLAT"
    assert plan(state)["orders"] == []
    with pytest.raises(ValueError, match="WAITING_FLAT"):
        core.register_order(state, intent())
    state = core.activate_if_flat(state, session_date="2026-10-02", broker_cash="12345.67",
                                  broker_shares=0, open_order_count=0, broker_confirmed=True)
    assert state["phase"] == "ACTIVE"
    assert Decimal(state["atr"]["cash"]) == Decimal("6172.835")
    assert state["atr"]["cash"] == state["r2"]["cash"]


def test_activation_is_account_scoped_and_cannot_reset_an_active_ledger():
    with pytest.raises(ValueError, match="KMW2"):
        core.initial_state("KMW", session_date="2026-10-01", broker_cash=1, broker_shares=0,
                           open_order_count=0, broker_confirmed=True)
    state = active()
    assert core.activate_if_flat(state, session_date="2026-10-02", broker_cash=999999,
                                 broker_shares=0, open_order_count=0, broker_confirmed=True) == state


def test_atr_warmup_and_prior_day_percentile_exclude_tested_observation():
    rows = history()
    session = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
    result = core.atr70_signal(rows, session_date=session)
    assert result["ready"] and result["passed"]
    assert Decimal(result["cutoff_atr_pct"]) == 2
    assert not core.atr70_signal(rows[:-1], session_date=session)["ready"]
    rows[-1]["high"] = "150"
    shocked = core.atr70_signal(rows, session_date=session)
    assert not shocked["passed"]
    assert shocked["cutoff_atr_pct"] == result["cutoff_atr_pct"]
    rows.append({"date": session, "open": 100, "high": 5000, "low": 1, "close": 1})
    assert core.atr70_signal(rows, session_date=session) == shocked


def test_atr_rejects_duplicate_or_nonfinite_history():
    rows = history()
    rows.append(rows[-1])
    with pytest.raises(ValueError, match="chronological"):
        core.atr70_signal(rows, session_date="2027-01-01")
    rows = history()
    rows[-1]["close"] = "NaN"
    with pytest.raises(ValueError):
        core.atr70_signal(rows, session_date="2027-01-01")


def test_breakout_uses_actual_open_and_needs_crossing_and_fresh_history():
    rows = history(323, start=(date(2026, 10, 1) - timedelta(days=322)).isoformat())
    state = active()
    assert not plan(state, regular_open=100, last_price="101.49", daily_rows=rows)["orders"]
    result = plan(state, regular_open=100, last_price="101.50", daily_rows=rows)
    order = result["orders"][0]
    assert order["strategy"] == "ATR" and order["order_type"] == "MARKET"
    assert order["exit_session"] == "2026-10-05"
    assert Decimal(order["signal_price"]) == Decimal("101.50")
    assert Decimal(order["price"]) == Decimal("111.650")
    assert order["quantity"] == 44
    assert not plan(state, regular_open=100, last_price=102, daily_rows=rows[:-1])["orders"]


def test_open_r2_loc_does_not_block_atr_breakout_but_unknown_does():
    rows = history(323, start=(date(2026, 10, 1) - timedelta(days=322)).isoformat())
    order = intent()
    state = core.register_order(active(), order, broker_order_id="r2-broker")
    result = plan(state, regular_open=100, last_price=102, daily_rows=rows)
    assert [item["strategy"] for item in result["orders"]] == ["ATR"]
    state = core.mark_order(state, order["intent_id"], status="UNKNOWN")
    assert plan(state, regular_open=100, last_price=102, daily_rows=rows)["phase"] == "UNKNOWN"


def test_partial_fill_cumulative_notional_and_fee_are_exact_and_idempotent():
    order = intent()
    state = core.register_order(active("200"), order)
    first = fill(order, 2, "20", ".1")
    state = core.reconcile_fills(state, [first])
    assert core.reconcile_fills(state, [first]) == state
    final = fill(order, 5, "56", ".28")
    state = core.reconcile_fills(state, [final])
    assert core.reconcile_fills(state, [final]) == state
    lot = state["r2"]["lots"][0]
    assert lot["shares"] == 5 and Decimal(lot["entry_price"]) == Decimal("11.2")
    assert Decimal(state["r2"]["cash"]) == Decimal("43.72")
    assert Decimal(state["atr"]["cash"]) == 100
    with pytest.raises(ValueError, match="regressed"):
        core.reconcile_fills(state, [first])
    sale = intent(side="SELL", quantity=2, price=15)
    state = core.register_order(state, sale)
    sale_fill = fill(sale, 2, 30, ".15")
    state = core.reconcile_fills(state, [sale_fill])
    assert core.reconcile_fills(state, [sale_fill]) == state
    assert state["r2"]["lots"][0]["shares"] == 3
    assert Decimal(state["r2"]["lots"][0]["remaining_cost"]) == Decimal("33.768")
    assert Decimal(state["r2"]["settlements"][0]["profit_loss"]) == Decimal("7.338")


def test_registered_orders_reserve_strategy_cash_and_lot_shares():
    state = active("200")
    state = core.register_order(state, intent(quantity=5, price=12))
    with pytest.raises(ValueError, match="fee reserve"):
        core.register_order(state, intent(quantity=5, price=12, lot="another"))
    assert core.available_cash(state, "ATR") == 100
    state = filled(active(), intent(quantity=5, price=12), 50)
    state = core.register_order(state, intent(side="SELL", quantity=4))
    with pytest.raises(ValueError, match="unreserved"):
        core.register_order(state, intent(side="SELL", quantity=2, day="2026-10-05"))


def test_partial_atr_entry_exits_confirmed_shares_independent_of_filter():
    buy = intent(strategy="ATR", quantity=5, price=10, lot="atr-lot")
    state = core.register_order(active(), buy)
    state = core.reconcile_fills(state, [fill(buy, 2, 20, status="CANCELLED")])
    result = core.build_runtime_plan(state, session_date="2026-10-05", previous_session="2026-10-02",
                                     next_session="2026-10-06", daily_rows=[], regular_open=1, last_price=1)
    assert result["phase"] == "ATR_EXIT"
    assert result["orders"][0]["quantity"] == 2
    assert result["orders"][0]["order_type"] == "MOO"
    overdue = core.build_runtime_plan(state, session_date="2026-10-05", previous_session="2026-10-02",
                                      next_session="2026-10-06", atr_exit_order_type="MARKET")
    assert overdue["orders"][0]["order_type"] == "MARKET"
    assert overdue["orders"][0]["event"] == "ATR_OVERDUE_EXIT"
    assert overdue["orders"][0]["intent_id"] != result["orders"][0]["intent_id"]


def test_duplicate_intents_and_broker_identity_changes_are_rejected():
    state = core.register_order(active(), intent(), broker_order_id="broker-1")
    assert core.register_order(state, intent(), broker_order_id="broker-1") == state
    with pytest.raises(ValueError, match="changed terms"):
        core.register_order(state, intent(quantity=6))
    with pytest.raises(ValueError, match="identity mismatch"):
        core.mark_order(state, intent()["intent_id"], broker_order_id="different")


def test_atr_same_session_rejected_attempt_is_not_retried():
    rows = history(323, start=(date(2026, 10, 1) - timedelta(days=322)).isoformat())
    state = active()
    order = plan(state, regular_open=100, last_price=102, daily_rows=rows)["orders"][0]
    state = core.register_order(state, order)
    state = core.mark_order(state, order["intent_id"], status="REJECTED")
    assert not plan(state, regular_open=100, last_price=102, daily_rows=rows)["orders"]


def test_r2_frozen_rules_and_preset_are_exact():
    sf, ag = core.r2_rules("SF"), core.r2_rules("AG")
    assert (sf["buy_multiplier"], sf["sell_multiplier"], sf["max_holding_days"]) == ("1.0625", "1.015", 35)
    assert ag["round_weights"] == ["0.2", "0.2", "0.3", "0.3"]
    assert core.r2_rules("AG", False)["buy_multiplier"] == "1.036"
    order = intent()
    state = filled(active(), order, 50)
    before = deepcopy(state["r2"]["lots"][0]["frozen_rules"])
    state["r2"]["mode"] = "AG"
    assert state["r2"]["lots"][0]["frozen_rules"] == before
    broken = deepcopy(state)
    broken["r2"]["lots"][0]["frozen_rules"]["sell_multiplier"] = "1.8"
    with pytest.raises(ValueError, match="Frozen"):
        core.validate_state(broken)


def test_r2_session_catchup_uses_us_calendar_and_ten_day_seed_cycle():
    state = active(day="2026-10-01")
    state["r2"]["settlements"] = [
        {"settlement_date": "2026-10-02", "profit_loss": "100", "frozen_rules": core.r2_rules("SF")},
        {"settlement_date": "2026-10-05", "profit_loss": "-100", "frozen_rules": core.r2_rules("AG")},
    ]
    state = core.advance_r2_session(state, "2026-10-05")
    assert state["r2"]["trading_days"] == 3
    assert Decimal(state["r2"]["seed"]) == Decimal("5050.700")
    assert Decimal(state["r2"]["reference_seed"]) == 5000
    state = core.advance_r2_session(state, "2026-10-14")
    assert state["r2"]["trading_days"] == 10
    assert Decimal(state["r2"]["reference_seed"]) == Decimal("5050.700")
    assert core.advance_r2_session(state, "2026-10-14") == state


def rebalance_state():
    state = active(fee="0")
    state = filled(state, intent(quantity=60, price=40, lot="sixty"), 2400)
    return filled(state, intent(quantity=40, price=40, lot="forty"), 1600)


def test_monthly_minimum_integer_sales_pro_rata_and_actual_fill_conservation():
    state = rebalance_state()
    result = core.build_runtime_plan(state, session_date="2026-11-02", previous_session="2026-10-30",
                                     next_session="2026-11-03", regular_open=100, first_session_of_month=True)
    monthly = result["rebalance"]
    assert result["phase"] == "REBALANCE"
    assert [item["quantity"] for item in monthly["orders"]] == [12, 8]
    with pytest.raises(ValueError, match="fully confirmed"):
        core.settle_monthly_rebalance(state, monthly)
    for order in monthly["orders"]:
        state = filled(state, order, order["quantity"] * 101)
    state = core.settle_monthly_rebalance(state, monthly, valuation_price=100)
    atr_nav = Decimal(state["atr"]["cash"])
    r2_nav = Decimal(state["r2"]["cash"]) + sum(lot["shares"] for lot in state["r2"]["lots"]) * 100
    assert atr_nav == r2_nav == Decimal("8010")
    assert core.settle_monthly_rebalance(state, monthly, valuation_price=100) == state
    assert [lot["round"] for lot in state["r2"]["lots"]] == [1, 1]


def test_monthly_waits_for_atr_exit_and_cash_only_transfer_is_idempotent():
    state = filled(active(), intent(strategy="ATR", quantity=2, price=10, lot="atr-lot"), 20)
    with pytest.raises(ValueError, match="ATR exits"):
        core.plan_monthly_rebalance(state, session_date="2026-11-02", valuation_price=10)
    state = active()
    state["atr"]["cash"], state["r2"]["cash"] = "6000", "4000"
    monthly = core.plan_monthly_rebalance(state, session_date="2026-11-02", valuation_price=100)
    assert not monthly["orders"]
    state = core.settle_monthly_rebalance(state, monthly)
    assert Decimal(state["atr"]["cash"]) == Decimal(state["r2"]["cash"]) == 5000
    assert Decimal(state["r2"]["reference_seed"]) == 6000


def test_missed_month_start_does_not_skip_rebalance_after_restart():
    result = core.build_runtime_plan(active(), session_date="2026-11-03", previous_session="2026-11-02",
                                     next_session="2026-11-04", first_session_of_month=False)
    assert result["phase"] == "WAITING_OPEN_REBALANCE"


def test_cash_only_rebalance_plan_can_be_persisted_before_settlement():
    state = active()
    monthly = core.plan_monthly_rebalance(state, session_date="2026-11-02", valuation_price=100)
    state["pending_rebalance"] = deepcopy(monthly)
    state = core.settle_monthly_rebalance(state, monthly)
    assert "2026-11" in state["rebalance_months"]
    assert Decimal(state["atr"]["cash"]) == Decimal(state["r2"]["cash"])


def test_cash_and_ownership_tampering_fail_conservation_checks():
    state = active()
    state["r2"]["cash"] = "999999"
    with pytest.raises(ValueError, match="conserve"):
        core.validate_state(state)
    state = filled(active(), intent(), 50)
    state["r2"]["lots"][0]["shares"] -= 1
    with pytest.raises(ValueError, match="confirmed cumulative"):
        core.validate_state(state)


def test_late_entry_fee_is_allocated_between_remaining_and_sold_shares():
    buy = intent(quantity=5, price=12)
    state = core.register_order(active(), buy)
    state = core.reconcile_fills(state, [{**fill(buy, 5, 50, ".5"), "fee_source": "CONFIGURED_RESERVE"}])
    sale = intent(side="SELL", quantity=2, price=15)
    state = filled(state, sale, 30, ".15")
    correction = {**fill(buy, 5, 50, "1"), "fee_source": "BROKER"}
    state = core.reconcile_fills(state, [correction])
    assert Decimal(state["r2"]["lots"][0]["remaining_cost"]) == Decimal("30.6")
    assert Decimal(state["r2"]["settlements"][0]["profit_loss"]) == Decimal("9.45")
    assert len(state["r2"]["settlements"]) == 1
    assert core.reconcile_fills(state, [correction]) == state


@pytest.mark.parametrize("actual_fee,expected_seed", [("1.5", "5034.920"), ("0.5", "5035.640")])
def test_actual_fee_corrects_previously_reflected_profitable_sale_at_profit_rate(actual_fee, expected_seed):
    buy = intent(quantity=5, price=12)
    state = core.register_order(active(), buy)
    state = core.reconcile_fills(state, [{**fill(buy, 5, 50, "1"), "fee_source": "CONFIGURED_RESERVE"}])
    sale = intent(side="SELL", quantity=5, price=20)
    state = filled(state, sale, 100)
    state = core.advance_r2_session(state, "2026-10-12")
    assert Decimal(state["r2"]["seed"]) == Decimal("5035.28")
    correction = {**fill(buy, 5, 50, actual_fee), "fee_source": "BROKER"}
    state = core.reconcile_fills(state, [correction])
    assert Decimal(state["r2"]["seed"]) == Decimal(expected_seed)
    assert Decimal(state["r2"]["lots"][0]["remaining_cost"]) == 0
    assert not state["r2"]["settlements"]
    assert core.reconcile_fills(state, [correction]) == state


def test_partial_sales_are_one_profit_calculation_before_strategy_reflection():
    state = filled(active(), intent(quantity=5, price=12), 50)
    sale = intent(side="SELL", quantity=5, price=12)
    state = core.register_order(state, sale)
    state = core.reconcile_fills(state, [fill(sale, 2, 15)])
    state = core.reconcile_fills(state, [fill(sale, 5, 60)])
    assert len(state["r2"]["settlements"]) == 1
    assert Decimal(state["r2"]["settlements"][0]["profit_loss"]) == 10
    state = core.advance_r2_session(state, "2026-10-12")
    assert Decimal(state["r2"]["seed"]) == Decimal("5007.2")


@pytest.mark.parametrize("mutate", [
    lambda state: state.update(schema_version=True),
    lambda state: state.update(preset="KMW"),
    lambda state: state["atr"].update(cash="-1"),
    lambda state: state["r2"].update(reference_seed="NaN"),
    lambda state: state.update(fee_rate="1"),
    lambda state: state.update(rebalance_months=["2026-10", "2026-10"]),
])
def test_invalid_state_fails_closed(mutate):
    state = active()
    mutate(state)
    with pytest.raises(ValueError):
        core.validate_state(state)
