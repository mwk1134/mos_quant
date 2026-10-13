"""Real loopback HTTP requests against a fake engine; no brokerage calls."""
from copy import deepcopy
import http.client
import json
import threading

import pytest

from kmw_engine_dashboard import create_server


class FakeEngine:
    def __init__(self):
        self.enabled = False
        self.at = "17:01"
        self.calls = []
        self.plan = {
            "status": "ready", "basis_date": "2026-10-09", "order_date": "2026-10-12",
            "orders": [{"side": "BUY", "ticker": "SOXL", "quantity": 142,
                        "price": "144.79", "order_type": "LOC", "round": 6}],
        }

    def status(self):
        return {"enabled": self.enabled, "running": False, "at": self.at,
                "next_run": None, "last_run": None, "plan": deepcopy(self.plan), "events": [],
                "review_id": "test-reviewed-plan", "broker_status": []}

    def preview(self):
        self.calls.append("preview")
        return self.plan

    def enable(self):
        self.calls.append("enable")
        self.enabled = True
        return self.status()

    def disable(self):
        self.calls.append("disable")
        self.enabled = False
        return self.status()

    def run_now(self, *, expected_review_id):
        self.calls.append(("run_now", expected_review_id))
        return {"state": "DIRECT_ACCEPTED", "orders": []}

    def configure(self, *, at):
        self.calls.append(("configure", at))
        self.at = at
        return self.status()


@pytest.fixture
def dashboard():
    engine = FakeEngine()
    server = create_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, engine
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, path="/api/status", *, method="GET", body=None, headers=None, trusted=False):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    values = {} if headers is None else dict(headers)
    if trusted:
        values.setdefault("Origin", server.origin)
        values.setdefault("X-CSRF-Token", server.csrf_token)
    data = None
    if body is not None:
        data = json.dumps(body)
        values.setdefault("Content-Type", "application/json")
    try:
        connection.request(method, path, body=data, headers=values)
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        return response.status, dict(response.getheaders()), payload
    finally:
        connection.close()


def post(server, path, body=None, **kwargs):
    return request(server, path, method="POST", body={} if body is None else body, trusted=True, **kwargs)


def test_create_server_is_inert_and_rejects_external_bind():
    engine = FakeEngine()
    server = create_server(engine, port=0)
    try:
        assert engine.calls == []
        assert not engine.enabled
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(engine, host="0.0.0.0", port=0)


@pytest.mark.parametrize("preset", ["KMW2", "JEH"])
def test_additional_account_dashboard_is_labeled_and_remains_inactive(preset):
    engine = FakeEngine()
    engine.preset = preset
    server = create_server(engine, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        status, _, page = request(server, "/")
        assert status == 200
        assert f'<meta name="engine-preset" content="{preset}">' in page
        assert f"{preset} 추천 주문표" in page
        assert f"다음 {preset} 주문" in page
        assert f"지정한 시간에 {preset} 추천 주문" in page
        assert engine.calls == [] and engine.enabled is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_read_status_and_ui_never_activate(dashboard):
    server, engine = dashboard
    code, headers, body = request(server)
    assert code == 200
    assert json.loads(body)["enabled"] is False
    assert headers["Cache-Control"] == "no-store"
    code, headers, html = request(server, "/")
    assert code == 200
    assert "실제 계좌 주문 활성화에 동의합니다" in html
    assert 'id="live-ack" type="checkbox"' in html
    assert 'id="enable" class="primary" disabled' in html
    assert 'id="run" disabled' in html
    assert "접수 결과와 체결 내역은 다릅니다" in html
    assert "체결 수량" in html
    assert server.csrf_token in html
    assert "__CSRF_TOKEN__" not in html
    assert headers["X-Frame-Options"] == "DENY"
    assert "Access-Control-Allow-Origin" not in headers
    assert engine.calls == []


@pytest.mark.parametrize("header", [
    {"X-CSRF-Token": "invalid"}, {"X-CSRF-Token": "ñ"},
    {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Host": "attacker.example"}, {"Host": "localhost:8701"},
])
def test_invalid_csrf_origin_or_host_cannot_change_engine(dashboard, header):
    server, engine = dashboard
    code, _, _ = post(server, "/api/enable", {"confirm_live": True}, headers=header)
    assert code == 403
    assert engine.calls == []
    assert not engine.enabled


def test_missing_origin_and_csrf_are_rejected(dashboard):
    server, engine = dashboard
    code, _, _ = request(server, "/api/enable", method="POST", body={"confirm_live": True})
    assert code == 403
    code, _, _ = request(server, "/api/enable", method="POST", body={"confirm_live": True},
                         headers={"Origin": server.origin})
    assert code == 403
    code, _, _ = request(server, "/api/enable", method="POST", body={"confirm_live": True},
                         headers={"X-CSRF-Token": server.csrf_token})
    assert code == 403
    assert engine.calls == []


def test_same_origin_referer_can_authenticate_without_origin(dashboard):
    server, engine = dashboard
    headers = {"X-CSRF-Token": server.csrf_token, "Referer": server.origin + "/"}
    code, _, _ = request(server, "/api/preview", method="POST", body={}, headers=headers)
    assert code == 200
    assert engine.calls == ["preview"]
    headers["Referer"] = server.origin + ".attacker.example/"
    code, _, _ = request(server, "/api/preview", method="POST", body={}, headers=headers)
    assert code == 403
    assert engine.calls == ["preview"]


@pytest.mark.parametrize("path", ["/api/enable", "/api/run"])
@pytest.mark.parametrize("body", [{}, {"confirm_live": False}, {"confirm_live": "true"}, {"confirm_live": 1}])
def test_live_actions_require_literal_user_acknowledgement(dashboard, path, body):
    server, engine = dashboard
    code, _, response = post(server, path, body)
    assert code == 400
    assert "명시적인 확인" in json.loads(response)["error"]
    assert engine.calls == []


def test_explicit_enable_run_and_disable_delegate_to_fake_engine(dashboard):
    server, engine = dashboard
    code, _, body = post(server, "/api/enable", {"confirm_live": True})
    assert code == 200
    assert json.loads(body)["status"]["enabled"] is True
    code, _, body = post(server, "/api/run", {"confirm_live": True, "review_id": "test-reviewed-plan"})
    assert code == 200
    assert json.loads(body)["result"]["state"] == "DIRECT_ACCEPTED"
    code, _, body = post(server, "/api/disable")
    assert code == 200
    assert json.loads(body)["status"]["enabled"] is False
    assert engine.calls == ["enable", ("run_now", "test-reviewed-plan"), "disable"]


def test_preview_and_configure_never_enable(dashboard):
    server, engine = dashboard
    assert post(server, "/api/preview")[0] == 200
    code, _, body = post(server, "/api/configure", {"at": "18:35"})
    assert code == 200
    assert json.loads(body)["status"]["at"] == "18:35"
    assert engine.calls == ["preview", ("configure", "18:35")]
    assert not engine.enabled


@pytest.mark.parametrize("path", ["/", "/private/8701/", "/relay/app/dashboard"])
def test_page_relative_api_queries_keep_post_json_contract(dashboard, path):
    server, engine = dashboard
    code, headers, body = request(server, path + "?kmw_api=status&ticket=test-only")
    assert code == 200 and json.loads(body)["enabled"] is False
    assert headers["X-KMW-Engine"] == "1"
    code, _, body = post(server, path + "?ticket=test-only&kmw_api=configure", {"at": "18:25"})
    assert code == 200 and json.loads(body)["status"]["at"] == "18:25"
    assert engine.calls == [("configure", "18:25")]
    assert not engine.enabled


@pytest.mark.parametrize("action", ["enable", "disable", "run", "configure", "preview"])
@pytest.mark.parametrize("path", ["/", "/private/8701/"])
def test_page_query_get_cannot_mutate_engine(dashboard, action, path):
    server, engine = dashboard
    code, _, body = request(server, path + "?kmw_api=" + action)
    assert code == 404 and json.loads(body)["ok"] is False
    assert engine.calls == []


@pytest.mark.parametrize("path", ["/", "/private/8701/"])
def test_page_query_live_request_still_needs_origin_csrf_and_ack(dashboard, path):
    server, engine = dashboard
    route = path + "?kmw_api=enable"
    code, _, _ = request(server, route, method="POST", body={"confirm_live": True})
    assert code == 403 and engine.calls == []
    code, _, _ = post(server, route, {})
    assert code == 400 and engine.calls == []
    code, _, body = post(server, route, {"confirm_live": True})
    assert code == 200 and json.loads(body)["status"]["enabled"] is True
    assert engine.calls == ["enable"]


@pytest.mark.parametrize("header", [
    {"X-CSRF-Token": "invalid"}, {"Origin": "https://attacker.example"},
    {"Host": "attacker.example"},
])
def test_proxy_path_alias_does_not_relax_request_authentication(dashboard, header):
    server, engine = dashboard
    code, _, _ = post(server, "/private/8701/?kmw_api=enable",
                      {"confirm_live": True}, headers=header)
    assert code == 403
    assert engine.calls == []
    assert not engine.enabled


@pytest.mark.parametrize("query", [
    "kmw_api=unknown", "kmw_api=", "kmw_api=enable&kmw_api=enable",
    "kmw_api=enable&kmw_api=", "kmw_api=status&kmw_api=configure",
])
def test_proxy_path_alias_requires_one_allowed_action(dashboard, query):
    server, engine = dashboard
    code, _, _ = post(server, "/private/8701/?" + query, {"confirm_live": True})
    assert code == 404
    assert engine.calls == []
    assert not engine.enabled


@pytest.mark.parametrize("value", [None, "24:00", "12:60", "7:01", "17:01:00", "18:00\n", 1701])
def test_invalid_schedule_never_reaches_engine(dashboard, value):
    server, engine = dashboard
    assert post(server, "/api/configure", {"at": value})[0] == 400
    assert engine.calls == []


def test_overlapping_mutations_cannot_double_invoke(dashboard):
    server, engine = dashboard
    server.mutation_lock.acquire()
    try:
        code, _, body = post(server, "/api/run", {"confirm_live": True, "review_id": "test-reviewed-plan"})
        assert code == 409
        assert "처리 중" in json.loads(body)["error"]
        assert engine.calls == []
        assert request(server)[0] == 200  # Status remains readable during execution.
        engine.enabled = True
        code, _, response = post(server, "/api/disable")
        assert code == 200  # Stop can interrupt a long-running order batch.
        assert json.loads(response)["status"]["enabled"] is False
        assert engine.calls == ["disable"]
        assert server.mutation_lock.locked()  # Stop must not release another request's lock.
    finally:
        server.mutation_lock.release()


@pytest.mark.parametrize("body", [[], "bad", None])
def test_non_object_json_is_rejected(dashboard, body):
    server, engine = dashboard
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("POST", "/api/enable", body=json.dumps(body),
                           headers={"Origin": server.origin, "X-CSRF-Token": server.csrf_token,
                                    "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
    assert engine.calls == []


def test_exception_returns_error_without_repeating_action(dashboard):
    from kmw_engine_backend import EngineBlocked
    server, engine = dashboard
    def blocked(*, expected_review_id):
        engine.calls.append("run_now")
        raise EngineBlocked("매수·매도 가격이 교차하여 전송을 중단했습니다.")
    engine.run_now = blocked
    code, _, body = post(server, "/api/run", {"confirm_live": True, "review_id": "test-reviewed-plan"})
    assert code == 409
    assert "교차" in json.loads(body)["error"]
    assert engine.calls == ["run_now"]
    assert not server.mutation_lock.locked()


def test_unexpected_exception_details_are_never_exposed(dashboard):
    server, engine = dashboard
    def broken():
        raise RuntimeError("secret-token https://api.example/private?key=secret")
    engine.preview = broken
    code, _, body = post(server, "/api/preview")
    assert code == 409
    assert "secret" not in body
    assert "api.example" not in body
    assert "실행 기록을 확인" in json.loads(body)["error"]
    assert not server.mutation_lock.locked()


@pytest.mark.parametrize("review_id", [None, "", " ", 123])
def test_unreviewed_run_never_reaches_engine(dashboard, review_id):
    server, engine = dashboard
    code, _, body = post(server, "/api/run", {"confirm_live": True, "review_id": review_id})
    assert code == 400
    assert "추천 주문표" in json.loads(body)["error"]
    assert engine.calls == []
