"""
Trading Days Calculator
영업일 계산 유틸리티
"""

from datetime import datetime, timedelta
import bisect


US_MARKET_FULL_HOLIDAYS = {
    # NYSE/Nasdaq full-market holidays. SOXL follows the US equity market calendar.
    "2026-01-01": ("New Year's Day", "신정"),
    "2026-01-19": ("Martin Luther King Jr. Day", "마틴 루터 킹 데이"),
    "2026-02-16": ("Washington's Birthday", "대통령의 날"),
    "2026-04-03": ("Good Friday", "성금요일"),
    "2026-05-25": ("Memorial Day", "메모리얼 데이"),
    "2026-06-19": ("Juneteenth National Independence Day", "준틴스"),
    "2026-07-03": ("Independence Day observed", "독립기념일 대체휴일"),
    "2026-09-07": ("Labor Day", "노동절"),
    "2026-11-26": ("Thanksgiving Day", "추수감사절"),
    "2026-12-25": ("Christmas Day", "크리스마스"),
    "2027-01-01": ("New Year's Day", "신정"),
    "2027-01-18": ("Martin Luther King Jr. Day", "마틴 루터 킹 데이"),
    "2027-02-15": ("Washington's Birthday", "대통령의 날"),
    "2027-03-26": ("Good Friday", "성금요일"),
    "2027-05-31": ("Memorial Day", "메모리얼 데이"),
    "2027-06-18": ("Juneteenth National Independence Day observed", "준틴스 대체휴일"),
    "2027-07-05": ("Independence Day observed", "독립기념일 대체휴일"),
    "2027-09-06": ("Labor Day", "노동절"),
    "2027-11-25": ("Thanksgiving Day", "추수감사절"),
    "2027-12-24": ("Christmas Day observed", "크리스마스 대체휴일"),
    "2028-01-17": ("Martin Luther King Jr. Day", "마틴 루터 킹 데이"),
    "2028-02-21": ("Washington's Birthday", "대통령의 날"),
    "2028-04-14": ("Good Friday", "성금요일"),
    "2028-05-29": ("Memorial Day", "메모리얼 데이"),
    "2028-06-19": ("Juneteenth National Independence Day", "준틴스"),
    "2028-07-04": ("Independence Day", "독립기념일"),
    "2028-09-04": ("Labor Day", "노동절"),
    "2028-11-23": ("Thanksgiving Day", "추수감사절"),
    "2028-12-25": ("Christmas Day", "크리스마스"),
}

US_MARKET_EARLY_CLOSES = {
    "2026-07-02": ("Day before Independence Day observed", "독립기념일 전 조기폐장", "13:00"),
    "2026-11-27": ("Day after Thanksgiving", "추수감사절 다음날", "13:00"),
    "2026-12-24": ("Christmas Eve", "크리스마스 이브", "13:00"),
    "2027-11-26": ("Day after Thanksgiving", "추수감사절 다음날", "13:00"),
    "2028-07-03": ("Day before Independence Day", "독립기념일 전 조기폐장", "13:00"),
    "2028-11-24": ("Day after Thanksgiving", "추수감사절 다음날", "13:00"),
}


def get_us_market_calendar_status(date_str=None, now=None):
    """
    미국 주식시장(NYSE/Nasdaq) 거래 가능 상태를 반환한다.
    - date_str가 주어지면 해당 날짜의 캘린더 상태(거래일/휴장/조기폐장일)를 반환한다.
    - date_str 없이 now(datetime)를 주면 ET 현재 시각 기준 세션 상태(개장 전/장중/장마감/주말/휴장)를 반환한다.
    """
    realtime_session_check = False
    if date_str:
        target_dt = datetime.strptime(str(date_str), "%Y-%m-%d")
    elif now is not None:
        target_dt = now if isinstance(now, datetime) else datetime.strptime(str(now), "%Y-%m-%d")
        realtime_session_check = isinstance(now, datetime)
    else:
        target_dt = datetime.now()
        realtime_session_check = True

    iso_date = target_dt.strftime("%Y-%m-%d")
    weekday = int(target_dt.weekday())

    if iso_date in US_MARKET_FULL_HOLIDAYS:
        reason_en, reason_ko = US_MARKET_FULL_HOLIDAYS[iso_date]
        return {
            "date": iso_date,
            "status": "closed",
            "status_label": "휴장",
            "is_trading_day": False,
            "reason": reason_en,
            "reason_ko": reason_ko,
            "close_time_et": None,
            "summary": f"미국장 휴장 · {reason_ko}",
            "timezone": "America/New_York",
        }

    if weekday >= 5:
        next_dt = target_dt + timedelta(days=1)
        while next_dt.weekday() >= 5 or next_dt.strftime("%Y-%m-%d") in US_MARKET_FULL_HOLIDAYS:
            next_dt += timedelta(days=1)
        next_trading_day = next_dt.strftime("%Y-%m-%d")
        return {
            "date": iso_date,
            "status": "closed",
            "status_label": "주말",
            "is_trading_day": False,
            "reason": "Weekend",
            "reason_ko": "주말",
            "close_time_et": None,
            "next_trading_day": next_trading_day,
            "summary": f"미국장 주말 · 다음 거래일 {next_trading_day} ET",
            "timezone": "America/New_York",
        }

    if realtime_session_check:
        open_dt = target_dt.replace(hour=9, minute=30, second=0, microsecond=0)
        close_hour = 16
        close_minute = 0
        close_label = "16:00"
        if iso_date in US_MARKET_EARLY_CLOSES:
            _, _, close_label = US_MARKET_EARLY_CLOSES[iso_date]
            try:
                close_hour, close_minute = [int(x) for x in str(close_label).split(":", 1)]
            except Exception:
                close_hour, close_minute = 13, 0
                close_label = "13:00"
        close_dt = target_dt.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)

        if target_dt < open_dt:
            return {
                "date": iso_date,
                "status": "closed",
                "status_label": "휴장",
                "is_trading_day": True,
                "reason": "Pre-market",
                "reason_ko": "개장 전",
                "close_time_et": close_label,
                "summary": "미국장 개장 전 · 09:30 ET 개장",
                "timezone": "America/New_York",
            }

        if target_dt >= close_dt:
            next_trading_day = get_next_trading_date_on_or_after((target_dt + timedelta(days=1)).strftime("%Y-%m-%d"))
            return {
                "date": iso_date,
                "status": "closed",
                "status_label": "휴장",
                "is_trading_day": True,
                "reason": "Post-market",
                "reason_ko": "장마감",
                "close_time_et": close_label,
                "summary": f"미국장 장마감 · 다음 거래일 {next_trading_day} ET",
                "timezone": "America/New_York",
            }

    if iso_date in US_MARKET_EARLY_CLOSES:
        reason_en, reason_ko, close_time_et = US_MARKET_EARLY_CLOSES[iso_date]
        return {
            "date": iso_date,
            "status": "early_close",
            "status_label": "조기폐장",
            "is_trading_day": True,
            "reason": reason_en,
            "reason_ko": reason_ko,
            "close_time_et": close_time_et,
            "summary": f"미국장 조기폐장 · {close_time_et} ET 종료",
            "timezone": "America/New_York",
        }

    return {
        "date": iso_date,
        "status": "open",
        "status_label": "거래일",
        "is_trading_day": True,
        "reason": "Regular session",
        "reason_ko": "정규장",
        "close_time_et": "16:00",
        "summary": "미국장 거래일 · 정규장",
        "timezone": "America/New_York",
    }


def _count_weekdays_exclusive_start_inclusive_end(start_dt, end_dt):
    """
    (start, end] 구간의 미국장 거래일 수를 계산한다.
    """
    if end_dt <= start_dt:
        return 0
    curr = start_dt
    count = 0
    while curr < end_dt:
        curr += timedelta(days=1)
        if _is_us_market_trading_day(curr):
            count += 1
    return int(count)


def _add_weekdays_exclusive_start(start_dt, n_days):
    """
    start_dt 다음날부터 n_days개의 미국장 거래일을 더한 날짜를 반환한다.
    """
    curr = start_dt
    added = 0
    target = max(int(n_days), 0)
    while added < target:
        curr += timedelta(days=1)
        if _is_us_market_trading_day(curr):
            added += 1
    return curr


def _is_us_market_trading_day(dt):
    iso_date = dt.strftime("%Y-%m-%d")
    return dt.weekday() < 5 and iso_date not in US_MARKET_FULL_HOLIDAYS


def count_trading_days(start_date, end_date, market_data=None, symbol='SOXL'):
    """
    두 날짜 사이의 영업일 수 계산
    """
    if start_date == end_date:
        return 0

    if market_data is not None:
        try:
            # 인덱스를 날짜 문자열로 변환하여 비교 (가장 안전)
            dates = market_data.index.strftime('%Y-%m-%d')
            mask = (dates > start_date) & (dates <= end_date)
            count = int(mask.sum())
            if count > 0:
                return count
        except Exception:
            pass # 실패 시 아래 로직으로

    # 외부 네트워크 의존 없이 평일 기준으로 근사한다.
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    return _count_weekdays_exclusive_start_inclusive_end(start_dt, end_dt)


def get_trading_date_after_n_days(start_date, n_days, market_data=None, symbol='SOXL'):
    """
    시작일로부터 N 영업일 후의 날짜 반환
    """
    if market_data is not None:
        try:
            # 인덱스를 문자열로 변환하여 위치 찾기
            dates = market_data.index.strftime('%Y-%m-%d').tolist()
            if dates:
                # start_date가 거래일이면 그 다음 거래일부터 계산(exclusive)
                # start_date가 휴장일/주말이면 그 다음 거래일부터 계산
                if start_date in dates:
                    base_idx = dates.index(start_date) + 1
                else:
                    base_idx = bisect.bisect_right(dates, start_date)
                target_idx = base_idx + int(n_days) - 1
                if target_idx >= 0 and target_idx < len(dates):
                    return dates[target_idx]
                if int(n_days) > 0 and base_idx < len(dates):
                    # 데이터 범위를 넘어가면 마지막 거래일까지는 실제 데이터, 이후는 평일 근사.
                    available = max(0, len(dates) - base_idx)
                    remaining = max(0, int(n_days) - available)
                    if remaining == 0:
                        return dates[-1]
                    last_known = datetime.strptime(str(dates[-1]), "%Y-%m-%d")
                    return _add_weekdays_exclusive_start(last_known, remaining).strftime("%Y-%m-%d")
        except Exception:
            pass

    # 실시간용 또는 데이터 범위를 벗어난 경우: 외부 호출 없이 평일 근사.
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    return _add_weekdays_exclusive_start(start_dt, int(n_days)).strftime('%Y-%m-%d')


def get_next_trading_date_on_or_after(date_str, market_data=None, symbol='SOXL'):
    """
    주어진 날짜가 거래일이면 그대로, 휴장일/주말이면 다음 거래일 반환.
    """
    try:
        dt = datetime.strptime(str(date_str), "%Y-%m-%d")
    except Exception:
        return str(date_str)

    # 주말/미국장 휴장일은 다음 정규 거래일로 보정한다.
    while not _is_us_market_trading_day(dt):
        dt += timedelta(days=1)
    normalized = dt.strftime("%Y-%m-%d")

    if market_data is not None:
        try:
            dates = market_data.index.strftime('%Y-%m-%d').tolist()
            if not dates:
                return normalized
            idx = bisect.bisect_left(dates, normalized)
            if idx < len(dates):
                return str(dates[idx])
            # 시뮬레이션 데이터가 늦어도 표시일을 과거로 되돌리지 않는다.
            return normalized
        except Exception:
            pass

    return normalized
