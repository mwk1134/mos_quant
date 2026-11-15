"""Explicit read projections for two strategies sharing one KMW2 account.

Account facts remain on the original client. The coordinator verifies the sum
of both strategy inventories; each reconciler sees only its documented orders
and inventory allocation. All submission methods delegate without rewriting.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import re
import sqlite3

from atr70_r2_runtime import ET, MixedBlocked, _money, _quantity
from kmw_autotrade import active_reservation, symbol
from kmw_order_plan import completed_trading_date
from meritz_api import SubmissionStopped
from preset_snapshot_sync import (_apply_confirmed_cancellations, _completed_auction_order,
                                  _holding_quantity, _history_day, _order_id,
                                  normalize_confirmed_fills)


_LOT = re.compile(r"[1-9][0-9]*_\d{4}-\d{2}-\d{2}")


def _blocked(code, message):
    raise MixedBlocked(code, message)


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return value
    except (ValueError, TypeError):
        _blocked("INVALID_BROKER_FACT", "전략 주문의 미국 거래일을 확인하세요.")


def _key(day, number):
    return _day(day), _order_id(number)


def _merge(owners, key, *, side, maximum=None, confirmed=0, archived=False):
    if side not in {"BUY", "SELL"}:
        _blocked("OWNERSHIP_CONFLICT", "전략 주문의 매매 방향을 확인하세요.")
    maximum = _quantity(maximum) if maximum is not None else None
    confirmed = _quantity(confirmed)
    if maximum is not None and (not maximum or maximum < confirmed):
        _blocked("OWNERSHIP_CONFLICT", "전략 주문의 원수량과 확인된 체결수량이 다릅니다.")
    old = owners.get(key)
    if old:
        if old["side"] != side or (old["maximum"] is not None and maximum is not None
                                   and old["maximum"] != maximum):
            _blocked("OWNERSHIP_CONFLICT", "같은 주문번호가 서로 다른 전략 주문으로 저장되어 있습니다.")
        maximum = old["maximum"] if maximum is None else maximum
        confirmed = max(confirmed, old["confirmed"])
        archived = old["archived"] and archived
    if maximum is not None and confirmed > maximum:
        _blocked("OWNERSHIP_CONFLICT", "전략 주문의 확인된 체결수량이 원수량을 초과합니다.")
    owners[key] = {"side": side, "maximum": maximum, "confirmed": confirmed, "archived": archived}


def _mixed_owners(snapshot):
    owners, active = {}, set()
    history = snapshot.get("strategyHistory", [])
    if not isinstance(history, list) or any(not isinstance(entry, dict) for entry in history):
        _blocked("OWNERSHIP_CONFLICT", "전략 전환 기록을 확인하세요.")
    states = [(entry.get("mixed_state"), True) for entry in history]
    states.append((snapshot.get("atr70_r2"), False))
    for state, archived in states:
        if state is None:
            continue
        if not isinstance(state, dict) or not isinstance(state.get("orders"), dict):
            _blocked("OWNERSHIP_CONFLICT", "혼합 전략의 주문 장부를 확인하세요.")
        for order in state["orders"].values():
            if not isinstance(order, dict):
                _blocked("OWNERSHIP_CONFLICT", "혼합 전략의 주문 기록을 확인하세요.")
            if not archived and order.get("status") in {"SENDING", "UNKNOWN"}:
                _blocked("UNRESOLVED_ORDER", "이전 주문의 접수 여부를 확인한 뒤 진행하세요.")
            number = order.get("broker_order_id")
            if number is None:
                continue
            key = _key(order.get("session_date"), number)
            _merge(owners, key, side=order.get("side"), maximum=order.get("quantity"),
                   confirmed=order.get("cumulative_quantity", 0), archived=archived)
            if not archived:
                active.add(key)
    return owners, active


def mixed_owned_ids(snapshot):
    """Known mixed order identities, including completed archived periods."""
    if not isinstance(snapshot, dict):
        _blocked("OWNERSHIP_CONFLICT", "KMW2 전략 장부를 확인하세요.")
    return set(_mixed_owners(snapshot)[0])


def _ledger_rows(ledger_path, account):
    path = Path(ledger_path)
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                "SELECT order_date,payload,state,response FROM orders WHERE account=?", (account,)).fetchall()
        finally:
            connection.close()
        result = []
        for day, payload, status, response in rows:
            if status in {"SENDING", "UNKNOWN"}:
                _blocked("UNRESOLVED_ORDER", "계좌에 접수 여부가 확인되지 않은 주문이 있습니다.")
            order = json.loads(payload)
            receipt = json.loads(response) if response else None
            if not isinstance(order, dict):
                raise ValueError
            result.append((_day(day), order, status, receipt))
        return result
    except MixedBlocked:
        raise
    except Exception:
        _blocked("LEDGER_READ_FAILED", "KMW2 계좌의 주문 접수 장부를 확인할 수 없습니다.")


def _legacy_owners(document, ledger_path, account):
    snapshot = document["KMW2"]
    owners, reservations = {}, {}
    records = snapshot.get("broker_sync", {}).get("executions", {})
    if not isinstance(records, dict):
        _blocked("OWNERSHIP_CONFLICT", "동파 전략의 체결 확인 기록을 확인하세요.")
    for identity, row in records.items():
        if not isinstance(identity, str) or not isinstance(row, dict):
            _blocked("OWNERSHIP_CONFLICT", "동파 전략의 주문 식별자를 확인하세요.")
        day, number = identity.split(":", 1)
        key = _key(day, number)
        if row.get("trade_date", day) != day:
            _blocked("OWNERSHIP_CONFLICT", "동파 체결의 미국 주문일이 일치하지 않습니다.")
        _merge(owners, key, side=row.get("side"), maximum=row.get("recommended_quantity"),
               confirmed=row.get("quantity", 0))
    for day, order, status, receipt in _ledger_rows(ledger_path, account):
        if order.get("intent_id") or order.get("ticker") != "SOXL" or not isinstance(receipt, dict):
            continue
        data = receipt.get("data")
        if not isinstance(data, dict):
            continue
        if status == "DIRECT_ACCEPTED" and data.get("oder_no") not in (None, "", 0, "0"):
            _merge(owners, _key(day, data["oder_no"]), side=order.get("side"), maximum=order.get("quantity"))
        if status == "RESERVED" and data.get("oder_rsev_mst_no") not in (None, "", 0, "0"):
            reservation_day = data.get("oder_rsev_mst_date", day)
            if isinstance(reservation_day, str) and len(reservation_day) == 8:
                reservation_day = _history_day(reservation_day).isoformat()
            _merge(reservations, _key(reservation_day, data["oder_rsev_mst_no"]),
                   side=order.get("side"), maximum=order.get("quantity"))
    confirmations = snapshot.get("manual_buy_confirmations", [])
    if not isinstance(confirmations, list):
        _blocked("OWNERSHIP_CONFLICT", "수동 주문의 확인 기록을 확인하세요.")
    for row in confirmations:
        if not isinstance(row, dict):
            _blocked("OWNERSHIP_CONFLICT", "수동 주문의 확인 기록을 확인하세요.")
        # A day/price/quantity guess is not explicit ownership evidence.
        number = row.get("broker_order_id", row.get("order_id"))
        if number is not None:
            _merge(owners, _key(row.get("trade_date"), number), side=row.get("side", "BUY"),
                   maximum=row.get("quantity"))
    return owners, reservations


def legacy_owned_ids(document, ledger_path, account):
    """Known original-strategy order identities; never guess a manual match."""
    return set(_legacy_owners(document, ledger_path, account)[0])


def _inventory(snapshot):
    state = snapshot["atr70_r2"]
    mixed = sum(_quantity(lot["shares"]) for sleeve in ("atr", "r2") for lot in state[sleeve]["lots"])
    legacy = sum(_quantity(row["shares"]) for key, row in snapshot.items()
                 if isinstance(key, str) and _LOT.fullmatch(key) and isinstance(row, dict))
    return mixed, legacy


class _SubmissionGuard:
    def __init__(self, client, before_submission=None):
        self._client = client
        self._before_submission = before_submission

    def __getattr__(self, name):
        return getattr(self._client, name)

    def _submit(self, method, parameters):
        if self._before_submission is not None and self._before_submission(deepcopy(parameters)) is False:
            raise SubmissionStopped("Strategy submission stopped before transport")
        return getattr(self._client, method)(**parameters)

    def submit_order(self, **parameters):
        return self._submit("submit_order", parameters)

    def submit_mixed_order(self, **parameters):
        return self._submit("submit_mixed_order", parameters)

    def submit_reserved_order(self, **parameters):
        return self._submit("submit_reserved_order", parameters)


class _StrategyBrokerView(_SubmissionGuard):
    def __init__(self, client, document, *, strategy, ledger_path, account, now,
                 mixed_quantity=None, legacy_quantity=None, before_submission=None):
        super().__init__(client, before_submission)
        self._strategy = strategy
        self._snapshot = deepcopy(document["KMW2"])
        self._now = now
        self._basis = completed_trading_date(now.astimezone(ET))
        sync = self._snapshot.get("broker_sync", {})
        self._baseline = _day(sync.get("baseline_date", self._snapshot.get("as_of_date")))
        self._mixed, self._active_mixed = _mixed_owners(self._snapshot)
        self._legacy, self._legacy_reservations = _legacy_owners(document, ledger_path, account)
        if self._mixed.keys() & self._legacy.keys():
            _blocked("OWNERSHIP_CONFLICT", "동일한 주문이 혼합과 동파 양쪽 장부에 기록되어 있습니다.")
        self._owners = {**self._mixed, **self._legacy}
        if mixed_quantity is None and legacy_quantity is None:
            self._mixed_quantity, self._legacy_quantity = _inventory(self._snapshot)
        elif mixed_quantity is None or legacy_quantity is None:
            _blocked("HOLDINGS_MISMATCH", "조정 중인 두 전략의 보유수량을 함께 확인하세요.")
        else:
            self._mixed_quantity, self._legacy_quantity = _quantity(mixed_quantity), _quantity(legacy_quantity)
        state = self._snapshot["atr70_r2"]
        self._budget = (_money(state["atr"]["cash"]) + _money(state["r2"]["cash"])
                        if strategy == "MIXED" else _money(self._snapshot.get("available_cash")))
        config = document.get("_preset_configs", {}).get("KMW2", {})
        today = now.astimezone(ET).date()
        start = min(date.fromisoformat(self._baseline), today - timedelta(days=30),
                    date.fromisoformat(config.get("session_start_date", self._baseline)))
        self._reservation_range = (start.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
        self._open_sell_reserve = self._reservation_sell_reserve = 0
        self._order_rows(self._client.orders_today())
        self._reservation_rows(self._client.reserved_orders(*self._reservation_range, active_only=True))
        self._holdings()

    def _selected(self, key):
        return key in (self._active_mixed if self._strategy == "MIXED" else self._legacy)

    def _holdings(self):
        rows = self._client.holdings()
        held = _holding_quantity(rows)
        if held != self._mixed_quantity + self._legacy_quantity:
            _blocked("HOLDINGS_MISMATCH", "실제 계좌 수량과 혼합·동파 장부의 합계가 다릅니다.")
        if self._open_sell_reserve + self._reservation_sell_reserve > held:
            _blocked("SELLABLE", "전체 미체결·예약 매도수량이 실제 보유수량을 초과합니다.")
        return rows, held

    def holdings(self):
        rows, _ = self._holdings()
        quantity = self._mixed_quantity if self._strategy == "MIXED" else self._legacy_quantity
        stock_rows = [row for row in rows if symbol(row) == "SOXL"]
        result = [deepcopy(row) for row in rows if symbol(row) != "SOXL"]
        if quantity:
            if not stock_rows:
                _blocked("HOLDINGS_MISMATCH", "전략 보유분의 실제 계좌 잔고를 확인하세요.")
            codes = {row.get("ovrs_exch_vndr_idno") for row in stock_rows}
            if len(codes) > 1:
                _blocked("HOLDINGS_MISMATCH", "SOXL 보유분의 거래소 구분이 다릅니다.")
            row = deepcopy(stock_rows[0])
            row["rmnd_dqty"] = quantity
            row["seln_able_dqty"] = min(quantity, sum(_quantity(item.get("seln_able_dqty")) for item in stock_rows))
            result.append(row)
        return result

    def _order_rows(self, rows, *, capture=True):
        normalized = _apply_confirmed_cancellations(rows)
        result, reserved_sells = [], 0
        for row in normalized:
            if symbol(row) != "SOXL":
                continue
            key = (_history_day(row.get("oder_date")).isoformat(), _order_id(row.get("oder_no")))
            parent = row.get("orgl_oder_no")
            parent_key = (key[0], _order_id(parent)) if parent not in (None, "", 0, "0") else None
            if (key in self._owners and parent_key in self._owners
                    and (key in self._mixed) != (parent_key in self._mixed)):
                _blocked("OWNERSHIP_CONFLICT", "정정·취소 주문과 원주문의 전략 소유권이 다릅니다.")
            owner_key = key if key in self._owners else parent_key
            meta = self._owners.get(owner_key)
            requested = _quantity(row.get("odqt"))
            filled, cancelled = _quantity(row.get("cntg_qty", 0)), _quantity(row.get("cncl_cnfr_qty", 0))
            rectified = _quantity(row.get("rctf_cnfr_qty", 0))
            remaining = requested - filled - cancelled - rectified
            if remaining < 0:
                _blocked("ORDER_MISMATCH", "계좌 주문의 수량 합계가 일치하지 않습니다.")
            status = str(row.get("ovrs_stck_oder_stat_cls_code", ""))
            side = {"1": "SELL", "2": "BUY"}.get(str(row.get("seln_byov_clcd")))
            if side is None:
                _blocked("ORDER_MISMATCH", "계좌 주문의 매매 방향을 확인하세요.")
            if meta:
                if side != meta["side"] or (meta["maximum"] is not None and
                        (requested > meta["maximum"] or (owner_key == key and requested != meta["maximum"]))):
                    _blocked("ORDER_MISMATCH", "실제 주문의 방향·수량이 전략 장부와 다릅니다.")
                if status not in {"0", "1", "2", "4"}:
                    _blocked("UNRESOLVED_ORDER", "실제 주문 상태를 확인한 뒤 진행하세요.")
            elif filled and key[0] > self._baseline:
                _blocked("FOREIGN_FILL", "전략 장부에 없는 SOXL 체결이 있습니다.")
            active = remaining > 0 and status not in {"1", "2", "4"} and not _completed_auction_order(row, self._basis)
            if active:
                if status != "0" or meta is None or meta["archived"]:
                    _blocked("FOREIGN_ORDER", "소유권이 확인되지 않은 SOXL 미체결 주문이 있습니다.")
                if side == "SELL":
                    reserved_sells += remaining
            if meta and self._selected(owner_key):
                result.append(deepcopy(row))
        if reserved_sells > self._mixed_quantity + self._legacy_quantity:
            _blocked("SELLABLE", "전체 SOXL 매도 예약수량이 실제 보유수량을 초과합니다.")
        if capture:
            self._open_sell_reserve = reserved_sells
        return result

    def orders_today(self, *args, **kwargs):
        all_rows = self._client.orders_today()
        result = self._order_rows(all_rows)
        if args or kwargs:
            result = self._order_rows(self._client.orders_today(*args, **kwargs), capture=False)
        self._holdings()
        return result

    def _reservation_rows(self, rows, *, capture=True):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            _blocked("INVALID_BROKER_FACT", "계좌 예약주문 조회 결과를 확인하세요.")
        result, sell_reserved = [], 0
        for row in rows:
            if symbol(row) != "SOXL":
                continue
            number = row.get("oder_rsev_mst_no")
            day = row.get("oder_rsev_mst_date")
            key = (_history_day(day).isoformat(), _order_id(number)) if day and number else None
            meta = self._legacy_reservations.get(key)
            active = active_reservation(row, self._reservation_range[1])
            if active and meta is None:
                _blocked("FOREIGN_ORDER", "소유권이 확인되지 않은 SOXL 예약주문이 있습니다.")
            if meta:
                if ({"1": "SELL", "2": "BUY"}.get(str(row.get("seln_byov_clcd"))) != meta["side"]
                        or _quantity(row.get("odqt")) != meta["maximum"]):
                    _blocked("ORDER_MISMATCH", "예약주문의 실제 방향·수량이 전략 장부와 다릅니다.")
                if active and meta["side"] == "SELL":
                    sell_reserved += _quantity(row["odqt"])
                if self._strategy == "DONGPA":
                    result.append(deepcopy(row))
        if capture:
            self._reservation_sell_reserve = sell_reserved
        return result

    def reserved_orders(self, *args, **kwargs):
        # Always inspect the complete active reservation range before applying
        # the caller's narrower query or filters.
        self._reservation_rows(self._client.reserved_orders(*self._reservation_range, active_only=True))
        result = self._reservation_rows(self._client.reserved_orders(*args, **kwargs), capture=False)
        self._holdings()
        return result

    def orders_history(self, start_date, end_date, *args, **kwargs):
        all_rows = self._client.orders_history(start_date, end_date)
        self._history_rows(all_rows)
        rows = self._client.orders_history(start_date, end_date, *args, **kwargs) if args or kwargs else all_rows
        result = self._history_rows(rows)
        self._holdings()
        return result

    def _history_rows(self, rows):
        fills = normalize_confirmed_fills(rows)
        for fill in fills:
            key = (fill["trade_date"], fill["order_id"])
            meta = self._owners.get(key)
            if meta is None:
                if key[0] > self._baseline:
                    _blocked("FOREIGN_FILL", "전략 장부에 없는 SOXL 체결이 있습니다.")
                continue
            if (fill["side"] != meta["side"] or (meta["maximum"] is not None
                    and _quantity(fill["quantity"]) > meta["maximum"])
                    or _quantity(fill["quantity"]) < meta["confirmed"]
                    or (meta["archived"] and _quantity(fill["quantity"]) != meta["confirmed"])):
                _blocked("FILL_MISMATCH", "실제 체결의 방향·수량이 전략 장부와 다릅니다.")
        return [deepcopy(row) for row in rows if symbol(row) == "SOXL" and
                self._selected((_history_day(row.get("oder_date")).isoformat(), _order_id(row.get("oder_no"))))]

    def order_estimate(self, integrated_margin=False):
        if integrated_margin is not False:
            _blocked("BUYING_POWER", "전략별 주문은 통합증거금 없이 독립 USD 예산으로 확인하세요.")
        rows = self._client.order_estimate(integrated_margin=False)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            _blocked("BUYING_POWER", "독립 USD 주문가능금액을 확인하세요.")
        usd = [row for row in rows if row.get("crcd") == "USD"]
        if len(usd) != 1 or _money(usd[0].get("othr_crnc_evdn_camt")):
            _blocked("BUYING_POWER", "다른 통화 담보 없는 독립 USD 주문가능금액을 확인하세요.")
        result = deepcopy(usd[0])
        result["oder_crnc_oder_able_camt"] = str(min(self._budget, _money(usd[0].get("oder_crnc_oder_able_camt"))))
        self._holdings()
        return [result]


def make_broker_view(client, document, *, strategy, ledger_path, account, now,
                     mixed_quantity=None, legacy_quantity=None, before_submission=None):
    """Return a read allocation only for an explicit KMW2 hybrid transition."""
    if strategy not in {"MIXED", "DONGPA"}:
        raise ValueError("Strategy must be MIXED or DONGPA")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A timezone-aware projection instant is required")
    snapshot = document.get("KMW2", {}) if isinstance(document, dict) else {}
    transition = snapshot.get("strategyTransition", {})
    if not (isinstance(transition, dict) and transition.get("mode") == "DONGPA" and "atr70_r2" in snapshot):
        return _SubmissionGuard(client, before_submission) if before_submission is not None else client
    return _StrategyBrokerView(client, document, strategy=strategy, ledger_path=ledger_path, account=account,
        now=now, mixed_quantity=mixed_quantity, legacy_quantity=legacy_quantity,
        before_submission=before_submission)
