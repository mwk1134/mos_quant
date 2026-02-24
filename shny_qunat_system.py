import os
from datetime import datetime, timedelta

import pandas as pd
import requests

from soxl_quant_system import SOXLQuantTrader


class SHNYQuantTrader(SOXLQuantTrader):
    """SHNY 전용 트레이더 (SHNY 티커 사용)"""

    STOCK_SPLITS = [
        {"date": "2026-02-24", "ratio": 10},  # 10:1 분할
    ]
    
    def __init__(self, initial_capital: float = 40000, sf_config=None, ag_config=None):
        """
        초기화
        Args:
            initial_capital: 투자원금
            sf_config: SF 모드 설정
            ag_config: AG 모드 설정
        """
        super().__init__(initial_capital, sf_config, ag_config)
        self.ticker = "SHNY"  # SHNY 티커 설정
        self._original_get_stock_data = super().get_stock_data  # 원본 메서드 저장
        self._rt_price_cache = {}

    def _get_realtime_price(self):
        """분할 감지용 실시간 시세 조회 (5분 캐시)"""
        cached = self._rt_price_cache.get(self.ticker)
        if cached:
            price, ts = cached
            if (datetime.now() - ts).total_seconds() < 300:
                return price

        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.ticker}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = requests.get(
                url, headers=headers, params={"range": "5d", "interval": "1d"}, timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                meta = data["chart"]["result"][0].get("meta", {})
                price = meta.get("regularMarketPrice")
                if price and price > 0:
                    self._rt_price_cache[self.ticker] = (price, datetime.now())
                    print(f"📡 SHNY 실시간 시세 조회: ${price:.2f}")
                    return price
        except Exception as e:
            print(f"⚠️ 실시간 시세 조회 실패: {e}")
        return None

    def _adjust_for_splits(self, df):
        """
        주식분할에 따른 과거 가격 데이터 조정.
        Yahoo Finance API가 아직 분할을 반영하지 않은 경우에만 자동 적용.

        Case 1: 분할일 전후 데이터가 모두 있으면 가격 불연속 감지
        Case 2: 장 개시 전이라 post-split 데이터가 없으면 실시간 시세로 비교
        """
        if df is None or len(df) < 2:
            return df

        today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"))

        for split in self.STOCK_SPLITS:
            split_date = pd.Timestamp(split["date"])
            ratio = split["ratio"]

            if today < split_date:
                continue

            pre_mask = df.index < split_date
            pre_split = df.loc[pre_mask]
            post_split = df.loc[~pre_mask]

            if len(pre_split) == 0:
                continue

            last_pre_close = pre_split.iloc[-1]["Close"]
            needs_adjustment = False

            if len(post_split) > 0:
                first_post_close = post_split.iloc[0]["Close"]
                if first_post_close > 0 and last_pre_close / first_post_close > ratio * 0.5:
                    needs_adjustment = True
            else:
                # 장 개시 전: 실시간 시세(regularMarketPrice)로 비교
                rt_price = self._get_realtime_price()
                if rt_price and rt_price > 0:
                    if last_pre_close / rt_price > ratio * 0.5:
                        needs_adjustment = True
                else:
                    # 실시간 시세 조회 실패 시 가격 크기 휴리스틱
                    # 조정 후 가격이 $0.50 이상이면 미조정 데이터로 판단
                    if last_pre_close / ratio > 0.50:
                        needs_adjustment = True

            if needs_adjustment:
                print(
                    f"🔄 SHNY {ratio}:1 주식분할 조정 적용 "
                    f"(분할일: {split['date']}, 최종종가: ${last_pre_close:.2f} → "
                    f"조정후: ${last_pre_close / ratio:.2f})"
                )
                df = df.copy()
                for col in ["Open", "High", "Low", "Close"]:
                    if col in df.columns:
                        df.loc[pre_mask, col] = df.loc[pre_mask, col] / ratio
                if "Volume" in df.columns:
                    df.loc[pre_mask, "Volume"] = df.loc[pre_mask, "Volume"] * ratio

        return df

    def get_stock_data(self, symbol: str, period: str = "1mo"):
        """
        주식 데이터 가져오기 (SOXL 요청을 SHNY로 리다이렉트 + 분할 조정)
        """
        # SOXL 요청을 SHNY로 변경
        if symbol == "SOXL":
            symbol = self.ticker
        
        # 원본 메서드 호출
        data = self._original_get_stock_data(symbol, period)

        # SHNY 데이터에 대해 주식분할 조정 적용
        if data is not None and symbol == self.ticker:
            data = self._adjust_for_splits(data)

        return data


def main():
    """SHNY 전용 실행 함수"""
    ticker = "SHNY"
    print(f"🚀 {ticker} 퀀트투자 시스템")
    print("=" * 50)

    # 투자원금 사용자 입력
    while True:
        try:
            initial_capital_input = input("💰 초기 투자금을 입력하세요 (달러): ").strip()
            if not initial_capital_input:
                initial_capital = 40000  # 기본값
                print(f"💰 투자원금: ${initial_capital:,.0f} (기본값)")
                break

            initial_capital = float(initial_capital_input)
            if initial_capital <= 0:
                print("❌ 투자금은 0보다 큰 값이어야 합니다.")
                continue

            print(f"💰 투자원금: ${initial_capital:,.0f}")
            break

        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")
            continue

    # 트레이더 초기화 (기본 SHNY 로직 사용)
    trader = SHNYQuantTrader(initial_capital)

    # 시작일 입력(엔터 시 1년 전)
    start_date_input = input("📅 투자 시작일을 입력하세요 (YYYY-MM-DD, 엔터시 1년 전): ").strip()
    if not start_date_input:
        start_date_input = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    trader.session_start_date = start_date_input

    while True:
        print("\n" + "=" * 50)
        print("메뉴를 선택하세요:")
        print("1. 오늘의 매매 추천 보기")
        print("2. 포트폴리오 현황 보기")
        print("3. 백테스팅 실행")
        print("4. 매수 실행 (테스트)")
        print("5. 매도 실행 (테스트)")
        print("T. 테스트 날짜(오늘) 설정/해제")
        print("6. 종료")

        choice = input("\n선택 (1-6): ").strip()

        if choice == '1':
            # 저장된 시작일부터 오늘까지 시뮬레이션으로 현재 상태 산출
            start_date = trader.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            sim_result = trader.simulate_from_start_to_today(start_date, quiet=True)
            if "error" in sim_result:
                print(f"❌ 시뮬레이션 실패: {sim_result['error']}")

            # 현재 상태 기반 오늘의 추천 출력
            recommendation = trader.get_daily_recommendation()
            trader.print_recommendation(recommendation)

        elif choice == '2':
            # 저장된 시작일부터 오늘까지 시뮬레이션으로 현황 재계산
            start_date = trader.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            sim_result = trader.simulate_from_start_to_today(start_date, quiet=True)
            if "error" in sim_result:
                print(f"❌ 시뮬레이션 실패: {sim_result['error']}")

            # 기존 형식 유지하여 현황 출력
            if trader.positions:
                print("\n💼 현재 포트폴리오:")
                print("-" * 30)
                for pos in trader.positions:
                    hold_days = (datetime.now() - pos['buy_date']).days
                    print(f"{pos['round']}회차: {pos['shares']}주 @ ${pos['buy_price']:.2f} ({hold_days}일)")
                print(f"\n현금잔고: ${trader.available_cash:,.0f}")
            else:
                print("\n보유 포지션이 없습니다.")
                print(f"현금잔고: ${trader.available_cash:,.0f}")

        elif choice == '3':
            # 백테스팅 실행
            print("\n📊 백테스팅 실행")
            print("-" * 30)

            start_date = input("시작 날짜를 입력하세요 (YYYY-MM-DD): ").strip()
            if not start_date:
                print("날짜를 입력해주세요.")
                continue

            end_date = input("종료 날짜를 입력하세요 (YYYY-MM-DD, 엔터시 오늘까지): ").strip()
            if not end_date:
                end_date = None

            print("\n백테스팅을 시작합니다...")
            backtest_result = trader.run_backtest(start_date, end_date)

            if "error" in backtest_result:
                print(f"❌ 백테스팅 실패: {backtest_result['error']}")
                continue

            # MDD 계산
            mdd_info = trader.calculate_mdd(backtest_result['daily_records'])

            # 결과 출력
            print("\n" + "=" * 60)
            print("📊 백테스팅 결과 요약")
            print("=" * 60)
            print(f"기간: {backtest_result['start_date']} ~ {backtest_result['end_date']}")
            print(f"거래일수: {backtest_result['trading_days']}일")
            print(f"초기자본: ${backtest_result['initial_capital']:,.0f}")
            print(f"최종자산: ${backtest_result['final_value']:,.0f}")
            print(f"총수익률: {backtest_result['total_return']:+.2f}%")
            if backtest_result.get('annualized_return') is not None:
                print(f"연평균 수익률(CAGR): {backtest_result['annualized_return']:+.2f}%")

            print(f"최대 MDD: {mdd_info.get('mdd_percent', 0.0):.2f}%")
            print(f"최종보유포지션: {backtest_result['final_positions']}개")
            print(f"총 거래일수: {len(backtest_result['daily_records'])}일")

            # 엑셀 내보내기 여부 확인
            export_choice = input("\n엑셀 파일로 내보내시겠습니까? (y/n): ").strip().lower()
            if export_choice == 'y':
                filename = trader.export_backtest_to_excel(backtest_result)
                if filename:
                    print(f"📁 파일 위치: {os.path.abspath(filename)}")

        elif choice == '4':
            print("\n🔧 매수 테스트 기능 (개발 중)")

        elif choice == '5':
            print("\n🔧 매도 테스트 기능 (개발 중)")

        elif choice.lower() == 't':
            print("\n🧪 테스트 날짜 설정")
            print("- 비우고 엔터하면 해제됩니다")
            test_date = input("테스트 오늘 날짜 (YYYY-MM-DD): ").strip()
            trader.set_test_today(test_date if test_date else None)

        elif choice == '6':
            print("프로그램을 종료합니다.")
            break

        else:
            print("올바른 선택지를 입력하세요.")


if __name__ == "__main__":
    main()

