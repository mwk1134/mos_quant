import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import sys
import io
from contextlib import redirect_stdout
import plotly.graph_objects as go

# Force redeploy - version 1.1
import plotly.express as px
from plotly.subplots import make_subplots

# 기존 SOXLQuantTrader 클래스 import
from soxl_quant_system import SOXLQuantTrader

# 페이지 설정
st.set_page_config(
    page_title="SOXL 퀀트투자 시스템",
    page_icon="📈",
    layout="wide"
)

# 커스텀 CSS - 모바일 최적화
st.markdown("""
<style>
    /* 메인 헤더 */
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 1.5rem;
    }
    
    /* 모바일에서 헤더 크기 조정 */
    @media (max-width: 768px) {
        .main-header {
            font-size: 1.5rem;
            margin-bottom: 1rem;
        }
    }
    
    .metric-card {
        background-color: #f0f2f6;
        padding: 0.8rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1f77b4;
        margin-bottom: 0.5rem;
    }
    
    .success-card {
        background-color: #d4edda;
        border-left-color: #28a745;
    }
    
    .warning-card {
        background-color: #fff3cd;
        border-left-color: #ffc107;
    }
    
    .danger-card {
        background-color: #f8d7da;
        border-left-color: #dc3545;
    }
    
    .mode-sf {
        color: #28a745;
        font-weight: bold;
    }
    
    .mode-ag {
        color: #ff8c00;
        font-weight: bold;
    }
    
    /* 모바일 설정 패널 */
    .mobile-settings-panel {
        margin-bottom: 1rem;
    }
    
    /* 모바일에서 빈 입력 필드 숨기기 */
    @media (max-width: 768px) {
        /* number_input의 빈 라벨 숨기기 */
        .stNumberInput > div > div > div > label {
            display: none !important;
        }
        
        /* date_input의 빈 라벨 숨기기 */
        .stDateInput > div > div > div > label {
            display: none !important;
        }
        
        /* 빈 div 요소들 숨기기 */
        div[data-testid="stNumberInput"] > div > div > div:empty,
        div[data-testid="stDateInput"] > div > div > div:empty {
            display: none !important;
        }
    }
    
    /* 사이드바 완전히 숨기기 (모든 화면) */
    [data-testid="stSidebar"] {
        display: none !important;
    }
    
    /* 메인 컨텐츠 전체 너비 사용 */
    .main .block-container {
        padding-left: 1rem;
        padding-right: 1rem;
        max-width: 100%;
    }
    
    /* 모바일 최적화 */
    @media (max-width: 768px) {
        /* 메트릭 카드 크기 조정 */
        .metric-card {
            padding: 0.6rem;
            font-size: 0.9rem;
        }
        
        /* 버튼 크기 조정 */
        .stButton > button {
            width: 100%;
            height: 3rem;
            font-size: 1rem;
        }
        
        /* 탭 크기 조정 */
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.5rem;
        }
        
        .stTabs [data-baseweb="tab"] {
            height: 2.5rem;
            font-size: 0.9rem;
        }
        
        /* 데이터프레임 스크롤 최적화 */
        .stDataFrame {
            font-size: 0.8rem;
        }
        
        /* 메인 컨텐츠 전체 너비 사용 */
        .main .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
        
        /* 로딩 스피너 최적화 */
        .stSpinner {
            margin: 2rem auto;
        }
        
        /* 폼 최적화 */
        .stForm {
            margin-bottom: 1rem;
        }
    }
    
    /* 매우 작은 화면 (스마트폰 세로) */
    @media (max-width: 480px) {
        .main-header {
            font-size: 1.2rem;
        }
        
        .metric-card {
            padding: 0.4rem;
            font-size: 0.8rem;
        }
        
        .stTabs [data-baseweb="tab"] {
            height: 2rem;
            font-size: 0.8rem;
            padding: 0.2rem 0.4rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
if 'trader' not in st.session_state:
    st.session_state.trader = None
if 'initial_capital' not in st.session_state:
    st.session_state.initial_capital = 9000
if 'session_start_date' not in st.session_state:
    st.session_state.session_start_date = "2025-08-27"  # 기본값 설정
if 'test_today_override' not in st.session_state:
    st.session_state.test_today_override = datetime.now().strftime('%Y-%m-%d')  # 초기값: 오늘 날짜
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

# 배포 테스트 - 버전 1.4
import time
current_time = int(time.time())
st.sidebar.success("🚀 앱 버전 1.4 로드됨!")
st.sidebar.info(f"📅 로드 시간: {current_time}")
st.sidebar.info("💡 캐시 문제 시 Ctrl+F5로 강제 새로고침")

def login_page():
    """로그인 페이지 - 모바일 최적화"""
    # 간단한 헤더
    st.markdown("# 🔐 MOSxMOS 퀀트투자 시스템")
    st.markdown("### 로그인하여 시스템에 접속하세요")
    
    with st.form("login_form"):
        st.markdown("### 🔑 로그인")
        
        username = st.text_input("사용자 ID", placeholder="사용자 ID를 입력하세요")
        password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
        
        submitted = st.form_submit_button("로그인", use_container_width=True)
        
        if submitted:
            if username == "mosmos" and password == "mosmos!":
                st.session_state.authenticated = True
                st.success("✅ 로그인 성공!")
                st.rerun()
            else:
                st.error("❌ 잘못된 사용자 ID 또는 비밀번호입니다.")
    

def initialize_trader():
    """트레이더 초기화 - 오류 처리 강화"""
    if st.session_state.trader is None:
        try:
            with st.spinner('MOS 퀀트투자 시스템 초기화 중...'):
                st.session_state.trader = SOXLQuantTrader(st.session_state.initial_capital)
                if st.session_state.test_today_override:
                    st.session_state.trader.set_test_today(st.session_state.test_today_override)
        except Exception as e:
            st.error(f"시스템 초기화 실패: {str(e)}")
            st.info("페이지를 새로고침해주세요.")
            if st.button("🔄 새로고침"):
                st.rerun()

def show_mobile_settings():
    """모바일용 설정 패널"""
    st.markdown("""
    <div class="mobile-settings-panel">
    """, unsafe_allow_html=True)
    
    # 투자원금 설정
    initial_capital = st.number_input(
        "💰 초기 투자금 (달러)",
        min_value=1000.0,
        max_value=1000000.0,
        value=float(st.session_state.initial_capital),
        step=1000.0,
        format="%.0f",
        key="mobile_capital"
    )
    
    if initial_capital != st.session_state.initial_capital:
        st.session_state.initial_capital = initial_capital
        st.session_state.trader = None  # 트레이더 재초기화
        if st.session_state.trader:
            st.session_state.trader.clear_cache()  # 캐시 초기화
        st.rerun()  # 즉시 새로고침
    
    # 시작일 설정
    # session_state에 값이 있으면 사용, 없으면 기본값
    default_start_date = datetime.strptime(st.session_state.session_start_date, '%Y-%m-%d') if st.session_state.session_start_date else datetime(2025, 8, 27)
    
    session_start_date = st.date_input(
        "📅 투자 시작일",
        value=default_start_date,
        max_value=datetime.now(),
        key="mobile_start_date"
    )
    
    new_start_date = session_start_date.strftime('%Y-%m-%d')
    if new_start_date != st.session_state.session_start_date:
        st.session_state.session_start_date = new_start_date
        st.session_state.trader = None  # 트레이더 재초기화
        if st.session_state.trader:
            st.session_state.trader.clear_cache()  # 캐시 초기화
        st.rerun()  # 즉시 새로고침
    
    # 테스트 날짜 설정
    with st.expander("🧪 테스트 설정"):
        st.info("💡 기본값은 오늘 날짜입니다. 과거 날짜를 선택하여 백테스팅할 수 있습니다.")
        
        # session_state에 저장된 값을 value로 사용하여 유지
        default_test_date = datetime.strptime(st.session_state.test_today_override, '%Y-%m-%d').date()
        
        test_today = st.date_input(
            "오늘 날짜 강제 변경",
            value=default_test_date,
            help="이 날짜를 '오늘'로 간주하여 시뮬레이션합니다",
            key="mobile_test_date"
        )
        
        # 테스트 날짜 업데이트 - 값이 변경되었을 때만
        new_test_date = test_today.strftime('%Y-%m-%d') if test_today else None
        
        if new_test_date and new_test_date != st.session_state.test_today_override:
            st.session_state.test_today_override = new_test_date
            st.session_state.trader = None  # 트레이더 재초기화
            st.rerun()
    
    # 시스템 상태와 로그아웃
    col1, col2 = st.columns([3, 1])
    
    with col1:
        if st.session_state.trader:
            st.success("✅ 준비 완료")
            st.caption(f"💰 ${st.session_state.initial_capital:,.0f}")
            st.caption(f"📅 {st.session_state.session_start_date}")
        else:
            st.warning("⚠️ 초기화 필요")
    
    with col2:
        if st.button("🚪 로그아웃", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.trader = None
            st.rerun()
    
    # 설정 변경 안내
    if st.session_state.initial_capital != 9000 or st.session_state.session_start_date != "2025-08-27":
        st.info("💡 설정이 변경되었습니다. 대시보드가 업데이트됩니다.")
    
    st.markdown("""
    </div>
    """, unsafe_allow_html=True)

def main():
    try:
        # 로그인 체크
        if not st.session_state.authenticated:
            login_page()
            return
    except Exception as e:
        st.error(f"페이지 로딩 오류: {str(e)}")
        if st.button("🔄 페이지 새로고침"):
            st.rerun()
        return
    
    # 메인 헤더
    st.markdown('<div class="main-header">📈 SOXL 퀀트투자 시스템</div>', unsafe_allow_html=True)
    
    # 설정 패널 (모든 화면)
    show_mobile_settings()
    
    # 트레이더 초기화
    initialize_trader()
    
    # 메인 네비게이션 - 모바일 친화적
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏠 대시보드", 
        "📊 오늘의 매매", 
        "💼 포트폴리오", 
        "📈 백테스팅", 
        "⚙️ 설정"
    ])
    
    with tab1:
        show_dashboard()
    
    with tab2:
        show_daily_recommendation()
    
    with tab3:
        show_portfolio()
    
    with tab4:
        show_backtest()
    
    with tab5:
        show_advanced_settings()

def show_dashboard():
    """대시보드 페이지"""
    st.header("🏠 대시보드")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행하여 현재 상태 업데이트
    start_date = st.session_state.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    with st.spinner('현재 상태 계산 중...'):
        # 캐시 클리어하여 항상 최신 상태로 시뮬레이션
        st.session_state.trader.clear_cache()
        
        # 디버깅: 최신 거래일 확인
        latest_trading_day = st.session_state.trader.get_latest_trading_day()
        st.info(f"🔄 시뮬레이션 범위: {start_date} ~ {latest_trading_day.strftime('%Y-%m-%d')}")
        
        # 10/10일 매수 조건 확인을 위해 quiet=False로 변경
        sim_result = st.session_state.trader.simulate_from_start_to_today(start_date, quiet=False)
        st.session_state.sim_result = sim_result  # 로그 표시를 위해 저장
        if "error" in sim_result:
            st.error(f"시뮬레이션 실패: {sim_result['error']}")
            return
    
    # 현재 상태 요약
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "💰 투자원금",
            f"${st.session_state.initial_capital:,.0f}",
            delta=None
        )
    
    with col2:
        if st.session_state.trader.current_mode:
            mode_display = "안전모드" if st.session_state.trader.current_mode == "SF" else "공세모드"
            st.metric(
                "🎯 현재 모드",
                mode_display
            )
        else:
            st.metric("🎯 현재 모드", "미설정")
    
    with col3:
        st.metric(
            "📦 보유 포지션",
            f"{len(st.session_state.trader.positions)}개"
        )
    
    with col4:
        st.metric(
            "💵 현금잔고",
            f"${st.session_state.trader.available_cash:,.0f}"
        )
    
    # 최근 거래일 정보
    st.subheader("📅 최근 시장 정보")
    latest_trading_day = st.session_state.trader.get_latest_trading_day()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info(f"📅 최근 거래일: {latest_trading_day.strftime('%Y-%m-%d (%A)')}")
    
    with col2:
        is_market_closed = st.session_state.trader.is_market_closed(datetime.now())
        if is_market_closed:
            st.warning("🚫 현재 시장 휴장")
        else:
            st.success("✅ 현재 시장 개장")
    
    # 10/10일 매수 조건 확인 정보 표시
    if latest_trading_day.strftime('%Y-%m-%d') == '2025-10-10':
        st.subheader("🔍 10/10일 매수 조건 확인")
        
        # SOXL 데이터 가져오기
        soxl_data = st.session_state.trader.get_stock_data("SOXL", "1mo")
        if soxl_data is not None and len(soxl_data) > 0:
            # 디버깅: 데이터 범위 확인
            st.info(f"📊 SOXL 데이터 범위: {soxl_data.index[0].strftime('%Y-%m-%d')} ~ {soxl_data.index[-1].strftime('%Y-%m-%d')}")
            st.info(f"📊 총 데이터 수: {len(soxl_data)}개")
            
            # 최근 5개 날짜 표시
            recent_dates = soxl_data.index[-5:].strftime('%Y-%m-%d').tolist()
            st.info(f"📊 최근 5개 거래일: {', '.join(recent_dates)}")
            
            # 10/9일(전일)과 10/10일 데이터 찾기
            prev_date_str = '2025-10-09'
            target_date_str = '2025-10-10'
            prev_date = pd.to_datetime(prev_date_str)
            target_date = pd.to_datetime(target_date_str)
            
            # 전일(10/9) 종가 찾기
            prev_close = None
            if prev_date in soxl_data.index:
                prev_close = soxl_data.loc[prev_date, 'Close']
            else:
                for idx in soxl_data.index:
                    if idx.strftime('%Y-%m-%d') == prev_date_str:
                        prev_close = soxl_data.loc[idx, 'Close']
                        break
            
            # 당일(10/10) 종가 찾기
            daily_close = None
            if target_date in soxl_data.index:
                daily_close = soxl_data.loc[target_date, 'Close']
            else:
                for idx in soxl_data.index:
                    if idx.strftime('%Y-%m-%d') == target_date_str:
                        daily_close = soxl_data.loc[idx, 'Close']
                        break
            
            if prev_close is not None and daily_close is not None:
                # 현재 모드와 설정 가져오기
                current_config = st.session_state.trader.sf_config if st.session_state.trader.current_mode == "SF" else st.session_state.trader.ag_config
                # 매수가는 전일 종가 기준으로 계산
                buy_price = prev_close * (1 + current_config["buy_threshold"] / 100)
                
                # 매수 조건 확인
                can_buy = st.session_state.trader.can_buy_next_round()
                buy_condition = buy_price > daily_close
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.info(f"📊 10/9일 종가: ${prev_close:.2f}")
                with col2:
                    st.info(f"📊 10/10일 종가: ${daily_close:.2f}")
                with col3:
                    st.info(f"💰 매수가: ${buy_price:.2f}")
                with col4:
                    if buy_condition:
                        st.success(f"✅ 매수조건: True")
                    else:
                        st.error(f"❌ 매수조건: False")
                
                # 추가 정보
                st.info(f"🔍 매수 가능 여부: {can_buy}")
                st.info(f"📦 현재 회차: {st.session_state.trader.current_round}")
                st.info(f"💵 현금잔고: ${st.session_state.trader.available_cash:,.0f}")
            elif prev_close is None:
                st.warning("⚠️ 10/9일(전일) 데이터를 찾을 수 없습니다.")
            elif daily_close is None:
                st.warning("⚠️ 10/10일(당일) 데이터를 찾을 수 없습니다.")
    
    # 백테스트 로그 표시 (10/10일 관련만)
    if hasattr(st.session_state, 'sim_result') and st.session_state.sim_result:
        if 'logs' in st.session_state.sim_result and st.session_state.sim_result['logs']:
            st.subheader("📋 백테스트 실행 로그 ")
            
            # 10/10일 관련 로그만 필터링
            logs_1010 = [log for log in st.session_state.sim_result['logs'] if '2025-10-10' in log or '2025-10-09' in log]
            
            if logs_1010:
                with st.expander("🔍 10/10일 매수 실행 로그", expanded=True):
                    for log in logs_1010:
                        st.text(log)
            else:
                # 10/10일 로그가 없으면 최근 5개만 표시
                recent_logs = st.session_state.sim_result['logs'][-5:]
                with st.expander("🔍 최근 백테스트 로그 (최근 5개)", expanded=False):
                    for log in recent_logs:
                        st.text(log)

def show_daily_recommendation():
    """일일 매매 추천 페이지"""
    st.header("📊 일일 매매 추천")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행 - 캐시를 클리어하여 최신 상태 반영
    start_date = st.session_state.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    with st.spinner('현재 상태 계산 중...'):
        # 캐시 클리어하여 항상 최신 상태로 시뮬레이션
        st.session_state.trader.clear_cache()
        
        # 디버깅: 최신 거래일 확인
        latest_trading_day = st.session_state.trader.get_latest_trading_day()
        st.info(f"🔄 일일 추천 시뮬레이션 범위: {start_date} ~ {latest_trading_day.strftime('%Y-%m-%d')}")
        
        # 일일 추천 생성 (내부에서 상태 업데이트 수행)
        recommendation = st.session_state.trader.get_daily_recommendation()
    
    if "error" in recommendation:
        st.error(f"추천 생성 실패: {recommendation['error']}")
        return
    
    # 기본 정보 - 모바일 최적화
    # 모바일에서는 2x2 그리드, 데스크톱에서는 1x4 그리드
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric("📅 날짜", recommendation['date'])
        mode_name = "안전모드" if recommendation['mode'] == "SF" else "공세모드"
        mode_class = "mode-sf" if recommendation['mode'] == "SF" else "mode-ag"
        st.markdown(f"<div class='{mode_class}'>🎯 모드: {recommendation['mode']} ({mode_name})</div>", unsafe_allow_html=True)
    
    with col2:
        st.metric("📊 QQQ 주간 RSI", f"{recommendation['qqq_weekly_rsi']:.2f}")
        st.metric("💰 SOXL 현재가", f"${recommendation['soxl_current_price']:.2f}")
    
    # 매매 추천
    st.subheader("📋 오늘의 매매 추천")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🟢 매수 추천")
        if recommendation['can_buy']:
            st.success(f"✅ 매수 추천: {recommendation['next_buy_round']}회차")
            st.info(f"💰 매수가: ${recommendation['buy_price']:.2f} (LOC 주문)")
            st.info(f"💵 매수금액: ${recommendation['next_buy_amount']:,.0f}")
            shares = int(recommendation['next_buy_amount'] / recommendation['buy_price'])
            st.info(f"📦 매수주식수: {shares}주")
        else:
            if st.session_state.trader.current_round > st.session_state.trader.get_current_config()["split_count"]:
                st.warning("🔴 매수 불가: 모든 분할매수 완료")
            else:
                st.warning("🔴 매수 불가: 시드 부족")
    
    with col2:
        st.subheader("🔴 매도 추천")
        if recommendation['sell_recommendations']:
            st.success(f"✅ 매도 추천: {len(recommendation['sell_recommendations'])}건")
            for sell_info in recommendation['sell_recommendations']:
                pos = sell_info['position']
                st.info(f"📦 {pos['round']}회차 매도: {pos['shares']}주 @ ${sell_info['sell_price']:.2f}")
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
                    
                    # 매도 목표가까지 남은 상승률을 명확하게 표시
                    st.info(f"📦 {pos['round']}회차: 목표가 ${target_sell_price:.2f} (현재 ${current_price:.2f}, 목표까지 {price_diff_pct:+.1f}%)")
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
            hold_days = (datetime.now() - pos['buy_date']).days
            current_value = pos['shares'] * recommendation['soxl_current_price']
            pnl = current_value - pos['amount']
            pnl_rate = (pnl / pos['amount']) * 100
            
            positions_data.append({
                "회차": pos['round'],
                "주식수": pos['shares'],
                "매수가": f"${pos['buy_price']:.2f}",
                "보유일": f"{hold_days}일",
                "평가금액": f"${current_value:,.0f}",
                "손익": f"${pnl:,.0f}",
                "수익률": f"{pnl_rate:+.2f}%"
            })
        
        df_positions = pd.DataFrame(positions_data)
        st.dataframe(df_positions, use_container_width=True)

def show_portfolio():
    """포트폴리오 페이지"""
    st.header("💼 포트폴리오 현황")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행 - 투자시작일 기준으로 재계산
    start_date = st.session_state.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    with st.spinner('포트폴리오 현황 계산 중...'):
        # 캐시 클리어하여 항상 최신 상태로 시뮬레이션
        st.session_state.trader.clear_cache()
        
        # 디버깅: 최신 거래일 확인
        latest_trading_day = st.session_state.trader.get_latest_trading_day()
        st.info(f"🔄 포트폴리오 시뮬레이션 범위: {start_date} ~ {latest_trading_day.strftime('%Y-%m-%d')}")
        
        sim_result = st.session_state.trader.simulate_from_start_to_today(start_date, quiet=True)
            
        if "error" in sim_result:
            st.error(f"시뮬레이션 실패: {sim_result['error']}")
            return
    
    # 포트폴리오 요약
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("💰 초기자본", f"${st.session_state.initial_capital:,.0f}")
    
    with col2:
        total_invested = sum([pos["amount"] for pos in st.session_state.trader.positions])
        st.metric("💵 투자원금", f"${total_invested:,.0f}")
    
    with col3:
        st.metric("💵 현금잔고", f"${st.session_state.trader.available_cash:,.0f}")
    
    with col4:
        # SOXL 현재가 가져오기
        soxl_data = st.session_state.trader.get_stock_data("SOXL", "1mo")
        if soxl_data is not None and len(soxl_data) > 0:
            current_price = soxl_data.iloc[-1]['Close']
            total_position_value = sum([pos["shares"] * current_price for pos in st.session_state.trader.positions])
            total_assets = st.session_state.trader.available_cash + total_position_value
            total_return = ((total_assets - st.session_state.initial_capital) / st.session_state.initial_capital) * 100
            
            st.metric(
                "📈 총 수익률",
                f"{total_return:+.2f}%",
                f"${total_assets - st.session_state.initial_capital:+.0f}"
            )
    
    # 보유 포지션
    if st.session_state.trader.positions:
        st.subheader("📦 보유 포지션")
        
        positions_data = []
        for pos in st.session_state.trader.positions:
            hold_days = (datetime.now() - pos['buy_date']).days
            current_value = pos['shares'] * current_price if 'current_price' in locals() else pos['amount']
            pnl = current_value - pos['amount']
            pnl_rate = (pnl / pos['amount']) * 100
            
            positions_data.append({
                "회차": pos['round'],
                "매수일": pos['buy_date'].strftime('%Y-%m-%d'),
                "주식수": pos['shares'],
                "매수가": f"${pos['buy_price']:.2f}",
                "보유일": f"{hold_days}일",
                "현재가": f"${current_price:.2f}" if 'current_price' in locals() else "N/A",
                "평가금액": f"${current_value:,.0f}",
                "손익": f"${pnl:,.0f}",
                "수익률": f"{pnl_rate:+.2f}%",
                "모드": pos.get('mode', 'N/A')
            })
        
        df_positions = pd.DataFrame(positions_data)
        # 모바일에서 더 나은 표시를 위해 컬럼 너비 조정
        st.dataframe(
            df_positions, 
            use_container_width=True,
            hide_index=True
        )
        
        # 포지션별 수익률 차트 - 모바일 최적화
        if len(positions_data) > 1:
            fig = px.bar(
                df_positions, 
                x='회차', 
                y='수익률',
                title='포지션별 수익률',
                color='수익률',
                color_continuous_scale=['red', 'yellow', 'green']
            )
            fig.update_layout(
                yaxis_title="수익률 (%)",
                height=400  # 모바일에서 적절한 높이
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("현재 보유 포지션이 없습니다.")
    
    # 자산 구성 비율
    if st.session_state.trader.positions and 'current_price' in locals():
        st.subheader("📊 자산 구성")
        
        total_position_value = sum([pos["shares"] * current_price for pos in st.session_state.trader.positions])
        total_assets = st.session_state.trader.available_cash + total_position_value
        
        # 파이 차트 데이터
        labels = ['현금', '주식']
        values = [st.session_state.trader.available_cash, total_position_value]
        colors = ['#FFA500', '#32CD32']
        
        fig = go.Figure(data=[go.Pie(labels=labels, values=values, marker_colors=colors)])
        fig.update_layout(title="자산 구성 비율")
        st.plotly_chart(fig, use_container_width=True)

def show_backtest():
    """백테스팅 페이지"""
    st.header("📈 백테스팅")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 백테스팅 설정
    col1, col2 = st.columns(2)
    
    # 세션 상태 초기화
    if 'backtest_start_date' not in st.session_state:
        st.session_state.backtest_start_date = datetime.now() - timedelta(days=365)
    if 'backtest_end_date' not in st.session_state:
        st.session_state.backtest_end_date = datetime.now()
    
    with col1:
        start_date = st.date_input(
            "시작 날짜",
            value=st.session_state.backtest_start_date,
            max_value=datetime.now(),
            key="backtest_start_date_input"
        )
        if start_date != st.session_state.backtest_start_date:
            st.session_state.backtest_start_date = start_date
    
    with col2:
        end_date = st.date_input(
            "종료 날짜",
            value=st.session_state.backtest_end_date,
            max_value=datetime.now(),
            key="backtest_end_date_input"
        )
        if end_date != st.session_state.backtest_end_date:
            st.session_state.backtest_end_date = end_date
    
    # 백테스팅 실행
    if st.button("🚀 백테스팅 실행", use_container_width=True):
        with st.spinner('백테스팅 실행 중...'):
            backtest_result = st.session_state.trader.run_backtest(
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
        
        if "error" in backtest_result:
            st.error(f"백테스팅 실패: {backtest_result['error']}")
            return
        
        # 결과 표시
        st.success("✅ 백테스팅 완료!")
        
        # 요약 결과
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("💰 초기자본", f"${backtest_result['initial_capital']:,.0f}")
        
        with col2:
            st.metric("💰 최종자산", f"${backtest_result['final_value']:,.0f}")
        
        with col3:
            st.metric("📈 총수익률", f"{backtest_result['total_return']:+.2f}%")
        
        with col4:
            st.metric("📦 최종포지션", f"{backtest_result['final_positions']}개")
        
        # MDD 계산
        mdd_info = st.session_state.trader.calculate_mdd(backtest_result['daily_records'])
        
        st.subheader("⚠️ 리스크 지표")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("📉 MDD", f"{mdd_info.get('mdd_percent', 0.0):.2f}%")
        
        with col2:
            st.metric("📅 MDD 발생일", mdd_info.get('mdd_date', ''))
        
        with col3:
            st.metric("💰 최저자산", f"${mdd_info.get('mdd_value', 0.0):,.0f}")
        
        # 자산 변화 차트
        if backtest_result['daily_records']:
            st.subheader("📊 자산 변화")
            
            df_backtest = pd.DataFrame(backtest_result['daily_records'])
            
            # 날짜 파싱 - ISO 형식 (YYYY-MM-DD)으로 간단하게 처리
            df_backtest['date'] = pd.to_datetime(df_backtest['date'], errors='coerce')
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_backtest['date'],
                y=df_backtest['total_assets'],
                mode='lines',
                name='총 자산',
                line=dict(color='blue', width=2)
            ))
            
            fig.update_layout(
                title="자산 변화 추이",
                xaxis_title="날짜",
                yaxis_title="자산 ($)",
                hovermode='x unified'
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
            # 상세 거래 내역
            st.subheader("📋 상세 거래 내역")
            
            # 매수/매도 내역만 필터링
            df_trades = df_backtest[
                (df_backtest['buy_executed_price'] > 0) | 
                (df_backtest['sell_executed_price'] > 0)
            ].copy()
            
            if not df_trades.empty:
                # 필요한 컬럼만 선택
                display_columns = [
                    'date', 'week', 'rsi', 'mode', 'current_round',
                    'buy_executed_price', 'buy_quantity', 'buy_amount',
                    'sell_executed_price', 'realized_pnl'
                ]
                
                df_display = df_trades[display_columns].copy()
                df_display.columns = [
                    '날짜', '주차', 'RSI', '모드', '회차',
                    '매수가', '수량', '매수금액',
                    '매도가', '실현손익'
                ]
                
                # 날짜 컬럼을 문자열로 변환하여 표시
                df_display['날짜'] = df_display['날짜'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) else '')
                
                # 숫자 포맷팅
                for col in ['매수가', '매도가']:
                    df_display[col] = df_display[col].apply(lambda x: f"${x:.2f}" if x > 0 else "")
                
                for col in ['매수금액', '실현손익']:
                    df_display[col] = df_display[col].apply(lambda x: f"${x:,.0f}" if x != 0 else "")
                
                st.dataframe(df_display, use_container_width=True)
            else:
                st.info("거래 내역이 없습니다.")
        
        # 엑셀 다운로드
        if st.button("📥 엑셀 파일 생성", key="generate_excel"):
            with st.spinner('엑셀 파일 생성 중...'):
                # 임시 파일명 생성
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                temp_filename = f"SOXL_백테스팅_{backtest_result['start_date']}_{timestamp}.xlsx"
                
                # 엑셀 파일 생성
                result_filename = st.session_state.trader.export_backtest_to_excel(backtest_result, temp_filename)
                
                if result_filename and os.path.exists(result_filename):
                    # 파일을 메모리로 읽기
                    with open(result_filename, 'rb') as f:
                        excel_data = f.read()
                    
                    # 임시 파일 삭제
                    try:
                        os.remove(result_filename)
                    except:
                        pass
                    
                    # 다운로드 버튼 표시
                    st.download_button(
                        label="💾 엑셀 파일 다운로드",
                        data=excel_data,
                        file_name=f"SOXL_백테스팅_{backtest_result['start_date']}_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_excel"
                    )
                    st.success("✅ 엑셀 파일이 준비되었습니다. 위 버튼을 클릭하여 다운로드하세요.")
                else:
                    st.error("❌ 엑셀 파일 생성에 실패했습니다.")

def show_advanced_settings():
    """고급 설정 페이지"""
    st.header("⚙️ 고급 설정")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # RSI 참조 데이터 관리
    st.subheader("📊 RSI 참조 데이터")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🔄 RSI 데이터 업데이트"):
            with st.spinner('RSI 데이터 업데이트 중...'):
                success = st.session_state.trader.update_rsi_reference_file()
                if success:
                    st.success("✅ RSI 데이터 업데이트 완료")
                else:
                    st.error("❌ RSI 데이터 업데이트 실패")
    
    with col2:
        if st.button("📋 RSI 데이터 상태 확인"):
            with st.spinner('RSI 데이터 상태 확인 중...'):
                is_up_to_date = st.session_state.trader.check_and_update_rsi_data()
                if is_up_to_date:
                    st.success("✅ RSI 데이터가 최신 상태입니다")
                else:
                    st.warning("⚠️ RSI 데이터 업데이트가 필요합니다")
    
    # 모드 설정 확인
    st.subheader("🎯 모드 설정")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("SF 모드 (안전모드)")
        sf_config = st.session_state.trader.sf_config
        st.json(sf_config)
    
    with col2:
        st.subheader("AG 모드 (공세모드)")
        ag_config = st.session_state.trader.ag_config
        st.json(ag_config)
    
    # 시스템 정보
    st.subheader("ℹ️ 시스템 정보")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info(f"💰 초기 투자금: ${st.session_state.initial_capital:,.0f}")
        st.info(f"💵 현재 투자원금: ${st.session_state.trader.current_investment_capital:,.0f}")
        st.caption("💡 투자원금은 10거래일마다 총자산으로 자동 갱신됩니다")
        st.info(f"📅 세션 시작일: {st.session_state.session_start_date}")
        st.info(f"🎯 현재 모드: {st.session_state.trader.current_mode}")
        st.info(f"📦 현재 회차: {st.session_state.trader.current_round}")
    
    with col2:
        latest_trading_day = st.session_state.trader.get_latest_trading_day()
        st.info(f"📅 최근 거래일: {latest_trading_day.strftime('%Y-%m-%d')}")
        st.info(f"💵 현금잔고: ${st.session_state.trader.available_cash:,.0f}")
        st.info(f"📦 보유 포지션: {len(st.session_state.trader.positions)}개")
    
    # 시스템 재시작
    st.subheader("🔄 시스템 관리")
    
    if st.button("🔄 시스템 재시작", type="secondary"):
        st.session_state.trader = None
        st.rerun()

if __name__ == "__main__":
    main()
