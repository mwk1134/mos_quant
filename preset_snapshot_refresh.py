"""Advance a web-app preset using confirmed executions, never price-based fills.

This module is pure: it cannot query a broker, save GitHub data, submit orders,
or change another preset. The caller must obtain a complete broker history and
provide entry-time strategy rules from the original recommendation engine.
``fills`` contain cumulative executed quantity/average price for a direct order,
not its requested quantity or limit price. Pending recommendations are only
used to identify lots; they never create shares without a confirmed execution.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re

from kmw_order_plan import _kmw_config
from soxl_quant_system import SOXLQuantTrader
from us_market_calendar import is_us_equity_trading_day

_LOT = re.compile(r"^([1-9][0-9]*)_(\d{4}-\d{2}-\d{2})$")
_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STRATEGY_FIELDS = ("buy_threshold", "sell_threshold", "max_hold_days", "strategy_name")


class SnapshotRefreshBlocked(ValueError):
    """Safe refresh failure: no transport body or credential enters its message."""

    def __init__(self, message: str, *, code: str = "SNAPSHOT_REFRESH_BLOCKED"):
        super().__init__(message)
        self.code = code


def _blocked(message, code="INVALID_REFRESH_DATA"):
    raise SnapshotRefreshBlocked(message, code=code)


def _day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        _blocked("A refresh date is invalid.")


def _money(value, *, signed=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or (not signed and result < 0):
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation):
        _blocked("A refresh amount is invalid.")


def _quantity(value, *, positive=False):
    result = _money(value)
    if result != result.to_integral_value() or (positive and result <= 0):
        _blocked("An execution quantity is invalid.")
    return int(result)


def _position_items(snapshot):
    for key, position in snapshot.items():
        match = _LOT.fullmatch(key)
        if not match:
            continue
        if not isinstance(position, dict) or _quantity(position.get("round"), positive=True) != int(match[1]):
            _blocked("A saved lot is invalid.")
        _day(match[2])
        _quantity(position.get("shares"), positive=True)
        if _money(position.get("buy_price")) <= 0:
            _blocked("A saved entry price is invalid.")
        if position.get("mode") not in {"SF", "AG"}:
            _blocked("A saved lot has no confirmed strategy mode.")
        yield key, position


@dataclass
class _CompoundingState:
    """Minimal state for the original engine's pure settlement methods."""

    compound_seed: float
    compound_reference_seed: float
    profit_compounding_rate: float
    loss_compounding_rate: float
    compound_settlements: list
    trading_days_count: int = 0
    profit_loss_compounding_enabled: bool = True
    compounding_reference_renewal_days: int = 10
    _compound_processed_dates: set = field(default_factory=set)


def _compound_state(snapshot):
    settlements = []
    raw = snapshot.get("compound_settlements", [])
    if not isinstance(raw, list):
        _blocked("Saved compounding settlements are invalid.")
    for item in raw:
        if not isinstance(item, dict):
            _blocked("A saved compounding settlement is invalid.")
        trade_day, settlement_day = _day(item.get("trade_date")), _day(item.get("settlement_date"))
        if settlement_day < trade_day:
            _blocked("A compounding settlement precedes its trade.")
        settlements.append({"trade_date": datetime.combine(trade_day, datetime.min.time()),
                            "settlement_date": datetime.combine(settlement_day, datetime.min.time()),
                            "pnl": float(_money(item.get("pnl"), signed=True))})
    seed = _money(snapshot.get("compound_seed"))
    reference = _money(snapshot.get("compound_reference_seed"))
    if seed <= 0 or reference <= 0:
        _blocked("Saved compounding capital is missing.")
    profit_rate = _money(snapshot.get("compound_profit_rate", "0.70"))
    loss_rate = _money(snapshot.get("compound_loss_rate", "0.20"))
    if profit_rate > 1 or loss_rate > 1:
        _blocked("Saved compounding rates are invalid.")
    return _CompoundingState(float(seed), float(reference), float(profit_rate), float(loss_rate),
                            settlements, _quantity(snapshot.get("compound_trading_days", 0)))


def _rules(source):
    if not isinstance(source, dict) or source.get("mode") not in {"SF", "AG"}:
        _blocked("Confirmed buy entry rules are unavailable.", "BUY_RULES_MISSING")
    result = {"mode": source["mode"]}
    for name in ("buy_threshold", "sell_threshold"):
        if source.get(name) is not None:
            result[name] = float(_money(source[name], signed=True))
    if source.get("max_hold_days") is not None:
        result["max_hold_days"] = _quantity(source["max_hold_days"], positive=True)
    if source.get("strategy_name"):
        value = source["strategy_name"]
        if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
            _blocked("A buy strategy name is invalid.")
        result["strategy_name"] = value
    return result


def _same_rule(name, value, expected):
    if name in {"buy_threshold", "sell_threshold"}:
        return _money(value, signed=True) == _money(expected, signed=True)
    if name == "max_hold_days":
        return _quantity(value, positive=True) == _quantity(expected, positive=True)
    return value == expected


def _buy_key(event, snapshot, previous, buy_rules):
    day_text = event["trade_date"]
    pending = snapshot.get("pending_buy")
    pending = pending if isinstance(pending, dict) else {}
    supplied_key = event.get("position_key") or previous.get("position_key")
    if supplied_key is not None:
        match = _LOT.fullmatch(str(supplied_key))
        if not match or match[2] != day_text:
            _blocked("A buy execution does not match its saved lot.", "BUY_LOT_MISMATCH")
        round_num = int(match[1])
    else:
        if pending.get("order_date") != day_text:
            _blocked("A buy execution has no matching recommendation.", "BUY_LOT_MISMATCH")
        round_num = _quantity(pending.get("round"), positive=True)
        supplied_key = f"{round_num}_{day_text}"
    if event.get("round") is not None and _quantity(event["round"], positive=True) != round_num:
        _blocked("A buy execution has another recommendation round.", "BUY_LOT_MISMATCH")
    if pending.get("order_date") == day_text:
        if _quantity(pending.get("round"), positive=True) != round_num:
            _blocked("A buy execution has another pending round.", "BUY_LOT_MISMATCH")
        if event["quantity"] > _quantity(pending.get("quantity"), positive=True):
            _blocked("Confirmed buying exceeds its original recommendation.", "BUY_QUANTITY_MISMATCH")
    limit = previous.get("recommended_quantity")
    if limit is None and pending.get("order_date") == day_text:
        limit = pending.get("quantity")
    if limit is not None:
        limit = _quantity(limit, positive=True)
        if event["quantity"] > limit:
            _blocked("Confirmed buying exceeds its original recommendation.", "BUY_QUANTITY_MISMATCH")
        event["_recommended_quantity"] = limit
    existing = snapshot.get(supplied_key)
    if isinstance(existing, dict):
        rules = _rules(existing)
        if limit is not None and _quantity(existing["shares"]) + event["_delta_qty"] > limit:
            _blocked("Buying the same recommended lot exceeds its quantity.", "BUY_QUANTITY_MISMATCH")
    else:
        rules_source = (buy_rules or {}).get(day_text)
        if not rules_source and event.get("mode"):
            rules_source = event
        if not rules_source and pending.get("mode"):
            rules_source = pending
        rules = _rules(rules_source)
    if event.get("mode") and event["mode"] != rules["mode"]:
        _blocked("A buy execution has another entry strategy.", "BUY_RULES_MISMATCH")
    for name in _STRATEGY_FIELDS:
        if event.get(name) is not None and name in rules and not _same_rule(name, event[name], rules[name]):
            _blocked("A buy execution has different entry rules.", "BUY_RULES_MISMATCH")
    if pending.get("mode") and pending["mode"] != rules["mode"]:
        _blocked("A pending recommendation has different entry rules.", "BUY_RULES_MISMATCH")
    for name in _STRATEGY_FIELDS:
        if pending.get(name) is not None and name in rules and not _same_rule(name, pending[name], rules[name]):
            _blocked("A pending recommendation has different entry rules.", "BUY_RULES_MISMATCH")
    return supplied_key, round_num, rules


def _sell_key(event, snapshot, previous, delta_quantity):
    key = event.get("position_key") or previous.get("position_key")
    if key is None:
        # Exact unique lot matching is possible for an old manually placed order.
        # Ambiguous multi-lot sales never fall back to invented FIFO allocation.
        candidates = [k for k, p in _position_items(snapshot)
                      if _day(k.split("_", 1)[1]) < _day(event["trade_date"])
                      and _quantity(p["shares"]) == delta_quantity]
        if len(candidates) != 1:
            _blocked("A sell execution cannot be assigned to one confirmed lot.", "SELL_LOT_MISMATCH")
        key = candidates[0]
    position = snapshot.get(key)
    if not isinstance(key, str) or not _LOT.fullmatch(key) or not isinstance(position, dict):
        _blocked("A sold lot is not present in this preset.", "SELL_LOT_MISMATCH")
    if _day(key.split("_", 1)[1]) >= _day(event["trade_date"]):
        _blocked("A sale precedes its entry.", "SELL_LOT_MISMATCH")
    if delta_quantity > _quantity(position["shares"]):
        _blocked("A sell execution exceeds its confirmed lot.", "SELL_QUANTITY_MISMATCH")
    return key


def refresh_preset_snapshot(all_snapshots: dict, *, preset: str, basis_date: str,
                            fills: list, broker_quantity: int, unresolved_orders=(),
                            buy_rules: dict | None = None, broker_cash=None) -> dict:
    """Return an isolated full-document refresh after confirmed-account checks.

    ``fills`` rows: ``order_id``, ``trade_date``, ``side``, cumulative confirmed
    ``quantity`` and average ``price``; optional cumulative ``notional`` and ``fees`` (default 0),
    ``position_key``, ``round``, and frozen entry rule fields. The transport adapter
    must fully paginate the history from the persisted baseline through basis.
    A claimed/accepted/open order is not a fill. If fees are unavailable, cash
    remains the original strategy budget, not a claim of exact broker cash.
    ``broker_cash`` may only be confirmed account cash and is a comparison;
    an order estimate must never replace the strategy's persisted cash.
    """
    if unresolved_orders:
        _blocked("Broker orders must be resolved before refreshing.", "UNRESOLVED_ORDER")
    if not isinstance(all_snapshots, dict) or not isinstance(preset, str):
        _blocked("A preset snapshot document is required.")
    document = deepcopy(all_snapshots)
    snapshot = document.get(preset)
    if not isinstance(snapshot, dict) or not snapshot:
        _blocked("The selected preset has no confirmed snapshot.")
    try:
        config = _kmw_config(document, snapshot, preset=preset)
    except (ValueError, KeyError, TypeError):
        _blocked("The selected preset configuration is invalid.")
    basis = _day(basis_date)
    old_day = _day(snapshot.get("as_of_date"))
    if not is_us_equity_trading_day(basis) or basis < old_day:
        _blocked("Refresh must use the latest completed trading session.", "INVALID_BASIS_DATE")
    if (basis - old_day).days > 3660:
        _blocked("The snapshot refresh range is too large.")
    for key, _ in _position_items(snapshot):
        if _day(key.split("_", 1)[1]) > old_day:
            _blocked("A saved holding is after its snapshot checkpoint.")
    cash = _money(snapshot.get("available_cash"))
    processed = snapshot.get("processed_seed_dates")
    if not isinstance(processed, list):
        _blocked("The snapshot has no seed-processing checkpoint.")
    processed = {_day(d).isoformat() for d in processed}
    seeds = {}
    for entry in config["seed_increases"]:
        seed_day = _day(entry["date"])
        seeds[seed_day] = seeds.get(seed_day, Decimal(0)) + _money(entry["amount"])
        if seed_day <= old_day and seed_day.isoformat() not in processed:
            _blocked("The baseline has an unaccounted seed deposit.", "SEED_CHECKPOINT_MISMATCH")
    compound = _compound_state(snapshot)
    sync = snapshot.get("broker_sync", {})
    if not isinstance(sync, dict):
        _blocked("The broker reconciliation checkpoint is invalid.")
    baseline = _day(sync.get("baseline_date", old_day.isoformat()))
    if baseline > old_day:
        _blocked("The broker baseline exceeds the snapshot checkpoint.")
    records = deepcopy(sync.get("executions", {}))
    if not isinstance(records, dict):
        _blocked("The saved execution checkpoint is invalid.")
    if not isinstance(fills, list):
        _blocked("A complete confirmed execution list is required.")
    events, seen = [], set()
    for raw in fills:
        if not isinstance(raw, dict) or not isinstance(raw.get("order_id"), str) or not _ORDER_ID.fullmatch(raw["order_id"]):
            _blocked("A confirmed execution identifier is invalid.")
        trade_day = _day(raw.get("trade_date"))
        if raw.get("side") not in {"BUY", "SELL"} or not is_us_equity_trading_day(trade_day):
            _blocked("A confirmed execution side or date is invalid.")
        qty = _quantity(raw.get("quantity"))
        if trade_day > basis or qty == 0:
            continue
        identity = f"{trade_day.isoformat()}:{raw['order_id']}"
        if identity in seen:
            _blocked("The broker returned a duplicate execution record.", "DUPLICATE_FILL")
        seen.add(identity)
        if trade_day <= baseline and identity not in records:
            continue  # Initial snapshot already includes the whole baseline day.
        prior = records.get(identity, {})
        if not isinstance(prior, dict):
            _blocked("A saved execution record is invalid.")
        if prior and (prior.get("side") != raw["side"] or prior.get("trade_date") != raw["trade_date"]):
            _blocked("A confirmed execution changed identity.", "FILL_CHANGED")
        price = _money(raw.get("price"))
        if price <= 0:
            _blocked("A confirmed execution price is missing.")
        gross, fees = price * qty, _money(raw.get("fees", 0))
        if raw.get("notional") is not None:
            confirmed_gross = _money(raw["notional"])
            if abs(confirmed_gross - gross) > Decimal("0.01"):
                _blocked("Confirmed execution notional disagrees with its average price.", "FILL_AMOUNT_MISMATCH")
            gross = confirmed_gross
        previous_qty = _quantity(prior.get("quantity", 0))
        previous_gross, previous_fees = _money(prior.get("notional", 0)), _money(prior.get("fees", 0))
        if qty < previous_qty or gross < previous_gross or fees < previous_fees:
            _blocked("A confirmed execution regressed.", "FILL_CHANGED")
        delta_qty, delta_gross, delta_fees = qty - previous_qty, gross - previous_gross, fees - previous_fees
        if delta_qty == 0 and delta_gross != 0:
            _blocked("An execution price was revised; reconcile it first.", "FILL_CHANGED")
        if delta_qty == 0 and delta_fees == 0:
            continue
        if trade_day < old_day:
            _blocked("A past execution changed after its checkpoint.", "LATE_EXECUTION")
        event = {**raw, "quantity": qty, "_identity": identity, "_day": trade_day,
                 "_delta_qty": delta_qty, "_delta_gross": delta_gross, "_delta_fees": delta_fees,
                 "_gross": gross, "_fees": fees, "_previous": prior}
        events.append(event)
    events.sort(key=lambda e: (e["_day"], e["side"] != "SELL", e["order_id"]))
    by_day = {}
    for event in events:
        by_day.setdefault(event["_day"], []).append(event)
    day = old_day if old_day in by_day else old_day + timedelta(days=1)
    while day <= basis:
        if is_us_equity_trading_day(day) and day > old_day:
            for seed_day, amount in sorted(seeds.items()):
                if seed_day <= day and seed_day.isoformat() not in processed:
                    cash += amount
                    SOXLQuantTrader._add_compounding_seed(compound, float(amount))
                    processed.add(seed_day.isoformat())
            compound.trading_days_count += 1
            SOXLQuantTrader._process_compounding_for_date(compound, datetime.combine(day, datetime.min.time()))
        for event in by_day.get(day, []):
            delta_qty, gross, fees = event["_delta_qty"], event["_delta_gross"], event["_delta_fees"]
            previous = event["_previous"]
            if delta_qty == 0:
                # A final fee publication changes budget only, never shares.
                cash -= fees
                key = previous["position_key"]
                if event["side"] == "SELL" and fees:
                    compound.compound_settlements.append({"trade_date": datetime.combine(day, datetime.min.time()),
                        "settlement_date": datetime.combine(day + timedelta(days=7), datetime.min.time()), "pnl": -float(fees)})
            elif event["side"] == "BUY":
                key, round_num, rules = _buy_key(event, snapshot, previous, buy_rules)
                position = snapshot.get(key, {})
                prior_shares = _quantity(position.get("shares", 0))
                prior_cost = _money(position.get("buy_price", 0)) * prior_shares
                shares, cost = prior_shares + delta_qty, prior_cost + gross
                snapshot[key] = {**position, **rules, "round": round_num, "shares": shares,
                                 "buy_price": float(cost / shares), "amount": float(cost)}
                cash -= gross + fees
            else:
                key = _sell_key(event, snapshot, previous, delta_qty)
                position = snapshot[key]
                cost = _money(position["buy_price"]) * delta_qty
                remaining = _quantity(position["shares"]) - delta_qty
                if remaining:
                    position["shares"] = remaining
                    position["amount"] = float(_money(position["buy_price"]) * remaining)
                else:
                    del snapshot[key]
                cash += gross - fees
                compound.compound_settlements.append({"trade_date": datetime.combine(day, datetime.min.time()),
                    "settlement_date": datetime.combine(day + timedelta(days=7), datetime.min.time()),
                    "pnl": float(gross - fees - cost)})
            records[event["_identity"]] = {"trade_date": event["trade_date"], "side": event["side"],
                "quantity": event["quantity"], "notional": format(event["_gross"], "f"),
                "fees": format(event["_fees"], "f"), "position_key": key}
            limit = event.get("_recommended_quantity", previous.get("recommended_quantity"))
            if limit is not None:
                records[event["_identity"]]["recommended_quantity"] = _quantity(limit, positive=True)
        day += timedelta(days=1)
    if cash < 0:
        _blocked("Confirmed executions exceed the saved strategy cash.", "CASH_MISMATCH")
    if sum(_quantity(p["shares"]) for _, p in _position_items(snapshot)) != _quantity(broker_quantity):
        _blocked("Refreshed lots do not match actual broker holdings.", "HOLDINGS_MISMATCH")
    if broker_cash is not None and abs(cash - _money(broker_cash)) > Decimal("0.01"):
        _blocked("Strategy cash and confirmed broker cash differ.", "CASH_MISMATCH")
    pending = snapshot.get("pending_buy")
    if isinstance(pending, dict) and _day(pending.get("order_date")) <= basis:
        snapshot.pop("pending_buy", None)
    snapshot.update({"as_of_date": basis.isoformat(), "available_cash": float(cash),
        "processed_seed_dates": sorted(processed), "compound_seed": compound.compound_seed,
        "compound_reference_seed": compound.compound_reference_seed,
        "compound_trading_days": compound.trading_days_count,
        "compound_settlements": [{"trade_date": s["trade_date"].strftime("%Y-%m-%d"),
            "settlement_date": s["settlement_date"].strftime("%Y-%m-%d"), "pnl": float(s["pnl"])}
            for s in compound.compound_settlements],
        "broker_sync": {"version": 1, "baseline_date": baseline.isoformat(), "executions": records}})
    if not any(True for _ in _position_items(snapshot)):
        snapshot["cash_snapshot_date"] = basis.isoformat()
    return document
