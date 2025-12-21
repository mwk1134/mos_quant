"""
파라미터.xlsx 파일에서 파라미터를 읽어서 백테스팅을 실행하는 스크립트
"""
import openpyxl
from datetime import datetime
from soxl_quant_system import SOXLQuantTrader


def load_parameters_from_excel(excel_file: str = "파라미터.xlsx"):
    """
    엑셀 파일에서 파라미터 읽기
    Args:
        excel_file: 엑셀 파일 경로
    Returns:
        tuple: (ag_config, sf_config)
    """
    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active
        
        # 공세모드(AG) 파라미터 읽기
        ag_buy_threshold = ws['B11'].value  # 공세모드 매수임계값
        ag_sell_threshold = ws['B12'].value  # 공세모드 매도임계값
        ag_max_hold_days = int(ws['B13'].value) if ws['B13'].value else None  # 공세모드 최대보유일
        ag_split_count = int(ws['B14'].value) if ws['B14'].value else None  # 공세모드 분할횟수
        
        # 안전모드(SF) 파라미터 읽기
        sf_buy_threshold = ws['B15'].value  # 안전모드 매수임계값
        sf_sell_threshold = ws['B16'].value  # 안전모드 매도임계값
        sf_max_hold_days = int(ws['B17'].value) if ws['B17'].value else None  # 안전모드 최대보유일
        sf_split_count = int(ws['B18'].value) if ws['B18'].value else None  # 안전모드 분할횟수
        
        # 공세모드 회차별 비중 읽기 (B21~B28)
        ag_split_ratios = []
        for row in range(21, 29):  # B21 ~ B28
            cell_value = ws[f'B{row}'].value
            if cell_value is not None:
                ag_split_ratios.append(float(cell_value))
        
        # 안전모드 회차별 비중 읽기 (B29~B36, 빈칸이 있으면 매수하지 않음)
        sf_split_ratios = []
        for row in range(29, 37):  # B29 ~ B36
            cell_value = ws[f'B{row}'].value
            if cell_value is not None and str(cell_value).strip() != '':
                sf_split_ratios.append(float(cell_value))
            else:
                # 빈칸이 있으면 그 이후는 무시 (매수하지 않음)
                break
        
        # 공세모드 설정
        ag_config = {
            "buy_threshold": float(ag_buy_threshold),
            "sell_threshold": float(ag_sell_threshold),
            "max_hold_days": ag_max_hold_days,
            "split_count": len(ag_split_ratios) if ag_split_count is None else ag_split_count,
            "split_ratios": ag_split_ratios
        }
        
        # 안전모드 설정
        sf_config = {
            "buy_threshold": float(sf_buy_threshold),
            "sell_threshold": float(sf_sell_threshold),
            "max_hold_days": sf_max_hold_days,
            "split_count": len(sf_split_ratios) if sf_split_count is None else sf_split_count,
            "split_ratios": sf_split_ratios
        }
        
        print("✅ 파라미터 로드 완료")
        print(f"   공세모드: 매수 {ag_config['buy_threshold']}%, 매도 {ag_config['sell_threshold']}%, 보유일 {ag_config['max_hold_days']}일, 분할 {ag_config['split_count']}회")
        print(f"   안전모드: 매수 {sf_config['buy_threshold']}%, 매도 {sf_config['sell_threshold']}%, 보유일 {sf_config['max_hold_days']}일, 분할 {sf_config['split_count']}회")
        
        return ag_config, sf_config
        
    except Exception as e:
        print(f"❌ 엑셀 파일 읽기 오류: {e}")
        raise


def calculate_mdd(daily_records):
    """
    최대 낙폭(MDD) 계산
    Args:
        daily_records: 일별 기록 리스트
    Returns:
        dict: MDD 정보
    """
    if not daily_records:
        return {"mdd_percent": 0.0, "mdd_date": "", "mdd_value": 0.0}
    
    peak = daily_records[0]['total_assets']
    max_drawdown = 0.0
    mdd_date = ""
    mdd_value = 0.0
    
    for record in daily_records:
        total_assets = record['total_assets']
        if total_assets > peak:
            peak = total_assets
        
        drawdown = (peak - total_assets) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            mdd_date = record['date']
            mdd_value = total_assets
    
    return {
        "mdd_percent": max_drawdown,
        "mdd_date": mdd_date,
        "mdd_value": mdd_value
    }


def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("🚀 파라미터 기반 백테스팅")
    print("=" * 60)
    
    # 엑셀 파일에서 파라미터 읽기
    try:
        ag_config, sf_config = load_parameters_from_excel("파라미터.xlsx")
    except Exception as e:
        print(f"❌ 파라미터 로드 실패: {e}")
        return
    
    # 기본 설정값
    initial_capital = 10000  # 투자원금 1만 달러
    start_date = "2011-01-01"  # 투자시작일
    end_date = "2025-12-07"  # 투자종료일
    
    print(f"\n💰 투자원금: ${initial_capital:,.0f}")
    print(f"📅 투자기간: {start_date} ~ {end_date}")
    
    # 트레이더 초기화 (파라미터 적용)
    print("\n🔄 트레이더 초기화 중...")
    trader = SOXLQuantTrader(
        initial_capital=initial_capital,
        sf_config=sf_config,
        ag_config=ag_config
    )
    
    # 백테스팅 실행
    print("\n📊 백테스팅 실행 중...")
    backtest_result = trader.run_backtest(start_date, end_date)
    
    if "error" in backtest_result:
        print(f"❌ 백테스팅 실패: {backtest_result['error']}")
        return
    
    # MDD 계산
    mdd_info = calculate_mdd(backtest_result['daily_records'])
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("📊 백테스팅 결과")
    print("=" * 60)
    print(f"기간: {backtest_result['start_date']} ~ {backtest_result['end_date']}")
    print(f"거래일수: {backtest_result['trading_days']}일")
    print(f"초기자본: ${backtest_result['initial_capital']:,.0f}")
    print(f"최종자산: ${backtest_result['final_value']:,.0f}")
    print(f"총수익률: {backtest_result['total_return']:+.2f}%")
    print(f"최대 MDD: {mdd_info.get('mdd_percent', 0.0):.2f}%")
    print(f"MDD 발생일: {mdd_info.get('mdd_date', 'N/A')}")
    print(f"최종보유포지션: {backtest_result['final_positions']}개")
    print(f"총 거래일수: {len(backtest_result['daily_records'])}일")
    
    # 연평균 수익률 계산
    if backtest_result['trading_days'] > 0:
        years = backtest_result['trading_days'] / 252  # 연간 거래일 약 252일
        if years > 0:
            annual_return = ((backtest_result['final_value'] / backtest_result['initial_capital']) ** (1 / years) - 1) * 100
            print(f"연평균 수익률: {annual_return:+.2f}%")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
