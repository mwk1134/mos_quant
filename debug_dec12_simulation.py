"""
12월 12일 매수가 매도 추천에 나타나지 않는 원인 확인
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# 현재 디렉토리를 경로에 추가
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from soxl_quant_system import SOXLQuantTrader

def debug_dec12_simulation():
    """12월 12일 매수가 매도 추천에 나타나지 않는 원인 확인"""
    print("=" * 80)
    print("12월 12일 매수가 매도 추천에 나타나지 않는 원인 확인")
    print("=" * 80)
    
    # KMW 프리셋 설정
    initial_capital = 9000.0
    session_start_date = "2025-08-27"
    seed_increases = [{"date": "2025-10-21", "amount": 31000.0}]
    
    # 트레이더 초기화
    trader = SOXLQuantTrader(initial_capital)
    
    # 시드증액 추가
    for seed in seed_increases:
        trader.add_seed_increase(seed['date'], seed['amount'], f"시드증액 {seed['date']}")
    
    print(f"\n📋 KMW 프리셋 설정:")
    print(f"   초기자본: ${initial_capital:,.0f}")
    print(f"   시작일: {session_start_date}")
    print(f"   시드증액: {seed_increases}")
    
    # 최신 거래일 확인
    latest_trading_day = trader.get_latest_trading_day()
    print(f"\n📅 최신 거래일: {latest_trading_day.strftime('%Y-%m-%d')}")
    
    # 시뮬레이션 실행
    print(f"\n🔄 시뮬레이션 실행 중...")
    print(f"   시작일: {session_start_date} ~ 종료일: {latest_trading_day.strftime('%Y-%m-%d')}")
    
    sim_result = trader.simulate_from_start_to_today(session_start_date, quiet=False)
    
    if "error" in sim_result:
        print(f"\n❌ 시뮬레이션 실패: {sim_result['error']}")
        return
    
    print(f"\n✅ 시뮬레이션 완료!")
    print(f"   보유 포지션 수: {len(trader.positions)}개")
    
    # 보유 포지션 확인
    if trader.positions:
        print(f"\n📦 보유 포지션:")
        for pos in trader.positions:
            buy_date_str = pos['buy_date'].strftime('%Y-%m-%d') if isinstance(pos['buy_date'], (datetime, pd.Timestamp)) else str(pos['buy_date'])
            print(f"   - {pos['round']}회차: {pos['shares']}주 @ ${pos['buy_price']:.2f} (매수일: {buy_date_str})")
            
            # 12월 12일 포지션 확인
            if buy_date_str == "2025-12-12":
                print(f"      ✅ 12월 12일 매수 포지션 발견!")
    else:
        print(f"\n❌ 보유 포지션이 없습니다!")
    
    # 일일 추천 생성
    print(f"\n📊 일일 추천 생성 중...")
    recommendation = trader.get_daily_recommendation()
    
    if "error" in recommendation:
        print(f"\n❌ 추천 생성 실패: {recommendation['error']}")
        return
    
    print(f"\n✅ 일일 추천 생성 완료!")
    print(f"   날짜: {recommendation['date']}")
    print(f"   기준일: {recommendation.get('basis_date', 'N/A')}")
    print(f"   현재가: ${recommendation['soxl_current_price']:.2f}")
    print(f"   보유 포지션 수: {recommendation['portfolio']['positions_count']}개")
    print(f"   매도 추천 수: {len(recommendation['sell_recommendations'])}건")
    
    # 매도 추천 확인
    if recommendation['sell_recommendations']:
        print(f"\n🔴 매도 추천:")
        for sell_info in recommendation['sell_recommendations']:
            pos = sell_info['position']
            buy_date = pos.get('buy_date')
            if isinstance(buy_date, pd.Timestamp):
                buy_date_str = buy_date.strftime('%Y-%m-%d')
            elif isinstance(buy_date, datetime):
                buy_date_str = buy_date.strftime('%Y-%m-%d')
            else:
                buy_date_str = str(buy_date)
            
            print(f"   - {pos['round']}회차: {pos['shares']}주 @ ${sell_info['sell_price']:.2f} (매수일: {buy_date_str})")
            print(f"     매도 사유: {sell_info['reason']}")
            
            if buy_date_str == "2025-12-12":
                print(f"     ✅ 12월 12일 매수 포지션 매도 추천 발견!")
    else:
        print(f"\n❌ 매도 추천이 없습니다!")
        
        # 보유 포지션이 있는데 매도 추천이 없는 경우
        if trader.positions:
            print(f"\n   보유 포지션이 있는데 매도 추천이 없는 이유 확인:")
            soxl_data = trader.get_stock_data("SOXL", "1mo")
            if soxl_data is not None and len(soxl_data) > 0:
                current_price = soxl_data.iloc[-1]['Close']
                print(f"   현재가: ${current_price:.2f}")
                
                for pos in trader.positions:
                    buy_date = pos.get('buy_date')
                    if isinstance(buy_date, pd.Timestamp):
                        buy_date_str = buy_date.strftime('%Y-%m-%d')
                    elif isinstance(buy_date, datetime):
                        buy_date_str = buy_date.strftime('%Y-%m-%d')
                    else:
                        buy_date_str = str(buy_date)
                    
                    config = trader.sf_config if pos['mode'] == "SF" else trader.ag_config
                    target_sell_price = pos['buy_price'] * (1 + config['sell_threshold'] / 100)
                    
                    # 보유기간 계산
                    hold_days = 0
                    temp_date = buy_date if isinstance(buy_date, datetime) else buy_date.to_pydatetime() if isinstance(buy_date, pd.Timestamp) else datetime.strptime(buy_date_str, '%Y-%m-%d')
                    today_for_hold = latest_trading_day
                    while temp_date < today_for_hold:
                        temp_date += timedelta(days=1)
                        if trader.is_trading_day(temp_date):
                            hold_days += 1
                    
                    print(f"\n   📦 {pos['round']}회차 (매수일: {buy_date_str}):")
                    print(f"      매수가: ${pos['buy_price']:.2f}")
                    print(f"      매도목표가: ${target_sell_price:.2f}")
                    print(f"      현재가: ${current_price:.2f}")
                    print(f"      보유기간: {hold_days}일 (최대: {config['max_hold_days']}일)")
                    print(f"      모드: {pos['mode']}")
                    
                    # 매도 조건 확인
                    if current_price >= target_sell_price:
                        print(f"      ✅ 매도 조건 1 충족: 목표가 도달 (${current_price:.2f} >= ${target_sell_price:.2f})")
                    else:
                        print(f"      ❌ 매도 조건 1 불충족: 목표가 미달 (${current_price:.2f} < ${target_sell_price:.2f})")
                    
                    if hold_days > config['max_hold_days']:
                        print(f"      ✅ 매도 조건 2 충족: 보유기간 초과 ({hold_days}일 > {config['max_hold_days']}일)")
                    else:
                        print(f"      ❌ 매도 조건 2 불충족: 보유기간 미달 ({hold_days}일 <= {config['max_hold_days']}일)")
                    
                    if buy_date_str == "2025-12-12":
                        print(f"      ⚠️ 12월 12일 포지션인데 매도 추천이 없습니다!")
    
    print(f"\n" + "=" * 80)

if __name__ == "__main__":
    debug_dec12_simulation()

