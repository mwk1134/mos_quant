"""KMW3 uses its own strategy view and never starts the legacy account trader."""
import ast
import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from banban_strategy import DEFAULT_KMW3_CONFIG


APP = Path(__file__).resolve().parents[1] / "app.py"


def sample_report():
    return {
        "preset": "KMW3", "strategy_id": "atr70_r2_mix", "simulation": True,
        "execution_mode": "simulation", "end_date": "2026-10-20",
        "config": deepcopy(DEFAULT_KMW3_CONFIG),
        "snapshot": {"preset": "KMW3", "strategy_id": "atr70_r2_mix", "simulation": True,
                     "initial_capital": 80000.0, "session_start_date": "2026-10-20",
                     "as_of_date": "2026-10-20", "available_cash": 60000.0},
        "daily": [{"date": "2026-10-20", "total_assets": 80200.0,
                   "available_cash": 60000.0, "atr_asset": 40000.0, "r2_asset": 40200.0}],
        "sleeves": [{"strategy": "ATR70", "cash": 40000.0, "shares": 0, "position_value": 0,
                     "total_assets": 40000.0, "weight_pct": 49.8753},
                    {"strategy": "R2", "cash": 20000.0, "shares": 200, "position_value": 20200.0,
                     "total_assets": 40200.0, "weight_pct": 50.1247}],
        "holdings": [{"sleeve": "R2", "shares": 200, "entry_price": 100.0, "entry_date": "2026-10-20"}],
        "order_preview": {
            "session_date": "2026-10-21",
            "ATR": [{"side": "BUY", "order_type": "MARKET", "quantity": None,
                     "reference_price": None, "condition": "시가 × 1.015 돌파 조건"}],
            "R2": [{"side": "BUY", "order_type": "LOC", "quantity": 10,
                    "reference_price": 101.5, "condition": "종가 조건"}],
        },
        "trades": [],
    }


def test_default_config_survives_app_normalization_and_json_roundtrip():
    tree = ast.parse(APP.read_text())
    wanted = {"_copy_seed_increases", "_normalize_preset_config"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"MA_ALIGNMENT_SWITCH_KEY": "ma_alignment_switch_enabled"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), "exec"), namespace)
    normalized = namespace["_normalize_preset_config"](deepcopy(DEFAULT_KMW3_CONFIG))
    assert json.loads(json.dumps(normalized)) == DEFAULT_KMW3_CONFIG


def test_kmw3_snapshot_uses_bundled_fallback_without_replacing_remote_records():
    class Session(dict):
        __setattr__ = dict.__setitem__

    remote = {"KMW": {"available_cash": 12345.0},
              "_preset_configs": {"KMW": {"initial_capital": 9000.0}}}
    bundled = {"KMW": {"available_cash": 999.0},
               "KMW3": {"preset": "KMW3", "simulation": True, "available_cash": 80000.0,
                        "as_of_date": "2026-10-19"}}
    state = Session(kmw3_preset=deepcopy(DEFAULT_KMW3_CONFIG))
    generic_fallback = Mock(return_value={})
    bundled_fallback = Mock(return_value=deepcopy(bundled))
    tree = ast.parse(APP.read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "load_preset_snapshot")
    namespace = {"st": SimpleNamespace(session_state=state),
                 "MA_ALIGNMENT_SWITCH_KEY": "ma_alignment_switch_enabled",
                 "_gh_load_all_snapshots": Mock(return_value=(deepcopy(remote), "remote-sha")),
                 "_load_snapshot_fallback": generic_fallback,
                 "_load_all_snapshots_fallback": bundled_fallback}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), "exec"), namespace)

    result = namespace["load_preset_snapshot"]("KMW3")

    generic_fallback.assert_called_once_with("KMW3")
    bundled_fallback.assert_called_once_with(prefer_local=True)
    assert result == {**bundled["KMW3"], "ma_alignment_switch_enabled": False}
    assert state["_gh_snapshot_all"] == {**remote, "KMW3": bundled["KMW3"]}
    assert state["_gh_snapshot_sha"] == "remote-sha"


def test_full_app_kmw3_button_loads_requested_settings_and_bypasses_legacy_trader():
    document = {
        "KMW3": {"simulation": True, "available_cash": 80000.0, "as_of_date": "2026-10-19"},
        "KMW2": {"available_cash": 67612.0, "as_of_date": "2026-10-19"},
        "_preset_configs": {"KMW2": {"initial_capital": 67612.0,
                                        "session_start_date": "2026-04-29", "seed_increases": []}},
    }
    encoded = base64.b64encode(json.dumps(document).encode()).decode()

    def fake_get(url, **kwargs):
        assert url == "https://api.github.com/repos/mwk1134/mos_quant/contents/data/positions_snapshots.json"
        response = Mock(status_code=200)
        response.json.return_value = {"content": encoded, "sha": "kmw3-fake-sha"}
        return response

    def render_marker(config):
        import streamlit as st
        st.write("KMW3 전용 반반매매 화면")

    with patch("requests.get", side_effect=fake_get), \
            patch("requests.put", side_effect=AssertionError("No account writes")), \
            patch("requests.Session.request", side_effect=AssertionError("No broker or market calls")), \
            patch("soxl_quant_system.SOXLQuantTrader.__init__", side_effect=AssertionError("No legacy trader")), \
            patch("banban_ui.show_workspace", side_effect=render_marker) as workspace:
        app = AppTest.from_file(str(APP))
        app.secrets["GITHUB_TOKEN"] = "synthetic-kmw3-token"
        app.session_state["authenticated"] = True
        app.session_state["active_preset"] = "KMW3"
        app.session_state["initial_capital"] = 9000.0
        app.session_state["session_start_date"] = "2025-08-27"
        app.session_state["_rsi_reference_next_check_at"] = 99999999999.0
        app.run(timeout=25)
        assert not app.exception and not app.error
        next(button for button in app.button if button.label == "KMW3").click().run(timeout=25)

    assert not app.exception and not app.error
    assert app.session_state.active_preset == "KMW3"
    assert app.session_state.initial_capital == 80000.0
    assert app.session_state.session_start_date == "2026-10-20"
    assert app.session_state.seed_increases == []
    assert app.session_state.trader is None
    assert app.session_state.kmw3_preset == DEFAULT_KMW3_CONFIG
    assert workspace.call_args.args == (DEFAULT_KMW3_CONFIG,)
    assert not app.toggle
    assert next(widget for widget in app.number_input if widget.label == "💰 초기 투자금 (달러)").disabled
    assert next(widget for widget in app.date_input if widget.label == "📅 투자 시작일").disabled


def test_workspace_displays_separate_order_tables_and_simulation_basis():
    script = """
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
show_workspace(DEFAULT_KMW3_CONFIG)
"""
    report = sample_report()
    with patch("banban_ui.load_replay", return_value=report), \
            patch("requests.Session.request", side_effect=AssertionError("No network")), \
            patch("requests.put", side_effect=AssertionError("No writes")):
        app = AppTest.from_string(script).run(timeout=20)

    assert not app.exception and not app.error
    headings = [heading.value for heading in app.subheader]
    assert "🔀 KMW3 · 반반매매 (ATR70-R2)" in headings
    assert "ATR70 주문표" in headings and "R2 주문표" in headings
    assert [metric.value for metric in app.metric] == ["$80,200.00", "$60,000.00", "0.25%"]
    assert any("모의 운용" in caption.value for caption in app.caption)
    assert any("2026-10-21" in caption.value for caption in app.caption)
    assert any("$40,000" in markdown.value for markdown in app.markdown)
    assert app.session_state.positions_snapshot == report["snapshot"]


def test_workspace_market_failure_shows_saved_simulation_with_its_date():
    report = sample_report()
    stored = {**report["snapshot"], "banban_report": {key: value for key, value in report.items() if key != "snapshot"}}
    script = f"""
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
st.session_state.positions_snapshot = {stored!r}
show_workspace(DEFAULT_KMW3_CONFIG)
"""
    with patch("banban_ui.load_replay", side_effect=ValueError("offline")):
        app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception and not app.error
    assert any("2026-10-20" in warning.value and "저장된 모의 결과" in warning.value for warning in app.warning)
    assert any(metric.value == "$80,200.00" for metric in app.metric)
    headings = [heading.value for heading in app.subheader]
    assert "ATR70 주문표" not in headings and "R2 주문표" not in headings
    assert all("유형" not in frame.value.columns for frame in app.dataframe)
    assert all(10 not in frame.value["수량"].tolist() for frame in app.dataframe if "수량" in frame.value)


@pytest.mark.parametrize("saved_capital", [10000.0, "invalid"])
def test_workspace_failure_rejects_saved_result_with_different_or_invalid_config(saved_capital):
    report = sample_report()
    report["config"]["initial_capital"] = saved_capital
    stored = {**report["snapshot"], "banban_report": {key: value for key, value in report.items() if key != "snapshot"}}
    script = f"""
import streamlit as st
from banban_ui import show_workspace
from banban_strategy import DEFAULT_KMW3_CONFIG
st.session_state.positions_snapshot = {stored!r}
show_workspace(DEFAULT_KMW3_CONFIG)
"""
    with patch("banban_ui.load_replay", side_effect=ValueError("offline")):
        app = AppTest.from_string(script).run(timeout=20)
    assert not app.exception and not app.error
    assert not app.metric
    assert any("확정 시세를 불러오지 못했습니다" in warning.value for warning in app.warning)
