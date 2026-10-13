"""Calendar boundaries use frozen clocks and fake backends, never APIs."""
from datetime import date, datetime, time
from unittest.mock import Mock

import pytest

from kmw_autotrade import ET, KST, OrderBlocked, direct_order_window, next_run
from kmw_engine import KMWEngine, review_id
from us_market_calendar import is_us_equity_trading_day


HOLIDAY_NEXT_SESSIONS_2026 = [
    ("2026-01-01", "2026-01-02"),
    ("2026-01-19", "2026-01-20"),
    ("2026-02-16", "2026-02-17"),
    ("2026-04-03", "2026-04-06"),
    ("2026-05-25", "2026-05-26"),
    ("2026-06-19", "2026-06-22"),
    ("2026-07-03", "2026-07-06"),
    ("2026-09-07", "2026-09-08"),
    ("2026-11-26", "2026-11-27"),
    ("2026-12-25", "2026-12-28"),
]


def kst(day, hour=17, minute=0):
    return datetime.combine(date.fromisoformat(day), time(hour, minute), tzinfo=KST)


def fake_engine(tmp_path, current, *, preset="KMW"):
    backend = Mock()
    backend.preset = preset
    backend.preview.return_value = {"preset": preset, "status": "ready", "orders": [],
                                    "order_date": current.astimezone(ET).date().isoformat()}
    backend.execute.return_value = {"state": "DIRECT_ACCEPTED", "orders": []}
    moment = [current]
    engine = KMWEngine(backend, tmp_path / (preset.lower() + ".json"),
                       clock=lambda: moment[0], preset=preset)
    return engine, backend, moment


@pytest.mark.parametrize("holiday,next_session", HOLIDAY_NEXT_SESSIONS_2026)
def test_configured_next_run_skips_full_us_holidays_at_default_korean_time(holiday, next_session):
    target = next_run(kst(holiday), time(17, 1))
    assert target == kst(next_session, 17, 1)
    assert is_us_equity_trading_day(target.astimezone(ET).date())


@pytest.mark.parametrize("closed_day", ["2026-10-17", "2026-10-18"])
def test_weekend_default_schedule_waits_for_monday_us_session(closed_day):
    assert next_run(kst(closed_day), time(17, 1)) == kst("2026-10-19", 17, 1)


@pytest.mark.parametrize("day,et_hour,offset_hours", [
    ("2026-03-06", 3, -5), ("2026-03-09", 4, -4),
    ("2026-10-30", 4, -4), ("2026-11-02", 3, -5),
])
def test_default_korean_time_remains_stable_across_us_dst(day, et_hour, offset_hours):
    target = next_run(kst(day), time(17, 1))
    assert target == kst(day, 17, 1)
    market = target.astimezone(ET)
    assert market.date().isoformat() == day and (market.hour, market.minute) == (et_hour, 1)
    assert market.utcoffset().total_seconds() == offset_hours * 3600
    direct_order_window(target, day)


def test_schedule_calendar_uses_us_date_even_when_korea_is_already_saturday():
    # A late US Friday is already Saturday in Korea. Calendar classification
    # must use the ET session; execution-window checks remain independent.
    target = next_run(kst("2026-11-27", 23, 59), time(0, 30))
    assert target == kst("2026-11-28", 0, 30)
    assert target.weekday() == 5 and target.astimezone(ET).date() == date(2026, 11, 27)
    with pytest.raises(OrderBlocked, match="pre-open"):
        direct_order_window(target, "2026-11-27")


def test_schedule_skips_a_us_holiday_even_if_korea_is_on_the_next_calendar_day():
    # 00:30 Friday KST is Thursday 10:30 ET (Thanksgiving), so the first
    # calendar-eligible target is 00:30 Saturday KST / Friday ET.
    target = next_run(kst("2026-11-26", 23, 59), time(0, 30))
    assert target == kst("2026-11-28", 0, 30)
    assert is_us_equity_trading_day(target.astimezone(ET).date())


def test_restart_skips_an_attempted_session_then_rechecks_holiday_and_weekend(tmp_path):
    engine, backend, _ = fake_engine(tmp_path, kst("2026-12-24"))
    engine._commit(enabled=True, scheduled_days=["2026-12-24"])
    restored = KMWEngine(backend, engine.state_file, clock=lambda: kst("2026-12-24"))
    assert restored.status()["next_run"] == kst("2026-12-28", 17, 1).isoformat()
    backend.preview.assert_not_called()
    backend.execute.assert_not_called()


@pytest.mark.parametrize("preset", ["KMW", "KMW2", "JEH", "JEH2"])
def test_a_due_run_delayed_until_a_us_holiday_skips_every_backend_call(tmp_path, preset):
    engine, backend, moment = fake_engine(tmp_path, kst("2026-11-25"), preset=preset)
    engine.enable()
    moment[0] = kst("2026-11-26", 17, 1)
    result = engine.tick()
    assert result["state"] == "SKIPPED" and result["error_code"] == "MARKET_CLOSED"
    assert result["orders"] == []
    assert engine.status()["last_run"]["state"] == "SKIPPED"
    assert engine.status()["next_run"] == kst("2026-11-27", 17, 1).isoformat()
    backend.preview.assert_not_called()
    backend.execute.assert_not_called()


@pytest.mark.parametrize("preset", ["KMW", "KMW2", "JEH", "JEH2"])
def test_manual_run_on_a_us_holiday_preserves_future_display_without_dispatch(tmp_path, preset):
    engine, backend, _ = fake_engine(tmp_path, kst("2026-11-26", 17, 1), preset=preset)
    display = {"preset": preset, "status": "ready", "orders": [],
               "basis_date": "2026-11-25", "order_date": "2026-11-27"}
    backend.preview.return_value = display
    engine.preview()
    backend.preview.reset_mock()
    engine.enable()
    result = engine.run_now(review_id(display))
    assert result["state"] == "SKIPPED" and result["error_code"] == "MARKET_CLOSED"
    assert result["orders"] == [] and result["trigger"] == "manual"
    assert engine.status()["plan"] == display
    backend.preview.assert_not_called()
    backend.execute.assert_not_called()


@pytest.mark.parametrize("holiday,next_session", HOLIDAY_NEXT_SESSIONS_2026)
def test_visible_future_recommendation_cannot_be_submitted_on_a_us_holiday(holiday, next_session):
    # The all-day planner intentionally displays the next session on holidays.
    # A ready display plan is not authorization to send that future day's rows.
    with pytest.raises(OrderBlocked, match="trading session"):
        direct_order_window(kst(holiday, 17, 1), next_session)
    with pytest.raises(OrderBlocked, match="trading session"):
        direct_order_window(kst(holiday, 17, 1), holiday)


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_early_close_sessions_remain_calendar_eligible_for_preopen_orders(day):
    assert is_us_equity_trading_day(date.fromisoformat(day))
    target = next_run(kst(day), time(17, 1))
    assert target == kst(day, 17, 1)
    direct_order_window(target, day)


@pytest.mark.parametrize("current,order_date", [
    (kst("2026-10-13", 8, 41), "2026-10-13"),
    (kst("2026-11-03", 8, 41), "2026-11-03"),
])
def test_korean_morning_next_session_display_waits_for_us_calendar_rollover(current, order_date):
    assert current.astimezone(ET).date().isoformat() != order_date
    with pytest.raises(OrderBlocked, match="trading session"):
        direct_order_window(current, order_date)
