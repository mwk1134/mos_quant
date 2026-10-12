"""Keep the fixed KMW Site connected to its authenticated PC gateway.

This sidecar only reads the gateway's status and registers connection metadata.
It never sends engine commands, activates trading, or calls a brokerage. Import
and the CLI's default invocation perform no network requests.

Use an ignored, owner-owned 0600 JSON file (default
``.autotrade/site_bridge.json``) containing ``site_origin``,
``sites_bypass_token``, ``registration_secret``, and ``gateway_config_path``.
The gateway path is resolved relative to the process's working directory; for
example, ``.autotrade/phone_gateway.json``. Credentials stay in those private
configuration files and request bodies/headers, never command-line arguments
or logs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import threading
from typing import Any
from urllib.parse import urlsplit

import requests

from kmw_phone_gateway import load_config as load_gateway_config


SITE_ORIGIN = "https://kmw-trading-console.ugi1134.chatgpt.site"
_CONFIG_FIELDS = {"site_origin", "sites_bypass_token", "registration_secret", "gateway_config_path"}
_MAX_CONFIG = 65_536
_MAX_RESPONSE = 262_144
_HTTP_TIMEOUT = 20
_POLL_INTERVAL = 5


class BridgeError(RuntimeError):
    """An intentionally generic error safe to display without request data."""


def _secret(value: Any) -> str:
    if (not isinstance(value, str) or not 16 <= len(value) <= 8192
            or any(ord(char) <= 32 or ord(char) >= 127 for char in value)):
        raise ValueError("Site connection secret is invalid")
    return value


def load_config(path: str | Path) -> dict[str, str]:
    """Read only a bounded, owner-owned, regular nonsymlink 0600 file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()):
            raise ValueError("Site connection configuration must be an owner-owned regular 0600 file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(_MAX_CONFIG + 1)
        if len(raw) > _MAX_CONFIG:
            raise ValueError("Site connection configuration is too large")
        values = json.loads(raw)
        if not isinstance(values, dict) or set(values) != _CONFIG_FIELDS:
            raise ValueError("Site connection configuration fields are invalid")
        # Both the suffix and the exact configured destination are fixed: never
        # send Site bypass or registration credentials to another HTTPS host.
        origin = values["site_origin"]
        if not isinstance(origin, str) or origin != SITE_ORIGIN:
            raise ValueError("Site connection origin is not the configured KMW Site")
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname.endswith(".chatgpt.site")
                or parsed.netloc != parsed.hostname or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Site connection origin is invalid")
        _secret(values["sites_bypass_token"])
        _secret(values["registration_secret"])
        gateway_path = values["gateway_config_path"]
        if (not isinstance(gateway_path, str) or not gateway_path.strip()
                or len(gateway_path) > 4096 or "\x00" in gateway_path
                or "://" in gateway_path):
            raise ValueError("Gateway configuration path is invalid")
        return values
    finally:
        os.close(fd)


def _json_response(response: Any) -> Any:
    """Read a bounded JSON response; never include its contents in errors."""
    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("application/json"):
        raise BridgeError("Connection response could not be verified")
    data = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        data.extend(chunk)
        if len(data) > _MAX_RESPONSE:
            raise BridgeError("Connection response could not be verified")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise BridgeError("Connection response could not be verified") from None


class SiteBridge:
    """Watch private configuration and register each changed gateway once."""

    def __init__(self, config_path: str | Path, *, session: Any = None):
        self.config_path = Path(config_path)
        self.session = session if session is not None else requests.Session()
        # Do not inherit proxies, .netrc credentials, custom CA environment
        # variables, or other ambient settings for these credentialed requests.
        self.session.trust_env = False
        self.session.verify = True
        self._registered_fingerprint: bytes | None = None

    def close(self) -> None:
        self.session.close()

    def _healthy_gateway(self, origin: str, username: str, password: str) -> None:
        credential = base64.b64encode((username + ":" + password).encode("utf-8")).decode("ascii")
        response = self.session.get(
            origin + "/api/status", headers={"Authorization": "Basic " + credential},
            timeout=_HTTP_TIMEOUT, allow_redirects=False, verify=True, stream=True,
        )
        try:
            if response.status_code != 200 or response.headers.get("X-KMW-Phone-Gateway") != "1":
                raise BridgeError("PC gateway status could not be verified")
            status = _json_response(response)
            if (not isinstance(status, dict) or type(status.get("enabled")) is not bool
                    or type(status.get("running")) is not bool):
                raise BridgeError("PC gateway status could not be verified")
        finally:
            response.close()

    def _register(self, config: dict[str, str], connection: dict[str, str]) -> None:
        response = self.session.post(
            config["site_origin"] + "/api/bridge/register", json=connection,
            headers={"OAI-Sites-Authorization": "Bearer " + config["sites_bypass_token"],
                     "Authorization": "Bearer " + config["registration_secret"]},
            timeout=_HTTP_TIMEOUT, allow_redirects=False, verify=True, stream=True,
        )
        try:
            if response.status_code not in {200, 201}:
                raise BridgeError("Site connection registration could not be verified")
            payload = _json_response(response)
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise BridgeError("Site connection registration could not be verified")
        finally:
            response.close()

    def step(self) -> bool:
        """Return True only after changed metadata has been registered."""
        try:
            config = load_config(self.config_path)
            gateway = load_gateway_config(config["gateway_config_path"])
            connection = {"origin": gateway["public_origin"].rstrip("/"),
                          "username": gateway["username"], "password": gateway["password"]}
            fingerprint = hashlib.sha256(json.dumps(
                {**connection, "site_origin": config["site_origin"],
                 "sites_bypass_token": config["sites_bypass_token"],
                 "registration_secret": config["registration_secret"]},
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")).digest()
            if fingerprint == self._registered_fingerprint:
                return False
            self._healthy_gateway(connection["origin"], connection["username"], connection["password"])
            self._register(config, connection)
            self._registered_fingerprint = fingerprint
            return True
        except BridgeError:
            raise
        except (OSError, ValueError, TypeError, KeyError, requests.RequestException):
            # Exception strings can contain URLs, credentials, headers, or
            # server response text. Keep the public diagnostic constant.
            raise BridgeError("Site connection could not be registered; private configuration or connectivity needs checking") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep the fixed KMW Site connected to its authenticated PC gateway")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Verify and register the current gateway once")
    mode.add_argument("--serve", action="store_true", help="Watch configuration every five seconds")
    parser.add_argument("--config", default=".autotrade/site_bridge.json", help="Owner-owned 0600 JSON configuration file")
    args = parser.parse_args(argv)
    if not (args.once or args.serve):
        parser.print_help()
        return 0

    bridge = SiteBridge(args.config)
    stop = threading.Event()
    previous_handlers: dict[int, Any] = {}
    try:
        if args.once:
            try:
                bridge.step()
            except BridgeError:
                print("KMW Site connection could not be registered. Check private configuration and connectivity.", flush=True)
                return 1
            print("KMW Site connection registered.", flush=True)
            return 0

        def request_stop(_signum: int, _frame: Any) -> None:
            stop.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_stop)
        failed = False
        while not stop.is_set():
            try:
                changed = bridge.step()
                if changed:
                    print("KMW Site connection registered.", flush=True)
                failed = False
            except BridgeError:
                if not failed:
                    print("KMW Site connection unavailable; metadata registration will be checked again.", flush=True)
                failed = True
            stop.wait(_POLL_INTERVAL)
        return 0
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
