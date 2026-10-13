"""Remove credentials from diagnostics without changing ordinary broker fields.

This is an output boundary, not an OAuth parser. Safe dictionaries and lists
retain their identity; branches containing credentials are copied on write.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
import re


REDACTED = "[REDACTED]"
_CREDENTIAL_NAMES = frozenset({
    "authorization", "proxyauthorization", "apikey", "appkey", "appsecret",
    "apisecret", "accesstoken", "refreshtoken", "idtoken", "clientsecret",
    "clientid", "bottoken", "githubtoken", "password", "passwd", "pwd",
    "secret", "token", "registrationsecret", "sitesbypasstoken",
})
_ENVIRONMENT_NAMES = frozenset({
    "MERITZ_APP_KEY", "MERITZ_APP_SECRET", "MERITZ_ACCESS_TOKEN",
    "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "REGISTRATION_SECRET",
    "SITES_BYPASS_TOKEN", "ENGINE_BRIDGE_SECRET", "ENGINE_BRIDGE_KEY", "KMW_BRIDGE_SECRET",
})


def _credential_name(value: str) -> bool:
    name = re.sub(r"[^a-z0-9]", "", value.lower())
    return any(name == field or name.endswith(field) for field in _CREDENTIAL_NAMES)


def environment_secret_values(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return only configured secret values, never paths or account settings."""
    source = os.environ if env is None else env
    return tuple(value for key in _ENVIRONMENT_NAMES
                 if isinstance(value := source.get(key), str) and value)


def redact_credentials(value, secrets=()):
    """Recursively mask secret fields and literal secret strings.

    Unknown ordinary fields, numeric types, receipt numbers and result codes
    remain exact. Input objects are never mutated, and credential-free objects
    are returned unchanged, including at nested levels.
    """
    literals = tuple(sorted({secret for secret in secrets
                             if isinstance(secret, str) and secret}, key=len, reverse=True))

    def text(value):
        for secret in literals:
            value = value.replace(secret, REDACTED)
        return value

    def visit(current):
        if isinstance(current, str):
            changed = text(current)
            return changed if changed != current else current
        if isinstance(current, dict):
            result = None
            for key, child in current.items():
                safe_key = text(key) if isinstance(key, str) else key
                safe_child = REDACTED if isinstance(key, str) and _credential_name(key) else visit(child)
                if safe_key != key or safe_child is not child:
                    if result is None:
                        result = dict(current)
                    if safe_key != key:
                        result.pop(key, None)
                    result[safe_key] = safe_child
            return current if result is None else result
        if isinstance(current, list):
            result = None
            for index, child in enumerate(current):
                safe_child = visit(child)
                if safe_child is not child:
                    if result is None:
                        result = list(current)
                    result[index] = safe_child
            return current if result is None else result
        if isinstance(current, tuple):
            result = tuple(visit(child) for child in current)
            return current if all(a is b for a, b in zip(current, result)) else result
        return current

    return visit(value)
