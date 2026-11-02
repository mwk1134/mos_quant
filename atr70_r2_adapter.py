"""Market-data, recommendation and snapshot boundaries for the KMW2 mix.

Imports and construction are inert. Persisted mixed accounting is the source
of truth; the legacy backtest must never reconstruct its positions or cash.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
import io
import re

import pandas as pd

from atr70_r2 import (
    MIXED_SWITCH_KEY, STATE_KEY, STRATEGY_ID, advance_r2_session,
    atr70_signal, available_cash, build_runtime_plan, r2_rules, validate_state,
)
from us_market_calendar import is_us_equity_trading_day


_LOT = re.compile(r"^[1-9][0-9]*_\d{4}-\d{2}-\d{2}$")


def mixed_requested(document, preset="KMW2"):
    if preset != "KMW2":
        return False
    snapshot = document.get(preset, {})
    config = document.get("_preset_configs", {}).get(preset, {})
    value = config.get(MIXED_SWITCH_KEY, snapshot.get(MIXED_SWITCH_KEY, False))
    if type(value) is not bool:
        raise ValueError("Mixed switch must be a boolean")
    return value


def legacy_quantity(snapshot):
    quantity = 0
    for key, lot in snapshot.items():
        if _LOT.fullmatch(key):
            shares = lot.get("shares") if isinstance(lot, dict) else None
            if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
                raise ValueError("Invalid legacy holding")
            quantity += shares
    return quantity


def _sessions(session):
    from kmw_order_plan import next_trading_date, previous_trading_date
    day = date.fromisoformat(session)
    previous, following = previous_trading_date(day), next_trading_date(day)
    return previous.isoformat(), following.isoformat(), previous.month != day.month


def _money(value):
    if isinstance(value, bool):
        raise ValueError("Invalid amount")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid amount")
    return result


def _validated_rows(rows, basis):
    """Reject missing sessions rather than treating a sparse feed as 70 days."""
    checked = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        if day.isoformat() > basis:
            continue
        values = {key: _money(row[key]) for key in ("open", "high", "low", "close")}
        if not is_us_equity_trading_day(day) or any(value <= 0 for value in values.values()):
            raise ValueError("Invalid completed daily bar")
        if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
            raise ValueError("Invalid daily OHLC")
        if checked:
            expected = date.fromisoformat(checked[-1]["date"]) + timedelta(days=1)
            while not is_us_equity_trading_day(expected):
                expected += timedelta(days=1)
            if expected != day:
                raise ValueError("Missing or duplicate daily session")
        checked.append({"date": day.isoformat(), **{key: str(value) for key, value in values.items()}})
    if not checked or checked[-1]["date"] != basis:
        raise ValueError("Stale mixed-strategy daily data")
    return checked


def _frame_rows(frame):
    return [{"date": stamp.date().isoformat(), **{key.lower(): row[key] for key in ("Open", "High", "Low", "Close")}}
            for stamp, row in frame.iterrows()]


def load_market_rows(now, basis_date):
    """Keep the same full-history anchor for Wilder on every execution day."""
    from kmw_order_plan import _KMWPlanTrader
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        provider = _KMWPlanTrader(now=now, expected_basis_date=date.fromisoformat(basis_date), initial_capital=1)
        soxl = provider.get_stock_data("SOXL", "max")
        qqq = provider.get_stock_data("QQQ", "max")
    return _validated_rows(_frame_rows(soxl), basis_date), _validated_rows(_frame_rows(qqq), basis_date)


def r2_market_context(qqq_rows, session_date):
    """Original rolling-mean RSI and explicit mode transitions; no saved overrides."""
    frame = pd.DataFrame(qqq_rows)
    frame.index = pd.to_datetime(frame.pop("date"))
    close = frame["close"].astype(float).resample("W-FRI").last().dropna()
    close = close[close.index < pd.Timestamp(session_date)]
    delta = close.diff()
    gains = delta.where(delta > 0, 0).rolling(14).mean()
    losses = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsis = 100 - 100 / (1 + gains / losses)
    mode = "SF"
    valid = 0
    for older, latest in zip(rsis.iloc[:-1], rsis.iloc[1:]):
        if pd.isna(older) or pd.isna(latest):
            continue
        valid += 1
        if ((latest > older and older <= 35) or (older <= 50 < latest)
                or (latest > older and 50 < older <= 60)):
            mode = "AG"
        elif ((latest < older and older >= 65) or (older >= 50 > latest)
              or (latest < older and 40 <= older < 50)):
            mode = "SF"
    if not valid or len(rsis) < 2 or pd.isna(rsis.iloc[-1]) or pd.isna(rsis.iloc[-2]):
        raise ValueError("Insufficient completed weekly RSI data")
    return {"mode": mode, "rsi_t1": float(rsis.iloc[-1]), "rsi_t2": float(rsis.iloc[-2])}


def build_r2_exit_orders(state, *, session_date):
    validate_state(state)
    from kmw_order_plan import next_trading_date, order_price
    lots = [lot for lot in state["r2"]["lots"] if lot["shares"]]
    orders = []
    for lot in lots:
        expiry = date.fromisoformat(lot["entry_date"])
        for _ in range(lot["frozen_rules"]["max_holding_days"]):
            expiry = next_trading_date(expiry)
        expired = session_date >= expiry.isoformat()
        orders.append({"ticker": "SOXL", "side": "SELL", "quantity": lot["shares"],
                       "price": "0" if expired else order_price(_money(lot["entry_price"]) * _money(lot["frozen_rules"]["sell_multiplier"])),
                       "order_type": "MOC" if expired else "LOC", "round": lot["round"],
                       "lot_id": lot["lot_id"], "position_key": lot["lot_id"],
                       "frozen_rules": deepcopy(lot["frozen_rules"]), "expiry_date": expiry.isoformat()})
    return orders


def build_r2_orders(state, *, session_date, daily_rows, qqq_rows, enabled=True):
    """Keep frozen exits available even when the next entry cannot be computed."""
    from kmw_order_plan import order_price
    orders = build_r2_exit_orders(state, session_date=session_date)
    if not enabled:
        return orders
    previous, _, _ = _sessions(session_date)
    rows = _validated_rows(daily_rows, previous)
    context = r2_market_context(_validated_rows(qqq_rows, previous), session_date)
    if len(rows) < 60:
        raise ValueError("Insufficient moving-average history")
    closes = [_money(row["close"]) for row in rows]
    aligned = sum(closes[-5:]) / 5 > sum(closes[-20:]) / 20 > sum(closes[-60:]) / 60
    rules = r2_rules(context["mode"], aligned)
    lots = [lot for lot in state["r2"]["lots"] if lot["shares"]]
    next_round = len(lots) + 1
    if enabled and next_round <= len(rules["round_weights"]):
        price = _money(order_price(closes[-1] * _money(rules["buy_multiplier"])))
        budget = min(available_cash(state, "R2"), _money(state["r2"]["reference_seed"]) * _money(rules["round_weights"][next_round - 1]))
        quantity = int((budget / (price * (1 + _money(state["fee_rate"])))).to_integral_value(rounding=ROUND_FLOOR))
        if quantity:
            orders.append({"ticker": "SOXL", "side": "BUY", "quantity": quantity, "price": str(price),
                           "order_type": "LOC", "round": next_round, "lot_id": "R2:" + session_date,
                           "position_key": "R2:" + session_date, "frozen_rules": rules})
    return orders


def mirror_mixed_snapshot(snapshot, state, basis_date, now):
    validate_state(state)
    if legacy_quantity(snapshot):
        raise ValueError("Legacy holdings cannot coexist with mixed accounting")
    result = deepcopy(snapshot)
    result[STATE_KEY] = deepcopy(state)
    result["available_cash"] = float(_money(state["atr"]["cash"]) + _money(state["r2"]["cash"]) + _money(state["unallocated_cash"]))
    result["as_of_date"] = basis_date
    result["mixed_updated_at"] = now.isoformat()
    result.pop("pending_buy", None)
    return result


class MixedSnapshotStore:
    """CAS state store. Never turns an account API into a price-based fill."""

    def __init__(self, env, preset="KMW2", *, github=None, market_loader=None):
        if preset != "KMW2":
            raise ValueError("Mixed store requires KMW2")
        self.env, self.preset = dict(env), preset
        self._github = github
        self._owns_github = github is None
        self._market_loader = market_loader or load_market_rows
        self._context = None
        self._market_cache = None

    def _connection(self):
        if self._github is None:
            from preset_snapshot_sync import _GithubSnapshots
            token = self.env.get("GITHUB_TOKEN")
            if not isinstance(token, str) or not token or any(ord(c) < 33 for c in token):
                raise ValueError("Mixed state storage is not configured")
            self._github = _GithubSnapshots(token)
        return self._github

    def read(self, now):
        from kmw_order_plan import ET, completed_trading_date, next_trading_date
        document, sha = self._connection().read()
        snapshot = document.get(self.preset)
        if not isinstance(snapshot, dict) or not snapshot:
            raise ValueError("KMW2 snapshot is required")
        enabled = mixed_requested(document, self.preset)
        state = deepcopy(snapshot.get(STATE_KEY))
        if state is not None:
            validate_state(state)
            if legacy_quantity(snapshot):
                raise ValueError("Legacy and mixed holdings overlap")
        basis = completed_trading_date(now.astimezone(ET)).isoformat()
        session = next_trading_date(date.fromisoformat(basis)).isoformat()
        previous, following, first = _sessions(session)
        context = {"enabled": enabled, "state": state, "snapshot": deepcopy(snapshot),
                   "document": document, "sha": sha, "basis_date": basis, "session_date": session,
                   "previous_session": previous, "next_session": following,
                   "first_session_of_month": first, "daily_rows": [], "qqq_rows": [], "r2_orders": [],
                   "legacy_shares": legacy_quantity(snapshot),
                   "broker_confirmed": isinstance(snapshot.get("broker_sync"), dict) and snapshot.get("as_of_date") == basis,
                   "reservation_start_date": document.get("_preset_configs", {}).get("KMW2", {}).get("session_start_date", "2026-04-29"),
                   "fee_rate": self.env.get("MERITZ_MIX_FEE_RATE", "0.005")}
        if state is not None and state["phase"] == "ACTIVE":
            advanced = advance_r2_session(state, session)
            context.update(advanced_state=advanced,
                           r2_orders=build_r2_exit_orders(advanced, session_date=session))
            try:
                if self._market_cache is None or self._market_cache[0] != basis:
                    self._market_cache = (basis, *self._market_loader(now, basis))
                _, daily, qqq = self._market_cache
                r2_orders = build_r2_orders(advanced, session_date=session, daily_rows=daily, qqq_rows=qqq, enabled=enabled)
                context.update(daily_rows=deepcopy(daily), qqq_rows=deepcopy(qqq), r2_orders=r2_orders)
            except Exception:
                context["market_error"] = "완료 일봉·주봉 데이터를 확인할 수 없어 신규 매수를 중단합니다. 기존 청산 관리는 유지합니다."
        self._context = deepcopy(context)
        return context

    def save(self, state, now):
        from kmw_order_plan import ET, completed_trading_date
        validate_state(state)
        if self._context is None:
            raise ValueError("Read the current mixed state before saving")
        expected = self._context
        if state == expected["state"]:
            return deepcopy(state)
        connection = self._connection()
        for _ in range(3):
            document, sha = connection.read()
            snapshot = document.get(self.preset, {})
            if snapshot.get(STATE_KEY) != expected["state"] or mixed_requested(document) != expected["enabled"]:
                raise ValueError("Mixed state or switch changed; refresh before dispatch")
            candidate = deepcopy(document)
            candidate[self.preset] = mirror_mixed_snapshot(snapshot, state, completed_trading_date(now.astimezone(ET)).isoformat(), now)
            if candidate == document or connection.save(candidate, sha, self.preset) == "saved":
                self._context.update(state=deepcopy(state), snapshot=candidate[self.preset], document=candidate)
                return deepcopy(state)
        raise ValueError("Mixed state save conflicted repeatedly")

    def close(self):
        if self._github is not None and self._owns_github:
            self._github.close()


def strategy_order_tables(state, *, session_date, enabled, runtime_plan, r2_orders, daily_rows):
    """Display two independent sleeves without making conditional rows executable.

    A preview has no regular-session live quote. In particular, neither the ATR
    trigger nor its quantity can be invented from the previous close. Accepted
    orders remain visible when the switch is off; switching is not cancellation.
    """
    validate_state(state)
    labels = {"PLANNED": "전송 준비", "SENDING": "접수 확인 중", "UNKNOWN": "접수 여부 확인 필요",
              "ACCEPTED": "접수 확인 · 체결 대기", "OPEN": "미체결", "FILLED": "체결 완료 · 추가 전송 없음",
              "CANCELLED": "취소·만료 확인", "REJECTED": "거절 · 자동 재전송 없음"}
    phase = runtime_plan.get("phase", "WATCHING")
    blocked = phase in {"UNKNOWN", "WAITING_FLAT", "ATR_EXIT", "EXIT_INCOMPLETE", "WAITING_OPEN_REBALANCE", "REBALANCE", "WAITING_FILLS"}
    tables = {}
    for strategy, label in (("ATR", "ATR70 주문표"), ("R2", "R2 주문표")):
        tables[strategy] = {"label": label, "status": phase, "order_date": session_date,
                            "available_cash": str(available_cash(state, strategy)), "orders": [], "message": ""}
    # Today's durable receipts take precedence over a new recommendation. Keep
    # old UNKNOWN orders visible as they block all new dispatches after restart.
    existing = [order for order in state["orders"].values()
                if order["session_date"] == session_date or order["status"] not in {"FILLED", "CANCELLED", "REJECTED"}]
    for order in existing:
        kind, qty, filled = order["order_type"], order["quantity"], order["cumulative_quantity"]
        status = labels[order["status"]]
        if 0 < filled < qty:
            status = f"{status} · {filled}/{qty}주 체결"
        tables[order["strategy"]]["orders"].append({
            **deepcopy(order), "reference_price": order["price"] if kind == "LOC" else None,
            "condition": "저장된 주문 기록 · 접수와 체결은 별도 확인",
            "status": status, "filled_quantity": filled,
            "remaining_quantity": 0 if order["status"] in {"FILLED", "CANCELLED", "REJECTED"} else qty - filled,
            "existing_order": True, "display_only": True})
    # R2 orders are independent close-auction orders, with frozen lot exits.
    for raw in r2_orders:
        if raw["side"] == "BUY" and not enabled:
            continue
        if any(order["strategy"] == "R2" and order["side"] == raw["side"]
               and (raw["side"] == "BUY" or order["lot_id"] == raw.get("lot_id")) for order in existing):
            continue
        kind = raw["order_type"]
        condition = ("종가가 주문가 이하이면 매수" if raw["side"] == "BUY" else
                     "보유 만기 종가 청산" if kind == "MOC" else "종가가 주문가 이상이면 매도")
        tables["R2"]["orders"].append({**deepcopy(raw), "strategy": "R2",
            "reference_price": raw["price"] if kind == "LOC" else None,
            "condition": condition, "status": "선행 청산·비중 조정 후 재계산" if blocked else "종가 조건 주문안",
            "existing_order": False, "display_only": True})
    atr_exits = [order for order in runtime_plan.get("orders", []) if order["strategy"] == "ATR" and order["side"] == "SELL"]
    for order in atr_exits:
        if any(old["strategy"] == "ATR" and old["side"] == "SELL" and old["lot_id"] == order["lot_id"] for old in existing):
            continue
        overdue = order["order_type"] == "MARKET"
        tables["ATR"]["orders"].append({**deepcopy(order), "reference_price": None,
            "condition": "시가 매도 마감 후 정규장 청산 · 실제 보유수량 기준" if overdue else "다음 미국 거래일 시가 청산 · 실제 보유수량 기준",
            "status": "정규장 시장가 청산 대기" if overdue else "시가 매도 접수 대기",
            "existing_order": False, "display_only": True})
    existing_atr_entry = any(order["strategy"] == "ATR" and order["side"] == "BUY" for order in existing)
    if not enabled:
        tables["ATR"]["message"] = tables["R2"]["message"] = "스위치 OFF · 신규 매수 중단, 기접수 주문과 기존 청산 관리는 유지합니다."
    elif existing_atr_entry or state.get("last_atr_entry_session") == session_date:
        tables["ATR"]["message"] = "진행 중인 ATR70 매수 주문 또는 오늘 매수 기록이 있어 추가 매수하지 않습니다."
    elif blocked or any(lot["shares"] for lot in state["atr"]["lots"]):
        tables["ATR"]["message"] = "선행 청산·주문 확인·월초 비중 조정 후 돌파 조건을 다시 확인합니다."
        tables["R2"]["message"] = "선행 청산·주문 확인·월초 비중 조정 후 각 R2 주문을 다시 계산합니다."
    else:
        signal = atr70_signal(daily_rows, session_date=session_date)
        if not signal.get("ready") or signal.get("basis_date") != _sessions(session_date)[0]:
            tables["ATR"].update(status="HISTORY_UNAVAILABLE", message="ATR70 확정 일봉을 확인할 수 없어 신규 매수를 보류합니다.")
        elif not signal.get("passed"):
            tables["ATR"].update(status="FILTER_BLOCKED", message="전일 ATR 변동성 필터 미충족 · 오늘 돌파 신규 매수 없음")
        else:
            tables["ATR"].update(status="WAITING_BREAKOUT", message="ATR 필터 통과 · 정규장 시가와 실시간 돌파를 확인한 뒤 별도 매수합니다.")
            tables["ATR"]["orders"].append({"strategy": "ATR", "side": "BUY", "ticker": "SOXL",
                "order_type": "MARKET", "reference_price": None, "price": None, "quantity": None,
                "condition": "정규장 시가 × 1.015 이상 실시간 체결 · ATR70 예산 내 정수주",
                "status": "시가·돌파 확인 대기 · 수량은 조건 충족 시 확정", "existing_order": False, "display_only": True})
    if not tables["R2"]["message"]:
        tables["R2"]["message"] = "R2는 전략별 예산으로 별도 LOC/MOC 주문합니다. ATR70 주문과 합치지 않습니다."
    return tables


def build_mixed_preview(document, snapshot, now, plan):
    """A mixed preview cannot be dispatched through the legacy LOC runner."""
    state = deepcopy(snapshot[STATE_KEY])
    validate_state(state)
    if legacy_quantity(snapshot):
        raise ValueError("Legacy holdings cannot coexist with the mixed state")
    session = plan["order_date"]
    previous, following, first = _sessions(session)
    advanced = advance_r2_session(state, session)
    warnings = []
    try:
        daily, qqq = load_market_rows(now, previous)
        r2 = build_r2_orders(advanced, session_date=session, daily_rows=daily, qqq_rows=qqq,
                             enabled=mixed_requested(document))
    except Exception:
        daily = []
        r2 = build_r2_exit_orders(advanced, session_date=session)
        warnings.append("완료 일봉·주봉 데이터 확인 실패: 신규 매수는 중단하며 기존 청산 관리만 표시합니다.")
    from datetime import time
    from kmw_order_plan import ET
    market_now = now.astimezone(ET)
    overdue_exit = market_now.date().isoformat() == session and market_now.time().replace(tzinfo=None) >= time(9, 25)
    mixed = build_runtime_plan(advanced, session_date=session, previous_session=previous,
                               next_session=following, daily_rows=daily, r2_orders=r2,
                               first_session_of_month=first, atr_exit_order_type="MARKET" if overdue_exit else "MOO")
    enabled = mixed_requested(document)
    if not enabled:
        mixed["orders"] = [order for order in mixed["orders"] if order["side"] != "BUY"]
    tables = strategy_order_tables(advanced, session_date=session, enabled=enabled,
                                   runtime_plan=mixed, r2_orders=r2, daily_rows=daily)
    return {**plan, "status": "ready", "strategy_name": STRATEGY_ID, "mode": "MIXED",
            "atr70_r2_managed": True, "mixed_phase": mixed["phase"], "mixed_enabled": mixed_requested(document),
            "orders": mixed["orders"], "available_cash": float(_money(state["atr"]["cash"]) + _money(state["r2"]["cash"])),
            "strategy_order_tables": tables,
            "snapshot_positions": [{"strategy": strategy, "position_key": lot["lot_id"], "quantity": lot["shares"],
                                    "buy_date": lot["entry_date"], "buy_price": lot["entry_price"], "round": lot["round"]}
                                   for strategy in ("ATR", "R2") for lot in state[strategy.lower()]["lots"] if lot["shares"]],
            "mixed_state": deepcopy(state), "warnings": plan["warnings"] + warnings + [
                "반반매매 (ATR70-R2)는 전용 장중 엔진이 실행합니다. ATR70 50% + 정배열 R2 50%, 월초 비중 조정."]}


def refresh_mixed_document(document, sha, *, github, client, now):
    """Read-only broker reconciliation for preview; never dispatch mixed orders."""
    from atr70_r2_runtime import reconcile_broker
    from kmw_order_plan import ET, completed_trading_date
    from preset_snapshot_sync import SOURCE, SnapshotRefreshBlocked
    for _ in range(3):
        snapshot = document.get("KMW2", {})
        state = snapshot.get(STATE_KEY)
        validate_state(state)
        if state["phase"] != "ACTIVE":
            return document, SOURCE
        updated, _ = reconcile_broker(state, client, now=now)
        candidate = deepcopy(document)
        candidate["KMW2"] = mirror_mixed_snapshot(snapshot, updated, completed_trading_date(now.astimezone(ET)).isoformat(), now)
        if candidate == document or github.save(candidate, sha, "KMW2") == "saved":
            return candidate, SOURCE
        document, sha = github.read()
    raise SnapshotRefreshBlocked("Mixed snapshot changed repeatedly; refresh before ordering.", code="SNAPSHOT_CONFLICT")
