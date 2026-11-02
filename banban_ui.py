"""Streamlit views for the separate KMW3 historical strategy preset."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timezone
import io
import json

import pandas as pd
import streamlit as st

from banban_strategy import normalize_config, replay, report_from_snapshot


@st.cache_data(ttl=300, show_spinner=False)
def _load_completed_replay(config_json, basis_date):
    from kmw_order_plan import _KMWPlanTrader

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        provider = _KMWPlanTrader(now=datetime.now(timezone.utc), expected_basis_date=date.fromisoformat(basis_date),
                                  initial_capital=1)
        market = {symbol: provider.get_stock_data(symbol, "max") for symbol in ("SOXL", "QQQ")}
    return replay(market, json.loads(config_json), end_date=basis_date)


def load_replay(config):
    """Use the same confirmed US daily-data boundary as the existing strategy."""
    from kmw_order_plan import completed_trading_date, ET

    basis = completed_trading_date(datetime.now(ET)).isoformat()
    return _load_completed_replay(json.dumps(config, sort_keys=True), basis)


def _show_orders(report):
    preview = report.get("order_preview") or {}
    if isinstance(preview, dict):
        order_date = preview.get("order_date") or preview.get("session_date")
        if order_date:
            st.caption(f"미국 주문일 {order_date} · 모의 보유분 기준")
        if preview.get("monthly_rebalance_due"):
            st.info("월초 비중 조정 예정입니다. 시가로 50:50 조정을 마친 뒤 매매 수량을 다시 계산합니다.")
        tables = preview.get("strategy_order_tables", preview)
        for strategy, label in (("ATR", "ATR70 주문표"), ("R2", "R2 주문표")):
            st.subheader(label)
            table = tables.get(strategy, tables.get(strategy.lower(), {}))
            if isinstance(table, list):
                orders = table
            else:
                orders = table.get("orders", []) if isinstance(table, dict) else []
                if isinstance(table, dict) and table.get("message"):
                    st.info(table["message"])
            if orders:
                rows = [_order_row(order) for order in orders]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.caption("조건 확인 대기 · 신규 주문 없음")
    st.caption("확정 일봉으로 계산한 모의 매매표입니다. 실제 계좌의 주문·체결을 나타내지 않습니다.")


def _order_row(order):
    status = order.get("status", "")
    blocked = status == "FILTER_BLOCKED" or order.get("quantity") == 0
    kind = order.get("order_type", "-")
    price = order.get("reference_price") or order.get("price")
    if price:
        price = f"${float(price):,.2f}"
    else:
        price = {"MOO": "시가 시장가", "MOC": "종가 시장가", "BREAKOUT": "당일 시가 × 1.015"}.get(kind, "조건 확인 후 산정")
    status_label = {"NEXT_OPEN": "다음 거래일 시가 청산", "CONDITIONAL": "종가 조건 확인 대기",
                    "FILTER_BLOCKED": "변동성 조건 미충족 · 신규 매수 없음",
                    "WAITING_OPEN": "정규장 시가 × 1.015 돌파 확인 후 수량 산정",
                    "REBALANCE_PENDING": "월초 비중 조정 후 수량 재계산"}.get(status, status)
    return {"매매": "대기" if blocked else "매수" if order.get("side") == "BUY" else "매도",
            "유형": kind, "기준가격": "-" if blocked else price,
            "수량": "-" if blocked else order.get("quantity") if order.get("quantity") is not None else "조건 확인 후 산정",
            "조건": "가용 현금 부족 · 신규 매수 없음" if order.get("quantity") == 0 else order.get("condition", status_label)}


def show_workspace(config):
    st.subheader("🔀 KMW3 · 반반매매 (ATR70-R2)")
    st.caption(f"투자 시작일 {config['session_start_date']} · 투자원금 ${config['initial_capital']:,.0f} · ATR70 50% + R2 50%")
    if st.button("🔄 반반매매 시세 갱신", key="kmw3_refresh"):
        _load_completed_replay.clear()
    historical = False
    try:
        with st.spinner("반반매매의 확정 시세와 보유분을 계산 중입니다..."):
            report = load_replay(config)
    except Exception:
        saved = st.session_state.get("positions_snapshot") or {}
        report = report_from_snapshot(saved)
        try:
            compatible = bool(report) and normalize_config(report.get("config")) == normalize_config(config)
        except (TypeError, ValueError):
            compatible = False
        if not compatible:
            st.warning("반반매매의 확정 시세를 불러오지 못했습니다. 시세 갱신 후 다시 확인해 주세요.")
            _show_settings(config)
            return
        st.warning(f"최신 확정 시세를 확인하지 못해 {saved.get('as_of_date', '-')}까지의 저장된 모의 결과를 표시합니다.")
        historical = True
    else:
        st.session_state.positions_snapshot = deepcopy(report["snapshot"])

    snapshot = report.get("snapshot") or st.session_state.get("positions_snapshot") or {}
    st.caption(f"반영일: {snapshot.get('as_of_date', '-')} · 모의 운용")
    today, history, settings = st.tabs(["📊 오늘의 매매", "📈 백테스팅", "⚙️ 설정"])
    with today:
        _show_today(report, config, historical=historical)
    with history:
        _show_history(report, config)
    with settings:
        _show_settings(config)


def _show_today(report, config, *, historical=False):
    daily = report.get("daily") or []
    snapshot = report.get("snapshot") or st.session_state.get("positions_snapshot") or {}
    latest = daily[-1] if daily else {}
    assets = latest.get("total_assets", latest.get("account_value", config["initial_capital"]))
    cash = latest.get("available_cash", snapshot.get("available_cash", config["initial_capital"]))
    first, second, third = st.columns(3)
    first.metric("총자산", f"${float(assets):,.2f}")
    second.metric("현금", f"${float(cash):,.2f}")
    third.metric("원금 대비 수익률", f"{(float(assets) / config['initial_capital'] - 1) * 100:.2f}%")
    sleeves = report.get("sleeves") or []
    if isinstance(sleeves, dict):
        sleeves = [{"strategy": key, **value} for key, value in sleeves.items()]
    if sleeves:
        st.write("**전략별 자산**")
        rows = [{"전략": row["strategy"], "현금 ($)": round(row["cash"], 2), "보유수량": row["shares"],
                 "평가액 ($)": round(row["position_value"], 2), "총자산 ($)": round(row["total_assets"], 2),
                 "비중 (%)": round(row["weight_pct"], 2)} for row in sleeves]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    holdings = report.get("holdings") or []
    if holdings:
        st.write("**모의 보유분**")
        rows = [{"전략": row["sleeve"], "매수일": row["entry_date"], "수량": row["shares"],
                 "매수가 ($)": round(row["entry_price"], 2),
                 "목표가 ($)": round(row["target_price"], 2) if row.get("target_price") else "다음 거래일 시가"}
                for row in holdings]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if historical:
        st.info("최신 시세 확인 전에는 오늘의 주문안을 표시하지 않습니다. 저장된 자산·보유분과 과거 성과를 확인해 주세요.")
    else:
        _show_orders(report)


def _show_history(report, config):
    metrics = report.get("metrics") or {}
    if metrics:
        first, second = st.columns(2)
        first.metric("최대 낙폭 (MDD)", f"{abs(metrics.get('mdd_pct', 0)):.2f}%")
        second.metric("모의 청산 횟수", str(metrics.get("total_trades", 0)))
    daily = report.get("daily") or []
    if daily:
        frame = pd.DataFrame(daily)
        frame["date"] = pd.to_datetime(frame["date"])
        value_key = "total_assets" if "total_assets" in frame else "account_value"
        st.line_chart(frame.set_index("date")[[value_key]].rename(columns={value_key: "총자산 ($)"}))
        st.dataframe(frame[["date", value_key, "available_cash", "atr_asset", "r2_asset"]].rename(columns={
            "date": "거래일", value_key: "총자산 ($)", "available_cash": "현금 ($)",
            "atr_asset": "ATR70 자산 ($)", "r2_asset": "R2 자산 ($)"}), use_container_width=True, hide_index=True)
        st.download_button("📥 반반매매 일별 내역 CSV", frame.to_csv(index=False).encode("utf-8-sig"),
                           "kmw3_banban_daily.csv", "text/csv")
    trades = report.get("trades") or []
    if trades:
        st.write("**모의 청산 내역**")
        rows = [{"전략": row["sleeve"], "매수일": row["entry_date"], "매도일": row["exit_date"],
                 "수량": row["shares"], "매수가 ($)": round(row["entry_price"], 2),
                 "매도가 ($)": round(row["exit_price"], 2), "손익 ($)": round(row["profit_loss"], 2)}
                for row in trades]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"비용 가정: 매수·매도 각각 {float(config.get('commission_rate', 0.0005)) * 100:g}%, 슬리피지 0%. ATR 돌파는 일봉 고가, R2는 종가로 체결을 가정합니다.")


def _show_settings(config):
    half = config["initial_capital"] / 2
    st.write(f"전략: **반반매매 (ATR70-R2)** · 시작일: **{config['session_start_date']}**")
    st.write(f"투자원금 **${config['initial_capital']:,.0f}** · ATR70 **${half:,.0f}** / R2 **${half:,.0f}**")
    st.write("ATR: Wilder 70 · 변동성 252일·75백분위 필터 · 시가 대비 +1.5% 돌파 매수 · 다음 거래일 시가 청산")
    st.write("R2: 완료된 QQQ 주봉 RSI와 SOXL 정배열에 따른 회차 매매")
    st.write("매월 첫 미국 거래일에 ATR70·R2 자산 비중을 50:50으로 조정합니다.")
    st.caption("KMW3는 확정 일봉을 이용한 모의 운용 프리셋입니다.")
