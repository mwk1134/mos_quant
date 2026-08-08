"""US equity-market trading calendar helpers.

The recurring full-day closures follow the NYSE/Nasdaq holiday calendar.  A
small explicit set covers unscheduled full-market closures that cannot be
derived from a recurring rule.  Early-close sessions remain trading days.
"""

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Iterable, Union


DateLike = Union[date, datetime]


# Unscheduled US equity-market closures relevant to the available price
# history.  Keep these explicit because no calendar rule can predict them.
SPECIAL_FULL_MARKET_CLOSURES = frozenset(
    {
        date(1994, 4, 27),  # President Richard Nixon funeral
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),  # September 11 market closure
        date(2004, 6, 11),  # President Ronald Reagan funeral
        date(2007, 1, 2),   # President Gerald Ford funeral
        date(2012, 10, 29),
        date(2012, 10, 30),  # Hurricane Sandy
        date(2018, 12, 5),  # President George H. W. Bush funeral
        date(2025, 1, 9),   # President Jimmy Carter funeral
    }
)


def _as_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected date or datetime, got {type(value).__name__}")


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (occurrence - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:  # Saturday -> Friday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:  # Sunday -> Monday
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=None)
def us_equity_market_holidays(year: int) -> frozenset[date]:
    """Return full-day NYSE/Nasdaq closures for *year*.

    New Year's Day is the one fixed-date exception: when January 1 falls on a
    Saturday, NYSE does not observe it on the preceding Friday.
    """
    holidays = set()

    new_year = date(year, 1, 1)
    if new_year.weekday() <= 4:
        holidays.add(new_year)
    elif new_year.weekday() == 6:
        holidays.add(new_year + timedelta(days=1))

    # NYSE has observed Martin Luther King Jr. Day since 1998.
    if year >= 1998:
        holidays.add(_nth_weekday(year, 1, 0, 3))

    holidays.update(
        {
            _nth_weekday(year, 2, 0, 3),       # Washington's Birthday
            _easter_sunday(year) - timedelta(days=2),  # Good Friday
            _last_weekday(year, 5, 0),         # Memorial Day
            _observed_fixed_holiday(year, 7, 4),
            _nth_weekday(year, 9, 0, 1),       # Labor Day
            _nth_weekday(year, 11, 3, 4),      # Thanksgiving Day
            _observed_fixed_holiday(year, 12, 25),
        }
    )

    # Juneteenth became a full US equity-market holiday in 2022.
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(year, 6, 19))

    holidays.update(day for day in SPECIAL_FULL_MARKET_CLOSURES if day.year == year)
    return frozenset(holidays)


def is_us_equity_market_holiday(
    value: DateLike,
    extra_holidays: Iterable[Union[DateLike, str]] = (),
) -> bool:
    day = _as_date(value)
    if day in us_equity_market_holidays(day.year):
        return True

    day_text = day.isoformat()
    for extra in extra_holidays:
        if isinstance(extra, str):
            if extra == day_text:
                return True
        elif _as_date(extra) == day:
            return True
    return False


def is_us_equity_trading_day(
    value: DateLike,
    extra_holidays: Iterable[Union[DateLike, str]] = (),
) -> bool:
    day = _as_date(value)
    return day.weekday() < 5 and not is_us_equity_market_holiday(day, extra_holidays)
