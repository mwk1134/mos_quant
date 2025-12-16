"""12월 12일 SOXL 데이터 상세 확인 - reconcile_positions_with_close_history 테스트"""
from soxl_quant_system import SOXLQuantTrader
from datetime import datetime
import pandas as pd

# 트레이더 초기화
trader = SOXLQuantTrader(initial_capital=9000.0)
trader.session_start_date = "2025-08-27"

# 시드증액 추가
trader.add_seed_increase("2025-10-21", 31000.0, "시드증액")

print("=" * 60)
print("12월 12일 SOXL 데이터 상세 확인")
print("=" * 60)

# 시뮬레이션 실행
print("\n1. 시뮬레이션 실행")
sim_result = trader.simulate_from_start_to_today("2025-08-27", quiet=False)
if "error" in sim_result:
    print(f"❌ 시뮬레이션 실패: {sim_result['error']}")
    exit(1)

print(f"\n✅ 시뮬레이션 완료")
print(f"   보유 포지션 수: {len(trader.positions)}개")

# 보유 포지션 확인
if trader.positions:
    print(f"\n2. 보유 포지션 목록:")
    for pos in trader.positions:
        buy_date = pos.get('buy_date')
        if isinstance(buy_date, pd.Timestamp):
            buy_date_str = buy_date.strftime('%Y-%m-%d')
        elif isinstance(buy_date, datetime):
            buy_date_str = buy_date.strftime('%Y-%m-%d')
        else:
            buy_date_str = str(buy_date)
        print(f"   - {pos['round']}회차: {buy_date_str} | ${pos['buy_price']:.2f} | {pos['shares']}주")

# SOXL 데이터 가져오기
print(f"\n3. SOXL 데이터 가져오기 (get_daily_recommendation 방식)")
soxl_data = trader.get_stock_data("SOXL", "1mo")
if soxl_data is None:
    print("❌ SOXL 데이터를 가져올 수 없습니다.")
    exit(1)

print(f"✅ SOXL 데이터: {len(soxl_data)}개 일봉")
print(f"   데이터 범위: {soxl_data.index[0].strftime('%Y-%m-%d')} ~ {soxl_data.index[-1].strftime('%Y-%m-%d')}")

# 12월 12일 데이터 확인
target_date_str = "2025-12-12"
print(f"\n4. {target_date_str} 데이터 확인")
dec12_found = False
dec12_data = None
dec12_index = None

for idx in soxl_data.index:
    if idx.strftime('%Y-%m-%d') == target_date_str:
        dec12_found = True
        dec12_data = soxl_data.loc[idx]
        dec12_index = idx
        break

if dec12_found:
    print(f"✅ {target_date_str} 데이터 발견!")
    print(f"   인덱스: {dec12_index}")
    print(f"   종가: ${dec12_data['Close']:.2f}")
    print(f"   시가: ${dec12_data['Open']:.2f}")
    print(f"   고가: ${dec12_data['High']:.2f}")
    print(f"   저가: ${dec12_data['Low']:.2f}")
else:
    print(f"❌ {target_date_str} 데이터를 찾을 수 없습니다.")
    print(f"   최근 10개 거래일:")
    for date in soxl_data.index[-10:]:
        print(f"   - {date.strftime('%Y-%m-%d')}")

# reconcile_positions_with_close_history에서 사용하는 데이터 확인
print(f"\n5. reconcile_positions_with_close_history() 데이터 필터링 확인")
today = trader.get_today_date()
today_date = today.date()
print(f"   오늘 날짜: {today_date}")
print(f"   정규장 마감 여부: {trader.is_regular_session_closed_now()}")

reconcile_data = soxl_data.copy()
if not trader.is_regular_session_closed_now() and len(reconcile_data) > 0:
    if reconcile_data.index.max().date() == today_date:
        print(f"   ⚠️ 필터링 적용: 오늘 날짜 데이터 제외")
        reconcile_data = reconcile_data[reconcile_data.index.date < today_date]
        print(f"   필터링 후 데이터 범위: {reconcile_data.index[0].strftime('%Y-%m-%d')} ~ {reconcile_data.index[-1].strftime('%Y-%m-%d')}")
    else:
        print(f"   ✅ 필터링 불필요: 최신 데이터 날짜({reconcile_data.index.max().date()})가 오늘({today_date})이 아님")

# reconcile_data에서 12월 12일 확인
dec12_in_reconcile = False
for idx in reconcile_data.index:
    if idx.strftime('%Y-%m-%d') == target_date_str:
        dec12_in_reconcile = True
        print(f"   ✅ reconcile_data에 {target_date_str} 데이터 존재")
        break

if not dec12_in_reconcile:
    print(f"   ❌ reconcile_data에 {target_date_str} 데이터 없음")

# 12월 12일 매수 포지션이 있다면 reconcile 테스트
print(f"\n6. 12월 12일 매수 포지션 reconcile 테스트")
dec12_positions = []
for pos in trader.positions:
    buy_date = pos.get('buy_date')
    if isinstance(buy_date, (datetime, pd.Timestamp)):
        buy_date_str = buy_date.strftime('%Y-%m-%d')
        if buy_date_str == target_date_str:
            dec12_positions.append(pos)

if dec12_positions:
    print(f"   ✅ 12월 12일 매수 포지션 {len(dec12_positions)}개 발견")
    for pos in dec12_positions:
        buy_date = pos['buy_date']
        print(f"\n   포지션: {pos['round']}회차")
        print(f"   매수일: {buy_date.strftime('%Y-%m-%d')}")
        print(f"   매수가: ${pos['buy_price']:.2f}")
        
        # 매수일 이후 데이터 확인
        future_data = reconcile_data[reconcile_data.index > buy_date]
        print(f"   매수일 이후 데이터 수: {len(future_data)}개")
        
        if len(future_data) > 0:
            print(f"   매수일 이후 첫 거래일: {future_data.index[0].strftime('%Y-%m-%d')}")
            print(f"   매수일 이후 마지막 거래일: {future_data.index[-1].strftime('%Y-%m-%d')}")
            
            # 매도 목표가 계산
            config = trader.sf_config if pos['mode'] == "SF" else trader.ag_config
            target_price = pos['buy_price'] * (1 + config['sell_threshold'] / 100)
            print(f"   매도 목표가: ${target_price:.2f}")
            
            # 목표가 도달 여부 확인
            hit_rows = future_data[future_data["Close"] >= target_price]
            if not hit_rows.empty:
                sell_date = hit_rows.index[0]
                sell_close = hit_rows.iloc[0]["Close"]
                print(f"   ⚠️ 매도 조건 충족: {sell_date.strftime('%Y-%m-%d')} 종가 ${sell_close:.2f} >= 목표가 ${target_price:.2f}")
                print(f"   → reconcile_positions_with_close_history()에서 이 포지션이 제거될 것입니다!")
            else:
                print(f"   ✅ 매도 조건 미충족: 목표가 ${target_price:.2f} 미만")
        else:
            print(f"   ⚠️ 매수일 이후 데이터 없음")
else:
    print(f"   ℹ️ 12월 12일 매수 포지션 없음")

print("\n" + "=" * 60)
print("테스트 완료")
print("=" * 60)

