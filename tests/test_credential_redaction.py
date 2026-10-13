import json

import pytest

from credential_redaction import REDACTED, environment_secret_values, redact_credentials


@pytest.mark.parametrize("field", [
    "Authorization", "Proxy-Authorization", "appkey", "app_key", "MERITZ_APP_KEY",
    "app_secret", "client_secret", "client_id", "access_token", "refresh_token",
    "id_token", "X-API-Key", "TELEGRAM_BOT_TOKEN", "password", "registration_secret",
    "sites_bypass_token",
])
def test_nested_credential_fields_are_masked_without_mutating_input(field):
    original = {"rsp_cd": "0001", "data": {"oder_no": 118082, "warn_cls_code": "0"},
                "diagnostic": [{field: {"value": "synthetic-private-value"}}]}
    result = redact_credentials(original)
    assert result["diagnostic"][0][field] == REDACTED
    assert original["diagnostic"][0][field] == {"value": "synthetic-private-value"}
    assert result["data"] is original["data"]
    assert result["rsp_cd"] == "0001" and result["data"]["oder_no"] == 118082


def test_registered_secret_literals_in_arbitrary_text_and_keys_are_masked():
    secrets = ("synthetic-key", "synthetic-secret", "synthetic-oauth-token")
    original = {"rsp_msg": "Failed for synthetic-key / synthetic-secret",
                "details": [{"url": "https://example.test/?anything=synthetic-oauth-token"}],
                "synthetic-key": "not-a-credential-field", "quantity": "0012.0000"}
    result = redact_credentials(original, secrets)
    rendered = json.dumps(result)
    assert all(secret not in rendered for secret in secrets)
    assert result[REDACTED] == "not-a-credential-field"
    assert result["quantity"] == "0012.0000"
    assert original["rsp_msg"] == "Failed for synthetic-key / synthetic-secret"


def test_safe_broker_responses_preserve_identity_types_and_exact_numbers():
    original = {"rsp_cd": "5620", "data": {"oder_no": 118082, "quantity": "0012.0000",
                "price": "100.00", "state": "DIRECT_ACCEPTED", "position_key": "1_2026-10-12"},
                "history": [{"cntg_qty": 3}], "warning": None}
    assert redact_credentials(original, ("unrelated-synthetic-key",)) is original
    safe_list, safe_tuple = [original], (original,)
    assert redact_credentials(safe_list) is safe_list
    assert redact_credentials(safe_tuple) is safe_tuple


def test_environment_secret_values_select_only_secret_values():
    env = {"MERITZ_APP_KEY": "synthetic-key", "MERITZ_APP_SECRET": "synthetic-secret",
           "TELEGRAM_BOT_TOKEN": "synthetic-bot", "GITHUB_TOKEN": "synthetic-github",
           "MERITZ_MAC_ADDRESS": "AABBCCDDEEFF", "MERITZ_ACCOUNT_PRESET": "KMW2",
           "TELEGRAM_CHAT_ID": "123", "PATH": "/private/path"}
    assert set(environment_secret_values(env)) == {
        "synthetic-key", "synthetic-secret", "synthetic-bot", "synthetic-github"}
