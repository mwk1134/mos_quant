"""Serialize this repository's Meritz requests across accounts and processes."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import threading
import time


# Keep the aggregate of all local accounts below one request per second,
# including authentication and background reads, with timing headroom.
REQUEST_INTERVAL_SECONDS = 1.1
DEFAULT_GATE_PATH = Path(__file__).resolve().parent / ".autotrade" / "meritz_requests.lock"
_THREAD_LOCK = threading.Lock()


class MeritzPacingError(RuntimeError):
    """The shared gate failed before a request could be sent."""


class MeritzRequestPacer:
    """A lazy request context; hold its locks through the transport call.

    Only the last monotonic dispatch timestamp is stored for auditing. Separate
    instances and processes using the same path share one limit, regardless of
    credentials. Each entry waits a fresh full interval after acquiring both
    locks, so slow persistence or scheduling before an HTTP call cannot create
    a burst. A future timestamp may belong to the preceding boot.
    """

    def __init__(self, path=None, *, interval=REQUEST_INTERVAL_SECONDS, clock=None, sleep=None):
        if isinstance(interval, bool) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("Meritz request interval must be positive and finite")
        self.path = Path(path) if path is not None else DEFAULT_GATE_PATH
        self.interval = interval
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep

    def _prepare(self, handle):
        raw = handle.read(128).strip()
        previous = float(raw) if raw else None
        if previous is not None and (not math.isfinite(previous) or previous < 0):
            raise ValueError("Invalid request timestamp")
        now = self._clock()
        if not math.isfinite(now) or now < 0:
            raise ValueError("Invalid monotonic clock")
        # The preceding transport has finished before these locks are released.
        # Never use a possibly stale persisted start time to shorten this wait.
        deadline = now + self.interval
        while True:
            now = self._clock()
            if not math.isfinite(now) or now < 0:
                raise ValueError("Invalid monotonic clock")
            delay = deadline - now
            if delay <= 0:
                break
            self._sleep(delay)
        handle.seek(0)
        handle.write(format(now, ".17g"))
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())

    @contextmanager
    def __call__(self):
        # Client construction and imports must not create directories or files.
        with _THREAD_LOCK:
            handle = None
            descriptor = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                handle = os.fdopen(descriptor, "r+", encoding="ascii")
                descriptor = None
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._prepare(handle)
            except BaseException as exc:
                if handle is not None:
                    handle.close()
                elif descriptor is not None:
                    os.close(descriptor)
                if isinstance(exc, (OSError, ValueError, OverflowError)):
                    raise MeritzPacingError("Meritz request pacing is unavailable; no request was sent") from None
                raise
            try:
                yield
            finally:
                # Closing releases flock even when the HTTP call raises.
                handle.close()


default_request_pacer = MeritzRequestPacer()
