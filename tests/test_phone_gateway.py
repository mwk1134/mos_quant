"""Phone transport checks use loopback fake engines, never brokerage clients."""
import base64
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

import kmw_phone_gateway as gateway_module
from kmw_engine_dashboard import create_server as create_dashboard
from kmw_phone_gateway import create_server, load_config


ORIGIN = "https://kmw-unit-test.trycloudflare.com"
HOST = "kmw-unit-test.trycloudflare.com"
USERNAME = "unit-test-user"
PASSWORD = "unit-test-password-with-more-than-24-characters"
AUTH = "Basic " + base64.b64encode((USERNAME + ":" + PASSWORD).encode()).decode()


class FakeEngine:
    def __init__(self):
        self.enabled = False
        self.at = "17:01"
        self.calls = []
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        return {"enabled": self.enabled, "running": False, "at": self.at,
                "plan": None, "events": [], "last_run": None, "next_run": None}

    def configure(self, *, at):
        self.calls.append(("configure", at))
        self.at = at
        return self.status()

    def enable(self):
        self.calls.append("enable")
        self.enabled = True
        return self.status()

    def disable(self):
        self.calls.append("disable")
        self.enabled = False
        return self.status()

    def preview(self):
        self.calls.append("preview")
        return {"status": "ready", "orders": []}

    def run_now(self, *, expected_review_id):
        self.calls.append(("run", expected_review_id))
        return {"state": "COMPLETED", "orders": []}


@pytest.fixture
def phone():
    engine = FakeEngine()
    dashboard = create_dashboard(engine, port=0)
    gateway = create_server(public_origin=ORIGIN, username=USERNAME, password=PASSWORD,
                            engine_port=dashboard.server_port, port=0)
    servers = [dashboard, gateway]
    threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
               for s in servers]
    for thread in threads:
        thread.start()
    try:
        yield gateway, dashboard, engine
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def request(phone, path="/api/status", *, method="GET", body=None, headers=None, authenticate=True):
    gateway, dashboard, _ = phone
    values = {"Host": HOST, "X-Forwarded-Proto": "https"}
    if authenticate:
        values["Authorization"] = AUTH
    if method == "POST":
        values.update({"Origin": ORIGIN, "X-CSRF-Token": dashboard.csrf_token})
    if headers:
        values.update(headers)
    if isinstance(body, dict):
        body = json.dumps(body)
        values.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        payload = response.read()
        return response.status, dict(response.getheaders()), payload
    finally:
        connection.close()


def raw_request(phone, path="/api/enable", *, method="POST", headers=None, body=b'{"confirm_live":true}'):
    gateway, dashboard, _ = phone
    values = [("Host", HOST), ("X-Forwarded-Proto", "https"), ("Authorization", AUTH), ("Origin", ORIGIN),
              ("X-CSRF-Token", dashboard.csrf_token), ("Content-Type", "application/json"),
              ("Content-Length", str(len(body)))]
    if headers:
        # A value of None removes the normal default, allowing duplicate/empty
        # headers to be exercised without http.client normalizing them.
        for name, value in headers:
            if value is None:
                values = [(n, v) for n, v in values if n.lower() != name.lower()]
            else:
                values.append((name, value))
    connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in values:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_constructor_is_inert_and_loopback_only(phone):
    gateway, _, engine = phone
    assert gateway.server_address[0] == "127.0.0.1"
    assert engine.calls == [] and engine.status_calls == 0
    assert not engine.enabled
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(public_origin=ORIGIN, username=USERNAME, password=PASSWORD, host="0.0.0.0", port=0)


@pytest.mark.parametrize("origin", ["http://" + HOST, "https://user:pass@" + HOST,
                                     ORIGIN + "/path", ORIGIN + "?q=1", ORIGIN + "#x",
                                     ORIGIN + ":443", "https://trycloudflare.com",
                                     "https://a.trycloudflare.com.attacker.example", "https://other.example"])
def test_public_origin_cannot_be_implicit_or_ambiguous(origin):
    with pytest.raises(ValueError):
        create_server(public_origin=origin, username=USERNAME, password=PASSWORD, port=0)


def test_other_https_host_requires_exact_explicit_trust():
    server = create_server(public_origin="https://orders.example.com", trusted_host="orders.example.com",
                           username=USERNAME, password=PASSWORD, port=0)
    try:
        assert server.public_origin == "https://orders.example.com"
        assert server.allowed_host == "orders.example.com"
    finally:
        server.server_close()
    with pytest.raises(ValueError, match="exactly"):
        create_server(public_origin=ORIGIN, trusted_host="other.trycloudflare.com",
                      username=USERNAME, password=PASSWORD, port=0)


@pytest.mark.parametrize("username,password", [("", PASSWORD), ("a:b", PASSWORD),
                                              ("a\n", PASSWORD), (USERNAME, "short"),
                                              (USERNAME, "x" * 24 + "\n")])
def test_credentials_must_be_explicit_and_long_enough(username, password):
    with pytest.raises(ValueError):
        create_server(public_origin=ORIGIN, username=username, password=password, port=0)


def test_requested_short_password_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        create_server(public_origin=ORIGIN, username=USERNAME, password="1234", port=0)
    with pytest.raises(ValueError):
        create_server(public_origin=ORIGIN, username=USERNAME, password="1234",
                      allow_short_password=False, port=0)
    server = create_server(public_origin=ORIGIN, username=USERNAME, password="1234",
                           allow_short_password=True, port=0)
    try:
        expected = gateway_module.hashlib.sha256((USERNAME + ":1234").encode()).digest()
        assert server.auth_digest == expected
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


@pytest.mark.parametrize("password", ["", "1", "123", "1234\n", "x" * 1025])
def test_short_password_opt_in_still_rejects_empty_too_short_or_invalid_values(password):
    with pytest.raises(ValueError):
        create_server(public_origin=ORIGIN, username=USERNAME, password=password,
                      allow_short_password=True, port=0)


@pytest.mark.parametrize("flag", [None, 0, 1, "true", "false", [], {}])
def test_short_password_opt_in_requires_a_boolean(flag):
    with pytest.raises(ValueError):
        create_server(public_origin=ORIGIN, username=USERNAME, password=PASSWORD,
                      allow_short_password=flag, port=0)


@pytest.mark.parametrize("method,path", [("GET", "/"), ("GET", "/api/status"),
                                         ("POST", "/api/enable"), ("POST", "/api/configure")])
def test_unauthenticated_requests_return_nonempty_401_without_upstream(phone, method, path):
    _, _, engine = phone
    code, headers, body = request(phone, path, method=method, body={} if method == "POST" else None,
                                  authenticate=False)
    assert code == 401 and json.loads(body)["ok"] is False
    assert headers["WWW-Authenticate"].startswith("Basic ")
    assert headers["Cache-Control"] == "no-store"
    assert int(headers["Content-Length"]) == len(body) > 0
    assert engine.status_calls == 0 and engine.calls == []


@pytest.mark.parametrize("value", ["Basic not-base64", "Bearer test", "Basic " + base64.b64encode(b"wrong:credentials").decode()])
def test_bad_authentication_never_contacts_engine(phone, value):
    code, _, _ = request(phone, headers={"Authorization": value})
    assert code == 401 and phone[2].status_calls == 0


def test_repeated_wrong_passwords_lock_only_the_failed_client_until_window_expires(phone, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gateway_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    wrong = "Basic " + base64.b64encode((USERNAME + ":wrong-password").encode()).decode()
    failed_client = {"Authorization": wrong, "CF-Connecting-IP": "192.0.2.10"}
    for _ in range(5):
        assert request(phone, headers=failed_client)[0] == 401
    code, headers, body = request(phone, headers=failed_client)
    assert code == 429 and headers["Retry-After"] == "60"
    assert "WWW-Authenticate" not in headers and json.loads(body)["ok"] is False
    assert phone[2].status_calls == 0 and phone[2].calls == []
    # Even valid credentials cannot enter an engine mutation through a bucket
    # currently locked after failed login attempts.
    code, _, _ = request(phone, "/api/enable", method="POST", body={"confirm_live": True},
                         headers={"CF-Connecting-IP": "192.0.2.10"})
    assert code == 429 and phone[2].calls == [] and not phone[2].enabled
    assert request(phone, headers={"CF-Connecting-IP": "192.0.2.11"})[0] == 200
    assert phone[2].status_calls == 1
    clock[0] += 60
    assert request(phone, headers=failed_client)[0] == 401
    assert request(phone, headers={"CF-Connecting-IP": "192.0.2.10"})[0] == 200
    assert phone[2].status_calls == 2 and phone[2].calls == []


def test_initial_browser_login_challenges_do_not_consume_failed_password_limit(phone):
    client = {"CF-Connecting-IP": "192.0.2.12"}
    for _ in range(10):
        code, headers, _ = request(phone, authenticate=False, headers=client)
        assert code == 401 and "WWW-Authenticate" in headers
    assert phone[2].status_calls == 0
    assert request(phone, headers=client)[0] == 200
    assert phone[2].status_calls == 1 and phone[2].calls == []


def test_successful_login_resets_unused_failed_password_count(phone):
    client = {"CF-Connecting-IP": "192.0.2.13"}
    wrong = {**client, "Authorization": "Basic " + base64.b64encode(b"wrong:password").decode()}
    for _ in range(3):
        assert request(phone, headers=wrong)[0] == 401
    assert request(phone, headers=client)[0] == 200
    for _ in range(3):
        assert request(phone, headers=wrong)[0] == 401
    assert request(phone, headers=client)[0] == 200
    assert phone[2].status_calls == 2 and phone[2].calls == []


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("protocol_headers", [
    [("X-Forwarded-Proto", None)],
    [("X-Forwarded-Proto", None), ("X-Forwarded-Proto", "http")],
    [("X-Forwarded-Proto", None), ("X-Forwarded-Proto", "HTTPS")],
    [("X-Forwarded-Proto", None), ("X-Forwarded-Proto", "https,http")],
    [("X-Forwarded-Proto", "https")],
])
def test_insecure_or_ambiguous_transport_never_challenges_or_forwards(phone, method, protocol_headers):
    gateway, _, engine = phone
    values = [("Host", HOST), ("X-Forwarded-Proto", "https")]
    for name, value in protocol_headers:
        if value is None:
            values = [(n, v) for n, v in values if n.lower() != name.lower()]
        else:
            values.append((name, value))
    connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
    try:
        connection.putrequest(method, "/api/status" if method == "GET" else "/api/enable", skip_host=True)
        for name, value in values:
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        assert response.status == 403 and json.loads(body)["ok"] is False
        assert response.getheader("WWW-Authenticate") is None
        assert len(body) == int(response.getheader("Content-Length")) > 0
    finally:
        connection.close()
    assert engine.status_calls == 0 and engine.calls == []


def test_valid_credentials_cannot_bypass_https_transport_requirement(phone):
    code, headers, body = request(phone, headers={"X-Forwarded-Proto": "http"})
    assert code == 403 and "WWW-Authenticate" not in headers
    assert json.loads(body)["ok"] is False and phone[2].status_calls == 0


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", HOST + ".attacker.example", HOST + ":443"])
def test_only_exact_configured_public_host_is_accepted(phone, host):
    code, _, _ = request(phone, headers={"Host": host})
    assert code == 403 and phone[2].status_calls == 0


def test_authenticated_ui_and_status_preserve_csrf_without_engine_mutation(phone):
    gateway, dashboard, engine = phone
    code, headers, html = request(phone, "/")
    assert code == 200 and dashboard.csrf_token.encode() in html
    assert "로그인으로 보호된 휴대폰 관리 화면입니다.".encode() in html
    assert "이 화면은 이 PC의 127.0.0.1에서만 열립니다.".encode() not in html
    assert len(html) == int(headers["Content-Length"])
    assert PASSWORD.encode() not in html and AUTH.encode() not in html
    assert headers["X-Frame-Options"] == "DENY"
    assert "Access-Control-Allow-Origin" not in headers
    assert headers["X-KMW-Phone-Gateway"] == "1"
    for path in ["/api/status", "/?kmw_api=status"]:
        code, headers, body = request(phone, path)
        assert code == 200 and json.loads(body)["enabled"] is False
        assert len(body) == int(headers["Content-Length"])
    assert engine.calls == [] and engine.status_calls == 2


@pytest.mark.parametrize("action", ["preview", "configure", "enable", "disable", "run"])
def test_get_requests_cannot_activate_or_mutate_engine(phone, action):
    for path in ["/api/" + action, "/?kmw_api=" + action]:
        assert request(phone, path)[0] == 404
    assert phone[2].calls == [] and phone[2].status_calls == 0


@pytest.mark.parametrize("path", ["https://attacker.example/api/status", "//attacker.example/api/status",
                                  "/?kmw_api=status&url=https://attacker.example", "/?kmw_api=status&kmw_api=enable",
                                  "/api/status?anything=1", "/private/8701/?kmw_api=status", "/?kmw_api=unknown"])
def test_arbitrary_proxy_targets_and_queries_are_rejected(phone, path):
    assert request(phone, path)[0] == 404
    assert phone[2].status_calls == 0


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", ORIGIN + "/", "http://" + HOST])
def test_post_requires_exact_https_origin(phone, origin):
    code, _, _ = request(phone, "/api/enable", method="POST", body={"confirm_live": True}, headers={"Origin": origin})
    assert code == 403 and phone[2].calls == []


@pytest.mark.parametrize("header,code", [("Host", 403), ("Origin", 403), ("X-CSRF-Token", 403),
                                        ("Content-Type", 400), ("Content-Length", 400), ("Authorization", 401)])
def test_duplicate_security_headers_are_rejected(phone, header, code):
    value = {"Host": HOST, "Origin": ORIGIN, "X-CSRF-Token": phone[1].csrf_token,
             "Content-Type": "application/json", "Content-Length": "21", "Authorization": AUTH}[header]
    assert raw_request(phone, headers=[(header, value)])[0] == code
    assert phone[2].calls == []


@pytest.mark.parametrize("headers", [[("Content-Length", None)], [("Content-Length", None), ("Content-Length", "0")],
                                     [("Content-Length", None), ("Content-Length", "16385")],
                                     [("Content-Length", None), ("Content-Length", "+21")],
                                     [("Transfer-Encoding", "chunked")], [("Transfer-Encoding", "")],
                                     [("Content-Type", None), ("Content-Type", "text/plain")]])
def test_post_body_framing_is_bounded_and_unambiguous(phone, headers):
    assert raw_request(phone, headers=headers)[0] == 400
    assert phone[2].calls == []


@pytest.mark.parametrize("body", [b"invalid-json", b"[]", b"null", b"\xff"])
def test_post_requires_a_json_object(phone, body):
    code, _, _ = request(phone, "/api/enable", method="POST", body=body, headers={"Content-Type": "application/json"})
    assert code == 400 and phone[2].calls == []


def test_gateway_does_not_supply_csrf_or_live_acknowledgement(phone):
    assert raw_request(phone, headers=[("X-CSRF-Token", None)])[0] == 403
    code, _, _ = request(phone, "/api/enable", method="POST", body={"confirm_live": True}, headers={"X-CSRF-Token": "wrong"})
    assert code == 403
    for body in [{}, {"confirm_live": False}, {"confirm_live": "true"}]:
        code, _, _ = request(phone, "/api/enable", method="POST", body=body)
        assert code == 400
    assert not phone[2].enabled and phone[2].calls == []


def test_user_post_configure_enable_disable_only_delegates_to_fake_engine(phone):
    engine = phone[2]
    code, _, body = request(phone, "/?kmw_api=configure", method="POST", body={"at": "18:25"})
    assert code == 200 and json.loads(body)["status"]["at"] == "18:25"
    assert not engine.enabled
    code, _, body = request(phone, "/api/enable", method="POST", body={"confirm_live": True})
    assert code == 200 and json.loads(body)["status"]["enabled"] is True
    code, _, body = request(phone, "/api/disable", method="POST", body={})
    assert code == 200 and json.loads(body)["status"]["enabled"] is False
    assert engine.calls == [("configure", "18:25"), "enable", "disable"]


def test_forwarding_strips_authorization_and_fixes_internal_host_origin(phone, monkeypatch):
    original = http.client.HTTPConnection.request
    forwarded = []

    def record(self, method, url, body=None, headers=None, **kwargs):
        if self.port == phone[1].server_port:
            forwarded.append((self.host, method, url, body, headers))
        return original(self, method, url, body=body, headers=headers or {}, **kwargs)

    monkeypatch.setattr(http.client.HTTPConnection, "request", record)
    code, _, _ = request(phone, "/api/configure", method="POST", body={"at": "18:30"},
                         headers={"X-Forwarded-Host": "attacker.example", "X-Forwarded-Proto": "https"})
    assert code == 200 and len(forwarded) == 1
    host, method, path, body, headers = forwarded[0]
    assert host == "127.0.0.1" and method == "POST" and path == "/api/configure"
    assert headers["Host"] == phone[1].allowed_host and headers["Origin"] == phone[1].origin
    assert headers["X-CSRF-Token"] == phone[1].csrf_token
    assert "Authorization" not in headers and "X-Forwarded-Host" not in headers
    assert "X-Forwarded-Proto" not in headers
    assert json.loads(body) == {"at": "18:30"}


@pytest.mark.parametrize("kind", ["empty", "redirect", "exception"])
def test_bad_upstream_is_nonempty_502_and_never_retried(phone, monkeypatch, kind):
    calls = []

    class Response:
        status = 302 if kind == "redirect" else 200

        def read(self, _limit):
            return b"" if kind == "empty" else b"{}"

        def getheader(self, key, default=None):
            return "application/json" if key == "Content-Type" else default

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port))

        def request(self, *args, **kwargs):
            if kind == "exception":
                raise OSError("secret-upstream-error")

        def getresponse(self):
            return Response()

        def close(self):
            pass

    # Preserve the test client's real connection; only gateway construction is
    # replaced, so no upstream request can reach a financial service.
    real_connection = http.client.HTTPConnection
    def choose(host, port, *args, **kwargs):
        return Connection(host, port, kwargs["timeout"]) if port == phone[1].server_port else real_connection(host, port, *args, **kwargs)
    monkeypatch.setattr(gateway_module.http.client, "HTTPConnection", choose)
    code, headers, body = request(phone, "/api/configure", method="POST", body={"at": "18:30"})
    assert code == 502 and json.loads(body)["ok"] is False
    assert len(body) == int(headers["Content-Length"]) > 0
    assert b"secret-upstream-error" not in body and len(calls) == 1
    assert phone[2].calls == []


def config_file(tmp_path, **updates):
    values = {"public_origin": ORIGIN, "username": USERNAME, "password": PASSWORD}
    values.update(updates)
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(values))
    path.chmod(0o600)
    return path


def test_config_load_is_inert_and_requires_owner_only_file(tmp_path):
    path = config_file(tmp_path, port=8702, engine_port=8701)
    assert load_config(path)["public_origin"] == ORIGIN
    assert load_config(path)["port"] == 8702
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_config(path)


def test_config_rejects_symlinks_unknown_fields_and_weak_password(tmp_path):
    path = config_file(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        load_config(link)
    config_file(tmp_path, target="https://attacker.example")
    with pytest.raises(ValueError):
        load_config(path)
    config_file(tmp_path, password="short")
    with pytest.raises(ValueError):
        load_config(path)


def test_config_accepts_requested_short_password_only_with_explicit_opt_in(tmp_path):
    path = config_file(tmp_path, password="1234", allow_short_password=True)
    values = load_config(path)
    assert values["password"] == "1234"
    assert values["allow_short_password"] is True
    for updates in [{"password": "1234"}, {"password": "1234", "allow_short_password": False}]:
        path = config_file(tmp_path, **updates)
        with pytest.raises(ValueError):
            load_config(path)


@pytest.mark.parametrize("flag", [None, 0, 1, "true", "false", [], {}])
def test_config_rejects_nonboolean_short_password_opt_in(tmp_path, flag):
    path = config_file(tmp_path, allow_short_password=flag)
    with pytest.raises(ValueError):
        load_config(path)


def test_cli_never_starts_without_serve(tmp_path):
    path = config_file(tmp_path)
    with pytest.raises(SystemExit) as result:
        gateway_module.main(["--config", str(path)])
    assert result.value.code == 2
