"""Confirmed account holdings refresh independently from quotes and other tabs."""
import ast
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.local_script_runner import LocalScriptRunner


APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No account, GitHub or quote transport in UI tests")
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("requests.get", forbidden)


def snapshot(*, fresh=False):
    result = {"2_2026-11-10": {"round": 2, "shares": 203, "buy_price": 70.0, "amount": 14210,
                              "mode": "SF", "sell_threshold": 1.1, "max_hold_days": 35},
              "available_cash": 10000, "compound_seed": 60000, "compound_reference_seed": 60000,
              "compound_settlements": [], "processed_seed_dates": [],
              "as_of_date": "2026-11-13" if fresh else "2026-11-12",
              "broker_sync": {"version": 1, "baseline_date": "2026-11-11", "executions": {}}}
    if fresh:
        result["3_2026-11-13"] = {"round": 3, "shares": 371, "buy_price": 68.0, "amount": 25228,
                                  "mode": "SF", "sell_threshold": 1.1, "max_hold_days": 35}
    else:
        result["1_2026-11-04"] = {"round": 1, "shares": 171, "buy_price": 75.0, "amount": 12825,
                                  "mode": "AG", "sell_threshold": 3.5, "max_hold_days": 7}
    return result


def functions(*names):
    nodes = [node for node in ast.parse(APP.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.returns = None
        for child in ast.walk(node):
            if isinstance(child, ast.arg):
                child.annotation = None
    return "\n\n".join(ast.unparse(node) for node in nodes)


def script(preset="KMW", *, mixed=False):
    body = functions("load_preset_snapshot", "_deduplicate_positions_by_date",
        "_broker_daily_selected", "show_broker_daily_fragment",
        "_show_broker_daily_snapshot_status", "show_daily_recommendation",
        "_atr70_r2_ui_selected", "_atr70_r2_order_tables", "show_atr70_r2_order_tables")
    initial = snapshot()
    if mixed:
        initial["atr70_r2"] = {"phase": "ACTIVE", "atr": {"cash": "0", "lots": []},
            "r2": {"cash": "0", "lots": []}, "orders": {"stored-sell": {
                "strategy": "R2", "side": "SELL", "order_type": "LOC", "quantity": 5, "price": "102",
                "broker_order_id": "202", "status": "ACCEPTED", "session_date": "2026-11-13"}}}
    return f'''
import streamlit as st
import pandas as pd
import re
from copy import deepcopy
from datetime import datetime as RealDatetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from streamlit.runtime.scriptrunner import get_script_run_ctx
from soxl_quant_system import SOXLQuantTrader

class datetime(RealDatetime):
    @classmethod
    def now(cls, tz=None):
        value = RealDatetime(2026, 11, 16, 0, 10, tzinfo=ZoneInfo('America/New_York'))
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)

class OfflineTrader(SOXLQuantTrader):
    def __init__(self):
        super().__init__(initial_capital=60000, auto_update_rsi=False)
    def get_today_date(self):
        return RealDatetime(2026, 11, 16)
    def get_latest_trading_day(self):
        return RealDatetime(2026, 11, 13)
    def get_daily_recommendation(self, **kwargs):
        st.session_state._recommendation_reads += 1
        return {{'error': '오프라인 테스트에서 시세 계산은 생략합니다.'}}
    def simulate_from_snapshot_to_today(self, *args, **kwargs):
        raise AssertionError('Confirmed KMW cannot simulate fills')
    def simulate_from_start_to_today(self, *args, **kwargs):
        raise AssertionError('Confirmed KMW cannot rebuild a simulation')

MA_ALIGNMENT_SWITCH_KEY='ma_alignment_switch_enabled'
ATR70_R2_SWITCH_KEY='atr70_r2_mix_enabled'
ATR70_R2_STATE_KEY='atr70_r2'
_PRESET_CONFIGS_KEY='_preset_configs'
if '_engine_snapshot' not in st.session_state:
    st.session_state._engine_snapshot={initial!r}
    st.session_state.positions_snapshot=deepcopy(st.session_state._engine_snapshot)
    st.session_state.active_preset={preset!r}
    st.session_state.kmw_preset={{MA_ALIGNMENT_SWITCH_KEY:False}}
    st.session_state.kmw2_preset={{MA_ALIGNMENT_SWITCH_KEY:False, ATR70_R2_SWITCH_KEY:{mixed!r}}}
    st.session_state.session_start_date='2025-08-27'
    st.session_state.position_edits={{}}
    st.session_state.trader=OfflineTrader()
    st.session_state._snapshot_reads=0
    st.session_state._recommendation_reads=0
    st.session_state._main_runs=0
    st.session_state._other_panel_runs=0
    st.session_state._mixed_renders=0
    st.session_state._intervals=[]
def _gh_load_all_snapshots():
    st.session_state._snapshot_reads += 1
    return {{{preset!r}:deepcopy(st.session_state._engine_snapshot)}}, 'synthetic-sha'
def _load_snapshot_fallback(*args, **kwargs):
    raise AssertionError('These complete fake snapshots do not use fallback')
def _gh_save_all_snapshots(*args, **kwargs):
    raise AssertionError('Display cannot write a broker snapshot')
def save_preset_snapshot(*args, **kwargs):
    raise AssertionError('Display cannot write a broker snapshot')
def show_atr70_r2_recommendation(snapshot, ma_alignment_placeholder=None):
    st.session_state._mixed_renders += 1
    st.info('기존 혼합 전략의 정상 표시 경로')
{body}

ctx = get_script_run_ctx()
original_enqueue = ctx.enqueue
def capture(message, *args, **kwargs):
    if message.HasField('auto_rerun'):
        st.session_state._intervals.append((message.auto_rerun.interval, message.auto_rerun.fragment_id))
    return original_enqueue(message, *args, **kwargs)
ctx.enqueue = capture
try:
    st.session_state._main_runs += 1
    if _broker_daily_selected():
        show_broker_daily_fragment(st.session_state.active_preset)
    else:
        st.info('다른 프리셋의 원래 화면')
    st.session_state._other_panel_runs += 1
finally:
    ctx.enqueue = original_enqueue
'''


def refresh_fragment(app):
    """Deliver the same fragment-only rerun request as the 60-second timer."""
    fragment_id = app.session_state._intervals[-1][1]
    original = LocalScriptRunner.request_rerun

    def request(runner, data):
        # LocalScriptRunner starts with an initial full-app boot request. Consume
        # that test-harness request so it cannot override the simulated timer.
        runner._requests.on_scriptrunner_ready()
        return original(runner, replace(data, fragment_id_queue=[fragment_id]))

    with patch.object(LocalScriptRunner, "request_rerun", request):
        app.run(timeout=20)
    assert not app.exception


@pytest.mark.parametrize("preset", ["KMW", "KMW2", "JEH", "JEH2", "KHW"])
def test_stale_live_account_lists_previous_inventory_without_simulating_or_hiding_one_lot(preset):
    app = AppTest.from_string(script(preset)).run(timeout=20)
    assert not app.exception and not app.error
    assert any("실제 체결 반영일: 2026-11-12" in item.value and "기준일: 2026-11-13" in item.value
               for item in app.caption)
    assert any("체결 동기화 대기" in item.value and "실계좌 최신 내역이 아닐 수" in item.value for item in app.warning)
    previous = app.dataframe[0].value
    assert sorted(previous["저장 수량"]) == [171, 203]
    assert any("이전 저장 보유내역" in heading.value for heading in app.subheader)
    assert not app.success and app.session_state._recommendation_reads == 0
    assert app.session_state._engine_snapshot == snapshot()
    assert app.session_state.trader.positions == []
    assert app.session_state._intervals[0][0] == 60 and app.session_state._intervals[0][1]


@pytest.mark.parametrize("preset", ["KMW", "KMW2", "JEH", "JEH2", "KHW"])
def test_real_fragment_timer_reloads_engine_checkpoint_without_rerunning_other_tabs(preset):
    app = AppTest.from_string(script(preset)).run(timeout=20)
    app.session_state._engine_snapshot = snapshot(fresh=True)
    refresh_fragment(app)
    assert app.session_state._main_runs == app.session_state._other_panel_runs == 1
    assert app.session_state._snapshot_reads == 2
    assert any("실제 체결 반영일: 2026-11-13" in item.value for item in app.caption)
    assert not any("체결 동기화 대기" in item.value for item in app.warning)
    assert sorted(position["shares"] for position in app.session_state.trader.positions) == [203, 371]
    assert sum(position["shares"] for position in app.session_state.trader.positions) == 574
    assert "1_2026-11-04" not in app.session_state.positions_snapshot
    assert app.session_state._engine_snapshot == snapshot(fresh=True)
    assert app.session_state._recommendation_reads == 1
    assert not app.dataframe  # The previous stored-inventory table was cleared.


def test_registered_account_timer_is_inert_after_switching_to_another_confirmed_account():
    app = AppTest.from_string(script()).run(timeout=20)
    app.session_state.active_preset = "JEH"
    other = {"available_cash": 1234, "keep_other_preset": True,
             "broker_sync": {"version": 1, "executions": {}}}
    app.session_state.positions_snapshot = deepcopy(other)
    refresh_fragment(app)
    assert app.session_state._main_runs == app.session_state._other_panel_runs == 1
    assert app.session_state._snapshot_reads == 1 and app.session_state._recommendation_reads == 0
    assert app.session_state.positions_snapshot == other
    assert app.session_state._engine_snapshot == snapshot()


def test_registered_broker_view_will_not_simulate_a_fallback_that_loses_confirmation_metadata():
    app = AppTest.from_string(script()).run(timeout=20)
    app.session_state._engine_snapshot = {"available_cash": 10000}
    refresh_fragment(app)
    assert any("확정 체결 장부를 확인하지 못해" in warning.value for warning in app.warning)
    assert app.session_state.trader.positions == [] and app.session_state._recommendation_reads == 0
    assert app.session_state._engine_snapshot == {"available_cash": 10000}
    app.session_state._engine_snapshot = snapshot(fresh=True)
    refresh_fragment(app)
    assert sorted(position["shares"] for position in app.session_state.trader.positions) == [203, 371]
    assert app.session_state._main_runs == app.session_state._other_panel_runs == 1


def test_snapshot_freshness_uses_actual_early_close_instead_of_quote_cache_or_normal_close_hour():
    helper = functions("_show_broker_daily_snapshot_status")
    source = snapshot(fresh=True)
    source["as_of_date"] = "2026-11-25"
    script_text = f'''
import streamlit as st
import pandas as pd
import re
from datetime import datetime
from zoneinfo import ZoneInfo
{helper}
st.session_state.ready = _show_broker_daily_snapshot_status(st.session_state.source, st.session_state.now)
'''
    app = AppTest.from_string(script_text)
    app.session_state.source = deepcopy(source)
    app.session_state.now = datetime(2026, 11, 27, 12, 59, tzinfo=ZoneInfo("America/New_York"))
    app.run(timeout=20)
    assert not app.exception and app.session_state.ready is True and not app.warning
    app.session_state.now = datetime(2026, 11, 27, 13, 1, tzinfo=ZoneInfo("America/New_York"))
    app.run(timeout=20)
    assert not app.exception and app.session_state.ready is False
    assert any("필요한 기준일 2026-11-27" in item.value for item in app.warning)
    assert app.session_state.source == source


def test_main_uses_the_confirmed_account_fragment_only_for_the_today_tab():
    module = ast.parse(APP.read_text())
    main = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    today = next(node for node in ast.walk(main) if isinstance(node, ast.With)
                 and any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == "tab1" for item in node.items))
    branch = today.body[0]
    assert isinstance(branch, ast.If) and ast.unparse(branch.test) == "_broker_daily_selected()"
    assert ast.unparse(branch.body[0]) == "show_broker_daily_fragment(st.session_state.active_preset)"
    assert "show_daily_recommendation(ma_alignment_placeholder)" in ast.unparse(branch.orelse[0])


def test_stale_mixed_account_keeps_saved_receipt_without_entering_current_recommendation_route():
    app = AppTest.from_string(script("KMW2", mixed=True)).run(timeout=20)
    assert not app.exception and not app.error
    assert any("실제 체결 반영일: 2026-11-12" in item.value for item in app.caption)
    assert any("체결 동기화 대기" in item.value for item in app.warning)
    assert any("R2 · 저장된 매도 주문" in item.value for item in app.info)
    assert any("주문번호: 202" in item.value for item in app.caption)
    assert not app.success and app.session_state._mixed_renders == 0
    assert app.session_state._recommendation_reads == 0


def test_simulation_preset_never_registers_the_shared_live_account_timer():
    app = AppTest.from_string(script("KMW3")).run(timeout=20)
    assert not app.exception and app.session_state._intervals == []
    assert app.session_state._snapshot_reads == 0


def test_periodic_account_render_does_not_write_automatic_mdd_updates():
    daily = next(node for node in ast.parse(APP.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == "show_daily_recommendation")
    record_block = next(node for node in daily.body if isinstance(node, ast.If)
                        and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                                and call.func.id == "_record_snapshot_max_mdd" for call in ast.walk(node)))

    class Session(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    original = snapshot(fresh=True)
    state = Session(active_preset="KMW", positions_snapshot=deepcopy(original))
    record = Mock(return_value=(deepcopy(original), {"percent": 12}, True))
    save = Mock(return_value=(True, ""))
    scope = {"st": SimpleNamespace(session_state=state), "broker_only": True,
             "mdd_info": {"mdd_percent": 12}, "recommendation": {"soxl_current_price": 100},
             "_record_snapshot_max_mdd": record, "save_preset_snapshot": save}
    code = compile(ast.Module(body=[record_block], type_ignores=[]), str(APP), "exec")
    exec(code, scope)
    record.assert_not_called()
    save.assert_not_called()
    assert state.positions_snapshot == original
    # The existing non-periodic/manual page path keeps its prior behavior.
    scope["broker_only"] = False
    exec(code, scope)
    record.assert_called_once()
    save.assert_called_once_with("KMW", original)
