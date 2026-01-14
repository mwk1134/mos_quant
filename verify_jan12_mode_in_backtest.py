"""
1월 12일 주차 모드가 백테스트에서 올바르게 적용되는지 확인
"""
import sys
import io
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

def verify_jan12_mode_in_backtest():
    """백테스트에서 1월 12일 주차 모드 확인"""
    print("=" * 80)
    print("🔍 백테스트에서 1월 12일 주차 모드 확인")
    print("=" * 80)
    
    # KMW 프리셋 설정
    initial_capital = 9000.0
    session_start_date = "2025-08-27"
    seed_increases = [{"date": "2025-10-21", "amount": 31000.0}]
    
    print(f"\n📋 KMW 프리셋 설정:")
    print(f"   - 초기 투자금: ${initial_capital:,.0f}")
    print(f"   - 시작일: {session_start_date}")
    print(f"   - 시드증액: {seed_increases}")
    
    # 트레이더 초기화
    trader = SOXLQuantTrader(initial_capital=initial_capital)
    trader.session_start_date = session_start_date
    
    # 시드증액 추가
    for seed in seed_increases:
        trader.add_seed_increase(seed['date'], seed['amount'], f"시드증액 {seed['date']}")
    
    # RSI 참조 데이터 로드
    print(f"\n📊 RSI 참조 데이터 로드 중...")
    rsi_ref_data = trader.load_rsi_reference_data()
    
    # 1월 12일이 속한 주의 금요일 계산
    jan12_date = datetime(2026, 1, 12)
    days_until_friday = (4 - jan12_date.weekday()) % 7
    if days_until_friday == 0 and jan12_date.weekday() != 4:
        days_until_friday = 7
    jan12_week_friday = jan12_date + timedelta(days=days_until_friday)
    
    # 1주전, 2주전 금요일 계산
    one_week_ago_friday = jan12_week_friday - timedelta(days=7)
    two_weeks_ago_friday = jan12_week_friday - timedelta(days=14)
    
    print(f"\n📅 1월 12일 주차 정보:")
    print(f"   - 해당 주 금요일: {jan12_week_friday.strftime('%Y-%m-%d')}")
    print(f"   - 1주전 금요일: {one_week_ago_friday.strftime('%Y-%m-%d')}")
    print(f"   - 2주전 금요일: {two_weeks_ago_friday.strftime('%Y-%m-%d')}")
    
    # RSI 값 확인
    one_week_rsi = trader.get_rsi_from_reference(one_week_ago_friday, rsi_ref_data)
    two_weeks_rsi = trader.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
    
    print(f"\n📈 RSI 값:")
    print(f"   - 1주전 RSI: {one_week_rsi}")
    print(f"   - 2주전 RSI: {two_weeks_rsi}")
    
    if one_week_rsi is None or two_weeks_rsi is None:
        print(f"   ❌ RSI 데이터가 없습니다.")
        return
    
    # 모드 판정 확인
    print(f"\n🔍 모드 판정:")
    is_matched, matched_mode = trader._is_mode_case_matched(one_week_rsi, two_weeks_rsi)
    
    if is_matched:
        print(f"   ✅ 조건 충족: {matched_mode}")
        expected_mode = matched_mode
    else:
        # 전주 모드 확인
        prev_week_friday = one_week_ago_friday
        prev_week_mode, success = trader._calculate_week_mode_recursive_with_reference(
            prev_week_friday, rsi_ref_data, max_depth=20
        )
        if success:
            expected_mode = trader.determine_mode(one_week_rsi, two_weeks_rsi, prev_week_mode)
            print(f"   ✅ 전주 모드 유지: {expected_mode} (전주 모드: {prev_week_mode})")
        else:
            print(f"   ❌ 전주 모드 계산 실패")
            return
    
    print(f"\n✅ 예상 모드: {expected_mode}")
    
    # 백테스트 실행하여 실제 모드 확인
    print(f"\n🔄 백테스트 실행 중...")
    trader.clear_cache()
    
    latest_trading_day = trader.get_latest_trading_day()
    end_date_str = latest_trading_day.strftime('%Y-%m-%d')
    
    # 백테스트 실행 (quiet=False로 상세 로그 확인)
    backtest_result = trader.run_backtest(session_start_date, end_date_str)
    
    if "error" in backtest_result:
        print(f"   ❌ 백테스트 실패: {backtest_result['error']}")
        return
    
    # 1월 12일 기록 찾기
    jan12_record = None
    if "daily_records" in backtest_result:
        for record in backtest_result["daily_records"]:
            if record.get("date") == "2026-01-12":
                jan12_record = record
                break
    
    if jan12_record:
        print(f"\n📋 1월 12일 백테스트 기록:")
        actual_mode = jan12_record.get('mode', 'N/A')
        buy_executed = jan12_record.get('buy_executed_price', 0) > 0
        
        print(f"   - 모드: {actual_mode}")
        print(f"   - 매수 체결: {buy_executed}")
        
        if buy_executed:
            buy_price = jan12_record.get('buy_executed_price', 0)
            print(f"   - 매수가: ${buy_price:.2f}")
            
            # 매도 목표가 계산
            if actual_mode == "AG":
                config = trader.ag_config
                target_sell_price = buy_price * (1 + config["sell_threshold"] / 100)
                print(f"   - 매도 목표가 (공세모드): ${target_sell_price:.2f} (수익률: {config['sell_threshold']}%)")
            elif actual_mode == "SF":
                config = trader.sf_config
                target_sell_price = buy_price * (1 + config["sell_threshold"] / 100)
                print(f"   - 매도 목표가 (안전모드): ${target_sell_price:.2f} (수익률: {config['sell_threshold']}%)")
        
        print(f"\n📊 모드 비교:")
        print(f"   - 예상 모드: {expected_mode}")
        print(f"   - 실제 모드: {actual_mode}")
        
        if actual_mode == expected_mode:
            print(f"   ✅ 모드가 올바르게 적용됨!")
        else:
            print(f"   ❌ 모드가 잘못 적용됨!")
            print(f"   → 백테스트 로직에서 모드 판정이 잘못되었을 가능성이 있습니다.")
    else:
        print(f"   ⚠️ 백테스트 결과에서 1월 12일 기록을 찾을 수 없습니다.")
    
    # 1월 13일 기록도 확인
    jan13_record = None
    if "daily_records" in backtest_result:
        for record in backtest_result["daily_records"]:
            if record.get("date") == "2026-01-13":
                jan13_record = record
                break
    
    if jan13_record:
        print(f"\n📋 1월 13일 백테스트 기록:")
        sell_executed = jan13_record.get('sell_executed_price', 0) > 0
        print(f"   - 매도 체결: {sell_executed}")
        
        if sell_executed:
            sell_price = jan13_record.get('sell_executed_price', 0)
            print(f"   - 매도가: ${sell_price:.2f}")
    
    print(f"\n" + "=" * 80)
    print(f"확인 완료")
    print(f"=" * 80)

if __name__ == "__main__":
    verify_jan12_mode_in_backtest()

