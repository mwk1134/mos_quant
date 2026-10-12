"""Site registration checks use fake HTTP only; no engine or brokerage commands."""
import base64
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest
import requests

import kmw_site_bridge as module
from kmw_site_bridge import BridgeError, SITE_ORIGIN, SiteBridge, load_config, main


ORIGIN = "https://kmw-site-test.trycloudflare.com"
USER = "kmw-test"
PASSWORD = "1234"
BYPASS = "unit-test-site-bypass-token"
SECRET = "unit-test-registration-secret"


def write_private(path, values):
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(0o600)


@pytest.fixture
def config_files(tmp_path):
    gateway_path = tmp_path / "gateway.json"
    bridge_path = tmp_path / "bridge.json"
    gateway = {"public_origin": ORIGIN, "username": USER, "password": PASSWORD,
               "allow_short_password": True}
    config = {"site_origin": SITE_ORIGIN, "sites_bypass_token": BYPASS,
              "registration_secret": SECRET, "gateway_config_path": str(gateway_path)}
    write_private(gateway_path, gateway)
    write_private(bridge_path, config)
    return SimpleNamespace(gateway_path=gateway_path, bridge_path=bridge_path,
                           gateway=gateway, config=config)


class FakeResponse:
    def __init__(self, body=None, *, status=200, mark=False, raw=None,
                 content_type="application/json; charset=utf-8", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if mark:
            self.headers["X-KMW-Phone-Gateway"] = "1"
        if headers:
            self.headers.update(headers)
        self.body = json.dumps(body).encode() if raw is None else raw
        self.closed = False

    def iter_content(self, *, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses) if responses is not None else [
            FakeResponse({"enabled": False, "running": False}, mark=True),
            FakeResponse({"ok": True}),
        ]
        self.calls = []
        self.trust_env = True
        self.verify = False
        self.closed = False

    def _request(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        assert self.responses, "An unexpected HTTP call was made"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self._request("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, kwargs)

    def close(self):
        self.closed = True


def test_config_loads_valid_private_file(config_files):
    assert load_config(config_files.bridge_path) == config_files.config


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660, 0o700])
def test_config_requires_exact_0600(config_files, mode):
    config_files.bridge_path.chmod(mode)
    with pytest.raises(ValueError, match="0600"):
        load_config(config_files.bridge_path)


def test_config_rejects_symlink(config_files, tmp_path):
    link = tmp_path / "link.json"
    link.symlink_to(config_files.bridge_path)
    with pytest.raises(OSError):
        load_config(link)


def test_config_rejects_directory(tmp_path):
    tmp_path.chmod(0o600)
    with pytest.raises((OSError, ValueError)):
        load_config(tmp_path)


def test_config_rejects_different_owner(config_files, monkeypatch):
    original = os.fstat

    def wrong_owner(fd):
        result = original(fd)
        return SimpleNamespace(st_mode=result.st_mode, st_uid=os.getuid() + 1)

    monkeypatch.setattr(module.os, "fstat", wrong_owner)
    with pytest.raises(ValueError, match="owner-owned"):
        load_config(config_files.bridge_path)


def test_config_read_is_bounded(config_files):
    config_files.bridge_path.write_bytes(b" " * 65_537)
    with pytest.raises(ValueError, match="too large"):
        load_config(config_files.bridge_path)


@pytest.mark.parametrize("update", [
    {"site_origin": "https://other.ugi1134.chatgpt.site"},
    {"site_origin": "http://kmw-trading-console.ugi1134.chatgpt.site"},
    {"site_origin": SITE_ORIGIN + "/"},
    {"site_origin": SITE_ORIGIN + "@evil.example"},
    {"site_origin": SITE_ORIGIN + "?token=secret"},
    {"sites_bypass_token": "short"},
    {"registration_secret": "bad secret with whitespace"},
    {"gateway_config_path": "https://evil.example/config"},
    {"gateway_config_path": "\x00file"},
    {"unknown_field": True},
])
def test_config_rejects_invalid_fields(config_files, update):
    write_private(config_files.bridge_path, {**config_files.config, **update})
    with pytest.raises(ValueError):
        load_config(config_files.bridge_path)


@pytest.mark.parametrize("missing", sorted(module._CONFIG_FIELDS))
def test_config_requires_all_fields(config_files, missing):
    values = dict(config_files.config)
    values.pop(missing)
    write_private(config_files.bridge_path, values)
    with pytest.raises(ValueError):
        load_config(config_files.bridge_path)


def test_step_reads_status_then_registers_only_metadata(config_files):
    session = FakeSession()
    bridge = SiteBridge(config_files.bridge_path, session=session)
    assert bridge.step() is True
    assert session.trust_env is False
    assert session.verify is True
    get_method, get_url, get_kwargs = session.calls[0]
    assert (get_method, get_url) == ("GET", ORIGIN + "/api/status")
    assert get_kwargs["headers"] == {
        "Authorization": "Basic " + base64.b64encode((USER + ":" + PASSWORD).encode()).decode()}
    post_method, post_url, post_kwargs = session.calls[1]
    assert (post_method, post_url) == ("POST", SITE_ORIGIN + "/api/bridge/register")
    assert post_kwargs["json"] == {"origin": ORIGIN, "username": USER, "password": PASSWORD}
    assert post_kwargs["headers"] == {"OAI-Sites-Authorization": "Bearer " + BYPASS,
                                       "Authorization": "Bearer " + SECRET}
    for _, _, kwargs in session.calls:
        assert kwargs["timeout"] == 20
        assert kwargs["allow_redirects"] is False
        assert kwargs["verify"] is True
        assert kwargs["stream"] is True
    assert bridge.step() is False
    assert len(session.calls) == 2
    bridge.close()
    assert session.closed


@pytest.mark.parametrize("enabled,running", [(False, False), (True, False), (True, True)])
def test_registration_never_changes_engine_state(config_files, enabled, running):
    session = FakeSession([FakeResponse({"enabled": enabled, "running": running}, mark=True),
                           FakeResponse({"ok": True})])
    assert SiteBridge(config_files.bridge_path, session=session).step() is True
    assert all(url == ORIGIN + "/api/status" for method, url, _ in session.calls if method == "GET")
    assert all(url == SITE_ORIGIN + "/api/bridge/register" for method, url, _ in session.calls if method == "POST")


@pytest.mark.parametrize("change", [
    {"public_origin": "https://kmw-new-site-test.trycloudflare.com"},
    {"username": "changed-user"},
    {"password": "4321"},
])
def test_changed_gateway_registers_again(config_files, change):
    session = FakeSession()
    bridge = SiteBridge(config_files.bridge_path, session=session)
    assert bridge.step() is True
    changed = {**config_files.gateway, **change}
    write_private(config_files.gateway_path, changed)
    session.responses.extend([FakeResponse({"enabled": False, "running": False}, mark=True),
                              FakeResponse({"ok": True})])
    assert bridge.step() is True
    assert len(session.calls) == 4
    assert session.calls[-1][2]["json"] == {"origin": changed["public_origin"],
                                           "username": changed["username"], "password": changed["password"]}


def test_changed_registration_secret_registers_again(config_files):
    session = FakeSession()
    bridge = SiteBridge(config_files.bridge_path, session=session)
    bridge.step()
    write_private(config_files.bridge_path, {**config_files.config, "registration_secret": SECRET + "-rotated"})
    session.responses.extend([FakeResponse({"enabled": False, "running": False}, mark=True),
                              FakeResponse({"ok": True})])
    assert bridge.step() is True
    assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer " + SECRET + "-rotated"


def test_gateway_config_uses_existing_secure_loader(config_files):
    config_files.gateway_path.chmod(0o644)
    session = FakeSession()
    with pytest.raises(BridgeError):
        SiteBridge(config_files.bridge_path, session=session).step()
    assert session.calls == []


def test_gateway_origin_is_normalized(config_files):
    write_private(config_files.gateway_path, {**config_files.gateway, "public_origin": ORIGIN + "/"})
    session = FakeSession()
    SiteBridge(config_files.bridge_path, session=session).step()
    assert session.calls[0][1] == ORIGIN + "/api/status"
    assert session.calls[1][2]["json"]["origin"] == ORIGIN


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 500])
def test_bad_gateway_status_never_registers_or_follows_redirect(config_files, status):
    response = FakeResponse({}, status=status, mark=True, headers={"Location": "https://evil.example"})
    session = FakeSession([response])
    with pytest.raises(BridgeError):
        SiteBridge(config_files.bridge_path, session=session).step()
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False
    assert response.closed


@pytest.mark.parametrize("response", [
    FakeResponse({"enabled": False, "running": False}),
    FakeResponse({"enabled": "false", "running": False}, mark=True),
    FakeResponse({"enabled": False}, mark=True),
    FakeResponse({"enabled": 0, "running": False}, mark=True),
    FakeResponse([], mark=True),
    FakeResponse(raw=b"", mark=True),
    FakeResponse(raw=b"not-json", mark=True),
    FakeResponse(raw=b"{}", content_type="text/html", mark=True),
    FakeResponse(raw=b"x" * 262_145, mark=True),
])
def test_gateway_marker_and_status_contract_required(config_files, response):
    session = FakeSession([response])
    with pytest.raises(BridgeError):
        SiteBridge(config_files.bridge_path, session=session).step()
    assert len(session.calls) == 1
    assert response.closed


@pytest.mark.parametrize("response", [
    FakeResponse({"ok": True}, status=302, headers={"Location": "https://evil.example"}),
    FakeResponse({"ok": True}, status=307),
    FakeResponse({"ok": True}, status=401),
    FakeResponse({"ok": False}),
    FakeResponse({"ok": 1}),
    FakeResponse([]),
    FakeResponse(raw=b""),
    FakeResponse(raw=b"{}", content_type="text/html"),
])
def test_registration_response_must_confirm_success(config_files, response):
    session = FakeSession([FakeResponse({"enabled": False, "running": False}, mark=True), response])
    bridge = SiteBridge(config_files.bridge_path, session=session)
    with pytest.raises(BridgeError):
        bridge.step()
    assert bridge._registered_fingerprint is None
    assert len(session.calls) == 2
    assert response.closed


def test_transient_registration_failure_can_retry_idempotent_metadata(config_files):
    session = FakeSession([FakeResponse({"enabled": False, "running": False}, mark=True),
                           requests.Timeout("secret information must not be logged")])
    bridge = SiteBridge(config_files.bridge_path, session=session)
    with pytest.raises(BridgeError) as caught:
        bridge.step()
    assert "secret information" not in str(caught.value)
    assert len(session.calls) == 2  # No in-request automatic retry.
    session.responses.extend([FakeResponse({"enabled": False, "running": False}, mark=True),
                              FakeResponse({"ok": True})])
    assert bridge.step() is True
    assert len(session.calls) == 4


def test_default_cli_does_not_read_config_or_create_http_session(monkeypatch, capsys):
    def prohibited(*_args, **_kwargs):
        pytest.fail("Default invocation must be inert")

    monkeypatch.setattr(module, "load_config", prohibited)
    monkeypatch.setattr(module.requests, "Session", prohibited)
    assert main([]) == 0
    assert "--once" in capsys.readouterr().out


def test_once_cli_success_contains_no_connection_data(config_files, monkeypatch, capsys):
    session = FakeSession()
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    assert main(["--once", "--config", str(config_files.bridge_path)]) == 0
    output = capsys.readouterr().out
    assert "registered" in output
    for secret in [ORIGIN, USER, PASSWORD, BYPASS, SECRET]:
        assert secret not in output
    assert session.closed


def test_once_cli_failure_redacts_network_exception(config_files, monkeypatch, capsys):
    session = FakeSession([requests.ConnectionError(ORIGIN + " " + BYPASS + " " + SECRET + " " + PASSWORD)])
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    assert main(["--once", "--config", str(config_files.bridge_path)]) == 1
    output = capsys.readouterr().out
    assert "could not" in output
    for secret in [ORIGIN, BYPASS, SECRET, PASSWORD]:
        assert secret not in output
    assert session.closed


def test_serve_stops_on_signal_and_restores_handlers(monkeypatch, capsys):
    handlers = {}
    originals = {signal.SIGINT: object(), signal.SIGTERM: object()}
    changes = []

    def set_handler(signum, handler):
        previous = handlers.get(signum, originals[signum])
        handlers[signum] = handler
        changes.append((signum, handler))
        return previous

    class FakeBridge:
        closed = False

        def __init__(self, _path):
            pass

        def step(self):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return True

        def close(self):
            FakeBridge.closed = True

    monkeypatch.setattr(module, "SiteBridge", FakeBridge)
    monkeypatch.setattr(module.signal, "signal", set_handler)
    assert main(["--serve"]) == 0
    assert FakeBridge.closed
    assert handlers == originals
    assert len(changes) == 4
    assert "registered" in capsys.readouterr().out
