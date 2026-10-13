from datetime import datetime, timedelta
import json
import stat
import threading
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import preset_snapshot_service as service
from preset_snapshot_sync import SnapshotSyncBlocked

NOW = datetime(2026, 10, 13, 16, 58, tzinfo=ZoneInfo("Asia/Seoul"))


def status(preset="JEH", **changes):
    return {"preset": preset, "enabled": True, "running": False,
            "next_run": (NOW + timedelta(minutes=3)).isoformat(), **changes}


def test_due_three_minutes_before_preopen_user_schedule():
    target = service.refresh_due(status(), preset="JEH", now=NOW)
    assert target == "2026-10-13T17:01:00+09:00"


@pytest.mark.parametrize("changes", [
    {"enabled": False}, {"running": True}, {"preset": "KMW"}, {"preset": None},
    {"next_run": None}, {"next_run": "invalid"}, {"next_run": "2026-10-13T17:01:00"},
    {"next_run": (NOW + timedelta(minutes=4)).isoformat()},
    {"next_run": (NOW - timedelta(seconds=1)).isoformat()},
])
def test_inactive_running_wrong_account_or_invalid_schedule_does_not_trigger(changes):
    assert service.refresh_due(status(**changes), preset="JEH", now=NOW) is None


def test_holiday_and_regular_session_order_times_never_trigger():
    for instant in [datetime(2026, 12, 25, 16, 58, tzinfo=ZoneInfo("Asia/Seoul")),
                    datetime(2026, 10, 13, 22, 28, tzinfo=ZoneInfo("Asia/Seoul"))]:
        value = status(next_run=(instant + timedelta(minutes=3)).isoformat())
        assert service.refresh_due(value, preset="JEH", now=instant) is None


def test_legacy_kmw_state_requires_matching_plan_marker():
    value = status("KMW", plan={"preset": "KMW"})
    value.pop("preset")
    assert service.refresh_due(value, preset="KMW", now=NOW) is not None
    value["plan"]["preset"] = "KMW2"
    assert service.refresh_due(value, preset="KMW", now=NOW) is None


def test_poll_only_refreshes_due_enabled_account_and_never_calls_engine_actions(monkeypatch, tmp_path):
    refresh = Mock(return_value=({"JEH": {"as_of_date": "2026-10-12"}}, "test"))
    reader = Mock(side_effect=lambda preset: status(preset, enabled=preset == "JEH"))
    env = {"MERITZ_ACCOUNT_PRESET": "JEH", "GITHUB_TOKEN": "synthetic-token"}
    parser = Mock(return_value=env)
    monkeypatch.setattr(service, "read_env", parser)
    worker = service.PresetSnapshotService(state_dir=tmp_path / "refresh", refresh=refresh, status_reader=reader)
    records = worker.poll(now=NOW)
    assert len(records) == 1
    assert records[0]["preset"] == "JEH"
    assert records[0]["status"] == "READY"
    assert records[0]["basis_date"] == "2026-10-12"
    assert refresh.call_count == 1
    assert refresh.call_args.kwargs["preset"] == "JEH"
    assert refresh.call_args.kwargs["env"] == env
    assert refresh.call_args.kwargs["ledger_path"].name == "orders_jeh.sqlite3"
    assert parser.call_args.args[0].name == ".env.meritz.jeh"
    assert [call.args[0] for call in reader.call_args_list] == ["KMW", "KMW2", "JEH"]
    state = tmp_path / "refresh" / "jeh" / "refresh_status.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700
    assert "synthetic-token" not in state.read_text()
    worker.poll(now=NOW + timedelta(seconds=15))
    assert refresh.call_count == 1


def test_restart_does_not_repeat_same_persisted_target(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(return_value=({"JEH": {"as_of_date": "2026-10-12"}}, "test"))
    reader = Mock(return_value=status())
    first = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    first.poll(now=NOW)
    second = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    assert second.poll(now=NOW) == []
    assert refresh.call_count == 1


def test_ambiguous_refresh_records_safe_code_and_never_retries_same_target(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(side_effect=SnapshotSyncBlocked("fixed safe message", code="SNAPSHOT_SAVE_UNCONFIRMED"))
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh,
                                           status_reader=Mock(return_value=status()))
    result = worker.poll(now=NOW)[0]
    assert result["status"] == "BLOCKED"
    assert result["code"] == "SNAPSHOT_SAVE_UNCONFIRMED"
    assert worker.poll(now=NOW + timedelta(seconds=15)) == []
    assert refresh.call_count == 1


def test_exception_message_name_and_code_cannot_leak_to_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    sentinel = "synthetic-private-exception-token"
    error = RuntimeError(sentinel)
    error.code = sentinel
    refresh = Mock(side_effect=error)
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh)
    result = worker.refresh_once("JEH", now=NOW)
    assert result["code"] == "SNAPSHOT_REFRESH_FAILED"
    assert sentinel not in json.dumps(result)
    assert sentinel not in (tmp_path / "jeh" / "refresh_status.json").read_text()
    assert sentinel not in "".join(capsys.readouterr())


def test_engine_disconnect_does_not_refresh_or_change_engine_state(tmp_path):
    refresh = Mock()
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh,
        status_reader=Mock(side_effect=RuntimeError("synthetic-private-disconnect")))
    assert worker.poll(now=NOW) == []
    refresh.assert_not_called()
    assert not (tmp_path / "jeh" / "refresh_status.json").exists()


def test_stop_event_ends_service_without_status_or_network_calls(tmp_path):
    stop = threading.Event()
    stop.set()
    reader = Mock()
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, status_reader=reader)
    worker.serve(stop_event=stop)
    reader.assert_not_called()


def test_status_reader_uses_allowlisted_local_get_and_no_environment_proxy(monkeypatch):
    session = Mock()
    response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
    response.json.return_value = status()
    session.get.return_value = response
    monkeypatch.setattr(service.requests, "Session", Mock(return_value=session))
    assert service.read_engine_status("JEH") == status()
    assert session.get.call_args.args == ("http://127.0.0.1:8704/api/status",)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["timeout"] == 5
    assert session.trust_env is False
    assert [call[0] for call in session.method_calls] == ["get", "close"]


def test_one_time_cli_requires_explicit_registered_preset(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(service, "PresetSnapshotService", factory)
    with pytest.raises(SystemExit) as caught:
        service.main(["--once"])
    assert caught.value.code == 2
    factory.assert_not_called()
