#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 데이터 업데이트 스크립트
오늘 날짜까지의 주간 RSI 데이터를 자동으로 계산하여 JSON 파일에 업데이트
"""

import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import sys

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
    
    def get_stock_data(self, symbol: str, period: str = "2y") -> pd.DataFrame:
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
            
            # 2년 데이터로 충분한 기간 확보
            params = {'range': period, 'interval': '1d'}
            
            print(f"📊 {symbol} 데이터 가져오는 중... (기간: {period})")
            
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
                        df = df.dropna()  # NaN 값 제거
                        df.set_index('Date', inplace=True)
                        
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
        주간 RSI 계산 (Wilder's RSI 방식)
        Args:
            df: 일일 주가 데이터
            window: RSI 계산 기간 (기본값: 14)
        Returns:
            Series: 주간 RSI 값들
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
                print(f"❌ 주간 RSI 계산을 위한 데이터 부족 (필요: {window+1}주, 현재: {len(weekly_df)}주)")
                return None
            
            # Wilder's RSI 계산
            delta = weekly_df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            
            print(f"📈 주간 RSI 계산 완료: {len(weekly_df)}주차 데이터")
            print(f"   최근 5개 RSI: {[f'{x:.2f}' if not np.isnan(x) else 'NaN' for x in rsi.tail(5).values]}")
            
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
        """
        RSI 데이터 업데이트 (오늘 날짜까지)
        Returns:
            bool: 업데이트 성공 여부
        """
        try:
            print("🔄 RSI 데이터 업데이트 시작...")
            print("=" * 60)
            
            # 1. 기존 데이터 로드
            existing_data = self.load_existing_data()
            
            # 2. QQQ 데이터 가져오기 (충분한 기간)
            print("\n📊 QQQ 데이터 수집 중...")
            qqq_data = self.get_stock_data("QQQ", "2y")
            if qqq_data is None:
                print("❌ QQQ 데이터를 가져올 수 없습니다.")
                return False
            
            # 3. 주간 RSI 계산
            print("\n📈 주간 RSI 계산 중...")
            weekly_rsi = self.calculate_weekly_rsi(qqq_data)
            if weekly_rsi is None:
                print("❌ 주간 RSI 계산 실패")
                return False
            
            # 4. 주간 데이터로 변환 (금요일 기준)
            weekly_data = qqq_data.resample('W-FRI').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
            
            # 5. 각 연도별로 데이터 업데이트
            print("\n📝 연도별 RSI 데이터 업데이트 중...")
            
            # 연도별로 그룹화
            yearly_data = {}
            for date, rsi_value in weekly_rsi.items():
                if not np.isnan(rsi_value):
                    year = date.year
                    if year not in yearly_data:
                        yearly_data[year] = []
                    
                    # 해당 주의 시작일 계산 (월요일)
                    week_start = date - timedelta(days=4)  # 금요일에서 4일 전 = 월요일
                    
                    # 주차 번호 계산
                    week_num = week_start.isocalendar()[1]
                    
                    yearly_data[year].append({
                        "start": week_start.strftime('%Y-%m-%d'),
                        "end": date.strftime('%Y-%m-%d'),
                        "week": week_num,
                        "rsi": round(float(rsi_value), 2)
                    })
            
            # 6. 기존 데이터에 새로운 데이터 추가/업데이트
            updated_count = 0
            for year, weeks_data in yearly_data.items():
                year_str = str(year)
                
                # 해당 연도 데이터 초기화
                if year_str not in existing_data:
                    existing_data[year_str] = {
                        "description": f"{year}년 주간 RSI 데이터",
                        "weeks": []
                    }
                
                # 주차별로 정렬
                weeks_data.sort(key=lambda x: x['week'])
                
                # 기존 주차와 비교하여 업데이트 또는 추가
                for week_data in weeks_data:
                    week_num = week_data['week']
                    
                    # 기존 주차 찾기
                    week_exists = False
                    for i, existing_week in enumerate(existing_data[year_str]['weeks']):
                        if existing_week['week'] == week_num:
                            # 기존 데이터 업데이트
                            old_rsi = existing_week.get('rsi', 0)
                            existing_data[year_str]['weeks'][i] = week_data
                            if old_rsi != week_data['rsi']:
                                updated_count += 1
                                print(f"   📝 {year}년 {week_num}주차 업데이트: {old_rsi} → {week_data['rsi']:.2f}")
                            week_exists = True
                            break
                    
                    if not week_exists:
                        # 새로운 주차 추가
                        existing_data[year_str]['weeks'].append(week_data)
                        updated_count += 1
                        print(f"   ➕ {year}년 {week_num}주차 추가: RSI {week_data['rsi']:.2f}")
                
                # 주차별로 정렬
                existing_data[year_str]['weeks'].sort(key=lambda x: x['week'])
            
            # 7. 메타데이터 업데이트
            total_weeks = sum(len(year_data['weeks']) for year, year_data in existing_data.items() if year != 'metadata')
            existing_data['metadata'] = {
                "last_updated": datetime.now().strftime('%Y-%m-%d'),
                "total_years": len([k for k in existing_data.keys() if k != 'metadata']),
                "total_weeks": total_weeks,
                "description": "QQQ 주간 RSI 참조 데이터 (14주 Wilder's RSI)",
                "updated_by": "update_rsi_data.py"
            }
            
            # 8. JSON 파일 저장
            print(f"\n💾 JSON 파일 저장 중...")
            with open(self.json_file_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            print("✅ RSI 데이터 업데이트 완료!")
            print("=" * 60)
            print(f"📊 업데이트 결과:")
            print(f"   - 총 {total_weeks}개 주차 데이터")
            print(f"   - 업데이트된 주차: {updated_count}개")
            print(f"   - 마지막 업데이트: {datetime.now().strftime('%Y-%m-%d')}")
            print(f"   - 파일 경로: {os.path.abspath(self.json_file_path)}")
            
            # 9. 최신 5주차 RSI 정보 출력
            print(f"\n📈 최신 5주차 RSI:")
            for year in sorted(yearly_data.keys(), reverse=True)[:2]:  # 최근 2년
                year_weeks = existing_data[str(year)]['weeks']
                recent_weeks = year_weeks[-3:] if len(year_weeks) >= 3 else year_weeks
                for week in recent_weeks:
                    print(f"   - {year}년 {week['week']}주차 ({week['end']}): RSI {week['rsi']:.2f}")
            
            return True
            
        except Exception as e:
            print(f"❌ RSI 데이터 업데이트 오류: {e}")
            import traceback
            traceback.print_exc()
            return False

def main():
    """메인 실행 함수"""
    print("🚀 RSI 데이터 업데이트 스크립트")
    print("=" * 60)
    print("📝 오늘 날짜까지의 QQQ 주간 RSI 데이터를 자동으로 계산하여 업데이트합니다.")
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
