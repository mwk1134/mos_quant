"""Read-only KMW-style recommendation cards for the live KMW2 account."""

import streamlit as st


def _label(strategy, row):
    label = "ATR70" if strategy == "ATR" else "R2"
    if row.get("회차") not in (None, "", "-"):
        label += f" · {row['회차']}회차"
    return label


def _is_pending(row):
    quantity = row.get("수량")
    return type(quantity) is not int or quantity <= 0


def _show_order(strategy, table, row, *, existing=False):
    side = row["매매"]
    label = _label(strategy, row)
    with st.container(border=True):
        if existing:
            st.info(f"📄 {label} · 저장된 {side} 주문")
        elif _is_pending(row):
            st.info(f"⏳ {label} · {side} 조건 확인 대기")
        elif side == "매수":
            st.success(f"✅ {label} 매수 추천")
        else:
            st.warning(f"📦 {label} 매도 추천")

        order_date = row.get("주문일") or table.get("order_date")
        if order_date:
            st.caption(f"📅 미국 주문일: {order_date}")
        if row.get("종목"):
            st.caption(f"종목: {row['종목']}")

        # These are already formatted display terms. In particular a market
        # order's budget price is not an execution price or an order amount.
        kind = str(row.get("유형") or "-")
        price = str(row.get("기준가격") or "-")
        st.info(f"💰 {side}가: {price} ({kind} 주문)")
        price_note = str(row.get("가격설명") or "")
        if price_note and price_note not in price:
            st.caption(price_note)
        quantity = row.get("수량")
        quantity_text = f"{quantity:,}주" if type(quantity) is int and quantity > 0 else str(quantity or "미정")
        st.info(f"📦 {side}주식수: {quantity_text}")

        mode = row.get("모드")
        if mode in {"SF", "AG"}:
            st.caption(f"모드: {mode} ({'안전모드' if mode == 'SF' else '공세모드'})")
        if side == "매도":
            if row.get("매수일"):
                st.caption(f"매수체결일: {row['매수일']}")
            if row.get("매수가") not in (None, "", "-"):
                st.caption(f"매수가: {row['매수가']}")
        if row.get("조건"):
            st.caption(f"조건: {row['조건']}")
        if row.get("상태"):
            st.caption(f"상태: {row['상태']}")
        if row.get("주문번호"):
            st.caption(f"주문번호: {row['주문번호']}")


def show_order_recommendations(tables, *, waiting=False, signal_available=True):
    """Present supplied rows without querying, mutating or dispatching anything."""
    tables = tables if isinstance(tables, dict) else {}
    entries = []
    for strategy in ("ATR", "R2"):
        table = tables.get(strategy)
        if not isinstance(table, dict):
            table = {}
        rows = table.get("rows")
        rows = rows if isinstance(rows, list) else []
        entries.append((strategy, table, [row for row in rows if isinstance(row, dict)]))

    st.subheader("📋 주문일 기준 매매 추천")
    if waiting:
        st.caption("실계좌 시작 조건 확인 대기 · 신규 주문 없음")
    elif not signal_available:
        st.warning("최신 신호 확인 불가 · 저장된 주문은 현재 신규 추천이 아닙니다.")

    buy_column, sell_column = st.columns(2)
    for side, column, heading in (("매수", buy_column, "🟢 매수 추천"),
                                  ("매도", sell_column, "🔴 매도 추천")):
        with column:
            st.subheader(heading)
            drafts, receipts = [], []
            for strategy, table, rows in entries:
                for row in rows:
                    if row.get("매매") != side:
                        continue
                    if row.get("기존주문"):
                        receipts.append((strategy, table, row))
                    elif not waiting and signal_available:
                        drafts.append((strategy, table, row))

            ready = [item for item in drafts if not _is_pending(item[2])]
            pending = [item for item in drafts if _is_pending(item[2])]
            if ready:
                st.info(f"📋 {side} 추천: {len(ready)}건")
            elif not pending:
                if waiting or not signal_available:
                    st.info(f"⏳ {side} 추천 확인 대기")
                else:
                    st.info(f"현재 {side} 추천이 없습니다.")

            for strategy, table, row in ready + pending:
                _show_order(strategy, table, row)
            if receipts:
                st.caption(f"저장된 {side} 주문 {len(receipts)}건 · 신규 추천 건수에 포함하지 않습니다.")
                for strategy, table, row in receipts:
                    _show_order(strategy, table, row, existing=True)

            shown = {strategy for strategy, _, _ in drafts + receipts}
            for strategy, table, _ in entries:
                if strategy not in shown:
                    label = "ATR70" if strategy == "ATR" else "R2"
                    message = (table.get("message") or "현재 주문이 없습니다."
                               if side == "매수" or waiting or not signal_available
                               else "현재 매도 주문안이 없습니다.")
                    st.caption(f"{label}: {message}")
