"""Telegram notifications for KMW order reservation results.

Only a validated ``sendMessage`` response confirms notification delivery.  A
network error may happen after Telegram accepted the message, so this module
never retries automatically.  Broker reservation acceptance is not a fill.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import os
import re
from zoneinfo import ZoneInfo

import requests


KST = ZoneInfo("Asia/Seoul")
MAX_TEXT_LENGTH = 4096
_TOKEN = re.compile(r"[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,200}\Z")
_CHAT_ID = re.compile(r"-?[1-9][0-9]{0,15}\Z")


class TelegramNotificationError(RuntimeError):
    """A safe error to print; never contains credentials or a response body."""

    def __init__(self, message: str, *, delivery_unknown: bool = False):
        super().__init__(message)
        self.delivery_unknown = delivery_unknown


class TelegramNotifier:
    """Send one plain-text message to a numeric private/group chat ID."""

    def __init__(self, token: str, chat_id: str | int, *, session=None):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise TelegramNotificationError("Invalid TELEGRAM_BOT_TOKEN format")
        if isinstance(chat_id, bool) or not isinstance(chat_id, (str, int)):
            raise TelegramNotificationError("Invalid TELEGRAM_CHAT_ID format")
        identifier = str(chat_id)
        if not _CHAT_ID.fullmatch(identifier) or abs(int(identifier)) >= 2 ** 52:
            raise TelegramNotificationError("Invalid TELEGRAM_CHAT_ID format")
        self._token = token
        self._chat_id = int(identifier)
        self._session = session if session is not None else requests.Session()

    @classmethod
    def from_env(cls, env=None, *, session=None):
        """Return None when unconfigured; reject partial/invalid configuration."""
        env = os.environ if env is None else env
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = env.get("TELEGRAM_CHAT_ID", "")
        if not token and not chat_id:
            return None
        if not token or not chat_id:
            raise TelegramNotificationError(
                "Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
        return cls(token, chat_id, session=session)

    def send(self, text: str) -> int:
        """Send once, returning a validated Telegram message ID on success."""
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT_LENGTH:
            raise TelegramNotificationError("Telegram text must contain 1-4096 characters")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise TelegramNotificationError("Telegram text must be valid UTF-8") from None
        try:
            response = self._session.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise TelegramNotificationError(
                "Telegram delivery could not be confirmed; no automatic retry",
                delivery_unknown=True,
            ) from None
        status = response.status_code
        if not isinstance(status, int) or isinstance(status, bool):
            raise TelegramNotificationError(
                "Telegram returned an invalid HTTP status; delivery unknown",
                delivery_unknown=True,
            )
        if not 200 <= status < 300:
            raise TelegramNotificationError(
                f"Telegram request failed (HTTP {status}); no automatic retry",
                delivery_unknown=status >= 500 or status == 408 or 300 <= status < 400,
            )
        try:
            body = response.json()
        except (ValueError, requests.RequestException):
            raise TelegramNotificationError(
                "Telegram returned an unreadable response; delivery unknown",
                delivery_unknown=True,
            ) from None
        if isinstance(body, dict) and body.get("ok") is False:
            raise TelegramNotificationError("Telegram rejected the message")
        result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise TelegramNotificationError(
                "Telegram returned no valid message ID; delivery unknown",
                delivery_unknown=True,
            )
        return message_id


def _integer(value, *, maximum=10 ** 12):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= maximum:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,15}", value):
        if int(value) <= maximum:
            return str(int(value))
    return None


def _price(value):
    try:
        if isinstance(value, bool):
            return "확인 필요"
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount < Decimal("10000000"):
            return "확인 필요"
        return f"${amount:.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return "확인 필요"


def _order_description(order):
    order = order if isinstance(order, Mapping) else {}
    side = {"BUY": "매수", "SELL": "매도"}.get(order.get("side"), "주문")
    quantity = _integer(order.get("quantity")) or "?"
    kind = order.get("order_type")
    terms = "MOC 종가 시장가" if kind == "MOC" else f"LOC {_price(order.get('price'))}" if kind == "LOC" else "주문유형 확인 필요"
    line = f"{side} {quantity}주 {terms}"
    round_number = _integer(order.get("round"), maximum=1000000)
    if round_number:
        line += f" · {round_number}회차"
    return line


def _reservation_number(response):
    data = response.get("data") if isinstance(response, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    return _integer(data.get("oder_rsev_mst_no"), maximum=10 ** 16 - 1)


def _order_day(plan):
    raw = plan.get("order_date")
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return "확인 필요"
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return "확인 필요"


def _generated_time(plan):
    raw = plan.get("generated_at")
    try:
        value = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        if not isinstance(value, datetime) or value.tzinfo is None:
            return "확인 필요"
        return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError):
        return "확인 필요"


def format_order_notification(
    plan, results, error=None, failed_order=None, failed_state=None, not_sent_count=0
) -> str:
    """Format only allowlisted order data; omit raw errors/account credentials."""
    plan = plan if isinstance(plan, Mapping) else {}
    results = [row for row in results if isinstance(row, Mapping)]
    accepted = sum(row.get("state") == "RESERVED" for row in results)
    unknown = failed_state in {"UNKNOWN", "SENDING"} or any(
        row.get("state") in {"UNKNOWN", "SENDING"} for row in results
    )
    failed = error is not None or failed_order is not None or failed_state is not None or any(
        row.get("state") != "RESERVED" for row in results
    )
    if unknown:
        state = "예약주문 접수 여부 불확실 · 전송 중단"
    elif failed and accepted:
        state = "일부 예약 접수 후 전송 중단"
    elif failed:
        state = "예약주문 실패 · 전송 중단"
    elif accepted:
        state = "예약주문 접수 완료"
    else:
        state = "주문 없음"
    lines = ["KMW 자동매매 · SOXL", state,
             f"미국 주문일: {_order_day(plan)}",
             f"계산 시각(KST): {_generated_time(plan)}"]
    for row in results:
        label = {"RESERVED": "접수", "UNKNOWN": "불확실", "SENDING": "불확실",
                 "REJECTED": "실패", "NOT_SENT": "미전송"}.get(row.get("state"), "확인 필요")
        line = f"{label}: {_order_description(row.get('order'))}"
        reservation = _reservation_number(row.get("response"))
        if reservation and row.get("state") == "RESERVED":
            line += f" · 예약번호 {reservation}"
        if len("\n".join(lines)) + len(line) > MAX_TEXT_LENGTH - 250:
            lines.append("추가 주문 내역은 로컬 주문 원장을 확인하세요.")
            break
        lines.append(line)
    if failed_order is not None:
        label = "불확실" if unknown else "실패"
        lines.append(f"{label}: {_order_description(failed_order)}")
    count = _integer(not_sent_count)
    if count:
        lines.append(f"남은 {count}건은 전송하지 않았습니다.")
    if unknown:
        lines.append("브로커에서 접수 여부를 확인하세요. 주문을 다시 보내지 마세요.")
    elif error is not None and failed_order is None and not accepted:
        lines.append("주문 전 확인 과정이 중단되었습니다. 로컬 실행 로그를 확인하세요.")
    lines.append("예약 접수 알림이며 체결 완료 알림이 아닙니다. 실제 체결은 별도 확인하세요.")
    return "\n".join(lines)
