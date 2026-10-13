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


def test_due_ten_minutes_before_schedule_but_not_before_lead_window():
    target = NOW + timedelta(minutes=10)
    assert service.refresh_due(status(next_run=target.isoformat()), preset="JEH", now=NOW) == target.isoformat()
    assert service.refresh_due(status(next_run=(target + timedelta(seconds=1)).isoformat()),
                               preset="JEH", now=NOW) is None


@pytest.mark.parametrize("changes", [
    {"enabled": False}, {"running": True}, {"preset": "KMW"}, {"preset": None},
    {"next_run": None}, {"next_run": "invalid"}, {"next_run": "2026-10-13T17:01:00"},
    {"next_run": (NOW + timedelta(minutes=11)).isoformat()},
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
    records = worker.poll(now=NOW, wait=True)
    assert len(records) == 1
    assert records[0]["preset"] == "JEH"
    assert records[0]["status"] == "READY"
    assert records[0]["basis_date"] == "2026-10-12"
    assert refresh.call_count == 1
    assert refresh.call_args.kwargs["preset"] == "JEH"
    assert refresh.call_args.kwargs["env"] == env
    assert refresh.call_args.kwargs["ledger_path"].name == "orders_jeh.sqlite3"
    assert parser.call_args.args[0].name == ".env.meritz.jeh"
    assert [call.args[0] for call in reader.call_args_list] == ["KMW", "KMW2", "JEH", "JEH2", "KHW"]
    state = tmp_path / "refresh" / "jeh" / "refresh_status.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700
    assert "synthetic-token" not in state.read_text()
    worker.poll(now=NOW + timedelta(seconds=15), wait=True)
    assert refresh.call_count == 1


def test_restart_does_not_repeat_same_persisted_target(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(return_value=({"JEH": {"as_of_date": "2026-10-12"}}, "test"))
    reader = Mock(return_value=status())
    first = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    first.poll(now=NOW, wait=True)
    second = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    assert second.poll(now=NOW, wait=True) == []
    assert refresh.call_count == 1


def test_ambiguous_refresh_records_safe_code_and_never_retries_same_target(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(side_effect=SnapshotSyncBlocked("fixed safe message", code="SNAPSHOT_SAVE_UNCONFIRMED"))
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh,
                                           status_reader=Mock(return_value=status()))
    result = worker.poll(now=NOW, wait=True)[0]
    assert result["status"] == "BLOCKED"
    assert result["code"] == "SNAPSHOT_SAVE_UNCONFIRMED"
    assert worker.poll(now=NOW + timedelta(seconds=15), wait=True) == []
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


def test_slow_account_does_not_block_another_account_or_duplicate_inflight_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", lambda path: {})
    slow_started, release_slow, fast_finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def refreshed(*, preset, **kwargs):
        calls.append(preset)
        if preset == "KMW":
            slow_started.set()
            assert release_slow.wait(timeout=3)
        else:
            fast_finished.set()
        return {preset: {"as_of_date": "2026-10-12"}}, "test"
    worker = service.PresetSnapshotService(presets=("KMW", "KMW2"), state_dir=tmp_path,
        refresh=refreshed, status_reader=lambda preset: status(preset))
    try:
        records = worker.poll(now=NOW)
        assert slow_started.wait(timeout=1)
        assert fast_finished.wait(timeout=1)
        # KMW remains blocked while KMW2 reaches READY independently.
        for thread in list(worker._workers.values()):
            if thread.name.endswith("kmw2"):
                thread.join(timeout=1)
        records += worker.poll(now=NOW + timedelta(seconds=1))
        assert any(record["preset"] == "KMW2" and record["status"] == "READY" for record in records)
        assert calls.count("KMW") == 1 and calls.count("KMW2") == 1
    finally:
        release_slow.set()
        for thread in list(worker._workers.values()):
            thread.join(timeout=1)


def test_definite_read_failure_retries_with_bounded_backoff_and_fresh_facts(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    error = SnapshotSyncBlocked("fixed safe message", code="SNAPSHOT_READ_FAILED")
    refresh = Mock(side_effect=[error, ({"JEH": {"as_of_date": "2026-10-12"}}, "test")])
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh,
        status_reader=Mock(return_value=status(next_run=(NOW + timedelta(minutes=10)).isoformat())))
    first = worker.poll(now=NOW, wait=True)[0]
    assert first["status"] == "BLOCKED" and first["retryable"] is True and first["attempt"] == 1
    assert worker.poll(now=NOW + timedelta(seconds=14), wait=True) == []
    second = worker.poll(now=NOW + timedelta(seconds=16), wait=True)[0]
    assert second["status"] == "READY" and second["attempt"] == 2
    assert refresh.call_count == 2
    assert refresh.call_args_list[1].kwargs["now"] > refresh.call_args_list[0].kwargs["now"]
    assert worker.poll(now=NOW + timedelta(minutes=1), wait=True) == []


def test_retry_limit_is_persisted_and_not_reset_by_service_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(side_effect=SnapshotSyncBlocked("fixed safe message", code="SNAPSHOT_READ_FAILED"))
    reader = Mock(return_value=status(next_run=(NOW + timedelta(minutes=10)).isoformat()))
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    for attempt, elapsed in enumerate((0, 16, 62, 183), start=1):
        result = worker.poll(now=NOW + timedelta(seconds=elapsed), wait=True)[0]
        assert result["attempt"] == attempt
        worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path,
                                               refresh=refresh, status_reader=reader)
    assert worker.poll(now=NOW + timedelta(seconds=400), wait=True) == []
    assert refresh.call_count == service.MAX_REFRESH_ATTEMPTS
    saved = json.loads((tmp_path / "jeh" / "refresh_status.json").read_text())
    assert saved["attempt"] == service.MAX_REFRESH_ATTEMPTS and "retry_after" not in saved


@pytest.mark.parametrize("code", ["SNAPSHOT_SAVE_UNCONFIRMED", "SNAPSHOT_REFRESH_FAILED", "UNKNOWN",
                                  "INVALID_BROKER_FACT", "BUY_LOT_MISMATCH", "SNAPSHOT_AUTH_MISSING"])
def test_ambiguous_unknown_or_invalid_facts_never_retry_even_after_restart(monkeypatch, tmp_path, code):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(side_effect=SnapshotSyncBlocked("fixed safe message", code=code))
    reader = Mock(return_value=status(next_run=(NOW + timedelta(minutes=10)).isoformat()))
    first = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    blocked = first.poll(now=NOW, wait=True)[0]
    assert blocked["status"] == "BLOCKED" and "retry_after" not in blocked
    restored = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    assert restored.poll(now=NOW + timedelta(minutes=2), wait=True) == []
    assert refresh.call_count == 1


def test_crash_during_refreshing_does_not_blindly_retry_an_unconfirmed_write(tmp_path):
    preset_dir = tmp_path / "jeh"
    preset_dir.mkdir()
    (preset_dir / "refresh_status.json").write_text(json.dumps({"preset": "JEH",
        "target": status()["next_run"], "status": "REFRESHING", "attempt": 1}))
    refresh = Mock()
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path,
                                           refresh=refresh, status_reader=Mock(return_value=status()))
    assert worker.poll(now=NOW, wait=True) == []
    refresh.assert_not_called()


def test_corrupt_retry_timestamp_does_not_reset_recorded_attempt(tmp_path):
    preset_dir = tmp_path / "jeh"
    preset_dir.mkdir()
    (preset_dir / "refresh_status.json").write_text(json.dumps({"preset": "JEH",
        "target": status()["next_run"], "status": "BLOCKED", "attempt": 1,
        "code": "SNAPSHOT_READ_FAILED", "retryable": True, "retry_after": "invalid"}))
    refresh = Mock()
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path,
                                           refresh=refresh, status_reader=Mock(return_value=status()))
    assert worker.poll(now=NOW, wait=True) == []
    refresh.assert_not_called()


def test_retry_does_not_start_after_target_or_after_user_disabled_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", Mock(return_value={}))
    refresh = Mock(side_effect=SnapshotSyncBlocked("fixed safe message", code="SNAPSHOT_READ_FAILED"))
    reader = Mock(return_value=status())
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, refresh=refresh, status_reader=reader)
    worker.poll(now=NOW, wait=True)
    reader.return_value = status(enabled=False)
    assert worker.poll(now=NOW + timedelta(seconds=16), wait=True) == []
    reader.return_value = status()
    assert worker.poll(now=NOW + timedelta(minutes=4), wait=True) == []
    assert refresh.call_count == 1


def test_stop_event_ends_service_without_status_or_network_calls(tmp_path):
    stop = threading.Event()
    stop.set()
    reader = Mock()
    worker = service.PresetSnapshotService(presets=("JEH",), state_dir=tmp_path, status_reader=reader)
    worker.serve(stop_event=stop)
    reader.assert_not_called()


@pytest.mark.parametrize("preset,port", [("KMW", 8701), ("KMW2", 8703), ("JEH", 8704), ("JEH2", 8705), ("KHW", 8706)])
def test_status_reader_uses_allowlisted_local_get_and_no_environment_proxy(monkeypatch, preset, port):
    session = Mock()
    response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
    response.json.return_value = status(preset)
    session.get.return_value = response
    monkeypatch.setattr(service.requests, "Session", Mock(return_value=session))
    assert service.read_engine_status(preset) == status(preset)
    assert session.get.call_args.args == (f"http://127.0.0.1:{port}/api/status",)
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["timeout"] == 5
    assert session.trust_env is False
    assert [call[0] for call in session.method_calls] == ["get", "close"]


def test_jeh2_refresh_uses_only_its_env_ledger_and_private_status_path(monkeypatch, tmp_path):
    env = {"MERITZ_ACCOUNT_PRESET": "JEH2", "GITHUB_TOKEN": "synthetic-jeh2-private-token"}
    parser = Mock(return_value=env)
    refresh = Mock(return_value=({"JEH2": {"as_of_date": "2026-10-12"}}, "test"))
    reader = Mock(side_effect=lambda preset: status(preset, enabled=preset == "JEH2"))
    monkeypatch.setattr(service, "read_env", parser)
    worker = service.PresetSnapshotService(state_dir=tmp_path, refresh=refresh, status_reader=reader)
    result = worker.poll(now=NOW, wait=True)
    assert len(result) == 1 and result[0]["preset"] == "JEH2" and result[0]["status"] == "READY"
    assert refresh.call_count == 1 and refresh.call_args.kwargs["preset"] == "JEH2"
    assert refresh.call_args.kwargs["env"] == env
    assert refresh.call_args.kwargs["ledger_path"].name == "orders_jeh2.sqlite3"
    assert parser.call_args.args[0].name == ".env.meritz.jeh2"
    assert [call.args[0] for call in reader.call_args_list] == ["KMW", "KMW2", "JEH", "JEH2", "KHW"]
    recorded = tmp_path / "jeh2" / "refresh_status.json"
    assert stat.S_IMODE(recorded.stat().st_mode) == 0o600
    assert stat.S_IMODE(recorded.parent.stat().st_mode) == 0o700
    assert "synthetic-jeh2-private-token" not in recorded.read_text()
    worker.poll(now=NOW + timedelta(seconds=15), wait=True)
    assert refresh.call_count == 1


@pytest.mark.parametrize("market_date", [(2026, 12, 25), (2026, 10, 17), (2026, 10, 18)])
def test_jeh2_holiday_and_weekend_never_prepare_snapshot(monkeypatch, tmp_path, market_date):
    instant = datetime(*market_date, 16, 58, tzinfo=ZoneInfo("Asia/Seoul"))
    refresh = Mock()
    reader = Mock(return_value=status("JEH2", next_run=(instant + timedelta(minutes=3)).isoformat()))
    worker = service.PresetSnapshotService(presets=("JEH2",), state_dir=tmp_path,
                                           refresh=refresh, status_reader=reader)
    assert worker.poll(now=instant, wait=True) == []
    refresh.assert_not_called()
    assert not (tmp_path / "jeh2").exists()


@pytest.mark.parametrize("marker", [None, "JEH", "KMW", "KMW2"])
def test_jeh2_status_reader_rejects_other_account_or_missing_preset_marker(monkeypatch, marker):
    session = Mock()
    response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
    response.json.return_value = status(marker)
    session.get.return_value = response
    monkeypatch.setattr(service.requests, "Session", Mock(return_value=session))
    with pytest.raises(ValueError, match="cannot be verified"):
        service.read_engine_status("JEH2")
    assert session.get.call_args.args == ("http://127.0.0.1:8705/api/status",)
    session.close.assert_called_once()


def test_slow_jeh_does_not_block_independent_jeh2_preparation(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", lambda path: {})
    slow_started, release_slow, fast_finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def refreshed(*, preset, **kwargs):
        calls.append(preset)
        if preset == "JEH":
            slow_started.set()
            assert release_slow.wait(timeout=3)
        else:
            fast_finished.set()
        return {preset: {"as_of_date": "2026-10-12"}}, "test"

    worker = service.PresetSnapshotService(presets=("JEH", "JEH2"), state_dir=tmp_path,
                                           refresh=refreshed, status_reader=lambda preset: status(preset))
    try:
        records = worker.poll(now=NOW)
        assert slow_started.wait(timeout=1) and fast_finished.wait(timeout=1)
        for thread in list(worker._workers.values()):
            if thread.name.endswith("jeh2"):
                thread.join(timeout=1)
        records += worker.poll(now=NOW + timedelta(seconds=1))
        assert any(record["preset"] == "JEH2" and record["status"] == "READY" for record in records)
        assert calls.count("JEH") == 1 and calls.count("JEH2") == 1
    finally:
        release_slow.set()
        for thread in list(worker._workers.values()):
            thread.join(timeout=1)


def test_one_time_cli_requires_explicit_registered_preset(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(service, "PresetSnapshotService", factory)
    with pytest.raises(SystemExit) as caught:
        service.main(["--once"])
    assert caught.value.code == 2
    factory.assert_not_called()


def test_khw_refresh_uses_only_its_env_ledger_and_private_status_path(monkeypatch, tmp_path):
    env = {"MERITZ_ACCOUNT_PRESET": "KHW", "GITHUB_TOKEN": "synthetic-khw-private-token"}
    parser = Mock(return_value=env)
    refresh = Mock(return_value=({"KHW": {"as_of_date": "2026-10-12"}}, "test"))
    reader = Mock(side_effect=lambda preset: status(preset, enabled=preset == "KHW"))
    monkeypatch.setattr(service, "read_env", parser)
    worker = service.PresetSnapshotService(state_dir=tmp_path, refresh=refresh, status_reader=reader)
    result = worker.poll(now=NOW, wait=True)
    assert len(result) == 1 and result[0]["preset"] == "KHW" and result[0]["status"] == "READY"
    assert refresh.call_count == 1 and refresh.call_args.kwargs["preset"] == "KHW"
    assert refresh.call_args.kwargs["env"] == env
    assert refresh.call_args.kwargs["ledger_path"].name == "orders_khw.sqlite3"
    assert parser.call_args.args[0].name == ".env.meritz.khw"
    assert [call.args[0] for call in reader.call_args_list] == ["KMW", "KMW2", "JEH", "JEH2", "KHW"]
    recorded = tmp_path / "khw" / "refresh_status.json"
    assert stat.S_IMODE(recorded.stat().st_mode) == 0o600
    assert stat.S_IMODE(recorded.parent.stat().st_mode) == 0o700
    assert "synthetic-khw-private-token" not in recorded.read_text()
    worker.poll(now=NOW + timedelta(seconds=15), wait=True)
    assert refresh.call_count == 1


@pytest.mark.parametrize("market_date", [(2026, 12, 25), (2026, 10, 17), (2026, 10, 18)])
def test_khw_holiday_and_weekend_never_prepare_snapshot(monkeypatch, tmp_path, market_date):
    instant = datetime(*market_date, 16, 58, tzinfo=ZoneInfo("Asia/Seoul"))
    refresh = Mock()
    reader = Mock(return_value=status("KHW", next_run=(instant + timedelta(minutes=3)).isoformat()))
    worker = service.PresetSnapshotService(presets=("KHW",), state_dir=tmp_path,
                                           refresh=refresh, status_reader=reader)
    assert worker.poll(now=instant, wait=True) == []
    refresh.assert_not_called()
    assert not (tmp_path / "khw").exists()


@pytest.mark.parametrize("marker", [None, "JEH", "JEH2", "KMW", "KMW2"])
def test_khw_status_reader_rejects_other_account_or_missing_preset_marker(monkeypatch, marker):
    session = Mock()
    response = Mock(status_code=200, content=b"{}", headers={"Content-Type": "application/json"})
    response.json.return_value = status(marker)
    session.get.return_value = response
    monkeypatch.setattr(service.requests, "Session", Mock(return_value=session))
    with pytest.raises(ValueError, match="cannot be verified"):
        service.read_engine_status("KHW")
    assert session.get.call_args.args == ("http://127.0.0.1:8706/api/status",)
    session.close.assert_called_once()


def test_slow_jeh_does_not_block_independent_khw_preparation(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "read_env", lambda path: {})
    slow_started, release_slow, fast_finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def refreshed(*, preset, **kwargs):
        calls.append(preset)
        if preset == "JEH":
            slow_started.set()
            assert release_slow.wait(timeout=3)
        else:
            fast_finished.set()
        return {preset: {"as_of_date": "2026-10-12"}}, "test"

    worker = service.PresetSnapshotService(presets=("JEH", "KHW"), state_dir=tmp_path,
                                           refresh=refreshed, status_reader=lambda preset: status(preset))
    try:
        records = worker.poll(now=NOW)
        assert slow_started.wait(timeout=1) and fast_finished.wait(timeout=1)
        for thread in list(worker._workers.values()):
            if thread.name.endswith("khw"):
                thread.join(timeout=1)
        records += worker.poll(now=NOW + timedelta(seconds=1))
        assert any(record["preset"] == "KHW" and record["status"] == "READY" for record in records)
        assert calls.count("JEH") == 1 and calls.count("KHW") == 1
    finally:
        release_slow.set()
        for thread in list(worker._workers.values()):
            thread.join(timeout=1)
