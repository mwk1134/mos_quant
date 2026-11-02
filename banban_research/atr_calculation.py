"""Shared ATR arithmetic for live candidates and historical simulations."""

import math


def linear_quantile(values, percentile):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("백분위 계산 데이터가 없습니다")
    position = (len(ordered) - 1) * (float(percentile) / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def atr_series(rows, period, method="SMA"):
    if method not in {"SMA", "WILDER"}:
        raise ValueError("atr_method must be SMA or WILDER")
    values, true_ranges = [], []
    smoothed = None
    for index, row in enumerate(rows):
        if not index:
            continue
        previous_close = float(rows[index - 1]["close"])
        true_range = max(
            float(row["high"] - row["low"]),
            abs(float(row["high"] - previous_close)),
            abs(float(row["low"] - previous_close)),
        )
        true_ranges.append(true_range)
        if len(true_ranges) < period:
            continue
        if method == "SMA" or smoothed is None:
            smoothed = sum(true_ranges[-period:]) / float(period)
        else:
            smoothed = (smoothed * (period - 1) + true_range) / float(period)
        atr = smoothed
        if not math.isfinite(atr):
            raise ArithmeticError("ATR_CALCULATION_NONFINITE")
        values.append({"date": str(row["date"]), "atr": float(atr),
                       "atr_pct": float(atr / float(row["close"]) * 100.0)})
    return values


def filter_value(series, index, market_date, settings):
    lookback = int(settings["lookback_days"])
    if index + 1 < lookback + 1:
        return {"ready": False, "passed": False, "reason": "insufficient_history",
                "market_date": str(market_date), "atr_value_count": index + 1}
    current = series[index]
    window = series[index - lookback:index]
    values = [float(value["atr_pct"]) for value in window]
    cutoff = linear_quantile(values, settings["max_percentile"])
    rank = sum(value <= float(current["atr_pct"]) for value in values) / float(len(values)) * 100.0
    passed = bool(float(current["atr_pct"]) <= float(cutoff) + 1e-12)
    return {"ready": True, "passed": passed,
            "reason": "filter_passed" if passed else "high_atr_filtered",
            "market_date": str(current["date"]), "atr": float(current["atr"]),
            "atr_pct": float(current["atr_pct"]), "cutoff_atr_pct": float(cutoff),
            "percentile_rank": float(rank), "window_start_date": str(window[0]["date"]),
            "window_end_date": str(window[-1]["date"]), "comparison_excludes_current": True}


def build_filter_by_session(rows, settings):
    series = atr_series(rows, int(settings["atr_period"]), settings.get("atr_method", "SMA"))
    index_by_date = {row["date"]: index for index, row in enumerate(series)}
    return {str(current["date"]): filter_value(
        series, index_by_date.get(str(previous["date"]), -1), previous["date"], settings
    ) for previous, current in zip(rows, rows[1:])}
