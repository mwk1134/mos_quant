"""Streamlit views for the separate KMW3 historical strategy preset."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timezone
import io
import json
import logging

import pandas as pd
import streamlit as st

from banban_strategy import normalize_config, replay, report_from_snapshot
from banban_research.atr_sessions import ET, exchange_calendar
from kmw_order_plan import _KMWPlanTrader


class _BanbanMarketDataTrader(_KMWPlanTrader):
    """Cut at the exchange's completed session, including early closes."""

    def _drop_unfinished_daily_bar(self, stock_data):
        if stock_data is None or stock_data.empty:
            return stock_data
        return stock_data[stock_data.index.date <= self._expected_basis_date].copy()


class BanbanMarketDataError(RuntimeError):
    """A public data failure with a fixed, useful explanation."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@st.cache_data(ttl=300, show_spinner=False)
def _load_completed_replay(config_json, basis_date):
    config = normalize_config(json.loads(config_json))
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        provider = _BanbanMarketDataTrader(now=datetime.now(timezone.utc),
            expected_basis_date=date.fromisoformat(basis_date), initial_capital=1)
        market = {}
        for symbol in ("SOXL", "QQQ"):
            try:
                # The existing 15y path first requests the entire daily history
                # with explicit dates. range=max silently returns monthly bars.
                market[symbol] = provider.get_stock_data(symbol, "15y")
            except ValueError as exc:
                raise BanbanMarketDataError("DAILY_DATA_UNAVAILABLE",
                    f"{symbol}의 {basis_date}까지 확정된 일봉을 불러오지 못했습니다.") from exc
    anchor = "2010-03-11"
    if (market["SOXL"].index.min().date().isoformat() != anchor
            or market["QQQ"].index.min().date().isoformat() > anchor):
        raise BanbanMarketDataError("INCOMPLETE_HISTORY",
            "SOXL·QQQ의 전체 일봉 이력을 확인하지 못했습니다. 과거 이력이 축소되어 추천 계산을 보류했습니다.")
    try:
        return replay(market, config, end_date=basis_date)
    except ValueError as exc:
        raise BanbanMarketDataError("INVALID_DAILY_HISTORY",
            "받아온 일봉의 날짜·가격 이력을 검증하지 못해 추천 계산을 보류했습니다.") from exc


def _completed_basis_date(now=None):
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("A timezone-aware market instant is required")
    calendar = exchange_calendar()
    session = calendar.date_to_session(current.astimezone(ET).date().isoformat(), direction="previous")
    if current < calendar.session_close(session).to_pydatetime():
        session = calendar.previous_session(session)
    return session.strftime("%Y-%m-%d")


def _clear_recommendation_cache():
    """Retry these daily histories without erasing unrelated preset data."""
    import soxl_quant_system as strategy

    _load_completed_replay.clear()
    with strategy._SHARED_STOCK_DATA_CACHE_LOCK:
        for symbol in ("SOXL", "QQQ"):
            key = f"{symbol}_15y"
            strategy._SHARED_STOCK_DATA_CACHE.pop(key, None)
            strategy._SHARED_STOCK_DATA_RETRY_AFTER.pop(key, None)


def load_replay(config):
    """Use the same confirmed US daily-data boundary as the existing strategy."""
    basis = _completed_basis_date()
    return _load_completed_replay(json.dumps(config, sort_keys=True), basis)


def _show_orders(report, *, unavailable_reason=None):
    st.subheader("매수·매도 추천")
    if unavailable_reason:
        for label in ("ATR70 주문표", "R2 주문표"):
            st.subheader(label)
            st.dataframe(pd.DataFrame([{"매매": "대기", "회차": "-", "유형": "-", "기준가격": "-", "수량": "-",
                                        "조건": unavailable_reason}]), use_container_width=True, hide_index=True)
        st.caption("최신 확정 일봉 확인 후 매수가·매도가와 수량을 계산합니다.")
        return
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
                if strategy == "R2" and not any(order.get("side") == "BUY" for order in orders):
                    st.caption("R2 가용 회차가 없어 신규 매수는 없습니다. 보유분의 매도 조건은 계속 표시합니다.")
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
    pending_quantity = "비중 조정 후 산정" if status == "REBALANCE_PENDING" else "조건 확인 후 산정"
    return {"매매": "대기" if blocked else "매수" if order.get("side") == "BUY" else "매도",
            "회차": order.get("round") if order.get("round") is not None else "-",
            "유형": kind, "기준가격": "-" if blocked else price,
            "수량": "-" if blocked else order.get("quantity") if order.get("quantity") is not None else pending_quantity,
            "조건": "가용 현금 부족 · 신규 매수 없음" if order.get("quantity") == 0 else order.get("condition", status_label)}


@st.fragment(run_every=60)
def show_workspace(config):
    if st.session_state.get("active_preset") not in (None, "KMW3"):
        return
    st.subheader("🔀 KMW3 · 반반매매 (ATR70-R2)")
    st.caption(f"투자 시작일 {config['session_start_date']} · 투자원금 ${config['initial_capital']:,.0f} · ATR70 50% + R2 50%")
    if st.button("🔄 반반매매 시세 갱신", key="kmw3_refresh"):
        _clear_recommendation_cache()
    st.caption("화면이 열려 있으면 1분마다 확인합니다. 미국 장 마감 후 다음 거래일의 추천으로 갱신합니다.")
    historical = False
    try:
        with st.spinner("반반매매의 확정 시세와 보유분을 계산 중입니다..."):
            report = load_replay(config)
    except Exception as exc:
        logging.getLogger(__name__).exception("KMW3 recommendation refresh failed")
        reason = str(exc) if isinstance(exc, BanbanMarketDataError) else "반반매매 추천 계산을 완료하지 못했습니다."
        basis = _completed_basis_date()
        candidates = []
        for saved in (st.session_state.get("_kmw3_last_verified_snapshot"),
                      st.session_state.get("positions_snapshot")):
            candidate = report_from_snapshot(saved)
            try:
                compatible = (bool(candidate) and date.fromisoformat(candidate["end_date"]).isoformat() <= basis
                              and normalize_config(candidate.get("config")) == normalize_config(config))
            except (KeyError, TypeError, ValueError):
                compatible = False
            if compatible:
                candidates.append(candidate)
        if not candidates:
            st.warning(reason + " 시세 갱신 후 다시 확인해 주세요.")
            _show_orders({}, unavailable_reason="확정 시세 확인 대기 · 가격·수량 계산 보류")
            _show_settings(config)
            return
        report = max(candidates, key=lambda candidate: candidate["end_date"])
        historical = report["end_date"] != basis
        if historical:
            st.warning(f"{reason} {report['end_date']}까지의 저장된 모의 결과를 표시합니다.")
        else:
            st.info(f"시세 재조회가 지연되어 {basis} 확정 일봉으로 계산해 둔 추천을 표시합니다.")
    else:
        st.session_state.positions_snapshot = deepcopy(report["snapshot"])
        st.session_state._kmw3_last_verified_snapshot = deepcopy(report["snapshot"])

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
        _show_orders(report, unavailable_reason="최신 확정 시세 확인 대기 · 저장된 과거 주문안 사용 보류")
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
