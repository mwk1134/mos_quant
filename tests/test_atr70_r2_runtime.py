"""Mixed live routing with synthetic broker evidence; no network or credentials."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import atr70_r2 as core
from atr70_r2_runtime import (MixedBlocked, fresh_quote, mixed_order_window,
                              normalize_trade_message, run_mixed_tick)
from kmw_autotrade import OrderLedger
from meritz_api import AmbiguousSubmission
from us_market_calendar import is_us_equity_trading_day

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=ET)


def quote(now=NOW, price="103", opening="100"):
    return {"source": "meritz_websocket", "realtime": True, "symbol": "SOXL",
            "session_date": now.date().isoformat(), "last_price": price, "regular_open": opening,
            "timestamp": now.isoformat(), "received_at": now.isoformat()}


def bars():
    day = date(2025, 1, 1)
    result = []
    while day < NOW.date():
        if is_us_equity_trading_day(day):
            result.append({"date": day.isoformat(), "open": "100", "high": "101", "low": "99", "close": "100"})
        day += timedelta(days=1)
    return result


def state(cash="10000"):
    return core.initial_state("KMW2", session_date="2026-10-30", broker_cash=cash,
        broker_shares=0, open_order_count=0, broker_confirmed=True, fee_rate="0.005")


def context(**changes):
    return {"preset": "KMW2", "session_date": "2026-10-30", "previous_session": "2026-10-29",
            "next_session": "2026-11-02", "broker_confirmed": True, "legacy_shares": 0,
            "first_session_of_month": False, "daily_rows": bars(), "r2_orders": [], **changes}


class Broker:
    def __init__(self):
        self.held = 0
        self.orders = []
        self.history = []
        self.calls = []
        self.failure = None
        self.cash = "10000"

    def holdings(self):
        return ([{"iscd": "SOXL", "rmnd_dqty": self.held, "seln_able_dqty": self.held,
                  "rmnd_clcd": "1", "loan_dtl_type_code": "0000"}] if self.held else [])

    def orders_today(self):
        return deepcopy(self.orders)

    def orders_history(self, *args):
        return deepcopy(self.history)

    def reserved_orders(self, *args, **kwargs):
        return []

    def currencies(self):
        return [{"crcd": "USD", "frcr_amt": self.cash, "estm_frcr_amt": self.cash}]

    def order_estimate(self):
        return [{"crcd": "USD", "oder_crnc_oder_able_camt": "10000", "othr_crnc_evdn_camt": "0"}]

    def submit_mixed_order(self, **order):
        self.calls.append(order)
        if self.failure:
            raise self.failure
        number = len(self.calls)
        kind = {"LOC": "5", "MOC": "7", "MOO": "6", "MARKET": "1"}[order["order_type"]]
        self.orders.append({"iscd": "SOXL", "oder_date": self.session.replace("-", ""), "oder_no": str(number),
            "odqt": order["quantity"], "cntg_qty": 0, "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0,
            "seln_byov_clcd": "2" if order["side"] == "buy" else "1", "ovrs_stck_oder_stat_cls_code": "0",
            "aspr_type_code": kind})
        return {"data": {"oder_no": number, "warn_cls_code": "0"}}

    def fill(self, number, price):
        row = self.orders[number - 1]
        row["cntg_qty"] = row["odqt"]
        quantity = row["odqt"]
        self.held += quantity if row["seln_byov_clcd"] == "2" else -quantity
        self.history.append({"iscd": "SOXL", "oder_date": row["oder_date"], "oder_no": str(number),
            "cntg_dqty": quantity, "cntg_crnc_prc": str(price), "cntg_camt": str(Decimal(str(price)) * quantity),
            "seln_byov_clcd": row["seln_byov_clcd"]})


def run(broker, saved, ctx, ledger, now=NOW, enabled=True, market_quote=None):
    broker.session = now.date().isoformat()
    return run_mixed_tick(broker, saved[0], ctx, lambda value: saved.__setitem__(0, deepcopy(value)), ledger,
        now=now, enabled=enabled, account="fake-kmw2", exchange_code="0066", quote=market_quote)


def test_live_ws_requires_exact_realtime_channel_session_and_fresh_timestamp():
    stamp = NOW.astimezone(ZoneInfo("Asia/Seoul"))
    message = {"header": {"tr_cd": "ohhts_exst_cntg", "tr_key": "AX@SOXL"},
        "body": {"iscd": "AX@SOXL", "dely_yn": "0", "acpl_bsop_date": "20261030",
                 "korea_date": stamp.strftime("%Y%m%d"), "korea_hour": stamp.strftime("%H%M%S"),
                 "oprc": "100", "prpr": "103"}}
    parsed = normalize_trade_message(message, key="AX@SOXL", now=NOW)
    assert fresh_quote(parsed, NOW, "2026-10-30")["regular_open"] == "100"
    for mutate in (lambda v: v["body"].update(dely_yn="1"),
                   lambda v: v["header"].update(tr_key="AX@TQQQ"),
                   lambda v: v["body"].update(iscd="OQ@SOXL"),
                   lambda v: v["body"].update(acpl_bsop_date="20261029")):
        wrong = deepcopy(message)
        mutate(wrong)
        with pytest.raises(MixedBlocked):
            normalize_trade_message(wrong, key="AX@SOXL", now=NOW)
    with pytest.raises(MixedBlocked, match="15초"):
        normalize_trade_message(message, key="AX@SOXL", now=NOW + timedelta(seconds=16))


def test_r2_loc_and_atr_market_have_independent_receipts_and_no_quote_fills(tmp_path):
    broker, saved = Broker(), [state()]
    r2 = {"ticker": "SOXL", "side": "BUY", "quantity": 2, "price": "105", "order_type": "LOC",
          "round": 1, "lot_id": "R2:2026-10-30", "frozen_rules": core.r2_rules("SF", True)}
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        result = run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert result["state"] == "DIRECT_ACCEPTED"
        assert len(broker.calls) == len(result["orders"]) == 2
        assert all(row["preset"] == "KMW2" and row["symbol"] == "SOXL" and row["side"] == "buy"
                   and row["exchange_vendor_code"] == "0066" for row in broker.calls)
        assert {row["order_type"] for row in broker.calls} == {"MARKET", "LOC"}
        orders = {order["strategy"]: order for order in saved[0]["orders"].values()}
        assert set(orders) == {"ATR", "R2"}
        assert len({order["intent_id"] for order in orders.values()}) == 2
        assert {order["broker_order_id"] for order in orders.values()} == {"1", "2"}
        assert orders["ATR"]["order_type"] == "MARKET" and orders["R2"]["order_type"] == "LOC"
        assert [row["quantity"] for row in broker.calls] == [orders["ATR"]["quantity"], orders["R2"]["quantity"]]
        assert [row["price"] for row in broker.calls] == ["0", "105"]
        assert orders["R2"]["quantity"] == 2
        fee_factor = 1 + Decimal(saved[0]["fee_rate"])
        reserved = {strategy: Decimal(order["price"]) * order["quantity"] * fee_factor
                    for strategy, order in orders.items()}
        assert all(Decimal("0") < amount <= Decimal("5000") for amount in reserved.values())
        assert sum(reserved.values()) <= Decimal("10000")
        assert core.available_cash(saved[0], "ATR") == Decimal("5000") - reserved["ATR"]
        assert core.available_cash(saved[0], "R2") == Decimal("5000") - reserved["R2"]
        receipts = ledger.rows("fake-kmw2")
        assert len(receipts) == 2
        assert {row["response"]["data"]["oder_no"] for row in receipts} == {1, 2}
        assert {row["order"]["strategy"] for row in receipts} == {"ATR", "R2"}
        assert all(row["state"] == "DIRECT_ACCEPTED" for row in receipts)
        assert saved[0]["atr"]["lots"] == [] and saved[0]["r2"]["lots"] == []
        assert all(order["status"] == "ACCEPTED" for order in saved[0]["orders"].values())
        broker.fill(1, "103")
        run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert len(broker.calls) == 2
        assert saved[0]["atr"]["lots"][0]["shares"] > 0
        assert saved[0]["r2"]["lots"] == []
        assert saved[0]["r2"]["cash"] == "5000"
        actual_atr_notional = Decimal("103") * orders["ATR"]["quantity"]
        assert Decimal(saved[0]["atr"]["cash"]) == Decimal("5000") - actual_atr_notional * fee_factor
        assert saved[0]["fee_source"] == "configured_reserve"
        broker.fill(2, "105")
        run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert len(broker.calls) == 2
        assert sum(lot["shares"] for leg in ("atr", "r2") for lot in saved[0][leg]["lots"]) == broker.held
        assert broker.held == orders["ATR"]["quantity"] + orders["R2"]["quantity"]
        assert Decimal(saved[0]["r2"]["cash"]) == Decimal("5000") - Decimal("210") * fee_factor
        assert all(order["status"] == "FILLED" for order in saved[0]["orders"].values())


class OrderLedgerContext:
    def __init__(self, path):
        self.ledger = OrderLedger(path)
    def __enter__(self):
        return self.ledger
    def __exit__(self, *args):
        self.ledger.db.close()


def test_pending_r2_loc_does_not_disable_later_atr_crossing(tmp_path):
    broker, saved = Broker(), [state()]
    r2 = {"side": "BUY", "quantity": 2, "price": "105", "order_type": "LOC", "round": 1,
          "lot_id": "R2:2026-10-30", "frozen_rules": core.r2_rules("SF", True)}
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote(price="101"))
        assert [order["order_type"] for order in broker.calls] == ["LOC"]
        run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert [order["order_type"] for order in broker.calls] == ["LOC", "MARKET"]
        assert all(order["side"] == "buy" for order in broker.calls)
        assert {order["strategy"]: order["broker_order_id"] for order in saved[0]["orders"].values()} == {"R2": "1", "ATR": "2"}
        assert saved[0]["r2"]["lots"] == [] and saved[0]["atr"]["lots"] == []


def test_unfilled_atr_market_does_not_spend_r2_budget_or_prevent_later_r2_loc(tmp_path):
    broker, saved = Broker(), [state()]
    r2 = {"side": "BUY", "quantity": 100000, "price": "105", "order_type": "LOC", "round": 1,
          "lot_id": "R2:2026-10-30", "frozen_rules": core.r2_rules("SF", True)}
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        first = run(broker, saved, context(), ledger, market_quote=quote())
        assert len(first["orders"]) == 1 and first["orders"][0]["order"]["strategy"] == "ATR"
        assert core.available_cash(saved[0], "R2") == Decimal("5000")
        second = run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert len(second["orders"]) == 1 and second["orders"][0]["order"]["strategy"] == "R2"
        assert [row["order_type"] for row in broker.calls] == ["MARKET", "LOC"]
        orders = {order["strategy"]: order for order in saved[0]["orders"].values()}
        assert orders["ATR"]["status"] == orders["R2"]["status"] == "ACCEPTED"
        assert orders["ATR"]["broker_order_id"] != orders["R2"]["broker_order_id"]
        assert orders["ATR"]["intent_id"] != orders["R2"]["intent_id"]
        # Even an oversized R2 recommendation is capped by its own half.
        r2_reserved = Decimal("105") * orders["R2"]["quantity"] * Decimal("1.005")
        assert r2_reserved <= Decimal("5000")
        assert Decimal("105") * (orders["R2"]["quantity"] + 1) * Decimal("1.005") > Decimal("5000")
        assert saved[0]["atr"]["cash"] == saved[0]["r2"]["cash"] == "5000"
        assert broker.held == 0
        run(broker, saved, context(r2_orders=[r2]), ledger, market_quote=quote())
        assert len(broker.calls) == 2


def test_flag_off_keeps_next_open_atr_exit_and_expired_r2_is_not_an_open_order(tmp_path):
    broker, saved = Broker(), [state()]
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        run(broker, saved, context(), ledger, market_quote=quote())
        broker.fill(1, "103")
        run(broker, saved, context(), ledger, market_quote=quote())
        monday = datetime(2026, 11, 2, 8, 50, tzinfo=ET)
        ctx = context(session_date="2026-11-02", previous_session="2026-10-30", next_session="2026-11-03",
                      first_session_of_month=True)
        result = run(broker, saved, ctx, ledger, now=monday, enabled=False)
        assert result["state"] == "DIRECT_ACCEPTED"
        assert broker.calls[-1]["order_type"] == "MOO" and broker.calls[-1]["side"] == "sell"
        assert len(broker.calls) == 2


def test_unknown_submission_is_durable_and_never_retried_after_restart(tmp_path):
    broker, saved = Broker(), [state()]
    broker.failure = AmbiguousSubmission("synthetic network ambiguity")
    path = tmp_path / "orders.sqlite3"
    with OrderLedgerContext(path) as ledger:
        with pytest.raises(AmbiguousSubmission):
            run(broker, saved, context(), ledger, market_quote=quote())
        assert saved[0]["orders"][next(iter(saved[0]["orders"]))]["status"] == "UNKNOWN"
    with OrderLedgerContext(path) as ledger:
        with pytest.raises(Exception, match="unknown result"):
            run(broker, saved, context(), ledger, market_quote=quote())
    assert len(broker.calls) == 1


def test_foreign_order_and_stale_quote_block_new_entry(tmp_path):
    broker, saved = Broker(), [state()]
    broker.orders = [{"iscd": "SOXL", "oder_date": "20261030", "oder_no": "999", "odqt": 1,
        "cntg_qty": 0, "seln_byov_clcd": "2", "ovrs_stck_oder_stat_cls_code": "0"}]
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        with pytest.raises(MixedBlocked) as blocked:
            run(broker, saved, context(), ledger, market_quote=quote())
        assert blocked.value.code == "FOREIGN_ORDER"
        broker.orders = []
        run(broker, saved, context(), ledger, market_quote=quote(NOW - timedelta(seconds=20)))
        assert broker.calls == []


def test_waiting_for_flat_and_settled_cash_cannot_create_holdings(tmp_path):
    broker, saved = Broker(), [None]
    broker.held = 3
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        assert run(broker, saved, context(legacy_shares=3), ledger)["handled"] is False
        assert saved[0] is None and broker.calls == []
        broker.held = 0
        broker.currencies = lambda: [{"crcd": "USD", "frcr_amt": "900", "estm_frcr_amt": "1000"}]
        with pytest.raises(MixedBlocked) as blocked:
            run(broker, saved, context(), ledger)
        assert blocked.value.code == "WAITING_CASH"
        sunday = datetime(2026, 11, 1, 10, tzinfo=ET)
        assert run(broker, saved, context(session_date="2026-11-02"), ledger, now=sunday)["handled"] is False
        assert saved[0] is None


def test_moo_cutoff_and_market_dispatch_sessions_are_separate():
    moo = {"session_date": "2026-10-30", "side": "SELL", "order_type": "MOO"}
    mixed_order_window(NOW.replace(hour=9, minute=24), moo)
    with pytest.raises(MixedBlocked):
        mixed_order_window(NOW.replace(hour=9, minute=25), moo)
    with pytest.raises(MixedBlocked):
        mixed_order_window(NOW.replace(hour=9, minute=29), {**moo, "order_type": "MARKET"})


def test_persistence_failure_precedes_every_financial_dispatch(tmp_path):
    broker = Broker()
    broker.session = "2026-10-30"
    persist = Mock(side_effect=ValueError("synthetic CAS conflict"))
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        with pytest.raises(ValueError, match="CAS"):
            run_mixed_tick(broker, state(), context(), persist, ledger, now=NOW, enabled=True,
                account="fake-kmw2", exchange_code="0066", quote=quote())
    assert broker.calls == []


def test_monthly_sale_uses_confirmed_actual_fill_before_half_split(tmp_path):
    saved = [state("1000")]
    raw = {"side": "BUY", "quantity": 4, "price": "100", "order_type": "LOC", "round": 1,
           "lot_id": "R2:2026-10-30", "frozen_rules": core.r2_rules("SF", True)}
    intent = core.build_runtime_plan(saved[0], session_date="2026-10-30", previous_session="2026-10-29",
                                    next_session="2026-11-02", r2_orders=[raw])["orders"][0]
    saved[0] = core.register_order(saved[0], intent, "77")
    saved[0] = core.reconcile_fills(saved[0], [{"order_id": intent["intent_id"], "broker_order_id": "77",
        "cumulative_quantity": 4, "cumulative_notional": "400", "cumulative_fee": "2", "status": "FILLED"}])
    broker = Broker()
    broker.held = 4
    broker.history = [{"iscd": "SOXL", "oder_date": "20261030", "oder_no": "77", "cntg_dqty": 4,
                       "cntg_crnc_prc": "100", "cntg_camt": "400", "seln_byov_clcd": "2"}]
    monday = datetime(2026, 11, 2, 10, tzinfo=ET)
    ctx = context(session_date="2026-11-02", previous_session="2026-10-30", next_session="2026-11-03",
                  first_session_of_month=True, daily_rows=None)
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        run(broker, saved, ctx, ledger, now=monday, market_quote=quote(monday, "300", "300"))
        assert len(broker.calls) == 1 and broker.calls[0]["side"] == "sell"
        assert saved[0]["atr"]["cash"] == "500"
        assert "2026-11" not in saved[0]["rebalance_months"]
        broker.fill(1, "280")
        result = run(broker, saved, ctx, ledger, now=monday, enabled=False,
                     market_quote=quote(monday, "280", "300"))
        assert "2026-11" in saved[0]["rebalance_months"]
        atr_nav = Decimal(saved[0]["atr"]["cash"])
        r2_nav = Decimal(saved[0]["r2"]["cash"]) + sum(lot["shares"] for lot in saved[0]["r2"]["lots"]) * 280
        assert atr_nav == r2_nav
        assert len(broker.calls) == 1


def test_late_start_exits_atr_with_market_and_does_not_buy_again(tmp_path):
    broker, saved = Broker(), [state()]
    with OrderLedgerContext(tmp_path / "orders.sqlite3") as ledger:
        run(broker, saved, context(), ledger, market_quote=quote())
        broker.fill(1, "103")
        run(broker, saved, context(), ledger, market_quote=quote())
        monday = datetime(2026, 11, 2, 9, 31, tzinfo=ET)
        ctx = context(session_date="2026-11-02", previous_session="2026-10-30", next_session="2026-11-03",
                      first_session_of_month=True)
        run(broker, saved, ctx, ledger, now=monday, market_quote=quote(monday))
        assert broker.calls[-1]["order_type"] == "MARKET" and broker.calls[-1]["side"] == "sell"
        run(broker, saved, ctx, ledger, now=monday, market_quote=quote(monday))
        assert len(broker.calls) == 2


def test_mixed_api_codes_do_not_broaden_the_legacy_route():
    from meritz_api import MeritzClient
    client = MeritzClient("fake-key", "fake-secret", "001122334455")
    client._request = Mock(return_value={"data": {"oder_no": 55, "warn_cls_code": "0"}})
    for kind, side, code, condition in (("MOO", "sell", "33", "2"), ("MARKET", "buy", "05", "0")):
        client.submit_mixed_order(preset="KMW2", side=side, symbol="SOXL", exchange_vendor_code="0066",
                                  quantity=3, price="0", order_type=kind)
        payload = client._request.call_args.kwargs["payload"]
        assert payload["oder_cls_code"] == code and payload["ovrs_stck_oder_prc"] == "0"
        assert payload["oder_cond_cls_code"] == condition
    client._request.reset_mock()
    with pytest.raises(ValueError):
        client.submit_order(side="sell", symbol="SOXL", exchange_vendor_code="0066", quantity=3, price=0, order_type="MOO")
    with pytest.raises(ValueError):
        client.submit_mixed_order(preset="KMW", side="buy", symbol="SOXL", exchange_vendor_code="0066",
                                  quantity=3, price=0, order_type="MARKET")
    client._request.assert_not_called()


def test_legacy_cli_cannot_submit_even_a_loc_only_mixed_preview():
    from kmw_autotrade import OrderBlocked, validate_plan
    plan = {"preset": "KMW2", "status": "ready", "atr70_r2_managed": True,
            "orders": [{"side": "BUY", "order_type": "LOC", "price": "100.00", "quantity": 1}]}
    with pytest.raises(OrderBlocked, match="dedicated KMW2 ATR70/R2"):
        validate_plan(plan, NOW, order_route="direct", preset="KMW2")


def test_engine_polls_mixed_independently_from_daily_schedule_and_respects_global_stop(tmp_path):
    from kmw_engine import KMWEngine

    class Backend:
        preset = "KMW2"
        def __init__(self):
            self.calls = 0
            self.execute = Mock()
        def validate_account_configuration(self):
            return {"ready": True}
        def mixed_tick(self, *, now, should_continue):
            assert should_continue()
            self.calls += 1
            return {"handled": True, "state": "WAITING", "orders": []}

    backend = Backend()
    engine = KMWEngine(backend, tmp_path / "engine.json", clock=lambda: NOW, preset="KMW2")
    engine.tick()
    assert backend.calls == 0
    engine.enable()
    assert engine.tick()["handled"] is True
    assert backend.calls == 1
    backend.execute.assert_not_called()
    engine.disable()
    engine.tick()
    assert backend.calls == 1


def test_backend_executes_through_cas_store_real_runtime_and_mock_feed(tmp_path, monkeypatch):
    from kmw_engine_backend import KMWBackend
    import atr70_r2_adapter
    import atr70_r2_runtime
    import kmw_engine_backend
    import meritz_api

    broker = Broker()
    broker.session = "2026-10-30"
    broker.exchanges = lambda: [{"ovrs_exch_vndr_idno": "0066", "onln_yn": "Y", "ovrs_stck_exch_idno": "AX"}]
    saved = [state()]

    class Store:
        def __init__(self, *args, **kwargs):
            pass
        def read(self, now):
            return context(state=deepcopy(saved[0]), enabled=True)
        def save(self, updated, now):
            saved[0] = deepcopy(updated)
        def close(self):
            pass

    class Feed:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def latest(self):
            return quote()
        def close(self):
            pass

    monkeypatch.setattr(atr70_r2_adapter, "MixedSnapshotStore", Store)
    monkeypatch.setattr(atr70_r2_runtime, "MeritzTradeFeed", Feed)
    monkeypatch.setattr(meritz_api.MeritzClient, "from_env", Mock(return_value=broker))
    notifications = Mock()
    monkeypatch.setattr(kmw_engine_backend, "notify_order_result", notifications)
    backend = KMWBackend(env_file=None, preset="KMW2", ledger_path=tmp_path / "orders.sqlite3", clock=lambda: NOW)
    backend._load_credentials = lambda: {"MERITZ_ACCOUNT_PRESET": "KMW2", "MERITZ_APP_KEY": "fake-key",
        "MERITZ_APP_SECRET": "fake-secret", "MERITZ_MAC_ADDRESS": "001122334455", "MERITZ_SOXL_EXCHANGE_CODE": "0066"}
    result = backend.mixed_tick(now=NOW, should_continue=lambda: True, force=True)
    assert result["handled"] is True and result["state"] == "DIRECT_ACCEPTED"
    assert result["orders"][0]["order"]["order_type"] == "MARKET"
    assert saved[0]["orders"][next(iter(saved[0]["orders"]))]["status"] == "ACCEPTED"
    notifications.assert_called_once()
    backend.close()
