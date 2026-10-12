"""KMW engine adapter with inert construction and explicit broker execution.

The engine's scheduler calls ``preview`` and, only after user activation,
``execute``.  This adapter reuses the standalone runner's validation, durable
order identities, and Telegram outbox.  Importing or constructing it cannot
load credentials, contact a broker, or send a notification.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

from kmw_autotrade import (
    KST, ROOT, OrderBlocked, OrderLedger, account_id, configured_notifier,
    direct_order_window, load_env, load_snapshots, notify_order_result, preflight, submit_plan,
    symbol, validate_plan,
)
from kmw_order_plan import build_kmw_plan
from meritz_api import AmbiguousSubmission, MeritzClient, MeritzError


class EngineBlocked(ValueError):
    """An actionable public error; messages never contain transport details."""

    def __init__(self, message: str, *, code: str = "BLOCKED"):
        super().__init__(message)
        self.code = code


_STATES = frozenset({
    "PLANNED", "SENDING", "UNKNOWN", "NOT_SENT", "REJECTED", "RESERVED",
    "DIRECT_ACCEPTED", "CANCELLED", "STOPPED", "FILLED", "PARTIALLY_FILLED",
    "OPEN", "INVALID", "BLOCKED",
})
_ORDER_FIELDS = (
    "ticker", "side", "quantity", "price", "order_type", "round",
    "position_key", "reason", "expiry_date",
)


def _now(value: datetime | None) -> datetime:
    current = value if value is not None else datetime.now(KST)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise EngineBlocked("실행 시각에 시간대 정보가 필요합니다.", code="INVALID_TIME")
    return current


def _failure(exc: Exception) -> tuple[str, str]:
    """Classify an error without forwarding its body, URL, or arbitrary text."""
    if isinstance(exc, EngineBlocked):
        return exc.code, str(exc)
    if isinstance(exc, AmbiguousSubmission):
        return "UNKNOWN_SUBMISSION", "주문 접수 여부가 불확실합니다. 메리츠 주문 내역을 확인하기 전에는 다시 전송하지 않습니다."
    if isinstance(exc, OrderBlocked):
        message = str(exc)
        cases = (
            ("Submission stopped by user", "USER_STOPPED", "자동매매가 비활성화되어 남은 주문 전송을 중단했습니다."),
            ("already attempted", "ALREADY_ATTEMPTED", "오늘 같은 계좌·회차의 주문을 이미 시도했습니다. 주문 내역을 확인하고 기존 기록을 유지해 주세요. 자동 재전송하지 않습니다."),
            ("unknown result", "UNRESOLVED_ORDER", "이전 주문의 접수 여부가 불확실합니다. 메리츠 주문 내역과 로컬 기록을 먼저 확인해 주세요."),
            ("broker holds", "HOLDINGS_MISMATCH", "웹앱 KMW 보유 수량과 메리츠 실제 보유 수량이 다릅니다. 실제 체결 내역으로 웹앱을 갱신해 주세요."),
            ("snapshot is stale", "STALE_SNAPSHOT", "KMW 보유 데이터가 최신 거래일 기준이 아닙니다. 실제 보유 내역으로 웹앱을 갱신해 주세요."),
            ("most recent completed", "STALE_MARKET_DATA", "추천 주문표가 직전 완료 거래일 기준이 아닙니다. 최신 데이터로 다시 계산해 주세요."),
            ("existing SOXL order", "OPEN_ORDER", "미체결 SOXL 주문이 있습니다. 메리츠 주문 내역을 확인하기 전에는 추가 주문을 보내지 않습니다."),
            ("active SOXL reservation", "OPEN_RESERVATION", "활성 SOXL 예약주문이 있습니다. 메리츠 예약주문 내역을 먼저 확인해 주세요."),
            ("below the required", "INSUFFICIENT_CASH", "메리츠 달러 주문가능금액이 추천 매수금액과 수수료 여유금보다 부족합니다."),
            ("sellable quantity", "INSUFFICIENT_SELLABLE", "메리츠 매도가능수량이 추천 매도수량보다 부족합니다."),
            ("pre-open", "OUTSIDE_ORDER_WINDOW", "현재 엔진은 미국 정규장 시작 전의 KMW 추천 주문만 전송합니다. 실행 시각을 확인해 주세요."),
            ("trading session", "WRONG_SESSION", "추천 주문일이 현재 미국 거래일과 다릅니다. 실행 시각과 주문표를 확인해 주세요."),
            ("MERITZ_", "CONFIGURATION", "메리츠 API의 로컬 환경 설정을 확인해 주세요. KMW 계좌 설정과 인증정보가 필요합니다."),
            ("order unit", "ORDER_UNITS", "메리츠 호가·수량 단위를 확인할 수 없거나 추천 주문과 맞지 않습니다. 로컬 호가 단위 설정을 확인해 주세요."),
            ("order units", "ORDER_UNITS", "추천 가격 또는 수량이 메리츠 주문 단위와 맞지 않습니다. 주문표를 확인해 주세요."),
        )
        for fragment, code, public in cases:
            if fragment in message:
                return code, public
        return "INVALID_PLAN", "KMW 주문표 또는 계좌 조건 검증을 통과하지 못했습니다. 주문표와 실제 보유 내역을 확인해 주세요."
    if isinstance(exc, MeritzError):
        return "BROKER_ERROR", "메리츠 API 요청을 완료하지 못했습니다. 주문 내역과 API 연결 상태를 확인해 주세요. 자동 재전송하지 않습니다."
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return "LOCAL_CONFIGURATION", "로컬 설정 파일 또는 주문 기록을 읽을 수 없습니다. 파일 경로와 권한을 확인해 주세요."
    if isinstance(exc, ValueError):
        message = str(exc)
        if "Stale" in message or "stale" in message:
            return "STALE_SNAPSHOT", "KMW 추천 또는 보유 데이터가 최신 거래일 기준이 아닙니다. 웹앱 데이터를 갱신해 주세요."
        if "MERITZ_" in message:
            return "CONFIGURATION", "메리츠 API의 로컬 인증정보와 MAC 주소 설정을 확인해 주세요."
        return "INVALID_DATA", "KMW 데이터 또는 로컬 설정이 올바르지 않습니다. 웹앱과 설정 파일을 확인해 주세요."
    return "ENGINE_ERROR", "자동매매 실행을 완료하지 못했습니다. 로컬 주문 기록과 메리츠 주문 내역을 확인해 주세요. 자동 재전송하지 않습니다."


def _nonnegative_number(value):
    try:
        if isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quantity(value):
    number = _nonnegative_number(value)
    return int(number) if number is not None and number == number.to_integral_value() else None


def _number(value):
    quantity = _quantity(value)
    return str(quantity) if quantity is not None and quantity > 0 else None


def _day(value):
    raw = str(value or "")
    if re.fullmatch(r"\d{8}", raw):
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _safe_order(order):
    """Public results keep recommendation fields and omit arbitrary metadata."""
    if not isinstance(order, dict):
        return {}
    result = {}
    for field in _ORDER_FIELDS:
        value = order.get(field)
        if field == "ticker" and value == "SOXL":
            result[field] = value
        elif field == "side" and isinstance(value, str) and value in {"BUY", "SELL"}:
            result[field] = value
        elif field == "order_type" and isinstance(value, str) and value in {"LOC", "MOC"}:
            result[field] = value
        elif field in {"quantity", "round"}:
            quantity = _quantity(value)
            if quantity is not None:
                result[field] = quantity
        elif field == "price":
            number = _nonnegative_number(value)
            if number is not None:
                result[field] = format(number, "f")
        elif field == "position_key" and isinstance(value, str) and re.fullmatch(r"[1-9]\d*_\d{4}-\d{2}-\d{2}", value):
            result[field] = value
        elif field == "reason" and isinstance(value, str) and value in {"holding_period_expired", "target_price", "daily_recommendation"}:
            result[field] = value
        elif field == "expiry_date" and _day(value):
            result[field] = _day(value)
    return result


def _public_result(row):
    body = row.get("response")
    data = body.get("data") if isinstance(body, dict) else None
    data = data if isinstance(data, dict) else {}
    state = row.get("state")
    result = {
        "order": _safe_order(row.get("order")),
        "state": state if isinstance(state, str) and state in _STATES else "UNKNOWN",
        "order_no": _number(data.get("oder_no")),
        "reservation_no": _number(data.get("oder_rsev_mst_no")),
    }
    if _day(row.get("order_date")):
        result["order_date"] = _day(row["order_date"])
    updated = row.get("updated_at")
    try:
        if isinstance(updated, str):
            result["updated_at"] = datetime.fromisoformat(updated).isoformat()
    except ValueError:
        pass
    return result


class KMWBackend:
    """Direct LOC/MOC adapter. All external work starts in explicit methods."""

    def __init__(self, env_file: Path | str | None = ROOT / ".env.meritz",
                 ledger_path: Path | str = ROOT / ".autotrade" / "orders.sqlite3", *,
                 clock: Callable[[], datetime] | None = None):
        self.env_file = Path(env_file) if env_file is not None else None
        self.ledger_path = Path(ledger_path)
        self._clock = clock if clock is not None else lambda: datetime.now(KST)

    def _load_credentials(self):
        if self.env_file is not None:
            load_env(self.env_file)

    def preview(self, now: datetime | None = None) -> dict:
        """Fetch the same persisted KMW portfolio as the web app; no broker calls."""
        current = _now(now)
        try:
            snapshots, source = load_snapshots()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                plan = build_kmw_plan(snapshots, now=current)
            plan = json.loads(json.dumps(plan, ensure_ascii=False, allow_nan=False))
            plan["order_route"] = "direct"
            plan["snapshot_source"] = source
            return plan
        except Exception as exc:
            code, message = _failure(exc)
            raise EngineBlocked(message, code=code) from None

    def execute(self, plan: dict, now: datetime | None = None,
                should_continue: Callable[[], bool] | None = None) -> dict:
        """Validate and send each recommended row once after user activation.

        The scheduler owns activation and run locking. ``should_continue``
        permits a stop between orders; an in-flight broker request must finish
        and remain durably recorded. No quantities, prices, or sides are altered.
        Operational failures return a safe result, keeping the original ledger
        state and any accepted prefix. No ambiguous request is retried.
        """
        ledger = None
        context = {}
        notifier = None
        checked = None
        results = []
        outcome = None
        failure = None
        financial_started = False
        deadline_reached = False

        def may_dispatch():
            nonlocal deadline_reached
            if should_continue is not None and not should_continue():
                return False
            try:
                # Validate a fresh instant after network reads and before each
                # transaction. The starting recommendation time can go stale
                # while a broker request is in flight.
                direct_order_window(_now(self._clock()), plan["order_date"])
            except OrderBlocked:
                deadline_reached = True
                return False
            return True

        try:
            current = _now(now)
            if should_continue is not None and not should_continue():
                return {"state": "STOPPED", "orders": [], "preflight": None,
                        "error_code": "USER_STOPPED", "error": "자동매매가 비활성화되어 주문을 전송하지 않았습니다."}
            if not isinstance(plan, dict):
                raise EngineBlocked("KMW 주문표가 필요합니다.", code="INVALID_PLAN")
            plan = deepcopy(plan)
            plan["order_route"] = "direct"
            if plan.get("preset") == "KMW" and plan.get("status") == "skipped":
                return {"state": "SKIPPED", "orders": [], "preflight": None}
            context = {"plan": plan, "order_route": "direct", "results": []}
            # Validate the recommendation date and each row's format before
            # constructing a client. Do not compare independent rows' prices.
            validate_plan(plan, current, order_route="direct")
            self._load_credentials()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                notifier = configured_notifier()
            account = account_id()
            ledger = OrderLedger(self.ledger_path)
            ledger.unresolved(account)
            # Check durable identities before broker reads; keep the previous
            # test BUY receipt and never delete/replace it to force a new batch.
            duplicate_rows = []
            for order in plan["orders"]:
                identity = OrderLedger.identity(account, plan, order)
                previous = ledger.db.execute(
                    "SELECT payload,state,response FROM orders WHERE identity=?", (identity,)
                ).fetchone()
                if previous:
                    payload, state, response = previous
                    duplicate_rows.append({"order": json.loads(payload), "state": state,
                                           "response": json.loads(response) if response else None})
            if duplicate_rows:
                includes_test = any(row["order"].get("side") == "BUY" and row["order"].get("quantity") == 1
                                    for row in duplicate_rows)
                detail = "기존 1주 테스트 주문을 포함한 메리츠 내역" if includes_test else "메리츠 주문 내역"
                outcome = {"state": "BLOCKED", "orders": [_public_result(row) for row in duplicate_rows],
                           "preflight": None, "error_code": "ALREADY_ATTEMPTED",
                           "error": f"오늘 같은 계좌·회차의 주문 기록이 있습니다. {detail}을 확인해 주세요. 기록을 삭제하거나 자동 재전송하지 않습니다."}
                raise OrderBlocked("This account/date/position was already attempted")
            client = MeritzClient.from_env()
            checked = preflight(client, plan, current, Decimal("0.005"), order_route="direct")
            # Recheck activation after read-only preflight, before claiming or
            # sending any identities. submit_plan also checks between orders.
            if not may_dispatch():
                context.update(failed_state="STOPPED", not_sent_count=len(plan["orders"]))
                raise OrderBlocked("Submission stopped by user")
            if not plan["orders"]:
                outcome = {"state": "COMPLETED", "orders": [], "preflight": checked}
            else:
                financial_started = True
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    results = submit_plan(client, plan, ledger, account, checked["exchange_code"],
                                          notification_context=context, order_route="direct",
                                          should_continue=may_dispatch)
                context.update(results=results, completed=True)
                outcome = {"state": "DIRECT_ACCEPTED", "orders": [_public_result(row) for row in results],
                           "preflight": checked}
        except Exception as exc:
            failure = exc
            code, message = _failure(exc)
            if outcome is None:
                state = context.get("failed_state")
                if context.get("completed"):
                    state = "DIRECT_ACCEPTED"
                    code = "POST_ACCEPTANCE_ERROR"
                    message = "주문 접수는 확인됐지만 결과 표시를 완료하지 못했습니다. 로컬 주문 기록을 확인해 주세요. 주문을 다시 보내지 않습니다."
                elif isinstance(exc, AmbiguousSubmission):
                    state = "UNKNOWN"
                elif code == "USER_STOPPED":
                    state = "STOPPED"
                elif financial_started and state is None:
                    state = "UNKNOWN"
                    context["failed_state"] = state
                    code = "UNKNOWN_SUBMISSION"
                    message = "주문 전송 중 오류가 발생해 접수 여부를 확정할 수 없습니다. 메리츠 주문 내역과 로컬 기록을 확인하기 전에는 다시 전송하지 않습니다."
                elif state not in {"UNKNOWN", "REJECTED", "STOPPED"}:
                    state = "BLOCKED"
                if deadline_reached:
                    state = "BLOCKED"
                    code = "OUTSIDE_ORDER_WINDOW"
                    message = "주문 전송 중 미국 정규장 시작 전 주문 시간이 끝나 남은 주문을 중단했습니다. 접수된 주문과 미전송 주문을 확인해 주세요."
                results = context.get("results", results)
                outcome = {"state": state, "orders": [_public_result(row) for row in results],
                           "preflight": checked, "error_code": code, "error": message}
                if context.get("failed_order"):
                    outcome["failed_order"] = _safe_order(context["failed_order"])
                if context.get("not_sent_count") is not None:
                    outcome["not_sent_count"] = context["not_sent_count"]
        finally:
            if ledger is None and failure is not None and context.get("plan"):
                try:
                    # A locally blocked batch still needs a Telegram notice.
                    # Initialize only the notification config/outbox here;
                    # never instantiate or query a brokerage client.
                    self._load_credentials()
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        notifier = configured_notifier()
                    ledger = OrderLedger(self.ledger_path)
                except Exception:
                    if outcome is not None:
                        outcome["notification_status"] = "UNAVAILABLE"
            if ledger is not None:
                try:
                    # There is no Telegram send on preview, import, constructor,
                    # local status, or broker status polling.
                    if context.get("plan") and (context.get("completed") or failure is not None):
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            notify_order_result(ledger, notifier, context,
                                                error=None if context.get("completed") else failure)
                    if outcome is not None:
                        count = ledger.db.execute(
                            "SELECT COUNT(*) FROM notifications WHERE state!='SENT'"
                        ).fetchone()[0]
                        outcome["notification_pending"] = count
                except Exception:
                    # Ancillary outbox/status errors cannot undo a known broker
                    # acceptance or relabel an already returned partial batch.
                    if outcome is not None:
                        outcome["notification_status"] = "UNKNOWN"
                finally:
                    try:
                        ledger.db.close()
                    except sqlite3.Error:
                        pass
        return outcome

    def local_orders(self, date: str | None = None) -> list[dict]:
        """Read public receipts without loading credentials or opening a client."""
        if date is not None and _day(date) is None:
            raise EngineBlocked("주문일은 YYYY-MM-DD 형식이어야 합니다.", code="INVALID_DATE")
        if not self.ledger_path.exists():
            return []
        try:
            # mode=ro prevents a status request from creating a database.
            db = sqlite3.connect(self.ledger_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            try:
                parameters = () if date is None else (_day(date),)
                condition = "" if date is None else " WHERE order_date=?"
                rows = db.execute(
                    "SELECT order_date,payload,state,response,updated_at FROM orders"
                    + condition + " ORDER BY updated_at", parameters,
                ).fetchall()
            finally:
                db.close()
            return [_public_result({"order_date": day, "order": json.loads(payload), "state": state,
                                    "response": json.loads(response) if response else None, "updated_at": updated})
                    for day, payload, state, response, updated in rows]
        except Exception as exc:
            code, message = _failure(exc)
            raise EngineBlocked(message, code=code) from None

    def poll_status(self, now: datetime | None = None) -> dict:
        """Read ordinary SOXL order/fill status; no submission or notifications."""
        try:
            current = _now(now)
            self._load_credentials()
            account_id()  # Ensure the configured account is the KMW account.
            client = MeritzClient.from_env()
            orders = []
            for row in client.orders_today():
                if not isinstance(row, dict) or symbol(row) != "SOXL":
                    continue
                quantity = _quantity(row.get("odqt"))
                filled = _quantity(row.get("cntg_qty"))
                cancelled = _quantity(row.get("cncl_cnfr_qty", 0))
                price = _nonnegative_number(row.get("ovrs_stck_oder_prc"))
                broker_state = str(row.get("ovrs_stck_oder_stat_cls_code", ""))
                if quantity is None or filled is None or cancelled is None or filled + cancelled > quantity:
                    state = "UNKNOWN"
                elif broker_state != "0":
                    state = "REJECTED" if broker_state in {"1", "2", "4"} else "UNKNOWN"
                elif quantity and filled == quantity:
                    state = "FILLED"
                elif quantity and cancelled and filled + cancelled == quantity:
                    state = "CANCELLED"
                elif filled:
                    state = "PARTIALLY_FILLED"
                else:
                    state = "OPEN"
                orders.append({
                    "order_date": _day(row.get("oder_date")), "order_no": _number(row.get("oder_no")),
                    "ticker": "SOXL", "side": {"1": "SELL", "2": "BUY"}.get(str(row.get("seln_byov_clcd", "")), "UNKNOWN"),
                    "quantity": quantity, "price": format(price, "f") if price is not None else None,
                    "order_type": {"5": "LOC", "7": "MOC"}.get(str(row.get("aspr_type_code", "")), "UNKNOWN"),
                    "filled_quantity": filled, "cancelled_quantity": cancelled, "state": state,
                })
            return {"state": "OK", "checked_at": current.isoformat(), "orders": orders}
        except Exception as exc:
            code, message = _failure(exc)
            return {"state": "ERROR", "orders": [], "error_code": code, "error": message}
