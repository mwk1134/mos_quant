#!/usr/bin/env python3
"""Local KMW engine. First boot is inactive; live activation is a user action."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable

from kmw_autotrade import ET, KST, ROOT, next_run
from kmw_order_plan import SUPPORTED_PRESETS
from us_market_calendar import is_us_equity_trading_day


class EngineStateError(ValueError):
    pass


class EngineBusyError(EngineStateError):
    pass


def validate_time(at: str) -> str:
    if not isinstance(at, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
        raise EngineStateError("주문 시간은 한국시간 HH:MM 형식으로 입력하세요.")
    return at


def review_id(plan: dict | None) -> str | None:
    if not isinstance(plan, dict) or plan.get("status") != "ready":
        return None
    fields = ("preset", "order_date", "basis_date", "snapshot_date", "snapshot_fresh",
              "orders", "snapshot_positions", "mode", "buy_budget")
    reviewed = {key: plan.get(key) for key in fields}
    return hashlib.sha256(json.dumps(reviewed, sort_keys=True).encode()).hexdigest()


class KMWEngine:
    def __init__(self, backend, state_file: Path | str | None = None,
                 clock: Callable[[], datetime] | None = None, *, preset: str = "KMW"):
        if preset not in SUPPORTED_PRESETS:
            raise EngineStateError("지원하지 않는 자동매매 프리셋입니다.")
        backend_preset = getattr(backend, "preset", None)
        if isinstance(backend_preset, str) and backend_preset != preset:
            raise EngineStateError("엔진 프리셋과 계좌 연결 프리셋이 다릅니다.")
        self.preset = preset
        self.backend = backend
        self.state_file = Path(state_file) if state_file is not None else ROOT / ".autotrade" / (
            "engine.json" if preset == "KMW" else f"engine_{preset.lower()}.json")
        self.clock = clock or (lambda: datetime.now(KST))
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker = None
        self._running = False
        self._generation = 0
        self._broker_status = None
        self._last_poll = 0.0
        self._state = self._read_state()
        self._next = self._next_target(self.clock())

    def _read_state(self):
        default = {"version": 1, "preset": self.preset, "enabled": False, "at": "17:01", "phase": "INACTIVE",
                   "plan": None, "last_run": None, "scheduled_days": [], "events": []}
        if not self.state_file.exists():
            return default
        try:
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("version") != 1:
                raise ValueError()
            if saved.get("preset", "KMW") != self.preset:
                raise ValueError()
            if type(saved.get("enabled")) is not bool:
                raise ValueError()
            validate_time(saved.get("at"))
            if not isinstance(saved.get("scheduled_days"), list) or not isinstance(saved.get("events"), list):
                raise ValueError()
            default.update({key: saved[key] for key in default if key in saved})
            # A process restart never claims that a previous call is still running.
            default["phase"] = "WAITING" if default["enabled"] else "INACTIVE"
            return default
        except (OSError, ValueError, TypeError):
            raise EngineStateError("엔진 설정 파일을 확인할 수 없습니다. 주문 원장을 보존하고 설정을 확인하세요.") from None

    def _commit(self, **changes):
        state = deepcopy(self._state)
        state.update(changes)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(self.state_file.name + f".{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(state, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_file)
        except OSError:
            # Failure to save activation or an attempt must precede any order call.
            raise EngineStateError("엔진 상태를 저장하지 못해 실행을 중단했습니다.") from None
        self._state = state

    def _event(self, text, **changes):
        events = self._state["events"][-49:] + [{"at": self.clock().isoformat(), "text": text}]
        self._commit(events=events, **changes)

    def _next_target(self, now):
        at = datetime.strptime(self._state["at"], "%H:%M").time()
        target = next_run(now, at)
        # Retiming or restarting cannot repeat an automatic session already tried.
        while (not is_us_equity_trading_day(target.astimezone(ET).date())
               or target.astimezone(ET).date().isoformat() in self._state["scheduled_days"]):
            target += timedelta(days=1)
        return target

    def status(self):
        with self._lock:
            state = deepcopy(self._state)
            account_ready = True
            if callable(getattr(type(self.backend), "validate_account_configuration", None)):
                try:
                    account_ready = self.backend.validate_account_configuration().get("ready") is True
                except Exception:
                    account_ready = False
            state.update(running=self._running, timezone="Asia/Seoul",
                         preset=self.preset, account_ready=account_ready,
                         next_run=self._next.isoformat() if state["enabled"] else None,
                         review_id=review_id(state["plan"]), broker_status=deepcopy(self._broker_status))
            return state

    def configure(self, at):
        at = validate_time(at)
        with self._lock:
            self._event(f"주문 시간을 한국시간 {at}로 저장했습니다.", at=at)
            self._next = self._next_target(self.clock())
        self._wake.set()
        return self.status()

    def enable(self):
        if callable(getattr(type(self.backend), "validate_account_configuration", None)):
            try:
                if self.backend.validate_account_configuration().get("ready") is not True:
                    raise ValueError()
            except Exception:
                raise EngineStateError(f"{self.preset} 계좌의 API 설정을 먼저 확인하세요.") from None
        with self._lock:
            if not self._state["enabled"]:
                self._event("사용자가 실제 주문 자동 실행을 활성화했습니다.", enabled=True, phase="WAITING")
                self._generation += 1
                self._next = self._next_target(self.clock())
        self._wake.set()
        return self.status()

    def disable(self):
        with self._lock:
            self._event("자동 실행을 중지했습니다. 이미 접수된 주문은 유지됩니다.",
                        enabled=False, phase="STOPPING" if self._running else "INACTIVE")
            self._generation += 1
        self._wake.set()
        return self.status()

    def preview(self):
        if not self._run_lock.acquire(blocking=False):
            raise EngineBusyError("주문 처리 중입니다. 완료 후 추천을 새로고침하세요.")
        try:
            plan = self._latest_plan(now=self.clock(), display=True)
            self._validate_plan_preset(plan)
            with self._lock:
                self._event(f"최신 웹앱 {self.preset} 저장 데이터로 추천을 새로 계산했습니다.", plan=plan)
            return self.status()
        finally:
            self._run_lock.release()

    def _latest_plan(self, *, now, display=False):
        # Concrete upgraded backends refresh factual fills before planning.
        # Legacy/fake adapters retain their original credential-free interface.
        if callable(getattr(type(self.backend), "refresh_preview", None)):
            return self.backend.refresh_preview(now=now)
        if display and self.preset != "KMW":
            return self.backend.preview(now=now, allow_stale_snapshot=True)
        return self.backend.preview(now=now)

    def _validate_plan_preset(self, plan):
        if not isinstance(plan, dict) or plan.get("preset", self.preset if plan.get("status") == "skipped" else None) != self.preset:
            raise EngineStateError("추천 주문표의 프리셋이 선택한 계좌와 다릅니다.")

    def run_now(self, expected_review_id=None):
        if not isinstance(expected_review_id, str) or not expected_review_id:
            raise EngineStateError("추천을 새로고침하고 주문 내역을 확인한 뒤 실행하세요.")
        with self._lock:
            if not self._state["enabled"]:
                raise EngineStateError("실제 주문 자동 실행을 먼저 활성화하세요.")
        return self._execute("manual", self.clock(), expected_review_id=expected_review_id)

    def _may_continue(self, generation):
        with self._lock:
            return self._state["enabled"] and self._generation == generation and not self._stop.is_set()

    def _execute(self, trigger, now, expected_review_id=None):
        if not self._run_lock.acquire(blocking=False):
            raise EngineBusyError("다른 실행이 진행 중입니다. 중복 실행은 차단됩니다.")
        try:
            with self._lock:
                if not self._state["enabled"] or self._stop.is_set():
                    raise EngineStateError("자동 실행이 비활성 상태입니다.")
                day = now.astimezone(ET).date().isoformat()
                if trigger == "scheduled" and day in self._state["scheduled_days"]:
                    return {"state": "ALREADY_ATTEMPTED"}
                attempted = self._state["scheduled_days"]
                # A user-triggered batch also replaces that day's automatic run.
                # A further explicit manual review remains possible; per-order
                # brokerage receipts still prevent duplicate submissions.
                attempted = sorted(set(attempted + [day]))[-90:]
                self._event(f"{self.preset} 추천과 실제 계좌의 주문 가능 상태를 확인합니다.",
                            phase="RUNNING", scheduled_days=attempted)
                self._running = True
                generation = self._generation
            execute_started = False
            try:
                if not is_us_equity_trading_day(now.astimezone(ET).date()):
                    result = {"state": "SKIPPED", "error_code": "MARKET_CLOSED", "orders": [],
                              "error": "미국 증시 휴장일에는 주문을 전송하지 않습니다."}
                else:
                    plan = self._latest_plan(now=now)
                    self._validate_plan_preset(plan)
                    with self._lock:
                        self._commit(plan=plan)
                    if expected_review_id is not None and review_id(plan) != expected_review_id:
                        result = {"state": "BLOCKED", "error_code": "PLAN_CHANGED",
                                  "error": "확인한 주문표가 변경되었습니다. 최신 내역을 다시 확인하세요.", "orders": []}
                    elif not self._may_continue(generation):
                        result = {"state": "STOPPED", "orders": [], "error": "사용자가 실행을 중지했습니다."}
                    elif plan.get("status") != "ready":
                        result = {"state": "SKIPPED", "orders": [], "error": plan.get("reason", "오늘 주문 대상이 없습니다.")}
                    else:
                        execute_started = True
                        result = self.backend.execute(plan, now=self.clock(),
                                                      should_continue=lambda: self._may_continue(generation))
            except Exception:
                # Raw HTTP errors may contain tokens/URLs: publish only a fixed message.
                result = {"state": "UNKNOWN" if execute_started else "ERROR", "error_code": "RUN_FAILED", "orders": [],
                          "error": "추천 계산 또는 실행 확인에 실패했습니다. 상태를 확인하고 직접 재검토하세요."}
            with self._lock:
                result = dict(result, trigger=trigger, finished_at=self.clock().isoformat())
                uncertain = result.get("state") == "UNKNOWN"
                enabled = self._state["enabled"] and not uncertain
                if uncertain:
                    self._generation += 1
                phase = "INACTIVE" if not enabled else "BLOCKED" if result.get("state") in {"BLOCKED", "ERROR"} else "WAITING"
                summary = result.get("error") or f"{self.preset} 실행 결과: {result.get('state', 'ERROR')}"
                self._event(summary, last_run=result, phase=phase, enabled=enabled)
            return result
        finally:
            with self._lock:
                self._running = False
                self._next = self._next_target(self.clock())
            self._run_lock.release()

    def tick(self, now=None):
        """One scheduler tick, also used with a fake backend in offline tests."""
        now = now or self.clock()
        with self._lock:
            due = self._state["enabled"] and now >= self._next
        if due:
            try:
                return self._execute("scheduled", now)
            except EngineBusyError:
                # A read-only preview can hold the lock at the appointed second.
                # Wait for it to finish rather than losing today's scheduled run.
                return None
        return None

    def poll_status(self):
        with self._lock:
            if not self._state["enabled"] or self._running:
                return None
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            result = self.backend.poll_status(now=self.clock())
            with self._lock:
                self._broker_status = result
            return result
        finally:
            self._run_lock.release()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
                if time.monotonic() - self._last_poll >= 60:
                    self.poll_status()
                    self._last_poll = time.monotonic()
            except Exception:
                # Stop dispatch if persistence or an unexpected worker operation fails.
                with self._lock:
                    self._generation += 1
                    self._state["enabled"] = False
                    self._state["phase"] = "ERROR"
                    try:
                        self._commit(enabled=False, phase="ERROR")
                    except EngineStateError:
                        pass
            self._wake.wait(1)
            self._wake.clear()

    def start_worker(self):
        if self._worker is not None:
            raise EngineStateError("엔진이 이미 실행 중입니다.")
        self._worker = threading.Thread(target=self._loop, name="kmw-scheduler", daemon=True)
        self._worker.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="사용자가 활성화하는 KMW 자동매매 엔진")
    parser.add_argument("command", choices=("serve", "status", "preview"))
    parser.add_argument("--preset", choices=SUPPORTED_PRESETS, default="KMW")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    args.port = args.port if args.port is not None else {"KMW": 8701, "KMW2": 8703, "JEH": 8704}[args.preset]
    from kmw_engine_backend import KMWBackend
    backend_kwargs = {"preset": args.preset}
    if args.env_file is not None:
        backend_kwargs["env_file"] = args.env_file
    if args.ledger is not None:
        backend_kwargs["ledger_path"] = args.ledger
    backend = KMWBackend(**backend_kwargs)
    if args.command == "preview":
        # CLI preview must not overwrite a running service's activation journal.
        print(json.dumps(backend.preview(), ensure_ascii=False, indent=2))
        return 0
    engine = KMWEngine(backend, args.state_file, preset=args.preset)
    args.state_file = engine.state_file
    if args.command == "status":
        print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
        return 0
    # A file lock also prevents two dashboard ports dispatching the same engine.
    import fcntl
    from kmw_engine_dashboard import create_server
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    with args.state_file.with_suffix(".lock").open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise EngineStateError("같은 설정의 엔진이 이미 실행 중입니다.") from None
        server = create_server(engine, host="127.0.0.1", port=args.port)
        engine.start_worker()
        print(f"{args.preset} 관리 화면: http://127.0.0.1:{server.server_address[1]} · "
              f"{'활성' if engine.status()['enabled'] else '비활성'}", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            engine.close()
            server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EngineStateError as exc:
        print(str(exc))
        raise SystemExit(1)
