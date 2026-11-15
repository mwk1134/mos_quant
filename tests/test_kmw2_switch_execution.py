"""Real KMW2 switch/store/planner/runtime/runner paths, with synthetic transport."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

import atr70_r2 as core
import atr70_r2_runtime
import kmw_engine_backend
import preset_snapshot_sync
from kmw_autotrade import OrderLedger, account_id
from kmw_engine_backend import KMWBackend
from kmw_order_plan import _KMWPlanTrader
from meritz_api import SubmissionStopped
from test_kmw2_hybrid import Broker, Github, NOW, buy_state, document, history, intent


CREDENTIALS = {"MERITZ_ACCOUNT_PRESET": "KMW2", "MERITZ_APP_KEY": "synthetic-switch-key",
               "MERITZ_APP_SECRET": "synthetic-switch-secret", "MERITZ_MAC_ADDRESS": "001122334455",
               "MERITZ_SOXL_EXCHANGE_CODE": "0066", "MERITZ_SOXL_PRICE_TICK": "0.01",
               "GITHUB_TOKEN": "synthetic-github-token"}
ENTRY_RULES = {"mode": "SF", "strategy_name": "기본", "buy_threshold": 3.5,
               "sell_threshold": 1.1, "max_hold_days": 35}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No real network, financial service or notification transport")
    monkeypatch.setattr("requests.Session.request", forbidden)
    monkeypatch.setattr("requests.get", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def recommendation(trader, **kwargs):
    assert kwargs == {"skip_simulate": True, "preserve_snapshot_shares": True}
    basis = trader._expected_basis_date.isoformat()
    return {"basis_date": basis, "buy_order_date": trader._get_next_trading_day(basis),
            "mode": "SF", "strategy_name": "기본", "can_buy": True,
            "buy_price": 101.0, "next_buy_amount": 303.0, "next_buy_round": trader.current_round,
            "active_config": {"buy_threshold": 3.5, "sell_threshold": 1.1, "max_hold_days": 35},
            "sell_recommendations": []}


class MemoryGithub(Github):
    def __init__(self, source, *, concurrent=None):
        super().__init__(source)
        self.concurrent = concurrent
        self.armed = False
        self.injected = False

    def save(self, candidate, sha, preset):
        result = super().save(candidate, sha, preset)
        if (result == "saved" and self.concurrent is not None and not self.injected
                and candidate["KMW2"].get("strategyTransition", {}).get("mode") == "DONGPA"):
            self.armed = True
        return result

    def read(self):
        if self.armed:
            self.armed = False
            self.injected = True
            self.concurrent(self.document)
        return super().read()

    def close(self):
        pass


class ExecutionBroker(Broker):
    def __init__(self):
        super().__init__()
        self.transport_calls = []
        self.on_quote = None

    def exchanges(self):
        return [{"ovrs_exch_vndr_idno": "0066", "onln_yn": "Y", "ovrs_stck_exch_idno": "AX"}]

    def quote(self, *args, **kwargs):
        if self.on_quote is not None:
            callback, self.on_quote = self.on_quote, None
            callback()
        return {"iscd": "SOXL", "shnu_aspr_unit": "0.01", "seln_aspr_unit": "0.01",
                "shnu_oder_qty_unit": 1, "seln_oder_qty_unit": 1}

    def _submit(self, method, parameters):
        self.transport_calls.append((method, deepcopy(parameters)))
        number = str(900 + len(self.transport_calls))
        kind = {"MARKET": "1", "LOC": "5", "MOO": "6", "MOC": "7"}[parameters["order_type"]]
        self.today.append({"iscd": "SOXL", "oder_date": NOW.strftime("%Y%m%d"), "oder_no": number,
            "orgl_oder_no": "0", "odqt": parameters["quantity"], "cntg_qty": 0,
            "cncl_cnfr_qty": 0, "rctf_cnfr_qty": 0,
            "seln_byov_clcd": "2" if parameters["side"] == "buy" else "1",
            "ovrs_stck_oder_stat_cls_code": "0", "aspr_type_code": kind,
            "ovrs_exch_vndr_idno": "0066", "ovrs_stck_oder_prc": parameters["price"]})
        return {"rsp_cd": "0001", "data": {"warn_cls_code": "0", "oder_no": number}}

    def submit_order(self, **parameters):
        return self._submit("submit_order", parameters)

    def submit_mixed_order(self, **parameters):
        return self._submit("submit_mixed_order", parameters)


class ClientSession:
    """Independent submission guards, just as separate native client instances."""
    def __init__(self, broker, before_submission=None):
        self.broker, self.before_submission = broker, before_submission

    def __getattr__(self, name):
        return getattr(self.broker, name)

    def _submit(self, name, parameters):
        if self.before_submission is not None and not self.before_submission():
            raise SubmissionStopped("Synthetic native dispatch guard stopped")
        return getattr(self.broker, name)(**parameters)

    def submit_order(self, **parameters):
        return self._submit("submit_order", parameters)

    def submit_mixed_order(self, **parameters):
        return self._submit("submit_mixed_order", parameters)


class Feed:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def latest(self):
        return None

    def close(self):
        pass


@pytest.fixture
def setup_backend(tmp_path, monkeypatch):
    instances = []

    def setup(*, source=None, broker=None, concurrent=None):
        github = MemoryGithub(document() if source is None else source, concurrent=concurrent)
        broker = ExecutionBroker() if broker is None else broker
        monkeypatch.setattr(preset_snapshot_sync, "_GithubSnapshots", lambda token: github)
        monkeypatch.setattr(kmw_engine_backend, "load_snapshots",
            lambda **kwargs: (deepcopy(github.document), "synthetic-memory-snapshot"))
        monkeypatch.setattr(kmw_engine_backend.MeritzClient, "from_env",
            lambda **kwargs: ClientSession(broker, kwargs.get("before_submission")))
        notifier = Mock()
        notifier.send.return_value = 1
        monkeypatch.setattr(kmw_engine_backend, "configured_notifier", lambda **kwargs: notifier)
        monkeypatch.setattr(atr70_r2_runtime, "MeritzTradeFeed", Feed)
        monkeypatch.setattr(_KMWPlanTrader, "get_daily_recommendation", recommendation)
        engine = KMWBackend(env_file=None, preset="KMW2", ledger_path=tmp_path / "switch.sqlite3", clock=lambda: NOW)
        engine._load_credentials = lambda: deepcopy(CREDENTIALS)
        instances.append(engine)
        return engine, github, broker

    yield setup
    for engine in instances:
        engine.close()


def receipts(engine):
    ledger = OrderLedger(engine.ledger_path)
    try:
        return ledger.rows(account_id("KMW2", env=CREDENTIALS))
    finally:
        ledger.db.close()


def start_off(engine):
    result = engine.mixed_tick(now=NOW, force=True, should_continue=lambda: True)
    assert result.get("state") == "DIRECT_ACCEPTED", result
    assert result["allow_legacy_schedule"] is True and result["dongpa_transition_started"] is True
    assert len(result["orders"]) == 1 and result["orders"][0]["order"]["side"] == "SELL"
    plan = result["plan"]
    assert plan["dongpa_transition_managed"] is True and plan["atr70_r2_managed"] is False
    assert [(order["side"], order["quantity"]) for order in plan["orders"]] == [("BUY", 3)]
    return result, plan


def test_first_off_preserves_r2_exit_and_executes_independent_dongpa_buy_with_frozen_entry_rules(setup_backend):
    engine, github, broker = setup_backend()
    original_lots = deepcopy(github.document["KMW2"]["atr70_r2"]["r2"]["lots"])
    result, plan = start_off(engine)
    assert github.document["KMW2"]["atr70_r2"]["r2"]["lots"] == original_lots
    assert broker.transport_calls[0][0] == "submit_mixed_order"
    assert broker.transport_calls[0][1]["side"] == "sell" and broker.transport_calls[0][1]["quantity"] == 5
    assert plan["snapshot_positions"] == []
    assert github.document["KMW2"]["available_cash"] == 73503.5

    outcome = engine.execute(plan, NOW, should_continue=lambda: True)
    assert outcome["state"] == "DIRECT_ACCEPTED", outcome
    assert [method for method, _ in broker.transport_calls] == ["submit_mixed_order", "submit_order"]
    submitted = broker.transport_calls[1][1]
    assert (submitted["side"], submitted["quantity"], submitted["price"], submitted["order_type"]) == ("buy", 3, "101.00", "LOC")
    rows = receipts(engine)
    legacy = next(row for row in rows if not row["order"].get("intent_id"))
    assert legacy["state"] == "DIRECT_ACCEPTED" and legacy["order"]["entry_rules"] == ENTRY_RULES
    assert github.document["KMW2"]["atr70_r2"]["r2"]["lots"] == original_lots
    assert all(call[1]["symbol"] == "SOXL" for call in broker.transport_calls)


def test_switch_on_during_hybrid_preflight_stops_new_dongpa_buy_but_preserves_original_exit(setup_backend):
    engine, github, broker = setup_backend()
    _, plan = start_off(engine)
    old_lots = deepcopy(github.document["KMW2"]["atr70_r2"]["r2"]["lots"])
    broker.on_quote = lambda: github.document["_preset_configs"]["KMW2"].update(atr70_r2_mix_enabled=True)
    outcome = engine.execute(plan, NOW, should_continue=lambda: True)
    assert outcome["state"] == "STOPPED", outcome
    assert len(broker.transport_calls) == 1 and broker.transport_calls[0][1]["side"] == "sell"
    assert github.document["KMW2"]["atr70_r2"]["r2"]["lots"] == old_lots
    assert github.document["_preset_configs"]["KMW2"]["atr70_r2_mix_enabled"] is True
    legacy = next(row for row in receipts(engine) if not row["order"].get("intent_id"))
    assert legacy["state"] == "NOT_SENT" and legacy["order"]["entry_rules"] == ENTRY_RULES


def test_archived_returned_dongpa_plan_also_stops_new_buy_when_switch_changes_during_preflight(setup_backend):
    state = buy_state()
    sale = intent("SELL", "2026-11-13", number="12", price="110")
    state = core.register_order(state, sale)
    state = core.reconcile_fills(state, [{"order_id": sale["intent_id"], "broker_order_id": "12",
        "cumulative_quantity": 5, "cumulative_notional": "550", "cumulative_fee": "2.75",
        "fee_source": "CONFIGURED_RESERVE", "status": "FILLED"}])
    broker = ExecutionBroker()
    broker.history.append(history("SELL", "12", "2026-11-13", 5, 110))
    broker.quantity, broker.orderable = 0, "74051.29"
    engine, github, broker = setup_backend(source=document(state), broker=broker)
    transition = engine.mixed_tick(now=NOW, force=True, should_continue=lambda: True)
    assert transition["state"] == "DONGPA_RESUMED", transition
    assert "atr70_r2" not in github.document["KMW2"]
    assert github.document["KMW2"]["strategyHistory"]
    plan = transition["plan"]
    assert plan["dongpa_transition_managed"] is True and len(plan["orders"]) == 1
    broker.on_quote = lambda: github.document["_preset_configs"]["KMW2"].update(atr70_r2_mix_enabled=True)
    outcome = engine.execute(plan, NOW, should_continue=lambda: True)
    assert outcome["state"] == "STOPPED", outcome
    assert broker.transport_calls == []
    assert receipts(engine)[0]["state"] == "NOT_SENT"


@pytest.mark.parametrize("account_changed", [True, False])
def test_same_account_change_between_sync_and_read_defers_but_other_preset_change_is_preserved(setup_backend, account_changed):
    def concurrent(document):
        target = document["KMW2"] if account_changed else document["KMW"]
        target["concurrent_revision"] = "new-user-value"

    engine, github, broker = setup_backend(concurrent=concurrent)
    result = engine.mixed_tick(now=NOW, force=True, should_continue=lambda: True)
    assert github.injected
    if account_changed:
        assert result == {"handled": True, "state": "WAITING_STRATEGY_REFRESH", "orders": [],
                          "allow_legacy_schedule": False}
        assert broker.transport_calls == []
        assert github.document["KMW2"]["concurrent_revision"] == "new-user-value"
    else:
        assert result["state"] == "DIRECT_ACCEPTED" and result["allow_legacy_schedule"] is True
        assert result["dongpa_transition_started"] is True
        assert len(broker.transport_calls) == 1
        assert github.document["KMW"]["concurrent_revision"] == "new-user-value"
