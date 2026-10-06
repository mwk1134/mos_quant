#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 데이터 업데이트 스크립트
완료된 미국 거래 주의 RSI 데이터를 자동으로 계산하여 JSON 파일에 업데이트
"""

import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import os
import sys

from weekly_rsi import (
    US_EASTERN,
    calculate_completed_weekly_rsi,
    latest_completed_week_end,
    merge_rsi_reference,
)

class CompactJSONEncoder(json.JSONEncoder):
    """각 주차 객체를 한 줄로 저장하는 커스텀 JSON 인코더"""
    def encode(self, obj):
        if isinstance(obj, dict):
            # metadata는 일반 포맷으로
            if 'metadata' in obj:
                result = []
                for key, value in obj.items():
                    if key == 'metadata':
                        continue
                    result.append(f'  "{key}": {self._encode_year(value)}')
                result.append(f'  "metadata": {json.dumps(obj["metadata"], ensure_ascii=False, indent=2)}')
                return '{\n' + ',\n'.join(result) + '\n}'
            else:
                return json.dumps(obj, ensure_ascii=False, indent=2)
        return super().encode(obj)
    
    def _encode_year(self, year_data):
        """연도 데이터 인코딩"""
        desc = json.dumps(year_data['description'], ensure_ascii=False)
        weeks = []
        for week in year_data['weeks']:
            week_str = json.dumps(week, ensure_ascii=False, separators=(',', ':'))
            weeks.append(f'      {week_str}')
        weeks_str = '[\n' + ',\n'.join(weeks) + '\n    ]'
        return f'{{\n    "description": {desc},\n    "weeks": {weeks_str}\n  }}'

class RSIDataUpdater:
    """RSI 데이터 업데이트 클래스"""
    
    def __init__(self, json_file_path: str = "data/weekly_rsi_reference.json"):
        """
        초기화
        Args:
            json_file_path: RSI JSON 파일 경로
        """
        self.json_file_path = json_file_path
        self.data_dir = os.path.dirname(json_file_path)
        
        # data 폴더가 없으면 생성
        if self.data_dir and not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            print(f"📁 {self.data_dir} 폴더 생성 완료")
    
    def get_stock_data(self, symbol: str, period: str = "max") -> pd.DataFrame:
        """
        Yahoo Finance API를 통해 주식 데이터 가져오기
        Args:
            symbol: 주식 심볼 (예: "QQQ")
            period: 기간 (1y, 2y, 5y, 10y, 15y, max)
        Returns:
            DataFrame: 주식 데이터 (Date, Open, High, Low, Close, Volume)
        """
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # range=max는 interval=1d 요청에도 월봉으로 축약될 수 있다.
            # 전체 이력은 시작/종료 시각을 명시해 일봉으로 받는다.
            if period in ("max", "15y"):
                params = {
                    'period1': 0,
                    'period2': int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
                    'interval': '1d',
                    'events': 'history',
                }
            else:
                params = {'range': period, 'interval': '1d'}
            
            print(f"📊 {symbol} 데이터 가져오는 중... (기간: {period})")
            
            response = requests.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                    result = data['chart']['result'][0]
                    granularity = (result.get('meta') or {}).get('dataGranularity')
                    if granularity and granularity != '1d':
                        print(f"⚠️ {symbol} 일봉 대신 {granularity} 응답이 반환되었습니다.")
                        if period != '2y':
                            print("   최근 2년 일봉으로 다시 조회합니다. 이전 참조값은 보존합니다.")
                            return self.get_stock_data(symbol, '2y')
                        return None
                    
                    if 'timestamp' in result and 'indicators' in result:
                        timestamps = result['timestamp']
                        quote_data = result['indicators']['quote'][0]
                        
                        # DataFrame 생성
                        df_data = {
                            'Date': pd.to_datetime(timestamps, unit='s', utc=True)
                                .tz_convert(US_EASTERN).tz_localize(None).normalize(),
                            'Open': quote_data.get('open', [None] * len(timestamps)),
                            'High': quote_data.get('high', [None] * len(timestamps)),
                            'Low': quote_data.get('low', [None] * len(timestamps)),
                            'Close': quote_data.get('close', [None] * len(timestamps)),
                            'Volume': quote_data.get('volume', [None] * len(timestamps))
                        }
                        
                        df = pd.DataFrame(df_data)
                        df = df.dropna(subset=['Close'])
                        df.set_index('Date', inplace=True)
                        if df.empty:
                            return None
                        
                        print(f"✅ {symbol} 데이터 가져오기 성공! ({len(df)}일치 데이터)")
                        print(f"   기간: {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
                        return df
                    else:
                        print(f"   ❌ 차트 데이터 구조 오류")
                else:
                    print(f"   ❌ 차트 결과 없음")
            else:
                print(f"   ❌ HTTP 오류: {response.status_code}")
            
            return None
                
        except Exception as e:
            print(f"❌ {symbol} 데이터 가져오기 오류: {e}")
            return None
    
    def calculate_weekly_rsi(self, df: pd.DataFrame, window: int = 14) -> pd.Series:
        """
        완료된 미국 거래 주의 14주 단순평균 RSI 계산
        Args:
            df: 일일 주가 데이터
            window: RSI 계산 기간 (기본값: 14)
        Returns:
            Series: 주간 RSI 값들
        """
        try:
            rsi = calculate_completed_weekly_rsi(df, window=window)
            if len(rsi) < window + 1:
                print(f"❌ 주간 RSI 계산을 위한 데이터 부족 (필요: {window+1}주, 현재: {len(rsi)}주)")
                return None

            print(f"📈 완료된 주간 RSI 계산 완료: {len(rsi)}주차 데이터")
            print(f"   데이터 기간: {rsi.index[0].strftime('%Y-%m-%d')} ~ {rsi.index[-1].strftime('%Y-%m-%d')}")
            print(f"   최근 3개 RSI: {[f'{x:.2f}' if not np.isnan(x) else 'NaN' for x in rsi.tail(3).values]}")
            return rsi
            
        except Exception as e:
            print(f"❌ 주간 RSI 계산 오류: {e}")
            return None
    
    def load_existing_data(self) -> dict:
        """기존 RSI 데이터 로드"""
        try:
            if os.path.exists(self.json_file_path):
                with open(self.json_file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                metadata = data.get('metadata', {})
                total_weeks = metadata.get('total_weeks', 0)
                last_updated = metadata.get('last_updated', 'Unknown')
                
                print(f"📊 기존 RSI 데이터 로드 완료")
                print(f"   - 파일 경로: {self.json_file_path}")
                print(f"   - 총 {len(data)-1}개 연도 데이터 ({total_weeks}주차)")
                print(f"   - 마지막 업데이트: {last_updated}")
                
                return data
            else:
                print(f"⚠️ RSI 파일이 없습니다: {self.json_file_path}")
                return {}
        except Exception as e:
            print(f"❌ RSI 데이터 로드 오류: {e}")
            return {}
    
    def update_rsi_data(self) -> bool:
        """전체 이력에서 완료된 미국 거래 주의 RSI 참조값을 갱신한다."""
        try:
            print("🔄 RSI 데이터 업데이트 시작...")
            print("=" * 60)
            existing_data = self.load_existing_data()

            # 2010년부터의 RSI도 충분한 준비 구간으로 계산할 수 있도록 전체
            # 이력을 받아 계산한다. 기존 과거 주차는 계산값이 없으면 보존한다.
            print("\n📊 QQQ 전체 이력 수집 중...")
            qqq_data = self.get_stock_data("QQQ", "max")
            if qqq_data is None or qqq_data.empty:
                print("❌ QQQ 데이터를 가져올 수 없습니다.")
                return False

            print("\n📈 완료된 주간 RSI 계산 중...")
            weekly_rsi = self.calculate_weekly_rsi(qqq_data)
            if weekly_rsi is None or weekly_rsi.dropna().empty:
                print("❌ 완료된 주간 RSI 계산 실패")
                return False
            required_end = latest_completed_week_end()
            if weekly_rsi.dropna().index.max() != required_end:
                print(f"❌ 최신 완료 주차({required_end:%Y-%m-%d}) 종가가 없어 기존 참조값을 보존합니다.")
                return False

            # 종료 날짜로 병합하므로 연말 ISO 주차 번호 충돌과 다른 연도에
            # 잘못 저장된 중복 주차를 함께 정리한다.
            updated_data = merge_rsi_reference(
                existing_data, weekly_rsi, refresh_all=True
            )
            updated_data['metadata']['updated_by'] = "update_rsi_data.py"
            total_weeks = updated_data['metadata']['total_weeks']
            prior_values = {
                week['end']: week.get('rsi')
                for year, year_data in existing_data.items()
                if year != 'metadata' and isinstance(year_data, dict)
                for week in year_data.get('weeks', [])
                if week.get('end')
            }
            updated_count = sum(
                prior_values.get(week['end']) != week['rsi']
                for year, year_data in updated_data.items()
                if year != 'metadata'
                for week in year_data['weeks']
            )

            print("\n💾 JSON 파일 저장 중...")
            with open(self.json_file_path, 'w', encoding='utf-8') as output:
                output.write(json.dumps(
                    updated_data, ensure_ascii=False,
                    cls=CompactJSONEncoder,
                ))
                output.write('\n')

            print("✅ RSI 데이터 업데이트 완료!")
            print("=" * 60)
            print(f"   - 총 {total_weeks}개 주차 데이터")
            print(f"   - 업데이트된 주차: {updated_count}개")
            print(f"   - 마지막 완료 주차: {updated_data['metadata']['last_completed_week_end']}")
            print(f"   - 마지막 업데이트: {updated_data['metadata']['last_updated']}")
            print(f"   - 파일 경로: {os.path.abspath(self.json_file_path)}")
            print("\n📈 최신 5주차 RSI:")
            for week_end, value in weekly_rsi.dropna().tail(5).items():
                print(f"   - {week_end.strftime('%Y-%m-%d')}: RSI {value:.2f}")
            return True
        except Exception as error:
            print(f"❌ RSI 데이터 업데이트 오류: {error}")
            import traceback
            traceback.print_exc()
            return False

def main():
    """메인 실행 함수"""
    print("🚀 RSI 데이터 업데이트 스크립트")
    print("=" * 60)
    print("📝 완료된 미국 거래 주의 QQQ 주간 RSI 데이터를 자동으로 계산하여 업데이트합니다.")
    print()
    
    # JSON 파일 경로 확인
    json_file = "data/weekly_rsi_reference.json"
    
    # 명령행 인수로 파일 경로 지정 가능
    if len(sys.argv) > 1:
        json_file = sys.argv[1]
    
    print(f"📁 대상 파일: {json_file}")
    
    # 업데이터 초기화
    updater = RSIDataUpdater(json_file)
    
    # 업데이트 실행
    success = updater.update_rsi_data()
    
    if success:
        print("\n🎉 RSI 데이터 업데이트가 성공적으로 완료되었습니다!")
        print("💡 이제 soxl_quant_system.py를 실행하면 최신 RSI 데이터를 사용할 수 있습니다.")
    else:
        print("\n❌ RSI 데이터 업데이트에 실패했습니다.")
        print("💡 네트워크 연결과 인터넷 상태를 확인해주세요.")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
