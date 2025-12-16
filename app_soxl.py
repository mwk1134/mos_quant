import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Force redeploy - version 1.0
# ensure local mos_quant modules take precedence
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# SOXLQuantTrader 클래스 import
from soxl_quant_system import SOXLQuantTrader

# 페이지 설정
st.set_page_config(
    page_title="SOXL 퀀트투자 시스템",
    page_icon="📈",
    layout="wide"
)

# 커스텀 CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 1.5rem;
    }
    
    .mode-sf {
        color: #28a745;
        font-weight: bold;
    }
    
    .mode-ag {
        color: #ff8c00;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
if 'trader' not in st.session_state:
    # 초기 투자금: 2793달러
    initial_capital = 2793.0
    # 투자 시작일: 2025-10-30
    start_date = "2025-10-30"
    
    # 트레이더 초기화
    st.session_state.trader = SOXLQuantTrader(initial_capital=initial_capital)
    st.session_state.trader.session_start_date = start_date
    st.session_state.session_start_date = start_date

# 메인 헤더
st.markdown('<div class="main-header">📈 SOXL 퀀트투자 시스템</div>', unsafe_allow_html=True)

# 일일 매매 추천 페이지
st.header("📊 오늘의 매매 추천")

if not st.session_state.trader:
    st.error("시스템이 초기화되지 않았습니다.")
    st.stop()

# 시뮬레이션 실행
start_date = st.session_state.session_start_date

with st.spinner('현재 상태 계산 중...'):
    # 캐시 클리어하여 항상 최신 상태로 시뮬레이션
    st.session_state.trader.clear_cache()
    
    # 최신 거래일 확인
    latest_trading_day = st.session_state.trader.get_latest_trading_day()
    st.info(f"🔄 일일 추천 시뮬레이션 범위: {start_date} ~ {latest_trading_day.strftime('%Y-%m-%d')}")
    
    # 시뮬레이션 실행하여 트레이더 상태 업데이트
    sim_result = st.session_state.trader.simulate_from_start_to_today(start_date, quiet=True)
    if "error" in sim_result:
        st.error(f"시뮬레이션 실패: {sim_result['error']}")
        st.stop()
    
    # 일일 추천 생성
    recommendation = st.session_state.trader.get_daily_recommendation()

if "error" in recommendation:
    st.error(f"추천 생성 실패: {recommendation['error']}")
    st.stop()

# 기본 정보
col1, col2 = st.columns(2)

with col1:
    st.metric("📅 날짜", recommendation['date'])
    mode_name = "안전모드" if recommendation['mode'] == "SF" else "공세모드"
    mode_class = "mode-sf" if recommendation['mode'] == "SF" else "mode-ag"
    st.markdown(f"<div class='{mode_class}'>🎯 모드: {recommendation['mode']} ({mode_name})</div>", unsafe_allow_html=True)

with col2:
    one_week_rsi = recommendation.get('qqq_one_week_ago_rsi')
    two_weeks_rsi = recommendation.get('qqq_two_weeks_ago_rsi')
    if one_week_rsi is not None:
        if two_weeks_rsi is not None:
            st.metric("📊 QQQ 주간 RSI", f"1주전: {one_week_rsi:.2f} | 2주전: {two_weeks_rsi:.2f}")
        else:
            st.metric("📊 QQQ 주간 RSI", f"1주전: {one_week_rsi:.2f}")
    else:
        st.metric("📊 QQQ 주간 RSI", "계산 불가")
    st.metric("💰 SOXL 현재가", f"${recommendation['soxl_current_price']:.2f}")

# 매매 추천
st.subheader("📋 오늘의 매매 추천")
# 기준 종가 날짜 안내
if 'basis_date' in recommendation:
    basis_date = recommendation['basis_date']
    display_date = recommendation.get('date')
    if display_date and basis_date and display_date != basis_date:
        st.caption(f"오늘({display_date}) 기준 • 가격 계산은 전 거래일 종가({basis_date}) 기준")
    elif basis_date:
        st.caption(f"가격 계산 기준: {basis_date} 종가")

col1, col2 = st.columns(2)

with col1:
    st.subheader("🟢 매수 추천")
    available_cash = recommendation['portfolio']['available_cash']
    st.metric("💵 잔여 예수금", f"${available_cash:,.0f}")
    
    if recommendation['can_buy']:
        st.success(f"✅ 매수 추천: {recommendation['next_buy_round']}회차")
        st.info(f"💰 매수가: ${recommendation['buy_price']:.2f} (LOC 주문)")
        st.info(f"💵 매수금액: ${recommendation['next_buy_amount']:,.0f}")
        shares = int(recommendation['next_buy_amount'] / recommendation['buy_price'])
        st.info(f"📦 매수주식수: {shares}주")
        
        # 예수금 부족 시 안내
        if available_cash < recommendation['next_buy_amount']:
            possible_shares = int(available_cash / recommendation['buy_price'])
            possible_amount = possible_shares * recommendation['buy_price']
            st.warning(f"⚠️ 예수금 부족: 목표 금액 ${recommendation['next_buy_amount']:,.0f} 대비 예수금 ${available_cash:,.0f} 부족")
            st.info(f"💡 가능한 매수: {possible_shares}주 (약 ${possible_amount:,.0f})")
        
        # 장중 주문 가이드
        current_price = recommendation.get('soxl_current_price')
        if current_price:
            if current_price >= recommendation['buy_price']:
                st.caption("현재가가 매수가 이상입니다. 즉시 체결 원하면 지정가/시장가 고려")
            else:
                st.caption("현재가가 매수가 미만입니다. 당일 유효(DAY) 지정가로 매수가를 걸어두면 터치 시 체결")
    else:
        if st.session_state.trader.current_round > st.session_state.trader.get_current_config()["split_count"]:
            st.warning("🔴 매수 불가: 모든 분할매수 완료")
        else:
            st.warning("🔴 매수 불가: 시드 부족")
            if available_cash > 0:
                st.info(f"💡 잔여 예수금: ${available_cash:,.0f} (목표 금액 ${recommendation['next_buy_amount']:,.0f} 미만)")

with col2:
    st.subheader("🔴 매도 추천")
    if recommendation['sell_recommendations']:
        st.success(f"✅ 매도 추천: {len(recommendation['sell_recommendations'])}건")
        for sell_info in recommendation['sell_recommendations']:
            pos = sell_info['position']
            buy_date = pos.get('buy_date')
            if isinstance(buy_date, pd.Timestamp):
                buy_date_str = buy_date.strftime('%Y-%m-%d')
                buy_date_dt = buy_date.to_pydatetime() if hasattr(buy_date, 'to_pydatetime') else datetime.combine(buy_date.date(), datetime.min.time())
            elif isinstance(buy_date, datetime):
                buy_date_str = buy_date.strftime('%Y-%m-%d')
                buy_date_dt = buy_date
            else:
                buy_date_str = str(buy_date) if buy_date else "-"
                buy_date_dt = None
            
            buy_price = pos.get('buy_price')
            buy_price_text = f"${buy_price:.2f}" if isinstance(buy_price, (int, float)) else "-"
            mode = pos.get('mode', 'SF')
            mode_name = "안전모드" if mode == "SF" else "공세모드"
            
            # 손절 예정일 계산
            config = st.session_state.trader.sf_config if mode == "SF" else st.session_state.trader.ag_config
            stop_loss_date = ""
            if buy_date_dt:
                stop_loss_date = st.session_state.trader.calculate_stop_loss_date(buy_date_dt, config['max_hold_days'])
            
            st.info(f"📦 {pos['round']}회차 매도: {pos['shares']}주 @ ${sell_info['sell_price']:.2f}")
            st.caption(f"매수체결일: {buy_date_str} | 매수가: {buy_price_text}")
            st.caption(f"모드: {mode} ({mode_name}) | 손절예정일: {stop_loss_date if stop_loss_date else '-'}")
            st.caption(f"매도 사유: {sell_info['reason']}")
    else:
        # 보유 포지션이 있으면 매도 목표가 안내
        if st.session_state.trader.positions:
            st.warning("📋 보유 포지션이 있습니다. 매도 목표가를 확인하세요:")
            for pos in st.session_state.trader.positions:
                config = st.session_state.trader.sf_config if pos['mode'] == "SF" else st.session_state.trader.ag_config
                target_sell_price = pos['buy_price'] * (1 + config['sell_threshold'] / 100)
                current_price = recommendation['soxl_current_price']
                price_diff = target_sell_price - current_price
                price_diff_pct = (price_diff / current_price) * 100
                
                buy_date = pos.get('buy_date')
                if isinstance(buy_date, pd.Timestamp):
                    buy_date_str = buy_date.strftime('%Y-%m-%d')
                    buy_date_dt = buy_date.to_pydatetime() if hasattr(buy_date, 'to_pydatetime') else datetime.combine(buy_date.date(), datetime.min.time())
                elif isinstance(buy_date, datetime):
                    buy_date_str = buy_date.strftime('%Y-%m-%d')
                    buy_date_dt = buy_date
                else:
                    buy_date_str = str(buy_date) if buy_date else "-"
                    buy_date_dt = None
                
                mode = pos.get('mode', 'SF')
                mode_name = "안전모드" if mode == "SF" else "공세모드"
                
                # 손절 예정일 계산
                stop_loss_date = ""
                if buy_date_dt:
                    stop_loss_date = st.session_state.trader.calculate_stop_loss_date(buy_date_dt, config['max_hold_days'])
                
                st.info(f"📦 {pos['round']}회차: 목표가 ${target_sell_price:.2f} (현재 ${current_price:.2f}, 목표까지 {price_diff_pct:+.1f}%)")
                st.caption(f"매수체결일: {buy_date_str} | 매수가: ${pos['buy_price']:.2f} | 보유: {pos['shares']}주")
                st.caption(f"모드: {mode} ({mode_name}) | 손절예정일: {stop_loss_date if stop_loss_date else '-'}")
        else:
            st.info("🟡 매도 추천 없음")

# 포트폴리오 현황
st.subheader("💼 포트폴리오 현황")

portfolio = recommendation['portfolio']

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("📦 보유 포지션", f"{portfolio['positions_count']}개")

with col2:
    st.metric("💰 투자원금", f"${portfolio['total_invested']:,.0f}")

with col3:
    unrealized_pnl_rate = (portfolio['unrealized_pnl']/portfolio['total_invested']*100) if portfolio['total_invested'] > 0 else 0
    st.metric(
        "📈 평가손익", 
        f"${portfolio['unrealized_pnl']:,.0f}",
        f"{unrealized_pnl_rate:+.2f}%"
    )

with col4:
    st.metric("💵 총 자산", f"${portfolio['total_portfolio_value']:,.0f}")

# 보유 포지션 상세
if st.session_state.trader.positions:
    st.subheader("📊 보유 포지션 상세")
    
    positions_data = []
    for pos in st.session_state.trader.positions:
        today_for_hold_days = datetime.now()
        hold_days = (today_for_hold_days - pos['buy_date']).days
        current_value = pos['shares'] * recommendation['soxl_current_price']
        pnl = current_value - pos['amount']
        pnl_rate = (pnl / pos['amount']) * 100
        
        # 매수체결일 포맷팅
        buy_date = pos['buy_date']
        if isinstance(buy_date, pd.Timestamp):
            buy_date_str = buy_date.strftime('%Y-%m-%d')
        elif isinstance(buy_date, datetime):
            buy_date_str = buy_date.strftime('%Y-%m-%d')
        else:
            buy_date_str = str(buy_date)
        
        # 모드 정보
        mode = pos.get('mode', 'SF')
        mode_name = "안전모드(SF)" if mode == "SF" else "공세모드(AG)"
        
        # 매도 목표가 계산
        position_config = st.session_state.trader.sf_config if mode == "SF" else st.session_state.trader.ag_config
        target_sell_price = pos['buy_price'] * (1 + position_config['sell_threshold'] / 100)
        
        positions_data.append({
            "회차": pos['round'],
            "매수체결일": buy_date_str,
            "모드": mode_name,
            "주식수": pos['shares'],
            "매수가": f"${pos['buy_price']:.2f}",
            "매도목표가": f"${target_sell_price:.2f}",
            "보유일": f"{hold_days}일",
            "평가금액": f"${current_value:,.0f}",
            "손익": f"${pnl:,.0f}",
            "수익률": f"{pnl_rate:+.2f}%"
        })
    
    df_positions = pd.DataFrame(positions_data)
    st.dataframe(df_positions, use_container_width=True)

