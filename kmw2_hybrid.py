"""Atomically move free mixed cash to Dongpa while retaining frozen exits.

This coordinator submits no orders. The caller owns the account execution lock;
all accounting changes are saved together against a fresh GitHub content SHA.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, DecimalException
import hashlib
import json
import math
import re

import atr70_r2 as core
from atr70_r2_adapter import legacy_quantity, mixed_requested
from atr70_r2_runtime import MixedBlocked, reconcile_broker
from kmw_order_plan import ET, _kmw_config, completed_trading_date
from preset_snapshot_refresh import refresh_preset_snapshot
from preset_snapshot_sync import (_attach_lot_identifiers, _holding_quantity,
                                  _ledger_facts, _needed_buy_rules,
                                  normalize_confirmed_fills)
from us_market_calendar import is_us_equity_trading_day


_LOT = re.compile(r"^[1-9][0-9]*_\d{4}-\d{2}-\d{2}$")


def _blocked(code, message):
    raise MixedBlocked(code, message)


def _money(value):
    try:
        amount = core._amount(value)
        if not math.isfinite(float(amount)) or (amount and float(amount) == 0):
            raise ValueError
        return amount
    except (ValueError, DecimalException, OverflowError):
        _blocked("INVALID_BROKER_FACT", "병행 장부의 USD 금액을 확인하세요.")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def is_hybrid(snapshot):
    return (isinstance(snapshot, dict) and core.STATE_KEY in snapshot
            and isinstance(snapshot.get("strategyTransition"), dict)
            and snapshot["strategyTransition"].get("mode") == "DONGPA")


def mixed_owned_ids(snapshot):
    """Include archived identities permanently, even after the old book closes."""
    from kmw2_broker_view import mixed_owned_ids as owned
    return owned(snapshot)


def make_broker_view(*args, **kwargs):
    from kmw2_broker_view import make_broker_view as make
    return make(*args, **kwargs)


def _continue(should_continue):
    if should_continue is not None and not should_continue():
        _blocked("USER_STOPPED", "자동매매가 중단되어 장부 전환을 저장하지 않았습니다.")


def _validate_credits(snapshot):
    transition = snapshot["strategyTransition"]
    credits = transition.get("capital_credits")
    if not isinstance(credits, list) or any(not isinstance(item, dict) for item in credits):
        _blocked("CASH_TRANSFER_MISMATCH", "양쪽 장부의 내부 자금 이체 기록을 확인하세요.")
    transfers = snapshot[core.STATE_KEY].get("cash_transfers", [])
    if len(credits) != len(transfers):
        _blocked("CASH_TRANSFER_MISMATCH", "양쪽 장부의 내부 자금 이체 기록을 확인하세요.")
    by_id = {}
    for credit in credits:
        identity = credit.get("id")
        if not isinstance(identity, str) or identity in by_id:
            _blocked("CASH_TRANSFER_MISMATCH", "내부 자금 이체 식별자가 중복됩니다.")
        by_id[identity] = credit
    total = Decimal(0)
    for transfer in transfers:
        credit = by_id.get(transfer["id"])
        if (credit is None or credit.get("strategy") != transfer["strategy"]
                or credit.get("session_date") != transfer["session_date"]
                or _money(credit.get("amount")) != _money(transfer["amount"])):
            _blocked("CASH_TRANSFER_MISMATCH", "양쪽 장부의 내부 자금 이체 금액이 다릅니다.")
        total += _money(transfer["amount"])
    if total != _money(transition.get("transferred_cash")):
        _blocked("CASH_TRANSFER_MISMATCH", "내부 자금 이체 합계가 일치하지 않습니다.")


def _archive(snapshot, state, *, identity, basis, now, config, event):
    history = snapshot.setdefault("strategyHistory", [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        _blocked("INVALID_HISTORY", "기존 전략 전환 기록을 확인하세요.")
    history.append({"id": identity, "strategy_id": core.STRATEGY_ID, "event": event,
                    "basis_date": basis, "completed_at": now.isoformat(),
                    "mixed_state": deepcopy(state), "config_before": deepcopy(config),
                    "snapshot_before": {key: deepcopy(value) for key, value in snapshot.items()
                                        if key not in {core.STATE_KEY, "strategyHistory"}}})


def _start(document, *, basis, now):
    snapshot = document["KMW2"]
    state = snapshot[core.STATE_KEY]
    if legacy_quantity(snapshot):
        _blocked("LEGACY_HOLDINGS", "최초 전환 전 기존 동파 보유수량을 확인하세요.")
    config = _kmw_config(document, snapshot, preset="KMW2")
    processed = snapshot.get("processed_seed_dates")
    if not isinstance(processed, list):
        _blocked("SEED_CHECKPOINT_MISMATCH", "기존 시드 반영 기록을 확인하세요.")
    try:
        if any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day for day in processed):
            raise ValueError
        processed = sorted(set(processed))
    except (ValueError, TypeError):
        _blocked("SEED_CHECKPOINT_MISMATCH", "기존 시드 반영 기록을 확인하세요.")
    if any(seed["date"] <= basis and seed["date"] not in processed for seed in config["seed_increases"]):
        _blocked("SEED_CHECKPOINT_MISMATCH", "과거 시드 증액을 확인한 뒤 전환하세요.")
    identity = _digest({"state": state, "basis_date": basis,
                        "history_length": len(snapshot.get("strategyHistory", []))})
    raw_config = document.get("_preset_configs", {}).get("KMW2", {})
    _archive(snapshot, state, identity=identity, basis=basis, now=now,
             config=raw_config, event="DONGPA_STARTED")
    for key in list(snapshot):
        if _LOT.fullmatch(key) or key in {"pending_buy", "manual_buy_confirmations"}:
            snapshot.pop(key)
    snapshot.update(available_cash=0.0, compound_seed=.01, compound_reference_seed=.01,
                    compound_settlements=[], compound_trading_days=0,
                    compound_profit_rate=.7, compound_loss_rate=.2,
                    processed_seed_dates=processed, as_of_date=basis, cash_snapshot_date=basis,
                    broker_sync={"version": 1, "baseline_date": basis, "executions": {}},
                    strategyTransition={"id": identity, "mode": "DONGPA", "basis_date": basis,
                        "completed_at": now.isoformat(), "transferred_cash": "0",
                        "legacy_seed_initialized": False, "capital_credits": []})


def _facts(document, client, *, ledger_path, account, now, initial, basis):
    """Project each book only from its own cumulative execution evidence."""
    snapshot = document["KMW2"]
    state = snapshot[core.STATE_KEY]
    mappings, unresolved = _ledger_facts(ledger_path, account)
    if unresolved:
        _blocked("UNRESOLVED_ORDER", "접수 여부가 불명확한 계좌 주문을 확인하세요.")
    owned = mixed_owned_ids(snapshot)
    active = {(order["session_date"], str(order["broker_order_id"])): order
              for order in state["orders"].values() if order.get("broker_order_id")}
    cursor = snapshot.get("broker_sync", {}) if not initial else {}
    baseline = basis if initial else cursor.get("baseline_date", snapshot.get("as_of_date"))
    executions = cursor.get("executions", {})
    if not isinstance(executions, dict):
        _blocked("INVALID_REFRESH_DATA", "동파 체결 체크포인트를 확인하세요.")
    from kmw2_broker_view import legacy_owned_ids
    legacy_ids = legacy_owned_ids(document, ledger_path, account)
    for identity in executions:
        try:
            day, number = identity.split(":", 1)
            date.fromisoformat(day)
            legacy_ids.add((day, number))
        except (ValueError, AttributeError):
            _blocked("INVALID_REFRESH_DATA", "동파 체결 식별자를 확인하세요.")
    if owned & legacy_ids:
        # SQLite also contains mixed receipts; their ATR/R2 position namespaces
        # are evidence for the old book, never a second legacy ownership claim.
        for identity in owned & legacy_ids:
            mapping = mappings.get(identity, {})
            if _LOT.fullmatch(str(mapping.get("position_key", ""))):
                _blocked("CONFLICTING_LEDGER", "두 전략이 같은 체결을 소유하고 있습니다.")
        legacy_ids -= owned
    days = [state["activated_session"], baseline, *(day for day, _ in owned)]
    start, today = min(days), now.astimezone(ET).date().isoformat()
    fills = normalize_confirmed_fills(client.orders_history(start.replace("-", ""), today.replace("-", "")))
    if any(not start <= fill["trade_date"] <= today for fill in fills):
        _blocked("INVALID_BROKER_FACT", "체결 조회기간 밖의 기록이 있습니다.")
    lookup = {(fill["trade_date"], fill["order_id"]): fill for fill in fills}
    projected_mixed = 0
    for identity, order in active.items():
        fill = lookup.get(identity)
        quantity = 0 if fill is None else fill["quantity"]
        if (quantity < order["cumulative_quantity"] or quantity > order["quantity"]
                or (fill is not None and fill["side"] != order["side"])):
            _blocked("FILL_MISMATCH", "혼합 장부의 누적 체결이 실제 기록과 다릅니다.")
        projected_mixed += quantity if order["side"] == "BUY" else -quantity
    if projected_mixed < 0:
        _blocked("HOLDINGS_MISMATCH", "혼합 장부의 실제 보유수량을 확인하세요.")
    legacy_fills = []
    projected_legacy = 0 if initial else legacy_quantity(snapshot)
    projected_basis = projected_legacy
    intraday = False
    for fill in fills:
        identity = (fill["trade_date"], fill["order_id"])
        if identity in owned:
            continue
        if identity not in legacy_ids:
            cutoff = state["activated_session"] if initial else baseline
            unknown_after_checkpoint = (fill["trade_date"] >= cutoff if initial
                                        else fill["trade_date"] > cutoff)
            if unknown_after_checkpoint:
                _blocked("FOREIGN_FILL", "어느 전략에도 속하지 않는 SOXL 체결이 있습니다.")
            continue
        if initial and fill["trade_date"] < state["activated_session"]:
            continue  # A previous completed Dongpa period is already sealed.
        if initial:
            _blocked("FOREIGN_FILL", "최초 전환 전 동파 체결을 확인하세요.")
        legacy_fills.append(fill)
        key = f"{fill['trade_date']}:{fill['order_id']}"
        previous = executions.get(key, {})
        if fill["trade_date"] <= baseline and not previous:
            continue
        old_quantity = previous.get("quantity", 0)
        if type(old_quantity) is not int or old_quantity < 0 or fill["quantity"] < old_quantity:
            _blocked("FILL_CHANGED", "동파 누적 체결이 이전 체크포인트보다 줄었습니다.")
        delta = fill["quantity"] - old_quantity
        signed = delta if fill["side"] == "BUY" else -delta
        projected_legacy += signed
        if fill["trade_date"] <= basis:
            projected_basis += signed
        elif delta:
            intraday = True
    actual = _holding_quantity(client.holdings())
    if min(projected_legacy, projected_basis) < 0 or projected_mixed + projected_legacy != actual:
        _blocked("HOLDINGS_MISMATCH", "두 장부의 확정 보유 합계가 실제 잔고와 다릅니다.")
    return {"mixed_quantity": projected_mixed, "legacy_quantity": projected_legacy,
            "legacy_snapshot_quantity": projected_basis, "intraday": intraday,
            "fills": _attach_lot_identifiers(legacy_fills, mappings), "actual": actual}


def _credit_free_cash(document, client, *, basis, now):
    snapshot = document["KMW2"]
    state = snapshot[core.STATE_KEY]
    if ("pending_rebalance" in state
            or any(order["status"] in {"UNKNOWN", "SENDING"} for order in state["orders"].values())):
        return
    estimates = [row for row in client.order_estimate(integrated_margin=False) if row.get("crcd") == "USD"]
    if len(estimates) != 1 or _money(estimates[0].get("othr_crnc_evdn_camt")):
        _blocked("BUYING_POWER", "다른 통화 담보 없는 독립 USD 주문가능금액이 필요합니다.")
    cash = _money(snapshot["available_cash"])
    capacity = max(Decimal(0), _money(estimates[0].get("oder_crnc_oder_able_camt")) - cash)
    transition = snapshot["strategyTransition"]
    credits = transition.setdefault("capital_credits", [])
    if not isinstance(credits, list):
        _blocked("INVALID_HISTORY", "내부 자금 이체 기록을 확인하세요.")
    session = now.astimezone(ET).date().isoformat()
    if not is_us_equity_trading_day(date.fromisoformat(session)):
        session = basis
    for strategy in ("ATR", "R2"):
        amount = min(core.available_cash(state, strategy), capacity)
        if not amount:
            continue
        identity = _digest({"transition": transition["id"], "strategy": strategy,
                            "cash_transfers": state.get("cash_transfers", []), "amount": str(amount),
                            "session_date": session})
        state = core.transfer_cash(state, strategy=strategy, amount=amount,
                                   transfer_id=identity, session_date=session)
        cash += amount
        if not transition.get("legacy_seed_initialized", False):
            # Remove only the dormant sentinel, preserving any later external
            # seed deposits the ordinary refresher has already confirmed.
            snapshot["compound_seed"] = float(_money(snapshot["compound_seed"]) - Decimal("0.01") + amount)
            snapshot["compound_reference_seed"] = float(_money(snapshot["compound_reference_seed"]) - Decimal("0.01") + amount)
            transition["legacy_seed_initialized"] = True
        else:
            snapshot["compound_seed"] = float(_money(snapshot["compound_seed"]) + amount)
            snapshot["compound_reference_seed"] = float(_money(snapshot["compound_reference_seed"]) + amount)
        credits.append({"id": identity, "strategy": strategy, "amount": str(amount), "session_date": session})
        transition["transferred_cash"] = str(_money(transition.get("transferred_cash", "0")) + amount)
        capacity -= amount
    snapshot[core.STATE_KEY] = state
    snapshot["available_cash"] = float(cash)


def sync_hybrid(document, sha, *, github, client, ledger_path, account, now, should_continue=None):
    """Reconcile both books and save the whole candidate in one bounded SHA CAS."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _blocked("INVALID_REFRESH_DATE", "시간대가 있는 현재 시각이 필요합니다.")
    basis = completed_trading_date(now.astimezone(ET)).isoformat()
    for attempt in range(3):
        _continue(should_continue)
        snapshot = document.get("KMW2") if isinstance(document, dict) else None
        if not isinstance(snapshot, dict):
            _blocked("SNAPSHOT_MISSING", "KMW2 장부가 필요합니다.")
        state = snapshot.get(core.STATE_KEY)
        if state is None:
            return document, {"handled": False, "state": "DONGPA_RESUMED", "orders": [],
                              "allow_legacy_schedule": True}
        core.validate_state(state)
        try:
            checkpoint = snapshot.get("as_of_date")
            if date.fromisoformat(checkpoint).isoformat() != checkpoint or checkpoint > basis:
                raise ValueError
        except (ValueError, TypeError):
            _blocked("INVALID_REFRESH_DATE", "기존 장부의 완료 거래일을 확인하세요.")
        initial = not is_hybrid(snapshot)
        if initial and mixed_requested(document):
            _blocked("SWITCH_ENABLED", "반반매매 OFF 요청이 변경되어 전환을 중단했습니다.")
        if (state["phase"] == "ACTIVE"
                and state["activated_session"] > now.astimezone(ET).date().isoformat()):
            # A preconfigured activation is not permission to fabricate a
            # future-dated cash transfer or run the future ledger backwards.
            return document, {"handled": True, "state": "WAITING_ACTIVATION_SESSION", "orders": [],
                "allow_legacy_schedule": False,
                "reason": "반반매매 시작 미국 거래일 이후 현금 배정을 전환합니다. 신규 매수는 중단했습니다."}
        if not initial:
            _validate_credits(snapshot)
        elif state.get("cash_transfers"):
            _blocked("CASH_TRANSFER_MISMATCH", "기존 내부 이체의 동파 수신 장부를 확인하세요.")
        if state["phase"] == "WAITING_FLAT":
            from kmw_strategy_transition import build_dongpa_handoff
            candidate = build_dongpa_handoff(document, expected_state=state,
                basis_date=basis, now=now, broker_cash=None)
            facts = {"mixed_quantity": 0, "legacy_quantity": legacy_quantity(candidate["KMW2"]),
                     "legacy_snapshot_quantity": legacy_quantity(candidate["KMW2"]), "intraday": False}
            status = "DONGPA_RESUMED"
        else:
            facts = _facts(document, client, ledger_path=ledger_path, account=account,
                           now=now, initial=initial, basis=basis)
            candidate = deepcopy(document)
            if initial:
                _start(candidate, basis=basis, now=now)
            mixed_view = make_broker_view(client, candidate, strategy="MIXED", ledger_path=ledger_path,
                account=account, now=now, mixed_quantity=facts["mixed_quantity"],
                legacy_quantity=facts["legacy_quantity"])
            updated, _ = reconcile_broker(state, mixed_view, now=now,
                reservation_start_date=document.get("_preset_configs", {}).get("KMW2", {}).get("session_start_date", "2026-04-29"))
            candidate["KMW2"][core.STATE_KEY] = updated
            projection = deepcopy(candidate)
            projection["KMW2"].pop(core.STATE_KEY)
            completed_fills = [fill for fill in facts["fills"] if fill["trade_date"] <= basis]
            rules = _needed_buy_rules(projection, "KMW2", completed_fills)
            projection = refresh_preset_snapshot(projection, preset="KMW2", basis_date=basis,
                fills=completed_fills, broker_quantity=facts["legacy_snapshot_quantity"], buy_rules=rules)
            projection["KMW2"][core.STATE_KEY] = updated
            candidate = projection
            _credit_free_cash(candidate, client, basis=basis, now=now)
            state = candidate["KMW2"][core.STATE_KEY]
            if sum(lot["shares"] for sleeve in ("atr", "r2") for lot in state[sleeve]["lots"]) != facts["mixed_quantity"]:
                _blocked("HOLDINGS_MISMATCH", "혼합 체결 재조정 후 보유수량이 다릅니다.")
            status = "DONGPA_STARTED" if initial else "HYBRID_ACTIVE"
            can_archive = (not facts["mixed_quantity"] and not state.get("pending_rebalance")
                and all(order["status"] in core.TERMINAL_STATUSES and order["session_date"] <= basis
                        for order in state["orders"].values())
                and _money(state["atr"]["cash"]) + _money(state["r2"]["cash"]) == 0)
            if can_archive:
                target = candidate["KMW2"]
                _archive(target, state, identity=_digest({"transition": target["strategyTransition"]["id"],
                    "state": state, "event": "DONGPA_RESUMED"}), basis=basis, now=now,
                    config=candidate.get("_preset_configs", {}).get("KMW2", {}), event="DONGPA_RESUMED")
                target.pop(core.STATE_KEY)
                target.pop("atr70_r2_status", None)
                target.pop("mixed_updated_at", None)
                target["strategyTransition"]["exits_completed_at"] = now.isoformat()
                status = "DONGPA_RESUMED"
            if _holding_quantity(client.holdings()) != facts["mixed_quantity"] + facts["legacy_quantity"]:
                _blocked("HOLDINGS_MISMATCH", "저장 전 실제 보유수량이 변경되어 다시 확인해야 합니다.")
        _continue(should_continue)
        result = {"handled": core.STATE_KEY in candidate["KMW2"], "state": status, "orders": [],
                  "allow_legacy_schedule": not facts["intraday"],
                  **{key: facts[key] for key in ("mixed_quantity", "legacy_quantity", "legacy_snapshot_quantity")}}
        if facts["intraday"]:
            result["blocked_reason"] = "CURRENT_SESSION_LEGACY_FILL"
        if candidate == document:
            return candidate, result
        outcome = github.save(candidate, sha, "KMW2")
        if outcome == "saved":
            return candidate, result
        if outcome != "conflict":
            _blocked("SNAPSHOT_SAVE_UNCONFIRMED", "병행 장부 저장 결과를 확인하세요.")
        document, sha = github.read()
    _blocked("SNAPSHOT_CONFLICT", "장부가 반복 변경되어 최신 상태 확인 후 재시도합니다.")
