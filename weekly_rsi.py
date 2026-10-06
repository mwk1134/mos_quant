"""The strategy's 14-week simple-average RSI, using completed US sessions."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from us_market_calendar import is_us_equity_trading_day


RSI_CALCULATION_VERSION = "weekly_sma14_completed_v1"
RSI_DESCRIPTION = "QQQ 주간 RSI 참조 데이터 (14주 단순평균 / Cutler RSI)"
try:
    US_EASTERN = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    from dateutil.tz import gettz

    US_EASTERN = gettz("America/New_York")


def eastern_now(as_of=None):
    """Naive timestamps supplied by the trader already represent US Eastern."""
    now = as_of if as_of is not None else datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(US_EASTERN).replace(tzinfo=None)
    return now


def _last_weekly_session_date(friday):
    last_session = friday
    monday = friday - timedelta(days=4)
    while last_session >= monday and not is_us_equity_trading_day(last_session):
        last_session -= timedelta(days=1)
    return last_session if last_session >= monday else None


def latest_completed_week_end(as_of=None, latest_session_date=None):
    """Return the Friday label after the week's final session has closed.

    Friday holidays use Thursday's close. A provider that only has Thursday
    in an ordinary Friday week cannot supply that week's confirmed RSI.
    Early closes are conservatively considered complete at 16:00 Eastern.
    """
    now = eastern_now(as_of)
    closed_day = now.date()
    if now.hour < 16:
        closed_day -= timedelta(days=1)
    while not is_us_equity_trading_day(closed_day):
        closed_day -= timedelta(days=1)
    if latest_session_date is not None:
        closed_day = min(closed_day, pd.Timestamp(latest_session_date).date())

    friday = closed_day + timedelta(days=4 - closed_day.weekday())
    while True:
        last_session = _last_weekly_session_date(friday)
        if last_session is not None and last_session <= closed_day:
            return pd.Timestamp(friday)
        friday -= timedelta(days=7)


def calculate_completed_weekly_rsi(daily_data, window=14, as_of=None):
    """Preserve the spreadsheet's SMA formula; exclude unfinished weeks."""
    if daily_data is None or daily_data.empty:
        return pd.Series(dtype=float)
    close = daily_data["Close"].dropna().copy()
    if close.empty:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex(close.index)
    if index.tz is not None:
        index = index.tz_convert(US_EASTERN).tz_localize(None)
    close.index = index.normalize()
    close = close.sort_index()
    cutoff = latest_completed_week_end(as_of, close.index.max())
    weekly_close = close.resample("W-FRI").last()
    weekly_close = weekly_close.loc[weekly_close.index <= cutoff]
    session_dates = set(close.index.date)
    for label in weekly_close.index:
        if _last_weekly_session_date(label.date()) not in session_dates:
            weekly_close.loc[label] = float("nan")
    delta = weekly_close.diff()
    gain = delta.where(delta > 0, 0).rolling(window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    valid_change = weekly_close.notna() & weekly_close.shift(1).notna()
    if not valid_change.empty:
        # Preserve the spreadsheet's zero seed for the first observed week.
        valid_change.iloc[0] = pd.notna(weekly_close.iloc[0])
    return rsi.where(valid_change.rolling(window).sum() == window)


def merge_rsi_reference(existing_data, weekly_rsi, as_of=None, refresh_all=False):
    """Key rows by end date, keeping canonical rows when legacy years conflict."""
    values = weekly_rsi.dropna()
    if values.empty:
        raise ValueError("완료된 주간 RSI 데이터가 없습니다.")
    latest_end = values.index.max().strftime("%Y-%m-%d")
    rows = {}
    canonical = set()
    for year, year_data in existing_data.items():
        if year == "metadata" or not isinstance(year_data, dict):
            continue
        for row in year_data.get("weeks", []):
            end = row.get("end", "")
            if not end or end > latest_end:
                continue
            if end not in rows or str(year) == end[:4]:
                rows[end] = dict(row)
            if str(year) == end[:4]:
                canonical.add(end)

    recent_dates = set(values.tail(12).index)
    for week_end, value in values.items():
        if week_end.year < 2010:
            continue
        end = week_end.strftime("%Y-%m-%d")
        if refresh_all or week_end in recent_dates or end not in canonical:
            start = week_end - timedelta(days=4)
            rows[end] = {
                "start": start.strftime("%Y-%m-%d"),
                "end": end,
                "week": int(start.isocalendar().week),
                "rsi": round(float(value), 2),
            }

    result = {}
    for end, row in sorted(rows.items()):
        year = end[:4]
        result.setdefault(year, {
            "description": existing_data.get(year, {}).get(
                "description", f"{year}년 주간 RSI 데이터"
            ),
            "weeks": [],
        })["weeks"].append(row)
    result["metadata"] = {
        "last_updated": eastern_now(as_of).strftime("%Y-%m-%d"),
        "last_completed_week_end": latest_end,
        "total_years": len(result),
        "total_weeks": sum(len(data["weeks"]) for data in result.values()),
        "description": RSI_DESCRIPTION,
        "calculation_version": RSI_CALCULATION_VERSION,
    }
    return result
