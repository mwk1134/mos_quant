"""Build KMW reservation orders from the web app's persisted portfolio.

This module never submits orders or writes snapshots.  It uses the strategy
engine, but disables its price-history-based simulated fills: a broker holding
must survive until an actual execution has been reconciled.  ``pending_buy``
is display metadata, and is never treated as a holding or an executable order.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any
from zoneinfo import ZoneInfo

from soxl_quant_system import SOXLQuantTrader, recommendation_buy_order_terms
from us_market_calendar import is_us_equity_trading_day


ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
_POSITION_KEY = re.compile(r"^([1-9][0-9]*)_(\d{4}-\d{2}-\d{2})$")
_CENT = Decimal("0.01")
DEFAULT_KMW_CONFIG = {
    "initial_capital": 9000.0,
    "session_start_date": "2025-08-27",
    "seed_increases": [
        {"date": "2025-10-21", "amount": 31000.0},
        {"date": "2026-06-21", "amount": 11293.0},
    ],
    "position_edits": {},
    "ma_alignment_switch_enabled": False,
}


def _number(value: Any, field: str, *, minimum: Decimal = Decimal("0")) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number < minimum:
            raise ValueError
        return number
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def _date(value: Any, field: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}; expected YYYY-MM-DD") from exc


def _quantity(value: Any, field: str) -> int:
    number = _number(value, field, minimum=Decimal("1"))
    if number != number.to_integral_value():
        raise ValueError(f"Invalid {field}; whole shares are required")
    return int(number)


def order_price(value: Any) -> str:
    """Return a finite, positive LOC price as an exact two-decimal string."""
    price = _number(value, "order price", minimum=Decimal("0.00000001"))
    rounded = price.quantize(_CENT, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        raise ValueError("Order price rounds to zero")
    return format(rounded, ".2f")


def previous_trading_date(day: date) -> date:
    previous = day - timedelta(days=1)
    while not is_us_equity_trading_day(previous):
        previous -= timedelta(days=1)
    return previous


def next_trading_date(day: date) -> date:
    following = day + timedelta(days=1)
    while not is_us_equity_trading_day(following):
        following += timedelta(days=1)
    return following


def completed_trading_date(et_now: datetime) -> date:
    """Match the web app's latest completed session at its 16:00 ET cutoff."""
    if (
        is_us_equity_trading_day(et_now.date())
        and et_now.time().replace(tzinfo=None) >= time(16)
    ):
        return et_now.date()
    return previous_trading_date(et_now.date())


class _KMWPlanTrader(SOXLQuantTrader):
    """Keep factual quantities/cash while reusing the app's recommendation."""

    def __init__(self, *, now: datetime, expected_basis_date: date, **kwargs: Any):
        super().__init__(auto_update_rsi=False, **kwargs)
        self._plan_now = now.astimezone(ET).replace(tzinfo=None)
        self._expected_basis_date = expected_basis_date

    def get_us_eastern_now(self) -> datetime:
        return self._plan_now

    def get_stock_data(self, ticker: str, *args: Any, **kwargs: Any):
        data = super().get_stock_data(ticker, *args, **kwargs)
        if data is None or data.empty:
            raise ValueError(f"{ticker} market data is unavailable")
        # Include the same-day candle after the regular close, but never let a
        # provider's partial order-day candle enter a recommendation.
        data = data[data.index.date <= self._expected_basis_date].copy()
        if data.empty or data.index[-1].date() != self._expected_basis_date:
            last = data.index[-1].date().isoformat() if not data.empty else "none"
            raise ValueError(
                f"Stale {ticker} market data: {last}; "
                f"expected {self._expected_basis_date.isoformat()}"
            )
        return data

    def reconcile_positions_with_close_history(self, soxl_data):
        # A past close is not evidence of an actual broker execution.
        return None

    def _prune_positions_failing_loc_verification(self, soxl_data):
        # Factual buys may differ from a backtest's historical LOC condition.
        return None


def _kmw_config(all_snapshots: dict, snapshot: dict) -> dict:
    config = deepcopy(DEFAULT_KMW_CONFIG)
    persisted = all_snapshots.get("_preset_configs", {})
    if not isinstance(persisted, dict):
        raise ValueError("_preset_configs must be an object")
    persisted = persisted.get("KMW", {})
    if not isinstance(persisted, dict):
        raise ValueError("_preset_configs.KMW must be an object")
    for field in config:
        if field in persisted:
            config[field] = deepcopy(persisted[field])
    if "ma_alignment_switch_enabled" not in persisted:
        config["ma_alignment_switch_enabled"] = bool(
            snapshot.get("ma_alignment_switch_enabled", False)
        )
    _number(config["initial_capital"], "initial_capital", minimum=Decimal("0.01"))
    _date(config["session_start_date"], "session_start_date")
    if not isinstance(config["seed_increases"], list):
        raise ValueError("KMW seed_increases must be a list")
    for seed in config["seed_increases"]:
        if not isinstance(seed, dict):
            raise ValueError("Each KMW seed increase must be an object")
        _date(seed.get("date"), "seed increase date")
        _number(seed.get("amount"), "seed increase amount")
    # app.py's ensure_preset_seed_increase enforces this entry after loading.
    mandatory_seed = {"date": "2026-06-21", "amount": 11293.0}
    if not any(
        seed["date"] == mandatory_seed["date"]
        and Decimal(str(seed["amount"])) == Decimal("11293")
        for seed in config["seed_increases"]
    ):
        config["seed_increases"].append(mandatory_seed)
    if not isinstance(config["position_edits"], dict):
        raise ValueError("KMW position_edits must be an object")
    return config


def build_kmw_plan(
    all_snapshots: dict,
    now: datetime | None = None,
    *,
    allow_stale_snapshot: bool = False,
) -> dict:
    """Return the upcoming KMW plan, or raise for stale/incomplete holdings.

    ``all_snapshots`` has the same shape as ``data/positions_snapshots.json``.
    ``now`` must carry a timezone; omitted means the current instant.  Like the
    web app, recommendations use the latest completed regular session (16:00
    ET cutoff) and are for the next trading day.  They remain visible during
    the session, after close, and on weekends/holidays.  Submission timing is
    checked separately by the order engine.
    ``allow_stale_snapshot`` permits a preview using older factual holdings;
    the returned ``snapshot_fresh=False`` must prohibit broker submission.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    et_now = now.astimezone(ET)
    basis_day = completed_trading_date(et_now)
    order_day = next_trading_date(basis_day)
    plan = {
        "preset": "KMW",
        "status": "skipped",
        "generated_at": now.isoformat(),
        "order_date": order_day.isoformat(),
        "basis_date": basis_day.isoformat(),
        "expected_basis_date": basis_day.isoformat(),
        "orders": [],
        "warnings": [],
    }
    if not isinstance(all_snapshots, dict):
        raise ValueError("Snapshot document must be an object")
    snapshot = deepcopy(all_snapshots.get("KMW"))
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("A nonempty KMW portfolio snapshot is required; simulation is disabled")
    snapshot_day = _date(snapshot.get("as_of_date"), "KMW as_of_date")
    plan["snapshot_date"] = snapshot_day.isoformat()
    plan["snapshot_fresh"] = snapshot_day == basis_day
    if snapshot_day > basis_day:
        raise ValueError("KMW snapshot is after the completed trading day")
    if snapshot_day != basis_day:
        stale_message = (
            f"Stale KMW snapshot: {snapshot_day.isoformat()}; "
            f"expected {basis_day.isoformat()}. Refresh and reconcile actual holdings first."
        )
        if not allow_stale_snapshot:
            raise ValueError(stale_message)
        plan["warnings"].append(stale_message + " Preview only; submission is prohibited.")
    cash = _number(snapshot.get("available_cash"), "KMW available_cash")
    for field in ("compound_seed", "compound_reference_seed"):
        _number(snapshot.get(field), f"KMW {field}", minimum=Decimal("0.01"))
    processed = snapshot.get("processed_seed_dates")
    if not isinstance(processed, list):
        raise ValueError("KMW processed_seed_dates is required")
    for processed_day in processed:
        _date(processed_day, "processed seed date")
    config = _kmw_config(all_snapshots, snapshot)
    if _date(config["session_start_date"], "session_start_date") > basis_day:
        raise ValueError("KMW session_start_date is after the completed trading day")
    for seed in config["seed_increases"]:
        if _date(seed["date"], "seed increase date") <= basis_day and seed["date"] not in processed:
            raise ValueError(f"Snapshot has not accounted for seed increase {seed['date']}")
    position_keys = []
    for key, saved in snapshot.items():
        match = _POSITION_KEY.fullmatch(key)
        if match is None:
            continue
        if not isinstance(saved, dict):
            raise ValueError(f"Invalid KMW holding {key}")
        buy_day = _date(match.group(2), f"{key} buy date")
        if buy_day > basis_day or not is_us_equity_trading_day(buy_day):
            raise ValueError(f"Invalid KMW holding date: {key}")
        if _quantity(saved.get("round"), f"{key} round") != int(match.group(1)):
            raise ValueError(f"Position key and stored round disagree: {key}")
        _quantity(saved.get("shares"), f"{key} shares")
        _number(saved.get("buy_price"), f"{key} buy_price", minimum=Decimal("0.01"))
        _number(saved.get("amount", Decimal(str(saved["shares"])) * Decimal(str(saved["buy_price"]))), f"{key} amount")
        if saved.get("mode") not in ("SF", "AG"):
            raise ValueError(f"KMW holding {key} requires its saved SF/AG entry mode")
        position_keys.append(key)
    trader = _KMWPlanTrader(
        now=now,
        expected_basis_date=basis_day,
        initial_capital=float(config["initial_capital"]),
    )
    # Same configuration as app.py configure_app_trader, without importing UI.
    trader.set_profit_loss_compounding(
        enabled=True, profit_rate=0.70, loss_rate=0.20,
        settlement_delay_days=7, renewal_days=10,
    )
    trader.set_ma_alignment_switch(bool(config["ma_alignment_switch_enabled"]))
    trader.set_seed_increases(config["seed_increases"])
    trader.session_start_date = config["session_start_date"]
    trader.positions, _, _ = trader._snapshot_to_positions_and_state(snapshot)
    trader.available_cash = float(cash)
    trader.processed_seed_dates = set(processed)
    trader.current_round = len(trader.positions) + 1
    trader._restore_compounding_from_snapshot(snapshot)
    for position in trader.positions:
        key = f"{position['round']}_{position['buy_date']:%Y-%m-%d}"
        position["_snapshot_key"] = key
        edits = config["position_edits"].get(key)
        if edits:
            qty = _quantity(edits.get("shares"), f"{key} edited shares")
            price = float(_number(edits.get("buy_price"), f"{key} edited buy_price", minimum=Decimal("0.01")))
            if not trader.update_position(trader.positions.index(position), qty, price):
                raise ValueError(f"KMW position edit cannot be applied: {key}")
    if len(trader.positions) != len(position_keys):
        raise ValueError("Some KMW holding rows could not be restored")
    expected_positions = [(p["_snapshot_key"], p["shares"], p["buy_price"]) for p in trader.positions]
    recommendation = trader.get_daily_recommendation(skip_simulate=True, preserve_snapshot_shares=True)
    if "error" in recommendation:
        raise ValueError(f"KMW recommendation failed: {recommendation['error']}")
    if [(p["_snapshot_key"], p["shares"], p["buy_price"]) for p in trader.positions] != expected_positions:
        raise ValueError("Recommendation altered factual KMW holdings")
    if recommendation.get("basis_date") != basis_day.isoformat():
        raise ValueError(f"Stale recommendation basis: {recommendation.get('basis_date')}")
    if recommendation.get("can_buy") and recommendation.get("buy_order_date") != order_day.isoformat():
        raise ValueError(f"Recommendation is for another order date: {recommendation.get('buy_order_date')}")
    plan.update({
        "status": "ready",
        "mode": str(recommendation["mode"]),
        "strategy_name": str(recommendation.get("strategy_name") or "기본"),
        "available_cash": float(trader.available_cash),
        "buy_budget": float(recommendation.get("next_buy_amount", 0)),
        "snapshot_positions": [],
    })
    # Place sell rows for every actual holding.  The UI's list hides historical
    # target hits, whereas actual broker holdings still need their sell order.
    order_midnight = datetime.combine(order_day, time.min)
    for position in trader.positions:
        position_config = trader.get_position_config(position)
        expiry = trader.get_trading_date_after(position["buy_date"], position_config["max_hold_days"])
        expired = order_midnight >= expiry
        qty = _quantity(position["shares"], "sell quantity")
        entry = {
            "round": int(position["round"]),
            "position_key": position["_snapshot_key"],
            "quantity": qty,
            "buy_date": position["buy_date"].date().isoformat(),
            "buy_price": order_price(position["buy_price"]),
            "mode": str(position["mode"]),
            "expiry_date": expiry.date().isoformat(),
        }
        plan["snapshot_positions"].append(entry)
        plan["orders"].append({
            "side": "SELL", "ticker": "SOXL", "quantity": qty,
            "price": "0" if expired else order_price(trader.calculate_position_sell_price(position)),
            "order_type": "MOC" if expired else "LOC",
            "round": int(position["round"]), "position_key": position["_snapshot_key"],
            "reason": "holding_period_expired" if expired else "target_price",
            "expiry_date": expiry.date().isoformat(),
        })
    if recommendation.get("can_buy"):
        terms = recommendation_buy_order_terms(
            recommendation["buy_price"], recommendation["next_buy_amount"]
        )
        qty = terms["quantity"]
        if qty > 0:
            buy_round = _quantity(recommendation["next_buy_round"], "buy round")
            plan["orders"].append({
                "side": "BUY", "ticker": "SOXL", "quantity": qty,
                "price": terms["price"], "order_type": "LOC", "round": buy_round,
                "position_key": f"{buy_round}_{order_day.isoformat()}",
                "reason": "daily_recommendation",
            })
        else:
            plan["warnings"].append("Recommended buy budget cannot buy one whole share")
    return plan
