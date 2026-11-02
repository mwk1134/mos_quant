from copy import deepcopy
import json
import math
from typing import Any
from .atr_calculation import atr_series, filter_value


ATR_BREAKOUT_SCHEMA_VERSION = 1


ATR_BREAKOUT_SETTING_NAME = "atr_breakout_v1"


ATR_BREAKOUT_STRATEGY_ID = "atr_breakout_v1"


DEFAULT_ATR_BREAKOUT_SETTINGS = {
    "schema_version": ATR_BREAKOUT_SCHEMA_VERSION,
    "enabled": False,
    "offset_pct": 1.5,
    "atr_method": "SMA",
    "atr_period": 30,
    "lookback_days": 252,
    "max_percentile": 75.0,
    "allocation_pct": 100.0,
    "updated_at": None,
}


def _positive_int(value: Any, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}는 1 이상의 정수여야 합니다") from exc
    if normalized <= 0:
        raise ValueError(f"{field_name}는 1 이상의 정수여야 합니다")
    return normalized


def _boolean(value: Any, field_name: str, *, strict: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    if strict:
        raise ValueError(f"{field_name}는 true 또는 false여야 합니다")
    return False


def _bounded_float(
    value: Any,
    field_name: str,
    minimum: float,
    maximum: float,
    default: float,
    *,
    strict: bool,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"{field_name}는 숫자여야 합니다")
        return float(default)
    if not math.isfinite(normalized) or normalized < minimum or normalized > maximum:
        if strict:
            raise ValueError(f"{field_name} 값은 {minimum:g}~{maximum:g} 사이여야 합니다")
        return float(default)
    return float(normalized)


def _bounded_int(
    value: Any,
    field_name: str,
    minimum: int,
    maximum: int,
    default: int,
    *,
    strict: bool,
) -> int:
    try:
        normalized_float = float(value)
        normalized = int(normalized_float)
    except (TypeError, ValueError, OverflowError):
        if strict:
            raise ValueError(f"{field_name}는 정수여야 합니다")
        return int(default)
    if (
        not math.isfinite(normalized_float)
        or abs(normalized_float - normalized) > 1e-9
        or normalized < minimum
        or normalized > maximum
    ):
        if strict:
            raise ValueError(f"{field_name} 값은 {minimum}~{maximum} 사이 정수여야 합니다")
        return int(default)
    return int(normalized)


def default_atr_breakout_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_ATR_BREAKOUT_SETTINGS)


def normalize_atr_breakout_settings(
    raw: Any,
    *,
    strict: bool = False,
) -> tuple[dict[str, Any], bool]:
    original = raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            if strict:
                raise ValueError("ATR 돌파 설정 JSON 형식이 올바르지 않습니다")
            raw = {}
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("ATR 돌파 설정은 object여야 합니다")
        raw = {}

    defaults = default_atr_breakout_settings()
    method = str(raw.get("atr_method", "SMA")).strip().upper()
    method = "WILDER" if method == "RMA" else method
    if method not in {"SMA", "WILDER"}:
        if strict:
            raise ValueError("atr_method는 SMA 또는 WILDER(RMA)여야 합니다")
        method = "SMA"
    normalized = {
        "schema_version": ATR_BREAKOUT_SCHEMA_VERSION,
        "atr_method": method,
        "enabled": _boolean(raw.get("enabled", defaults["enabled"]), "enabled", strict=strict),
        "offset_pct": _bounded_float(
            raw.get("offset_pct", defaults["offset_pct"]),
            "offset_pct",
            0.0,
            30.0,
            defaults["offset_pct"],
            strict=strict,
        ),
        "atr_period": _bounded_int(
            raw.get("atr_period", defaults["atr_period"]),
            "atr_period",
            2,
            120,
            defaults["atr_period"],
            strict=strict,
        ),
        "lookback_days": _bounded_int(
            raw.get("lookback_days", defaults["lookback_days"]),
            "lookback_days",
            30,
            1000,
            defaults["lookback_days"],
            strict=strict,
        ),
        "max_percentile": _bounded_float(
            raw.get("max_percentile", defaults["max_percentile"]),
            "max_percentile",
            1.0,
            99.0,
            defaults["max_percentile"],
            strict=strict,
        ),
        "allocation_pct": _bounded_float(
            raw.get("allocation_pct", defaults["allocation_pct"]),
            "allocation_pct",
            0.0,
            100.0,
            defaults["allocation_pct"],
            strict=strict,
        ),
        "updated_at": str(raw.get("updated_at") or "").strip() or None,
    }
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if isinstance(original, str):
        try:
            original_canonical = json.dumps(
                json.loads(original),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            original_canonical = ""
    elif isinstance(original, dict):
        original_canonical = json.dumps(
            original,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        original_canonical = ""
    return normalized, canonical != original_canonical


def _normalize_price_rows(rows: Any, session_date: str) -> list[dict[str, Any]]:
    normalized = []
    for raw in list(rows or []):
        if not isinstance(raw, dict):
            continue
        date_value = str(raw.get("date") or "").strip()
        if not date_value or date_value >= str(session_date):
            continue
        try:
            open_price = float(raw.get("open") or 0)
            high_price = float(raw.get("high") or 0)
            low_price = float(raw.get("low") or 0)
            close_price = float(raw.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if (
            not all(math.isfinite(value) for value in (open_price, high_price, low_price, close_price))
            or min(open_price, high_price, low_price, close_price) <= 0
            or high_price + 1e-9 < max(open_price, close_price)
            or low_price - 1e-9 > min(open_price, close_price)
        ):
            continue
        normalized.append(
            {
                "date": date_value,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
    deduped = {row["date"]: row for row in normalized}
    return [deduped[key] for key in sorted(deduped)]


def _linear_quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("백분위 계산 데이터가 없습니다")
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * (float(percentile) / 100.0)
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return float(ordered[lower_index])
    weight = position - lower_index
    return float(
        ordered[lower_index]
        + (ordered[upper_index] - ordered[lower_index]) * weight
    )


def evaluate_atr_filter(
    rows: Any,
    session_date: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    normalized_settings, _ = normalize_atr_breakout_settings(settings, strict=True)
    price_rows = _normalize_price_rows(rows, session_date)
    period = int(normalized_settings["atr_period"])
    lookback = int(normalized_settings["lookback_days"])
    series = atr_series(price_rows, period, normalized_settings["atr_method"])
    result = filter_value(
        series, len(series) - 1,
        str(price_rows[-1]["date"]) if price_rows else "", normalized_settings,
    )
    required = period + lookback + 1
    if not result["ready"]:
        message = (
            f"ATR 필터 계산에 {required}개 내외의 일봉이 필요합니다. "
            f"현재 유효 일봉은 {len(price_rows)}개입니다."
        )
    elif result["passed"]:
        message = "ATR 고변동 상위 구간이 아니므로 돌파 후보를 계산합니다."
    else:
        message = "ATR 고변동 상위 구간이므로 신규 돌파 매수를 제외합니다."
    return {
        **result, "message": message, "atr_period": period,
        "atr_method": normalized_settings["atr_method"],
        "lookback_days": lookback,
        "max_percentile": float(normalized_settings["max_percentile"]),
        "price_row_count": len(price_rows), "atr_value_count": len(series),
        "required_price_rows": required,
    }
