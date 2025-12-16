"""
1주전 RSI 계산 테스트 스크립트
"""
import sys
from datetime import datetime, timedelta
import pandas as pd
from soxl_quant_system import SOXLQuantTrader

def main():
    print("=" * 60)
    print("1주전 RSI 계산 테스트")
    print("=" * 60)
    
    trader = SOXLQuantTrader()
    
    # QQQ 데이터 가져오기
    print("\n📊 QQQ 데이터 가져오는 중...")
    qqq_data = trader.get_stock_data("QQQ", "6mo")
    if qqq_data is None:
        print("❌ QQQ 데이터를 가져올 수 없습니다.")
        return
    
    print(f"✅ QQQ 데이터 가져오기 성공!")
    print(f"   기간: {qqq_data.index[0].strftime('%Y-%m-%d')} ~ {qqq_data.index[-1].strftime('%Y-%m-%d')}")
    print(f"   총 {len(qqq_data)}일치 데이터")
    
    # 주간 데이터로 변환
    print("\n📅 주간 데이터로 변환 중...")
    weekly_df = qqq_data.resample('W-FRI').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    print(f"✅ 주간 데이터 변환 완료!")
    print(f"   기간: {weekly_df.index[0].strftime('%Y-%m-%d')} ~ {weekly_df.index[-1].strftime('%Y-%m-%d')}")
    print(f"   총 {len(weekly_df)}주차 데이터")
    
    # 최근 5주 종가 출력
    print("\n📈 최근 5주 종가:")
    for i in range(-5, 0):
        if abs(i) <= len(weekly_df):
            date = weekly_df.index[i]
            close = weekly_df.iloc[i]['Close']
            print(f"   {date.strftime('%Y-%m-%d')} (금요일): ${close:.2f}")
    
    # RSI 계산
    if len(weekly_df) < 15:
        print(f"\n❌ 주간 데이터 부족 (필요: 15주, 현재: {len(weekly_df)}주)")
        return
    
    print("\n🔢 RSI 계산 중...")
    delta = weekly_df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 최근 5주 RSI 출력
    print("\n📊 최근 5주 RSI 값:")
    for i in range(-5, 0):
        if abs(i) <= len(rsi):
            date = weekly_df.index[i]
            rsi_value = rsi.iloc[i]
            if pd.notna(rsi_value):
                print(f"   {date.strftime('%Y-%m-%d')} (금요일): RSI = {rsi_value:.2f}")
            else:
                print(f"   {date.strftime('%Y-%m-%d')} (금요일): RSI = NaN")
    
    # 현재 코드에서 사용하는 값들
    print("\n" + "=" * 60)
    print("현재 코드에서 사용하는 RSI 값:")
    print("=" * 60)
    
    if len(rsi) >= 1:
        latest_rsi = rsi.iloc[-1]
        latest_date = weekly_df.index[-1]
        print(f"📌 최신 주간 RSI (rsi.iloc[-1]):")
        print(f"   날짜: {latest_date.strftime('%Y-%m-%d')} (금요일)")
        print(f"   RSI: {latest_rsi:.2f}")
    
    if len(rsi) >= 2:
        one_week_ago_rsi = rsi.iloc[-2]
        one_week_ago_date = weekly_df.index[-2]
        print(f"\n📌 1주전 RSI (rsi.iloc[-2]):")
        print(f"   날짜: {one_week_ago_date.strftime('%Y-%m-%d')} (금요일)")
        print(f"   RSI: {one_week_ago_rsi:.2f}")
    
    if len(rsi) >= 3:
        two_weeks_ago_rsi = rsi.iloc[-3]
        two_weeks_ago_date = weekly_df.index[-3]
        print(f"\n📌 2주전 RSI (rsi.iloc[-3]):")
        print(f"   날짜: {two_weeks_ago_date.strftime('%Y-%m-%d')} (금요일)")
        print(f"   RSI: {two_weeks_ago_rsi:.2f}")
    
    # 오늘 날짜 확인
    today = trader.get_today_date()
    print(f"\n📅 오늘 날짜: {today.strftime('%Y-%m-%d')} ({['월', '화', '수', '목', '금', '토', '일'][today.weekday()]}요일)")
    
    # 이번 주 금요일 계산
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0 and today.weekday() != 4:
        days_until_friday = 7
    this_week_friday = today + timedelta(days=days_until_friday)
    print(f"📅 이번 주 금요일: {this_week_friday.strftime('%Y-%m-%d')}")
    
    # 1주전 금요일 계산
    prev_week_friday = this_week_friday - timedelta(days=7)
    print(f"📅 1주전 금요일: {prev_week_friday.strftime('%Y-%m-%d')}")
    
    # weekly_rsi_reference.json과 비교
    print("\n" + "=" * 60)
    print("weekly_rsi_reference.json과 비교:")
    print("=" * 60)
    
    rsi_ref_data = trader.load_rsi_reference_data()
    if rsi_ref_data:
        ref_one_week_rsi = trader.get_rsi_from_reference(prev_week_friday, rsi_ref_data)
        if ref_one_week_rsi is not None:
            print(f"📌 weekly_rsi_reference.json에서 1주전 RSI:")
            print(f"   날짜: {prev_week_friday.strftime('%Y-%m-%d')}")
            print(f"   RSI: {ref_one_week_rsi:.2f}")
            
            if len(rsi) >= 2:
                calc_one_week_rsi = rsi.iloc[-2]
                print(f"\n📌 직접 계산한 1주전 RSI (rsi.iloc[-2]):")
                print(f"   날짜: {one_week_ago_date.strftime('%Y-%m-%d')}")
                print(f"   RSI: {calc_one_week_rsi:.2f}")
                
                print(f"\n🔍 차이: {abs(ref_one_week_rsi - calc_one_week_rsi):.2f}")
        else:
            print(f"❌ weekly_rsi_reference.json에서 {prev_week_friday.strftime('%Y-%m-%d')}의 RSI를 찾을 수 없습니다.")
    else:
        print("❌ RSI 참조 데이터를 로드할 수 없습니다.")

if __name__ == "__main__":
    main()

