"""User activation and durable schedule tests with no broker or network."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import threading
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from kmw_engine import EngineStateError, KMWEngine, review_id, validate_time


NOW = datetime(2026, 10, 12, 17, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def plan():
    return {"preset": "KMW", "status": "ready", "order_date": "2026-10-12",
            "basis_date": "2026-10-09", "snapshot_date": "2026-10-09", "snapshot_fresh": True,
            "snapshot_positions": [], "mode": "AG", "buy_budget": 20000,
            "orders": [{"ticker": "SOXL", "side": "SELL", "quantity": q, "price": p,
                        "order_type": "LOC", "round": i, "position_key": str(i)}
                       for i, (q, p) in enumerate(((41, "170.02"), (89, "170.01"), (78, "164.47"),
                                                   (102, "147.51"), (115, "144.65")), 1)] +
                      [{"ticker": "SOXL", "side": "BUY", "quantity": 142, "price": "140.00",
                        "order_type": "LOC", "round": 6, "position_key": "6"}]}


@pytest.fixture
def engine(tmp_path):
    backend = Mock()
    backend.preview.return_value = plan()
    backend.execute.return_value = {"state": "DIRECT_ACCEPTED", "orders": []}
    backend.poll_status.return_value = {"state": "OK", "orders": []}
    moment = [NOW]
    obj = KMWEngine(backend, tmp_path / "engine.json", clock=lambda: moment[0])
    obj.test_moment = moment
    return obj


def test_first_boot_and_read_status_are_inert(engine):
    assert engine.status()["enabled"] is False
    assert engine.status()["next_run"] is None
    engine.tick(NOW + timedelta(hours=2))
    engine.poll_status()
    engine.backend.preview.assert_not_called()
    engine.backend.execute.assert_not_called()
    engine.backend.poll_status.assert_not_called()


def test_preview_cannot_enable_or_submit(engine):
    state = engine.preview()
    assert state["plan"] == plan()
    assert state["review_id"] == review_id(plan())
    assert state["enabled"] is False
    engine.backend.execute.assert_not_called()


def test_user_activation_schedule_and_persistence(engine):
    engine.configure("18:07")
    state = engine.enable()
    assert state["next_run"].startswith("2026-10-12T18:07")
    assert state["enabled"] is True
    restored = KMWEngine(engine.backend, engine.state_file, clock=lambda: NOW)
    assert restored.status()["at"] == "18:07"
    assert restored.status()["enabled"] is True
    engine.backend.execute.assert_not_called()


def test_past_time_activation_waits_until_tomorrow(engine):
    engine.test_moment[0] = NOW + timedelta(minutes=2)
    assert engine.enable()["next_run"].startswith("2026-10-13T17:01")
    engine.tick()
    engine.backend.execute.assert_not_called()


def test_scheduled_session_persists_before_six_row_dispatch_and_cannot_repeat(engine):
    engine.enable()
    expected = deepcopy(plan())
    def execute(received, **kwargs):
        persisted = json.loads(engine.state_file.read_text())
        assert "2026-10-12" in persisted["scheduled_days"]
        assert received == expected
        assert len(received["orders"]) == 6
        assert kwargs["should_continue"]()
        return {"state": "DIRECT_ACCEPTED", "orders": []}
    engine.backend.execute.side_effect = execute
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    engine.tick()
    engine.tick()
    engine.configure("18:01")
    engine.tick(NOW + timedelta(hours=2))
    restored = KMWEngine(engine.backend, engine.state_file, clock=lambda: NOW)
    restored.tick(NOW + timedelta(hours=2))
    assert engine.backend.execute.call_count == 1
    assert restored.status()["next_run"].startswith("2026-10-13")


def test_manual_run_requires_activation_and_review(engine):
    with pytest.raises(EngineStateError, match="추천"):
        engine.run_now()
    with pytest.raises(EngineStateError, match="활성화"):
        engine.run_now(review_id(plan()))
    engine.backend.execute.assert_not_called()


def test_manual_rebuild_must_match_reviewed_order_quantities(engine):
    old = engine.preview()["review_id"]
    engine.enable()
    changed = deepcopy(plan())
    changed["orders"][-1]["quantity"] = 143
    engine.backend.preview.return_value = changed
    result = engine.run_now(old)
    assert result["error_code"] == "PLAN_CHANGED"
    assert engine.status()["review_id"] != old
    engine.backend.execute.assert_not_called()


def test_generation_time_only_does_not_change_review_id():
    a = plan()
    b = deepcopy(a)
    a["generated_at"] = NOW.isoformat()
    b["generated_at"] = (NOW + timedelta(minutes=1)).isoformat()
    assert review_id(a) == review_id(b)


def test_blocked_session_is_not_automatically_retried(engine):
    engine.backend.execute.return_value = {"state": "BLOCKED", "error": "가격 교차", "orders": []}
    engine.enable()
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    engine.tick()
    engine.tick()
    assert engine.status()["phase"] == "BLOCKED"
    assert engine.status()["last_run"]["error"] == "가격 교차"
    assert engine.backend.execute.call_count == 1


def test_unknown_stops_activation_and_survives_restart(engine):
    engine.backend.execute.return_value = {"state": "UNKNOWN", "orders": []}
    engine.enable()
    result = engine.run_now(review_id(plan()))
    assert result["state"] == "UNKNOWN"
    assert engine.status()["enabled"] is False
    restored = KMWEngine(engine.backend, engine.state_file, clock=lambda: NOW)
    assert restored.status()["enabled"] is False


def test_unexpected_error_after_execute_started_is_unknown(engine):
    engine.backend.execute.side_effect = RuntimeError("must-not-publish-secret")
    engine.enable()
    result = engine.run_now(review_id(plan()))
    assert result["state"] == "UNKNOWN"
    assert "must-not-publish-secret" not in json.dumps(engine.status())
    assert engine.status()["enabled"] is False


def test_preview_error_has_no_financial_side_effects_or_raw_errors(engine):
    engine.backend.preview.side_effect = RuntimeError("token-in-url")
    engine.enable()
    result = engine.run_now(review_id(plan()))
    assert result["state"] == "ERROR"
    assert "token-in-url" not in json.dumps(engine.status())
    engine.backend.execute.assert_not_called()


def test_stop_prevents_next_dispatch_even_after_reenable(engine):
    engine.enable()
    entered, release = threading.Event(), threading.Event()
    continue_values = []
    def execute(received, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        continue_values.append(kwargs["should_continue"]())
        return {"state": "STOPPED", "orders": []}
    engine.backend.execute.side_effect = execute
    worker = threading.Thread(target=engine.run_now, args=(review_id(plan()),))
    worker.start()
    assert entered.wait(timeout=2)
    with pytest.raises(EngineStateError, match="進行|진행"):
        engine.run_now(review_id(plan()))
    engine.disable()
    engine.enable()
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert continue_values == [False]


def test_disabled_schedule_and_future_order_status_do_not_make_calls(engine):
    engine.enable()
    engine.disable()
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    engine.tick()
    engine.poll_status()
    engine.backend.execute.assert_not_called()
    engine.backend.poll_status.assert_not_called()


def test_skipped_market_day_never_instantiates_execute(engine):
    engine.backend.preview.return_value = {"status": "skipped", "reason": "US market holiday or weekend", "orders": []}
    engine.enable()
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    assert engine.tick()["state"] == "SKIPPED"
    engine.backend.execute.assert_not_called()


def test_unwritable_state_fails_before_activation_or_orders(engine, monkeypatch):
    def cannot_save(*args, **kwargs):
        raise OSError("denied")
    monkeypatch.setattr("kmw_engine.os.replace", cannot_save)
    with pytest.raises(EngineStateError, match="저장"):
        engine.enable()
    assert engine.status()["enabled"] is False
    engine.backend.execute.assert_not_called()


def test_scheduled_persistence_failure_is_not_mistaken_for_busy(engine, monkeypatch):
    engine.enable()
    def cannot_save(*args, **kwargs):
        raise OSError("denied")
    monkeypatch.setattr("kmw_engine.os.replace", cannot_save)
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    with pytest.raises(EngineStateError, match="저장"):
        engine.tick()
    engine.backend.preview.assert_not_called()
    engine.backend.execute.assert_not_called()


def test_readonly_preview_lock_does_not_drop_the_scheduled_session(engine):
    engine.enable()
    engine.test_moment[0] = NOW + timedelta(minutes=1)
    engine._run_lock.acquire()
    try:
        assert engine.tick() is None
        assert engine.status()["next_run"].startswith("2026-10-12T17:01")
    finally:
        engine._run_lock.release()
    assert engine.tick()["state"] == "DIRECT_ACCEPTED"
    assert engine.backend.execute.call_count == 1


@pytest.mark.parametrize("value", [None, "9:00", "24:00", "17:60", "1701", "17:01 "])
def test_invalid_schedule_fails(value):
    with pytest.raises(EngineStateError):
        validate_time(value)


def test_corrupted_saved_activation_fails_closed(tmp_path):
    path = tmp_path / "engine.json"
    path.write_text('{"version":1,"enabled":"true","at":"17:01"}')
    backend = Mock()
    with pytest.raises(EngineStateError):
        KMWEngine(backend, path)
    backend.execute.assert_not_called()
