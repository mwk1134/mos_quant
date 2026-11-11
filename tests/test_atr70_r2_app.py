"""KMW2 switch persistence and actual Streamlit components with fake accounts."""
import ast
import base64
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


APP = Path(__file__).resolve().parents[1] / "app.py"
FLAG = "atr70_r2_mix_enabled"
MA = "ma_alignment_switch_enabled"


class Session(dict):
    __setattr__ = dict.__setitem__

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def functions(*names, **extra):
    tree = ast.parse(APP.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    for node in selected:
        node.returns = None
        for child in ast.walk(node):
            if isinstance(child, ast.arg):
                child.annotation = None
    scope = {"deepcopy": deepcopy, "datetime": datetime, "Decimal": Decimal,
             "InvalidOperation": InvalidOperation, "re": re,
             "MA_ALIGNMENT_SWITCH_KEY": MA, "ATR70_R2_SWITCH_KEY": FLAG,
             "ATR70_R2_STATE_KEY": "atr70_r2", "_PRESET_CONFIGS_KEY": "_preset_configs",
             **extra}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(APP), "exec"), scope)
    return scope


def mixed_state():
    return {"phase": "ACTIVE", "fee_rate": "0.005", "fee_source": "configured_reserve", "atr": {"cash": "5000", "lots": []},
            "r2": {"cash": "4000", "lots": [{"shares": 10, "entry_date": "2026-10-30", "entry_price": "100"}]}}


def document():
    return {"KMW2": {"available_cash": 9000.0, "as_of_date": "2026-10-30", "atr70_r2": mixed_state()},
            "KMW": {"available_cash": 111},
            "_preset_configs": {"KMW2": {"initial_capital": 10000, "session_start_date": "2026-10-01",
                                          "seed_increases": [], "position_edits": {}, MA: True, FLAG: False},
                                "KMW": {"initial_capital": 111}}}


def switch_helpers(doc=None):
    doc = deepcopy(doc if doc is not None else document())
    state = Session(active_preset="KMW2", kmw2_preset=deepcopy(doc["_preset_configs"]["KMW2"]),
                    positions_snapshot={"old_page": True}, **{FLAG: False})
    save = Mock(return_value=(True, ""))
    load = Mock(return_value=(deepcopy(doc), "sha"))
    local = Mock()
    scope = functions("save_preset_atr70_r2_setting", "_normalize_preset_config", "_copy_seed_increases",
                      st=SimpleNamespace(session_state=state), _gh_load_all_snapshots=load,
                      _gh_save_all_snapshots=save, _write_local_all_snapshots=local)
    return scope, state, save, load, local


def test_switch_save_changes_only_preference_and_keeps_latest_mixed_ledger():
    scope, state, save, _, local = switch_helpers()
    before = document()
    assert scope["save_preset_atr70_r2_setting"]("KMW2", True) == (True, "")
    written, sha = save.call_args.args
    expected = deepcopy(before)
    expected["KMW2"][FLAG] = True
    expected["_preset_configs"]["KMW2"][FLAG] = True
    assert written == expected and sha == "sha"
    assert state.positions_snapshot == expected["KMW2"]
    assert state.kmw2_preset[MA] is True
    assert state[FLAG] is True
    local.assert_called_once_with(expected)


def test_switch_conflict_retries_only_the_flag_on_latest_document():
    scope, state, save, load, _ = switch_helpers()
    newest = document()
    newest["KMW2"]["atr70_r2"]["atr"]["cash"] = "4500"
    newest["KMW2"]["atr70_r2"]["revision"] = 2
    newest["_preset_configs"]["KMW2"][MA] = False
    newest["KMW"]["available_cash"] = 999
    load.side_effect = [(document(), "old-sha"), (newest, "latest-sha"), (newest, "final-sha")]
    save.side_effect = [(False, "GitHub API (409)"), (True, "")]
    assert scope["save_preset_atr70_r2_setting"]("KMW2", True)[0]
    written, sha = save.call_args.args
    assert sha == "latest-sha"
    assert written["KMW2"]["atr70_r2"] == newest["KMW2"]["atr70_r2"]
    assert written["KMW"] == newest["KMW"]
    assert written["_preset_configs"]["KMW2"][MA] is False
    assert state.positions_snapshot["atr70_r2"]["revision"] == 2


def test_failed_switch_save_does_not_look_enabled_or_write_local_account():
    scope, state, save, _, local = switch_helpers()
    original = deepcopy(state)
    save.return_value = False, "not saved"
    assert scope["save_preset_atr70_r2_setting"]("KMW2", True) == (False, "not saved")
    assert state == original
    local.assert_not_called()


@pytest.mark.parametrize("fresh_document,sha", [({}, None), ({"KMW2": {}}, None)])
def test_snapshot_save_requires_fresh_document_and_sha_before_any_write(fresh_document, sha):
    state = Session(_gh_snapshot_all={"KMW2": {"cached_partial": True}}, _gh_snapshot_sha="cached-sha")
    before = deepcopy(state)
    save, local = Mock(), Mock()
    scope = functions("save_preset_snapshot", st=SimpleNamespace(session_state=state),
                      _gh_load_all_snapshots=Mock(return_value=(fresh_document, sha)),
                      _gh_save_all_snapshots=save, _write_local_all_snapshots=local)
    ok, error = scope["save_preset_snapshot"]("KMW2", {"new_snapshot": True})
    assert not ok and "최신 원본과 버전" in error
    save.assert_not_called()
    local.assert_not_called()
    assert state == before


def test_snapshot_conflict_read_failure_stops_without_cached_or_local_retry():
    document = {"KMW2": {"old": True}, "KMW": {"other_account": True}}
    state = Session(_gh_snapshot_all={"KMW2": {"cached_partial": True}}, _gh_snapshot_sha="cached-sha")
    before = deepcopy(state)
    save = Mock(return_value=(False, "GitHub API (409)"))
    local = Mock()
    scope = functions("save_preset_snapshot", st=SimpleNamespace(session_state=state),
                      _gh_load_all_snapshots=Mock(side_effect=[(document, "fresh-sha"), ({}, None)]),
                      _gh_save_all_snapshots=save, _write_local_all_snapshots=local,
                      _broker_snapshot_write_allowed=Mock(return_value=True),
                      _is_manual_cash_locked_snapshot=Mock(return_value=False),
                      _merge_snapshot_max_mdd=lambda current, previous: current)
    ok, error = scope["save_preset_snapshot"]("KMW2", {"new_snapshot": True})
    assert not ok and "최신 원본과 버전" in error
    save.assert_called_once_with({"KMW2": {"new_snapshot": True}, "KMW": {"other_account": True}}, "fresh-sha")
    local.assert_not_called()
    assert state == before


@pytest.mark.parametrize("preset", ["KMW", "JEH", "JEH2", "KHW", "kmw2", ""])
def test_switch_rejects_every_other_preset_without_reading_or_saving(preset):
    scope, _, save, load, local = switch_helpers()
    assert scope["save_preset_atr70_r2_setting"](preset, True)[0] is False
    save.assert_not_called()
    load.assert_not_called()
    local.assert_not_called()


def test_mixed_ledger_cannot_be_reset_changed_or_replaced_with_legacy_positions():
    scope = functions("_broker_snapshot_write_allowed")
    previous = document()["KMW2"]
    assert scope["_broker_snapshot_write_allowed"](deepcopy(previous), previous)
    mirrored = {**deepcopy(previous), FLAG: True}
    assert scope["_broker_snapshot_write_allowed"](mirrored, previous) is False
    variants = [{}, {"available_cash": 9000}]
    changed = deepcopy(previous)
    changed["atr70_r2"]["atr"]["cash"] = "5001"
    variants.append(changed)
    changed = deepcopy(previous)
    changed["available_cash"] = 9001
    variants.append(changed)
    changed = deepcopy(previous)
    changed["1_2026-10-30"] = {"shares": 1}
    variants.append(changed)
    for variant in variants:
        assert scope["_broker_snapshot_write_allowed"](variant, previous) is False


def test_waiting_switch_also_protects_legacy_lots_from_a_stale_simulating_page():
    scope = functions("_broker_snapshot_write_allowed")
    previous = {FLAG: True, "available_cash": 1000,
                "1_2026-10-30": {"round": 1, "shares": 3, "buy_price": 100}}
    assert scope["_broker_snapshot_write_allowed"](deepcopy(previous), previous)
    stale = {"available_cash": 1300, FLAG: False}
    assert scope["_broker_snapshot_write_allowed"](stale, previous) is False


def test_active_mixed_snapshot_is_never_built_from_legacy_trader():
    scope = functions("_build_snapshot_from_positions")
    original = document()["KMW2"]
    trader = Mock()
    result = scope["_build_snapshot_from_positions"](trader, original)
    assert result == original and result is not original
    trader.assert_not_called()


@pytest.mark.parametrize("active", [False, True])
def test_waiting_or_active_mixed_snapshot_never_simulates_fills(active):
    constructor = Mock(side_effect=AssertionError("Legacy simulation forbidden"))
    scope = functions("_simulate_preset_snapshot", SOXLQuantTrader=constructor)
    original = document()["KMW2"] if active else {"available_cash": 10000, "1_2026-10-30": {"shares": 3}}
    result, error = scope["_simulate_preset_snapshot"]("KMW2", {FLAG: True}, original)
    assert result == original and result is not original and error is None
    constructor.assert_not_called()


def test_sleeve_display_keeps_strategy_ownership_and_actual_quantities():
    scope = functions("_atr70_r2_sleeve_rows")
    summary, lots = scope["_atr70_r2_sleeve_rows"](mixed_state())
    assert [row["SOXL 보유"] for row in summary] == [0, 10]
    assert [row["장부 현금"] for row in summary] == ["$5,000.00", "$4,000.00"]
    assert lots[0]["전략"] == "정배열 R2" and lots[0]["수량"] == 10
    for cash in ["NaN", "Infinity", "-1"]:
        bad = mixed_state()
        bad["atr"]["cash"] = cash
        with pytest.raises(ValueError):
            scope["_atr70_r2_sleeve_rows"](bad)


def component_script(active="KMW2", running=False, fee_rate="0.005", *, enabled=None,
                     real_snapshot_loader=False, flat_waiting=False):
    """Run the real changed Streamlit functions with an entirely fake backend."""
    names = {"_copy_seed_increases", "_normalize_preset_config", "_preset_state_key", "save_preset_atr70_r2_setting",
             "_atr70_r2_ui_selected", "_atr70_r2_live_start", "_atr70_r2_waiting_message",
             "show_atr70_r2_preference_control",
             "_atr70_r2_sleeve_rows",
             "_atr70_r2_readonly_plan", "_atr70_r2_order_tables", "show_atr70_r2_order_tables",
             "show_atr70_r2_recommendation", "show_daily_recommendation"}
    if real_snapshot_loader:
        names.add("load_preset_snapshot")
    tree = ast.parse(APP.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in nodes:
        node.returns = None
        for child in ast.walk(node):
            if isinstance(child, ast.arg):
                child.annotation = None
    doc = document()
    doc["_preset_configs"]["KMW2"][FLAG] = running if enabled is None else enabled
    if running:
        doc["KMW2"]["atr70_r2"]["fee_rate"] = fee_rate
    if not running:
        doc["KMW2"].pop("atr70_r2")
        doc["KMW2"]["1_2026-10-30"] = {"round": 1, "shares": 3, "buy_price": 100}
        if flat_waiting:
            doc["KMW2"].pop("1_2026-10-30")
            doc["KMW2"]["atr70_r2"] = {"phase": "WAITING_FLAT", "initial_cash": "74006.54"}
    prelude = f'''
import streamlit as st
import pandas as pd
import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
MA_ALIGNMENT_SWITCH_KEY={MA!r}
ATR70_R2_SWITCH_KEY={FLAG!r}
ATR70_R2_STATE_KEY='atr70_r2'
_PRESET_CONFIGS_KEY='_preset_configs'
class SOXLQuantTrader:
    MA_ALIGNMENT_SF_CONFIG={{'split_ratios':[1.0], 'split_count':1}}
    MA_ALIGNMENT_AG_CONFIG={{'split_ratios':[1.0], 'split_count':1}}
if '_stored' not in st.session_state:
    st.session_state._stored={doc!r}
    st.session_state.active_preset={active!r}
    st.session_state.kmw2_preset=deepcopy(st.session_state._stored['_preset_configs']['KMW2'])
    st.session_state.kmw_preset={{MA_ALIGNMENT_SWITCH_KEY:True}}
    st.session_state.positions_snapshot=deepcopy(st.session_state._stored.get({active!r}, {{}}))
    st.session_state.trader=None
def _gh_load_all_snapshots():
    return deepcopy(st.session_state._stored), 'fake-sha'
def _gh_save_all_snapshots(value, sha):
    st.session_state.save_attempts=st.session_state.get('save_attempts',0)+1
    if st.session_state.get('_save_error'):
        return False, st.session_state._save_error
    st.session_state._stored=deepcopy(value)
    st.session_state.save_count=st.session_state.get('save_count',0)+1
    return True, ''
def _write_local_all_snapshots(value):
    pass
def load_preset_snapshot(preset):
    return deepcopy(st.session_state._stored[preset])
'''
    body = "\n\n".join(ast.unparse(node) for node in nodes)
    return prelude + "\n" + body + '''
show_atr70_r2_preference_control()
placeholder=None
if _atr70_r2_ui_selected():
    show_daily_recommendation(placeholder)
'''



def test_streamlit_switch_on_waits_for_actual_zero_with_only_the_mixed_switch():
    from streamlit.testing.v1 import AppTest
    with patch("requests.Session.request", side_effect=AssertionError("Network forbidden")), \
            patch("requests.get", side_effect=AssertionError("Network forbidden")):
        app = AppTest.from_string(component_script()).run(timeout=20)
        assert not app.exception
        mixed = next(toggle for toggle in app.toggle if toggle.label == "반반매매 (ATR70-R2) 적용")
        mixed.set_value(True).run(timeout=20)
        assert not app.exception
        assert app.session_state._stored["_preset_configs"]["KMW2"][FLAG] is True
        assert "atr70_r2" not in app.session_state._stored["KMW2"]
        assert app.session_state._stored["KMW2"]["1_2026-10-30"]["shares"] == 3
        assert [toggle.label for toggle in app.toggle] == ["반반매매 (ATR70-R2) 적용"]
        assert any("전량 매도 대기" in warning.value for warning in app.warning)


def test_streamlit_active_mixed_off_keeps_lots_and_shows_exit_management():
    from streamlit.testing.v1 import AppTest
    with patch("requests.Session.request", side_effect=AssertionError("Network forbidden")):
        app = AppTest.from_string(component_script(running=True)).run(timeout=20)
        assert not app.exception
        assert len(app.dataframe) == 2
        assert len(order_html_boards(app)) == 2
        assert any("비용 여유분 0.50%는 추정 비용이며 실제 브로커 수수료와 다를 수 있습니다." == caption.value for caption in app.caption)
        mixed = next(toggle for toggle in app.toggle if toggle.label == "반반매매 (ATR70-R2) 적용")
        mixed.set_value(False).run(timeout=20)
        assert not app.exception
        assert app.session_state._stored["_preset_configs"]["KMW2"][FLAG] is False
        assert app.session_state._stored["KMW2"]["atr70_r2"] == mixed_state()
        assert any("청산 관리" in warning.value for warning in app.warning)
        assert len(app.dataframe) == 2
        assert len(order_html_boards(app)) == 2
        assert [toggle.label for toggle in app.toggle] == ["반반매매 (ATR70-R2) 적용"]


def test_flat_account_waits_for_start_conditions_and_settled_usd():
    from streamlit.testing.v1 import AppTest
    with patch("kmw_order_plan.build_kmw_plan", side_effect=ValueError("Data unavailable")):
        app = AppTest.from_string(component_script(flat_waiting=True, enabled=True)).run(timeout=20)
    assert not app.exception
    assert any("실계좌 시작 조건 확인 대기" in warning.value for warning in app.warning)
    assert not any("전량 매도 대기" in warning.value for warning in app.warning)
    assert any("USD 예수금과 추정 예수금 일치" in caption.value for caption in app.caption)
    assert any("실제 SOXL 잔고 0주·미체결 및 활성 예약 없음" in text.value for text in app.markdown)
    assert "save_attempts" not in app.session_state
    assert app.session_state._stored["KMW2"]["atr70_r2"]["phase"] == "WAITING_FLAT"


@pytest.mark.parametrize("fee_rate", ["NaN", "Infinity", "-0.005", "1", True])
def test_streamlit_never_displays_invalid_fee_reserve_as_a_real_cost(fee_rate):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string(component_script(running=True, fee_rate=fee_rate)).run(timeout=20)
    assert not app.exception
    assert any(caption.value == "비용 여유분 설정을 확인할 수 없습니다." for caption in app.caption)
    assert not any("추정 비용이며" in caption.value for caption in app.caption)


@pytest.mark.parametrize("preset", ["KMW", "JEH", "JEH2", "KHW", None, "UNREGISTERED"])
def test_streamlit_other_preset_has_no_strategy_switch_or_writes(preset):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string(component_script(active=preset)).run(timeout=20)
    assert not app.exception
    assert not app.toggle
    assert "save_count" not in app.session_state


def test_streamlit_kmw2_off_still_has_exactly_one_mixed_switch():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string(component_script(running=False)).run(timeout=20)
    assert not app.exception
    assert [toggle.label for toggle in app.toggle] == ["반반매매 (ATR70-R2) 적용"]
    assert app.toggle[0].value is False
    assert "save_count" not in app.session_state


@pytest.mark.parametrize("preset", ["KMW", "KMW2", "JEH", "JEH2", "KHW", None, "UNREGISTERED"])
@pytest.mark.parametrize("enabled", [False, True])
def test_retired_legacy_switch_cannot_mutate_preferences_or_read_or_write_accounts(preset, enabled):
    state = Session(active_preset="KMW2", kmw2_preset={MA: True}, **{MA: True})
    before = deepcopy(state)
    read, save, local = Mock(), Mock(), Mock()
    scope = functions("save_preset_ma_alignment_setting", st=SimpleNamespace(session_state=state),
                      _gh_load_all_snapshots=read, _gh_save_all_snapshots=save,
                      _write_local_all_snapshots=local)
    ok, message = scope["save_preset_ma_alignment_setting"](preset, enabled)
    assert ok is False and isinstance(message, str) and message
    assert state == before
    read.assert_not_called()
    save.assert_not_called()
    local.assert_not_called()


@pytest.mark.parametrize("requested", [None, False, True])
def test_retired_alignment_setting_stays_saved_but_is_disabled_for_legacy_new_orders(requested):
    state = Session(**{MA: True})
    trader = Mock()
    scope = functions("configure_app_trader", st=SimpleNamespace(session_state=state),
                      APP_COMPOUNDING_ENABLED=True, APP_PROFIT_COMPOUNDING_RATE=0.70,
                      APP_LOSS_COMPOUNDING_RATE=0.20, APP_COMPOUNDING_SETTLEMENT_DAYS=7,
                      APP_COMPOUNDING_RENEWAL_DAYS=10)
    result = scope["configure_app_trader"](trader, ma_alignment_enabled=requested)
    assert result is trader
    trader.set_ma_alignment_switch.assert_called_once_with(False)
    assert state[MA] is True


def test_legacy_switch_renderers_and_switch_prompt_are_removed_from_the_app():
    tree = ast.parse(APP.read_text())
    functions_present = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not {"show_ma_alignment_preference_control", "show_ma_alignment_alert", "_update_ma_alignment_display"} & functions_present
    string_literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert "이 프리셋에 정배열 전략 적용" not in string_literals
    assert not any("스위치를 켜면 다음 신규 매수에 정배열 전략을 적용" in value for value in string_literals)


@pytest.mark.parametrize("running", [False, True])
def test_full_webapp_mixed_waiting_and_active_render_without_network_or_writes(running):
    from streamlit.testing.v1 import AppTest
    doc = document()
    doc["_preset_configs"]["KMW2"][FLAG] = True
    doc["_preset_configs"]["KMW2"].update(initial_capital=74006.54, session_start_date="2026-11-12")
    if running:
        doc["KMW2"]["atr70_r2"].update(activated_session="2026-11-12", initial_cash="74006.54")
        doc["_preset_configs"]["KMW2"].update(initial_capital=67612.0, session_start_date="2026-04-29")
    else:
        doc["KMW2"].pop("atr70_r2")
        doc["KMW2"]["1_2026-10-30"] = {"round": 1, "shares": 3, "buy_price": 100,
                                        "amount": 300, "mode": "SF"}
    encoded = base64.b64encode(json.dumps(doc).encode()).decode()

    def fake_get(url, **kwargs):
        if url == "https://api.github.com/repos/mwk1134/mos_quant/contents/data/positions_snapshots.json":
            result = Mock(status_code=200)
            result.json.return_value = {"content": encoded, "sha": "synthetic-sha"}
            return result
        raise AssertionError("Only the fake snapshot source is allowed")

    with patch("requests.get", side_effect=fake_get), \
            patch("requests.put", side_effect=AssertionError("No remote writes allowed")), \
            patch("requests.Session.request", side_effect=AssertionError("No broker or market calls allowed")):
        app = AppTest.from_file(str(APP))
        app.secrets["GITHUB_TOKEN"] = "synthetic-test-token"
        app.session_state["authenticated"] = True
        app.session_state["active_preset"] = "KMW2"
        app.session_state["initial_capital"] = 10000.0
        app.session_state["session_start_date"] = "2026-10-01"
        app.session_state["positions_snapshot"] = deepcopy(doc["KMW2"])
        app.session_state["_rsi_reference_next_check_at"] = 99999999999.0
        app.run(timeout=25)
    assert not app.exception
    assert not app.error
    assert next(toggle for toggle in app.toggle if toggle.label == "반반매매 (ATR70-R2) 적용").value
    assert [toggle.label for toggle in app.toggle] == ["반반매매 (ATR70-R2) 적용"]
    assert app.session_state.trader.ma_alignment_switch_enabled is False
    assert next(widget for widget in app.number_input if widget.label == "💰 초기 투자금 (달러)").disabled
    assert next(widget for widget in app.date_input if widget.label == "📅 투자 시작일").disabled
    assert next(widget for widget in app.number_input if widget.label == "💰 초기 투자금 (달러)").value == 74006.54
    assert next(widget for widget in app.date_input if widget.label == "📅 투자 시작일").value.isoformat() == "2026-11-12"
    if running:
        assert any("실계좌 운용 시작일 2026-11-12 · 시작자금 $74,006.54" in caption.value for caption in app.caption)
        assert any(heading.value == "🔀 KMW2 · 반반매매 (ATR70-R2)" for heading in app.subheader)
        assert app.session_state.initial_capital == 10000.0
        assert app.session_state.session_start_date == "2026-10-01"
        assert app.session_state.positions_snapshot["atr70_r2"] == doc["KMW2"]["atr70_r2"]


@pytest.mark.parametrize("metadata", [
    {}, {"activated_session": "2026-11-12", "initial_cash": None},
    {"activated_session": "2026-11-12", "initial_cash": "NaN"},
    {"activated_session": "2026-11-12", "initial_cash": True},
    {"activated_session": "invalid", "initial_cash": "74006.54"},
])
def test_older_or_invalid_mixed_start_metadata_keeps_existing_settings(metadata):
    snapshot = {"atr70_r2": {**mixed_state(), **metadata}}
    before = deepcopy(snapshot)
    scope = functions("_atr70_r2_live_start")
    assert scope["_atr70_r2_live_start"](snapshot) is None
    assert snapshot == before


def preview_tables():
    return {"preset": "KMW2", "status": "ready", "order_date": "2026-11-02",
            "generated_at": "2026-11-02T08:00:00+09:00", "orders": [],
            "strategy_order_tables": {
                "ATR": {"label": "ATR70 주문표", "status": "WAITING_OPEN", "message": "시가·돌파 확인 대기",
                        "orders": [{"side": "BUY", "order_type": "MARKET", "reference_price": None,
                                    "quantity": None, "condition": "정규장 시가 ×1.015 이상 실시간 체결 확인",
                                    "status": "시가·돌파 확인 대기", "display_only": True}]},
                "R2": {"label": "R2 주문표", "status": "READY", "message": "R2 LOC 매수 조건",
                       "orders": [{"side": "BUY", "order_type": "LOC", "reference_price": "100.00",
                                   "quantity": 2, "condition": "전일 확정 종가 기준", "status": "종가 조건 대기",
                                   "display_only": True}]}}}


def test_order_tables_keep_atr_unknown_open_distinct_from_known_r2_loc_terms():
    scope = functions("_atr70_r2_order_tables")
    plan = preview_tables()
    original = deepcopy(plan)
    tables = scope["_atr70_r2_order_tables"](plan, mixed_state(), enabled=True, waiting=False, signal_available=True)
    assert set(tables) == {"ATR", "R2"}
    atr = tables["ATR"]["rows"][0]
    r2 = tables["R2"]["rows"][0]
    assert atr["수량"] == "시가 확정 후 산정" and "$0" not in atr["기준가격"]
    assert "시가 확정 후 산정" in atr["기준가격"] and "1.015" in atr["조건·상태"]
    assert r2["수량"] == 2 and r2["기준가격"] == "$100.00" and r2["유형"] == "LOC"
    assert plan == original


def test_waiting_and_unmet_conditions_still_get_two_explicit_no_order_tables():
    scope = functions("_atr70_r2_order_tables")
    for enabled in (True, False):
        waiting = scope["_atr70_r2_order_tables"](preview_tables(), None, enabled=enabled, waiting=True, signal_available=True)
        assert all(table["rows"][0]["매매"] == "대기" for table in waiting.values())
        assert all("전량 매도 확인 대기" in table["rows"][0]["조건·상태"] for table in waiting.values())
    plan = preview_tables()
    for table in plan["strategy_order_tables"].values():
        table.update(status="FILTER_NOT_MET", message="조건 미충족 · 신규 주문 없음", orders=[])
    tables = scope["_atr70_r2_order_tables"](plan, mixed_state(), enabled=True, waiting=False, signal_available=True)
    assert all("조건 미충족" in table["rows"][0]["조건·상태"] for table in tables.values())
    assert all(table["rows"][0]["기준가격"] == "-" and table["rows"][0]["수량"] == "-" for table in tables.values())


def test_off_hides_only_new_buys_and_keeps_prior_accepted_buy_visible():
    scope = functions("_atr70_r2_order_tables")
    plan = preview_tables()
    existing = {"side": "BUY", "order_type": "LOC", "price": "90", "quantity": 10,
                "filled_quantity": 3, "status": "ACCEPTED", "existing_order": True,
                "broker_order_id": "fake-order", "session_date": "2026-11-02"}
    plan["strategy_order_tables"]["R2"]["orders"].append(existing)
    tables = scope["_atr70_r2_order_tables"](plan, mixed_state(), enabled=False, waiting=False, signal_available=True)
    assert tables["ATR"]["rows"][0]["매매"] == "대기"
    assert len(tables["R2"]["rows"]) == 1
    receipt = tables["R2"]["rows"][0]
    assert receipt["매매"] == "매수" and receipt["기준가격"] == "$90.00"
    assert "체결 3/10주" in receipt["조건·상태"] and "미국 주문일 2026-11-02" in receipt["조건·상태"]
    assert all("기접수 주문은 유지됩니다" in table["message"] for table in tables.values())


def test_network_failure_falls_back_to_each_strategy_receipts_with_explicit_source():
    scope = functions("_atr70_r2_order_tables")
    state = mixed_state()
    state["orders"] = {
        "atr": {"strategy": "ATR", "side": "BUY", "order_type": "MARKET", "price": "110", "quantity": 5,
                "cumulative_quantity": 2, "status": "OPEN", "broker_order_id": "synthetic-atr", "session_date": "2026-11-02"},
        "r2": {"strategy": "R2", "side": "SELL", "order_type": "MOC", "price": "0", "quantity": 10,
               "status": "ACCEPTED", "broker_order_id": "synthetic-r2", "session_date": "2026-11-02"}}
    before = deepcopy(state)
    tables = scope["_atr70_r2_order_tables"](None, state, enabled=False, waiting=False, signal_available=False)
    assert all(table["source"] == "저장된 접수내역" for table in tables.values())
    assert all("최신 신호 확인 불가" in table["message"] for table in tables.values())
    assert tables["ATR"]["rows"][0]["매매"] == "매수"
    assert "예산 기준 $110.00" in tables["ATR"]["rows"][0]["기준가격"]
    assert "체결 2/5주" in tables["ATR"]["rows"][0]["조건·상태"]
    assert "부분체결" in tables["ATR"]["rows"][0]["조건·상태"]
    assert tables["R2"]["rows"][0]["매매"] == "매도"
    assert state == before


def test_moo_is_never_presented_as_a_zero_dollar_sell_price():
    scope = functions("_atr70_r2_order_tables")
    plan = {"orders": [{"strategy": "ATR", "side": "SELL", "order_type": "MOO", "price": "0", "quantity": 4}]}
    tables = scope["_atr70_r2_order_tables"](plan, mixed_state(), enabled=True, waiting=False, signal_available=True)
    assert tables["ATR"]["rows"][0]["기준가격"] == "시가 시장가"


@pytest.mark.parametrize("price,expected", [("110", "시장가 · 예산 기준 $110.00"),
                                            ("0", "시장가 · 접수 가격 없음 · 체결 별도 확인"),
                                            (None, "시장가 · 접수 가격 없음 · 체결 별도 확인")])
def test_accepted_market_order_never_claims_its_opening_price_is_still_unknown(price, expected):
    scope = functions("_atr70_r2_order_tables")
    plan = {"strategy_order_tables": {"ATR": {"orders": [{"strategy": "ATR", "side": "BUY", "order_type": "MARKET",
            "reference_price": None, "price": price, "quantity": 5, "status": "ACCEPTED", "existing_order": True}]}}}
    tables = scope["_atr70_r2_order_tables"](plan, mixed_state(), enabled=False, waiting=False, signal_available=True)
    assert tables["ATR"]["rows"][0]["기준가격"] == expected
    assert "시가 확정 후" not in tables["ATR"]["rows"][0]["기준가격"]


def test_readonly_preview_does_not_mutate_snapshot_cache_or_save_anything():
    doc = document()
    state = Session(_gh_snapshot_all=deepcopy(doc), kmw2_preset=deepcopy(doc["_preset_configs"]["KMW2"]))
    before = deepcopy(state)
    scope = functions("_atr70_r2_readonly_plan", st=SimpleNamespace(session_state=state))
    with patch("kmw_order_plan.build_kmw_plan", return_value=preview_tables()) as builder:
        plan = scope["_atr70_r2_readonly_plan"](doc["KMW2"])
    assert plan["order_date"] == "2026-11-02"
    assert builder.call_args.kwargs == {"preset": "KMW2", "allow_stale_snapshot": True}
    assert state == before and doc == document()


def order_html_boards(app):
    return [element.value for element in app.markdown
            if 'data-order-strategy="ATR"' in element.value or 'data-order-strategy="R2"' in element.value]


def test_streamlit_always_displays_two_separate_current_strategy_order_tables():
    from streamlit.testing.v1 import AppTest
    with patch("kmw_order_plan.build_kmw_plan", return_value=preview_tables()) as builder:
        app = AppTest.from_string(component_script(running=True)).run(timeout=20)
    assert not app.exception
    assert [heading.value for heading in app.subheader if heading.value in {"ATR70 주문표", "R2 주문표"}] == ["ATR70 주문표", "R2 주문표"]
    boards = order_html_boards(app)
    assert len(boards) == 2
    assert 'data-order-strategy="ATR"' in boards[0] and 'MARKET' in boards[0]
    assert 'data-order-strategy="R2"' in boards[1] and 'LOC' in boards[1]
    assert "시가 확정 후 산정" in boards[0] and "미정" in boards[0]
    assert "$100.00" in boards[1]
    assert len(app.dataframe) == 2  # Only the cash/holdings views remain dataframes.
    builder.assert_called_once()
    assert any("미국 주문일 2026-11-02" in caption.value for caption in app.caption)
    assert any("확인된 보유분" in text.value for text in app.markdown)


def test_streamlit_source_failure_still_displays_both_tables_and_warning():
    from streamlit.testing.v1 import AppTest
    with patch("kmw_order_plan.build_kmw_plan", side_effect=ValueError("synthetic data failure")):
        app = AppTest.from_string(component_script(running=True)).run(timeout=20)
    assert not app.exception
    boards = order_html_boards(app)
    assert len(boards) == 2
    assert all("저장된 접수내역" in board for board in boards)
    assert any("최신 신호 확인 불가" in warning.value for warning in app.warning)
    assert not any("synthetic data failure" in warning.value for warning in app.warning)


def test_streamlit_html_keeps_accepted_partial_buy_visible_after_switch_off():
    from streamlit.testing.v1 import AppTest
    plan = preview_tables()
    plan["strategy_order_tables"]["R2"]["orders"].append({
        "strategy": "R2", "side": "BUY", "order_type": "LOC", "price": "90", "quantity": 10,
        "filled_quantity": 3, "status": "OPEN", "existing_order": True,
        "broker_order_id": "synthetic-receipt", "session_date": "2026-11-02"})
    with patch("kmw_order_plan.build_kmw_plan", return_value=plan):
        app = AppTest.from_string(component_script(running=True)).run(timeout=20)
        app.toggle[0].set_value(False).run(timeout=20)
    assert not app.exception
    boards = order_html_boards(app)
    assert len(boards) == 2
    assert 'class="mix-order-badge buy"' not in boards[0]
    assert "저장 주문" in boards[1] and "부분체결" in boards[1] and "체결 3/10주" in boards[1]
    assert "미국 주문일 2026-11-02" in boards[1]
    assert "$90.00" in boards[1] and "$100.00" not in boards[1]
    assert all("기접수 주문은 유지됩니다" in board for board in boards)


@pytest.mark.parametrize("initial_enabled", [False, True])
def test_remote_switch_change_syncs_existing_widget_without_writing_it_back(initial_enabled):
    from streamlit.testing.v1 import AppTest
    with patch("kmw_order_plan.build_kmw_plan", side_effect=ValueError("Data unavailable")):
        app = AppTest.from_string(component_script(
            running=True, enabled=initial_enabled, real_snapshot_loader=True)).run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is initial_enabled
        before = deepcopy(app.session_state._stored)

        # Another browser changes the setting. The real snapshot loader reads
        # it after rendering the switch, matching the full app's execution order.
        remote_enabled = not initial_enabled
        app.session_state._stored["_preset_configs"]["KMW2"][FLAG] = remote_enabled
        app.run(timeout=20)
        assert not app.exception
        assert app.session_state.kmw2_preset[FLAG] is remote_enabled
        app.run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is remote_enabled
        assert app.session_state._stored["_preset_configs"]["KMW2"][FLAG] is remote_enabled
        assert "save_attempts" not in app.session_state
        expected = deepcopy(before)
        expected["_preset_configs"]["KMW2"][FLAG] = remote_enabled
        assert app.session_state._stored == expected

        # An actual user click remains a save request after synchronization.
        app.toggle[0].set_value(initial_enabled).run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is initial_enabled
        assert app.session_state._stored["_preset_configs"]["KMW2"][FLAG] is initial_enabled
        assert app.session_state.save_attempts == app.session_state.save_count == 1
        assert app.session_state._stored["KMW2"]["atr70_r2"] == before["KMW2"]["atr70_r2"]
        app.run(timeout=20)
        assert not app.exception
        assert app.session_state.save_attempts == 1



@pytest.mark.parametrize("initial_enabled", [False, True])
def test_failed_switch_click_restores_selection_without_retrying_on_rerun(initial_enabled):
    from streamlit.testing.v1 import AppTest
    with patch("kmw_order_plan.build_kmw_plan", side_effect=ValueError("Data unavailable")):
        app = AppTest.from_string(component_script(
            running=True, enabled=initial_enabled, real_snapshot_loader=True)).run(timeout=20)
        assert not app.exception
        before = deepcopy(app.session_state._stored)
        app.session_state._save_error = "Synthetic save failure"
        app.toggle[0].set_value(not initial_enabled).run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is initial_enabled
        assert app.session_state.kmw2_preset[FLAG] is initial_enabled
        assert app.session_state._stored == before
        assert app.session_state.save_attempts == 1
        assert "save_count" not in app.session_state
        assert any("설정을 저장하지 못해 기존 선택을 유지합니다" in error.value for error in app.error)
        app.run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is initial_enabled
        assert app.session_state.save_attempts == 1
        assert app.session_state._stored == before

        # Recovery still requires an explicit new click.
        del app.session_state._save_error
        app.toggle[0].set_value(not initial_enabled).run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is not initial_enabled
        assert app.session_state._stored["_preset_configs"]["KMW2"][FLAG] is not initial_enabled
        assert app.session_state.save_attempts == 2
        assert app.session_state.save_count == 1
        assert app.session_state._stored["KMW2"]["atr70_r2"] == before["KMW2"]["atr70_r2"]
