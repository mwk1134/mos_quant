#!/usr/bin/env python3
"""KMW-only Meritz order runner. Default execution only prints a plan."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, time as clock_time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

import requests

from kmw_order_plan import build_kmw_plan
from meritz_api import AmbiguousSubmission, MeritzClient, MeritzError
from telegram_notify import TelegramNotificationError, TelegramNotifier, format_order_notification
from us_market_calendar import is_us_equity_trading_day

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent
SNAPSHOT_REPO = "mwk1134/mos_quant"
SNAPSHOT_PATH = "data/positions_snapshots.json"


class OrderBlocked(ValueError):
    pass


def decimal(value, name="number"):
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise OrderBlocked(f"Invalid {name}") from None
    if not result.is_finite() or result < 0:
        raise OrderBlocked(f"Invalid {name}")
    return result


def load_env(path: Path):
    """Read literal KEY=value entries; never execute shell or expand substitutions."""
    allowed = {
        "MERITZ_APP_KEY", "MERITZ_APP_SECRET", "MERITZ_MAC_ADDRESS",
        "MERITZ_ACCOUNT_PRESET", "MERITZ_SOXL_EXCHANGE_CODE", "MERITZ_SOXL_PRICE_TICK", "GITHUB_TOKEN",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        key = key.strip()
        if not separator or key not in allowed:
            raise OrderBlocked("Unsupported entry in environment file")
        values = shlex.split(raw, comments=True)
        if len(values) > 1:
            raise OrderBlocked(f"Quote the value for {key}")
        os.environ[key] = values[0] if values else ""


def load_snapshots(path: Path | None = None):
    if path is not None:
        data = json.loads(path.read_text(encoding="utf-8"))
        source = str(path.resolve())
    else:
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            url = f"https://api.github.com/repos/{SNAPSHOT_REPO}/contents/{SNAPSHOT_PATH}"
            response = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json"},
                                    params={"ref": "main"}, timeout=20)
            response.raise_for_status()
            data = json.loads(base64.b64decode(response.json()["content"]))
        else:
            url = f"https://raw.githubusercontent.com/{SNAPSHOT_REPO}/main/{SNAPSHOT_PATH}"
            response = requests.get(url, params={"t": int(time.time())}, timeout=20)
            response.raise_for_status()
            data = response.json()
        source = f"github:{SNAPSHOT_REPO}/main/{SNAPSHOT_PATH}"
    if not isinstance(data, dict) or not isinstance(data.get("KMW"), dict):
        raise OrderBlocked("KMW snapshot is missing")
    return data, source


def reservation_window(now: datetime, order_date: str):
    local = now.astimezone(KST)
    market_day = now.astimezone(ET).date()
    if str(market_day) != order_date or local.date() != market_day:
        raise OrderBlocked("Order date does not match today's US trading session")
    opening = datetime.combine(market_day, clock_time(9, 30), tzinfo=ET).astimezone(KST)
    cutoff = opening - timedelta(hours=1)
    start = datetime.combine(local.date(), clock_time(9), tzinfo=KST)
    if not start <= local < cutoff:
        raise OrderBlocked("Reservation window is 09:00 KST to one hour before the US open")


def direct_order_window(now: datetime, order_date: str):
    """Use today's pre-open recommendation; broker decides direct acceptance hours."""
    market_now = now.astimezone(ET)
    if market_now.date().isoformat() != order_date or not is_us_equity_trading_day(market_now.date()):
        raise OrderBlocked("Order date does not match today's US trading session")
    # The planner currently supports pre-open recommendations only. Restrict
    # submissions to the same window, which also avoids early-close cutoffs.
    if market_now.time() >= clock_time(9, 30):
        raise OrderBlocked("Direct automated orders currently require a pre-open recommendation")


def validate_order_route(order_route):
    if order_route not in {"direct", "reserved"}:
        raise OrderBlocked("Order route must be direct or reserved")
    return order_route


def symbol(row):
    return str(row.get("ovrs_stck_symb_iscd") or row.get("iscd") or "").strip().split(".")[0].upper()


def cap_quantities(plan, cap):
    capped = deepcopy(plan)
    if cap is not None:
        if cap < 1:
            raise OrderBlocked("Quantity cap must be positive")
        for order in capped["orders"]:
            order["recommended_quantity"] = order["quantity"]
            order["quantity"] = min(order["quantity"], cap)
        capped["quantity_cap"] = cap
    return capped


def select_side(plan, side):
    selected = deepcopy(plan)
    if side is not None:
        selected["orders"] = [o for o in selected["orders"] if o["side"] == side]
        selected["selected_side"] = side
        selected["warnings"].append(f"Explicit test selection: only {side} orders are included")
    return selected


def active_reservation(row, order_day):
    """Use actual dates/quantity; inability to cancel is not proof of inactivity."""
    if row.get("rsev_cncl_yn") == "Y":
        return False
    end = str(row.get("oder_fin_date") or "").strip()
    if end:
        try:
            end_day = datetime.strptime(end, "%Y%m%d").date()
        except ValueError:
            raise OrderBlocked("SOXL reservation has an unrecognized end date") from None
        if end_day < datetime.strptime(order_day, "%Y%m%d").date():
            return False
    # Periodic reservations can accumulate fills while still placing future orders.
    # A cumulative fill quantity cannot establish that a reservation is inactive.
    return True


def validate_plan(plan, now, order_route="reserved"):
    if plan.get("preset") != "KMW" or plan.get("status") != "ready":
        raise OrderBlocked("Only a ready KMW plan can be submitted")
    validate_order_route(order_route)
    if order_route == "direct":
        direct_order_window(now, plan["order_date"])
    else:
        reservation_window(now, plan["order_date"])
    if plan.get("basis_date") != plan.get("expected_basis_date"):
        raise OrderBlocked("Plan does not use the most recent completed trading session")
    if plan.get("snapshot_date") != plan.get("expected_basis_date") or plan.get("snapshot_fresh") is False:
        raise OrderBlocked("The KMW snapshot is stale. Refresh and reconcile it before submitting")
    seen = set()
    for order in plan["orders"]:
        if order.get("ticker") != "SOXL" or order.get("side") not in ("BUY", "SELL"):
            raise OrderBlocked("Only KMW SOXL buy/sell orders are supported")
        quantity = order.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise OrderBlocked("Orders must have a positive whole-share quantity")
        price = decimal(order.get("price"), "order price")
        kind = order.get("order_type")
        if kind == "LOC":
            if price <= 0 or price != price.quantize(Decimal("0.01")):
                raise OrderBlocked("LOC prices must be positive and normalized to cents")
        elif kind != "MOC" or order["side"] != "SELL" or price != 0:
            raise OrderBlocked("MOC is supported only for sell orders at price zero")
        identity = (order["side"], order.get("position_key"), order.get("round"))
        if identity in seen:
            raise OrderBlocked("Duplicate position in plan")
        seen.add(identity)
    buys = [o for o in plan["orders"] if o["side"] == "BUY"]
    sells = [o for o in plan["orders"] if o["side"] == "SELL"]
    for buy in buys:
        for sell in sells:
            if sell["order_type"] == "MOC" or decimal(buy["price"]) >= decimal(sell["price"]):
                raise OrderBlocked("Opposing SOXL orders overlap; resolve the account's self-trade conflict first")


def preflight(client, plan, now, fee_buffer, order_route="reserved"):
    validate_plan(plan, now, order_route=order_route)
    holdings = [r for r in client.holdings() if symbol(r) == "SOXL"]
    for row in holdings:
        if row.get("rmnd_clcd") != "1" or row.get("loan_dtl_type_code") != "0000":
            raise OrderBlocked("Only ordinary whole-share SOXL holdings are supported")
    held = sum((decimal(r.get("rmnd_dqty"), "held quantity") for r in holdings), Decimal(0))
    expected = sum((decimal(p["quantity"]) for p in plan["snapshot_positions"]), Decimal(0))
    if held != expected:
        raise OrderBlocked(f"KMW snapshot holds {expected} SOXL shares; broker holds {held}. Reconcile first")
    sellable = sum((decimal(r.get("seln_able_dqty"), "sellable quantity") for r in holdings), Decimal(0))
    requested_sells = sum(o["quantity"] for o in plan["orders"] if o["side"] == "SELL")
    if decimal(requested_sells) > sellable:
        raise OrderBlocked("Broker sellable quantity is below the planned sell quantity")
    codes = {str(r.get("ovrs_exch_vndr_idno", "")).strip() for r in holdings}
    configured = os.environ.get("MERITZ_SOXL_EXCHANGE_CODE", "").strip()
    if configured:
        codes.add(configured)
    if len(codes) != 1 or "" in codes:
        raise OrderBlocked("Set MERITZ_SOXL_EXCHANGE_CODE to the verified SOXL exchange code")
    code = codes.pop()
    exchange_rows = client.exchanges()
    matches = [r for r in exchange_rows if str(r.get("ovrs_exch_vndr_idno", "")).strip() == code
               and r.get("onln_yn") == "Y"]
    if len(matches) != 1 or code not in {"0066", "0321", "0537"}:
        raise OrderBlocked("SOXL exchange code is not an enabled US exchange")
    quote = client.quote("SOXL", matches[0]["ovrs_stck_exch_idno"], delayed=True)
    if symbol(quote) != "SOXL":
        raise OrderBlocked("Broker quote did not identify SOXL")
    configured_tick = os.environ.get("MERITZ_SOXL_PRICE_TICK", "").strip()
    for order in plan["orders"]:
        prefix = "shnu" if order["side"] == "BUY" else "seln"
        tick = decimal(quote.get(f"{prefix}_aspr_unit"), "broker price tick")
        unit = decimal(quote.get(f"{prefix}_oder_qty_unit"), "broker quantity unit")
        if not tick and configured_tick:
            tick = decimal(configured_tick, "verified SOXL price tick")
        elif tick and configured_tick and tick != decimal(configured_tick):
            raise OrderBlocked("Configured SOXL price tick differs from the broker quote")
        if not tick or not unit:
            raise OrderBlocked("Broker omitted an order unit; set MERITZ_SOXL_PRICE_TICK only after verifying it")
        if decimal(order["quantity"]) % unit or (order["order_type"] == "LOC" and decimal(order["price"]) % tick):
            raise OrderBlocked("Planned price or quantity does not match broker order units")
    day = plan["order_date"].replace("-", "")
    today_orders = client.orders_today()
    for row in today_orders:
        if symbol(row) != "SOXL" or str(row.get("ovrs_stck_oder_stat_cls_code")) != "0":
            continue
        remaining = decimal(row.get("odqt")) - decimal(row.get("cntg_qty"))
        remaining -= decimal(row.get("cncl_cnfr_qty", 0)) + decimal(row.get("rctf_cnfr_qty", 0))
        if remaining > 0 and str(row.get("oder_rctf_cncl_cls_code")) != "2":
            raise OrderBlocked("An existing SOXL order is still open; inspect it before submitting")
    start = (now.astimezone(KST).date() - timedelta(days=30)).strftime("%Y%m%d")
    for row in client.reserved_orders(start, day, active_only=True):
        if symbol(row) == "SOXL" and active_reservation(row, day):
            raise OrderBlocked("An active SOXL reservation already exists; inspect it before submitting")
    buys = [o for o in plan["orders"] if o["side"] == "BUY"]
    needed = sum((decimal(o["price"]) * o["quantity"] for o in buys), Decimal(0))
    needed *= Decimal(1) + fee_buffer
    available = Decimal(0)
    if buys:
        usd = [r for r in client.order_estimate() if str(r.get("crcd", "")).strip() == "USD"]
        if len(usd) != 1:
            raise OrderBlocked("Broker did not return one USD buying-power row")
        if decimal(usd[0].get("othr_crnc_evdn_camt", 0)) != 0:
            raise OrderBlocked("Buying power includes other currencies; confirm the account's margin settings")
        available = decimal(usd[0].get("oder_crnc_oder_able_camt"), "USD buying power")
        if needed > available:
            raise OrderBlocked(f"USD buying power {available} is below the required amount {needed:.2f}")
    return {"exchange_code": code, "held_quantity": str(held), "sellable_quantity": str(sellable),
            "buying_power_usd": str(available), "required_usd": str(needed.quantize(Decimal("0.01")))}


class OrderLedger:
    """Reserve every batch identity transactionally before any order is sent."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute("""CREATE TABLE IF NOT EXISTS orders (
            identity TEXT PRIMARY KEY, account TEXT NOT NULL, order_date TEXT NOT NULL,
            plan_hash TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
            response TEXT, updated_at TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS notifications (
            event_key TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT NOT NULL,
            message_id INTEGER, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        self.db.commit()

    @staticmethod
    def identity(account, plan, order):
        # Price/quantity are intentionally excluded: changing a recommendation cannot resend a position.
        values = [account, "KMW", plan["order_date"], order["ticker"], order["side"],
                  order.get("round"), order.get("position_key")]
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def claim(self, account, plan):
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
        identities = [self.identity(account, plan, order) for order in plan["orders"]]
        try:
            self.db.execute("BEGIN IMMEDIATE")
            for identity, order in zip(identities, plan["orders"]):
                if self.db.execute("SELECT 1 FROM orders WHERE identity=?", (identity,)).fetchone():
                    raise OrderBlocked("This account/date/position was already attempted. Use --status; do not resend")
                self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                                (identity, account, plan["order_date"], digest,
                                 json.dumps(order, sort_keys=True), "PLANNED", None, datetime.now(KST).isoformat()))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return identities

    def update(self, identity, state, response=None):
        self.db.execute("UPDATE orders SET state=?, response=?, updated_at=? WHERE identity=?",
                        (state, json.dumps(response) if response is not None else None,
                         datetime.now(KST).isoformat(), identity))
        self.db.commit()

    def unresolved(self, account):
        rows = self.db.execute("SELECT order_date,state FROM orders WHERE account=? AND state IN ('SENDING','UNKNOWN')",
                               (account,)).fetchall()
        if rows:
            raise OrderBlocked("A previous submission has an unknown result. Inspect --status and the broker first")

    def rows(self, account):
        rows = self.db.execute("SELECT order_date,payload,state,response,updated_at FROM orders WHERE account=? ORDER BY updated_at",
                               (account,)).fetchall()
        return [{"order_date": d, "order": json.loads(p), "state": s,
                 "response": json.loads(r) if r else None, "updated_at": u} for d, p, s, r, u in rows]

    def queue_notification(self, event_key, text):
        stamp = datetime.now(KST).isoformat()
        cursor = self.db.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?,?)",
                                 (event_key, text, "PENDING", None, None, stamp, stamp))
        self.db.commit()
        return cursor.rowcount == 1

    def claim_notification(self, event_key, retry=False):
        stamp = datetime.now(KST).isoformat()
        expired = (datetime.now(KST) - timedelta(minutes=5)).isoformat()
        condition = "state='PENDING'"
        parameters = [stamp, event_key]
        if retry:
            condition = "(state IN ('PENDING','FAILED','UNKNOWN') OR (state='SENDING' AND updated_at<?))"
            parameters.append(expired)
        cursor = self.db.execute(
            f"UPDATE notifications SET state='SENDING',updated_at=? WHERE event_key=? AND {condition}", parameters)
        self.db.commit()
        return cursor.rowcount == 1

    def update_notification(self, event_key, state, message_id=None, error=None):
        self.db.execute("UPDATE notifications SET state=?,message_id=?,last_error=?,updated_at=? WHERE event_key=?",
                        (state, message_id, error, datetime.now(KST).isoformat(), event_key))
        self.db.commit()

    def pending_notifications(self):
        expired = (datetime.now(KST) - timedelta(minutes=5)).isoformat()
        rows = self.db.execute("""SELECT event_key,text FROM notifications
            WHERE state IN ('PENDING','FAILED','UNKNOWN') OR (state='SENDING' AND updated_at<?)
            ORDER BY created_at LIMIT 20""", (expired,)).fetchall()
        return [{"event_key": key, "text": text} for key, text in rows]


def submit_plan(client, plan, ledger, account, exchange_code, notification_context=None, order_route="reserved"):
    validate_order_route(order_route)
    if notification_context is not None:
        notification_context["order_route"] = order_route
    ledger.unresolved(account)
    identities = ledger.claim(account, plan)
    results = []
    for index, (identity, order) in enumerate(zip(identities, plan["orders"])):
        ledger.update(identity, "SENDING")
        try:
            parameters = dict(side=order["side"].lower(), symbol=order["ticker"],
                exchange_vendor_code=exchange_code, quantity=order["quantity"],
                price=order["price"], order_type=order["order_type"])
            if order_route == "direct":
                body = client.submit_order(**parameters)
                accepted_state = "DIRECT_ACCEPTED"
            else:
                body = client.submit_reserved_order(**parameters, reservation_class="1", order_date=None)
                accepted_state = "RESERVED"
            ledger.update(identity, accepted_state, body)
            results.append({"order": order, "state": accepted_state, "response": body})
        except Exception as exc:
            state = "UNKNOWN" if isinstance(exc, AmbiguousSubmission) else "REJECTED" if isinstance(exc, MeritzError) else "UNKNOWN"
            body = getattr(exc, "response", None)
            ledger.update(identity, state, body if isinstance(body, dict) else None)
            for pending in identities[index + 1:]:
                ledger.update(pending, "NOT_SENT")
            if notification_context is not None:
                notification_context.update(results=results, failed_order=order,
                                            failed_state=state, not_sent_count=len(identities) - index - 1)
            print(json.dumps({"accepted_before_error": results, "stopped_order": order,
                              "state": state, "broker_response": body if isinstance(body, dict) else None},
                             ensure_ascii=False, indent=2))
            raise
    return results


def print_plan(plan):
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def account_id():
    if os.environ.get("MERITZ_ACCOUNT_PRESET", "") != "KMW":
        raise OrderBlocked("Set MERITZ_ACCOUNT_PRESET=KMW for the App Key's intended account")
    key = os.environ.get("MERITZ_APP_KEY", "")
    if not key:
        raise OrderBlocked("MERITZ_APP_KEY is missing")
    return hashlib.sha256(key.encode()).hexdigest()


def configured_notifier(required=False):
    try:
        notifier = TelegramNotifier.from_env()
        if notifier is None and required:
            raise TelegramNotificationError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment file")
        return notifier
    except TelegramNotificationError:
        if required:
            raise
        print("[Telegram] Configuration invalid; order-result notifications will remain pending", file=sys.stderr)
        return None


def deliver_notification(ledger, event_key, text, notifier, retry=False):
    """Notification errors never propagate into the order submission path."""
    try:
        if notifier is None:
            print("[Telegram] Not configured; notification saved for --retry-notifications", file=sys.stderr)
            return False
        if not ledger.claim_notification(event_key, retry=retry):
            return False
        try:
            message_id = notifier.send(text)
        except TelegramNotificationError as exc:
            state = "UNKNOWN" if exc.delivery_unknown else "FAILED"
            # Never store or print transport exception strings containing a bot URL/token.
            ledger.update_notification(event_key, state, error="Telegram delivery was not confirmed")
            print(f"[Telegram] Delivery {state}; retry notifications separately", file=sys.stderr)
            return False
        except Exception:
            ledger.update_notification(event_key, "UNKNOWN", error="Telegram delivery was not confirmed")
            print("[Telegram] Delivery UNKNOWN; retry notifications separately", file=sys.stderr)
            return False
        ledger.update_notification(event_key, "SENT", message_id=message_id)
        print("[Telegram] Order-result notification sent", file=sys.stderr)
        return True
    except Exception:
        print("[Telegram] Could not save delivery status; broker order states are unchanged", file=sys.stderr)
        return False


def notify_order_result(ledger, notifier, context, error=None):
    try:
        plan = context.get("plan") or {"preset": "KMW", "orders": [],
                                      "order_date": datetime.now(ET).date().isoformat(),
                                      "generated_at": datetime.now(KST).isoformat()}
        results = context.get("results", [])
        text = format_order_notification(plan, results, error=error,
                                         failed_order=context.get("failed_order"),
                                         failed_state=context.get("failed_state"),
                                         not_sent_count=context.get("not_sent_count", 0),
                                         order_route=context.get("order_route"))
        account = hashlib.sha256(os.environ.get("MERITZ_APP_KEY", "unconfigured").encode()).hexdigest()
        identities = sorted(OrderLedger.identity(account, plan, o) for o in plan["orders"])
        direct = context.get("order_route") == "direct" or any(r.get("state") == "DIRECT_ACCEPTED" for r in results)
        outcome = ("DIRECT_ACCEPTED" if direct else "RESERVED") if error is None else context.get("failed_state", "BLOCKED")
        event = [account, plan["order_date"], identities, outcome]
        event_key = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        if ledger.queue_notification(event_key, text):
            deliver_notification(ledger, event_key, text, notifier)
    except Exception:
        print("[Telegram] Could not queue notification; broker order states are unchanged", file=sys.stderr)


def _run_once(args, notification_context=None):
    now = datetime.now(KST)
    if args.status:
        account = account_id()
        client = MeritzClient.from_env()
        ledger = OrderLedger(args.ledger)
        try:
            start = (now.date() - timedelta(days=30)).strftime("%Y%m%d")
            end = now.strftime("%Y%m%d")
            print_plan({"preset": "KMW", "ledger": ledger.rows(account),
                        "reservations": [r for r in client.reserved_orders(start, end) if symbol(r) == "SOXL"],
                        "orders": [r for r in client.orders_history(start, end) if symbol(r) == "SOXL"],
                        "holdings": [r for r in client.holdings() if symbol(r) == "SOXL"]})
        finally:
            ledger.db.close()
        return
    snapshots, source = load_snapshots(args.snapshot_file)
    with redirect_stdout(io.StringIO()):
        generated = build_kmw_plan(snapshots, now=now, allow_stale_snapshot=args.allow_stale_preview)
    plan = cap_quantities(select_side(generated, args.side), args.quantity_cap)
    order_route = validate_order_route(getattr(args, "order_route", "direct"))
    plan["order_route"] = order_route
    plan["snapshot_source"] = source
    if notification_context is not None:
        notification_context["plan"] = plan
        notification_context["order_route"] = order_route
    print_plan(plan)
    if plan.get("status") == "skipped":
        return
    plan_dir = ROOT / ".autotrade" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / f"{plan['order_date']}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.submit and not args.check_account:
        return
    account = account_id()
    client = MeritzClient.from_env()
    ledger = OrderLedger(args.ledger)
    try:
        ledger.unresolved(account)
        checked = preflight(client, plan, datetime.now(KST), decimal(args.fee_buffer), order_route=order_route)
        print_plan({"preflight": checked})
        if args.submit and plan["orders"]:
            result = submit_plan(client, plan, ledger, account, checked["exchange_code"],
                                 notification_context=notification_context, order_route=order_route)
            if notification_context is not None:
                notification_context.update(results=result, completed=True)
            state = "DIRECT_ACCEPTED" if order_route == "direct" else "RESERVED"
            print_plan({"preset": "KMW", "state": state, "orders": result,
                        "note": "Order accepted; fills must be checked separately with --status"})
    finally:
        ledger.db.close()


def run_once(args):
    if args.test_notification:
        notifier = configured_notifier(required=True)
        notifier.send("KMW 텔레그램 알림 연결 테스트\n이 메시지는 주문 접수나 체결을 의미하지 않습니다.")
        print("Telegram test notification sent")
        return
    if args.retry_notifications:
        notifier = configured_notifier(required=True)
        ledger = OrderLedger(args.ledger)
        try:
            pending = ledger.pending_notifications()
            sent = sum(deliver_notification(ledger, row["event_key"], row["text"], notifier, retry=True)
                       for row in pending)
            print_plan({"telegram_notifications": {"pending": len(pending), "sent": sent}})
            if sent != len(pending):
                raise TelegramNotificationError("Some notification deliveries were not confirmed; broker orders were not retried")
        finally:
            ledger.db.close()
        return
    if not args.submit:
        return _run_once(args)
    notifier = configured_notifier()
    context = {}
    notification_ledger = OrderLedger(args.ledger)
    try:
        _run_once(args, notification_context=context)
        if context.get("completed"):
            notify_order_result(notification_ledger, notifier, context)
    except Exception as exc:
        # A later stdout/file error cannot undo a confirmed broker order.
        notify_order_result(notification_ledger, notifier, context,
                            error=None if context.get("completed") else exc)
        raise
    finally:
        notification_ledger.db.close()


def next_run(now, at):
    local = now.astimezone(KST)
    target = datetime.combine(local.date(), at, tzinfo=KST)
    if target <= local:
        target += timedelta(days=1)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="KMW SOXL LOC/MOC Meritz order runner (default: preview, direct route)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--submit", action="store_true", help="Actually submit this account's planned orders")
    mode.add_argument("--check-account", action="store_true", help="Read broker account and validate without placing orders")
    mode.add_argument("--status", action="store_true", help="Read reservation/fill status; does not change webapp snapshots")
    mode.add_argument("--test-notification", action="store_true", help="Send a Telegram test message without any broker calls")
    mode.add_argument("--retry-notifications", action="store_true", help="Retry saved Telegram messages without submitting orders")
    parser.add_argument("--env-file", type=Path, help="Local literal KEY=value credential file")
    parser.add_argument("--snapshot-file", type=Path, help="Use this snapshot instead of fetching GitHub main")
    parser.add_argument("--quantity-cap", type=int, help="Cap EACH order's quantity for a small live test")
    parser.add_argument("--side", choices=["BUY", "SELL"], help="Explicitly test only buy or only sell orders")
    parser.add_argument("--order-route", choices=["direct", "reserved"], default="direct",
                        help="Direct LOC/MOC orders by default; reserved must be selected explicitly")
    parser.add_argument("--allow-stale-preview", action="store_true", help="Inspect a stale snapshot's plan; submission is disabled")
    parser.add_argument("--fee-buffer", default="0.005", help="Buy cash reserve fraction; default 0.5%%")
    parser.add_argument("--ledger", type=Path, default=ROOT / ".autotrade" / "orders.sqlite3")
    parser.add_argument("--schedule", action="store_true", help="Keep running daily at --at in Asia/Seoul")
    parser.add_argument("--at", default="17:01", help="Daily KST run time (default 17:01)")
    args = parser.parse_args(argv)
    try:
        if args.allow_stale_preview and (args.submit or args.check_account or args.status):
            raise OrderBlocked("--allow-stale-preview cannot be used with broker account commands")
        if args.env_file:
            load_env(args.env_file)
        if args.schedule:
            if args.status or args.test_notification or args.retry_notifications:
                raise OrderBlocked("Status and Telegram-only commands are one-time commands")
            at = datetime.strptime(args.at, "%H:%M").time()
            while True:
                target = next_run(datetime.now(KST), at)
                print(f"Next KMW run: {target.isoformat()}", flush=True)
                while datetime.now(KST) < target:
                    time.sleep(min(30, max(0.1, (target - datetime.now(KST)).total_seconds())))
                try:
                    run_once(args)
                except Exception as exc:
                    # No automatic same-day retry after an uncertain order result.
                    print(f"KMW run stopped: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        else:
            run_once(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"KMW run stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
