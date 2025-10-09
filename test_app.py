import streamlit as st

st.title("🧪 테스트 앱 - Force redeploy v1.1")

st.write("현재 시간:", st.session_state.get('test_time', 'Not set'))

if 'test_time' not in st.session_state:
    st.session_state.test_time = "2025-01-27 15:30:00"

st.success("✅ 앱이 정상적으로 로드되었습니다!")

# 투자원금 갱신 테스트
st.subheader("투자원금 갱신 테스트")

try:
    from soxl_quant_system import SOXLQuantTrader
    
    # 트레이더 초기화
    trader = SOXLQuantTrader(9000)
    st.write(f"초기 투자원금: ${trader.current_investment_capital:,.0f}")
    st.write(f"1회차 매수금액: ${trader.calculate_position_size(1):,.0f}")
    
    st.success("✅ SOXLQuantTrader 클래스 정상 작동!")
    
except Exception as e:
    st.error(f"❌ 오류 발생: {str(e)}")
