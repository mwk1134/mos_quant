import hashlib
import json


CALCULATION_VERSION = "atr-filter-v2"


class AtrSettingsError(ValueError):
    def __init__(self, message, code="ATR_SETTINGS_INVALID", status_code=400):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def scope_ids(user_id, account_id):
    for value in (user_id, account_id):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AtrSettingsError("user_id와 account_id는 양의 정수로 필수입니다")
    return user_id, account_id


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def freeze_atr_context(settings, *, session_date, basis_date, history_start):
    return {"strategy_id": "atr_breakout_v1", "settings_version": int(settings.get("settings_version", 0)),
            "calculation_version": CALCULATION_VERSION, "session_date": session_date,
            "effective_session": settings.get("effective_session", "0001-01-01"),
            "basis_date": basis_date, "history_start": history_start,
            "settings": {key: settings[key] for key in ("atr_method", "atr_period", "lookback_days", "max_percentile", "offset_pct", "allocation_pct")}}
