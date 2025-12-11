"""
주간 RSI 계산 함수
QQQ 일일 주가 데이터를 주간 데이터로 변환하여 RSI를 계산합니다.
soxl_quant_system.py의 calculate_weekly_rsi 메서드를 독립 함수로 분리한 버전입니다.
"""
import pandas as pd
import numpy as np
from typing import Optional


def calculate_weekly_rsi(df: pd.DataFrame, window: int = 14, verbose: bool = True) -> Optional[float]:
    """
    주간 RSI 계산 (제공된 함수 방식 적용)
    
    Args:
        df: 일일 주가 데이터 (Date를 인덱스로 하는 DataFrame, Open, High, Low, Close, Volume 컬럼 필요)
        window: RSI 계산 기간 (기본값: 14)
        verbose: 상세 정보 출력 여부 (기본값: True)
    
    Returns:
        float: 최신 주간 RSI 값 (계산 실패 시 None)
    """
    try:
        # 주간 데이터로 변환 (금요일 기준)
        weekly_df = df.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        
        if verbose:
            # 디버깅: 주간 데이터 확인
            print(f"   주간 데이터 변환 결과:")
            print(f"   - 기간: {weekly_df.index[0].strftime('%Y-%m-%d')} ~ {weekly_df.index[-1].strftime('%Y-%m-%d')}")
            print(f"   - 주간 데이터 수: {len(weekly_df)}주")
            print(f"   - 최근 5주 종가: {weekly_df['Close'].tail(5).values}")
        
        if len(weekly_df) < window + 1:
            if verbose:
                print(f"❌ 주간 RSI 계산을 위한 데이터 부족 (필요: {window+1}주, 현재: {len(weekly_df)}주)")
            return None
        
        # 제공된 함수 방식으로 RSI 계산
        delta = weekly_df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # 디버깅 정보 출력
        latest_rsi = rsi.iloc[-1]
        
        if verbose:
            print(f"📈 QQQ 주간 RSI: {latest_rsi:.2f}")
            print(f"   데이터 기간: {weekly_df.index[0].strftime('%Y-%m-%d')} ~ {weekly_df.index[-1].strftime('%Y-%m-%d')}")
            print(f"   주간 데이터 수: {len(weekly_df)}주")
            print(f"   최근 3개 RSI: {[f'{x:.2f}' if not np.isnan(x) else 'NaN' for x in rsi.tail(3).values]}")
            
            # 상세 계산 과정 출력
            print(f"   최근 3개 계산 과정:")
            for i in range(-3, 0):
                if i + len(weekly_df) >= 0:
                    date_str = weekly_df.index[i].strftime('%Y-%m-%d')
                    delta_val = delta.iloc[i]
                    gain_val = gain.iloc[i]
                    loss_val = loss.iloc[i]
                    rs_val = rs.iloc[i]
                    rsi_val = rsi.iloc[i]
                    print(f"   {date_str}: delta={delta_val:+.4f}, gain={gain_val:.4f}, loss={loss_val:.4f}, RS={rs_val:.4f}, RSI={rsi_val:.2f}")
        
        return float(latest_rsi)
        
    except Exception as e:
        if verbose:
            print(f"❌ 주간 RSI 계산 오류: {e}")
        return None


def calculate_weekly_rsi_series(df: pd.DataFrame, window: int = 14) -> Optional[pd.Series]:
    """
    주간 RSI 시리즈 전체 반환 (최신 값만이 아닌 전체 시리즈)
    
    Args:
        df: 일일 주가 데이터 (Date를 인덱스로 하는 DataFrame, Open, High, Low, Close, Volume 컬럼 필요)
        window: RSI 계산 기간 (기본값: 14)
    
    Returns:
        pd.Series: 주간 RSI 시리즈 (계산 실패 시 None)
    """
    try:
        # 주간 데이터로 변환 (금요일 기준)
        weekly_df = df.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        
        if len(weekly_df) < window + 1:
            return None
        
        # 제공된 함수 방식으로 RSI 계산
        delta = weekly_df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
        
    except Exception as e:
        print(f"❌ 주간 RSI 시리즈 계산 오류: {e}")
        return None


if __name__ == "__main__":
    """
    테스트 코드 예시
    """
    import requests
    from datetime import datetime
    
    def get_stock_data(symbol: str, period: str = "1mo") -> Optional[pd.DataFrame]:
        """Yahoo Finance API를 통해 주식 데이터 가져오기"""
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            params = {'range': period, 'interval': '1d'}
            
            print(f"[INFO] {symbol} 데이터 가져오는 중...")
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
    
    # 테스트 실행
    print("=" * 60)
    print("주간 RSI 계산 함수 테스트")
    print("=" * 60)
    
    # QQQ 데이터 가져오기
    qqq_data = get_stock_data("QQQ", "6mo")
    
    if qqq_data is not None:
        # 주간 RSI 계산
        rsi = calculate_weekly_rsi(qqq_data, window=14, verbose=True)
        
        if rsi is not None:
            print(f"\n✅ 최신 주간 RSI: {rsi:.2f}")
        else:
            print("\n❌ 주간 RSI 계산 실패")
        
        # 전체 RSI 시리즈 가져오기
        rsi_series = calculate_weekly_rsi_series(qqq_data, window=14)
        if rsi_series is not None:
            print(f"\n📊 전체 주간 RSI 시리즈 (최근 5개):")
            print(rsi_series.tail(5))
    else:
        print("\n❌ QQQ 데이터를 가져올 수 없습니다.")
