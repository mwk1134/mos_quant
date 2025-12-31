"""
이번 주 모드 입증 스크립트
RSI 값을 계산해서 전주 모드 확인, 조건 충족 없으면 전전주 모드 확인, 계속 역순으로 확인
"""

import json
from datetime import datetime, timedelta

def get_friday_of_week(date):
    """주어진 날짜가 속한 주의 금요일 반환"""
    days_since_friday = (date.weekday() - 4) % 7
    if days_since_friday == 0:
        return date
    friday = date - timedelta(days=days_since_friday)
    return friday

def get_rsi_from_reference(date, rsi_data):
    """
    특정 날짜의 RSI 값을 참조 데이터에서 가져오기
    """
    try:
        if not rsi_data:
            return None
        
        date_str = date.strftime('%Y-%m-%d')
        
        # 모든 연도에서 해당 날짜가 포함되는 주차 찾기
        available_years = [y for y in rsi_data.keys() if y != 'metadata']
        available_years.sort(reverse=True)  # 최신 연도부터 검색
        
        for year in available_years:
            if 'weeks' not in rsi_data[year]:
                continue
                
            weeks = rsi_data[year]['weeks']
            
            # 해당 날짜가 포함되는 주차 찾기
            for week_data in weeks:
                start_date = week_data['start']
                end_date = week_data['end']
                if start_date <= date_str <= end_date:
                    return float(week_data['rsi'])
        
        # 정확한 주차가 없으면 가장 가까운 이전 주차의 RSI 사용
        all_weeks = []
        for year in available_years:
            if 'weeks' not in rsi_data[year]:
                continue
            for week_data in rsi_data[year]['weeks']:
                week_data_copy = week_data.copy()
                week_data_copy['year'] = year
                all_weeks.append(week_data_copy)
        
        # 종료일 기준으로 정렬
        all_weeks.sort(key=lambda x: x['end'])
        
        # 해당 날짜보다 이전의 가장 가까운 주차 찾기
        for week_data in reversed(all_weeks):
            if week_data['end'] < date_str:
                return float(week_data['rsi'])
        
        return None
    except Exception as e:
        print(f"[오류] RSI 조회 실패: {e}")
        return None

def determine_mode(current_rsi, prev_rsi, prev_mode="SF"):
    """
    구글스프레드시트 수식 기반 모드 판단
    Args:
        current_rsi: 1주전 RSI (현재 적용할 RSI)
        prev_rsi: 2주전 RSI (이전 RSI)
        prev_mode: 전주 모드
    Returns:
        str: "SF" (안전모드) 또는 "AG" (공세모드)
    """
    if current_rsi is None or prev_rsi is None:
        raise ValueError(f"RSI 데이터가 없습니다. current_rsi: {current_rsi}, prev_rsi: {prev_rsi}")

    # 안전모드 조건들 (OR로 연결)
    safe_conditions = [
        # RSI > 65 영역에서 하락 (2주전 RSI > 65이고 2주전 > 1주전)
        prev_rsi > 65 and prev_rsi > current_rsi,
        # 40 < RSI < 50에서 하락 (2주전 RSI가 40~50 사이이고 2주전 > 1주전)
        40 < prev_rsi < 50 and prev_rsi > current_rsi,
        # RSI가 50 밑으로 하락 (2주전 >= 50이고 1주전 < 50)
        prev_rsi >= 50 and current_rsi < 50
    ]

    # 공세모드 조건들 (OR로 연결)
    aggressive_conditions = [
        # RSI가 50 위로 상승 (2주전 < 50이고 2주전 < 1주전이고 1주전 > 50)
        prev_rsi < 50 and prev_rsi < current_rsi and current_rsi > 50,
        # 50 < RSI < 60에서 상승 (2주전 RSI가 50~60 사이이고 2주전 < 1주전)
        50 < prev_rsi < 60 and prev_rsi < current_rsi,
        # RSI < 35 영역에서 상승 (2주전 < 35이고 2주전 < 1주전)
        prev_rsi < 35 and prev_rsi < current_rsi
    ]

    # 안전모드 조건 확인
    safe_result = any(safe_conditions)
    if safe_result:
        return "SF"

    # 공세모드 조건 확인
    aggressive_result = any(aggressive_conditions)
    if aggressive_result:
        return "AG"

    # 조건에 없으면 전주 모드 유지
    return prev_mode

def main():
    # 현재 날짜 (2025-12-31로 고정하여 테스트)
    today = datetime(2025, 12, 31)
    print(f"현재 날짜: {today.strftime('%Y-%m-%d')}")
    
    # 이번 주 금요일 계산 (2025-12-29 ~ 2026-01-02 주차의 금요일은 2026-01-02)
    # 12/29(월) ~ 1/2(금) 주차의 금요일은 1/2
    this_week_friday = datetime(2026, 1, 2)
    print(f"이번 주 금요일: {this_week_friday.strftime('%Y-%m-%d')}")
    print()
    
    # RSI 데이터 로드
    with open('data/weekly_rsi_reference.json', 'r', encoding='utf-8') as f:
        rsi_data = json.load(f)
    
    # 주차별 모드 저장
    week_modes = {}
    
    # 이번 주부터 역순으로 모드 확인
    current_friday = this_week_friday
    max_weeks = 10  # 최대 10주 전까지 확인
    
    print("=" * 80)
    print("주차별 모드 확인 (역순)")
    print("=" * 80)
    print()
    
    for week_num in range(max_weeks):
        # 현재 주차의 1주전, 2주전 금요일 계산
        prev_week_friday = current_friday - timedelta(days=7)
        two_weeks_ago_friday = current_friday - timedelta(days=14)
        
        # RSI 값 가져오기
        prev_week_rsi = get_rsi_from_reference(prev_week_friday, rsi_data)
        two_weeks_ago_rsi = get_rsi_from_reference(two_weeks_ago_friday, rsi_data)
        
        # 이전 주차의 모드 확인
        prev_week_friday_str = prev_week_friday.strftime('%Y-%m-%d')
        prev_week_mode = "SF"  # 기본값
        if prev_week_friday_str in week_modes:
            prev_week_mode = week_modes[prev_week_friday_str]
        else:
            # 이전 주차의 모드를 계산하기 위해 전전주 모드 확인
            prev_prev_week_friday = prev_week_friday - timedelta(days=7)
            prev_prev_week_friday_str = prev_prev_week_friday.strftime('%Y-%m-%d')
            
            prev_prev_week_mode = "SF"  # 기본값
            if prev_prev_week_friday_str in week_modes:
                prev_prev_week_mode = week_modes[prev_prev_week_friday_str]
            else:
                # 전전주 모드를 계산하기 위해 전전전주 모드 확인
                prev_prev_prev_week_friday = prev_prev_week_friday - timedelta(days=7)
                prev_prev_prev_week_friday_str = prev_prev_prev_week_friday.strftime('%Y-%m-%d')
                
                prev_prev_prev_week_mode = "SF"  # 기본값
                if prev_prev_prev_week_friday_str in week_modes:
                    prev_prev_prev_week_mode = week_modes[prev_prev_prev_week_friday_str]
                
                # 전전주의 RSI 확인
                prev_prev_prev_prev_week_friday = prev_prev_prev_week_friday - timedelta(days=7)
                
                prev_prev_prev_prev_rsi = get_rsi_from_reference(prev_prev_prev_prev_week_friday, rsi_data)
                prev_prev_prev_rsi = get_rsi_from_reference(prev_prev_prev_week_friday, rsi_data)
                
                if prev_prev_prev_rsi is not None and prev_prev_prev_prev_rsi is not None:
                    prev_prev_week_mode = determine_mode(prev_prev_prev_rsi, prev_prev_prev_prev_rsi, prev_prev_prev_week_mode)
                    week_modes[prev_prev_week_friday_str] = prev_prev_week_mode
                    print(f"  [전전주 모드 계산] {prev_prev_week_friday_str}: {prev_prev_prev_rsi:.2f} vs {prev_prev_prev_prev_rsi:.2f} -> {prev_prev_week_mode} (이전 모드: {prev_prev_prev_week_mode})")
            
            # 이전 주차의 모드 계산
            prev_prev_prev_prev_week_friday = prev_prev_week_friday - timedelta(days=7)
            
            prev_prev_prev_prev_rsi = get_rsi_from_reference(prev_prev_prev_prev_week_friday, rsi_data)
            prev_prev_prev_rsi = get_rsi_from_reference(prev_prev_prev_week_friday, rsi_data)
            
            if prev_week_rsi is not None and two_weeks_ago_rsi is not None:
                prev_week_mode = determine_mode(prev_week_rsi, two_weeks_ago_rsi, prev_prev_week_mode)
                week_modes[prev_week_friday_str] = prev_week_mode
                print(f"  [이전 주차 모드 계산] {prev_week_friday_str}: {prev_week_rsi:.2f} vs {two_weeks_ago_rsi:.2f} -> {prev_week_mode} (이전 모드: {prev_prev_week_mode})")
        
        # 현재 주차의 모드 결정
        if prev_week_rsi is None or two_weeks_ago_rsi is None:
            print(f"[경고] {current_friday.strftime('%Y-%m-%d')} 주차: RSI 데이터 없음 (1주전: {prev_week_rsi}, 2주전: {two_weeks_ago_rsi})")
            break
        
        # 모드 결정
        current_mode = determine_mode(prev_week_rsi, two_weeks_ago_rsi, prev_week_mode)
        week_modes[current_friday.strftime('%Y-%m-%d')] = current_mode
        
        # 조건 확인
        safe_cond1 = two_weeks_ago_rsi > 65 and two_weeks_ago_rsi > prev_week_rsi
        safe_cond2 = 40 < two_weeks_ago_rsi < 50 and two_weeks_ago_rsi > prev_week_rsi
        safe_cond3 = two_weeks_ago_rsi >= 50 and prev_week_rsi < 50
        
        ag_cond1 = two_weeks_ago_rsi < 50 and two_weeks_ago_rsi < prev_week_rsi and prev_week_rsi > 50
        ag_cond2 = 50 < two_weeks_ago_rsi < 60 and two_weeks_ago_rsi < prev_week_rsi
        ag_cond3 = two_weeks_ago_rsi < 35 and two_weeks_ago_rsi < prev_week_rsi
        
        condition_met = safe_cond1 or safe_cond2 or safe_cond3 or ag_cond1 or ag_cond2 or ag_cond3
        
        print(f"[{week_num + 1}주 전] {current_friday.strftime('%Y-%m-%d')} 주차")
        print(f"  1주전 RSI ({prev_week_friday.strftime('%Y-%m-%d')}): {prev_week_rsi:.2f}")
        print(f"  2주전 RSI ({two_weeks_ago_friday.strftime('%Y-%m-%d')}): {two_weeks_ago_rsi:.2f}")
        print(f"  이전 주차 모드: {prev_week_mode}")
        print(f"  안전모드 조건: cond1={safe_cond1}, cond2={safe_cond2}, cond3={safe_cond3}")
        print(f"  공세모드 조건: cond1={ag_cond1}, cond2={ag_cond2}, cond3={ag_cond3}")
        print(f"  조건 충족: {'예' if condition_met else '아니오 (이전 모드 유지)'}")
        print(f"  결정된 모드: {current_mode}")
        print()
        
        # 다음 주차로 이동
        current_friday = prev_week_friday
    
    print("=" * 80)
    print("최종 결과")
    print("=" * 80)
    this_week_mode = week_modes.get(this_week_friday.strftime('%Y-%m-%d'), "N/A")
    print(f"이번 주 ({this_week_friday.strftime('%Y-%m-%d')}) 모드: {this_week_mode}")
    print()
    
    # 이번 주 모드 상세 확인
    if this_week_mode != "N/A":
        prev_week_friday = this_week_friday - timedelta(days=7)
        prev_week_friday_str = prev_week_friday.strftime('%Y-%m-%d')
        two_weeks_ago_friday = this_week_friday - timedelta(days=14)
        two_weeks_ago_friday_str = two_weeks_ago_friday.strftime('%Y-%m-%d')
        
        prev_week_rsi = get_rsi_from_reference(prev_week_friday, rsi_data)
        two_weeks_ago_rsi = get_rsi_from_reference(two_weeks_ago_friday, rsi_data)
        
        prev_week_mode = week_modes.get(prev_week_friday_str, "SF")
        
        print(f"이번 주 모드 결정 근거:")
        print(f"  - 1주전 RSI ({prev_week_friday_str}): {prev_week_rsi:.2f}")
        print(f"  - 2주전 RSI ({two_weeks_ago_friday_str}): {two_weeks_ago_rsi:.2f}")
        print(f"  - 이전 주차 모드 ({prev_week_friday_str}): {prev_week_mode}")
        print(f"  - 결정된 모드: {this_week_mode}")

if __name__ == "__main__":
    main()
