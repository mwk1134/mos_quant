"""Launcher checks use fake processes and sockets; no tunnel or engine runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import pytest

import kmw_phone_access as access
import kmw_phone_gateway as gateway


ORIGIN = "https://example-test.trycloudflare.com"


def test_extract_announced_https_origin():
    assert access.public_origin_from_line(f"INFO | {ORIGIN} |") == ORIGIN


@pytest.mark.parametrize("value", [
    "http://example-test.trycloudflare.com",
    "https://example-test.trycloudflare.com.evil.example",
    "https://example-test.trycloudflare.com:443",
    "https://example-test.trycloudflare.com/other",
    "https://example-test.trycloudflare.com?token=anything",
    "https://example-test.trycloudflare.com#fragment",
    "https://user@example-test.trycloudflare.com",
    "https://-example.trycloudflare.com",
    "https://example-.trycloudflare.com",
])
def test_extractor_does_not_accept_a_partial_or_noncanonical_origin(value):
    assert access.public_origin_from_line(f"INFO | {value} |") is None


def test_configuration_is_private_and_reuses_user_login(tmp_path):
    path = tmp_path / ".autotrade" / "phone_gateway.json"
    first = access.write_phone_config(path, ORIGIN)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600
    assert path.lstat().st_uid == os.getuid()
    assert first["username"] == "kmw" and len(first["password"]) >= 32
    second = access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert second["username"] == first["username"]
    assert second["password"] == first["password"]
    assert second["public_origin"] == "https://second-test.trycloudflare.com"
    assert json.loads(path.read_text()) == second
    assert not list(path.parent.glob("*.tmp"))


def test_tunnel_restart_keeps_both_accounts_loopback_ports(tmp_path):
    path = tmp_path / "phone_gateway.json"
    previous = access.write_phone_config(path, ORIGIN)
    previous.update(engine_port=8701, kmw2_engine_port=8703)
    path.write_text(json.dumps(previous))
    renewed = access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert renewed["engine_port"] == 8701 and renewed["kmw2_engine_port"] == 8703
    assert renewed["username"] == previous["username"]
    assert renewed["password"] == previous["password"]
    assert gateway.load_config(path) == renewed


def test_tunnel_restart_preserves_three_distinct_account_ports_and_private_login(tmp_path):
    path = tmp_path / "phone_gateway.json"
    previous = access.write_phone_config(path, ORIGIN)
    previous.update(engine_port=8701, kmw2_engine_port=8703, jeh_engine_port=8704)
    path.write_text(json.dumps(previous))
    renewed = access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert {field: renewed[field] for field in ["engine_port", "kmw2_engine_port", "jeh_engine_port"]} == {
        "engine_port": 8701, "kmw2_engine_port": 8703, "jeh_engine_port": 8704}
    assert renewed["username"] == previous["username"] and renewed["password"] == previous["password"]
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600
    assert gateway.load_config(path) == renewed


def test_jeh_can_be_configured_without_kmw2_and_is_kept_when_origin_changes(tmp_path):
    path = tmp_path / "phone_gateway.json"
    previous = access.write_phone_config(path, ORIGIN)
    previous["jeh_engine_port"] = 8704
    path.write_text(json.dumps(previous))
    renewed = access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert renewed["engine_port"] == 8701 and renewed["jeh_engine_port"] == 8704
    assert "kmw2_engine_port" not in renewed
    assert gateway.load_config(path) == renewed


@pytest.mark.parametrize("third_port", [8701, 8703, True, False, 0, -1, 65536, "8704", 8704.0, []])
def test_invalid_or_colliding_jeh_port_never_rewrites_the_existing_config(tmp_path, third_port):
    path = tmp_path / "phone_gateway.json"
    config = access.write_phone_config(path, ORIGIN)
    config.update(kmw2_engine_port=8703, jeh_engine_port=third_port)
    path.write_text(json.dumps(config))
    original = path.read_text()
    with pytest.raises(ValueError, match="engine ports"):
        access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert path.read_text() == original


@pytest.mark.parametrize("second_port", [8701, True, 0, 65536, "8703"])
def test_invalid_second_account_port_is_not_saved(tmp_path, second_port):
    path = tmp_path / "phone_gateway.json"
    config = access.write_phone_config(path, ORIGIN)
    config["kmw2_engine_port"] = second_port
    path.write_text(json.dumps(config))
    original = path.read_text()
    with pytest.raises(ValueError, match="engine ports"):
        access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert path.read_text() == original


def test_reuses_user_requested_short_login_and_preserves_explicit_opt_in(tmp_path):
    path = tmp_path / "phone_gateway.json"
    previous = {"public_origin": ORIGIN, "username": "kmw", "password": "1234",
                "allow_short_password": True}
    path.write_text(json.dumps(previous))
    path.chmod(0o600)
    config = access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert config["password"] == "1234"
    assert config["username"] == "kmw"
    assert config["allow_short_password"] is True
    assert config["public_origin"] == "https://second-test.trycloudflare.com"
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600
    assert json.loads(path.read_text()) == config
    assert gateway.load_config(path) == config
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("opt_in", [None, False])
def test_existing_short_login_without_explicit_opt_in_is_rejected_unchanged(tmp_path, opt_in):
    path = tmp_path / "phone_gateway.json"
    previous = {"public_origin": ORIGIN, "username": "kmw", "password": "1234"}
    if opt_in is not None:
        previous["allow_short_password"] = opt_in
    original = json.dumps(previous)
    path.write_text(original)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        access.write_phone_config(path, "https://second-test.trycloudflare.com")
    assert path.read_text() == original
    assert stat.S_IMODE(path.lstat().st_mode) == 0o600


@pytest.mark.parametrize("flag", [None, 0, 1, "true", "false", [], {}])
def test_existing_config_short_password_flag_requires_a_boolean(tmp_path, flag):
    path = tmp_path / "phone_gateway.json"
    previous = {"username": "kmw", "password": "p" * 40, "allow_short_password": flag}
    original = json.dumps(previous)
    path.write_text(original)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        access.write_phone_config(path, ORIGIN)
    assert path.read_text() == original


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o644])
def test_existing_config_must_be_private_writable_regular_file(tmp_path, mode):
    path = tmp_path / "phone_gateway.json"
    path.write_text(json.dumps({"username": "kmw", "password": "p" * 40}))
    path.chmod(mode)
    with pytest.raises(ValueError, match="0600"):
        access.write_phone_config(path, ORIGIN)


@pytest.mark.parametrize("exists", [True, False])
def test_existing_or_dangling_config_symlink_is_refused(tmp_path, exists):
    target = tmp_path / "target.json"
    if exists:
        target.write_text(json.dumps({"username": "kmw", "password": "p" * 40}))
        target.chmod(0o600)
    path = tmp_path / "phone_gateway.json"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        access.write_phone_config(path, ORIGIN)
    assert path.is_symlink()


def test_tunnel_command_has_only_fixed_loopback_target(tmp_path):
    path = tmp_path / "phone_cloudflared.json"
    command = access.tunnel_command("/test/cloudflared", 8702, path)
    assert command == ["/test/cloudflared", "tunnel", "--config", str(path),
                       "--no-autoupdate", "--loglevel", "info", "--url",
                       "http://127.0.0.1:8702"]
    assert not any("password" in word or "token" in word or "credential" in word for word in command)


@pytest.mark.parametrize("port", [True, 0, -1, 65536])
def test_tunnel_command_rejects_invalid_ports(tmp_path, port):
    with pytest.raises(ValueError, match="port"):
        access.tunnel_command("cloudflared", port, tmp_path / "config.json")


def test_cli_without_start_flag_opens_no_socket_or_process(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Default command must not open listeners or launch processes")
    monkeypatch.setattr(access.socket, "socket", forbidden)
    monkeypatch.setattr(access.subprocess, "Popen", forbidden)
    monkeypatch.setattr(access, "start_phone_access", forbidden)
    monkeypatch.setattr(sys, "argv", ["kmw_phone_access.py"])
    access.main()
    assert "--start" in capsys.readouterr().out


def test_occupied_destination_fails_before_tunnel_or_gateway_start(tmp_path, monkeypatch):
    events = []

    class OccupiedSocket:
        def bind(self, address):
            events.append(("bind", address))
            raise OSError("Address already in use")

        def close(self):
            events.append(("close",))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Must own the destination before starting any tunnel or gateway")

    monkeypatch.setattr(access.shutil, "which", lambda _name: "/test/cloudflared")
    monkeypatch.setattr(access.socket, "socket", lambda *_args: OccupiedSocket())
    monkeypatch.setattr(access.subprocess, "Popen", forbidden)
    monkeypatch.setattr(gateway, "create_server", forbidden)
    with pytest.raises(OSError, match="already in use"):
        access.start_phone_access(tmp_path / ".autotrade" / "phone_gateway.json")
    assert events == [("bind", ("127.0.0.1", 8702)), ("close",)]


def test_gateway_thread_failure_does_not_shutdown_an_unstarted_server(tmp_path, monkeypatch):
    events = []

    class FakeSocket:
        def bind(self, address):
            events.append("bound")

        def listen(self, _backlog):
            events.append("listening")

        def getsockname(self):
            return ("127.0.0.1", 8702)

        def close(self):
            events.append("socket_closed")

    class FakeProcess:
        stdout = [f"INFO | {ORIGIN} |\n"]
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True
            events.append("tunnel_terminated")

        def wait(self, **_kwargs):
            return 0

    class FakeServer:
        socket = FakeSocket()
        def serve_forever(self):
            raise AssertionError("Failed thread must not run the gateway")

        def shutdown(self):
            events.append("unsafe_shutdown")

        def server_close(self):
            self.socket.close()

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            if getattr(self.target, "__name__", "") == "observe_tunnel":
                self.target()
            else:
                raise RuntimeError("cannot start gateway thread")

    def popen(*_args, **_kwargs):
        assert events[:2] == ["bound", "listening"]
        events.append("tunnel_started")
        return FakeProcess()

    def create(**config):
        assert config["port"] == 0
        events.append("gateway_constructed")
        return FakeServer()

    monkeypatch.setattr(access.shutil, "which", lambda _name: "/test/cloudflared")
    monkeypatch.setattr(access.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(access.subprocess, "Popen", popen)
    monkeypatch.setattr(access.threading, "Thread", FakeThread)
    monkeypatch.setattr(access.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(gateway, "create_server", create)
    with pytest.raises(RuntimeError, match="cannot start gateway thread"):
        access.start_phone_access(tmp_path / "phone_gateway.json")
    assert "unsafe_shutdown" not in events
    assert "tunnel_terminated" in events
