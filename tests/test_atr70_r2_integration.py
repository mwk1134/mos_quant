"""End-to-end mixed accounting through a CAS store and synthetic broker only."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import atr70_r2 as core
from atr70_r2_adapter import MixedSnapshotStore
from atr70_r2_runtime import run_mixed_tick
from kmw_autotrade import OrderLedger
from us_market_calendar import is_us_equity_trading_day


ET = ZoneInfo("America/New_York")


def bars(basis):
    output = []
    day = date(2024, 1, 2)
    while day.isoformat() <= basis:
        if is_us_equity_trading_day(day):
            close = Decimal("50") + Decimal(len(output)) / 100
            output.append({"date": day.isoformat(), "open": str(close), "high": str(close + 1),
                           "low": str(close - 1), "close": str(close)})
        day += timedelta(days=1)
    return output


class MemoryGitHub:
    def __init__(self):
        self.document = {"KMW2": {"available_cash": 10000, "as_of_date": "2026-10-29",
                                  "broker_sync": {"baseline_date": "2026-10-29"}},
                         "_preset_configs": {"KMW2": {core.MIXED_SWITCH_KEY: True}}}
        self.version = 0
        self.writes = []

    def read(self):
        return deepcopy(self.document), str(self.version)

    def save(self, document, sha, preset):
        assert preset == "KMW2"
        if sha != str(self.version):
            return "conflict"
        self.document = deepcopy(document)
        self.version += 1
        self.writes.append(deepcopy(document))
        return "saved"


class FakeBroker:
    def __init__(self):
        self.now = None
        self.held = 0
        self.cash = Decimal("10000")
        self.orders = []
        self.history = []
        self.calls = []

    def holdings(self):
        return ([{"iscd": "SOXL", "rmnd_dqty": self.held, "seln_able_dqty": self.held,
                  "rmnd_clcd": "1", "loan_dtl_type_code": "0000"}] if self.held else [])

    def orders_today(self):
        # A real 'today' endpoint need not retain yesterday's unfilled auctions.
        session = self.now.strftime("%Y%m%d")
        return deepcopy([row for row in self.orders if row["oder_date"] == session])

    def orders_history(self, start, end):
        return deepcopy([row for row in self.history if start <= row["oder_date"] <= end])

    def reserved_orders(self, *args, **kwargs):
        return []

    def currencies(self):
        return [{"crcd": "USD", "frcr_amt": str(self.cash), "estm_frcr_amt": str(self.cash)}]

    def order_estimate(self):
        return [{"crcd": "USD", "oder_crnc_oder_able_camt": str(self.cash), "othr_crnc_evdn_camt": "0"}]

    def submit_mixed_order(self, **request):
        self.calls.append(deepcopy(request))
        number = str(len(self.calls))
        self.orders.append({"iscd": "SOXL", "oder_date": self.now.strftime("%Y%m%d"), "oder_no": number,
            "odqt": request["quantity"], "cntg_qty": 0, "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0,
            "seln_byov_clcd": "2" if request["side"] == "buy" else "1",
            "ovrs_stck_oder_stat_cls_code": "0",
            "aspr_type_code": {"LOC": "5", "MOC": "7", "MOO": "6", "MARKET": "1"}[request["order_type"]]})
        return {"data": {"oder_no": number, "warn_cls_code": "0"}}

    def fill(self, number, price):
        row = self.orders[number - 1]
        assert row["cntg_qty"] == 0
        quantity = row["odqt"]
        notional = Decimal(str(price)) * quantity
        row["cntg_qty"] = quantity
        buying = row["seln_byov_clcd"] == "2"
        self.held += quantity if buying else -quantity
        self.cash += -notional * Decimal("1.005") if buying else notional * Decimal("0.995")
        self.history.append({"iscd": "SOXL", "oder_date": row["oder_date"], "oder_no": row["oder_no"],
            "cntg_dqty": quantity, "cntg_crnc_prc": str(price), "cntg_camt": str(notional),
            "seln_byov_clcd": row["seln_byov_clcd"]})


def quote(now, price="103", opening="100"):
    return {"source": "meritz_websocket", "realtime": True, "symbol": "SOXL",
            "session_date": now.date().isoformat(), "last_price": price, "regular_open": opening,
            "timestamp": now.isoformat(), "received_at": now.isoformat()}


def execute(store, broker, ledger, now, market_quote=None):
    broker.now = now
    context = store.read(now)
    return run_mixed_tick(broker, context["state"], context, lambda value: store.save(value, now),
                          ledger, now=now, enabled=context["enabled"], account="synthetic-kmw2",
                          exchange_code="0066", quote=market_quote)


def test_initialize_r2_atr_next_open_monthly_rebalance_and_off_through_real_store(tmp_path):
    github, broker = MemoryGitHub(), FakeBroker()
    store = MixedSnapshotStore({}, github=github, market_loader=lambda now, basis: (bars(basis), bars(basis)))
    ledger = OrderLedger(tmp_path / "mix.sqlite3")
    try:
        friday = datetime(2026, 10, 30, 8, 30, tzinfo=ET)
        execute(store, broker, ledger, friday)
        state = github.document["KMW2"][core.STATE_KEY]
        assert state["phase"] == "ACTIVE" and state["atr"]["cash"] == state["r2"]["cash"]
        assert not broker.calls
        execute(store, broker, ledger, friday + timedelta(minutes=1))
        assert [(row["order_type"], row["side"]) for row in broker.calls] == [("LOC", "buy")]
        intraday = friday.replace(hour=10)
        execute(store, broker, ledger, intraday, quote(intraday))
        assert [(row["order_type"], row["side"]) for row in broker.calls] == [("LOC", "buy"), ("MARKET", "buy")]
        broker.fill(2, "103")
        execute(store, broker, ledger, intraday + timedelta(seconds=1), quote(intraday + timedelta(seconds=1)))
        broker.fill(1, "55")
        monday = datetime(2026, 11, 2, 8, 50, tzinfo=ET)
        result = execute(store, broker, ledger, monday)
        assert result["phase"] == "ATR_EXIT"
        assert (broker.calls[-1]["order_type"], broker.calls[-1]["side"]) == ("MOO", "sell")
        broker.fill(3, "80")
        opened = monday.replace(hour=9, minute=31)
        result = execute(store, broker, ledger, opened, quote(opened, "80", "80"))
        assert result["state"] == "REBALANCED"
        state = github.document["KMW2"][core.STATE_KEY]
        atr_nav = Decimal(state["atr"]["cash"])
        r2_nav = Decimal(state["r2"]["cash"]) + sum(lot["shares"] for lot in state["r2"]["lots"]) * 80
        assert atr_nav == r2_nav and "2026-11" in state["rebalance_months"]
        github.document["_preset_configs"]["KMW2"][core.MIXED_SWITCH_KEY] = False
        opened += timedelta(minutes=1)
        execute(store, broker, ledger, opened, quote(opened, "80", "80"))
        assert (broker.calls[-1]["side"], broker.calls[-1]["order_type"]) == ("sell", "LOC")
        assert len(broker.calls) == 4
        broker.fill(4, broker.calls[3]["price"])
        opened += timedelta(minutes=1)
        execute(store, broker, ledger, opened, quote(opened, "110", "80"))
        state = github.document["KMW2"][core.STATE_KEY]
        assert all(lot["shares"] == 0 for leg in ("atr", "r2") for lot in state[leg]["lots"])
        assert broker.held == 0 and len(broker.calls) == 4
        assert Decimal(state["atr"]["cash"]) + Decimal(state["r2"]["cash"]) == broker.cash
        core.validate_state(state)
    finally:
        ledger.db.close()
        store.close()


def test_yesterday_unfilled_auction_expires_when_broker_today_returns_only_today(tmp_path):
    github, broker = MemoryGitHub(), FakeBroker()
    store = MixedSnapshotStore({}, github=github, market_loader=lambda now, basis: (bars(basis), bars(basis)))
    ledger = OrderLedger(tmp_path / "expiry.sqlite3")
    try:
        friday = datetime(2026, 10, 30, 8, 30, tzinfo=ET)
        execute(store, broker, ledger, friday)
        execute(store, broker, ledger, friday + timedelta(minutes=1))
        assert len(broker.calls) == 1
        monday = datetime(2026, 11, 2, 8, 50, tzinfo=ET)
        result = execute(store, broker, ledger, monday)
        state = github.document["KMW2"][core.STATE_KEY]
        assert broker.orders_today() == []
        assert next(iter(state["orders"].values()))["status"] == "CANCELLED"
        assert result["phase"] == "WAITING_OPEN_REBALANCE"
        opened = monday.replace(hour=9, minute=31)
        assert execute(store, broker, ledger, opened, quote(opened, "100", "100"))["state"] == "REBALANCED"
    finally:
        ledger.db.close()
        store.close()
