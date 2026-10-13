"""Confirmed account snapshots stay factual; all data and writes are fake."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from kmw_order_plan import build_kmw_plan
from soxl_quant_system import SOXLQuantTrader, recommendation_buy_order_terms


def confirmed_snapshot():
    return {
        "1_2026-09-28": {"round": 1, "shares": 3, "buy_price": 100.0,
                         "amount": 300.0, "mode": "AG", "buy_threshold": 3.6,
                         "sell_threshold": 3.5, "max_hold_days": 7},
        "2_2026-10-05": {"round": 2, "shares": 11, "buy_price": 100.0,
                         "amount": 1100.0, "mode": "AG", "buy_threshold": 3.6,
                         "sell_threshold": 3.5, "max_hold_days": 7},
        "available_cash": 10000.0, "as_of_date": "2026-10-09",
        "processed_seed_dates": ["2025-12-22", "2026-01-15"],
        "compound_seed": 20000.0, "compound_reference_seed": 19000.0,
        "compound_profit_rate": 0.7, "compound_loss_rate": 0.2,
        "compound_trading_days": 9,
        "compound_settlements": [{"trade_date": "2026-10-05", "settlement_date": "2026-10-12", "pnl": 100.0}],
        "broker_sync": {"version": 1, "baseline_date": "2026-10-05", "executions": {
            "2026-10-06:123": {"trade_date": "2026-10-06", "side": "SELL", "quantity": 2,
                              "notional": "200.00", "fees": "0.20", "position_key": "3_2026-10-05"},
        }},
    }


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Real network access is forbidden"))
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("requests.get", forbidden)


def new_trader():
    trader = SOXLQuantTrader(initial_capital=2793.0, auto_update_rsi=False)
    trader.set_profit_loss_compounding(enabled=True, profit_rate=0.7, loss_rate=0.2)
    return trader


def app_functions(*names, **extra):
    """Load only pure helpers; never import or start the Streamlit app."""
    path = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    for function in functions:
        function.returns = None
        for node in ast.walk(function):
            if isinstance(node, ast.arg):
                node.annotation = None
    namespace = {"pd": pd, "np": np, "datetime": datetime, "timedelta": timedelta,
                 "deepcopy": deepcopy, "MA_ALIGNMENT_SWITCH_KEY": "ma_alignment_switch_enabled",
                 "_merge_snapshot_max_mdd": lambda current, previous: current,
                 "_snapshot_has_positions": lambda snapshot: any(isinstance(v, dict) and "shares" in v for v in snapshot.values()),
                 **extra}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def test_confirmed_restore_never_replays_prices_or_creates_pending_buy(monkeypatch):
    trader = new_trader()
    snapshot = confirmed_snapshot()
    snapshot["pending_buy"] = {"round": 3, "order_date": "2026-10-12", "quantity": 99, "target_price": 100.0}
    original = deepcopy(snapshot)
    forbidden = Mock(side_effect=AssertionError("Confirmed restore attempted a price simulation"))
    monkeypatch.setattr(trader, "run_backtest", forbidden)
    monkeypatch.setattr(trader, "get_stock_data", forbidden)
    result = trader.simulate_from_snapshot_to_today(snapshot, "2025-10-30")
    assert result["broker_confirmed"] is True
    assert sum(p["shares"] for p in trader.positions) == 14
    assert trader.available_cash == 10000
    assert trader.current_round == 3 and trader.trading_days_count == 9
    assert trader.compound_seed == 20000 and trader.compound_reference_seed == 19000
    assert trader._pending_buy_recommendation is None
    assert snapshot == original
    forbidden.assert_not_called()


def test_confirmed_holdings_survive_price_target_hits_and_loc_failures():
    trader = new_trader()
    assert "error" not in trader.restore_real_positions(confirmed_snapshot())
    before = deepcopy(trader.positions)
    data = pd.DataFrame({"Close": 1000.0}, index=pd.bdate_range("2026-09-01", "2026-10-12"))
    trader.reconcile_positions_with_close_history(data)
    trader._prune_positions_failing_loc_verification(data)
    trader._process_compounding_for_date(datetime(2026, 10, 12))
    assert trader.positions == before and trader.available_cash == 10000
    assert trader.compound_seed == 20000 and trader.compound_reference_seed == 19000
    assert len(trader.compound_settlements) == 1


def test_confirmed_entry_mode_and_frozen_rules_are_not_recomputed(monkeypatch):
    trader = new_trader()
    forbidden = Mock(side_effect=AssertionError("Recomputed confirmed entry mode"))
    monkeypatch.setattr(trader, "_calculate_week_mode_recursive_with_reference", forbidden)
    trader.restore_real_positions(confirmed_snapshot())
    trader._revalidate_position_modes(trader.positions)
    assert [(p["mode"], p["sell_threshold"], p["max_hold_days"]) for p in trader.positions] == [("AG", 3.5, 7)] * 2
    forbidden.assert_not_called()


def test_malformed_confirmation_marker_fails_without_legacy_simulation(monkeypatch):
    trader = new_trader()
    snapshot = confirmed_snapshot()
    snapshot["broker_sync"] = {"version": 2}
    forbidden = Mock(side_effect=AssertionError("Malformed confirmation fell back to simulation"))
    monkeypatch.setattr(trader, "run_backtest", forbidden)
    assert "error" in trader.simulate_from_snapshot_to_today(snapshot, "2025-10-30")
    forbidden.assert_not_called()


def test_cash_only_confirmed_snapshot_and_counter_restore_without_price_queries(monkeypatch):
    trader = new_trader()
    snapshot = confirmed_snapshot()
    for key in [key for key, value in snapshot.items() if isinstance(value, dict) and "shares" in value]:
        snapshot.pop(key)
    forbidden = Mock(side_effect=AssertionError("Cash-only restore fetched prices"))
    monkeypatch.setattr(trader, "get_stock_data", forbidden)
    assert trader.restore_real_positions(snapshot)["cash_only"] is True
    assert trader.positions == [] and trader.current_round == 1
    assert trader.trading_days_count == 9 and trader.available_cash == 10000
    forbidden.assert_not_called()


def test_legacy_snapshot_reconciliation_still_simulates_a_target_hit(monkeypatch):
    trader = new_trader()
    snapshot = confirmed_snapshot()
    snapshot.pop("broker_sync")
    positions, _, cash = trader._snapshot_to_positions_and_state(snapshot)
    trader.positions, trader.available_cash = positions, cash
    monkeypatch.setattr(trader, "get_today_date", lambda: datetime(2026, 10, 13))
    monkeypatch.setattr(trader, "is_regular_session_closed_now", lambda: True)
    data = pd.DataFrame({"Close": [200.0]}, index=pd.to_datetime(["2026-10-09"]))
    with redirect_stdout(io.StringIO()):
        trader.reconcile_positions_with_close_history(data)
    assert trader.positions == [] and trader.available_cash == 12800


def test_reset_and_loading_legacy_snapshot_clear_confirmation_mode():
    trader = new_trader()
    trader.restore_real_positions(confirmed_snapshot())
    trader.reset_portfolio()
    assert trader._broker_confirmed_positions is False and trader._broker_sync_metadata is None
    trader.restore_real_positions(confirmed_snapshot())
    legacy = confirmed_snapshot()
    legacy.pop("broker_sync")
    trader._snapshot_to_positions_and_state(legacy)
    assert trader._broker_confirmed_positions is False


@pytest.mark.parametrize("runtime,preserve", [(True, True), (True, False), (False, False)])
def test_app_snapshot_roundtrip_keeps_cursor_count_and_actual_checkpoint(runtime, preserve, monkeypatch):
    trader = new_trader()
    snapshot = confirmed_snapshot()
    trader.restore_real_positions(snapshot)
    monkeypatch.setattr(trader, "get_latest_trading_day", lambda: datetime(2026, 10, 12))
    namespace = app_functions("_build_snapshot_from_positions", "_preserve_position_strategy_fields",
                              "_serialize_compound_settlements", "_preserve_broker_snapshot_metadata")
    output = namespace["_build_snapshot_from_positions"](trader, snapshot, include_runtime_state=runtime,
                                                          preserve_saved_positions=preserve)
    assert output["broker_sync"] == snapshot["broker_sync"]
    assert output["broker_sync"] is not snapshot["broker_sync"]
    assert output["compound_trading_days"] == 9
    assert output["as_of_date"] == "2026-10-09"
    assert [output[key]["shares"] for key in ("1_2026-09-28", "2_2026-10-05")] == [3, 11]


def test_app_confirmed_daily_view_does_not_request_automatic_save():
    namespace = app_functions("_should_auto_save_snapshot")
    previous = confirmed_snapshot()
    current = deepcopy(previous)
    current["pending_buy"] = {"quantity": 50}
    assert namespace["_should_auto_save_snapshot"](previous, current) is False


def test_confirmed_same_day_lots_are_not_deduplicated():
    namespace = app_functions("_deduplicate_positions_by_date")
    trader = SimpleNamespace(positions=[{"round": 1, "buy_date": datetime(2026, 10, 5), "shares": 2},
                                        {"round": 2, "buy_date": datetime(2026, 10, 5), "shares": 3}])
    before = deepcopy(trader.positions)
    namespace["_deduplicate_positions_by_date"](trader, confirmed_snapshot())
    assert trader.positions == before


@pytest.mark.parametrize("change", ["empty", "missing_cursor", "new_fills", "new_day"])
def test_stale_app_or_settings_reset_cannot_overwrite_confirmed_cursor(change):
    latest = confirmed_snapshot()
    stale = deepcopy(latest)
    if change == "empty":
        stale = {}
    elif change == "missing_cursor":
        stale.pop("broker_sync")
    elif change == "new_fills":
        latest["broker_sync"]["executions"]["2026-10-09:456"] = {"quantity": 3}
    else:
        latest["as_of_date"] = "2026-10-12"
    write = Mock(side_effect=AssertionError("Stale view attempted a write"))
    namespace = app_functions("save_preset_snapshot", "_broker_snapshot_write_allowed",
                              st=SimpleNamespace(session_state=SimpleNamespace()),
                              _gh_load_all_snapshots=Mock(return_value=({"JEH": latest}, "fake-sha")),
                              _gh_save_all_snapshots=write, _write_local_all_snapshots=write)
    ok, error = namespace["save_preset_snapshot"]("JEH", stale)
    assert ok is False and "최신 상태" in error
    write.assert_not_called()


def test_manual_edit_keeps_exact_cursor_and_retry_protects_newer_broker_state():
    latest = confirmed_snapshot()
    edited = deepcopy(latest)
    edited["2_2026-10-05"]["shares"] = 12
    updated = deepcopy(latest)
    updated["as_of_date"] = "2026-10-12"
    write = Mock(return_value=(False, "GitHub API (409)"))
    namespace = app_functions("save_preset_snapshot", "_broker_snapshot_write_allowed",
                              st=SimpleNamespace(session_state=SimpleNamespace()),
                              _gh_load_all_snapshots=Mock(side_effect=[({"JEH": latest}, "one"), ({"JEH": updated}, "two")]),
                              _gh_save_all_snapshots=write,
                              _is_manual_cash_locked_snapshot=lambda snapshot: False)
    ok, _ = namespace["save_preset_snapshot"]("JEH", edited)
    assert ok is False and write.call_count == 1
    assert write.call_args.args[0]["JEH"]["broker_sync"] == latest["broker_sync"]


def test_original_recommendation_and_headless_order_plan_match_confirmed_rows(monkeypatch):
    moment = datetime(2026, 10, 12, 17, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    data = pd.DataFrame({"Open": 200.0, "High": 200.0, "Low": 200.0,
                         "Close": 200.0, "Volume": 1000000},
                        index=pd.bdate_range("2026-04-01", "2026-10-09"))
    monkeypatch.setattr(SOXLQuantTrader, "get_stock_data", lambda *args, **kwargs: data.copy())
    monkeypatch.setattr(SOXLQuantTrader, "get_us_eastern_now", lambda self: moment.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None))
    monkeypatch.setattr(SOXLQuantTrader, "get_rsi_from_reference", lambda *args, **kwargs: 60.0)
    monkeypatch.setattr(SOXLQuantTrader, "update_mode", lambda self, *args: setattr(self, "current_mode", "AG") or "AG")
    monkeypatch.setattr(SOXLQuantTrader, "_calculate_week_mode_recursive_with_reference", lambda *args: ("AG", True))
    snapshot = confirmed_snapshot()
    trader = new_trader()
    trader.session_start_date = "2025-10-30"
    with redirect_stdout(io.StringIO()):
        assert "error" not in trader.restore_real_positions(snapshot)
        rec = trader.get_daily_recommendation(skip_simulate=True, preserve_snapshot_shares=True)
        plan = build_kmw_plan({"JEH": snapshot}, now=moment, preset="JEH")
    assert "error" not in rec
    displayed = [(s["position"]["shares"], s["order_type"], s["order_price"])
                 for s in rec["sell_recommendations"]]
    assert displayed == [(o["quantity"], o["order_type"], o["price"])
                         for o in plan["orders"] if o["side"] == "SELL"]
    buy = next(o for o in plan["orders"] if o["side"] == "BUY")
    terms = recommendation_buy_order_terms(rec["buy_price"], rec["next_buy_amount"])
    assert terms == {"quantity": buy["quantity"], "price": buy["price"]}
    assert trader.available_cash == plan["available_cash"] == 10000
    assert trader.compound_seed == 20000 and trader.trading_days_count == 9
