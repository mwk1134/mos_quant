"""Pure snapshot handoff from an inactive KMW2 mix to the original strategy.

The caller reconciles broker fills and verifies actual holdings, open orders,
reservations, and settled USD cash before saving this full document with CAS.
"""

from copy import deepcopy
from datetime import date, datetime
from decimal import DecimalException
import hashlib
import json
import math
import re

import atr70_r2 as mixed
from banban_research.atr_sessions import exchange_calendar
from kmw_order_plan import _kmw_config


class TransitionBlocked(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _day(value, code="INVALID_BASIS_DATE"):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise TransitionBlocked(code, "전환 기준일을 YYYY-MM-DD 형식으로 확인하세요.") from None


def _money(value):
    try:
        return mixed._amount(value)
    except (ValueError, DecimalException):
        raise TransitionBlocked("INVALID_CASH", "전환 예수금과 전략별 현금을 확인하세요.") from None


def _completed_basis(basis_date, now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise TransitionBlocked("INVALID_TIME", "전환 시각에 시간대 정보가 필요합니다.")
    basis = _day(basis_date)
    calendar = exchange_calendar()
    try:
        if not calendar.is_session(basis.isoformat()):
            raise ValueError
        closed = calendar.session_close(basis.isoformat()).to_pydatetime()
    except (TypeError, ValueError):
        raise TransitionBlocked("INVALID_BASIS_DATE", "완료된 미국 거래일을 전환 기준으로 사용하세요.") from None
    if now < closed:
        raise TransitionBlocked("BASIS_NOT_COMPLETED", "미국 정규장이 끝난 기준일을 확인한 뒤 전환합니다.")
    return basis


def _legacy_lot_keys(snapshot):
    return [key for key in snapshot
            if isinstance(key, str) and re.fullmatch(r"[1-9][0-9]*_\d{4}-\d{2}-\d{2}", key)]


def _active_cash(state, snapshot, config, basis, broker_cash):
    if any(lot["shares"] for sleeve in ("atr", "r2") for lot in state[sleeve]["lots"]):
        raise TransitionBlocked("MIXED_HOLDINGS", "혼합 보유분의 실제 전량 청산을 확인한 뒤 전환합니다.")
    if any(order["status"] not in mixed.TERMINAL_STATUSES for order in state["orders"].values()):
        raise TransitionBlocked("UNRESOLVED_ORDER", "접수·체결이 확인되지 않은 혼합 주문을 먼저 확인하세요.")
    if any(_day(order["session_date"]) > basis for order in state["orders"].values()):
        raise TransitionBlocked("INCOMPLETE_ORDER_SESSION", "마지막 혼합 주문 거래일이 완료된 뒤 전환합니다.")
    if state.get("pending_rebalance") is not None:
        raise TransitionBlocked("PENDING_REBALANCE", "진행 중인 비중 조정을 확인한 뒤 전환합니다.")
    for key in _legacy_lot_keys(snapshot):
        lot = snapshot[key]
        if (not isinstance(lot, dict) or type(lot.get("shares")) is not int
                or lot["shares"] < 0):
            raise TransitionBlocked("INVALID_LEGACY_HOLDING", "기존 기본 전략의 보유내역을 확인하세요.")
        if lot["shares"]:
            raise TransitionBlocked("LEGACY_HOLDINGS", "혼합 장부와 기존 기본 전략 보유분이 함께 남아 있습니다.")
    processed = snapshot.get("processed_seed_dates")
    if not isinstance(processed, list):
        raise TransitionBlocked("INVALID_SEED_CHECKPOINT", "기존 시드증액 처리 기록을 확인하세요.")
    processed = {_day(day, "INVALID_SEED_CHECKPOINT").isoformat() for day in processed}
    if any(_day(seed["date"], "INVALID_PRESET_CONFIG") <= basis and seed["date"] not in processed
           for seed in config["seed_increases"]):
        raise TransitionBlocked("UNPROCESSED_SEED", "아직 반영되지 않은 과거 시드증액을 확인한 뒤 전환합니다.")
    try:
        cash = _money(state["atr"]["cash"]) + _money(state["r2"]["cash"])
        outside = broker_cash - cash
        legacy_cash = float(cash)
    except (DecimalException, OverflowError):
        raise TransitionBlocked("INVALID_CASH", "전환 예수금과 전략별 현금을 확인하세요.") from None
    if cash <= 0:
        raise TransitionBlocked("EMPTY_STRATEGY_CASH", "기본 전략으로 이전할 운용 현금이 없습니다.")
    if not math.isfinite(legacy_cash) or legacy_cash <= 0:
        raise TransitionBlocked("INVALID_CASH", "전환 예수금과 전략별 현금을 확인하세요.")
    if outside < 0:
        raise TransitionBlocked("INSUFFICIENT_SETTLED_CASH", "정산 완료 USD 예수금이 전략 운용 현금보다 부족합니다.")
    return cash, outside


def build_dongpa_handoff(document, *, expected_state, basis_date, now, broker_cash):
    """Return a new full document after verifying the source mixed state.

    Active capital is rebased from both sleeves' reconciled cash. A waiting,
    never-funded mix only cancels its request and preserves the legacy account.
    """
    basis = _completed_basis(basis_date, now)
    if not isinstance(document, dict) or not isinstance(document.get("KMW2"), dict):
        raise TransitionBlocked("INVALID_DOCUMENT", "KMW2 원본 저장 문서를 확인하세요.")
    result = deepcopy(document)
    snapshot = result["KMW2"]
    if _day(snapshot.get("as_of_date")) > basis:
        raise TransitionBlocked("INVALID_BASIS_DATE", "저장된 보유 기준일보다 이전으로 전환할 수 없습니다.")
    if mixed.STATE_KEY not in snapshot or snapshot[mixed.STATE_KEY] != expected_state:
        raise TransitionBlocked("STATE_CHANGED", "혼합 장부가 변경되어 전환을 중단했습니다. 다시 확인하세요.")
    state = snapshot[mixed.STATE_KEY]
    try:
        mixed.validate_state(state)
    except (KeyError, TypeError, ValueError, DecimalException):
        raise TransitionBlocked("INVALID_MIXED_STATE", "혼합 전략 장부를 확인한 뒤 전환하세요.") from None
    try:
        config = _kmw_config(result, snapshot, preset="KMW2")
    except (KeyError, TypeError, ValueError):
        raise TransitionBlocked("INVALID_PRESET_CONFIG", "KMW2 저장 설정을 확인하세요.") from None
    if config[mixed.MIXED_SWITCH_KEY]:
        raise TransitionBlocked("SWITCH_ENABLED", "반반매매 적용이 꺼진 상태에서만 기본 전략으로 전환합니다.")
    history = snapshot.get("strategyHistory", [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        raise TransitionBlocked("INVALID_HISTORY", "기존 전략 전환 기록을 확인하세요.")
    try:
        payload = json.dumps({"state": state, "basis_date": basis_date, "history_length": len(history)},
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise TransitionBlocked("INVALID_MIXED_STATE", "혼합 전략 장부의 저장 형식을 확인하세요.") from None
    identity = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    completed_at = now.isoformat()
    config_before = deepcopy(result.get("_preset_configs", {}).get("KMW2", {}))
    archive = {"id": identity, "strategy_id": mixed.STRATEGY_ID,
               "basis_date": basis_date, "completed_at": completed_at,
               "mixed_state": deepcopy(state), "config_before": config_before,
               "snapshot_before": deepcopy({key: value for key, value in snapshot.items()
                                             if key not in {mixed.STATE_KEY, "strategyHistory"}})}
    transition = {"id": identity, "mode": "DONGPA", "basis_date": basis_date,
                  "completed_at": completed_at}
    if state["phase"] == "ACTIVE":
        cash, outside = _active_cash(state, snapshot, config, basis, _money(broker_cash))
        snapshot.update(available_cash=float(cash), compound_seed=float(cash),
                        compound_reference_seed=float(cash), compound_settlements=[],
                        compound_trading_days=0, compound_profit_rate=0.70, compound_loss_rate=0.20,
                        as_of_date=basis_date, cash_snapshot_date=basis_date,
                        processed_seed_dates=sorted(snapshot["processed_seed_dates"]),
                        broker_sync={"version": 1, "baseline_date": basis_date, "executions": {}})
        for key in [*_legacy_lot_keys(snapshot), "pending_buy", "manual_buy_confirmations"]:
            snapshot.pop(key, None)
        transition.update(transferred_cash=str(cash), outside_budget_cash=str(outside))
    else:
        transition.update(cancelled_before_activation=True, transferred_cash="0", outside_budget_cash=None)
    for key in (mixed.STATE_KEY, "atr70_r2_status", "mixed_updated_at"):
        snapshot.pop(key, None)
    snapshot[mixed.MIXED_SWITCH_KEY] = False
    snapshot["strategyHistory"] = history + [archive]
    snapshot["strategyTransition"] = transition
    result.setdefault("_preset_configs", {}).setdefault("KMW2", {})[mixed.MIXED_SWITCH_KEY] = False
    return result
