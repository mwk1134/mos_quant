"""
2025년 주간 RSI 계산 및 출력
calculate_weekly_rsi 모듈을 사용하여 2025년 QQQ 주간 RSI를 계산합니다.
"""
import requests
import pandas as pd
from datetime import datetime, timedelta
from calculate_weekly_rsi import calculate_weekly_rsi, calculate_weekly_rsi_series


def get_stock_data(symbol: str, start_date: str = None, end_date: str = None, period: str = None) -> pd.DataFrame:
    """
    Yahoo Finance API를 통해 주식 데이터 가져오기
    
    Args:
        symbol: 주식 심볼 (예: "QQQ")
        start_date: 시작 날짜 (YYYY-MM-DD 형식, 선택사항)
        end_date: 종료 날짜 (YYYY-MM-DD 형식, 선택사항)
        period: 기간 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max, 선택사항)
    
    Returns:
        pd.DataFrame: 주식 데이터 (Date 인덱스, Open, High, Low, Close, Volume 컬럼)
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # period가 지정되지 않았으면 start_date와 end_date 사용
        if period:
            params = {'range': period, 'interval': '1d'}
        elif start_date and end_date:
            # 시작일과 종료일을 타임스탬프로 변환
            start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp())
            end_ts = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp())
            params = {
                'period1': start_ts,
                'period2': end_ts,
                'interval': '1d'
            }
        else:
            # 기본값: 1년
            params = {'range': '1y', 'interval': '1d'}
        
        print(f"[INFO] {symbol} 데이터 가져오는 중...")
        if start_date and end_date:
            print(f"   기간: {start_date} ~ {end_date}")
        elif period:
            print(f"   기간: {period}")
        
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                result = data['chart']['result'][0]
                
                if 'timestamp' in result and 'indicators' in result:
                    timestamps = result['timestamp']
                    quote_data = result['indicators']['quote'][0]
                    
                    # DataFrame 생성
                    df_data = {
                        'Date': [datetime.fromtimestamp(ts) for ts in timestamps],
                        'Open': quote_data.get('open', [None] * len(timestamps)),
                        'High': quote_data.get('high', [None] * len(timestamps)),
                        'Low': quote_data.get('low', [None] * len(timestamps)),
                        'Close': quote_data.get('close', [None] * len(timestamps)),
                        'Volume': quote_data.get('volume', [None] * len(timestamps))
                    }
                    
                    df = pd.DataFrame(df_data)
                    df = df.dropna()
                    df.set_index('Date', inplace=True)
                    
                    print(f"[SUCCESS] {symbol} 데이터 가져오기 성공! ({len(df)}일치 데이터)")
                    return df
        
        print(f"❌ {symbol} 데이터 가져오기 실패")
        return None
            
    except Exception as e:
        print(f"❌ {symbol} 데이터 가져오기 오류: {e}")
        return None


def main():
    """2025년 주간 RSI 계산 및 출력"""
    print("=" * 60)
    print("2025년 QQQ 주간 RSI 계산")
    print("=" * 60)
    
    # 2025년 데이터를 가져오기 위해 충분한 기간의 데이터 필요 (RSI 계산을 위해 과거 데이터도 필요)
    # 2024년 말부터 데이터를 가져와서 2025년 주간 RSI를 계산
    print("\n[1단계] QQQ 데이터 가져오기 (2024-01-01 ~ 현재)")
    qqq_data = get_stock_data("QQQ", start_date="2024-01-01", end_date=None, period="2y")
    
    if qqq_data is None:
        print("❌ QQQ 데이터를 가져올 수 없습니다.")
        return
    
    # 2025년 데이터만 필터링
    qqq_2025 = qqq_data[qqq_data.index >= datetime(2025, 1, 1)]
    
    if len(qqq_2025) == 0:
        print("❌ 2025년 데이터가 없습니다.")
        return
    
    print(f"\n[2단계] 2025년 데이터 필터링 완료 ({len(qqq_2025)}일치)")
    print(f"   기간: {qqq_2025.index[0].strftime('%Y-%m-%d')} ~ {qqq_2025.index[-1].strftime('%Y-%m-%d')}")
    
    # 전체 데이터로 주간 RSI 시리즈 계산 (2024년 데이터 포함하여 정확한 RSI 계산)
    print("\n[3단계] 주간 RSI 계산 중...")
    rsi_series = calculate_weekly_rsi_series(qqq_data, window=14)
    
    if rsi_series is None:
        print("❌ 주간 RSI 계산 실패")
        return
    
    # 2025년 주간 RSI만 필터링
    rsi_2025 = rsi_series[rsi_series.index >= datetime(2025, 1, 1)]
    
    if len(rsi_2025) == 0:
        print("❌ 2025년 주간 RSI 데이터가 없습니다.")
        return
    
    print(f"\n[4단계] 2025년 주간 RSI 계산 완료 ({len(rsi_2025)}주차)")
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("2025년 주간 RSI 결과")
    print("=" * 60)
    print(f"{'주차':<10} {'시작일':<12} {'종료일':<12} {'RSI':<10}")
    print("-" * 60)
    
    for date, rsi_value in rsi_2025.items():
        # 해당 주의 시작일과 종료일 계산
        # 금요일이 종료일이므로, 시작일은 월요일
        week_start = date - timedelta(days=4)  # 금요일에서 4일 전 = 월요일
        
        # 주차 번호 계산
        week_num = week_start.isocalendar()[1]
        
        start_str = week_start.strftime('%Y-%m-%d')
        end_str = date.strftime('%Y-%m-%d')
        rsi_str = f"{rsi_value:.2f}" if not pd.isna(rsi_value) else "NaN"
        
        print(f"{week_num:>2}주차    {start_str}  {end_str}  {rsi_str:>8}")
    
    print("\n" + "=" * 60)
    print(f"총 {len(rsi_2025)}주차의 주간 RSI 계산 완료")
    print(f"최신 주간 RSI: {rsi_2025.iloc[-1]:.2f} ({rsi_2025.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 60)


if __name__ == "__main__":
    main()

