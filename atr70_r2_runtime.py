"""Explicit KMW2 live adapter for the pure mixed-strategy state machine.

Construction is inert. Every intent and its SENDING state are persisted before
dispatch; quotes never create holdings. Foreign open orders, stale market data
and ambiguous receipts stop execution instead of causing automatic retries.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import threading
from zoneinfo import ZoneInfo

import atr70_r2 as core
from kmw_autotrade import OrderBlocked, OrderLedger, symbol
from kmw_order_plan import completed_trading_date
from meritz_api import AmbiguousSubmission, MeritzError, SubmissionStopped
from meritz_request_pacer import MeritzPacingError
from us_market_calendar import is_us_equity_trading_day

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
CHANNEL = "ohhts_exst_cntg"
WS_URL = "wss://openapi.imeritz.com:29443/websocket"


class MixedBlocked(OrderBlocked):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _money(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and not result):
            raise ValueError
        return result
    except (ValueError, InvalidOperation, TypeError):
        raise MixedBlocked("INVALID_BROKER_FACT", "메리츠 수량·금액 정보를 확인할 수 없습니다.") from None


def _quantity(value):
    number = _money(value)
    if number != number.to_integral_value():
        raise MixedBlocked("INVALID_BROKER_FACT", "정수 주식 수량을 확인하세요.")
    return int(number)


def normalize_trade_message(message, *, key, now):
    """Validate the official regular-session WS event, including exchange time."""
    if isinstance(message, (str, bytes)):
        try:
            message = json.loads(message)
        except (ValueError, TypeError):
            raise MixedBlocked("INVALID_QUOTE", "실시간 체결 메시지 형식을 확인하세요.") from None
    header, body = message.get("header", {}), message.get("body", {})
    if (header.get("tr_cd") != CHANNEL or header.get("tr_key") != key
            or body.get("dely_yn") != "0"
            or body.get("iscd") not in {key, "SOXL", "SOXL." + key.split("@", 1)[0]}):
        raise MixedBlocked("INVALID_QUOTE", "SOXL 정규장 실시간 체결 채널을 확인하세요.")
    market_now = now.astimezone(ET)
    try:
        session = datetime.strptime(str(body["acpl_bsop_date"]), "%Y%m%d").date()
        stamp = datetime.strptime(str(body["korea_date"]) + str(body["korea_hour"]).zfill(6),
                                  "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except (KeyError, ValueError, TypeError):
        raise MixedBlocked("STALE_QUOTE", "실시간 체결의 날짜와 시각을 확인할 수 없습니다.") from None
    age = (now - stamp).total_seconds()
    if (session != market_now.date() or stamp.astimezone(ET).date() != session
            or not is_us_equity_trading_day(session) or age < -2 or age > 15
            or not time(9, 30) <= stamp.astimezone(ET).time().replace(tzinfo=None) < time(16)):
        raise MixedBlocked("STALE_QUOTE", "SOXL 실시간 정규장 체결이 15초 이내인지 확인하세요.")
    return {"symbol": "SOXL", "session_date": session.isoformat(),
            "last_price": str(_money(body.get("prpr"), positive=True)),
            "regular_open": str(_money(body.get("oprc"), positive=True)),
            "timestamp": stamp.isoformat(), "received_at": now.isoformat(),
            "source": "meritz_websocket", "realtime": True}


def fresh_quote(quote, now, session_date):
    if not isinstance(quote, dict) or quote.get("source") != "meritz_websocket" or quote.get("realtime") is not True:
        raise MixedBlocked("STALE_QUOTE", "검증된 실시간 SOXL 체결이 필요합니다.")
    try:
        stamp = datetime.fromisoformat(quote["timestamp"])
        received = datetime.fromisoformat(quote["received_at"])
        if stamp.tzinfo is None or received.tzinfo is None:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise MixedBlocked("STALE_QUOTE", "실시간 체결의 시각을 확인할 수 없습니다.") from None
    if (quote.get("symbol") != "SOXL" or quote.get("session_date") != session_date
            or stamp.astimezone(ET).date().isoformat() != session_date
            or not -2 <= (now - stamp).total_seconds() <= 15
            or not -2 <= (now - received).total_seconds() <= 15):
        raise MixedBlocked("STALE_QUOTE", "실시간 체결이 오래되었거나 거래일이 다릅니다.")
    _money(quote["last_price"], positive=True)
    _money(quote["regular_open"], positive=True)
    return quote


class MeritzTradeFeed:
    """Lazy background subscription; reconnecting never submits an order."""
    def __init__(self, client, *, key="AX@SOXL", clock=None, connect=None):
        self.client, self.key = client, key
        self.clock = clock or (lambda: datetime.now(KST))
        self._connect = connect
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._quote = None

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="kmw2-soxl-trades")
            self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self._connect is None:
                    from websockets.sync.client import connect
                else:
                    connect = self._connect
                token = self.client.websocket_token()
                with connect(WS_URL, open_timeout=15, close_timeout=3, max_size=1048576) as socket:
                    socket.send(json.dumps({"header": {"token": "Bearer " + token, "tr_type": "1"},
                                            "body": {"tr_cd": CHANNEL, "tr_key": self.key}}))
                    while not self._stop.is_set():
                        try:
                            raw = socket.recv(timeout=2)
                        except TimeoutError:
                            continue
                        try:
                            quote = normalize_trade_message(raw, key=self.key, now=self.clock())
                        except (MixedBlocked, AttributeError):
                            continue  # Subscription receipts/pings aren't market evidence.
                        with self._lock:
                            self._quote = quote
            except Exception:
                with self._lock:
                    self._quote = None
                self._stop.wait(5)

    def latest(self):
        with self._lock:
            return deepcopy(self._quote)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)


def mixed_order_window(now, order):
    market = now.astimezone(ET)
    if order.get("session_date") != market.date().isoformat() or not is_us_equity_trading_day(market.date()):
        raise MixedBlocked("ORDER_DATE", "혼합 주문의 미국 거래일을 확인하세요.")
    current = market.time().replace(tzinfo=None)
    kind = order.get("order_type")
    if kind == "MOO":
        if order.get("side") != "SELL" or current >= time(9, 25):
            raise MixedBlocked("MOO_WINDOW", "익일 시가 매도는 미국시간 09:25 전에 접수해야 합니다.")
    elif kind == "MARKET":
        if not time(9, 30) <= current < time(16):
            raise MixedBlocked("MARKET_WINDOW", "혼합 시장가 주문은 미국 정규장에서만 접수합니다.")
    elif kind in {"LOC", "MOC"}:
        # Submit well before both normal and half-day closing-auction cutoffs.
        if current >= time(12, 45):
            raise MixedBlocked("CLOSE_WINDOW", "혼합 종가 주문의 접수 마감 시간을 지났습니다.")
    else:
        raise MixedBlocked("ORDER_TYPE", "지원하지 않는 혼합 주문 유형입니다.")


def _positions(state):
    return sum(lot["shares"] for leg in ("atr", "r2") for lot in state[leg]["lots"])


def _held(rows):
    from preset_snapshot_sync import _holding_quantity
    return _holding_quantity(rows)


def _broker_cash(client):
    rows = [row for row in client.currencies() if row.get("crcd") == "USD"]
    if len(rows) != 1:
        raise MixedBlocked("WAITING_CASH", "실제 USD 현금을 확인하세요.")
    actual = _money(rows[0].get("frcr_amt"))
    estimated = _money(rows[0].get("estm_frcr_amt"))
    if actual != estimated:
        raise MixedBlocked("WAITING_CASH", "결제 대기 현금이 정리된 뒤 혼합 전략을 시작합니다.")
    return str(actual)


def reconcile_broker(state, client, *, now, reservation_start_date="2026-04-29"):
    """Map only owned broker IDs and use cumulative actual execution amounts."""
    from preset_snapshot_sync import _apply_confirmed_cancellations, _completed_auction_order, normalize_confirmed_fills
    result = deepcopy(state)
    owned = {(order["session_date"], str(order["broker_order_id"])): identity
             for identity, order in state["orders"].items() if order.get("broker_order_id")}
    start = min((day for day, _ in owned), default=state["activated_session"])
    today = now.astimezone(ET).date().isoformat()
    history = client.orders_history(start.replace("-", ""), today.replace("-", "")) if owned else []
    fills = normalize_confirmed_fills(history)
    normalized = []
    for fill in fills:
        identity = owned.get((fill["trade_date"], str(fill["order_id"])))
        if identity is None:
            # A baseline predates activation; any later unknown trade changes ownership.
            if fill["trade_date"] >= state["activated_session"]:
                raise MixedBlocked("FOREIGN_FILL", "혼합 장부에 없는 SOXL 체결이 있습니다.")
            continue
        order = result["orders"][identity]
        if fill["side"] != order["side"]:
            raise MixedBlocked("FILL_MISMATCH", "혼합 주문과 실제 체결의 방향이 다릅니다.")
        quantity = _quantity(fill["quantity"])
        notional = fill.get("notional", str(_money(fill["price"]) * quantity))
        status = "FILLED" if quantity == order["quantity"] else "OPEN"
        if order["status"] in {"CANCELLED", "REJECTED"} and quantity == order["cumulative_quantity"]:
            status = order["status"]
        normalized.append({"order_id": identity, "broker_order_id": str(fill["order_id"]),
            "cumulative_quantity": quantity,
            "cumulative_notional": notional,
            # The broker history has no commission field. Reserve configured
            # costs explicitly; never label this amount an actual broker fee.
            "cumulative_fee": str(_money(notional) * _money(state["fee_rate"])),
            "fee_source": "CONFIGURED_RESERVE",
            "status": status})
    if normalized:
        result = core.reconcile_fills(result, normalized)
        result["fee_source"] = "configured_reserve"
    current = _apply_confirmed_cancellations(client.orders_today())
    completed = completed_trading_date(now.astimezone(ET))
    active_rows = []
    for row in current:
        if symbol(row) != "SOXL":
            continue
        try:
            day = datetime.strptime(str(row["oder_date"]), "%Y%m%d").date().isoformat()
        except (ValueError, KeyError):
            raise MixedBlocked("FOREIGN_ORDER", "SOXL 주문일을 확인할 수 없습니다.") from None
        key = (day, str(row.get("oder_no")))
        identity = owned.get(key)
        remaining = _quantity(row.get("odqt")) - _quantity(row.get("cntg_qty", 0)) - _quantity(row.get("cncl_cnfr_qty", 0)) - _quantity(row.get("rctf_cnfr_qty", 0))
        if remaining < 0:
            raise MixedBlocked("ORDER_MISMATCH", "SOXL 주문 수량이 일치하지 않습니다.")
        rejected = str(row.get("ovrs_stck_oder_stat_cls_code")) in {"1", "2", "4"}
        if str(row.get("ovrs_stck_oder_stat_cls_code")) not in {"0", "1", "2", "4"}:
            raise MixedBlocked("ORDER_MISMATCH", "SOXL 주문의 메리츠 상태를 확인하세요.")
        expired = _completed_auction_order(row, completed)
        if remaining > 0 and not rejected and not expired:
            if identity is None:
                raise MixedBlocked("FOREIGN_ORDER", "혼합 전략에 속하지 않는 SOXL 미체결 주문이 있습니다.")
            order = result["orders"][identity]
            if (_quantity(row["odqt"]) != order["quantity"] or
                    {"1": "SELL", "2": "BUY"}.get(str(row.get("seln_byov_clcd"))) != order["side"]):
                raise MixedBlocked("ORDER_MISMATCH", "SOXL 미체결 주문의 소유권과 수량을 확인하세요.")
            active_rows.append(row)
        elif identity and result["orders"][identity]["status"] not in core.TERMINAL_STATUSES:
            status = "REJECTED" if rejected else "CANCELLED"
            if result["orders"][identity]["cumulative_quantity"] == result["orders"][identity]["quantity"]:
                status = "FILLED"
            result = core.mark_order(result, identity, status=status)
    # A real today endpoint needn't retain yesterday's auction rows. Completed
    # session-only auctions expire after their full history has been reconciled;
    # generic market orders never gain an invented cancellation from absence.
    for identity, order in list(result["orders"].items()):
        if (order["status"] in {"ACCEPTED", "OPEN"} and order.get("broker_order_id")
                and order["order_type"] in {"LOC", "MOC", "MOO"}
                and date.fromisoformat(order["session_date"]) <= completed):
            status = "FILLED" if order["cumulative_quantity"] == order["quantity"] else "CANCELLED"
            result = core.mark_order(result, identity, status=status)
    if _held(client.holdings()) != _positions(result):
        raise MixedBlocked("HOLDINGS_MISMATCH", "실제 SOXL 보유수량과 혼합 장부가 다릅니다.")
    reservation_start = date.fromisoformat(reservation_start_date).strftime("%Y%m%d")
    for row in client.reserved_orders(reservation_start, today.replace("-", ""), active_only=True):
        if symbol(row) == "SOXL":
            raise MixedBlocked("FOREIGN_ORDER", "기존 SOXL 예약주문을 확인하세요.")
    return result, active_rows


def _preflight_order(client, state, order, *, quote, now, active_rows):
    mixed_order_window(now, order)
    if order["order_type"] == "MARKET":
        fresh_quote(quote, now, order["session_date"])
    if order["side"] == "BUY":
        rows = [row for row in client.order_estimate() if row.get("crcd") == "USD"]
        if len(rows) != 1 or _money(rows[0].get("othr_crnc_evdn_camt", 0)):
            raise MixedBlocked("BUYING_POWER", "독립 USD 주문가능금액을 확인하세요.")
        required = _money(order["price"], positive=True) * order["quantity"] * (1 + _money(state["fee_rate"]))
        if required > _money(rows[0].get("oder_crnc_oder_able_camt")):
            raise MixedBlocked("BUYING_POWER", "혼합 매수에 필요한 USD 주문가능금액이 부족합니다.")
    else:
        rows = [row for row in client.holdings() if symbol(row) == "SOXL"]
        sellable = sum(_quantity(row.get("seln_able_dqty")) for row in rows)
        held = _held(rows)
        reserved = sum(_quantity(row.get("odqt")) - _quantity(row.get("cntg_qty", 0)) - _quantity(row.get("cncl_cnfr_qty", 0))
                       for row in active_rows if str(row.get("seln_byov_clcd")) == "1")
        if order["quantity"] > sellable or order["quantity"] + reserved > held:
            raise MixedBlocked("SELLABLE", "혼합 매도 예약을 포함한 실제 매도가능수량이 부족합니다.")


def run_mixed_tick(client, state, context, persist_state, ledger, *, now, enabled,
                   account, exchange_code, quote=None, should_continue=None,
                   clock=None, before_order=None, quote_provider=None):
    """One bounded poll. ``persist_state`` must atomically CAS the remote state."""
    if context.get("preset", "KMW2") != "KMW2" or type(enabled) is not bool:
        raise MixedBlocked("PRESET_MISMATCH", "혼합 실행은 KMW2 전용입니다.")
    session = now.astimezone(ET).date().isoformat()
    current_time = clock or (lambda: now)
    latest_quote = quote_provider or (lambda: quote)
    if state is None and not enabled:
        return {"handled": False, "state": "INACTIVE", "orders": []}
    if (not is_us_equity_trading_day(now.astimezone(ET).date())
            or context.get("session_date", session) != session):
        return {"handled": bool(state and state.get("phase") == "ACTIVE"),
                "state": "WAITING_NEXT_SESSION", "orders": []}
    ledger.unresolved(account)
    if state is None or state["phase"] == "WAITING_FLAT":
        actual_shares = _held(client.holdings())
        from preset_snapshot_sync import _open_orders
        today_orders = _open_orders(client.orders_today(), completed_basis=completed_trading_date(now.astimezone(ET)))
        reservation_start = date.fromisoformat(context.get("reservation_start_date", "2026-04-29")).strftime("%Y%m%d")
        reservations = [row for row in client.reserved_orders(reservation_start, session.replace("-", ""), active_only=True) if symbol(row) == "SOXL"]
        if not enabled:
            return {"handled": False, "state": "WAITING_FLAT", "orders": []}
        if actual_shares or context.get("legacy_shares", 0) or today_orders or reservations or context.get("broker_confirmed") is not True:
            return {"handled": False, "state": "WAITING_FLAT", "orders": []}
        cash = _broker_cash(client)
        state = core.initial_state("KMW2", session_date=session, broker_cash=cash, broker_shares=0,
                                   open_order_count=0, broker_confirmed=True,
                                   fee_rate=context.get("fee_rate", "0.005"))
        state["fee_source"] = "configured_reserve"
        persist_state(state)
    core.validate_state(state)
    state, active_rows = reconcile_broker(state, client, now=now,
        reservation_start_date=context.get("reservation_start_date", "2026-04-29"))
    state = core.advance_r2_session(state, session)
    persist_state(state)
    usable_quote = None
    try:
        usable_quote = fresh_quote(latest_quote(), current_time(), session)
    except MixedBlocked:
        pass
    pending_rebalance = state.get("pending_rebalance")
    month_due = session[:7] not in state["rebalance_months"]
    if usable_quote and (pending_rebalance or month_due) and not any(lot["shares"] for lot in state["atr"]["lots"]):
        actual_cash = _money(_broker_cash(client))
        accounted_cash = _money(state["atr"]["cash"]) + _money(state["r2"]["cash"])
        if actual_cash < accounted_cash:
            raise MixedBlocked("CASH_RECONCILIATION", "실제 USD 현금이 혼합 장부보다 적습니다. 비용과 외부 거래를 대조하세요.")
        # Excess broker cash may be settlement/commission differences or an
        # external flow. Preserve it outside both strategy spending budgets.
        state["unallocated_cost_cash"] = str(actual_cash - accounted_cash)
        persist_state(state)
    if pending_rebalance and usable_quote:
        try:
            state = core.settle_monthly_rebalance(state, pending_rebalance,
                                                  valuation_price=usable_quote["last_price"])
        except ValueError:
            relevant = [state["orders"].get(order["intent_id"]) for order in pending_rebalance["orders"]]
            if (all(order and order["status"] == "FILLED" for order in relevant)
                    or any(order and order["status"] in {"CANCELLED", "REJECTED"} for order in relevant)):
                raise MixedBlocked("REBALANCE_REVIEW_REQUIRED", "월초 실제 체결과 비중 조정 금액을 확인하세요. 원래 계획을 보존했습니다.") from None
            # All partial sales must become confirmed full fills first.
        else:
            state.pop("pending_rebalance", None)
            persist_state(state)
    if context.get("qqq_rows"):
        from atr70_r2_adapter import build_r2_orders
        try:
            r2_orders = build_r2_orders(state, session_date=session,
                daily_rows=context["daily_rows"], qqq_rows=context["qqq_rows"], enabled=enabled)
        except ValueError:
            # An unavailable RSI must never delay an already-owned ATR exit.
            r2_orders = [order for order in context.get("r2_orders", []) if order.get("side") == "SELL"]
    else:
        r2_orders = (context["r2_order_factory"](state) if callable(context.get("r2_order_factory"))
                     else context.get("r2_orders", []))
    plan = core.build_runtime_plan(state, session_date=session,
        previous_session=context["previous_session"], next_session=context["next_session"],
        regular_open=usable_quote["regular_open"] if usable_quote else None,
        last_price=usable_quote["last_price"] if usable_quote else None,
        daily_rows=context.get("daily_rows"), r2_orders=r2_orders,
        first_session_of_month=context.get("first_session_of_month", False),
        atr_exit_order_type="MOO" if now.astimezone(ET).time().replace(tzinfo=None) < time(9, 25) else "MARKET")
    if plan.get("rebalance"):
        state["pending_rebalance"] = plan["rebalance"]
        persist_state(state)
        if not plan["orders"]:
            state = core.settle_monthly_rebalance(state, plan["rebalance"],
                                                  valuation_price=usable_quote["last_price"])
            state.pop("pending_rebalance", None)
            persist_state(state)
            return {"handled": True, "state": "REBALANCED", "phase": plan["phase"], "orders": [], "plan": plan}
    sent = []
    for order in plan["orders"]:
        if order["side"] == "BUY" and not enabled:
            continue
        if should_continue is not None and not should_continue():
            break
        # A missing current quote blocks market entries/rebalancing, not MOO exits.
        if order["order_type"] == "MARKET":
            usable_quote = fresh_quote(latest_quote(), current_time(), session)
            if order["side"] == "BUY":
                last = _money(usable_quote["last_price"], positive=True)
                if last < _money(order.get("trigger_price", 0)) or last * Decimal("1.1") > _money(order["price"]):
                    raise MixedBlocked("PRICE_CHANGED", "돌파 가격과 매수 예산을 다시 계산해야 합니다.")
        _preflight_order(client, state, order, quote=usable_quote, now=current_time(), active_rows=active_rows)
        daily_plan = {"preset": "KMW2", "order_date": session, "orders": [order]}
        identity = OrderLedger.identity(account, daily_plan, order)
        previous = ledger.db.execute("SELECT state FROM orders WHERE identity=?", (identity,)).fetchone()
        if previous:
            raise MixedBlocked("ALREADY_ATTEMPTED", "같은 혼합 주문의 전송 기록이 있습니다. 자동 재전송하지 않습니다.")
        state = core.register_order(state, order)
        persist_state(state)
        ledger.claim(account, daily_plan)
        ledger.update(identity, "SENDING")
        state = core.mark_order(state, order["intent_id"], status="SENDING")
        persist_state(state)
        try:
            # Revalidate time after broker reads and persistence, before HTTP.
            mixed_order_window(current_time(), order)
            if order["order_type"] == "MARKET":
                usable_quote = fresh_quote(latest_quote(), current_time(), order["session_date"])
                if order["side"] == "BUY":
                    last = _money(usable_quote["last_price"], positive=True)
                    if last < _money(order.get("trigger_price", 0)) or last * Decimal("1.1") > _money(order["price"]):
                        raise MixedBlocked("PRICE_CHANGED", "전송 전 돌파 가격과 매수 예산이 달라졌습니다.")
            if should_continue is not None and not should_continue():
                raise SubmissionStopped("Mixed submission stopped")
            if before_order is not None:
                before_order(order)
            body = client.submit_mixed_order(preset="KMW2", side=order["side"].lower(), symbol="SOXL",
                exchange_vendor_code=exchange_code, quantity=order["quantity"],
                price=order["price"] if order["order_type"] == "LOC" else "0", order_type=order["order_type"])
            broker_id = str(body.get("data", {}).get("oder_no", ""))
            if not broker_id or not broker_id.isdigit() or int(broker_id) <= 0:
                raise AmbiguousSubmission("Missing mixed receipt")
            ledger.update(identity, "DIRECT_ACCEPTED", body)
            state = core.mark_order(state, order["intent_id"], broker_order_id=broker_id, status="ACCEPTED")
            persist_state(state)
            sent.append({"order": order, "state": "DIRECT_ACCEPTED", "response": body})
        except Exception as exc:
            status = ("UNKNOWN" if isinstance(exc, AmbiguousSubmission) else "REJECTED"
                      if isinstance(exc, (MeritzError, SubmissionStopped, MeritzPacingError, MixedBlocked)) else "UNKNOWN")
            ledger.update(identity, "NOT_SENT" if isinstance(exc, (SubmissionStopped, MeritzPacingError, MixedBlocked)) else status,
                          getattr(exc, "response", None))
            state = core.mark_order(state, order["intent_id"], status=status)
            try:
                persist_state(state)
            except Exception:
                pass  # Durable SENDING/UNKNOWN and receipts still prohibit retries.
            exc.mixed_context = {"preset": "KMW2", "account": account, "order_route": "direct",
                "plan": {"preset": "KMW2", "order_date": session, "generated_at": current_time().isoformat(),
                         "orders": plan["orders"]}, "results": sent, "failed_order": order,
                "failed_state": status, "not_sent_count": len(plan["orders"]) - len(sent) - 1}
            raise
    return {"handled": True, "state": "DIRECT_ACCEPTED" if sent else "WAITING",
            "phase": plan["phase"], "orders": sent, "plan": plan}
