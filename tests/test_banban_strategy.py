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
