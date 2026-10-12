"""Authenticated HTTPS tunnel entry point for the loopback KMW dashboard.

This server itself only binds to 127.0.0.1. A separately configured Cloudflare
HTTPS tunnel may connect to it. Importing or constructing it never activates the
engine, opens a tunnel, or contacts a brokerage.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


_ACTIONS = {"status", "preview", "configure", "enable", "disable", "run"}
_POST_ROUTES = {"/api/" + action for action in _ACTIONS - {"status"}}
_MAX_BODY = 16_384
_MAX_RESPONSE = 2 * 1024 * 1024
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")


def _public_address(public_origin: str, trusted_host: str | None) -> tuple[str, str]:
    """Validate an explicit origin; never derive trust from request headers."""
    if not isinstance(public_origin, str):
        raise ValueError("An explicit HTTPS public_origin is required")
    try:
        parsed = urlsplit(public_origin)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None
                or parsed.password is not None or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.port is not None
                or parsed.netloc != host
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)):
            raise ValueError
    except ValueError:
        raise ValueError("public_origin must be an HTTPS origin without a path or port") from None
    cloudflare_host = re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.trycloudflare\.com", host)
    if trusted_host is not None:
        if not isinstance(trusted_host, str) or trusted_host != host:
            raise ValueError("trusted_host must match the configured origin exactly")
    elif not cloudflare_host:
        raise ValueError("This origin requires an explicit matching trusted_host")
    return "https://" + host, host


def _credentials_digest(username: str, password: str, *, allow_short_password: bool = False) -> bytes:
    if type(allow_short_password) is not bool:
        raise ValueError("allow_short_password must be an explicit boolean")
    if (not isinstance(username, str) or not username.strip() or len(username) > 128
            or ":" in username or any(ord(c) < 32 or ord(c) == 127 for c in username)):
        raise ValueError("A nonempty username without colons or control characters is required")
    minimum = 4 if allow_short_password else 24
    if (not isinstance(password, str) or len(password) < minimum or len(password) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in password)):
        raise ValueError(f"A password of at least {minimum} characters without control characters is required")
    return hashlib.sha256((username + ":" + password).encode("utf-8")).digest()


def _port(value: Any, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 if allow_zero else 1) <= value <= 65535:
        raise ValueError("A valid integer port is required")
    return value


class PhoneGatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *, public_origin: str, username: str, password: str,
                 trusted_host: str | None = None, engine_port: int = 8701,
                 host: str = "127.0.0.1", port: int = 8702,
                 allow_short_password: bool = False):
        if host != "127.0.0.1":
            raise ValueError("KMW phone gateway must bind to 127.0.0.1")
        self.public_origin, self.allowed_host = _public_address(public_origin, trusted_host)
        self.auth_digest = _credentials_digest(username, password, allow_short_password=allow_short_password)
        self.auth_lock = threading.Lock()
        self.auth_failures: dict[str, list[float]] = {}
        self.engine_port = _port(engine_port)
        self.engine_host = f"127.0.0.1:{self.engine_port}"
        self.engine_origin = "http://" + self.engine_host
        super().__init__((host, _port(port, allow_zero=True)), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: PhoneGatewayServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, _format: str, *args: Any) -> None:
        # Do not log URLs, headers, credentials, body contents, or account data.
        pass

    def _respond(self, code: int, payload: bytes, content_type: str = "application/json; charset=utf-8",
                 *, challenge: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-KMW-Phone-Gateway", "1")
        self.send_header("Connection", "close")
        if code == 429:
            self.send_header("Retry-After", "60")
        if challenge:
            self.send_header("WWW-Authenticate", 'Basic realm="KMW engine", charset="UTF-8"')
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _error(self, code: int, message: str, *, challenge: bool = False) -> None:
        self._respond(code, json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8"),
                      challenge=challenge)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # BaseHTTPRequestHandler errors must also avoid request data in HTML/logs.
        self._error(code, "요청을 처리할 수 없습니다.")

    def _authenticated(self) -> bool:
        values = self.headers.get_all("Authorization", [])
        credential = b""
        well_formed = False
        if len(values) == 1 and len(values[0]) <= 4096:
            scheme, separator, encoded = values[0].partition(" ")
            if separator and scheme.lower() == "basic":
                try:
                    credential = base64.b64decode(encoded, validate=True)
                    well_formed = True
                except (ValueError, UnicodeEncodeError):
                    pass
        matches = hmac.compare_digest(hashlib.sha256(credential).digest(), self.server.auth_digest)
        # The explicitly configured Cloudflare tunnel supplies the visitor IP;
        # malformed/missing values share a bounded fallback bucket.
        addresses = self.headers.get_all("CF-Connecting-IP", [])
        try:
            visitor = str(ipaddress.ip_address(addresses[0])) if len(addresses) == 1 else "shared"
        except ValueError:
            visitor = "shared"
        now = time.monotonic()
        with self.server.auth_lock:
            if visitor not in self.server.auth_failures and len(self.server.auth_failures) >= 1024:
                for key, failures in list(self.server.auth_failures.items()):
                    if not failures or failures[-1] <= now - 60:
                        del self.server.auth_failures[key]
                if len(self.server.auth_failures) >= 1024:
                    visitor = "overflow"
            failures = [stamp for stamp in self.server.auth_failures.get(visitor, []) if stamp > now - 60]
            limited = len(failures) >= 5
            if not limited and well_formed and matches:
                failures = []
            elif not limited and values:
                failures.append(now)
            self.server.auth_failures[visitor] = failures
        if limited:
            self._error(429, "로그인 실패가 반복되어 잠시 제한했습니다. 60초 뒤 다시 시도하세요.")
            return False
        if not (well_formed and matches):
            self._error(401, "관리 화면 로그인이 필요합니다.", challenge=True)
            return False
        return True

    def _trusted_request(self) -> bool:
        # Cloudflare overwrites this incoming header with the visitor's actual
        # protocol. Trust it only at this explicitly configured loopback tunnel
        # entry point, and never challenge for Basic credentials over HTTP.
        # https://developers.cloudflare.com/fundamentals/reference/http-headers/
        if self.headers.get_all("X-Forwarded-Proto", []) != ["https"]:
            self._error(403, "HTTPS 관리 화면 주소로 접속하세요.")
            return False
        if not self._authenticated():
            return False
        if self.headers.get_all("Host", []) != [self.server.allowed_host]:
            self._error(403, "설정된 관리 화면 주소에서 접속하세요.")
            return False
        return True

    def _route(self) -> str | None:
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                return None
            if parsed.query:
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                values = query.get("kmw_api", [])
                if parsed.path != "/" or set(query) != {"kmw_api"} or len(values) != 1 or values[0] not in _ACTIONS:
                    return None
                return "/api/" + values[0]
            return parsed.path
        except ValueError:
            return None

    def _proxy(self, route: str, body: bytes | None = None, csrf_token: str | None = None) -> None:
        # Fixed loopback target and fixed allowed route. No redirects or retries.
        headers = {"Host": self.server.engine_host}
        if self.command == "POST":
            headers.update({"Origin": self.server.engine_origin, "Content-Type": "application/json",
                            "Content-Length": str(len(body or b"")), "X-CSRF-Token": csrf_token or ""})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.engine_port, timeout=120)
        try:
            connection.request(self.command, route, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(_MAX_RESPONSE + 1)
            if not payload or len(payload) > _MAX_RESPONSE:
                self._error(502, "PC 엔진 응답을 확인할 수 없습니다. 처리 상태를 확인하세요.")
                return
            content_type = response.getheader("Content-Type", "")
            if response.status in range(300, 400) or not (
                    content_type.lower().startswith("application/json")
                    or (self.command == "GET" and route == "/" and content_type.lower().startswith("text/html"))):
                self._error(502, "PC 엔진 응답 형식이 올바르지 않습니다.")
                return
            if self.command == "GET" and route == "/" and response.status == 200 and content_type.lower().startswith("text/html"):
                # This server is explicitly configured for authenticated phone
                # access. Change only its known static display-mode sentence;
                # never infer mode from incoming headers or alter the CSRF token.
                payload = payload.replace(
                    "이 화면은 이 PC의 127.0.0.1에서만 열립니다.".encode("utf-8"),
                    "로그인으로 보호된 휴대폰 관리 화면입니다.".encode("utf-8"),
                )
            self._respond(response.status, payload,
                          "text/html; charset=utf-8" if content_type.lower().startswith("text/html")
                          else "application/json; charset=utf-8")
        except (OSError, http.client.HTTPException, ValueError):
            self._error(502, "PC 엔진에 연결하지 못했습니다. 처리 결과를 확인하기 전에는 요청을 다시 보내지 마세요.")
        finally:
            connection.close()

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        route = self._route()
        if route not in {"/", "/api/status"}:
            self._error(404, "페이지를 찾을 수 없습니다.")
            return
        self._proxy(route)

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        route = self._route()
        if route not in _POST_ROUTES:
            self._error(404, "지원하지 않는 요청입니다.")
            return
        if self.headers.get_all("Origin", []) != [self.server.public_origin]:
            self._error(403, "관리 화면과 같은 주소에서 요청해야 합니다.")
            return
        tokens = self.headers.get_all("X-CSRF-Token", [])
        if (len(tokens) != 1 or not tokens[0] or len(tokens[0]) > 256
                or any(ord(c) < 32 or ord(c) == 127 for c in tokens[0])):
            self._error(403, "요청 인증 정보를 확인하세요.")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (len(self.headers.get_all("Content-Type", [])) != 1
                or self.headers.get_content_type() != "application/json"
                or self.headers.get_all("Transfer-Encoding", [])
                or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,5}", lengths[0])
                or not 0 < int(lengths[0]) <= _MAX_BODY):
            self._error(400, "JSON 요청 형식이 올바르지 않습니다.")
            return
        try:
            body = self.rfile.read(int(lengths[0]))
            if len(body) != int(lengths[0]) or not isinstance(json.loads(body), dict):
                raise ValueError
        except (OSError, ValueError, UnicodeDecodeError):
            self._error(400, "JSON 요청 형식이 올바르지 않습니다.")
            return
        # Keep live acknowledgement, review ID, and the browser-provided CSRF
        # token intact. The existing engine must validate every one itself.
        self._proxy(route, body, tokens[0])

    def _unsupported(self) -> None:
        if self._trusted_request():
            self._error(405, "지원하지 않는 요청 방식입니다.")

    do_HEAD = _unsupported
    do_PUT = _unsupported
    do_DELETE = _unsupported
    do_PATCH = _unsupported
    do_OPTIONS = _unsupported


def create_server(**kwargs: Any) -> PhoneGatewayServer:
    return PhoneGatewayServer(**kwargs)


def load_config(path: str | Path) -> dict[str, Any]:
    """Read credentials only from an owner-owned, nonsymlink 0600 JSON file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError("Gateway configuration must be an owner-owned regular file with mode 0600")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("Gateway configuration is too large")
        values = json.loads(raw)
        allowed = {"public_origin", "username", "password", "engine_port", "trusted_host", "port", "allow_short_password"}
        if not isinstance(values, dict) or set(values) - allowed or not {"public_origin", "username", "password"} <= set(values):
            raise ValueError("Gateway configuration fields are invalid")
        _public_address(values["public_origin"], values.get("trusted_host"))
        _credentials_digest(values["username"], values["password"], allow_short_password=values.get("allow_short_password", False))
        _port(values.get("engine_port", 8701))
        _port(values.get("port", 8702))
        return values
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authenticated loopback gateway for an explicitly configured HTTPS tunnel")
    parser.add_argument("--serve", action="store_true", help="Serve the configured gateway on 127.0.0.1")
    parser.add_argument("--config", required=True, help="Owner-owned 0600 JSON configuration file")
    parser.add_argument("--port", type=int, help="Override the configured loopback port (default 8702)")
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required; no service or public tunnel is started by default")
    try:
        values = load_config(args.config)
        if args.port is not None:
            values["port"] = args.port
        server = create_server(**values)
    except (OSError, ValueError):
        parser.exit(2, "Gateway configuration could not be loaded or the loopback port could not be bound.\n")
    try:
        print(f"KMW phone gateway: 127.0.0.1:{server.server_port} · authentication required", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
