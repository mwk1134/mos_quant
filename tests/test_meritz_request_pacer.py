"""Offline checks for one shared request limit across clients and processes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from pathlib import Path
import stat
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from meritz_api import MeritzAPIError, MeritzClient
from meritz_request_pacer import MeritzPacingError, MeritzRequestPacer, REQUEST_INTERVAL_SECONDS


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class RollingSecondBroker:
    """Fake gateway allowing one outbound request in any rolling second."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self._lock = threading.Lock()

    def request(self, method, url, **kwargs):
        with self._lock:
            started = self.clock()
            limited = any(0 <= started - call["started"] < 1.0 for call in self.calls)
            code = "EGW00200" if limited else None
            self.calls.append({"started": started, "method": method, "url": url, "code": code})
            if limited:
                body = {"rsp_cd": "EGW00200", "rsp_msg": "synthetic rolling-second limit"}
            elif url.endswith("/oauth2/token"):
                body = {"access_token": "synthetic-" + kwargs["data"]["client_id"],
                        "token_type": "Bearer", "expires_in": 3600}
            elif method == "GET":
                body = {"rsp_cd": "5820", "tr_cont": "0"}
            else:
                body = {"rsp_cd": "0001", "data": {"warn_cls_code": "0", "oder_no": len(self.calls)}}
            response = Mock(status_code=500 if limited else 200)
            response.json.return_value = body
            return response


class RequestPacerTests(unittest.TestCase):
    def test_constructor_and_client_creation_have_no_filesystem_or_network_effect(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "not-created" / "requests.lock"
            pacer = MeritzRequestPacer(path)
            session = Mock()
            with patch("meritz_api.default_request_pacer", pacer):
                MeritzClient("synthetic-key", "synthetic-secret", "001122AABBCC", session=session)
            self.assertFalse(path.parent.exists())
            session.request.assert_not_called()

    def test_separate_instances_share_timestamp_and_store_no_credentials(self):
        clock = FakeClock()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            first = MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)
            second = MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)
            starts = []
            for pacer in (first, second, first):
                with pacer():
                    starts.append(clock())
            self.assertEqual(REQUEST_INTERVAL_SECONDS, 1.1)
            self.assertAlmostEqual(starts[1] - starts[0], 1.1)
            self.assertAlmostEqual(starts[2] - starts[1], 1.1)
            self.assertEqual(float(path.read_text()), starts[-1])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_different_clients_share_pacing_for_authentication_reads_and_writes(self):
        clock = FakeClock()
        starts = []
        bodies = iter([
            {"access_token": "synthetic-token", "token_type": "Bearer", "expires_in": 3600},
            {"rsp_cd": "5820", "tr_cont": "0"},
            {"access_token": "other-token", "token_type": "Bearer", "expires_in": 3600},
            {"rsp_cd": "0001", "data": {"warn_cls_code": "0", "oder_no": 42}},
        ])

        def transport(*args, **kwargs):
            starts.append(clock())
            response = Mock(status_code=200)
            response.json.return_value = next(bodies)
            return response

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            sessions = [Mock(), Mock()]
            clients = []
            for index, session in enumerate(sessions):
                session.request.side_effect = transport
                clients.append(MeritzClient(f"key-{index}", f"secret-{index}", "001122AABBCC", session=session,
                                            request_pacer=MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)))
            clients[0].holdings()
            clients[1].submit_order(side="sell", symbol="SOXL", exchange_vendor_code="0066", quantity=1, price="45.12")
            self.assertEqual(len(starts), 4)
            for before, after in zip(starts, starts[1:]):
                self.assertAlmostEqual(after - before, 1.1)
            self.assertNotIn("key", path.read_text())
            self.assertNotIn("secret", path.read_text())
            self.assertNotIn("token", path.read_text())

    def test_transport_exception_releases_gate_and_keeps_attempt_spacing(self):
        clock = FakeClock()
        with TemporaryDirectory() as temporary:
            pacer = MeritzRequestPacer(Path(temporary) / "requests.lock", clock=clock, sleep=clock.sleep)
            with self.assertRaisesRegex(RuntimeError, "synthetic transport failure"):
                with pacer():
                    raise RuntimeError("synthetic transport failure")
            with pacer():
                self.assertAlmostEqual(clock(), 102.2)

    def test_delay_between_gate_entry_and_transport_cannot_shorten_next_wait(self):
        clock = FakeClock()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            first = MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)
            second = MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)
            with first():
                clock.now += 3  # Simulate fsync/scheduling delaying actual dispatch.
                first_transport_start = clock()
            with second():
                second_transport_start = clock()
            self.assertAlmostEqual(second_transport_start - first_transport_start, 1.1)
            self.assertEqual(len(clock.waits), 2)

    def test_corrupted_gate_blocks_before_any_http_call(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            path.write_text("not-a-timestamp")
            session = Mock()
            client = MeritzClient("synthetic-key", "synthetic-secret", "001122AABBCC", session=session,
                                  request_pacer=MeritzRequestPacer(path))
            with self.assertRaises(MeritzPacingError):
                client.holdings()
            session.request.assert_not_called()

    def test_previous_boot_timestamp_does_not_cause_an_unbounded_wait(self):
        clock = FakeClock()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            path.write_text("999999999")
            with MeritzRequestPacer(path, clock=clock, sleep=clock.sleep)():
                self.assertEqual(len(clock.waits), 1)
                self.assertAlmostEqual(clock.waits[0], 1.1)
                self.assertAlmostEqual(clock(), 101.1)
            self.assertEqual(float(path.read_text()), clock())

    def run_five_preset_burst(self, path, clock, broker, *, paced):
        ready = threading.Barrier(5)

        def run_preset(preset):
            session = Mock()
            session.request.side_effect = broker.request
            pacer = MeritzRequestPacer(path, clock=clock, sleep=clock.sleep) if paced else nullcontext
            client = MeritzClient(f"synthetic-{preset}", "synthetic-secret", "001122AABBCC",
                                  session=session, request_pacer=pacer)
            ready.wait(timeout=5)
            accepted = []
            try:
                client.holdings()  # Includes this preset's initial OAuth request.
                for index in range(8):
                    accepted.append(client.submit_order(side="sell" if index < 7 else "buy", symbol="SOXL",
                                                        exchange_vendor_code="0066", quantity=index + 1,
                                                        price="45.12"))
            except MeritzAPIError as exc:
                return {"preset": preset, "accepted": accepted, "error": exc}
            return {"preset": preset, "accepted": accepted, "error": None}

        with ThreadPoolExecutor(max_workers=5) as pool:
            return list(pool.map(run_preset, ("KMW", "KMW2", "JEH", "JEH2", "KHW")))

    def test_five_concurrent_presets_stay_below_independent_rolling_second_quota(self):
        clock = FakeClock()
        broker = RollingSecondBroker(clock)
        with TemporaryDirectory() as temporary:
            results = self.run_five_preset_burst(Path(temporary) / "requests.lock", clock, broker, paced=True)

        self.assertEqual(len(broker.calls), 50)  # Five OAuth + five reads + forty orders.
        self.assertTrue(all(result["error"] is None and len(result["accepted"]) == 8 for result in results))
        self.assertFalse(any(call["code"] == "EGW00200" for call in broker.calls))
        starts = [call["started"] for call in broker.calls]
        for started in starts:
            self.assertLessEqual(sum(0 <= other - started < 1.0 for other in starts), 1)
        receipts = [response["data"]["oder_no"] for result in results for response in result["accepted"]]
        self.assertEqual(len(set(receipts)), 40)

    def test_unpaced_five_preset_burst_reproduces_gateway_rate_limit(self):
        clock = FakeClock()
        broker = RollingSecondBroker(clock)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            results = self.run_five_preset_burst(path, clock, broker, paced=False)
            self.assertFalse(path.exists())

        self.assertTrue(any(call["code"] == "EGW00200" for call in broker.calls))
        self.assertTrue(any(isinstance(result["error"], MeritzAPIError)
                            and isinstance(result["error"].response, dict)
                            and result["error"].response.get("rsp_cd") == "EGW00200" for result in results))
        self.assertEqual(sum(len(result["accepted"]) for result in results), 0)
        self.assertEqual(clock.waits, [])

    def test_threads_hold_gate_for_the_full_transport_duration(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"

            def request(_):
                with MeritzRequestPacer(path, interval=0.025)():
                    started = time.monotonic()
                    time.sleep(0.045)
                    return started, time.monotonic()

            with ThreadPoolExecutor(max_workers=3) as pool:
                periods = sorted(pool.map(request, range(3)))
            for previous, following in zip(periods, periods[1:]):
                self.assertGreaterEqual(following[0], previous[1])

    def test_separate_processes_share_one_limit_and_do_not_overlap(self):
        script = """
import json, sys, time
from meritz_request_pacer import MeritzRequestPacer
pacer = MeritzRequestPacer(sys.argv[1], interval=0.06)
periods = []
for _ in range(2):
    with pacer():
        started = time.monotonic()
        time.sleep(0.01)
        periods.append((started, time.monotonic()))
print(json.dumps(periods))
"""
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "requests.lock"
            processes = [subprocess.Popen([sys.executable, "-B", "-c", script, str(path)],
                                          cwd=Path(__file__).resolve().parents[1],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for _ in range(3)]
            periods = []
            try:
                for process in processes:
                    output, error = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 0, error)
                    periods.extend(json.loads(output))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            periods.sort()
            self.assertEqual(len(periods), 6)
            for previous, following in zip(periods, periods[1:]):
                self.assertGreaterEqual(following[0], previous[1])
                self.assertGreaterEqual(following[0] - previous[0], 0.055)


if __name__ == "__main__":
    unittest.main()
