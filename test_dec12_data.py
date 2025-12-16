"""12월 12일 SOXL 데이터 가져오기 테스트"""
from soxl_quant_system import SOXLQuantTrader
from datetime import datetime
import pandas as pd

# 트레이더 초기화
trader = SOXLQuantTrader(initial_capital=2793.0)
trader.session_start_date = "2025-10-30"

print("=" * 60)
print("12월 12일 SOXL 데이터 확인 테스트")
print("=" * 60)

# SOXL 데이터 가져오기 (get_daily_recommendation과 동일한 방식)
print("\n1. get_daily_recommendation()에서 사용하는 방식으로 데이터 가져오기")
soxl_data = trader.get_stock_data("SOXL", "1mo")
if soxl_data is None:
    print("❌ SOXL 데이터를 가져올 수 없습니다.")
else:
    print(f"✅ SOXL 데이터 가져오기 성공: {len(soxl_data)}개 일봉")
    print(f"   데이터 범위: {soxl_data.index[0].strftime('%Y-%m-%d')} ~ {soxl_data.index[-1].strftime('%Y-%m-%d')}")
    
    # 12월 12일 데이터 확인
    target_date_str = "2025-12-12"
    target_date = pd.to_datetime(target_date_str)
    
    print(f"\n2. {target_date_str} 데이터 확인")
    
    # 원본 데이터에서 확인
    if target_date in soxl_data.index:
        dec12_data = soxl_data.loc[target_date]
        print(f"✅ {target_date_str} 데이터 발견!")
        print(f"   종가: ${dec12_data['Close']:.2f}")
        print(f"   시가: ${dec12_data['Open']:.2f}")
        print(f"   고가: ${dec12_data['High']:.2f}")
        print(f"   저가: ${dec12_data['Low']:.2f}")
    else:
        # 날짜 문자열로 검색
        found = False
        for idx in soxl_data.index:
            if idx.strftime('%Y-%m-%d') == target_date_str:
                dec12_data = soxl_data.loc[idx]
                print(f"✅ {target_date_str} 데이터 발견! (문자열 매칭)")
                print(f"   인덱스: {idx}")
                print(f"   종가: ${dec12_data['Close']:.2f}")
                print(f"   시가: ${dec12_data['Open']:.2f}")
                print(f"   고가: ${dec12_data['High']:.2f}")
                print(f"   저가: ${dec12_data['Low']:.2f}")
                found = True
                break
        
        if not found:
            print(f"❌ {target_date_str} 데이터를 찾을 수 없습니다.")
            print(f"\n   최근 10개 거래일:")
            for date in soxl_data.index[-10:]:
                print(f"   - {date.strftime('%Y-%m-%d')}")
    
    # get_daily_recommendation에서 필터링하는 방식 확인
    print(f"\n3. get_daily_recommendation() 필터링 로직 확인")
    today = trader.get_today_date()
    today_date = today.date()
    print(f"   오늘 날짜: {today_date}")
    print(f"   정규장 마감 여부: {trader.is_regular_session_closed_now()}")
    
    # 필터링 로직 적용
    filtered_data = soxl_data.copy()
    if not trader.is_regular_session_closed_now() and len(filtered_data) > 0:
        if filtered_data.index.max().date() == today_date:
            print(f"   ⚠️ 필터링 적용: 오늘 날짜 데이터 제외")
            filtered_data = filtered_data[filtered_data.index.date < today_date]
        else:
            print(f"   ✅ 필터링 불필요: 최신 데이터 날짜가 오늘이 아님")
    
    # 필터링 후 12월 12일 데이터 확인
    if target_date in filtered_data.index:
        print(f"   ✅ 필터링 후에도 {target_date_str} 데이터 존재")
    else:
        found_filtered = False
        for idx in filtered_data.index:
            if idx.strftime('%Y-%m-%d') == target_date_str:
                print(f"   ✅ 필터링 후에도 {target_date_str} 데이터 존재 (문자열 매칭)")
                found_filtered = True
                break
        
        if not found_filtered:
            print(f"   ❌ 필터링 후 {target_date_str} 데이터 없음")
            print(f"   필터링 후 데이터 범위: {filtered_data.index[0].strftime('%Y-%m-%d')} ~ {filtered_data.index[-1].strftime('%Y-%m-%d')}")
    
    # reconcile_positions_with_close_history에서 사용하는 방식 확인
    print(f"\n4. reconcile_positions_with_close_history() 필터링 로직 확인")
    reconcile_data = soxl_data.copy()
    if not trader.is_regular_session_closed_now() and len(reconcile_data) > 0:
        if reconcile_data.index.max().date() == today_date:
            print(f"   ⚠️ 필터링 적용: 오늘 날짜 데이터 제외")
            reconcile_data = reconcile_data[reconcile_data.index.date < today_date]
        else:
            print(f"   ✅ 필터링 불필요")
    
    if target_date in reconcile_data.index:
        print(f"   ✅ reconcile 후에도 {target_date_str} 데이터 존재")
    else:
        found_reconcile = False
        for idx in reconcile_data.index:
            if idx.strftime('%Y-%m-%d') == target_date_str:
                print(f"   ✅ reconcile 후에도 {target_date_str} 데이터 존재 (문자열 매칭)")
                found_reconcile = True
                break
        
        if not found_reconcile:
            print(f"   ❌ reconcile 후 {target_date_str} 데이터 없음")

print("\n" + "=" * 60)
print("테스트 완료")
print("=" * 60)

