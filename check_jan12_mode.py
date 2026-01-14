"""
1월 12일 주차 모드 판정 확인 스크립트
RSI 값으로 모드 판정이 올바른지 확인
"""
import sys
import io
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Windows에서 UTF-8 출력 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 루트를 경로에 추가
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from soxl_quant_system import SOXLQuantTrader

def check_jan12_mode():
    """1월 12일 주차 모드 판정 확인"""
    print("=" * 80)
    print("🔍 1월 12일 주차 모드 판정 확인")
    print("=" * 80)
    
    # 트레이더 초기화
    trader = SOXLQuantTrader(initial_capital=9000.0)
    
    # RSI 참조 데이터 로드
    print(f"\n📊 RSI 참조 데이터 로드 중...")
    rsi_ref_data = trader.load_rsi_reference_data()
    
    if not rsi_ref_data:
        print(f"❌ RSI 참조 데이터를 로드할 수 없습니다.")
        return
    
    # 1월 12일이 속한 주의 금요일 계산
    jan12_date = datetime(2026, 1, 12)
    days_until_friday = (4 - jan12_date.weekday()) % 7
    if days_until_friday == 0 and jan12_date.weekday() != 4:
        days_until_friday = 7
    jan12_week_friday = jan12_date + timedelta(days=days_until_friday)
    
    print(f"\n📅 날짜 정보:")
    print(f"   - 1월 12일: {jan12_date.strftime('%Y-%m-%d (%A)')}")
    print(f"   - 해당 주 금요일: {jan12_week_friday.strftime('%Y-%m-%d (%A)')}")
    
    # 1주전, 2주전 금요일 계산
    one_week_ago_friday = jan12_week_friday - timedelta(days=7)
    two_weeks_ago_friday = jan12_week_friday - timedelta(days=14)
    
    print(f"\n📊 RSI 조회:")
    print(f"   - 1주전 금요일: {one_week_ago_friday.strftime('%Y-%m-%d')}")
    print(f"   - 2주전 금요일: {two_weeks_ago_friday.strftime('%Y-%m-%d')}")
    
    # RSI 값 조회
    one_week_ago_rsi = trader.get_rsi_from_reference(one_week_ago_friday, rsi_ref_data)
    two_weeks_ago_rsi = trader.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
    
    print(f"\n📈 RSI 값:")
    if one_week_ago_rsi is not None:
        print(f"   - 1주전 RSI: {one_week_ago_rsi:.2f}")
    else:
        print(f"   - 1주전 RSI: ❌ 데이터 없음")
    
    if two_weeks_ago_rsi is not None:
        print(f"   - 2주전 RSI: {two_weeks_ago_rsi:.2f}")
    else:
        print(f"   - 2주전 RSI: ❌ 데이터 없음")
    
    if one_week_ago_rsi is None or two_weeks_ago_rsi is None:
        print(f"\n❌ RSI 데이터가 부족하여 모드 판정을 할 수 없습니다.")
        return
    
    # 사용자가 제공한 RSI 값과 비교
    print(f"\n📋 사용자 제공 RSI 값:")
    print(f"   - 1주전 RSI: 56.74")
    print(f"   - 2주전 RSI: 55.11")
    
    if abs(one_week_ago_rsi - 56.74) > 0.1 or abs(two_weeks_ago_rsi - 55.11) > 0.1:
        print(f"\n⚠️ RSI 값이 사용자가 제공한 값과 다릅니다!")
        print(f"   - 참조 데이터: 1주전={one_week_ago_rsi:.2f}, 2주전={two_weeks_ago_rsi:.2f}")
        print(f"   - 사용자 제공: 1주전=56.74, 2주전=55.11")
        print(f"\n   사용자가 제공한 RSI 값으로 모드 판정을 진행합니다.")
        one_week_ago_rsi = 56.74
        two_weeks_ago_rsi = 55.11
    
    # 모드 판정 조건 확인
    print(f"\n🔍 모드 판정 조건 확인:")
    print(f"   - 1주전 RSI: {one_week_ago_rsi:.2f}")
    print(f"   - 2주전 RSI: {two_weeks_ago_rsi:.2f}")
    
    # 안전모드 조건 확인
    print(f"\n📋 안전모드(SF) 조건:")
    safe_cond1 = two_weeks_ago_rsi > 65 and two_weeks_ago_rsi > one_week_ago_rsi
    safe_cond2 = 40 < two_weeks_ago_rsi < 50 and two_weeks_ago_rsi > one_week_ago_rsi
    safe_cond3 = two_weeks_ago_rsi >= 50 and one_week_ago_rsi < 50
    
    print(f"   조건1: 2주전 RSI > 65 AND 하락")
    print(f"          {two_weeks_ago_rsi:.2f} > 65 AND {two_weeks_ago_rsi:.2f} > {one_week_ago_rsi:.2f}")
    print(f"          = {two_weeks_ago_rsi > 65} AND {two_weeks_ago_rsi > one_week_ago_rsi} = {safe_cond1}")
    
    print(f"   조건2: 40 < 2주전 RSI < 50 AND 하락")
    print(f"          40 < {two_weeks_ago_rsi:.2f} < 50 AND {two_weeks_ago_rsi:.2f} > {one_week_ago_rsi:.2f}")
    print(f"          = {40 < two_weeks_ago_rsi < 50} AND {two_weeks_ago_rsi > one_week_ago_rsi} = {safe_cond2}")
    
    print(f"   조건3: 2주전 RSI >= 50 AND 1주전 RSI < 50")
    print(f"          {two_weeks_ago_rsi:.2f} >= 50 AND {one_week_ago_rsi:.2f} < 50")
    print(f"          = {two_weeks_ago_rsi >= 50} AND {one_week_ago_rsi < 50} = {safe_cond3}")
    
    safe_result = safe_cond1 or safe_cond2 or safe_cond3
    print(f"   → 안전모드 조건 충족: {safe_result}")
    
    # 공세모드 조건 확인
    print(f"\n📋 공세모드(AG) 조건:")
    ag_cond1 = two_weeks_ago_rsi < 50 and two_weeks_ago_rsi < one_week_ago_rsi and one_week_ago_rsi > 50
    ag_cond2 = 50 < two_weeks_ago_rsi < 60 and two_weeks_ago_rsi < one_week_ago_rsi
    ag_cond3 = two_weeks_ago_rsi < 35 and two_weeks_ago_rsi < one_week_ago_rsi
    
    print(f"   조건1: 2주전 RSI < 50 AND 상승 AND 1주전 RSI > 50")
    print(f"          {two_weeks_ago_rsi:.2f} < 50 AND {two_weeks_ago_rsi:.2f} < {one_week_ago_rsi:.2f} AND {one_week_ago_rsi:.2f} > 50")
    print(f"          = {two_weeks_ago_rsi < 50} AND {two_weeks_ago_rsi < one_week_ago_rsi} AND {one_week_ago_rsi > 50} = {ag_cond1}")
    
    print(f"   조건2: 50 < 2주전 RSI < 60 AND 상승")
    print(f"          50 < {two_weeks_ago_rsi:.2f} < 60 AND {two_weeks_ago_rsi:.2f} < {one_week_ago_rsi:.2f}")
    print(f"          = {50 < two_weeks_ago_rsi < 60} AND {two_weeks_ago_rsi < one_week_ago_rsi} = {ag_cond2}")
    
    print(f"   조건3: 2주전 RSI < 35 AND 상승")
    print(f"          {two_weeks_ago_rsi:.2f} < 35 AND {two_weeks_ago_rsi:.2f} < {one_week_ago_rsi:.2f}")
    print(f"          = {two_weeks_ago_rsi < 35} AND {two_weeks_ago_rsi < one_week_ago_rsi} = {ag_cond3}")
    
    ag_result = ag_cond1 or ag_cond2 or ag_cond3
    print(f"   → 공세모드 조건 충족: {ag_result}")
    
    # 모드 판정
    print(f"\n🎯 모드 판정 결과:")
    if safe_result:
        determined_mode = "SF"
        print(f"   → 안전모드(SF)로 판정됨")
    elif ag_result:
        determined_mode = "AG"
        print(f"   → 공세모드(AG)로 판정됨")
    else:
        print(f"   → 조건에 해당하지 않음 (전주 모드 유지)")
        # 전주 모드를 확인해야 함
        print(f"\n   전주 모드를 확인하기 위해 이전 주차의 모드를 계산합니다...")
        
        # 이전 주차의 금요일
        prev_week_friday = one_week_ago_friday
        
        # 재귀적으로 이전 주차 모드 계산
        prev_week_mode, success = trader._calculate_week_mode_recursive_with_reference(
            prev_week_friday, rsi_ref_data, max_depth=20
        )
        
        if success:
            print(f"\n   전주 모드: {prev_week_mode}")
            determined_mode = prev_week_mode
            print(f"   → 전주 모드 유지: {determined_mode}")
        else:
            print(f"\n   ❌ 전주 모드 계산 실패")
            return
    
    print(f"\n✅ 최종 판정 모드: {determined_mode}")
    
    # 사용자 기대값과 비교
    print(f"\n📊 사용자 기대값과 비교:")
    print(f"   - 사용자 기대: 공세모드(AG)")
    print(f"   - 실제 판정: {determined_mode}")
    
    if determined_mode == "AG":
        print(f"   ✅ 올바르게 공세모드로 판정됨!")
    else:
        print(f"   ❌ 잘못된 모드로 판정됨!")
        print(f"\n   문제점:")
        if safe_result:
            print(f"   - 안전모드 조건이 잘못 충족됨")
        elif not ag_result:
            print(f"   - 공세모드 조건이 충족되지 않음")
            print(f"   - 특히 조건2 (50 < 2주전 RSI < 60 AND 상승)가 충족되어야 함")
            print(f"   - 50 < {two_weeks_ago_rsi:.2f} < 60 = {50 < two_weeks_ago_rsi < 60}")
            print(f"   - {two_weeks_ago_rsi:.2f} < {one_week_ago_rsi:.2f} = {two_weeks_ago_rsi < one_week_ago_rsi}")
            print(f"   - 두 조건 모두 True인데 왜 공세모드로 판정되지 않았는지 확인 필요")
    
    print(f"\n" + "=" * 80)
    print(f"확인 완료")
    print(f"=" * 80)

if __name__ == "__main__":
    check_jan12_mode()

