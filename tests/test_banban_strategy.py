"""Real offline mixed-strategy replay, cash accounting, and endpoint isolation."""
from copy import deepcopy
from collections import defaultdict
import json
import math
from unittest.mock import patch

import pandas as pd
import pytest

from banban_strategy import (DEFAULT_KMW3_CONFIG, initial_snapshot, normalize_config,
                             replay, report_from_snapshot)
from banban_research.atr_sessions import exchange_calendar


@pytest.fixture(scope="module")
def market():
    dates = exchange_calendar().sessions_in_range("2024-01-02", "2026-11-03").strftime("%Y-%m-%d")
    bars = {"SOXL": [], "QQQ": []}
    for i, day in enumerate(dates):
        for symbol in bars:
            base = (100 + i * .08 + 2 * math.sin(i / 10)) if symbol == "SOXL" else (400 + i * .1 + 15 * math.sin(i / 16))
            bars[symbol].append({"date": day, "open": base, "high": base * 1.03,
                                 "low": base * .98, "close": base * 1.002, "volume": 1000000})
    return {"bars": bars}


def test_kmw3_initial_allocation_and_identity():
    config = normalize_config()
    assert config["session_start_date"] == "2026-10-20"
    assert config["initial_capital"] == 80000
    assert config["strategy_name"] == "반반매매"
    assert config["commission_rate"] == .0005
    snapshot = initial_snapshot()
    assert snapshot["as_of_date"] == "2026-10-19"
    assert snapshot["available_cash"] == 80000
    assert snapshot["banban"]["atr"]["cash"] == snapshot["banban"]["r2"]["cash"] == 40000
    assert snapshot["simulation"] is True
    assert "broker_sync" not in snapshot and "atr70_r2" not in snapshot
    assert report_from_snapshot(snapshot) is None


def test_replay_costs_cash_nav_and_saved_report(market):
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
        result = replay(market, end_date="2026-10-30")
    assert result["daily"][0]["date"] == "2026-10-20"
    assert result["daily"][-1]["date"] == "2026-10-30"
    assert len(result["daily"]) == result["metrics"]["trading_days"] == 9
    assert result["snapshot"]["simulation"] is True
    assert result["assumptions"]["live_execution_enabled"] is False
    assert result["metrics"]["commission_usd"] > 0
    assert {row["sleeve"] for row in result["sleeves"]} == {"ATR", "R2"}
    for row in result["daily"]:
        assert row["available_cash"] >= 0
        assert row["total_assets"] == pytest.approx(row["available_cash"] + row["position_value"])
        assert row["total_assets"] == pytest.approx(row["atr_asset"] + row["r2_asset"])
    # Independently reconstruct account cash and share counts from entry/exit
    # records, including open holdings. Neither sleeve cash is trusted here.
    events = defaultdict(lambda: {"cash": 0.0, "shares": 0})
    fees = 0.0
    for trade in result["trades"]:
        quantity = trade["shares"]
        entry, exit = quantity * trade["entry_price"], quantity * trade["exit_price"]
        events[trade["entry_date"]]["cash"] -= entry * 1.0005
        events[trade["entry_date"]]["shares"] += quantity
        events[trade["exit_date"]]["cash"] += exit * .9995
        events[trade["exit_date"]]["shares"] -= quantity
        fees += (entry + exit) * .0005
    for lot in result["holdings"]:
        entry = lot["shares"] * lot["entry_price"]
        events[lot["entry_date"]]["cash"] -= entry * 1.0005
        events[lot["entry_date"]]["shares"] += lot["shares"]
        fees += entry * .0005
    cash, shares = 80000.0, 0
    for row in result["daily"]:
        cash += events[row["date"]]["cash"]
        shares += events[row["date"]]["shares"]
        assert row["available_cash"] == pytest.approx(cash, abs=1e-7)
        assert row["total_assets"] == pytest.approx(cash + shares * row["close_price"], abs=1e-7)
    assert result["metrics"]["commission_usd"] == pytest.approx(fees)
    assert sum(row["total_assets"] for row in result["sleeves"]) == pytest.approx(result["metrics"]["final_nav"])
    serialized = json.loads(json.dumps(result["snapshot"], allow_nan=False))
    assert report_from_snapshot(serialized) == result


def test_endpoint_cutoff_blocks_future_price_and_weekly_rsi_leak(market):
    baseline = replay(market, end_date="2026-10-30")
    altered = deepcopy(market)
    for rows in altered["bars"].values():
        for row in rows:
            if row["date"] > "2026-10-30":
                row.update(open=1000000, high=2000000, low=.01, close=.02)
    future = replay(altered, end_date="2026-10-30")
    assert baseline == future
    trimmed = {"bars": {symbol: [row for row in rows if row["date"] <= "2026-10-30"]
                         for symbol, rows in market["bars"].items()}}
    assert replay(trimmed, end_date="2026-10-30") == baseline
    assert baseline["order_preview"]["basis_date"] == "2026-10-30"
    assert baseline["order_preview"]["session_date"] == "2026-11-02"
    assert baseline["order_preview"]["monthly_rebalance_due"] is True
    assert all(order["status"] == "REBALANCE_PENDING" for sleeve in ["ATR", "R2"]
               for order in baseline["order_preview"][sleeve] if order["side"] == "BUY")


def test_monthly_rebalance_uses_first_us_session_and_half_assets(market):
    result = replay(market, end_date="2026-11-02")
    assert len(result["rebalance_events"]) == 1
    event = result["rebalance_events"][0]
    assert event["session_date"] == "2026-11-02"
    assert float(event["atr_asset"]) == pytest.approx(float(event["r2_asset"]))
    assert result["snapshot"]["as_of_date"] == "2026-11-02"


def test_accepts_real_dataframes_and_isolates_caller_objects(market):
    frames = {}
    for symbol, rows in market["bars"].items():
        frame = pd.DataFrame(rows).set_index("date")
        frame.index = pd.to_datetime(frame.index)
        frame.columns = [column.title() for column in frame.columns]
        frames[symbol] = frame
    before = deepcopy(frames)
    result = replay(frames, end_date="2026-10-28")
    assert result["end_date"] == "2026-10-28"
    for symbol in frames:
        pd.testing.assert_frame_equal(before[symbol], frames[symbol])
    assert DEFAULT_KMW3_CONFIG["initial_capital"] == 80000


def test_missing_stale_or_duplicate_completed_prices_are_rejected(market):
    missing = deepcopy(market)
    missing["bars"]["QQQ"] = [row for row in missing["bars"]["QQQ"] if row["date"] != "2026-10-27"]
    with pytest.raises(ValueError, match="missing"):
        replay(missing, end_date="2026-10-30")
    with pytest.raises(ValueError, match="missing"):
        replay(market, end_date="2026-11-04")
    duplicate = deepcopy(market)
    duplicate["bars"]["SOXL"].append(deepcopy(duplicate["bars"]["SOXL"][-2]))
    with pytest.raises(ValueError, match="Duplicate"):
        replay(duplicate, end_date="2026-11-03")


@pytest.mark.parametrize("change", [{"initial_capital": False}, {"initial_capital": float("nan")},
    {"commission_rate": -.01}, {"execution_mode": "live"}, {"strategy_id": "dongpa_modified"},
    {"seed_increases": [{"date": "2026-10-21", "amount": 1}]}])
def test_rejects_unsafe_or_ambiguous_config(change):
    with pytest.raises(ValueError):
        normalize_config(change)


@pytest.fixture(scope="module")
def differential_market():
    """Multiple market cycles exercise SF/AG, alignment and long-held books."""
    dates = exchange_calendar().sessions_in_range("2023-01-03", "2026-11-03").strftime("%Y-%m-%d")
    bars = {"SOXL": [], "QQQ": []}
    for index, day in enumerate(dates):
        soxl = 100 * math.exp(.0002 * index + .38 * math.sin(index / 24) + .06 * math.sin(index / 5))
        qqq = 400 + 50 * math.sin(index / 18) + 20 * math.sin(index / 54)
        for symbol, price in (("SOXL", soxl), ("QQQ", qqq)):
            opening = price * (1 + .002 * math.sin(index))
            bars[symbol].append({"date": day, "open": opening, "high": max(opening, price) * 1.025,
                                 "low": min(opening, price) * .975, "close": price, "volume": 1000000})
    return {"bars": bars}


def make_engine(market, *, start="2026-10-20", end="2026-10-21"):
    from contextlib import redirect_stdout
    import io
    from banban_strategy import _settings
    from banban_research.atr_r2_backtest import AtrR2MonthlyBacktester
    from banban_research.snapshot_provider import SnapshotProvider

    data = {"bars": {symbol: [row for row in rows if row["date"] <= end]
                     for symbol, rows in market["bars"].items()}}
    with redirect_stdout(io.StringIO()):
        engine = AtrR2MonthlyBacktester(rows=data["bars"]["SOXL"], market_provider=SnapshotProvider(data),
            settings=_settings(), start_date=start, end_date=end, initial_seed=80000,
            commission_rate=.05, user_id=1, account_id=3)
        engine.get_market_data()
    return engine, data


@pytest.mark.parametrize("model_shares,order_shares", [(41, 40), (1, 0)])
def test_preview_uses_audited_fee_rounding_at_exact_integer_boundary(differential_market, model_shares, order_shares):
    from banban_strategy import _preview
    from banban_research.mode_manager import get_mode_config

    engine, data = make_engine(differential_market)
    engine.positions = []
    engine.trading_day_count = 2
    engine.current_mode = "SF"
    engine.qqq_weekly = pd.DataFrame(columns=["RSI"], index=pd.DatetimeIndex([]))
    engine._set_strategy_state_for_date("2026-10-22")
    rules = get_mode_config("SF", strategy_context=engine.strategy_context)
    previous = data["bars"]["SOXL"][-1]["close"]
    limit = previous * rules["buy_threshold"]
    # The floating ratio is 40.99999999999999. The audited fee calculation
    # intentionally tolerates this representation and returns exactly 41.
    engine.reference_seed = limit * (1 + engine.rate) * model_shares / rules["rounds"][0]
    candidate = engine.check_buy_signal(limit, previous)
    assert candidate["shares"] == model_shares
    preview = _preview(engine, data, "2026-10-21")
    buy = next(order for order in preview["R2"] if order["side"] == "BUY")
    assert buy["model_quantity"] == candidate["shares"]
    assert buy["quantity"] == order_shares
    assert buy["price"] == 190.97
    assert buy["strategy_price"] == pytest.approx(limit)
    assert buy["quantity"] * buy["price"] * 1.0005 <= buy["budget"]
    assert candidate["shares"] * buy["price"] * 1.0005 > buy["budget"]


@pytest.mark.parametrize("pnl,reflection", [(1000, .72), (-1000, .213)])
def test_preview_is_pure_and_settles_before_the_tenth_day_seed_renewal(differential_market, pnl, reflection):
    from banban_strategy import _preview
    from banban_research.mode_manager import get_mode_config

    engine, data = make_engine(differential_market)
    engine.positions = []
    engine.trading_day_count = 9
    engine.current_mode = "SF"
    engine.seed = 40000
    engine.reference_seed = 30000
    engine.qqq_weekly = pd.DataFrame(columns=["RSI"], index=pd.DatetimeIndex([]))
    engine._set_strategy_state_for_date("2026-10-22")
    context = deepcopy(engine.strategy_context)
    engine.settlements = [{"profit_loss": pnl, "settlement_date": "2026-10-22",
                           "trade_date": "2026-10-15", "strategy_context": context},
                          {"profit_loss": -1000, "settlement_date": "2026-10-23",
                           "trade_date": "2026-10-16", "strategy_context": context}]
    before = deepcopy(engine.__dict__)
    preview = _preview(engine, data, "2026-10-21")
    rules = get_mode_config("SF", strategy_context=context)
    buy = next(order for order in preview["R2"] if order["side"] == "BUY")
    assert buy["budget"] == pytest.approx((40000 + pnl * reflection) * rules["rounds"][0])
    assert engine.seed == before["seed"] and engine.reference_seed == before["reference_seed"]
    assert engine.settlements == before["settlements"]
    assert engine.mode_history == before["mode_history"]
    assert engine.strategy_context == before["strategy_context"]
    assert preview == _preview(engine, data, "2026-10-21")


def test_monthly_full_book_waits_for_open_before_round_and_quantity(differential_market):
    from banban_strategy import _preview
    from banban_research.strategy_presets import get_strategy_context

    data = deepcopy(differential_market)
    for row in data["bars"]["SOXL"]:
        if "2026-07-01" <= row["date"] <= "2026-10-30":
            row.update(open=90, high=91, low=89, close=90)
        elif row["date"] == "2026-11-02":
            row.update(open=100, high=101, low=89, close=90)
    engine, data = make_engine(data, end="2026-11-02")
    context = get_strategy_context("dongpa_modified")
    entries = exchange_calendar().sessions_in_range("2026-10-20", "2026-10-28").strftime("%Y-%m-%d")
    engine.positions = [{"entry_date": entry, "entry_price": 100, "shares": 1, "round": index + 1,
                         "mode": "SF", "strategy_id": "dongpa_modified", "strategy_context": deepcopy(context),
                         "target_amount": 100, "_entry_cost": .05, "_entry_slippage": 0}
                        for index, entry in enumerate(entries)]
    assert len(engine.positions) == 7
    engine.current_mode = "SF"
    engine.qqq_weekly = pd.DataFrame(columns=["RSI"], index=pd.DatetimeIndex([]))
    engine.atr_cash, engine.live_cash = 96, 0
    engine.atr_position = None
    engine._rebalance_month = "2026-10"
    engine.trading_day_count = 9
    engine._set_strategy_state_for_date("2026-11-02")
    assert engine.get_next_round() is None
    prior_data = {"bars": {symbol: [row for row in rows if row["date"] <= "2026-10-30"]
                            for symbol, rows in data["bars"].items()}}
    preview = _preview(engine, prior_data, "2026-10-30")
    buy = next(order for order in preview["R2"] if order["side"] == "BUY")
    assert buy["status"] == "REBALANCE_PENDING"
    assert buy["quantity"] is buy["round"] is buy["budget"] is None
    assert all(order["quantity"] is None for order in preview["R2"] if order["side"] == "SELL")
    assert all(order["shares_before_rebalance"] == 1 for order in preview["R2"] if order["side"] == "SELL")
    # Once the actual opening is supplied, four one-share lots are fully sold
    # to rebalance. The remaining three lots allow round 4 and one LOC share.
    engine.process_cash_flows("2026-11-02")
    engine.process_settlements("2026-11-02")
    engine.reference_seed = engine.seed
    engine.update_mode("2026-11-02")
    candidate = engine.check_buy_signal(90, 90)
    assert len(engine.positions) == 3
    assert candidate["round"] == 4 and candidate["shares"] == 1


def test_preview_differential_against_audited_daily_engine(differential_market):
    """Traverse 359 sessions; compare representative morning plans to execution.

    Snapshots are captured before each day's opening/cash flows. The independent
    oracle is the unchanged audited engine immediately before its close orders.
    Monday, 10-day renewal, month-end and regular sample days are compared.
    """
    from collections import Counter
    from contextlib import redirect_stdout
    import io
    from banban_strategy import _preview, _settings
    from banban_research.atr_r2_backtest import AtrR2MonthlyBacktester
    from banban_research.mode_manager import get_mode_config
    from banban_research.rsi_calculator import calculate_rsi
    from banban_research.snapshot_provider import SnapshotProvider

    class AuditedDailyEngine(AtrR2MonthlyBacktester):
        def __init__(self, **kwargs):
            self.previews, self.expected, self.sells, self.atr_facts = {}, {}, {}, {}
            self.coverage = Counter()
            super().__init__(**kwargs)

        def process_cash_flows(self, day):
            self.audit_day = day
            if self.start_date < day <= self.end_date:
                prior = exchange_calendar().previous_session(day).strftime("%Y-%m-%d")
                sample = (pd.Timestamp(day).weekday() == 0 or (self.trading_day_count + 1) % 10 == 0
                          or day[:7] != prior[:7] or self.trading_day_count % 13 == 0)
                if sample:
                    # A base-class view avoids invoking this test's recording
                    # overrides from inside the preview's conditional buy.
                    saved = object.__new__(AtrR2MonthlyBacktester)
                    saved.__dict__ = self.__dict__.copy()
                    saved.soxl_data = self.soxl_data.loc[:prior].copy()
                    saved.qqq_data = self.qqq_data.loc[:prior].copy()
                    saved.qqq_weekly = saved.qqq_data.resample("W-FRI").agg({
                        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
                    saved.qqq_weekly["RSI"] = calculate_rsi(saved.qqq_weekly, window=14)
                    data = {"bars": {"SOXL": [row for row in self.raw_rows if row["date"] <= prior]}}
                    self.previews[day] = _preview(saved, data, prior)
                    self.atr_facts[day] = {"before_lot": deepcopy(self.atr_position), "before_cash": self.atr_cash}
            result = super().process_cash_flows(day)
            if day in self.atr_facts:
                self.atr_facts[day].update(entry=deepcopy(self.atr_position),
                    exit=deepcopy(self.atr_trades[-1]) if self.atr_trades and self.atr_trades[-1]["exit_date"] == day else None)
            return result

        def check_buy_signal(self, price, previous):
            rules = get_mode_config(self.current_mode, strategy_context=self.strategy_context)
            limit = previous * rules["buy_threshold"]
            candidate = super().check_buy_signal(limit, previous)
            self.expected[self.audit_day] = {"candidate": deepcopy(candidate), "mode": self.current_mode,
                "strategy_id": self.active_strategy_id, "price": limit, "close": price,
                "previous_close": previous, "buy_multiplier": rules["buy_threshold"],
                "positions": deepcopy(self.positions), "reference_seed": self.reference_seed,
                "cash": self.live_cash}
            self.coverage[self.current_mode] += 1
            self.coverage[self.active_strategy_id] += 1
            self.coverage["max_lots"] = max(self.coverage["max_lots"], len(self.positions))
            self.coverage["renewals"] += int(self.trading_day_count % 10 == 0)
            return super().check_buy_signal(price, previous)

        def check_sell_signals(self, price, previous, day):
            signals = super().check_sell_signals(price, previous, day)
            self.sells[day] = [(deepcopy(position), reason) for position, reason, _ in signals]
            for _, reason, _ in signals:
                self.coverage[reason] += 1
            return signals

    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), redirect_stdout(io.StringIO()):
        engine = AuditedDailyEngine(rows=differential_market["bars"]["SOXL"],
            market_provider=SnapshotProvider(differential_market), settings=_settings(), start_date="2025-06-02",
            end_date="2026-11-03", initial_seed=80000, commission_rate=.05, user_id=1, account_id=3)
        engine.run()
    compared = Counter()
    for day, preview in engine.previews.items():
        expected = engine.expected[day]
        if preview["monthly_rebalance_due"]:
            assert all(order["status"] == "REBALANCE_PENDING" for order in preview["R2"])
            assert all(order["quantity"] is None for order in preview["R2"])
            compared["monthly"] += 1
            continue
        buys = [order for order in preview["R2"] if order["side"] == "BUY"]
        buy, candidate = buys[0] if buys else None, expected["candidate"]
        assert bool(buy and buy["model_quantity"] > 0) == bool(candidate), day
        if candidate:
            assert buy["model_quantity"] == candidate["shares"], day
            assert 0 <= buy["quantity"] <= buy["model_quantity"], day
            assert buy["quantity"] * buy["price"] * (1 + engine.rate) <= buy["budget"] + 1e-9, day
            assert buy["round"] == candidate["round"], day
            assert buy["mode"] == expected["mode"], day
            assert buy["strategy_id"] == expected["strategy_id"], day
            from decimal import Decimal, ROUND_HALF_UP
            assert buy["strategy_price"] == pytest.approx(expected["price"]), day
            assert buy["price"] == float((Decimal(str(expected["previous_close"])) * Decimal(str(expected["buy_multiplier"]))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)), day
            compared[expected["mode"]] += 1
            compared[expected["strategy_id"]] += 1
        elif not buys:
            compared["full_book"] += 1
        sells = {(order["entry_date"], order["round"]): order for order in preview["R2"] if order["side"] == "SELL"}
        assert set(sells) == {(position["entry_date"], position["round"]) for position in expected["positions"]}, day
        predicted_exits = set()
        for key, order in sells.items():
            if order["order_type"] == "MOC" or expected["close"] >= order["strategy_price"]:
                predicted_exits.add((*key, order["quantity"]))
        actual_exits = {(position["entry_date"], position["round"], position["shares"])
                        for position, reason in engine.sells[day]}
        assert predicted_exits == actual_exits, day
        for _, reason in engine.sells[day]:
            compared[reason] += 1
        atr_buy = next(order for order in preview["ATR"] if order["side"] == "BUY")
        assert atr_buy["filter_passed"] == engine.filters[day]["passed"], day
        facts = engine.atr_facts[day]
        atr_sells = [order for order in preview["ATR"] if order["side"] == "SELL"]
        assert bool(atr_sells) == bool(facts["before_lot"]), day
        if facts["before_lot"]:
            assert atr_sells[0]["order_type"] == "MOO", day
            assert atr_sells[0]["quantity"] == facts["before_lot"]["shares"], day
            assert facts["exit"]["shares"] == atr_sells[0]["quantity"], day
            assert facts["exit"]["exit_reason"] == "NEXT_OPEN", day
            assert atr_buy["budget"] is None, day
            compared["ATR_NEXT_OPEN"] += 1
        else:
            assert atr_buy["budget"] == pytest.approx(facts["before_cash"]), day
        bar = engine.raw[day]
        opening_cash = facts["before_cash"]
        if facts["before_lot"]:
            opening_cash += facts["before_lot"]["shares"] * bar["open"] * (1 - engine.rate)
        trigger = bar["open"] * 1.015
        from decimal import Decimal
        quantity = int(Decimal(str(opening_cash)) // (Decimal(str(trigger)) * (Decimal(1) + Decimal(str(engine.rate)))))
        should_enter = atr_buy["filter_passed"] and bar["high"] + 1e-12 >= trigger and quantity > 0
        assert bool(facts["entry"]) == should_enter, day
        if should_enter:
            assert facts["entry"]["shares"] == quantity, day
            assert facts["entry"]["entry_price"] == pytest.approx(trigger), day
            compared["ATR_ENTRY"] += 1
        compared["days"] += 1
    assert engine.coverage["SF"] > 100 and engine.coverage["AG"] > 100
    assert engine.coverage["dongpa_modified"] > 100 and engine.coverage["dongpa_ma_v1_1_r2"] > 100
    assert engine.coverage["renewals"] == 35 and engine.coverage["max_lots"] == 7
    assert len(engine.previews) > 100
    assert compared["days"] > 80 and compared["monthly"] >= 10
    assert all(compared[key] > 0 for key in ("SF", "AG", "dongpa_modified", "dongpa_ma_v1_1_r2", "full_book", "MOC", "LOC", "ATR_ENTRY", "ATR_NEXT_OPEN"))


def test_recommendation_prices_round_decimal_factors_half_up(differential_market):
    from banban_strategy import _preview
    from banban_research.strategy_presets import get_strategy_context

    engine, data = make_engine(differential_market)
    engine.soxl_data.loc[:, "Close"] = 25
    data["bars"]["SOXL"][-1].update(open=25, high=26, low=24, close=25)
    engine.qqq_weekly = pd.DataFrame(columns=["RSI"], index=pd.DatetimeIndex([]))
    engine.current_mode = "SF"
    engine.trading_day_count = 2
    engine.positions = [{"entry_date": "2026-10-20", "entry_price": 75, "shares": 1,
                         "round": 1, "mode": "SF", "strategy_id": "dongpa_modified",
                         "strategy_context": get_strategy_context("dongpa_modified")}]
    preview = _preview(engine, data, "2026-10-21")
    buy = next(order for order in preview["R2"] if order["side"] == "BUY")
    sell = next(order for order in preview["R2"] if order["side"] == "SELL")
    # Binary float multiplication lands just below these exact half-cent prices.
    assert buy["strategy_price"] == 25 * 1.035
    assert sell["strategy_price"] == 75 * 1.011
    assert buy["price"] == 25.88
    assert sell["price"] == 75.83
