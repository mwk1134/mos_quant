"""Pure, account-scoped accounting and signals for the ATR70/R2 mix.

Money is stored as decimal strings.  No function contacts a broker or infers a
fill from a quote.  Callers must persist an intent before dispatch and reconcile
broker-confirmed cumulative fills before spending their proceeds.
"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json

from us_market_calendar import is_us_equity_trading_day

MIXED_SWITCH_KEY = "atr70_r2_mix_enabled"
STATE_KEY = "atr70_r2"
STRATEGY_ID = "atr70_r2_mix"
SCHEMA_VERSION = 1
MARKET_BUY_BUFFER = Decimal("0.10")
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELLED", "REJECTED"})
ORDER_STATUSES = TERMINAL_STATUSES | {"PLANNED", "SENDING", "ACCEPTED", "OPEN", "UNKNOWN"}
ATR_RULES = {"strategy_id": "atr_breakout_v1", "atr_method": "WILDER", "atr_period": 70,
             "lookback_days": 252, "max_percentile": "75", "offset_pct": "1.5",
             "exit_rule": "NEXT_OPEN"}
R2_PRESET = {
    "strategy_id": "dongpa_ma_v1_1_r2", "profit_reflection_rate": "0.72",
    "loss_reflection_rate": "0.213", "reference_seed_renewal_days": 10,
    "settlement_delay_days": 7,
    "SF": {"buy_multiplier": "1.0625", "sell_multiplier": "1.015", "max_holding_days": 35,
           "round_weights": ["0.049", "0.127", "0.23", "0.257", "0.028", "0.169", "0.14"]},
    "AG": {"buy_multiplier": "1.1525", "sell_multiplier": "1.068", "max_holding_days": 7,
           "round_weights": ["0.2", "0.2", "0.3", "0.3"]},
}
_BASE = {
    "SF": {"buy_multiplier": "1.035", "sell_multiplier": "1.011", "max_holding_days": 35,
           "round_weights": deepcopy(R2_PRESET["SF"]["round_weights"])},
    "AG": {"buy_multiplier": "1.036", "sell_multiplier": "1.035", "max_holding_days": 7,
           "round_weights": ["0.062", "0.134", "0.118", "0.148", "0.15", "0.182", "0.186", "0.02"]},
}


def _amount(value, *, positive=False, signed=False):
    if isinstance(value, bool):
        raise ValueError("Boolean is not an amount")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Invalid amount") from exc
    if not result.is_finite() or (not signed and result < 0) or (positive and result <= 0):
        raise ValueError("Invalid amount")
    return result


def _count(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError("Quantity must be an integer")
    return value


def _day(value):
    if not isinstance(value, str):
        raise ValueError("ISO session date required")
    try:
        normalized = date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("ISO session date required") from exc
    if normalized != value:
        raise ValueError("ISO session date required")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _state_hash(state):
    # The execution layer persists its in-flight plan alongside accounting.
    return _digest({key: value for key, value in state.items() if key != "pending_rebalance"})


def _sleeve(strategy):
    if strategy not in {"ATR", "R2"}:
        raise ValueError("Unknown strategy")
    return strategy.lower()


def r2_rules(mode, ma_aligned=True):
    if mode not in {"SF", "AG"} or type(ma_aligned) is not bool:
        raise ValueError("Invalid R2 mode/alignment")
    rules = deepcopy((R2_PRESET if ma_aligned else _BASE)[mode])
    rules.update({key: deepcopy(R2_PRESET[key]) for key in (
        "profit_reflection_rate", "loss_reflection_rate", "reference_seed_renewal_days", "settlement_delay_days")})
    rules.update(mode=mode, ma_aligned=ma_aligned,
                 strategy_id=R2_PRESET["strategy_id"] if ma_aligned else "dongpa_modified")
    return rules


def _validate_rules(rules, strategy):
    if not isinstance(rules, dict):
        raise ValueError("Frozen entry rules required")
    expected = ATR_RULES if strategy == "ATR" else r2_rules(rules.get("mode"), rules.get("ma_aligned"))
    if any(rules.get(key) != value for key, value in expected.items()):
        raise ValueError("Frozen entry rules do not match strategy")


def initial_state(preset="KMW2", *, session_date, broker_cash, broker_shares,
                  open_order_count, broker_confirmed, fee_rate="0.0005"):
    if preset != "KMW2":
        raise ValueError("ATR70/R2 is restricted to KMW2")
    _day(session_date)
    _amount(broker_cash)
    _count(broker_shares)
    _count(open_order_count)
    if type(broker_confirmed) is not bool:
        raise ValueError("Broker confirmation must be explicit")
    rate = _amount(fee_rate)
    if rate >= 1:
        raise ValueError("Invalid fee rate")
    state = {"schema_version": SCHEMA_VERSION, "strategy_id": STRATEGY_ID, "preset": preset,
             "phase": "WAITING_FLAT", "activated_session": None, "fee_rate": str(rate),
             "initial_cash": str(_amount(broker_cash)),
             "unallocated_cash": str(_amount(broker_cash)),
             "atr": {"cash": "0", "lots": []},
             "r2": {"cash": "0", "lots": [], "seed": "0", "reference_seed": "0",
                    "trading_days": 0, "last_seed_session": None, "settlements": [], "mode": "SF"},
             "orders": {}, "last_atr_entry_session": None, "rebalance_months": []}
    return activate_if_flat(state, session_date=session_date, broker_cash=broker_cash,
                            broker_shares=broker_shares, open_order_count=open_order_count,
                            broker_confirmed=broker_confirmed)


def activate_if_flat(state, *, session_date, broker_cash, broker_shares, open_order_count, broker_confirmed):
    validate_state(state)
    _day(session_date)
    cash = _amount(broker_cash)
    _count(broker_shares)
    _count(open_order_count)
    if type(broker_confirmed) is not bool:
        raise ValueError("Broker confirmation must be explicit")
    result = deepcopy(state)
    if result["phase"] == "ACTIVE":
        return result
    result["unallocated_cash"] = str(cash)
    if broker_confirmed and broker_shares == 0 and open_order_count == 0:
        half = cash / 2
        result.update(phase="ACTIVE", activated_session=session_date, unallocated_cash="0", initial_cash=str(cash))
        result["atr"]["cash"] = result["r2"]["cash"] = str(half)
        result["r2"]["seed"] = result["r2"]["reference_seed"] = str(half)
        result["rebalance_months"] = [session_date[:7]]
    validate_state(result)
    return result


def validate_state(state):
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported mixed state schema")
    if state.get("preset") != "KMW2" or state.get("strategy_id") != STRATEGY_ID:
        raise ValueError("Mixed state identity mismatch")
    if state.get("phase") not in {"WAITING_FLAT", "ACTIVE"}:
        raise ValueError("Invalid mixed phase")
    if _amount(state.get("fee_rate")) >= 1:
        raise ValueError("Invalid fee reserve")
    _amount(state.get("unallocated_cash"))
    _amount(state.get("initial_cash"))
    if state["phase"] == "ACTIVE":
        _day(state.get("activated_session"))
        if _amount(state["unallocated_cash"]) != 0:
            raise ValueError("Active state contains unallocated cash")
    elif state.get("activated_session") is not None:
        raise ValueError("Waiting state already activated")
    lot_ids = set()
    for strategy in ("ATR", "R2"):
        sleeve = state.get(_sleeve(strategy))
        if not isinstance(sleeve, dict) or not isinstance(sleeve.get("lots"), list):
            raise ValueError("Strategy cash/lot ledger required")
        _amount(sleeve.get("cash"))
        for lot in sleeve["lots"]:
            identity = lot.get("lot_id")
            if not isinstance(identity, str) or not identity or identity in lot_ids:
                raise ValueError("Duplicate or invalid lot identity")
            lot_ids.add(identity)
            if lot.get("strategy") != strategy:
                raise ValueError("Lot strategy mismatch")
            _count(lot.get("shares"))
            if _count(lot.get("entered_shares"), positive=True) < lot["shares"]:
                raise ValueError("Lot holding exceeds confirmed entry quantity")
            _day(lot.get("entry_date"))
            _amount(lot.get("entry_price"), positive=True)
            _amount(lot.get("entry_fee"))
            _amount(lot.get("entry_notional"))
            _amount(lot.get("remaining_cost"))
            _validate_rules(lot.get("frozen_rules"), strategy)
            if strategy == "ATR":
                if _day(lot.get("exit_session")) <= lot["entry_date"]:
                    raise ValueError("ATR exit must be the next session")
            else:
                _count(lot.get("round"), positive=True)
                if lot["round"] > len(lot["frozen_rules"]["round_weights"]):
                    raise ValueError("R2 round outside frozen entry rules")
        if state["phase"] == "WAITING_FLAT" and (sleeve["lots"] or _amount(sleeve["cash"])):
            raise ValueError("Waiting state cannot own strategy assets")
    r2 = state["r2"]
    for key in ("seed", "reference_seed"):
        _amount(r2.get(key))
    _count(r2.get("trading_days"))
    if r2.get("mode") not in {"SF", "AG"} or not isinstance(r2.get("settlements"), list):
        raise ValueError("Invalid R2 state")
    if r2.get("last_seed_session") is not None:
        _day(r2["last_seed_session"])
    for item in r2["settlements"]:
        _day(item["settlement_date"])
        _amount(item["profit_loss"], signed=True)
        _validate_rules(item["frozen_rules"], "R2")
    if not isinstance(state.get("orders"), dict) or not isinstance(state.get("rebalance_months"), list):
        raise ValueError("Execution and rebalance ledgers required")
    if len(set(state["rebalance_months"])) != len(state["rebalance_months"]):
        raise ValueError("Duplicate rebalance month")
    for month in state["rebalance_months"]:
        if date.fromisoformat(month + "-01").strftime("%Y-%m") != month:
            raise ValueError("Invalid rebalance month")
    if state.get("last_atr_entry_session") is not None:
        _day(state["last_atr_entry_session"])
    broker_ids = set()
    for identity, order in state["orders"].items():
        if identity != order.get("intent_id") or order.get("status") not in ORDER_STATUSES:
            raise ValueError("Invalid order identity/status")
        _validate_intent(order)
        quantity = _count(order.get("cumulative_quantity"))
        if quantity > order["quantity"]:
            raise ValueError("Order overfill")
        notional = _amount(order.get("cumulative_notional"))
        fee = _amount(order.get("cumulative_fee"))
        if (quantity == 0 and (notional or fee)) or (quantity and notional <= 0):
            raise ValueError("Invalid cumulative accounting")
        if order["status"] == "FILLED" and quantity != order["quantity"]:
            raise ValueError("Unconfirmed full fill")
        broker_id = order.get("broker_order_id")
        if order.get("fee_source", "BROKER") not in {"BROKER", "CONFIGURED_RESERVE"}:
            raise ValueError("Invalid order fee source")
        if broker_id is not None:
            broker_identity = (order["session_date"], broker_id)
            if not isinstance(broker_id, str) or not broker_id or broker_identity in broker_ids:
                raise ValueError("Duplicate or invalid broker order identity")
            broker_ids.add(broker_identity)
        elif order["status"] in {"ACCEPTED", "OPEN", "FILLED"}:
            raise ValueError("Accepted order requires broker identity")
    if state["phase"] == "WAITING_FLAT" and state["orders"]:
        raise ValueError("Waiting state cannot dispatch orders")
    if state["phase"] == "ACTIVE":
        cash = _amount(state["atr"]["cash"]) + _amount(state["r2"]["cash"])
        expected_cash = _amount(state["initial_cash"])
        quantities = {}
        for order in state["orders"].values():
            side = 1 if order["side"] == "SELL" else -1
            expected_cash += side * _amount(order["cumulative_notional"]) - _amount(order["cumulative_fee"])
            key = (order["strategy"], order["lot_id"])
            quantities[key] = quantities.get(key, 0) - side * order["cumulative_quantity"]
        if cash != expected_cash:
            raise ValueError("Mixed cash ledger does not conserve confirmed funds")
        for strategy in ("ATR", "R2"):
            for lot in state[_sleeve(strategy)]["lots"]:
                if quantities.pop((strategy, lot["lot_id"]), 0) != lot["shares"]:
                    raise ValueError("Lot shares do not match confirmed cumulative fills")
        if any(quantities.values()):
            raise ValueError("Confirmed fills are missing their strategy lot")
        for strategy in ("ATR", "R2"):
            sleeve = state[_sleeve(strategy)]
            buys = [order for order in state["orders"].values() if order["strategy"] == strategy
                    and order["side"] == "BUY" and order["status"] not in TERMINAL_STATUSES]
            reserved_cash = sum((_amount(order["price"]) * (order["quantity"] - order["cumulative_quantity"]) *
                                 (1 + _amount(state["fee_rate"])) for order in buys), Decimal(0))
            if reserved_cash > _amount(sleeve["cash"]):
                raise ValueError("Pending orders exceed strategy fee reserve")
            for lot in sleeve["lots"]:
                entered = sum(order["cumulative_quantity"] for order in state["orders"].values()
                              if order["strategy"] == strategy and order["side"] == "BUY" and order["lot_id"] == lot["lot_id"])
                if entered != lot["entered_shares"]:
                    raise ValueError("Lot original shares do not match confirmed entries")
                reserved = sum(order["quantity"] - order["cumulative_quantity"] for order in state["orders"].values()
                               if order["strategy"] == strategy and order["side"] == "SELL" and
                               order["lot_id"] == lot["lot_id"] and order["status"] not in TERMINAL_STATUSES)
                if reserved > lot["shares"]:
                    raise ValueError("Pending sells exceed strategy lot")
    return state


def atr70_signal(daily_rows, *, session_date):
    """Use only bars before session_date, excluding tested ATR from percentile."""
    _day(session_date)
    rows = []
    for raw in daily_rows:
        day = _day(raw["date"])
        if day >= session_date:
            continue
        row = {"date": day, **{key: _amount(raw[key], positive=True) for key in ("open", "high", "low", "close")}}
        if row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
            raise ValueError("Invalid OHLC bar")
        rows.append(row)
    if any(a["date"] >= b["date"] for a, b in zip(rows, rows[1:])):
        raise ValueError("History must be unique and chronological")
    tr_values, series, smoothed = [], [], None
    for previous, current in zip(rows, rows[1:]):
        tr = max(current["high"] - current["low"], abs(current["high"] - previous["close"]),
                 abs(current["low"] - previous["close"]))
        tr_values.append(tr)
        if len(tr_values) < 70:
            continue
        smoothed = sum(tr_values[-70:]) / 70 if smoothed is None else (smoothed * 69 + tr) / 70
        series.append(smoothed / current["close"] * 100)
    if len(series) < 253:
        return {"ready": False, "passed": False, "reason": "INSUFFICIENT_HISTORY"}
    ordered = sorted(series[-253:-1])
    location = Decimal(251) * Decimal("0.75")
    lower = int(location)
    cutoff = ordered[lower] + (ordered[lower + 1] - ordered[lower]) * (location - lower)
    return {"ready": True, "passed": series[-1] <= cutoff, "atr_pct": str(series[-1]),
            "cutoff_atr_pct": str(cutoff), "basis_date": rows[-1]["date"],
            "comparison_excludes_current": True}


def _validate_intent(intent):
    strategy = intent.get("strategy")
    _sleeve(strategy)
    if not isinstance(intent.get("intent_id"), str) or not intent["intent_id"]:
        raise ValueError("Intent identity required")
    if intent.get("ticker") != "SOXL" or intent.get("side") not in {"BUY", "SELL"}:
        raise ValueError("Invalid order ticker/side")
    if intent.get("order_type") not in {"MARKET", "MOO", "LOC", "MOC"}:
        raise ValueError("Invalid order type")
    _count(intent.get("quantity"), positive=True)
    _amount(intent.get("price"), positive=intent["side"] == "BUY" or intent["order_type"] == "LOC")
    _day(intent.get("session_date"))
    if not isinstance(intent.get("lot_id"), str) or not intent["lot_id"]:
        raise ValueError("Lot identity required")
    if intent["side"] == "BUY":
        _validate_rules(intent.get("frozen_rules"), strategy)
        if strategy == "ATR":
            if _day(intent.get("exit_session")) <= intent["session_date"]:
                raise ValueError("ATR next exit session required")
        else:
            _count(intent.get("round"), positive=True)
            if intent["round"] > len(intent["frozen_rules"]["round_weights"]):
                raise ValueError("R2 round outside entry allocation")


def _intent(strategy, side, quantity, price, order_type, session_date, lot_id, event, **extra):
    identity = _digest([STRATEGY_ID, "KMW2", strategy, side, session_date, lot_id, event])
    result = {"intent_id": identity, "strategy": strategy, "ticker": "SOXL", "side": side,
              "quantity": quantity, "price": str(price), "order_type": order_type,
              "session_date": session_date, "lot_id": lot_id, "position_key": lot_id,
              "round": 1, "event": event, **extra}
    _validate_intent(result)
    return result


def _pending(state, strategy=None):
    return [order for order in state["orders"].values() if order["status"] not in TERMINAL_STATUSES
            and (strategy is None or order["strategy"] == strategy)]


def available_cash(state, strategy):
    validate_state(state)
    reserve = sum((_amount(order["price"]) * (order["quantity"] - order["cumulative_quantity"]) *
                   (1 + _amount(state["fee_rate"])) for order in _pending(state, strategy)
                   if order["side"] == "BUY"), Decimal(0))
    return max(Decimal(0), _amount(state[_sleeve(strategy)]["cash"]) - reserve)


def register_order(state, intent, broker_order_id=None):
    validate_state(state)
    if state["phase"] != "ACTIVE":
        raise ValueError("WAITING_FLAT forbids new strategy orders")
    _validate_intent(intent)
    result = deepcopy(state)
    identity = intent["intent_id"]
    if identity in result["orders"]:
        original = result["orders"][identity]
        if any(original.get(key) != value for key, value in intent.items()):
            raise ValueError("Intent identity was reused with changed terms")
        return mark_order(result, identity, broker_order_id=broker_order_id,
                          status="ACCEPTED") if broker_order_id else result
    sleeve = _sleeve(intent["strategy"])
    if intent["side"] == "BUY":
        required = _amount(intent["price"]) * intent["quantity"] * (1 + _amount(state["fee_rate"]))
        if required > available_cash(state, intent["strategy"]):
            raise ValueError("Insufficient strategy cash including fee reserve")
        if intent["strategy"] == "ATR" and (any(lot["shares"] for lot in state["atr"]["lots"]) or
                any(order["side"] == "BUY" for order in _pending(state, "ATR")) or
                state["last_atr_entry_session"] == intent["session_date"]):
            raise ValueError("ATR entry already exists for session")
    else:
        lot = next((lot for lot in state[sleeve]["lots"] if lot["lot_id"] == intent["lot_id"]), None)
        reserved = sum(order["quantity"] - order["cumulative_quantity"] for order in _pending(state, intent["strategy"])
                       if order["side"] == "SELL" and order["lot_id"] == intent["lot_id"])
        if lot is None or intent["quantity"] > lot["shares"] - reserved:
            raise ValueError("Sell quantity exceeds unreserved strategy holding")
    result["orders"][identity] = {**deepcopy(intent), "status": "ACCEPTED" if broker_order_id else "PLANNED",
                                   "broker_order_id": str(broker_order_id) if broker_order_id else None,
                                   "cumulative_quantity": 0, "cumulative_notional": "0", "cumulative_fee": "0"}
    validate_state(result)
    return result


def mark_order(state, intent_id, broker_order_id=None, status="ACCEPTED"):
    validate_state(state)
    if status not in ORDER_STATUSES:
        raise ValueError("Invalid order status")
    result = deepcopy(state)
    order = result["orders"][intent_id]
    if order["status"] in TERMINAL_STATUSES and status != order["status"]:
        raise ValueError("Cannot reopen terminal order")
    if broker_order_id is not None:
        if not str(broker_order_id) or (order["broker_order_id"] is not None and order["broker_order_id"] != str(broker_order_id)):
            raise ValueError("Broker order identity mismatch")
        order["broker_order_id"] = str(broker_order_id)
    order["status"] = status
    validate_state(result)
    return result


def _reflected_pnl(pnl, rules):
    return pnl * _amount(rules["profit_reflection_rate" if pnl > 0 else "loss_reflection_rate"])


def _queue_r2_pnl(state, sale, adjustment, rules):
    """Aggregate partial fills and late costs into their original sale's P/L."""
    previous = _amount(sale.get("realized_profit_loss", "0"), signed=True)
    corrected = previous + adjustment
    sale["realized_profit_loss"] = str(corrected)
    if sale.get("pnl_settled"):
        change = _reflected_pnl(corrected, rules) - _reflected_pnl(previous, rules)
        state["r2"]["seed"] = str(max(Decimal(0), _amount(state["r2"]["seed"]) + change))
        return
    existing = next((item for item in state["r2"]["settlements"]
                     if item.get("sale_intent_id") == sale["intent_id"]), None)
    if existing is not None:
        existing["profit_loss"] = str(corrected)
        return
    settlement_day = (date.fromisoformat(sale["session_date"]) + timedelta(days=rules["settlement_delay_days"])).isoformat()
    state["r2"]["settlements"].append({"settlement_date": settlement_day, "profit_loss": str(corrected),
        "frozen_rules": deepcopy(rules), "sale_intent_id": sale["intent_id"]})


def reconcile_fills(state, fills):
    validate_state(state)
    result = deepcopy(state)
    for fill in fills:
        identity = fill.get("order_id", fill.get("intent_id"))
        if identity not in result["orders"]:
            raise ValueError("Unregistered fill")
        order = result["orders"][identity]
        broker_id = str(fill.get("broker_order_id") or "")
        if not broker_id or (order["broker_order_id"] is not None and broker_id != order["broker_order_id"]):
            raise ValueError("Fill broker identity mismatch")
        quantity = _count(fill["cumulative_quantity"])
        notional, fee = _amount(fill["cumulative_notional"]), _amount(fill["cumulative_fee"])
        previous = order["cumulative_quantity"]
        previous_notional, previous_fee = _amount(order["cumulative_notional"]), _amount(order["cumulative_fee"])
        fee_source = fill.get("fee_source", "BROKER")
        if fee_source not in {"BROKER", "CONFIGURED_RESERVE"}:
            raise ValueError("Unknown fill fee source")
        reserve_correction = (order.get("fee_source") == "CONFIGURED_RESERVE" and fee_source == "BROKER"
                              and quantity == previous and notional == previous_notional)
        if quantity < previous or quantity > order["quantity"] or notional < previous_notional or (fee < previous_fee and not reserve_correction):
            raise ValueError("Cumulative fill regressed or overfilled")
        delta, cash_delta, fee_delta = quantity - previous, notional - previous_notional, fee - previous_fee
        if (delta == 0 and cash_delta != 0) or (delta and cash_delta <= 0):
            raise ValueError("Invalid cumulative notional increment")
        if order["status"] in {"CANCELLED", "REJECTED"} and delta:
            raise ValueError("Terminal order received additional fill")
        sleeve = result[_sleeve(order["strategy"])]
        cash = _amount(sleeve["cash"])
        lot = next((lot for lot in sleeve["lots"] if lot["lot_id"] == order["lot_id"]), None)
        if order["side"] == "BUY":
            if cash_delta + fee_delta > cash:
                raise ValueError("Confirmed fill exceeds strategy cash")
            sleeve["cash"] = str(cash - cash_delta - fee_delta)
            if delta:
                if lot is None:
                    lot = {"lot_id": order["lot_id"], "strategy": order["strategy"], "shares": 0,
                           "entered_shares": 0,
                           "entry_date": order["session_date"], "entry_price": "1", "entry_fee": "0",
                           "frozen_rules": deepcopy(order["frozen_rules"]), "round": order["round"],
                           "entry_notional": "0", "remaining_cost": "0"}
                    if order["strategy"] == "ATR":
                        lot["exit_session"] = order["exit_session"]
                    sleeve["lots"].append(lot)
                if lot["frozen_rules"] != order["frozen_rules"] or lot["entry_date"] != order["session_date"]:
                    raise ValueError("Lot entry identity mismatch")
                lot["shares"] += delta
                lot["entered_shares"] += delta
                lot["entry_notional"] = str(_amount(lot["entry_notional"]) + cash_delta)
                lot["entry_price"] = str(_amount(lot["entry_notional"]) / lot["entered_shares"])
                lot["entry_fee"] = str(_amount(lot["entry_fee"]) + fee_delta)
                lot["remaining_cost"] = str(_amount(lot["remaining_cost"]) + cash_delta + fee_delta)
                if order["strategy"] == "ATR":
                    result["last_atr_entry_session"] = order["session_date"]
            elif fee_delta and lot is not None:
                lot["entry_fee"] = str(_amount(lot["entry_fee"]) + fee_delta)
                open_fee = fee_delta * lot["shares"] / lot["entered_shares"]
                lot["remaining_cost"] = str(_amount(lot["remaining_cost"]) + open_fee)
                if order["strategy"] == "R2":
                    for sale in result["orders"].values():
                        if sale["strategy"] != "R2" or sale["side"] != "SELL" or sale["lot_id"] != lot["lot_id"] or not sale["cumulative_quantity"]:
                            continue
                        adjustment = -fee_delta * sale["cumulative_quantity"] / lot["entered_shares"]
                        _queue_r2_pnl(result, sale, adjustment, lot["frozen_rules"])
        else:
            if delta:
                if lot is None or delta > lot["shares"]:
                    raise ValueError("Fill exceeds strategy lot")
                basis = _amount(lot["remaining_cost"]) * delta / lot["shares"]
                lot["shares"] -= delta
                lot["remaining_cost"] = str(_amount(lot["remaining_cost"]) - basis)
                if order["strategy"] == "R2":
                    _queue_r2_pnl(result, order, cash_delta - fee_delta - basis, lot["frozen_rules"])
            elif fee_delta and order["strategy"] == "R2" and lot is not None:
                _queue_r2_pnl(result, order, -fee_delta, lot["frozen_rules"])
            if cash + cash_delta - fee_delta < 0:
                raise ValueError("Invalid sell fee")
            sleeve["cash"] = str(cash + cash_delta - fee_delta)
        status = fill.get("status", "FILLED" if quantity == order["quantity"] else "OPEN")
        if status not in ORDER_STATUSES or (status == "FILLED" and quantity != order["quantity"]):
            raise ValueError("Fill status mismatch")
        if order["status"] in TERMINAL_STATUSES and status != order["status"]:
            raise ValueError("Terminal order status regressed")
        order.update(cumulative_quantity=quantity, cumulative_notional=str(notional), cumulative_fee=str(fee),
                     status=status, broker_order_id=broker_id)
        order["fee_source"] = fee_source
        if order["side"] == "BUY" and lot is not None:
            lot["entry_fee_source"] = fee_source
    validate_state(result)
    return result


def advance_r2_session(state, session_date):
    """Apply matured strategic P/L, then renew sizing seed on day 1/10/20..."""
    validate_state(state)
    _day(session_date)
    result = deepcopy(state)
    r2 = result["r2"]
    if state["phase"] != "ACTIVE" or r2["last_seed_session"] == session_date:
        return result
    if r2["last_seed_session"] is not None and session_date < r2["last_seed_session"]:
        raise ValueError("R2 sessions cannot regress")
    checkpoint = r2["last_seed_session"]
    current = date.fromisoformat(checkpoint) + timedelta(days=1) if checkpoint else date.fromisoformat(state["activated_session"])
    while current <= date.fromisoformat(session_date):
        if is_us_equity_trading_day(current):
            day = current.isoformat()
            remaining = []
            for settlement in r2["settlements"]:
                if settlement["settlement_date"] > day:
                    remaining.append(settlement)
                    continue
                pnl = _amount(settlement["profit_loss"], signed=True)
                r2["seed"] = str(max(Decimal(0), _amount(r2["seed"]) + _reflected_pnl(pnl, settlement["frozen_rules"])))
                if settlement.get("sale_intent_id"):
                    result["orders"][settlement["sale_intent_id"]]["pnl_settled"] = True
            r2["settlements"] = remaining
            r2["trading_days"] += 1
            if r2["trading_days"] == 1 or r2["trading_days"] % 10 == 0:
                r2["reference_seed"] = r2["seed"]
            r2["last_seed_session"] = day
        current += timedelta(days=1)
    validate_state(result)
    return result


def plan_monthly_rebalance(state, *, session_date, valuation_price, fee_rate=None):
    validate_state(state)
    _day(session_date)
    price = _amount(valuation_price, positive=True)
    rate = _amount(state["fee_rate"] if fee_rate is None else fee_rate)
    if state["phase"] != "ACTIVE" or rate >= 1:
        raise ValueError("Rebalance requires active state and valid fee")
    if any(lot["shares"] for lot in state["atr"]["lots"]) or _pending(state):
        raise ValueError("Rebalance waits for ATR exits and confirmed orders")
    atr_cash, r2_cash = _amount(state["atr"]["cash"]), _amount(state["r2"]["cash"])
    lots = [lot for lot in state["r2"]["lots"] if lot["shares"]]
    shares = sum(lot["shares"] for lot in lots)
    r2_nav = r2_cash + shares * price
    shortfall = max(Decimal(0), (r2_nav - atr_cash) / 2 - r2_cash)
    quantity = int((shortfall / (price * (1 - rate / 2))).to_integral_value(rounding=ROUND_CEILING)) if shortfall else 0
    allocated = [{"lot": lot, "quantity": quantity * lot["shares"] // shares,
                  "remainder": quantity * lot["shares"] % shares, "index": index}
                 for index, lot in enumerate(lots)] if quantity else []
    for item in sorted(allocated, key=lambda item: (-item["remainder"], item["index"]))[:quantity - sum(item["quantity"] for item in allocated)]:
        item["quantity"] += 1
    orders = [_intent("R2", "SELL", item["quantity"], price, "MARKET", session_date,
                      item["lot"]["lot_id"], "MONTHLY_REBALANCE", round=item["lot"]["round"])
              for item in allocated if item["quantity"]]
    plan = {"month": session_date[:7], "session_date": session_date, "valuation_price": str(price),
            "state_hash": _state_hash(state), "orders": orders,
            "basis": {"atr_cash": str(atr_cash), "r2_cash": str(r2_cash),
                      "r2_seed": state["r2"]["seed"], "r2_reference_seed": state["r2"]["reference_seed"],
                      "r2_shares": {lot["lot_id"]: lot["shares"] for lot in state["r2"]["lots"]}},
            "estimated_total_asset": str(atr_cash + r2_nav - quantity * price * rate)}
    plan["plan_id"] = _digest(plan)
    return plan


def settle_monthly_rebalance(state, plan, *, valuation_price=None):
    validate_state(state)
    if plan.get("plan_id") != _digest({key: value for key, value in plan.items() if key != "plan_id"}):
        raise ValueError("Corrupt rebalance plan")
    if plan["month"] in state["rebalance_months"]:
        return deepcopy(state)
    if any(lot["shares"] for lot in state["atr"]["lots"]) or _pending(state):
        raise ValueError("Rebalance waits for confirmed fills")
    for intent in plan["orders"]:
        order = state["orders"].get(intent["intent_id"])
        if order is None or order["status"] != "FILLED" or order["cumulative_quantity"] != intent["quantity"]:
            raise ValueError("Rebalance sale is not fully confirmed")
        if any(order.get(key) != value for key, value in intent.items()):
            raise ValueError("Rebalance confirmed order terms changed")
    if not plan["orders"] and plan["state_hash"] != _state_hash(state):
        raise ValueError("Stale cash transfer plan")
    basis = plan["basis"]
    expected_cash = _amount(basis["r2_cash"])
    expected_shares = deepcopy(basis["r2_shares"])
    for intent in plan["orders"]:
        order = state["orders"][intent["intent_id"]]
        expected_cash += _amount(order["cumulative_notional"]) - _amount(order["cumulative_fee"])
        expected_shares[order["lot_id"]] -= order["cumulative_quantity"]
    if (_amount(state["atr"]["cash"]) != _amount(basis["atr_cash"]) or
            _amount(state["r2"]["cash"]) != expected_cash or
            state["r2"]["seed"] != basis["r2_seed"] or state["r2"]["reference_seed"] != basis["r2_reference_seed"] or
            {lot["lot_id"]: lot["shares"] for lot in state["r2"]["lots"]} != expected_shares):
        raise ValueError("Rebalance accounting changed outside planned confirmed sales")
    result = deepcopy(state)
    price = _amount(plan["valuation_price"] if valuation_price is None else valuation_price, positive=True)
    atr_cash, r2_cash = _amount(state["atr"]["cash"]), _amount(state["r2"]["cash"])
    stock = sum(lot["shares"] for lot in state["r2"]["lots"]) * price
    target = (atr_cash + r2_cash + stock) / 2
    transfer = target - (r2_cash + stock)
    if atr_cash - transfer < 0 or r2_cash + transfer < 0:
        raise ValueError("Actual fills leave insufficient rebalance cash")
    result["atr"]["cash"], result["r2"]["cash"] = str(atr_cash - transfer), str(r2_cash + transfer)
    for key in ("seed", "reference_seed"):
        updated = _amount(result["r2"][key]) + transfer
        if updated < 0:
            raise ValueError("Rebalance would make R2 sizing seed negative")
        result["r2"][key] = str(updated)
    result["rebalance_months"].append(plan["month"])
    validate_state(result)
    return result


def build_runtime_plan(state, *, session_date, previous_session, next_session,
                       regular_open=None, last_price=None, daily_rows=None, r2_orders=None,
                       first_session_of_month=False, fee_rate=None, atr_exit_order_type="MOO"):
    validate_state(state)
    _day(session_date)
    if _day(previous_session) >= session_date or _day(next_session) <= session_date:
        raise ValueError("Invalid adjacent market sessions")
    if type(first_session_of_month) is not bool:
        raise ValueError("Month-boundary flag must be explicit")
    if atr_exit_order_type not in {"MOO", "MARKET"}:
        raise ValueError("ATR exit order must be MOO or overdue MARKET")
    base = {"preset": "KMW2", "strategy_id": STRATEGY_ID, "session_date": session_date,
            "phase": state["phase"], "orders": [], "rebalance": None}
    if state["phase"] != "ACTIVE":
        return base
    pending = _pending(state)
    if any(order["status"] in {"UNKNOWN", "SENDING"} for order in pending):
        return {**base, "phase": "UNKNOWN"}
    exit_event = "ATR_EXIT" if atr_exit_order_type == "MOO" else "ATR_OVERDUE_EXIT"
    exits = [_intent("ATR", "SELL", lot["shares"], 0, atr_exit_order_type, session_date, lot["lot_id"], exit_event)
             for lot in state["atr"]["lots"] if lot["shares"] and lot["exit_session"] <= session_date
             and not any(order["side"] == "SELL" and order["lot_id"] == lot["lot_id"] for order in pending)]
    if exits:
        unsent = [order for order in exits if order["intent_id"] not in state["orders"]]
        return {**base, "phase": "ATR_EXIT" if unsent else "EXIT_INCOMPLETE", "orders": unsent}
    if any(order["strategy"] == "ATR" and order["side"] == "SELL" for order in pending):
        return {**base, "phase": "WAITING_FILLS"}
    # A restart after the first session still has to finish the month's transfer
    # before either sleeve takes new risk.
    month_due = session_date[:7] not in state["rebalance_months"]
    if month_due:
        if pending:
            return {**base, "phase": "WAITING_FILLS"}
        if regular_open is None:
            return {**base, "phase": "WAITING_OPEN_REBALANCE"}
        plan = plan_monthly_rebalance(state, session_date=session_date, valuation_price=regular_open, fee_rate=fee_rate)
        return {**base, "phase": "REBALANCE", "orders": plan["orders"], "rebalance": plan}
    orders = []
    rate = _amount(state["fee_rate"] if fee_rate is None else fee_rate)
    if rate >= 1:
        raise ValueError("Invalid fee rate")
    if regular_open is not None and last_price is not None and daily_rows is not None:
        opening, last = _amount(regular_open, positive=True), _amount(last_price, positive=True)
        signal = atr70_signal(daily_rows, session_date=session_date)
        base["atr_signal"] = signal
        if signal.get("basis_date") != previous_session:
            signal.update(passed=False, reason="STALE_HISTORY")
        trigger = opening * Decimal("1.015")
        base["atr_trigger"] = str(trigger)
        attempted = any(order["strategy"] == "ATR" and order["side"] == "BUY" and order["session_date"] == session_date
                        for order in state["orders"].values())
        if signal.get("passed") and last >= trigger and not any(lot["shares"] for lot in state["atr"]["lots"]) and not attempted and state["last_atr_entry_session"] != session_date:
            reserve_price = last * (1 + MARKET_BUY_BUFFER)
            quantity = int((available_cash(state, "ATR") / (reserve_price * (1 + rate))).to_integral_value(rounding=ROUND_FLOOR))
            if quantity:
                orders.append(_intent("ATR", "BUY", quantity, reserve_price, "MARKET", session_date,
                    "ATR:" + session_date, "ATR_BREAKOUT", frozen_rules=deepcopy(ATR_RULES), exit_session=next_session,
                    signal_price=str(last), trigger_price=str(trigger)))
    r2_budget = available_cash(state, "R2")
    for index, raw in enumerate(r2_orders or []):
        if raw.get("ticker", "SOXL") != "SOXL":
            raise ValueError("R2 adapter returned another ticker")
        side = raw["side"]
        price = _amount(raw["price"], positive=side == "BUY" or raw["order_type"] == "LOC")
        quantity = _count(raw["quantity"], positive=True)
        rules = deepcopy(raw.get("frozen_rules", r2_rules(state["r2"]["mode"], True)))
        lot_id = raw.get("lot_id", raw.get("position_key"))
        if side == "BUY":
            quantity = min(quantity, int((r2_budget / (price * (1 + rate))).to_integral_value(rounding=ROUND_FLOOR)))
            if not quantity:
                continue
            r2_budget -= quantity * price * (1 + rate)
            lot_id = lot_id or f"R2:{session_date}:{index}"
        if not lot_id:
            raise ValueError("R2 sell requires lot identity")
        if side == "SELL":
            lot = next((lot for lot in state["r2"]["lots"] if lot["lot_id"] == lot_id), None)
            reserved = sum(order["quantity"] - order["cumulative_quantity"] for order in pending
                           if order["strategy"] == "R2" and order["side"] == "SELL" and order["lot_id"] == lot_id)
            quantity = min(quantity, max(0, lot["shares"] - reserved) if lot else 0)
            if not quantity:
                continue
        extra = {"round": raw.get("round", 1)}
        if side == "BUY":
            extra["frozen_rules"] = rules
        order = _intent("R2", side, quantity, price, raw["order_type"], session_date,
                        lot_id, "R2_ENTRY" if side == "BUY" else "R2_EXIT", **extra)
        if order["intent_id"] not in state["orders"]:
            orders.append(order)
    return {**base, "phase": "READY" if orders else "WATCHING", "orders": orders}
