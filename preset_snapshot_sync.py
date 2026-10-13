"""Reconcile one account into the original web app's fixed GitHub snapshot.

Broker access is read-only. A save replaces only the selected preset and uses
GitHub's content SHA as a compare-and-swap guard. No order method, activation,
global environment mutation, historical price-based fill, or blind write retry
is part of this adapter. Exceptions expose fixed messages and codes only.
"""
from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Mapping
from zoneinfo import ZoneInfo

import requests

from kmw_autotrade import account_id, symbol, validate_account_configuration, validate_preset
from kmw_order_plan import (_KMWPlanTrader, _kmw_config, build_kmw_plan,
                            completed_trading_date, previous_trading_date)
from meritz_api import MeritzClient
from preset_snapshot_refresh import SnapshotRefreshBlocked, refresh_preset_snapshot
from us_market_calendar import is_us_equity_trading_day

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
SNAPSHOT_REPO = "mwk1134/mos_quant"
SNAPSHOT_PATH = "data/positions_snapshots.json"
SNAPSHOT_BRANCH = "main"
CONTENTS_URL = f"https://api.github.com/repos/{SNAPSHOT_REPO}/contents/{SNAPSHOT_PATH}"
SOURCE = f"github:{SNAPSHOT_REPO}/{SNAPSHOT_BRANCH}/{SNAPSHOT_PATH}"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_CAS_ATTEMPTS = 3
# stdout/stderr redirection and the original market-data cache are process
# shared; serialize only calculations, leaving brokerage/GitHub I/O parallel.
_MARKET_CALCULATION_LOCK = RLock()


class SnapshotSyncBlocked(SnapshotRefreshBlocked):
    """Public refresh error, deliberately independent of response bodies."""


def _blocked(code, message):
    raise SnapshotSyncBlocked(message, code=code)


def _day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        _blocked("INVALID_REFRESH_DATE", "A snapshot refresh date is invalid.")


def _number(value, *, positive=False, whole=False):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value).strip())
        if not result.is_finite() or result < 0 or (positive and result <= 0):
            raise ValueError
        if whole and result != result.to_integral_value():
            raise ValueError
        return int(result) if whole else result
    except (ValueError, TypeError, InvalidOperation):
        _blocked("INVALID_BROKER_FACT", "A confirmed brokerage value is invalid.")


def _order_id(value):
    return str(_number(value, positive=True, whole=True))


def _history_day(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
            raise ValueError
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError):
        _blocked("INVALID_BROKER_FACT", "A confirmed execution date is invalid.")


def _execution_timestamp(value, order_day):
    """The broker's Korea execution timestamp must belong to this US session."""
    if value in (None, ""):
        return
    try:
        text = str(value).strip()
        if re.fullmatch(r"\d{10}|\d{12}|\d{14}", text):
            fmt = {10: "%Y%m%d%H", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}[len(text)]
            stamp = datetime.strptime(text, fmt).replace(tzinfo=KST)
        else:
            stamp = datetime.fromisoformat(text)
            stamp = stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp.astimezone(KST)
        if stamp.astimezone(ET).date() != order_day:
            raise ValueError
    except (TypeError, ValueError):
        _blocked("INVALID_BROKER_FACT", "A confirmed execution timestamp does not match its US session.")


def normalize_confirmed_fills(rows):
    """Deduplicate cumulative order totals; requested order prices never enter."""
    if not isinstance(rows, list):
        _blocked("INVALID_BROKER_FACT", "A complete execution history is required.")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            _blocked("INVALID_BROKER_FACT", "An execution history row is invalid.")
        if symbol(row) != "SOXL":
            continue
        quantity = _number(row.get("cntg_dqty"), whole=True)
        if quantity == 0:
            continue  # An unfilled request is not a factual portfolio change.
        day = _history_day(row.get("oder_date"))
        order_id = _order_id(row.get("oder_no"))
        side = {"1": "SELL", "2": "BUY"}.get(str(row.get("seln_byov_clcd")))
        if side is None:
            _blocked("INVALID_BROKER_FACT", "A confirmed execution side is invalid.")
        price = _number(row.get("cntg_crnc_prc"), positive=True)
        _execution_timestamp(row.get("cntg_ymdh"), day)
        fill = {"order_id": order_id, "trade_date": day.isoformat(), "side": side,
                "quantity": quantity, "price": format(price, "f")}
        if row.get("cntg_camt") not in (None, ""):
            notional = _number(row["cntg_camt"], positive=True)
            if abs(notional - price * quantity) > Decimal("0.01"):
                _blocked("INVALID_BROKER_FACT", "Confirmed execution quantity, average and notional disagree.")
            fill["notional"] = format(notional, "f")
        identity = (day.isoformat(), order_id)
        previous = result.get(identity)
        if previous is not None and previous != fill:
            _blocked("CONFLICTING_EXECUTION", "Duplicate brokerage execution totals disagree.")
        result[identity] = fill
    return [result[key] for key in sorted(result)]


def _holding_quantity(rows):
    if not isinstance(rows, list):
        _blocked("INVALID_BROKER_FACT", "A confirmed holdings list is required.")
    quantity = 0
    for row in rows:
        if not isinstance(row, dict):
            _blocked("INVALID_BROKER_FACT", "A confirmed holdings row is invalid.")
        if symbol(row) != "SOXL":
            continue
        if row.get("rmnd_clcd") != "1" or row.get("loan_dtl_type_code") != "0000":
            _blocked("INVALID_BROKER_FACT", "Only ordinary confirmed SOXL holdings are supported.")
        quantity += _number(row.get("rmnd_dqty"), whole=True)
    return quantity


def _completed_auction_order(row, completed_basis):
    """Classify a completed session auction without inventing fills.

    The today endpoint can retain an unfilled opening/closing auction from a completed
    US session with state 0. Only its documented LOO/LOC/MOO/MOC type and a valid,
    completed trading date prove it is outside the current OPEN-order window.
    Missing/unknown types or dates remain subject to the conservative check.
    """
    if (not isinstance(row, dict) or type(completed_basis) is not date
            or not is_us_equity_trading_day(completed_basis)
            or row.get("aspr_type_code") not in ("4", "5", "6", "7")
            or str(row.get("ovrs_stck_oder_stat_cls_code", "")) != "0"):
        return False
    value = row.get("oder_date")
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
            return False
        order_day = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return False
    return order_day <= completed_basis and is_us_equity_trading_day(order_day)


def _open_orders(rows, *, completed_basis=None):
    if completed_basis is not None and (type(completed_basis) is not date
                                       or not is_us_equity_trading_day(completed_basis)):
        _blocked("INVALID_REFRESH_DATE", "A completed brokerage session is required.")
    if not isinstance(rows, list):
        _blocked("INVALID_BROKER_FACT", "A current brokerage order list is required.")
    unresolved = []
    for row in rows:
        if not isinstance(row, dict):
            _blocked("INVALID_BROKER_FACT", "A current brokerage order row is invalid.")
        if symbol(row) != "SOXL":
            continue
        requested = _number(row.get("odqt"), whole=True)
        # /orders/today uses cntg_qty; cntg_dqty belongs to /orders/history.
        # ncnt_qty and oder_able_qty do not establish the remaining quantity.
        filled = _number(row.get("cntg_qty"), whole=True)
        cancelled = _number(row.get("cncl_cnfr_qty", 0), whole=True)
        rectified = _number(row.get("rctf_cnfr_qty", 0), whole=True)
        remaining = requested - filled - cancelled - rectified
        if remaining < 0:
            _blocked("INVALID_BROKER_FACT", "Current order totals disagree.")
        state = str(row.get("ovrs_stck_oder_stat_cls_code", ""))
        if remaining and state == "0":
            if _completed_auction_order(row, completed_basis):
                continue
            unresolved.append({"order_id": _order_id(row.get("oder_no")), "state": "OPEN"})
        elif remaining and state not in {"1", "2", "4"}:
            _blocked("UNRESOLVED_ORDER", "A current brokerage order has an unknown state.")
    return unresolved


def _ledger_facts(path, account):
    """Read-only receipts can identify lots; reading never creates a ledger."""
    path = Path(path)
    if not path.exists():
        return {}, []
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                "SELECT order_date,payload,state,response FROM orders WHERE account=?", (account,)).fetchall()
        finally:
            connection.close()
        mappings, unresolved = {}, []
        for day, payload, state, receipt in rows:
            _day(day)
            if state in {"SENDING", "UNKNOWN"}:
                unresolved.append({"order_date": day, "state": state})
            order = json.loads(payload)
            response = json.loads(receipt) if receipt else None
            if not isinstance(order, dict) or not isinstance(response, dict):
                continue
            data = response.get("data")
            if not isinstance(data, dict) or data.get("oder_no") in (None, "", 0, "0"):
                continue
            if state != "DIRECT_ACCEPTED" or order.get("ticker") != "SOXL":
                continue
            identity = (day, _order_id(data["oder_no"]))
            mapping = {key: deepcopy(order[key]) for key in ("position_key", "round") if key in order}
            mapping["side"] = order.get("side")
            mapping["quantity"] = _number(order.get("quantity"), positive=True, whole=True)
            if identity in mappings and mappings[identity] != mapping:
                _blocked("CONFLICTING_LEDGER", "Broker receipts identify conflicting saved lots.")
            mappings[identity] = mapping
        return mappings, unresolved
    except SnapshotRefreshBlocked:
        raise
    except Exception:
        _blocked("LEDGER_READ_FAILED", "The selected account's order ledger cannot be read.")


def _attach_lot_identifiers(fills, mappings):
    attached = deepcopy(fills)
    for fill in attached:
        mapping = mappings.get((fill["trade_date"], fill["order_id"]))
        if mapping is None:
            continue
        if mapping["side"] != fill["side"] or fill["quantity"] > mapping["quantity"]:
            _blocked("FILL_LEDGER_MISMATCH", "A confirmed execution does not match its saved order.")
        for key in ("position_key", "round"):
            if key in mapping:
                fill[key] = mapping[key]
    return attached


def _historical_buy_rules(document, preset, days):
    """Freeze the original strategy's entry rules at each order day's preopen."""
    snapshot = document[preset]
    config = _kmw_config(document, snapshot, preset=preset)
    rules = {}
    for day_text in sorted(days):
        order_day = _day(day_text)
        basis = previous_trading_date(order_day)
        historical_now = datetime.combine(order_day, time(8), tzinfo=ET)
        with _MARKET_CALCULATION_LOCK, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            trader = _KMWPlanTrader(now=historical_now, expected_basis_date=basis,
                                    initial_capital=float(config["initial_capital"]))
            trader.set_profit_loss_compounding(enabled=True, profit_rate=0.70, loss_rate=0.20,
                                               settlement_delay_days=7, renewal_days=10)
            trader.set_ma_alignment_switch(bool(config["ma_alignment_switch_enabled"]))
            trader.set_seed_increases(config["seed_increases"])
            trader.session_start_date = config["session_start_date"]
            trader.positions, _, _ = trader._snapshot_to_positions_and_state(snapshot)
            trader.positions = [position for position in trader.positions if position["buy_date"].date() < order_day]
            trader.available_cash = float(snapshot["available_cash"])
            trader.processed_seed_dates = set(snapshot["processed_seed_dates"])
            trader.current_round = len(trader.positions) + 1
            trader._restore_compounding_from_snapshot(snapshot)
            recommendation = trader.get_daily_recommendation(skip_simulate=True, preserve_snapshot_shares=True)
        if not isinstance(recommendation, dict) or recommendation.get("error") or recommendation.get("mode") not in {"SF", "AG"}:
            _blocked("BUY_RULES_MISSING", "Original entry-time recommendation rules cannot be verified.")
        if recommendation.get("basis_date") != basis.isoformat():
            _blocked("BUY_RULES_MISSING", "Original entry-time market data is stale.")
        active = recommendation.get("active_config")
        if not isinstance(active, dict):
            _blocked("BUY_RULES_MISSING", "Original entry-time strategy configuration is unavailable.")
        rules[day_text] = {"mode": recommendation["mode"],
                          **{key: active[key] for key in ("buy_threshold", "sell_threshold", "max_hold_days")},
                          "strategy_name": recommendation.get("strategy_name") or active.get("strategy_name") or "기본"}
    return rules


def _needed_buy_rules(document, preset, fills):
    """Use frozen pending/lot rules; avoid replaying already reconciled buys."""
    snapshot = document[preset]
    cursor = snapshot.get("broker_sync", {})
    baseline = _day(cursor.get("baseline_date", snapshot.get("as_of_date")))
    executions = cursor.get("executions", {})
    if not isinstance(executions, dict):
        _blocked("INVALID_SNAPSHOT_DOCUMENT", "Saved brokerage execution checkpoints are invalid.")
    pending = snapshot.get("pending_buy")
    pending = pending if isinstance(pending, dict) else {}
    rules, historical = {}, set()
    for fill in fills:
        if fill["side"] != "BUY":
            continue
        identity = f"{fill['trade_date']}:{fill['order_id']}"
        previous = executions.get(identity, {})
        if not isinstance(previous, dict):
            _blocked("INVALID_SNAPSHOT_DOCUMENT", "A saved brokerage execution checkpoint is invalid.")
        if not previous and _day(fill["trade_date"]) <= baseline:
            continue
        if fill["quantity"] <= _number(previous.get("quantity", 0), whole=True):
            continue
        key = fill.get("position_key") or previous.get("position_key")
        if key is None and pending.get("order_date") == fill["trade_date"]:
            key = f"{pending.get('round')}_{fill['trade_date']}"
        if isinstance(snapshot.get(key), dict) and snapshot[key].get("mode") in {"SF", "AG"}:
            continue  # The pure refresher preserves the existing lot's rules.
        fields = ("mode", "buy_threshold", "sell_threshold", "max_hold_days", "strategy_name")
        if pending.get("order_date") == fill["trade_date"] and all(pending.get(field) is not None for field in fields):
            rules[fill["trade_date"]] = {field: deepcopy(pending[field]) for field in fields}
        else:
            historical.add(fill["trade_date"])
    if historical:
        rules.update(_historical_buy_rules(document, preset, historical))
    return rules


class _GithubSnapshots:
    def __init__(self, token):
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def read(self):
        try:
            response = self.session.get(CONTENTS_URL, headers=self.headers, params={"ref": SNAPSHOT_BRANCH},
                                        timeout=20, allow_redirects=False)
            if response.status_code != 200:
                _blocked("SNAPSHOT_READ_FAILED", "The original web app snapshot cannot be read.")
            metadata = response.json()
            if not isinstance(metadata, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(metadata.get("sha", ""))):
                raise ValueError
            if metadata.get("encoding") != "base64" or not isinstance(metadata.get("content"), str):
                raise ValueError
            content = base64.b64decode("".join(metadata["content"].split()), validate=True)
            if len(content) > MAX_DOCUMENT_BYTES or _blob_sha(content) != metadata["sha"]:
                raise ValueError
            document = json.loads(content)
            if not isinstance(document, dict):
                raise ValueError
            return document, metadata["sha"]
        except SnapshotRefreshBlocked:
            raise
        except Exception:
            _blocked("SNAPSHOT_READ_FAILED", "The original web app snapshot cannot be verified.")

    def save(self, document, sha, preset):
        content = _document_bytes(document)
        expected = _blob_sha(content)
        payload = {"branch": SNAPSHOT_BRANCH, "sha": sha, "content": base64.b64encode(content).decode("ascii"),
                   "message": f"Refresh confirmed {preset} preset holdings"}
        try:
            response = self.session.put(CONTENTS_URL, headers=self.headers, json=payload,
                                        timeout=20, allow_redirects=False)
        except requests.RequestException:
            return self._confirm_ambiguous(expected, document)
        if response.status_code in {409, 422}:
            return "conflict"
        if response.status_code in {200, 201}:
            try:
                result = response.json()
                if result.get("content", {}).get("sha") == expected and re.fullmatch(r"[0-9a-f]{40}", result.get("commit", {}).get("sha", "")):
                    return "saved"
            except Exception:
                pass
            return self._confirm_ambiguous(expected, document)
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            return self._confirm_ambiguous(expected, document)
        _blocked("SNAPSHOT_SAVE_FAILED", "The original web app snapshot save was rejected.")

    def _confirm_ambiguous(self, expected, document):
        try:
            saved, actual = self.read()
            if actual == expected and saved == document:
                return "saved"
        except Exception:
            pass
        _blocked("SNAPSHOT_SAVE_UNCONFIRMED", "Snapshot save could not be confirmed; check its latest state before retrying.")

    def close(self):
        self.session.close()


def _blob_sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()


def _document_bytes(document):
    try:
        content = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError
        return content
    except (TypeError, ValueError):
        _blocked("INVALID_SNAPSHOT_DOCUMENT", "The refreshed snapshot document cannot be saved.")


def _frozen_buy_rules(value):
    """Whitelist the original planner's safe entry metadata without changes."""
    fields = ("mode", "buy_threshold", "sell_threshold", "max_hold_days", "strategy_name")
    try:
        if not isinstance(value, dict) or value.get("mode") not in {"SF", "AG"}:
            raise ValueError
        for key in ("buy_threshold", "sell_threshold"):
            if isinstance(value.get(key), bool) or not Decimal(str(value[key])).is_finite():
                raise ValueError
        _number(value.get("max_hold_days"), positive=True, whole=True)
        label = value.get("strategy_name")
        if not isinstance(label, str) or not label or len(label) > 120 or any(ord(character) < 32 for character in label):
            raise ValueError
        return {key: deepcopy(value[key]) for key in fields}
    except Exception:
        _blocked("BUY_RULES_MISSING", "The next original buy recommendation has invalid entry rules.")


def _candidate(document, *, preset, basis, fills, quantity, unresolved, rules, now):
    candidate = refresh_preset_snapshot(deepcopy(document), preset=preset, basis_date=basis.isoformat(),
                                        fills=fills, broker_quantity=quantity,
                                        unresolved_orders=unresolved, buy_rules=rules)
    if not isinstance(candidate, dict) or {key: value for key, value in candidate.items() if key != preset} != {key: value for key, value in document.items() if key != preset}:
        _blocked("PRESET_MISMATCH", "A refresh attempted to change another preset.")
    with _MARKET_CALCULATION_LOCK, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        plan = build_kmw_plan(candidate, now=now, preset=preset)
    if plan.get("preset") != preset or plan.get("status") != "ready" or plan.get("snapshot_fresh") is not True or plan.get("basis_date") != basis.isoformat():
        _blocked("PLAN_VALIDATION_FAILED", "The refreshed original preset recommendation is not ready.")
    buys = [order for order in plan.get("orders", []) if order.get("side") == "BUY"]
    if len(buys) > 1:
        _blocked("PLAN_VALIDATION_FAILED", "The original preset has more than one pending buy recommendation.")
    snapshot = candidate[preset]
    if buys:
        order = buys[0]
        provided = plan.get("buy_entry_rules")
        if provided is None:
            provided = _historical_buy_rules(candidate, preset, {plan["order_date"]}).get(plan["order_date"])
        frozen = _frozen_buy_rules(provided)
        if plan.get("mode") is not None and frozen["mode"] != plan["mode"]:
            _blocked("BUY_RULES_MISSING", "The next original buy recommendation has inconsistent entry rules.")
        snapshot["pending_buy"] = {"round": order["round"], "quantity": order["quantity"],
                                   "target_price": float(order["price"]), "order_date": plan["order_date"],
                                   "basis_date": plan["basis_date"], **frozen}
    else:
        snapshot.pop("pending_buy", None)
    _document_bytes(candidate)
    return candidate


def refresh_and_load_snapshots(*, preset, env: Mapping[str, str], ledger_path, now):
    """Read confirmed fills, refresh only this preset, CAS-save, return full doc."""
    github = None
    broker_session = None
    try:
        preset = validate_preset(preset)
        isolated = dict(env)
        validate_account_configuration(preset, env=isolated)
        account = account_id(preset, env=isolated)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            _blocked("INVALID_REFRESH_DATE", "A timezone-aware refresh instant is required.")
        token = isolated.get("GITHUB_TOKEN", "")
        if not isinstance(token, str) or not token or any(ord(character) < 33 for character in token):
            _blocked("SNAPSHOT_AUTH_MISSING", "The selected account's original web app access is not configured.")
        basis = completed_trading_date(now.astimezone(ET))
        github = _GithubSnapshots(token)
        document, sha = github.read()
        snapshot = document.get(preset)
        if not isinstance(snapshot, dict) or not snapshot:
            _blocked("SNAPSHOT_MISSING", "The selected original web app preset has no confirmed snapshot.")
        baseline = _day(snapshot.get("broker_sync", {}).get("baseline_date", snapshot.get("as_of_date")))
        if baseline > basis or (basis - baseline).days > 3660:
            _blocked("INVALID_REFRESH_DATE", "The confirmed execution history range is invalid.")
        mappings, ledger_unresolved = _ledger_facts(ledger_path, account)
        if ledger_unresolved:
            _blocked("UNRESOLVED_ORDER", "A prior account submission must be reconciled before snapshot refresh.")
        broker_session = requests.Session()
        broker_session.trust_env = False
        client = MeritzClient.from_env(env=isolated, session=broker_session)
        quantity = _holding_quantity(client.holdings())
        history = client.orders_history(baseline.strftime("%Y%m%d"), basis.strftime("%Y%m%d"))
        fills = _attach_lot_identifiers(normalize_confirmed_fills(history), mappings)
        if any(not baseline <= _day(fill["trade_date"]) <= basis for fill in fills):
            _blocked("INVALID_BROKER_FACT", "Broker execution history exceeds its requested session range.")
        unresolved = _open_orders(client.orders_today(), completed_basis=basis)
        if unresolved:
            _blocked("UNRESOLVED_ORDER", "Open account orders must be resolved before snapshot refresh.")
        for attempt in range(MAX_CAS_ATTEMPTS):
            current_baseline = _day(document[preset].get("broker_sync", {}).get("baseline_date", document[preset].get("as_of_date")))
            if current_baseline < baseline:
                _blocked("CAS_BASELINE_CHANGED", "The original preset baseline changed outside the confirmed history range.")
            rules = _needed_buy_rules(document, preset, fills)
            candidate = _candidate(document, preset=preset, basis=basis, fills=fills, quantity=quantity,
                                   unresolved=unresolved, rules=rules, now=now)
            if candidate == document:
                return candidate, SOURCE
            outcome = github.save(candidate, sha, preset)
            if outcome == "saved":
                return candidate, SOURCE
            if attempt + 1 == MAX_CAS_ATTEMPTS:
                _blocked("SNAPSHOT_CONFLICT", "The original web app snapshot changed repeatedly; refresh it again before ordering.")
            document, sha = github.read()
            if not isinstance(document.get(preset), dict):
                _blocked("SNAPSHOT_MISSING", "The selected original web app preset disappeared during refresh.")
            # Account facts are reused; current GitHub data is recomputed and
            # every unrelated preset is retained exactly from the latest read.
        _blocked("SNAPSHOT_CONFLICT", "The original web app snapshot cannot be updated safely.")
    except SnapshotRefreshBlocked:
        raise
    except Exception:
        _blocked("SNAPSHOT_REFRESH_FAILED", "The original preset refresh failed; verify account and web app state before ordering.")
    finally:
        if github is not None:
            github.close()
        if broker_session is not None:
            broker_session.close()
