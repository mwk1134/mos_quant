"""
12월 12일 모드 판단 디버깅 스크립트
"""
from datetime import datetime, timedelta
from input_quant_system import SOXLQuantTrader

def main():
    print("=" * 60)
    print("12월 12일 모드 판단 디버깅")
    print("=" * 60)
    
    trader = SOXLQuantTrader(initial_capital=40000)
    
    # RSI 참조 데이터 로드
    rsi_ref_data = trader.load_rsi_reference_data()
    if not rsi_ref_data:
        print("❌ RSI 참조 데이터를 로드할 수 없습니다.")
        return
    
    # 12월 12일이 속한 주의 금요일 계산
    dec12 = datetime(2025, 12, 12)
    days_until_friday = (4 - dec12.weekday()) % 7
    if days_until_friday == 0 and dec12.weekday() != 4:
        days_until_friday = 7
    this_week_friday = dec12 + timedelta(days=days_until_friday)
    
    print(f"\n📅 12월 12일: {dec12.strftime('%Y-%m-%d')} ({['월', '화', '수', '목', '금', '토', '일'][dec12.weekday()]}요일)")
    print(f"📅 이번 주 금요일: {this_week_friday.strftime('%Y-%m-%d')}")
    
    # 1주전과 2주전 금요일 계산
    prev_week_friday = this_week_friday - timedelta(days=7)  # 1주전
    two_weeks_ago_friday = this_week_friday - timedelta(days=14)  # 2주전
    
    print(f"📅 1주전 금요일: {prev_week_friday.strftime('%Y-%m-%d')}")
    print(f"📅 2주전 금요일: {two_weeks_ago_friday.strftime('%Y-%m-%d')}")
    
    # RSI 값 가져오기
    prev_week_rsi = trader.get_rsi_from_reference(prev_week_friday, rsi_ref_data)
    two_weeks_ago_rsi = trader.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
    
    print(f"\n📊 RSI 값:")
    print(f"   1주전 RSI: {prev_week_rsi:.2f}")
    print(f"   2주전 RSI: {two_weeks_ago_rsi:.2f}")
    
    # 모드 판단 (이전 모드가 공세모드라고 가정)
    print(f"\n🔍 모드 판단 (이전 모드: AG 공세모드 가정):")
    mode_ag = trader.determine_mode(prev_week_rsi, two_weeks_ago_rsi, "AG")
    print(f"   결과: {mode_ag}")
    
    print(f"\n🔍 모드 판단 (이전 모드: SF 안전모드 가정):")
    mode_sf = trader.determine_mode(prev_week_rsi, two_weeks_ago_rsi, "SF")
    print(f"   결과: {mode_sf}")
    
    # determine_mode 함수의 로직 확인
    print(f"\n📋 determine_mode 로직 확인:")
    
    # 안전모드 조건들
    safe_conditions = [
        two_weeks_ago_rsi > 65 and two_weeks_ago_rsi > prev_week_rsi,
        40 < two_weeks_ago_rsi < 50 and two_weeks_ago_rsi > prev_week_rsi,
        two_weeks_ago_rsi >= 50 and prev_week_rsi < 50
    ]
    
    # 공세모드 조건들
    aggressive_conditions = [
        two_weeks_ago_rsi < 50 and two_weeks_ago_rsi < prev_week_rsi and prev_week_rsi > 50,
        50 < two_weeks_ago_rsi < 60 and two_weeks_ago_rsi < prev_week_rsi,
        two_weeks_ago_rsi < 35 and two_weeks_ago_rsi < prev_week_rsi
    ]
    
    print(f"\n   안전모드 조건:")
    print(f"   1. 2주전 RSI > 65이고 하락: {two_weeks_ago_rsi > 65 and two_weeks_ago_rsi > prev_week_rsi} (2주전: {two_weeks_ago_rsi:.2f} > 1주전: {prev_week_rsi:.2f})")
    print(f"   2. 40 < 2주전 RSI < 50이고 하락: {40 < two_weeks_ago_rsi < 50 and two_weeks_ago_rsi > prev_week_rsi} (2주전: {two_weeks_ago_rsi:.2f} > 1주전: {prev_week_rsi:.2f})")
    print(f"   3. 2주전 RSI >= 50이고 1주전 < 50: {two_weeks_ago_rsi >= 50 and prev_week_rsi < 50} (2주전: {two_weeks_ago_rsi:.2f} >= 50, 1주전: {prev_week_rsi:.2f} < 50)")
    
    print(f"\n   공세모드 조건:")
    print(f"   1. 2주전 < 50이고 상승하고 1주전 > 50: {two_weeks_ago_rsi < 50 and two_weeks_ago_rsi < prev_week_rsi and prev_week_rsi > 50} (2주전: {two_weeks_ago_rsi:.2f} < 1주전: {prev_week_rsi:.2f} < 50, 1주전: {prev_week_rsi:.2f} > 50)")
    print(f"   2. 50 < 2주전 < 60이고 상승: {50 < two_weeks_ago_rsi < 60 and two_weeks_ago_rsi < prev_week_rsi} (2주전: {two_weeks_ago_rsi:.2f}, 1주전: {prev_week_rsi:.2f})")
    print(f"   3. 2주전 < 35이고 상승: {two_weeks_ago_rsi < 35 and two_weeks_ago_rsi < prev_week_rsi} (2주전: {two_weeks_ago_rsi:.2f} < 35, 2주전: {two_weeks_ago_rsi:.2f} < 1주전: {prev_week_rsi:.2f})")
    
    print(f"\n   조건 충족 여부:")
    print(f"   - 안전모드 조건 충족: {any(safe_conditions)}")
    print(f"   - 공세모드 조건 충족: {any(aggressive_conditions)}")
    
    # 백테스팅 시뮬레이션으로 확인
    print(f"\n" + "=" * 60)
    print("백테스팅 시뮬레이션으로 확인:")
    print("=" * 60)
    
    # 12월 1일부터 12월 12일까지 백테스팅
    start_date = "2025-12-01"
    end_date = "2025-12-12"
    
    print(f"\n백테스팅 실행: {start_date} ~ {end_date}")
    result = trader.run_backtest(start_date, end_date)
    
    if "error" in result:
        print(f"❌ 백테스팅 실패: {result['error']}")
        return
    
    # 12월 12일의 기록 찾기
    daily_records = result.get('daily_records', [])
    dec12_record = None
    for record in daily_records:
        if record.get('date') == '2025-12-12':
            dec12_record = record
            break
    
    if dec12_record:
        print(f"\n📊 12월 12일 백테스팅 기록:")
        print(f"   날짜: {dec12_record.get('date')}")
        print(f"   모드: {dec12_record.get('mode')}")
        print(f"   RSI: {dec12_record.get('rsi')}")
        print(f"   매수 실행: {dec12_record.get('buy_executed_price', 0) > 0}")
        if dec12_record.get('buy_executed_price', 0) > 0:
            print(f"   매수 가격: ${dec12_record.get('buy_executed_price', 0):.2f}")
            print(f"   매수 회차: {dec12_record.get('buy_round', 0)}")
    else:
        print(f"\n❌ 12월 12일 기록을 찾을 수 없습니다.")
    
    # 포지션 확인
    print(f"\n📦 현재 포지션:")
    if trader.positions:
        for pos in trader.positions:
            buy_date_str = pos['buy_date'].strftime('%Y-%m-%d') if hasattr(pos['buy_date'], 'strftime') else str(pos['buy_date'])
            if '2025-12-12' in buy_date_str:
                print(f"   회차: {pos['round']}")
                print(f"   매수일: {buy_date_str}")
                print(f"   모드: {pos.get('mode', 'N/A')}")
                print(f"   매수가: ${pos['buy_price']:.2f}")
                print(f"   수량: {pos['shares']}주")

if __name__ == "__main__":
    main()


