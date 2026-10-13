"""Update original preset snapshots before user-scheduled engine runs.

This companion polls loopback status with GET only. It cannot enable an engine
or submit/cancel orders. Its own private status files are independent of engine
state, plans, and order journals; all diagnostics use fixed safe error codes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from zoneinfo import ZoneInfo

import requests

from kmw_autotrade import ROOT, default_env_file, default_ledger_path, read_env, validate_preset
from kmw_order_plan import SUPPORTED_PRESETS
from preset_snapshot_sync import SnapshotRefreshBlocked, refresh_and_load_snapshots
from us_market_calendar import is_us_equity_trading_day

ET = ZoneInfo("America/New_York")
ENGINE_PORTS = {"KMW": 8701, "KMW2": 8703, "JEH": 8704, "JEH2": 8705, "KHW": 8706}
REFRESH_LEAD = timedelta(minutes=10)
MAX_REFRESH_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = (15, 45, 120)
# These failures prove that no candidate snapshot was saved by the attempt.
# Unknown stages, ambiguous saves, malformed facts and configuration failures
# remain terminal. Retrying always starts with new read-only broker/GitHub facts
# and preserves the synchronizer's SHA CAS and verified write receipt.
RETRYABLE_CODES = frozenset({
    "SNAPSHOT_READ_FAILED", "LEDGER_READ_FAILED", "BUY_RULES_MISSING",
    "HOLDINGS_MISMATCH", "UNRESOLVED_ORDER", "SNAPSHOT_CONFLICT", "SNAPSHOT_SAVE_FAILED",
})
PUBLIC_CODES = frozenset({
    "SNAPSHOT_REFRESH_BLOCKED", "INVALID_REFRESH_DATA", "UNRESOLVED_ORDER", "INVALID_BROKER_FACT",
    "INVALID_REFRESH_DATE", "CONFLICTING_EXECUTION", "CONFLICTING_LEDGER", "FILL_LEDGER_MISMATCH",
    "LEDGER_READ_FAILED", "BUY_RULES_MISSING", "BUY_LOT_MISMATCH", "BUY_QUANTITY_MISMATCH",
    "BUY_RULES_MISMATCH", "SELL_LOT_MISMATCH", "SELL_QUANTITY_MISMATCH", "DUPLICATE_FILL",
    "FILL_CHANGED", "LATE_EXECUTION", "HOLDINGS_MISMATCH", "SEED_CHECKPOINT_MISMATCH",
    "FILL_AMOUNT_MISMATCH", "CASH_MISMATCH", "INVALID_BASIS_DATE",
    "SNAPSHOT_READ_FAILED", "SNAPSHOT_SAVE_FAILED", "SNAPSHOT_SAVE_UNCONFIRMED", "SNAPSHOT_CONFLICT",
    "SNAPSHOT_AUTH_MISSING", "SNAPSHOT_MISSING", "CAS_BASELINE_CHANGED", "PRESET_MISMATCH",
    "PLAN_VALIDATION_FAILED", "INVALID_SNAPSHOT_DOCUMENT", "SNAPSHOT_REFRESH_FAILED",
})


def _code(error):
    code = getattr(error, "code", "")
    return code if type(code) is str and code in PUBLIC_CODES else "SNAPSHOT_REFRESH_FAILED"


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware service instant is required")
    return value


def read_engine_status(preset):
    """Only an allowlisted loopback GET; browser actions are never called."""
    preset = validate_preset(preset)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(f"http://127.0.0.1:{ENGINE_PORTS[preset]}/api/status", timeout=5,
                               allow_redirects=False)
        if response.status_code != 200 or not response.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ValueError
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError
        state = response.json()
        if not isinstance(state, dict) or type(state.get("enabled")) is not bool or type(state.get("running")) is not bool:
            raise ValueError
        marker = state.get("preset")
        if marker is None and preset == "KMW":
            marker = state.get("plan", {}).get("preset") if isinstance(state.get("plan"), dict) else None
        if marker != preset:
            raise ValueError
        return state
    except Exception:
        raise ValueError("The selected loopback engine status cannot be verified") from None
    finally:
        session.close()


def refresh_due(state, *, preset, now, lead=REFRESH_LEAD):
    """A disabled, running, holiday, late or distant engine never triggers work."""
    now = _stamp(now)
    if not isinstance(state, dict) or state.get("enabled") is not True or state.get("running") is not False:
        return None
    marker = state.get("preset")
    if marker is None and preset == "KMW" and isinstance(state.get("plan"), dict):
        marker = state["plan"].get("preset")
    if marker != preset:
        return None
    try:
        target = datetime.fromisoformat(state["next_run"])
        _stamp(target)
    except (ValueError, TypeError, KeyError):
        return None
    remaining = target - now
    market = target.astimezone(ET)
    if remaining < timedelta(0) or remaining > lead or not is_us_equity_trading_day(market.date()):
        return None
    if (market.hour, market.minute) >= (9, 30):
        return None
    return target.isoformat()


class PresetSnapshotService:
    def __init__(self, *, presets=SUPPORTED_PRESETS, state_dir=None, refresh=None, status_reader=None):
        self.presets = tuple(validate_preset(preset) for preset in presets)
        if not self.presets or len(set(self.presets)) != len(self.presets):
            raise ValueError("Select distinct registered presets")
        self.state_dir = Path(state_dir) if state_dir is not None else ROOT / ".autotrade" / "snapshot_refresh"
        self.refresh = refresh or refresh_and_load_snapshots
        self.status_reader = status_reader or read_engine_status
        self.attempted = {}
        self._attempt_states = {}
        self._lock = threading.RLock()
        self._workers = {}
        self._completed = []
        for preset in self.presets:
            path = self.state_dir / preset.lower() / "refresh_status.json"
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                target = saved.get("target") if saved.get("preset") == preset else None
                if isinstance(target, str):
                    _stamp(datetime.fromisoformat(target))
                    self.attempted[preset] = target
                    attempt = saved.get("attempt", 1)
                    if type(attempt) is not int or not 1 <= attempt <= MAX_REFRESH_ATTEMPTS:
                        attempt = MAX_REFRESH_ATTEMPTS
                    retry = saved.get("retry_after")
                    try:
                        retry_at = _stamp(datetime.fromisoformat(retry)) if isinstance(retry, str) else None
                    except (ValueError, TypeError):
                        retry_at = None
                    # REFRESHING on disk may have reached GitHub before a crash.
                    # Only a recorded definite failure can resume after restart.
                    retryable = (saved.get("status") == "BLOCKED"
                                 and saved.get("code") in RETRYABLE_CODES
                                 and saved.get("retryable") is True and retry_at is not None)
                    self._attempt_states[preset] = {
                        "target": target, "attempt": attempt,
                        "retry_at": retry_at if retryable else None,
                    }
            except (OSError, ValueError, TypeError):
                pass

    def _record(self, preset, now, *, status, code=None, target=None, basis_date=None,
                attempt=1, retry_after=None):
        record = {"version": 1, "preset": preset, "updated_at": _stamp(now).isoformat(), "status": status}
        record["attempt"] = attempt
        if code is not None:
            record["code"] = code
        if target is not None:
            record["target"] = target
        if basis_date is not None:
            record["basis_date"] = basis_date
        if retry_after is not None:
            record["retryable"] = True
            record["retry_after"] = _stamp(retry_after).isoformat()
        parent = self.state_dir / preset.lower()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        os.chmod(parent, 0o700)
        path = parent / "refresh_status.json"
        temporary = parent / f".refresh_status.{os.getpid()}.{threading.get_ident()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(record, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
        return record

    def refresh_once(self, preset, *, now, target=None, attempt=1):
        preset = validate_preset(preset)
        now = _stamp(now)
        if type(attempt) is not int or not 1 <= attempt <= MAX_REFRESH_ATTEMPTS:
            raise ValueError("A bounded refresh attempt is required")
        started = time.monotonic()
        # Persist the attempt before any GitHub write. A service restart never
        # blindly repeats a possibly accepted write for the same target.
        self._record(preset, now, status="REFRESHING", target=target, attempt=attempt)
        if target is not None:
            with self._lock:
                self.attempted[preset] = target
                self._attempt_states[preset] = {"target": target, "attempt": attempt, "retry_at": None}
        try:
            env = read_env(default_env_file(preset))
            document, _ = self.refresh(preset=preset, env=env, ledger_path=default_ledger_path(preset), now=now)
            basis = document[preset]["as_of_date"]
            if not isinstance(basis, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", basis):
                raise ValueError
            finished = now + timedelta(seconds=max(0, time.monotonic() - started))
            return self._record(preset, finished, status="READY", target=target, basis_date=basis, attempt=attempt)
        except Exception as error:
            finished = now + timedelta(seconds=max(0, time.monotonic() - started))
            code = _code(error)
            retry_after = None
            if target is not None and code in RETRYABLE_CODES and attempt < MAX_REFRESH_ATTEMPTS:
                retry_at = finished + timedelta(seconds=RETRY_BACKOFF_SECONDS[attempt - 1])
                if retry_at < _stamp(datetime.fromisoformat(target)):
                    retry_after = retry_at
            result = self._record(preset, finished, status="BLOCKED", code=code, target=target,
                                  attempt=attempt, retry_after=retry_after)
            if target is not None:
                with self._lock:
                    self._attempt_states[preset] = {"target": target, "attempt": attempt, "retry_at": retry_after}
            return result

    def _run_refresh(self, preset, now, target, attempt):
        try:
            result = self.refresh_once(preset, now=now, target=target, attempt=attempt)
        except Exception:
            # A persistence/worker failure has no confirmed write outcome.
            # Leave the reserved target terminal rather than retrying it.
            result = {"version": 1, "preset": preset, "status": "BLOCKED",
                      "code": "SNAPSHOT_REFRESH_FAILED", "target": target,
                      "attempt": attempt, "updated_at": now.isoformat()}
            with self._lock:
                self._attempt_states[preset] = {"target": target, "attempt": attempt, "retry_at": None}
        with self._lock:
            self._completed.append(result)
            self._workers.pop(preset, None)

    def _drain(self):
        with self._lock:
            records, self._completed = self._completed, []
        return records

    def poll(self, *, now=None, wait=False):
        """Start independent preset workers without waiting for slow accounts.

        ``wait`` is for bounded offline checks; the production serve loop never
        waits for a worker. One in-flight refresh per preset protects retries.
        """
        now = _stamp(now if now is not None else datetime.now(timezone.utc))
        started = time.monotonic()
        records = self._drain()
        launched = []
        for preset in self.presets:
            try:
                state = self.status_reader(preset)
            except Exception:
                continue  # A temporary disconnect never changes engine state.
            current = now + timedelta(seconds=max(0, time.monotonic() - started))
            target = refresh_due(state, preset=preset, now=current)
            if target is None:
                continue
            with self._lock:
                if preset in self._workers:
                    continue
                prior = self._attempt_states.get(preset)
                attempt = 1
                if prior is not None and prior["target"] == target:
                    retry_at = prior["retry_at"]
                    if retry_at is None or current < retry_at or prior["attempt"] >= MAX_REFRESH_ATTEMPTS:
                        continue
                    attempt = prior["attempt"] + 1
                # Reserve before launching: concurrent polls cannot start the
                # same account or retry while its write outcome is pending.
                self.attempted[preset] = target
                self._attempt_states[preset] = {"target": target, "attempt": attempt, "retry_at": None}
                thread = threading.Thread(target=self._run_refresh,
                    args=(preset, current, target, attempt), name=f"snapshot-refresh-{preset.lower()}", daemon=True)
                self._workers[preset] = thread
                try:
                    thread.start()
                except Exception:
                    self._workers.pop(preset, None)
                    continue
                launched.append(thread)
        if wait:
            for thread in launched:
                thread.join(timeout=5)
        return records + self._drain()

    def serve(self, *, stop_event=None, poll_seconds=15):
        if poll_seconds <= 0 or poll_seconds > 60:
            raise ValueError("Use a polling interval of 1 to 60 seconds")
        stop = stop_event or threading.Event()
        while not stop.is_set():
            self.poll()
            stop.wait(poll_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true", help="Poll allowlisted local engines before their scheduled runs")
    mode.add_argument("--once", action="store_true", help="Refresh one original preset using read-only brokerage facts")
    parser.add_argument("--preset", choices=SUPPORTED_PRESETS, help="Required for an explicit one-time refresh")
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args(argv)
    if args.once and args.preset is None:
        parser.error("--once requires --preset")
    service = PresetSnapshotService(presets=(args.preset,) if args.preset else SUPPORTED_PRESETS)
    if args.once:
        result = service.refresh_once(args.preset, now=datetime.now(timezone.utc))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "READY" else 1
    try:
        service.serve(poll_seconds=args.poll_seconds)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
