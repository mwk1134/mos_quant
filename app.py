import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os
import sys
import io
from contextlib import redirect_stdout
from pathlib import Path
import plotly.graph_objects as go

# Force redeploy - version 1.1
import plotly.express as px
from plotly.subplots import make_subplots

# ensure local mos_quant modules take precedence
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

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
        
        /* Metric 글씨 크기 조정 (날짜, RSI, 현재가) */
        [data-testid="stMetric"] {
            font-size: 0.85rem !important;
        }
        
        [data-testid="stMetric"] > div {
            font-size: 0.85rem !important;
        }
        
        [data-testid="stMetric"] > div > div > div {
            font-size: 0.9rem !important;
        }
        
        [data-testid="stMetric"] > div > div > div > div {
            font-size: 1.1rem !important;
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
if 'position_edits' not in st.session_state:
    st.session_state.position_edits = {}  # {position_index: {'shares': int, 'buy_price': float}}
if 'kmw_preset' not in st.session_state:
    st.session_state.kmw_preset = {
        'initial_capital': 9000.0,
        'session_start_date': "2025-08-27",
        'seed_increases': [{"date": "2025-10-21", "amount": 31000.0}],
        'position_edits': {}  # 포지션 수정 정보 저장
    }
if 'jsd_preset' not in st.session_state:
    st.session_state.jsd_preset = {
        'initial_capital': 17300.0,
        'session_start_date': "2025-10-30",
        'seed_increases': [],
        'position_edits': {}  # 포지션 수정 정보 저장
    }
if 'jeh_preset' not in st.session_state:
    st.session_state.jeh_preset = {
        'initial_capital': 2793.0,
        'session_start_date': "2025-10-30",
        'seed_increases': [],
        'position_edits': {}  # 포지션 수정 정보 저장
    }

# 배포 테스트 - 버전 1.5 - FORCE REDEPLOY
import time
current_time = int(time.time())
st.sidebar.success("🚀 앱 버전 1.5 로드됨!")
st.sidebar.info(f"📅 로드 시간: {current_time}")
st.sidebar.info("💡 캐시 문제 시 Ctrl+F5로 강제 새로고침")
st.sidebar.error("🔴 강제 재배포 테스트 중...")

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
                # 사용자 지정 파라미터 가져오기 (없으면 None = 기본값 사용)
                sf_config = st.session_state.get('sf_config')
                ag_config = st.session_state.get('ag_config')
                
                st.session_state.trader = SOXLQuantTrader(
                    initial_capital=st.session_state.initial_capital,
                    sf_config=sf_config,
                    ag_config=ag_config
                )
                if st.session_state.test_today_override:
                    st.session_state.trader.set_test_today(st.session_state.test_today_override)
                
                # 시드증액 데이터 전달
                if 'seed_increases' in st.session_state and st.session_state.seed_increases:
                    for seed in st.session_state.seed_increases:
                        st.session_state.trader.add_seed_increase(
                            seed['date'],
                            seed['amount'],
                            f"시드증액 {seed['date']}"
                        )
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
        format="%.0f"
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
    
    # 날짜 입력 + 오늘 버튼 + KMW/JEH/JSD 프리셋 버튼
    start_col1, start_col2, start_col3, start_col4, start_col5, start_col6, start_col7, start_col8 = st.columns([3, 1, 1, 1, 1, 1, 1, 1])
    with start_col1:
        session_start_date = st.date_input(
            "📅 투자 시작일",
            value=default_start_date,
            max_value=datetime.now()
        )
    with start_col2:
        if st.button("오늘", help="투자 시작일을 오늘 날짜로 설정"):
            today = datetime.now().date()
            st.session_state.session_start_date = today.strftime('%Y-%m-%d')
            st.session_state.trader = None
            st.rerun()
    with start_col3:
        if st.button("KMW", help="초기설정: 9000달러, 시작일 2025/08/27, 2025/10/21 +31,000"):
            # KMW 프리셋 불러오기
            kmw = st.session_state.kmw_preset
            st.session_state.initial_capital = kmw['initial_capital']
            st.session_state.session_start_date = kmw['session_start_date']
            st.session_state.seed_increases = kmw['seed_increases'].copy()
            
            # 저장된 포지션 수정 정보 불러오기
            if 'position_edits' in kmw and kmw['position_edits']:
                st.session_state.position_edits = kmw['position_edits'].copy()
            else:
                st.session_state.position_edits = {}
            
            # 트레이더 재초기화 후 즉시 적용
            st.session_state.trader = None
            st.success("✅ KMW 프리셋이 적용되었습니다.")
            st.rerun()
    with start_col4:
        if st.button("KMW 저장", help="현재 설정과 수정된 포지션 정보를 KMW 프리셋에 저장"):
            # 현재 설정을 KMW 프리셋에 저장
            st.session_state.kmw_preset = {
                'initial_capital': st.session_state.initial_capital,
                'session_start_date': st.session_state.session_start_date,
                'seed_increases': st.session_state.seed_increases.copy() if st.session_state.seed_increases else [],
                'position_edits': st.session_state.position_edits.copy() if 'position_edits' in st.session_state else {}
            }
            st.success("✅ KMW 프리셋이 저장되었습니다!")
    with start_col5:
        if st.button("JEH", help="초기설정: 2793달러, 시작일 2025/10/30, 시드증액 없음"):
            # JEH 프리셋 불러오기
            jeh = st.session_state.jeh_preset
            st.session_state.initial_capital = jeh['initial_capital']
            st.session_state.session_start_date = jeh['session_start_date']
            st.session_state.seed_increases = jeh['seed_increases'].copy()
            
            # 저장된 포지션 수정 정보 불러오기
            if 'position_edits' in jeh and jeh['position_edits']:
                st.session_state.position_edits = jeh['position_edits'].copy()
            else:
                st.session_state.position_edits = {}
            
            # 트레이더 재초기화 후 즉시 적용
            st.session_state.trader = None
            st.success("✅ JEH 프리셋이 적용되었습니다.")
            st.rerun()
    with start_col6:
        if st.button("JEH 저장", help="현재 설정과 수정된 포지션 정보를 JEH 프리셋에 저장"):
            # 현재 설정을 JEH 프리셋에 저장
            st.session_state.jeh_preset = {
                'initial_capital': st.session_state.initial_capital,
                'session_start_date': st.session_state.session_start_date,
                'seed_increases': st.session_state.seed_increases.copy() if st.session_state.seed_increases else [],
                'position_edits': st.session_state.position_edits.copy() if 'position_edits' in st.session_state else {}
            }
            st.success("✅ JEH 프리셋이 저장되었습니다!")
    with start_col7:
        if st.button("JSD", help="초기설정: 17300달러, 시작일 2025/10/30, 시드증액 없음"):
            # JSD 프리셋 불러오기
            jsd = st.session_state.jsd_preset
            st.session_state.initial_capital = jsd['initial_capital']
            st.session_state.session_start_date = jsd['session_start_date']
            st.session_state.seed_increases = jsd['seed_increases'].copy()
            
            # 저장된 포지션 수정 정보 불러오기
            if 'position_edits' in jsd and jsd['position_edits']:
                st.session_state.position_edits = jsd['position_edits'].copy()
            else:
                st.session_state.position_edits = {}
            
            # 트레이더 재초기화 후 즉시 적용
            st.session_state.trader = None
            st.success("✅ JSD 프리셋이 적용되었습니다.")
            st.rerun()
    with start_col8:
        if st.button("JSD 저장", help="현재 설정과 수정된 포지션 정보를 JSD 프리셋에 저장"):
            # 현재 설정을 JSD 프리셋에 저장
            st.session_state.jsd_preset = {
                'initial_capital': st.session_state.initial_capital,
                'session_start_date': st.session_state.session_start_date,
                'seed_increases': st.session_state.seed_increases.copy() if st.session_state.seed_increases else [],
                'position_edits': st.session_state.position_edits.copy() if 'position_edits' in st.session_state else {}
            }
            st.success("✅ JSD 프리셋이 저장되었습니다!")
    
    new_start_date = session_start_date.strftime('%Y-%m-%d')
    if new_start_date != st.session_state.session_start_date:
        st.session_state.session_start_date = new_start_date
        st.session_state.trader = None  # 트레이더 재초기화
        if st.session_state.trader:
            st.session_state.trader.clear_cache()  # 캐시 초기화
        st.rerun()  # 즉시 새로고침
    
    # 시드증액 설정
    st.subheader("💰 시드증액")
    
    # 시드증액 목록 표시
    if 'seed_increases' not in st.session_state:
        st.session_state.seed_increases = []
    
    if st.session_state.seed_increases:
        st.write("**등록된 시드증액/인출:**")
        for i, seed in enumerate(st.session_state.seed_increases):
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                st.write(f"📅 {seed['date']}")
            with col2:
                if seed['amount'] > 0:
                    st.write(f"💰 +${seed['amount']:,.0f}")
                else:
                    st.write(f"🔴 -${abs(seed['amount']):,.0f}")
            with col3:
                if st.button("🗑️", key=f"delete_seed_{i}"):
                    st.session_state.seed_increases.pop(i)
                    st.rerun()
    
    # 시드증액 추가
    col1, col2 = st.columns(2)
    with col1:
        seed_date = st.date_input(
            "📅 시드증액 날짜",
            value=datetime.now().date(),
            max_value=datetime.now().date(),
            key="seed_date"
        )
    with col2:
        seed_amount = st.number_input(
            "💰 증액/인출 금액 (달러)",
            min_value=-1000000.0,
            max_value=1000000.0,
            value=31000.0,
            step=1000.0,
            format="%.0f",
            help="양수: 시드증액, 음수: 시드인출",
            key="seed_amount"
        )

    if st.button("➕ 시드증액/인출 추가", use_container_width=True):
        if seed_amount != 0:
            seed_increase = {
                "date": seed_date.strftime('%Y-%m-%d'),
                "amount": seed_amount
            }
            st.session_state.seed_increases.append(seed_increase)
            st.session_state.trader = None  # 트레이더 재초기화
            if seed_amount > 0:
                st.success(f"✅ 시드증액이 추가되었습니다: {seed_increase['date']} - ${seed_amount:,.0f}")
            else:
                st.warning(f"⚠️ 시드인출이 추가되었습니다: {seed_increase['date']} - ${abs(seed_amount):,.0f}")
            st.rerun()
        else:
            st.error("❌ 금액을 입력해주세요. (0은 불가)")
    
    
    
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
    
    # 실시간 시간 표시 (한국시간)
    from datetime import datetime, timezone, timedelta
    
    # 한국시간 (UTC+9)
    korea_tz = timezone(timedelta(hours=9))
    korea_time = datetime.now(korea_tz)
    st.info(f"🕐 한국시간: {korea_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    
    # 설정 패널 (모든 화면)
    show_mobile_settings()
    
    # 트레이더 초기화
    initialize_trader()
    
    # 메인 네비게이션 - 모바일 친화적
    tab1, tab2, tab3 = st.tabs([
        "📊 오늘의 매매", 
        "📈 백테스팅", 
        "⚙️ 설정"
    ])
    
    # 백테스팅 탭으로 복원 (JavaScript 사용)
    if st.session_state.get('active_tab') == 1:
        st.markdown("""
        <script>
        function selectBacktestTab() {
            // Streamlit 탭 요소 찾기
            const tabs = document.querySelectorAll('[data-baseweb="tab"]');
            if (tabs.length > 1 && tabs[1]) {
                // 백테스팅 탭(인덱스 1) 클릭
                tabs[1].click();
                return true;
            }
            return false;
        }
        
        // 즉시 시도
        if (!selectBacktestTab()) {
            // 실패 시 DOMContentLoaded 후 재시도
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', function() {
                    setTimeout(selectBacktestTab, 100);
                });
            } else {
                // 이미 로드된 경우 약간의 지연 후 시도
                setTimeout(selectBacktestTab, 100);
            }
        }
        </script>
        """, unsafe_allow_html=True)
        # 플래그 리셋 (한 번만 실행되도록)
        st.session_state.active_tab = None
    
    with tab1:
        show_daily_recommendation()
    
    with tab2:
        show_backtest()
    
    with tab3:
        show_advanced_settings()

def show_dashboard():
    """대시보드 페이지"""
    st.header("🏠 대시보드")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행하여 현재 상태 업데이트
    # 테스트 날짜 오버라이드 고려
    today_for_calc = datetime.now()
    if st.session_state.trader and st.session_state.trader.test_today_override:
        today_for_calc = datetime.strptime(st.session_state.trader.test_today_override, '%Y-%m-%d')
    start_date = st.session_state.session_start_date or (today_for_calc - timedelta(days=365)).strftime('%Y-%m-%d')
    
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
    

def show_daily_recommendation():
    """일일 매매 추천 페이지"""
    st.header("📊 일일 매매 추천")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행 - 캐시를 클리어하여 최신 상태 반영
    # 테스트 날짜 오버라이드 고려
    today_for_calc = datetime.now()
    if st.session_state.trader and st.session_state.trader.test_today_override:
        today_for_calc = datetime.strptime(st.session_state.trader.test_today_override, '%Y-%m-%d')
    start_date = st.session_state.session_start_date or (today_for_calc - timedelta(days=365)).strftime('%Y-%m-%d')
    
    with st.spinner('현재 상태 계산 중...'):
        # 캐시 클리어하여 항상 최신 상태로 시뮬레이션
        st.session_state.trader.clear_cache()
        
        # 디버깅: 최신 거래일 확인
        latest_trading_day = st.session_state.trader.get_latest_trading_day()
        st.info(f"🔄 일일 추천 시뮬레이션 범위: {start_date} ~ {latest_trading_day.strftime('%Y-%m-%d')}")
        
        # 먼저 시뮬레이션 실행하여 트레이더 상태 업데이트
        sim_result = st.session_state.trader.simulate_from_start_to_today(start_date, quiet=True)
        if "error" in sim_result:
            st.error(f"시뮬레이션 실패: {sim_result['error']}")
            return
        
        # 시뮬레이션 후 수정된 포지션 복원
        if 'position_edits' in st.session_state and st.session_state.position_edits:
            # 수정된 포지션 정보를 회차와 매수일로 매칭하여 복원
            for pos_idx, pos in enumerate(st.session_state.trader.positions):
                buy_date_str = pos['buy_date'].strftime('%Y-%m-%d') if isinstance(pos['buy_date'], (datetime, pd.Timestamp)) else str(pos['buy_date'])
                
                # 모든 수정 정보를 확인하여 매칭 (회차와 매수일로 매칭)
                for position_key, edit_info in st.session_state.position_edits.items():
                    key_parts = position_key.split('_')
                    if len(key_parts) >= 2:
                        key_round = int(key_parts[0])
                        key_date = key_parts[1]
                        
                        # 회차와 매수일이 일치하면 수정 적용
                        if pos['round'] == key_round and buy_date_str == key_date:
                            # 포지션 수정 (인덱스 사용)
                            st.session_state.trader.update_position(
                                pos_idx,
                                edit_info['shares'],
                                edit_info['buy_price']
                            )
                            break
        
        # 일일 추천 생성
        recommendation = st.session_state.trader.get_daily_recommendation()
    
    if "error" in recommendation:
        st.error(f"추천 생성 실패: {recommendation['error']}")
        return
    
    # 데이터 경고 표시 (Close가 None인 날짜들)
    if hasattr(st.session_state.trader, '_data_warnings') and st.session_state.trader._data_warnings:
        unique_warnings = list(set(st.session_state.trader._data_warnings))
        if unique_warnings:
            st.warning(f"⚠️ **데이터 경고**: 다음 날짜들의 Close 값이 None이어서 제거되었습니다: {', '.join(sorted(unique_warnings))}")
            st.info("💡 수동 보정이 필요할 수 있습니다. `soxl_quant_system.py`의 `manual_corrections` 딕셔너리에 추가하세요.")
    
    # 최근 10일 SOXL 종가 데이터 확인 (원본 API 응답 기준)
    try:
        # SOXL 데이터를 가져와서 API 응답에서 누락된 날짜 확인
        soxl_data = st.session_state.trader.get_stock_data("SOXL", "1mo")
        
        # 원본 API 응답에서 Close가 None이었던 날짜 확인 (수동 보정 포함)
        if hasattr(st.session_state.trader, '_api_missing_close_dates'):
            api_missing = st.session_state.trader._api_missing_close_dates.get("SOXL", [])
            if api_missing:
                # 최근 10일 내의 날짜만 필터링
                today = datetime.now().date()
                recent_missing = []
                for date_str in api_missing:
                    try:
                        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                        if (today - date_obj).days <= 10:
                            recent_missing.append(date_str)
                    except:
                        pass
                
                if recent_missing:
                    # 수동 보정 정보 가져오기
                    manual_corrections_info = {}
                    if hasattr(st.session_state.trader, '_manual_corrections_info'):
                        manual_corrections_info = st.session_state.trader._manual_corrections_info.get("SOXL", {})
                    
                    # 원본 API 값 정보 가져오기
                    api_original_values = {}
                    if hasattr(st.session_state.trader, '_api_original_values'):
                        api_original_values = st.session_state.trader._api_original_values.get("SOXL", {})
                    
                    # 경고 메시지 생성
                    warning_lines = []
                    for date_str in sorted(recent_missing):
                        original_value = api_original_values.get(date_str)
                        correction_info = manual_corrections_info.get(date_str)
                        
                        if correction_info:
                            # 수동 보정이 적용된 경우
                            corrected_close = correction_info.get('corrected_close')
                            if original_value is None:
                                warning_lines.append(f"- **{date_str}**: API 값 없음 → 수동 보정: ${corrected_close:.2f}")
                            else:
                                warning_lines.append(f"- **{date_str}**: API 값 ${original_value:.2f} → 수동 보정: ${corrected_close:.2f}")
                        else:
                            # 수동 보정이 없는 경우
                            if original_value is None:
                                warning_lines.append(f"- **{date_str}**: API 값 없음")
                            else:
                                warning_lines.append(f"- **{date_str}**: API 값 ${original_value:.2f} (제거됨)")
                    
                    st.warning("⚠️ **최근 10일 SOXL 종가 데이터 확인**:")
                    for line in warning_lines:
                        st.markdown(line)
                    st.info("💡 수동 보정이 적용된 날짜는 거래가 없었거나 데이터 제공 오류일 수 있습니다.")
    except Exception as e:
        st.warning(f"⚠️ 최근 10일 SOXL 종가 데이터 확인 중 오류 발생: {str(e)}")
    
    # 기본 정보 - 모바일 최적화
    # 모바일에서는 2x2 그리드, 데스크톱에서는 1x4 그리드
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
    # 기준 종가 날짜 안내(장중/휴장 시 전 거래일 기준 표시)
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
        # 잔여 예수금 표시
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
            
            # 장중 주문 가이드(현재가가 존재하는 경우 간단 안내)
            current_price = recommendation.get('soxl_current_price')
            if current_price:
                if current_price >= recommendation['buy_price']:
                    st.caption("현재가가 매수가 이상입니다. 즉시 체결 원하면 지정가/시장가, 또는 슬리피지 제한을 원하면 스톱-리밋(Stop=매수가, Limit≈매수가×1.002) 고려")
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
                elif hasattr(buy_date, "strftime"):
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
                
                # 레이아웃: 좌측 주요 정보, 우측 매수 정보
                col1, col2 = st.columns([3, 2])
                with col1:
                    st.info(f"📦 {pos['round']}회차 매도: {pos['shares']}주 @ ${sell_info['sell_price']:.2f}")
                    st.caption(f"모드: {mode} ({mode_name}) • 손절예정일: {stop_loss_date if stop_loss_date else '-'}")
                    st.caption(f"매도 사유: {sell_info['reason']}")
                with col2:
                    st.caption(f"매수체결일: {buy_date_str}")
                    st.caption(f"매수가: {buy_price_text}")
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
                    elif hasattr(buy_date, "strftime"):
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
                    
                    # 레이아웃: 좌측 주요 정보, 우측 매수 정보
                    col1, col2 = st.columns([3, 2])
                    with col1:
                        st.info(f"📦 {pos['round']}회차: 목표가 ${target_sell_price:.2f} (현재 ${current_price:.2f}, 목표까지 {price_diff_pct:+.1f}%) - 보유: {pos['shares']}주")
                        st.caption(f"모드: {mode} ({mode_name}) • 손절예정일: {stop_loss_date if stop_loss_date else '-'}")
                    with col2:
                        st.caption(f"매수체결일: {buy_date_str}")
                        st.caption(f"매수가: ${pos['buy_price']:.2f}")
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
            # 테스트 날짜 오버라이드 고려
            today_for_hold_days = datetime.now()
            if st.session_state.trader and st.session_state.trader.test_today_override:
                today_for_hold_days = datetime.strptime(st.session_state.trader.test_today_override, '%Y-%m-%d')
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
        
        # 포지션 수정 섹션
        st.subheader("✏️ 포지션 수정")
        st.caption("💡 실제 주문 수량이 추천과 다를 경우 수정하세요")
        
        # 수정할 포지션 선택 (인덱스 기반, 매수일과 회차로 구분)
        if st.session_state.trader.positions:
            position_options = []
            for idx, pos in enumerate(st.session_state.trader.positions):
                buy_date_str = pos['buy_date'].strftime('%Y-%m-%d') if isinstance(pos['buy_date'], (datetime, pd.Timestamp)) else str(pos['buy_date'])
                position_label = f"{pos['round']}회차 - {buy_date_str} - {pos['shares']}주 @ ${pos['buy_price']:.2f}"
                position_options.append((idx, position_label))
            
            if position_options:
                selected_option = st.selectbox(
                    "수정할 포지션 선택",
                    options=range(len(position_options)),
                    format_func=lambda x: position_options[x][1],
                    key="position_edit_select"
                )
                selected_position_index = selected_option
                selected_position = st.session_state.trader.positions[selected_position_index]
            
            if selected_position:
                col1, col2 = st.columns(2)
                
                with col1:
                    st.info(f"**현재 정보**")
                    buy_date_str = selected_position['buy_date'].strftime('%Y-%m-%d') if isinstance(selected_position['buy_date'], (datetime, pd.Timestamp)) else str(selected_position['buy_date'])
                    st.write(f"회차: {selected_position['round']}회차")
                    st.write(f"매수일: {buy_date_str}")
                    st.write(f"주식수: {selected_position['shares']}주")
                    st.write(f"매수가: ${selected_position['buy_price']:.2f}")
                    st.write(f"투자금액: ${selected_position['amount']:,.0f}")
                
                with col2:
                    st.info(f"**수정 정보**")
                    new_shares = st.number_input(
                        "주식수",
                        min_value=1,
                        value=int(selected_position['shares']),
                        step=1,
                        key=f"edit_shares_{selected_position_index}"
                    )
                    new_buy_price = st.number_input(
                        "매수가 ($)",
                        min_value=0.01,
                        value=float(selected_position['buy_price']),
                        step=0.01,
                        format="%.2f",
                        key=f"edit_price_{selected_position_index}"
                    )
                    new_amount = new_shares * new_buy_price
                    st.write(f"**새 투자금액: ${new_amount:,.0f}**")
                    
                    # 차액 계산
                    amount_diff = selected_position['amount'] - new_amount
                    if amount_diff > 0:
                        st.success(f"예수금 증가: ${amount_diff:,.0f}")
                    elif amount_diff < 0:
                        st.warning(f"예수금 감소: ${abs(amount_diff):,.0f}")
                    else:
                        st.info("변동 없음")
                
                # 수정 버튼
                if st.button("✅ 포지션 수정", key=f"apply_edit_{selected_position_index}", use_container_width=True):
                    success = st.session_state.trader.update_position(
                        selected_position_index,
                        new_shares,
                        new_buy_price
                    )
                    if success:
                        # 수정된 포지션 정보를 session_state에 저장 (시뮬레이션 후 복원용)
                        if 'position_edits' not in st.session_state:
                            st.session_state.position_edits = {}
                        
                        # 현재 포지션의 고유 식별자 생성 (회차 + 매수일)
                        buy_date_str = selected_position['buy_date'].strftime('%Y-%m-%d') if isinstance(selected_position['buy_date'], (datetime, pd.Timestamp)) else str(selected_position['buy_date'])
                        position_key = f"{selected_position['round']}_{buy_date_str}_{selected_position['buy_price']}"
                        st.session_state.position_edits[position_key] = {
                            'shares': new_shares,
                            'buy_price': new_buy_price
                        }
                        
                        st.success(f"✅ {selected_position['round']}회차 포지션이 수정되었습니다!")
                        st.session_state.trader.clear_cache()  # 캐시 초기화
                        st.rerun()
                    else:
                        st.error("❌ 포지션 수정 실패: 예수금이 부족합니다.")

def show_portfolio():
    """포트폴리오 페이지"""
    st.header("💼 포트폴리오 현황")
    
    if not st.session_state.trader:
        st.error("시스템이 초기화되지 않았습니다.")
        return
    
    # 시뮬레이션 실행 - 투자시작일 기준으로 재계산
    # 테스트 날짜 오버라이드 고려
    today_for_calc = datetime.now()
    if st.session_state.trader and st.session_state.trader.test_today_override:
        today_for_calc = datetime.strptime(st.session_state.trader.test_today_override, '%Y-%m-%d')
    start_date = st.session_state.session_start_date or (today_for_calc - timedelta(days=365)).strftime('%Y-%m-%d')
    
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
            # 테스트 날짜 오버라이드 고려
            today_for_hold_days = datetime.now()
            if st.session_state.trader and st.session_state.trader.test_today_override:
                today_for_hold_days = datetime.strptime(st.session_state.trader.test_today_override, '%Y-%m-%d')
            hold_days = (today_for_hold_days - pos['buy_date']).days
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
            min_value=datetime(2011, 1, 1),
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
        # 백테스팅 탭을 활성화하도록 플래그 설정
        st.session_state.active_tab = 1  # 백테스팅 탭 인덱스
        
        with st.spinner('백테스팅 실행 중...'):
            backtest_result = st.session_state.trader.run_backtest(
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
        
        if "error" in backtest_result:
            st.session_state.backtest_result = None
            st.session_state.backtest_error = backtest_result['error']
        else:
            # 결과를 session_state에 저장 (페이지 새로고침 후에도 유지)
            st.session_state.backtest_result = backtest_result
            st.session_state.backtest_error = None
            st.session_state.backtest_start_date_saved = start_date.strftime('%Y-%m-%d')
            st.session_state.backtest_end_date_saved = end_date.strftime('%Y-%m-%d')
        
        # 페이지 새로고침 없이 결과 표시를 위해 rerun 호출하지 않음
        # (버튼 클릭 시 자동으로 페이지가 새로고침됨)
    
    # 에러 표시
    if 'backtest_error' in st.session_state and st.session_state.backtest_error:
        st.error(f"백테스팅 실패: {st.session_state.backtest_error}")
    
    # 백테스팅 결과 표시
    if 'backtest_result' in st.session_state and st.session_state.backtest_result:
        backtest_result = st.session_state.backtest_result
        
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
    
    # 모드 설정 확인 및 수정
    st.subheader("🎯 모드 설정")
    
    # 기본값 정의
    default_sf_config = {
        "buy_threshold": 3.5,
        "sell_threshold": 1.4,
        "max_hold_days": 35,
        "split_count": 7,
        "split_ratios": [0.049, 0.127, 0.230, 0.257, 0.028, 0.169, 0.140]
    }
    
    default_ag_config = {
        "buy_threshold": 3.6,
        "sell_threshold": 3.5,
        "max_hold_days": 7,
        "split_count": 8,
        "split_ratios": [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020]
    }
    
    # session_state에 파라미터 저장 (초기화)
    if 'sf_config' not in st.session_state:
        st.session_state.sf_config = default_sf_config.copy()
    if 'ag_config' not in st.session_state:
        st.session_state.ag_config = default_ag_config.copy()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("SF 모드 (안전모드)")
        
        # SF 모드 파라미터 편집
        sf_config = st.session_state.sf_config
        
        sf_buy_threshold = st.number_input(
            "매수 임계값 (%)",
            min_value=0.0,
            max_value=10.0,
            value=float(sf_config.get("buy_threshold", 3.5)),
            step=0.1,
            key="sf_buy_threshold"
        )
        
        sf_sell_threshold = st.number_input(
            "매도 임계값 (%)",
            min_value=0.0,
            max_value=10.0,
            value=float(sf_config.get("sell_threshold", 1.4)),
            step=0.1,
            key="sf_sell_threshold"
        )
        
        sf_max_hold_days = st.number_input(
            "최대 보유일수",
            min_value=1,
            max_value=365,
            value=int(sf_config.get("max_hold_days", 30)),
            step=1,
            key="sf_max_hold_days"
        )
        
        sf_split_count = st.number_input(
            "분할매수 횟수",
            min_value=1,
            max_value=20,
            value=int(sf_config.get("split_count", 7)),
            step=1,
            key="sf_split_count"
        )
        
        # 분할 비율 입력
        st.write("**분할 비율:**")
        sf_split_ratios = []
        split_count = int(sf_split_count)
        
        # 기존 비율이 있으면 사용, 없으면 기본값
        existing_ratios = sf_config.get("split_ratios", default_sf_config["split_ratios"])
        
        for i in range(split_count):
            default_ratio = existing_ratios[i] if i < len(existing_ratios) else (1.0 / split_count)
            ratio = st.number_input(
                f"회차 {i+1}",
                min_value=0.0,
                max_value=1.0,
                value=float(default_ratio),
                step=0.001,
                format="%.3f",
                key=f"sf_split_ratio_{i}"
            )
            sf_split_ratios.append(ratio)
        
        # 비율 합이 1이 아니면 경고
        ratio_sum = sum(sf_split_ratios)
        if abs(ratio_sum - 1.0) > 0.01:
            st.warning(f"⚠️ 분할 비율의 합이 {ratio_sum:.3f}입니다. 1.0이 되도록 조정해주세요.")
        
        # 파라미터 변경 감지 및 업데이트
        new_sf_config = {
            "buy_threshold": sf_buy_threshold,
            "sell_threshold": sf_sell_threshold,
            "max_hold_days": sf_max_hold_days,
            "split_count": split_count,
            "split_ratios": sf_split_ratios
        }
        
        # 변경 감지 (딕셔너리 깊은 비교)
        config_changed = (
            new_sf_config["buy_threshold"] != sf_config.get("buy_threshold") or
            new_sf_config["sell_threshold"] != sf_config.get("sell_threshold") or
            new_sf_config["max_hold_days"] != sf_config.get("max_hold_days") or
            new_sf_config["split_count"] != sf_config.get("split_count") or
            len(new_sf_config["split_ratios"]) != len(sf_config.get("split_ratios", [])) or
            any(abs(new_sf_config["split_ratios"][i] - sf_config.get("split_ratios", [])[i]) > 0.0001 
                for i in range(min(len(new_sf_config["split_ratios"]), len(sf_config.get("split_ratios", [])))))
        )
        
        if config_changed:
            st.session_state.sf_config = new_sf_config
            st.session_state.trader = None  # 트레이더 재초기화 필요
            st.rerun()  # 즉시 재실행하여 트레이더 재초기화
    
    with col2:
        st.subheader("AG 모드 (공세모드)")
        
        # AG 모드 파라미터 편집
        ag_config = st.session_state.ag_config
        
        ag_buy_threshold = st.number_input(
            "매수 임계값 (%)",
            min_value=0.0,
            max_value=10.0,
            value=float(ag_config.get("buy_threshold", 3.6)),
            step=0.1,
            key="ag_buy_threshold"
        )
        
        ag_sell_threshold = st.number_input(
            "매도 임계값 (%)",
            min_value=0.0,
            max_value=10.0,
            value=float(ag_config.get("sell_threshold", 3.5)),
            step=0.1,
            key="ag_sell_threshold"
        )
        
        ag_max_hold_days = st.number_input(
            "최대 보유일수",
            min_value=1,
            max_value=365,
            value=int(ag_config.get("max_hold_days", 7)),
            step=1,
            key="ag_max_hold_days"
        )
        
        ag_split_count = st.number_input(
            "분할매수 횟수",
            min_value=1,
            max_value=20,
            value=int(ag_config.get("split_count", 8)),
            step=1,
            key="ag_split_count"
        )
        
        # 분할 비율 입력
        st.write("**분할 비율:**")
        ag_split_ratios = []
        ag_split_count = int(ag_split_count)
        
        # 기존 비율이 있으면 사용, 없으면 기본값
        existing_ag_ratios = ag_config.get("split_ratios", default_ag_config["split_ratios"])
        
        for i in range(ag_split_count):
            default_ratio = existing_ag_ratios[i] if i < len(existing_ag_ratios) else (1.0 / ag_split_count)
            ratio = st.number_input(
                f"회차 {i+1}",
                min_value=0.0,
                max_value=1.0,
                value=float(default_ratio),
                step=0.001,
                format="%.3f",
                key=f"ag_split_ratio_{i}"
            )
            ag_split_ratios.append(ratio)
        
        # 비율 합이 1이 아니면 경고
        ag_ratio_sum = sum(ag_split_ratios)
        if abs(ag_ratio_sum - 1.0) > 0.01:
            st.warning(f"⚠️ 분할 비율의 합이 {ag_ratio_sum:.3f}입니다. 1.0이 되도록 조정해주세요.")
        
        # 파라미터 변경 감지 및 업데이트
        new_ag_config = {
            "buy_threshold": ag_buy_threshold,
            "sell_threshold": ag_sell_threshold,
            "max_hold_days": ag_max_hold_days,
            "split_count": ag_split_count,
            "split_ratios": ag_split_ratios
        }
        
        # 변경 감지 (딕셔너리 깊은 비교)
        ag_config_changed = (
            new_ag_config["buy_threshold"] != ag_config.get("buy_threshold") or
            new_ag_config["sell_threshold"] != ag_config.get("sell_threshold") or
            new_ag_config["max_hold_days"] != ag_config.get("max_hold_days") or
            new_ag_config["split_count"] != ag_config.get("split_count") or
            len(new_ag_config["split_ratios"]) != len(ag_config.get("split_ratios", [])) or
            any(abs(new_ag_config["split_ratios"][i] - ag_config.get("split_ratios", [])[i]) > 0.0001 
                for i in range(min(len(new_ag_config["split_ratios"]), len(ag_config.get("split_ratios", [])))))
        )
        
        if ag_config_changed:
            st.session_state.ag_config = new_ag_config
            st.session_state.trader = None  # 트레이더 재초기화 필요
            st.rerun()  # 즉시 재실행하여 트레이더 재초기화
    
    # 시스템 정보
    st.subheader("ℹ️ 시스템 정보")
    
    # 트레이더가 초기화되지 않은 경우 체크
    if not st.session_state.trader:
        st.warning("⚠️ 시스템이 재초기화 중입니다. 잠시 후 새로고침해주세요.")
        return
    
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
