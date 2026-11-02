"""Exchange-session boundaries; no wall-clock/Korean-date approximations."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
ATR_HISTORY_START = "2000-01-01"


@lru_cache(maxsize=1)
def exchange_calendar():
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS", start=ATR_HISTORY_START, end="2050-12-31")


def as_et(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("timezone-aware now is required")
    return now.astimezone(ET)


def next_entry_session(now):
    tomorrow = as_et(now).date() + timedelta(days=1)
    return exchange_calendar().date_to_session(tomorrow.isoformat(), direction="next").strftime("%Y-%m-%d")


def previous_session(session_date):
    return exchange_calendar().previous_session(session_date).strftime("%Y-%m-%d")


def first_month_session(month):
    datetime.strptime(month, "%Y-%m")
    return exchange_calendar().date_to_session(month + "-01", direction="next").strftime("%Y-%m-%d")


def session_open_utc(session_date):
    return exchange_calendar().session_open(session_date).to_pydatetime().astimezone(timezone.utc)


def validate_completed_history(raw_rows, session_date):
    """Reject gaps/corrupt bars instead of silently bridging them with a TR."""
    calendar = exchange_calendar()
    expected_last = previous_session(session_date)
    normalized = {}
    import math
    for raw in list(raw_rows or []):
        if not isinstance(raw, dict):
            raise ValueError("ATR_INVALID_DAILY_BAR")
        day = str(raw.get("date") or "")
        datetime.strptime(day, "%Y-%m-%d")
        if day >= session_date:
            continue
        if not calendar.is_session(day):
            raise ValueError("ATR_NON_SESSION_BAR")
        if raw.get("is_complete") is False:
            raise ValueError("ATR_INCOMPLETE_DAILY_BAR")
        try:
            o, h, low, c = (float(raw[key]) for key in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("ATR_INVALID_DAILY_BAR") from exc
        if not all(math.isfinite(value) and value > 0 for value in (o, h, low, c)) or low > min(o, c) or h < max(o, c):
            raise ValueError("ATR_INVALID_DAILY_BAR")
        row = {"date": day, "open": o, "high": h, "low": low, "close": c}
        if day in normalized and normalized[day] != row:
            raise ValueError("ATR_CONFLICTING_DAILY_BAR")
        normalized[day] = row
    if not normalized or max(normalized) != expected_last:
        raise ValueError("ATR_STALE_DAILY_BARS")
    dates = sorted(normalized)
    expected = calendar.sessions_in_range(dates[0], expected_last).strftime("%Y-%m-%d").tolist()
    if dates != expected:
        raise ValueError("ATR_MISSING_DAILY_BAR")
    return [normalized[day] for day in dates]
