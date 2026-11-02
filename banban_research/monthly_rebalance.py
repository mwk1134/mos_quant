"""Monthly ATR/R2 planning and fill reconciliation, with no broker transport."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json

from .atr_sessions import as_et, first_month_session, session_open_utc
from .atr_settings import canonical, digest, scope_ids
from .strategy_presets import DEFAULT_STRATEGY_ID


R2_STRATEGY_ID = "dongpa_ma_v1_1_r2"
REBALANCE_POLICY = "FIRST_US_SESSION_OPEN_ATR_EXIT_THEN_50_50_V1"


class RebalanceError(ValueError):
    pass


def valid_position_id(value):
    return (type(value) is int and value > 0) or (isinstance(value, str) and 0 < len(value) <= 128)


def amount(value, *, nonnegative=True):
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise RebalanceError("REBALANCE_INVALID_AMOUNT") from exc
    if not number.is_finite() or (nonnegative and number < 0):
        raise RebalanceError("REBALANCE_INVALID_AMOUNT")
    return number


def shares(position):
    # Caller supplies the existing quantity SSOT, never a fresh seed/price estimate.
    value = position.get("effective_shares")
    if type(value) is not int or value < 0:
        raise RebalanceError("REBALANCE_EFFECTIVE_SHARES_REQUIRED")
    return value


def pro_rata_sales(positions, quantity):
    total = sum(shares(position) for position in positions)
    if type(quantity) is not int or not 0 <= quantity <= total:
        raise RebalanceError("REBALANCE_INVALID_SELL_QUANTITY")
    if not quantity:
        return []
    allocated = [{"position_id": position["id"], "quantity": quantity * shares(position) // total,
                  "remainder": quantity * shares(position) % total, "index": index}
                 for index, position in enumerate(positions)]
    remaining = quantity - sum(item["quantity"] for item in allocated)
    for item in sorted(allocated, key=lambda item: (-item["remainder"], item["index"]))[:remaining]:
        item["quantity"] += 1
    return [{"position_id": item["position_id"], "quantity": item["quantity"]}
            for item in allocated if item["quantity"]]


def _validate_scope(ledger):
    scope_ids(ledger.get("user_id"), ledger.get("account_id"))
    if type(ledger.get("revision")) is not int or ledger["revision"] < 0:
        raise RebalanceError("REBALANCE_INVALID_REVISION")


def _validate_sleeve(ledger, strategy):
    sleeve = ledger[strategy]
    amount(sleeve["cash"])
    amount(sleeve["reference_seed"])
    ids = []
    for position in sleeve["positions"]:
        scope_ids(position.get('user_id'), position.get('account_id'))
        if not valid_position_id(position.get("id")):
            raise RebalanceError("REBALANCE_INVALID_POSITION_ID")
        if position.get("account_id") != ledger["account_id"] or position.get("user_id") != ledger["user_id"]:
            raise RebalanceError("REBALANCE_POSITION_SCOPE_MISMATCH")
        allowed = {R2_STRATEGY_ID, DEFAULT_STRATEGY_ID} if strategy == "r2" else {"atr_breakout_v1"}
        if position.get("strategy_id") not in allowed:
            raise RebalanceError("REBALANCE_POSITION_STRATEGY_MISMATCH")
        if strategy == "r2" and (position.get("strategy_context") or {}).get("strategy_id") != position["strategy_id"]:
            raise RebalanceError("REBALANCE_POSITION_CONTEXT_REQUIRED")
        shares(position)
        datetime.strptime(position["entry_date"], "%Y-%m-%d")
        if strategy == "r2" and (position.get("mode") not in {"SF", "AG"} or type(position.get("round_number")) is not int):
            raise RebalanceError("REBALANCE_POSITION_CONTEXT_REQUIRED")
        if strategy == "atr" and not isinstance(position.get("atr_context"), dict):
            raise RebalanceError("REBALANCE_ATR_ENTRY_CONTEXT_REQUIRED")
        ids.append(position["id"])
    if len(ids) != len(set(ids)):
        raise RebalanceError("REBALANCE_DUPLICATE_POSITION")


def validate_ledger(ledger):
    _validate_scope(ledger)
    if ledger.get("r2_strategy_id") != R2_STRATEGY_ID:
        raise RebalanceError("REBALANCE_R2_STRATEGY_REQUIRED_NO_AUTO_SWITCH")
    if ledger.get("unresolved_orders"):
        raise RebalanceError("REBALANCE_ORDER_STATE_UNCERTAIN")
    for strategy in ("atr", "r2"):
        _validate_sleeve(ledger, strategy)


def atr_exit_intents(ledger, session_date):
    # Entry settings and the other sleeve must not gate an existing ATR exit.
    _validate_scope(ledger)
    _validate_sleeve(ledger, "atr")
    intents = []
    for position in ledger["atr"]["positions"]:
        if position.get("status", "OPEN") != "OPEN" or not shares(position):
            continue
        if position["entry_date"] >= session_date:
            raise RebalanceError("REBALANCE_ATR_SAME_DAY_POSITION")
        intents.append({"position_id": position["id"], "side": "SELL", "type": "MOO",
                        "symbol": "SOXL",
                        "quantity": shares(position), "session_date": session_date,
                        "atr_context": deepcopy(position.get("atr_context")),
                        "idempotency_key": digest([ledger["user_id"], ledger["account_id"], session_date, "ATR_EXIT", position["id"]])})
    return intents


def plan_monthly_rebalance(ledger, *, session_date, open_price, fee_rate, now=None, atr_weight_pct=50, initial=False,
                           sell_slippage_rate=0):
    validate_ledger(ledger)
    weight = amount(atr_weight_pct) / 100
    if weight > 1 or type(initial) is not bool:
        raise RebalanceError('REBALANCE_INVALID_WEIGHT')
    if not initial and first_month_session(session_date[:7]) != session_date:
        raise RebalanceError("REBALANCE_NOT_FIRST_US_SESSION")
    if now is not None and (now.tzinfo is None or now < session_open_utc(session_date)):
        raise RebalanceError("REBALANCE_BEFORE_US_OPEN")
    price, rate, slip = amount(open_price), amount(fee_rate), amount(sell_slippage_rate)
    if price <= 0 or rate + slip >= 1:
        raise RebalanceError("REBALANCE_INVALID_PRICE_OR_FEE")
    base = {"user_id": ledger["user_id"], "account_id": ledger["account_id"],
            "month": session_date[:7], "session_date": session_date, "policy": REBALANCE_POLICY,
            "ledger_revision": ledger["revision"], "ledger_hash": digest(ledger),
            "open_price": str(price), "fee_rate": str(rate),
            "scheduled_open": session_open_utc(session_date).isoformat(),
            "ledger": deepcopy(ledger), "execution_enabled": False}
    if slip:
        base['sell_slippage_rate'] = str(slip)
    if weight != Decimal('.5') or initial:
        base.update(policy='FIRST_US_SESSION_OPEN_WEIGHTED_V2', atr_weight_pct=str(weight * 100), initial=initial)
    exits = atr_exit_intents(ledger, session_date)
    if exits:
        return {**base, "status": "WAITING_ATR_EXIT", "atr_exits": exits, "sales": []}
    atr_cash, r2_cash = amount(ledger["atr"]["cash"]), amount(ledger["r2"]["cash"])
    positions = [position for position in ledger["r2"]["positions"] if position.get("status", "OPEN") == "OPEN"]
    total_shares = sum(shares(position) for position in positions)
    r2_nav = r2_cash + total_shares * price
    excess = weight * r2_nav - (1 - weight) * atr_cash
    shortfall = max(Decimal(0), excess - r2_cash)
    # Net sale proceeds fund the transfer; the remaining R2 NAV bears (1 - weight) of costs.
    quantity = int((shortfall / (price * (1 - (rate + slip) * (1 - weight)))).to_integral_value(rounding=ROUND_CEILING)) if shortfall else 0
    sales = [{**item, "side": "SELL", "type": "MARKET", "symbol": "SOXL"}
             for item in pro_rata_sales(positions, quantity)]
    fees = quantity * price * rate
    slippage = quantity * price * slip
    total_after_fees = atr_cash + r2_nav - fees - slippage
    transfer = total_after_fees * (1 - weight) - (r2_nav - fees - slippage)
    if -transfer > r2_cash + quantity * price * (1 - rate - slip) or transfer > atr_cash:
        raise RebalanceError("REBALANCE_INSUFFICIENT_CASH")
    plan = {**base, "status": "PLANNED", "atr_exits": [], "sales": sales,
            "estimated_sell_fee": str(fees), "estimated_transfer_to_r2": str(transfer),
            "estimated_total_after_fees": str(total_after_fees)}
    if slip:
        plan['estimated_sell_slippage'] = str(slippage)
    plan["plan_id"] = digest(plan)
    return plan


def _verified_fills(plan, expected, fills):
    if not isinstance(fills, list) or len(fills) != len(expected):
        raise RebalanceError("REBALANCE_FILLS_UNCONFIRMED")
    by_id = {}
    numbers = set()
    for fill in fills:
        scope_ids(fill.get('user_id'), fill.get('account_id'))
        if not valid_position_id(fill.get("position_id")):
            raise RebalanceError("REBALANCE_INVALID_POSITION_ID")
        if fill.get("verification") != "VERIFIED" or not fill.get("broker_order_no"):
            raise RebalanceError("REBALANCE_FILLS_UNCONFIRMED")
        if fill.get("user_id") != plan["user_id"] or fill.get("account_id") != plan["account_id"]:
            raise RebalanceError("REBALANCE_FILL_SCOPE_MISMATCH")
        if fill.get("session_date") != plan["session_date"] or fill.get("position_id") in by_id:
            raise RebalanceError("REBALANCE_FILL_IDENTITY_MISMATCH")
        number = str(fill["broker_order_no"])
        if number in numbers:
            raise RebalanceError("REBALANCE_DUPLICATE_BROKER_ORDER")
        numbers.add(number)
        by_id[fill["position_id"]] = fill
    for intent in expected:
        fill = by_id.get(intent["position_id"])
        if fill is None or type(fill.get("quantity")) is not int or fill["quantity"] != intent["quantity"]:
            raise RebalanceError("REBALANCE_PARTIAL_OR_MISMATCHED_FILL")
        if any(fill.get(key) != intent[key] for key in ("side", "type", "symbol")):
            raise RebalanceError("REBALANCE_FILL_ORDER_TYPE_MISMATCH")
        if amount(fill["price"]) <= 0:
            raise RebalanceError("REBALANCE_INVALID_FILL_PRICE")
        if amount(fill["fee"]) > fill["quantity"] * amount(fill["price"]):
            raise RebalanceError("REBALANCE_INVALID_FILL_FEE")
        slippage = amount(fill.get('slippage_cost', 0))
        expected_slippage = fill['quantity'] * amount(fill['price']) * amount(plan.get('sell_slippage_rate', 0))
        if slippage != expected_slippage:
            raise RebalanceError('REBALANCE_SLIPPAGE_MISMATCH')
        if slippage and not amount(plan.get('sell_slippage_rate', 0)):
            raise RebalanceError('REBALANCE_UNEXPECTED_SLIPPAGE')
        if amount(fill['fee']) + slippage > fill['quantity'] * amount(fill['price']):
            raise RebalanceError('REBALANCE_INVALID_EXECUTION_COST')
        timestamp = datetime.fromisoformat(fill["filled_at"])
        if timestamp.tzinfo is None or timestamp < session_open_utc(plan["session_date"]) or as_et(timestamp).date().isoformat() != plan["session_date"]:
            raise RebalanceError("REBALANCE_INVALID_FILL_TIME")
    return by_id


def reconcile_atr_exits(ledger, *, session_date, fills):
    expected = atr_exit_intents(ledger, session_date)
    proof = {"user_id": ledger["user_id"], "account_id": ledger["account_id"], "session_date": session_date}
    verified = _verified_fills(proof, expected, fills)
    updated = deepcopy(ledger)
    cash = amount(updated["atr"]["cash"])
    for position in updated["atr"]["positions"]:
        fill = verified.get(position["id"])
        if fill is None:
            continue
        cash += fill["quantity"] * amount(fill["price"]) - amount(fill["fee"])
        position.update(status="CLOSED", effective_shares=0, shares_override=0,
                        closed_at=fill["filled_at"], exit_price=fill["price"], exit_fee=fill["fee"])
    updated["atr"]["cash"] = str(cash)
    updated["revision"] += 1
    return updated


def settle_monthly_rebalance(plan, ledger, *, fills, valuation_price, valuation_at):
    validate_ledger(ledger)
    if plan.get("plan_id") != digest({key: value for key, value in plan.items() if key != "plan_id"}):
        raise RebalanceError("REBALANCE_CORRUPT_PLAN")
    if plan["status"] != "PLANNED" or plan["ledger_hash"] != digest(ledger):
        raise RebalanceError("REBALANCE_STALE_OR_UNREADY_PLAN")
    verified = _verified_fills(plan, plan["sales"], fills)
    price = amount(valuation_price)
    moment = datetime.fromisoformat(valuation_at)
    if price <= 0 or moment.tzinfo is None or moment < session_open_utc(plan["session_date"]):
        raise RebalanceError("REBALANCE_INVALID_VALUATION")
    if any(datetime.fromisoformat(fill["filled_at"]) > moment for fill in fills):
        raise RebalanceError("REBALANCE_VALUATION_BEFORE_FILL")
    updated = deepcopy(ledger)
    r2_cash = amount(updated["r2"]["cash"])
    fees, slippage_cost = Decimal(0), Decimal(0)
    deviations = []
    partial_sales = []
    for position in updated["r2"]["positions"]:
        fill = verified.get(position["id"])
        if fill is None:
            continue
        quantity, original = fill["quantity"], shares(position)
        before = deepcopy(position)
        fee = amount(fill["fee"])
        slip = amount(fill.get('slippage_cost', 0))
        r2_cash += quantity * amount(fill["price"]) - fee - slip
        fees += fee
        slippage_cost += slip
        remaining = original - quantity
        position.update(effective_shares=remaining, shares=remaining, shares_override=remaining)
        if "target_amount" in position:
            position["target_amount"] = str(amount(position["target_amount"]) * remaining / original)
        for key in ("entry_fee", "_entry_cost"):
            if key in position:
                position[key] = str(amount(position[key]) * remaining / original)
        if not remaining:
            position.update(status="CLOSED", closed_at=fill["filled_at"])
        deviations.append({"position_id": position["id"], "broker_order_no": str(fill["broker_order_no"]),
            "price_difference": str(amount(fill["price"]) - amount(plan["open_price"])),
            "delay_seconds": (datetime.fromisoformat(fill["filled_at"]) - session_open_utc(plan["session_date"])).total_seconds()})
        partial_sales.append({"position_id": position["id"], "quantity": quantity,
                              "before": before, "after": deepcopy(position),
                              "price": str(fill["price"]), "fee": str(fee)})
    remaining_value = sum(shares(position) * price for position in updated["r2"]["positions"] if position.get("status", "OPEN") == "OPEN")
    atr_cash = amount(updated["atr"]["cash"])
    total = atr_cash + r2_cash + remaining_value
    weight = amount(plan.get('atr_weight_pct', 50)) / 100
    transfer = total * (1 - weight) - (r2_cash + remaining_value)
    if r2_cash + transfer < 0 or atr_cash - transfer < 0:
        raise RebalanceError("REBALANCE_ACTUAL_CASH_SHORTFALL_REVIEW_REQUIRED")
    updated["atr"]["cash"] = str(atr_cash - transfer)
    updated["r2"]["cash"] = str(r2_cash + transfer)
    for strategy, delta in (("r2", transfer), ("atr", -transfer)):
        if strategy == "atr":
            updated[strategy]["reference_seed"] = str(atr_cash - transfer)
            if "seed" in updated[strategy]:
                updated[strategy]["seed"] = str(atr_cash - transfer)
            continue
        seed = amount(updated[strategy]["reference_seed"]) + delta
        if seed < 0:
            raise RebalanceError("REBALANCE_NEGATIVE_REFERENCE_SEED")
        updated[strategy]["reference_seed"] = str(seed)
        if "seed" in updated[strategy]:
            updated[strategy]["seed"] = str(amount(updated[strategy]["seed"]) + delta)
    updated["revision"] += 1
    audit = {"status": "COMPLETED", "plan_id": plan["plan_id"], "policy": plan['policy'],
             "session_date": plan["session_date"], "transfer_to_r2": str(transfer),
             "actual_fees": str(fees), "total_asset": str(total), "atr_asset": str(total * weight),
             "r2_asset": str(total * (1 - weight)), "valuation_price": str(price), "valuation_at": valuation_at,
             "fill_deviations": deviations, "fills": deepcopy(fills), "partial_sales": partial_sales}
    if amount(plan.get('sell_slippage_rate', 0)):
        audit['actual_slippage_cost'] = str(slippage_cost)
    return updated, audit
