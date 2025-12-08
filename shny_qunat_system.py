import os
from datetime import datetime, timedelta

from input_quant_system import SOXLQuantTrader


class SHNYQuantTrader(SOXLQuantTrader):
    """SHNY 전용 트레이더 (티커 기본값 SHNY)"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("ticker", "SHNY")
        super().__init__(*args, **kwargs)


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

    # 트레이더 초기화 (티커 고정)
    trader = SHNYQuantTrader(initial_capital, ticker=ticker)

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

