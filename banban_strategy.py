"""Pure historical replay for the KMW3 반반매매 preset.

``replay({'SOXL': soxl_frame, 'QQQ': qqq_frame}, config, end_date='2026-11-02')``
accepts OHLC DataFrames or an explicit ``{'bars': {'SOXL': [...], 'QQQ': [...]}}``
snapshot. Inputs must include continuous exchange sessions and sufficient ATR
history. All returned objects are JSON serializable. This module neither fetches
prices nor accesses accounts, submits orders, or persists state.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import copy, deepcopy
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import io
import json
import math

import pandas as pd

from banban_research.atr_breakout import default_atr_breakout_settings
from banban_research.atr_calculation import build_filter_by_session
from banban_research.atr_r2_backtest import AtrR2MonthlyBacktester
from banban_research.atr_sessions import exchange_calendar
from banban_research.mode_manager import get_mode_config
from banban_research.snapshot_provider import SnapshotProvider, normalize_bars

STRATEGY_ID = "atr70_r2_mix"
STRATEGY_NAME = "반반매매"
PRESET = "KMW3"
DEFAULT_KMW3_CONFIG = {
    "initial_capital": 80000.0,
    "session_start_date": "2026-10-20",
    "seed_increases": [],
    "position_edits": {},
    "ma_alignment_switch_enabled": False,
    "strategy_id": STRATEGY_ID,
    "strategy_name": STRATEGY_NAME,
    "execution_mode": "simulation",
    "commission_rate": 0.0005,
}


def _iso(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("ISO date required")
    return value


def normalize_config(config=None):
    result = deepcopy(DEFAULT_KMW3_CONFIG)
    if config is not None:
        if not isinstance(config, dict):
            raise ValueError("Preset configuration must be an object")
        result.update(deepcopy(config))
    _iso(result["session_start_date"])
    for key in ("initial_capital", "commission_rate"):
        if isinstance(result[key], bool):
            raise ValueError("Invalid " + key)
        result[key] = float(result[key])
        if not math.isfinite(result[key]):
            raise ValueError("Invalid " + key)
    if result["initial_capital"] <= 0 or not 0 <= result["commission_rate"] <= .05:
        raise ValueError("Invalid capital or commission rate")
    if result.get("seed_increases") or result.get("position_edits"):
        raise ValueError("반반매매 replay requires its original capital and strategy-owned holdings")
    if result.get("strategy_id") != STRATEGY_ID or result.get("execution_mode") != "simulation":
        raise ValueError("KMW3 is a simulation preset for 반반매매")
    result.update(strategy_name=STRATEGY_NAME, ma_alignment_switch_enabled=False)
    return result


def _settings():
    return {**default_atr_breakout_settings(), "atr_method": "WILDER", "atr_period": 70,
            "offset_pct": 1.5, "lookback_days": 252, "max_percentile": 75,
            "allocation_pct": 100}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _rows(value):
    if isinstance(value, pd.DataFrame):
        columns = {str(column).lower(): column for column in value.columns}
        if not {"open", "high", "low", "close"} <= set(columns):
            raise ValueError("OHLC DataFrame required")
        return [{"date": pd.Timestamp(stamp).date().isoformat(),
                 **{key: float(row[columns[key]]) for key in ("open", "high", "low", "close")},
                 "volume": float(row[columns["volume"]]) if "volume" in columns else 0.0}
                for stamp, row in value.iterrows()]
    return deepcopy(value)


def _market_snapshot(market, end_date):
    if not isinstance(market, dict):
        raise ValueError("Explicit SOXL and QQQ histories are required")
    bars = market.get("bars", market)
    if not isinstance(bars, dict) or set(bars) != {"SOXL", "QQQ"}:
        raise ValueError("Only SOXL and QQQ histories are accepted")
    # Cut before validation and indicator calculation: later candles cannot
    # influence weekly modes, ATR percentiles, holdings, or final valuations.
    normalized = {}
    for symbol in ("SOXL", "QQQ"):
        rows = _rows(bars[symbol])
        if end_date is not None:
            rows = [row for row in rows if str(row.get("date", "")) <= end_date]
        normalized[symbol] = normalize_bars(rows)
    first = max(rows[0]["date"] for rows in normalized.values())
    ends = {rows[-1]["date"] for rows in normalized.values()}
    if len(ends) != 1:
        raise ValueError("SOXL and QQQ completed endpoints differ")
    end = ends.pop()
    calendar = exchange_calendar()
    if end_date is not None:
        expected_end = calendar.date_to_session(end_date, direction="previous").strftime("%Y-%m-%d")
        if end != expected_end:
            raise ValueError("Requested completed market session is missing")
    expected = calendar.sessions_in_range(first, end).strftime("%Y-%m-%d").tolist()
    for symbol, rows in normalized.items():
        normalized[symbol] = [row for row in rows if row["date"] >= first]
        if [row["date"] for row in normalized[symbol]] != expected:
            raise ValueError(symbol + " has missing, duplicate, or non-session daily bars")
    return {"bars": normalized}, end


def initial_snapshot(config=None):
    """Return a labeled uninvested simulation seed, never a confirmed account."""
    config = normalize_config(config)
    capital = config["initial_capital"]
    previous = exchange_calendar().date_to_session(config["session_start_date"], direction="next")
    previous = exchange_calendar().previous_session(previous).strftime("%Y-%m-%d")
    return {"preset": PRESET, "strategy_id": STRATEGY_ID, "strategy_name": STRATEGY_NAME,
            "simulation": True, "execution_mode": "simulation", "available_cash": capital,
            "initial_capital": capital, "session_start_date": config["session_start_date"],
            "as_of_date": previous, "banban": {"phase": "INITIAL", "atr": {"cash": capital / 2, "lots": []},
                                                "r2": {"cash": capital / 2, "lots": [], "seed": capital / 2,
                                                       "reference_seed": capital / 2}}}


def _holdings(engine):
    result = []
    if engine.atr_position:
        result.append({**deepcopy(engine.atr_position), "sleeve": "ATR", "strategy": "ATR Wilder70",
                       "exit_rule": "NEXT_OPEN"})
    for position in engine.positions:
        context = engine._get_position_strategy_context(position)
        rules = get_mode_config(position["mode"], strategy_context=context)
        result.append({**deepcopy(position), "sleeve": "R2", "strategy": "정배열 R2",
                       "target_price": position["entry_price"] * rules["sell_threshold"],
                       "max_holding_days": rules["max_holding_days"]})
    return result


def _cent_price(value):
    """Round only the displayed recommendation; keep model prices separate."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _preview(engine, data, end):
    # Preview the next morning's settlements/mode/renewal on a private view.
    # Never advance the replay engine or consume its pending settlements.
    engine = copy(engine)
    engine.mode_history = deepcopy(engine.mode_history)
    engine.settlements = deepcopy(engine.settlements)
    calendar = exchange_calendar()
    session = calendar.next_session(end).strftime("%Y-%m-%d")
    monthly = session[:7] != end[:7]
    result = {"session_date": session, "basis_date": end, "simulation": True,
              "monthly_rebalance_due": monthly, "ATR": [], "R2": []}
    if engine.atr_position:
        result["ATR"].append({"side": "SELL", "order_type": "MOO", "quantity": engine.atr_position["shares"],
                              "price": None, "status": "NEXT_OPEN", "exit_rule": "NEXT_OPEN"})
    # build_filter_by_session produces filters for rows only. Append a dummy
    # next-session row; the filter uses strictly prior completed candles.
    last = data["bars"]["SOXL"][-1]
    future = {**last, "date": session}
    filt = build_filter_by_session(data["bars"]["SOXL"] + [future], _settings())[session]
    result["ATR"].append({"side": "BUY", "order_type": "BREAKOUT", "quantity": None, "price": None,
                          "budget": None if monthly or engine.atr_position else engine.atr_cash,
                          "cash_now": engine.atr_cash, "trigger_multiplier": 1.015,
                          "filter_passed": bool(filt.get("passed")),
                          "status": "REBALANCE_PENDING" if monthly else "WAITING_OPEN" if filt.get("passed") else "FILTER_BLOCKED"})
    engine._set_strategy_state_for_date(session)
    engine.update_mode(session)
    engine.process_settlements(session)
    if (engine.trading_day_count + 1) % engine.reference_seed_renewal_days == 0:
        engine.reference_seed = engine.seed
    for position in engine.positions:
        context = engine._get_position_strategy_context(position)
        rules = get_mode_config(position["mode"], strategy_context=context)
        expiry = calendar.session_offset(position["entry_date"], int(rules["max_holding_days"])).strftime("%Y-%m-%d")
        expired = session >= expiry
        strategy_price = position["entry_price"] * rules["sell_threshold"]
        result["R2"].append({"side": "SELL", "order_type": "MOC" if expired else "LOC",
                             "quantity": None if monthly else position["shares"],
                             "model_quantity": None if monthly else position["shares"],
                             "shares_before_rebalance": position["shares"],
                             "strategy_price": strategy_price,
                             "price": None if expired else _cent_price(Decimal(str(position["entry_price"])) * Decimal(str(rules["sell_threshold"]))),
                             "round": position["round"], "entry_date": position["entry_date"],
                             "expiry_date": expiry, "status": "REBALANCE_PENDING" if monthly else "CONDITIONAL"})
    rules = get_mode_config(engine.current_mode, strategy_context=engine.strategy_context)
    round_number = engine.get_next_round()
    strategy_price = last["close"] * rules["buy_threshold"]
    price = _cent_price(Decimal(str(last["close"])) * Decimal(str(rules["buy_threshold"])))
    if monthly:
        # Opening rebalancing can fully remove lots and change the next round,
        # cash and reference seed. Even a full book can gain a buy round; its
        # quantity cannot be known from the previous close alone.
        result["R2"].append({"side": "BUY", "order_type": "LOC", "quantity": None,
                             "model_quantity": None, "strategy_price": strategy_price,
                             "price": price, "budget": None, "round": None,
                             "mode": engine.current_mode, "strategy_id": engine.active_strategy_id,
                             "status": "REBALANCE_PENDING"})
    elif round_number is not None:
        budget = min(engine.live_cash, engine.reference_seed * rules["rounds"][round_number - 1])
        # Evaluate a conditional fill at the already-known LOC limit. The
        # audited sizing includes its exact fee reservation and float tolerance,
        # and fixes quantity before any same-day sale proceeds are available.
        # Capping again at the cent-rounded recommendation prevents that shown
        # order from exceeding its budget when rounding raises the unit price.
        candidate = engine.check_buy_signal(strategy_price, last["close"])
        model_quantity = candidate["shares"] if candidate else 0
        unit_cost = Decimal(str(price)) * (Decimal(1) + Decimal(str(engine.rate)))
        rounded_cash_cap = int(Decimal(str(budget)) // unit_cost) if unit_cost > 0 else 0
        quantity = min(model_quantity, max(0, rounded_cash_cap))
        result["R2"].append({"side": "BUY", "order_type": "LOC", "quantity": quantity,
                             "model_quantity": model_quantity, "strategy_price": strategy_price,
                             "price": price, "budget": budget, "round": round_number,
                             "mode": engine.current_mode, "strategy_id": engine.active_strategy_id,
                             "status": "CONDITIONAL"})
    return result


def replay(market_snapshot, config=None, *, end_date=None):
    """Replay both sleeves through an explicitly completed session.

    The caller owns the completed-session cutoff. Data later than ``end_date``
    are discarded before calculating any signal. Results are simulations and
    must never be supplied as broker-confirmed holdings.
    """
    config = normalize_config(config)
    if end_date is not None:
        _iso(end_date)
    data, end = _market_snapshot(market_snapshot, end_date)
    start = config["session_start_date"]
    if end < start:
        raise ValueError("Completed history ends before the preset start date")
    prior = [row for row in data["bars"]["SOXL"] if row["date"] < start]
    if len(prior) < 322:
        raise ValueError("At least 322 prior SOXL sessions are required for Wilder70 and ATR percentile warmup")
    with redirect_stdout(io.StringIO()):
        engine = AtrR2MonthlyBacktester(rows=data["bars"]["SOXL"], market_provider=SnapshotProvider(data),
            settings=_settings(), start_date=start, end_date=end, initial_seed=config["initial_capital"],
            commission_rate=config["commission_rate"] * 100, user_id=1, account_id=3, atr_weight_pct=50,
            buy_slippage_pct=0, sell_slippage_pct=0)
        report = engine.run()
    combined = report["combined_daily_log"]
    if not combined:
        raise ValueError("No completed sessions in investment period")
    daily = []
    peak, mdd, mdd_date = config["initial_capital"], 0.0, start
    for combined_row, atr, r2 in zip(combined, report["atr_daily_log"], report["r2"]["daily_log"]):
        nav = combined_row["account_value"]
        cash = atr["cash"] + r2["live_cash"]
        peak = max(peak, nav)
        drawdown = (nav / peak - 1) * 100
        if drawdown < mdd:
            mdd, mdd_date = drawdown, combined_row["date"]
        daily.append({**combined_row, "total_assets": nav, "available_cash": cash, "cash_balance": cash,
                      "position_value": nav - cash, "drawdown_pct": drawdown, "mdd_pct": mdd,
                      "close_price": r2["close_price"], "r2_mode": r2["mode"]})
    holdings = _holdings(engine)
    close = data["bars"]["SOXL"][-1]["close"]
    sleeves = []
    for sleeve, cash in (("ATR", engine.atr_cash), ("R2", engine.live_cash)):
        positions = [position for position in holdings if position["sleeve"] == sleeve]
        shares = sum(position["shares"] for position in positions)
        sleeves.append({"sleeve": sleeve, "strategy": "ATR Wilder70" if sleeve == "ATR" else "정배열 R2",
                        "target_weight_pct": 50, "cash": cash, "shares": shares,
                        "position_value": shares * close, "total_assets": cash + shares * close,
                        "weight_pct": (cash + shares * close) / daily[-1]["total_assets"] * 100})
    trades = [{**trade, "sleeve": "R2"} for trade in report["r2"]["trades"]]
    for trade in report["atr_trades"]:
        pnl = trade["shares"] * (trade["exit_price"] - trade["entry_price"]) - trade["entry_fee"] - trade["exit_fee"]
        trades.append({**trade, "sleeve": "ATR", "profit_loss": pnl})
    trades.sort(key=lambda trade: (trade["exit_date"], trade["entry_date"], trade["sleeve"]))
    final = daily[-1]["total_assets"]
    metrics = {"initial_capital": config["initial_capital"], "final_nav": final, "total_assets": final,
               "profit_loss": final - config["initial_capital"], "return_pct": (final / config["initial_capital"] - 1) * 100,
               "mdd_pct": mdd, "mdd_date": mdd_date, "trading_days": len(daily), "total_trades": len(trades),
               "realized_profit_loss": sum(trade["profit_loss"] for trade in trades),
               "available_cash": daily[-1]["available_cash"], "position_value": daily[-1]["position_value"],
               "commission_usd": report["execution_cost_totals"]["commission_usd"]}
    snapshot = initial_snapshot(config)
    snapshot.update(as_of_date=end, available_cash=metrics["available_cash"], total_assets=final,
                    holdings=holdings, maxMdd={"percent": -mdd, "date": mdd_date,
                        "updatedAt": end, "calculationVersion": "banban_full_history_v1"},
                    banban={"phase": "SIMULATED", "atr": {"cash": engine.atr_cash,
                        "lots": [lot for lot in holdings if lot["sleeve"] == "ATR"]},
                        "r2": {"cash": engine.live_cash, "lots": [lot for lot in holdings if lot["sleeve"] == "R2"],
                               "seed": engine.seed, "reference_seed": engine.reference_seed,
                               "settlements": deepcopy(engine.settlements)}})
    result = {"strategy_id": STRATEGY_ID, "strategy_name": STRATEGY_NAME, "preset": PRESET,
              "simulation": True, "execution_mode": "simulation", "config": config,
              "start_date": start, "end_date": end, "snapshot": snapshot, "metrics": metrics,
              "daily": daily, "holdings": holdings, "sleeves": sleeves, "trades": trades,
              "rebalance_events": report["rebalance_events"], "order_preview": _preview(engine, data, end),
              "assumptions": report["assumptions"]}
    result = _json_safe(result)
    # A saved report remains inspectable when fresh market data is unavailable.
    # Keep its actual basis date; never advance it merely because the app opened.
    result["snapshot"]["banban_report"] = deepcopy({key: value for key, value in result.items() if key != "snapshot"})
    json.dumps(result, allow_nan=False)
    return result


def report_from_snapshot(snapshot):
    """Restore a replay result from a saved simulation snapshot, or return None."""
    if not isinstance(snapshot, dict) or snapshot.get("simulation") is not True:
        return None
    report = snapshot.get("banban_report")
    if (snapshot.get("preset") != PRESET or snapshot.get("strategy_id") != STRATEGY_ID
            or not isinstance(report, dict) or report.get("simulation") is not True
            or report.get("end_date") != snapshot.get("as_of_date")):
        return None
    result = deepcopy(report)
    result["snapshot"] = deepcopy(snapshot)
    return result
