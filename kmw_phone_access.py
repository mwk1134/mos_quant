"""Explicitly start a password-protected, temporary phone connection.

Importing this module and running it without --start opens no listener or tunnel.
It never enables the trading engine or calls a brokerage API.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time


_PUBLIC_URL = re.compile(r"(?<!\S)https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com(?=$|\s|\|)")
DEFAULT_CONFIG = Path(__file__).resolve().parent / ".autotrade" / "phone_gateway.json"


def public_origin_from_line(line: str) -> str | None:
    match = _PUBLIC_URL.search(line)
    return match.group(0) if match else None


def tunnel_command(executable: str, port: int, config_path: Path) -> list[str]:
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Invalid gateway port")
    return [executable, "tunnel", "--config", str(config_path), "--no-autoupdate",
            "--loglevel", "info", "--url", f"http://127.0.0.1:{port}"]


def _private_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise ValueError("Phone configuration must be an owner-owned regular file with mode 0600")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid phone configuration")
    return value


def write_phone_config(path: Path, origin: str, *, port: int = 8702) -> dict:
    if public_origin_from_line(origin) != origin:
        raise ValueError("A specific HTTPS Quick Tunnel origin is required")
    if path.exists() or path.is_symlink():
        previous = _private_json(path)
        username, password = previous.get("username"), previous.get("password")
        allow_short_password = previous.get("allow_short_password", False)
        minimum = 4 if allow_short_password is True else 32
        if (type(allow_short_password) is not bool or not isinstance(username, str) or not username
                or not isinstance(password, str) or len(password) < minimum):
            raise ValueError("Invalid existing phone credentials")
    else:
        username, password = "kmw", secrets.token_urlsafe(32)
        allow_short_password = False
    config = {"public_origin": origin, "username": username, "password": password,
              "engine_port": 8701, "port": port}
    if allow_short_password:
        config["allow_short_password"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return config


def start_phone_access(config_path: Path = DEFAULT_CONFIG, *, port: int = 8702) -> None:
    """Called only after the user explicitly requests external phone access."""
    from kmw_phone_gateway import create_server

    executable = shutil.which("cloudflared")
    if not executable:
        raise RuntimeError("cloudflared is not installed")
    # An explicit empty configuration avoids using another project's tunnel.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tunnel_config = config_path.with_name("phone_cloudflared.json")
    descriptor = os.open(tunnel_config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("{}\n")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("TUNNEL_") and key not in {"NO_TLS_VERIFY", "NO_AUTOUPDATE"}}
    stopped, url_ready = threading.Event(), threading.Event()
    announced: list[str] = []
    server = None
    server_started = False
    process = None
    reserved_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    previous_handlers = {}
    try:
        # Own the destination before creating any public tunnel. An occupied
        # port must fail here rather than expose a different local service.
        reserved_socket.bind(("127.0.0.1", port))
        reserved_socket.listen(128)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
        process = subprocess.Popen(tunnel_command(executable, port, tunnel_config),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, env=environment)

        def observe_tunnel() -> None:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                origin = public_origin_from_line(line)
                if origin and not announced:
                    announced.append(origin)
                    url_ready.set()
            url_ready.set()

        threading.Thread(target=observe_tunnel, daemon=True).start()
        deadline = time.monotonic() + 45
        while not url_ready.wait(0.25):
            if stopped.is_set() or time.monotonic() >= deadline:
                raise RuntimeError("Phone tunnel did not become ready")
        if stopped.is_set() or not announced or process.poll() is not None:
            raise RuntimeError("Phone tunnel did not become ready")
        config = write_phone_config(config_path, announced[0], port=port)
        server = create_server(**{**config, "port": 0})
        server.socket.close()
        server.socket = reserved_socket
        server.server_address = reserved_socket.getsockname()
        server.server_name, server.server_port = server.server_address
        reserved_socket = None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server_started = True
        print(f"휴대폰 관리 주소: {announced[0]}/", flush=True)
        print(f"로그인 정보는 {config_path}에 저장했습니다. --login-info로 확인할 수 있습니다.", flush=True)
        print("엔진 활성화 상태는 변경하지 않았습니다. Ctrl+C는 휴대폰 연결만 종료합니다.", flush=True)
        while not stopped.wait(1):
            if process.poll() is not None:
                raise RuntimeError("Phone tunnel stopped; existing engine state is unchanged")
    finally:
        if server is not None:
            if server_started:
                server.shutdown()
            server.server_close()
        if reserved_socket is not None:
            reserved_socket.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def main() -> None:
    parser = argparse.ArgumentParser(description="로그인으로 보호한 임시 휴대폰 관리 연결")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--start", action="store_true", help="외부 HTTPS 연결을 명시적으로 시작")
    actions.add_argument("--login-info", action="store_true", help="저장된 로그인 정보를 본인의 터미널에 표시")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--port", type=int, default=8702)
    args = parser.parse_args()
    if args.login_info:
        config = _private_json(args.config)
        print(f"사용자 ID: {config['username']}\n비밀번호: {config['password']}")
    elif args.start:
        start_phone_access(args.config, port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
