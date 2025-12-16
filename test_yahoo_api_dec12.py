"""Yahoo Finance API에서 12월 12일 데이터 직접 확인"""
import requests
from datetime import datetime
import pandas as pd

def test_yahoo_api():
    """Yahoo Finance API 직접 호출하여 12월 12일 데이터 확인"""
    symbol = "SOXL"
    period = "1mo"
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    params = {'range': period, 'interval': '1d'}
    
    print("=" * 60)
    print(f"Yahoo Finance API 직접 호출 테스트")
    print(f"심볼: {symbol}, 기간: {period}")
    print("=" * 60)
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"HTTP 상태 코드: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                result = data['chart']['result'][0]
                
                if 'timestamp' in result and 'indicators' in result:
                    timestamps = result['timestamp']
                    quote_data = result['indicators']['quote'][0]
                    
                    print(f"\n✅ API 응답 성공!")
                    print(f"총 timestamp 수: {len(timestamps)}")
                    
                    # 12월 12일 찾기
                    target_date = datetime(2025, 12, 12).date()
                    target_timestamp = None
                    target_index = None
                    
                    print(f"\n🔍 12월 12일 ({target_date}) 검색 중...")
                    
                    for i, ts in enumerate(timestamps):
                        ts_date = datetime.fromtimestamp(ts).date()
                        if ts_date == target_date:
                            target_timestamp = ts
                            target_index = i
                            print(f"✅ 12월 12일 timestamp 발견!")
                            print(f"   인덱스: {i}")
                            print(f"   timestamp: {ts}")
                            print(f"   날짜: {ts_date}")
                            break
                    
                    if target_index is None:
                        print(f"❌ 12월 12일 timestamp가 API 응답에 없습니다!")
                        print(f"\n주변 날짜 확인:")
                        nearby = []
                        for ts in timestamps:
                            ts_date = datetime.fromtimestamp(ts).date()
                            if abs((ts_date - target_date).days) <= 5:
                                nearby.append((ts_date, ts))
                        
                        for date, ts in sorted(nearby):
                            print(f"   {date}: timestamp={ts}")
                    else:
                        # 12월 12일 데이터 확인
                        print(f"\n📊 12월 12일 가격 데이터:")
                        print(f"   Open: {quote_data.get('open', [None] * len(timestamps))[target_index]}")
                        print(f"   High: {quote_data.get('high', [None] * len(timestamps))[target_index]}")
                        print(f"   Low: {quote_data.get('low', [None] * len(timestamps))[target_index]}")
                        print(f"   Close: {quote_data.get('close', [None] * len(timestamps))[target_index]}")
                        print(f"   Volume: {quote_data.get('volume', [None] * len(timestamps))[target_index]}")
                        
                        # DataFrame 생성하여 확인
                        df_data = {
                            'Date': [datetime.fromtimestamp(ts) for ts in timestamps],
                            'Open': quote_data.get('open', [None] * len(timestamps)),
                            'High': quote_data.get('high', [None] * len(timestamps)),
                            'Low': quote_data.get('low', [None] * len(timestamps)),
                            'Close': quote_data.get('close', [None] * len(timestamps)),
                            'Volume': quote_data.get('volume', [None] * len(timestamps))
                        }
                        
                        df = pd.DataFrame(df_data)
                        print(f"\n📊 DataFrame 생성 후 (dropna 전):")
                        print(f"   총 행 수: {len(df)}")
                        
                        # 12월 12일 행 확인
                        dec12_row = df.iloc[target_index]
                        print(f"\n   12월 12일 행:")
                        print(f"   Date: {dec12_row['Date']}")
                        print(f"   Open: {dec12_row['Open']}")
                        print(f"   High: {dec12_row['High']}")
                        print(f"   Low: {dec12_row['Low']}")
                        print(f"   Close: {dec12_row['Close']}")
                        print(f"   Volume: {dec12_row['Volume']}")
                        
                        # NaN 확인
                        print(f"\n   NaN 확인:")
                        print(f"   Open is NaN: {pd.isna(dec12_row['Open'])}")
                        print(f"   High is NaN: {pd.isna(dec12_row['High'])}")
                        print(f"   Low is NaN: {pd.isna(dec12_row['Low'])}")
                        print(f"   Close is NaN: {pd.isna(dec12_row['Close'])}")
                        print(f"   Volume is NaN: {pd.isna(dec12_row['Volume'])}")
                        
                        # dropna(subset=['Close']) 적용
                        df_after_dropna = df.dropna(subset=['Close'])
                        print(f"\n📊 dropna(subset=['Close']) 적용 후:")
                        print(f"   총 행 수: {len(df_after_dropna)}")
                        
                        # 12월 12일이 남아있는지 확인
                        dec12_in_result = False
                        for idx in df_after_dropna.index:
                            if df_after_dropna.loc[idx, 'Date'].date() == target_date:
                                dec12_in_result = True
                                print(f"   ✅ 12월 12일 데이터가 남아있습니다!")
                                print(f"      Close: ${df_after_dropna.loc[idx, 'Close']:.2f}")
                                break
                        
                        if not dec12_in_result:
                            print(f"   ❌ 12월 12일 데이터가 dropna()로 제거되었습니다!")
                            print(f"   원인: Close 값이 NaN입니다.")
                else:
                    print("❌ 차트 데이터 구조 오류")
            else:
                print("❌ 차트 결과 없음")
        else:
            print(f"❌ HTTP 오류: {response.status_code}")
            print(f"응답 내용: {response.text[:500]}")
            
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_yahoo_api()

