"""
1월 12일 주차 RSI 값 수정 및 모드 판정 확인 스크립트
"""
import sys
import io
import json
from pathlib import Path
from datetime import datetime, timedelta

# Windows에서 UTF-8 출력 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 루트를 경로에 추가
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from soxl_quant_system import SOXLQuantTrader

def fix_jan12_rsi():
    """1월 12일 주차 RSI 값 수정"""
    print("=" * 80)
    print("🔧 1월 12일 주차 RSI 값 수정")
    print("=" * 80)
    
    # 트레이더 초기화
    trader = SOXLQuantTrader(initial_capital=9000.0)
    
    # RSI 참조 데이터 파일 경로
    rsi_file_path = trader._resolve_data_path("weekly_rsi_reference.json")
    
    # RSI 참조 데이터 로드
    print(f"\n📊 RSI 참조 데이터 로드 중...")
    with open(rsi_file_path, 'r', encoding='utf-8') as f:
        rsi_ref_data = json.load(f)
    
    # 1월 12일이 속한 주의 금요일 계산
    jan12_date = datetime(2026, 1, 12)
    days_until_friday = (4 - jan12_date.weekday()) % 7
    if days_until_friday == 0 and jan12_date.weekday() != 4:
        days_until_friday = 7
    jan12_week_friday = jan12_date + timedelta(days=days_until_friday)
    
    # 1주전, 2주전 금요일 계산
    one_week_ago_friday = jan12_week_friday - timedelta(days=7)  # 2026-01-09
    two_weeks_ago_friday = jan12_week_friday - timedelta(days=14)  # 2026-01-02
    
    print(f"\n📅 날짜 정보:")
    print(f"   - 1월 12일: {jan12_date.strftime('%Y-%m-%d (%A)')}")
    print(f"   - 해당 주 금요일: {jan12_week_friday.strftime('%Y-%m-%d (%A)')}")
    print(f"   - 1주전 금요일: {one_week_ago_friday.strftime('%Y-%m-%d')}")
    print(f"   - 2주전 금요일: {two_weeks_ago_friday.strftime('%Y-%m-%d')}")
    
    # 수정할 RSI 값
    correct_one_week_rsi = 56.74  # 2026-01-09
    correct_two_weeks_rsi = 55.11  # 2026-01-02
    
    print(f"\n📈 수정할 RSI 값:")
    print(f"   - 1주전 RSI (2026-01-09): {correct_one_week_rsi}")
    print(f"   - 2주전 RSI (2026-01-02): {correct_two_weeks_rsi}")
    
    # 2026년 데이터가 없으면 생성
    if '2026' not in rsi_ref_data:
        print(f"\n📅 2026년 데이터 생성 중...")
        rsi_ref_data['2026'] = {
            "description": "2026년 주간 RSI 데이터",
            "weeks": []
        }
    
    # 각 주차의 start와 end 날짜 계산
    # 2026-01-02 금요일이 속한 주: 2025-12-29 ~ 2026-01-02
    week1_start = datetime(2025, 12, 29)
    week1_end = datetime(2026, 1, 2)
    
    # 2026-01-09 금요일이 속한 주: 2026-01-05 ~ 2026-01-09
    week2_start = datetime(2026, 1, 5)
    week2_end = datetime(2026, 1, 9)
    
    # 2026-01-16 금요일이 속한 주: 2026-01-12 ~ 2026-01-16
    week3_start = datetime(2026, 1, 12)
    week3_end = datetime(2026, 1, 16)
    
    print(f"\n🔧 RSI 값 업데이트 중...")
    
    # 기존 주차 확인 및 업데이트
    weeks_2026 = rsi_ref_data['2026']['weeks']
    updated_weeks = []
    
    # 1주차 (2025-12-29 ~ 2026-01-02)
    week1_found = False
    for week in weeks_2026:
        if week.get('end') == week1_end.strftime('%Y-%m-%d'):
            week['rsi'] = correct_two_weeks_rsi
            print(f"   ✅ 2주전 RSI 업데이트: {week1_end.strftime('%Y-%m-%d')} - {week.get('rsi', 'N/A')} → {correct_two_weeks_rsi}")
            week1_found = True
            updated_weeks.append(week)
            break
    
    if not week1_found:
        # 새 주차 추가
        new_week = {
            "start": week1_start.strftime('%Y-%m-%d'),
            "end": week1_end.strftime('%Y-%m-%d'),
            "week": 1,
            "rsi": correct_two_weeks_rsi
        }
        weeks_2026.append(new_week)
        print(f"   ✅ 2주전 RSI 추가: {week1_end.strftime('%Y-%m-%d')} - {correct_two_weeks_rsi}")
        updated_weeks.append(new_week)
    
    # 2주차 (2026-01-05 ~ 2026-01-09)
    week2_found = False
    for week in weeks_2026:
        if week.get('end') == week2_end.strftime('%Y-%m-%d'):
            week['rsi'] = correct_one_week_rsi
            print(f"   ✅ 1주전 RSI 업데이트: {week2_end.strftime('%Y-%m-%d')} - {week.get('rsi', 'N/A')} → {correct_one_week_rsi}")
            week2_found = True
            updated_weeks.append(week)
            break
    
    if not week2_found:
        # 새 주차 추가
        new_week = {
            "start": week2_start.strftime('%Y-%m-%d'),
            "end": week2_end.strftime('%Y-%m-%d'),
            "week": 2,
            "rsi": correct_one_week_rsi
        }
        weeks_2026.append(new_week)
        print(f"   ✅ 1주전 RSI 추가: {week2_end.strftime('%Y-%m-%d')} - {correct_one_week_rsi}")
        updated_weeks.append(new_week)
    
    # 주차 번호 정렬
    weeks_2026.sort(key=lambda x: x['end'])
    for i, week in enumerate(weeks_2026, 1):
        week['week'] = i
    
    # 파일 저장
    print(f"\n💾 RSI 참조 데이터 저장 중...")
    try:
        with open(rsi_file_path, 'w', encoding='utf-8') as f:
            json.dump(rsi_ref_data, f, indent=2, ensure_ascii=False)
        print(f"   ✅ 저장 완료: {rsi_file_path}")
    except Exception as e:
        print(f"   ❌ 저장 실패: {e}")
        return
    
    # 수정 후 모드 판정 확인
    print(f"\n🔍 수정 후 모드 판정 확인:")
    
    # RSI 값 다시 조회
    trader.load_rsi_reference_data()  # 다시 로드
    new_one_week_rsi = trader.get_rsi_from_reference(one_week_ago_friday, rsi_ref_data)
    new_two_weeks_rsi = trader.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
    
    print(f"   - 1주전 RSI: {new_one_week_rsi}")
    print(f"   - 2주전 RSI: {new_two_weeks_rsi}")
    
    if new_one_week_rsi != correct_one_week_rsi or new_two_weeks_rsi != correct_two_weeks_rsi:
        print(f"   ⚠️ RSI 값이 예상과 다릅니다!")
        return
    
    # 모드 판정
    is_matched, matched_mode = trader._is_mode_case_matched(new_one_week_rsi, new_two_weeks_rsi)
    
    print(f"\n🎯 모드 판정:")
    print(f"   - 1주전 RSI: {new_one_week_rsi:.2f}")
    print(f"   - 2주전 RSI: {new_two_weeks_rsi:.2f}")
    
    if is_matched:
        print(f"   ✅ 조건 충족: {matched_mode}")
        final_mode = matched_mode
    else:
        # 전주 모드 확인
        prev_week_friday = one_week_ago_friday
        prev_week_mode, success = trader._calculate_week_mode_recursive_with_reference(
            prev_week_friday, rsi_ref_data, max_depth=20
        )
        if success:
            final_mode = trader.determine_mode(new_one_week_rsi, new_two_weeks_rsi, prev_week_mode)
            print(f"   ✅ 전주 모드 유지: {final_mode} (전주 모드: {prev_week_mode})")
        else:
            print(f"   ❌ 전주 모드 계산 실패")
            return
    
    print(f"\n✅ 최종 판정 모드: {final_mode}")
    
    if final_mode == "AG":
        print(f"   ✅ 올바르게 공세모드로 판정됨!")
    else:
        print(f"   ❌ 잘못된 모드로 판정됨! (예상: AG, 실제: {final_mode})")
    
    print(f"\n" + "=" * 80)
    print(f"수정 완료")
    print(f"=" * 80)

if __name__ == "__main__":
    fix_jan12_rsi()
