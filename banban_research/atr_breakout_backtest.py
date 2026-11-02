"""Pure backtest engine for the ATR-filtered opening breakout strategy."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import math
from typing import Any

from .atr_breakout import normalize_atr_breakout_settings
from .atr_calculation import (
    build_filter_by_session as _build_filter_by_session,
    linear_quantile as _linear_quantile,
)
from .atr_settings import freeze_atr_context


def _iso_date(value: Any, field_name: str) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 형식은 YYYY-MM-DD 이어야 합니다") from exc


def _positive_money(value: Any, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}는 0보다 큰 숫자여야 합니다") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name}는 0보다 큰 숫자여야 합니다")
    if normalized > 1_000_000_000_000:
        raise ValueError(f"{field_name} 값이 너무 큽니다")
    return float(normalized)


def _commission_rate(value: Any) -> float:
    try:
        normalized = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("commission_rate는 0~5 사이 숫자여야 합니다") from exc
    if not math.isfinite(normalized) or normalized < 0 or normalized > 5:
        raise ValueError("commission_rate는 0~5 사이 숫자여야 합니다")
    return float(normalized)


def _integer_share_count(
    allocation_amount: float,
    trigger_price: float,
    fee_rate: float,
) -> int:
    allocation = Decimal(str(allocation_amount))
    unit_cost = Decimal(str(trigger_price)) * (
        Decimal("1") + Decimal(str(fee_rate))
    )
    if allocation <= 0 or unit_cost <= 0:
        return 0
    return int(allocation // unit_cost)




def _normalize_rows(rows: Any) -> list[dict[str, Any]]:
    normalized = {}
    for raw in list(rows or []):
        if not isinstance(raw, dict):
            continue
        date_value = str(raw.get("date") or "").strip()
        try:
            date_value = _iso_date(date_value, "price date")
            open_price = float(raw.get("open") or 0.0)
            high_price = float(raw.get("high") or 0.0)
            low_price = float(raw.get("low") or 0.0)
            close_price = float(raw.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        prices = (open_price, high_price, low_price, close_price)
        if (
            not all(math.isfinite(price) and price > 0 for price in prices)
            or high_price + 1e-9 < max(open_price, close_price)
            or low_price - 1e-9 > min(open_price, close_price)
            or low_price - 1e-9 > high_price
        ):
            continue
        normalized[date_value] = {
            "date": date_value,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        }
    return [normalized[key] for key in sorted(normalized)]




def _build_yearly_metrics(
    daily_log: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    initial_seed: float,
) -> list[dict[str, Any]]:
    daily_by_year = defaultdict(list)
    trades_by_year = defaultdict(list)
    for row in daily_log:
        daily_by_year[int(str(row["date"])[:4])].append(row)
    for trade in trades:
        trades_by_year[int(str(trade["entry_date"])[:4])].append(trade)

    yearly = []
    previous_end = float(initial_seed)
    previous_mark_to_market_end = float(initial_seed)
    for year in sorted(daily_by_year):
        rows = daily_by_year[year]
        start_equity = float(previous_end)
        peak = float(start_equity)
        max_drawdown = 0.0
        mark_to_market_start = float(previous_mark_to_market_end)
        mark_to_market_peak = float(mark_to_market_start)
        mark_to_market_max_drawdown = 0.0
        for row in rows:
            account_value = float(row["reference_account_value"])
            peak = max(peak, account_value)
            drawdown = (peak - account_value) / peak if peak > 0 else 0.0
            max_drawdown = max(max_drawdown, drawdown)
            mark_to_market_value = float(row["account_value"])
            mark_to_market_peak = max(mark_to_market_peak, mark_to_market_value)
            mark_to_market_drawdown = (
                (mark_to_market_peak - mark_to_market_value) / mark_to_market_peak
                if mark_to_market_peak > 0
                else 0.0
            )
            mark_to_market_max_drawdown = max(
                mark_to_market_max_drawdown,
                mark_to_market_drawdown,
            )
        end_equity = float(rows[-1]["reference_account_value"])
        mark_to_market_end = float(rows[-1]["account_value"])
        closed = list(trades_by_year.get(year) or [])
        wins = sum(1 for trade in closed if float(trade["profit_loss"]) > 0)
        yearly.append(
            {
                "year": int(year),
                "start_equity": float(start_equity),
                "end_equity": float(end_equity),
                "return_pct": (
                    (end_equity / start_equity - 1.0) * 100.0
                    if start_equity > 0
                    else 0.0
                ),
                "max_drawdown": float(max_drawdown * 100.0),
                "mark_to_market_max_drawdown": float(
                    mark_to_market_max_drawdown * 100.0
                ),
                "trades": int(len(closed)),
                "win_rate": float(wins / len(closed) * 100.0 if closed else 0.0),
            }
        )
        previous_end = end_equity
        previous_mark_to_market_end = mark_to_market_end
    return yearly


def run_atr_breakout_backtest(
    *,
    rows: Any,
    start_date: Any,
    end_date: Any,
    initial_seed: Any,
    settings: Any,
    commission_rate: Any = 0.0,
    symbol: str = "SOXL",
) -> dict[str, Any]:
    """Run an overnight ATR breakout simulation without future-data leakage."""

    start = _iso_date(start_date, "start_date")
    end = _iso_date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date는 end_date보다 늦을 수 없습니다")
    seed = _positive_money(initial_seed, "initial_seed")
    fee_pct = _commission_rate(commission_rate)
    fee_rate = fee_pct / 100.0
    normalized_settings, _ = normalize_atr_breakout_settings(settings, strict=True)
    normalized_settings["settings_version"] = int((settings or {}).get("settings_version", 0))
    normalized_settings["effective_session"] = (settings or {}).get("effective_session", "0001-01-01")
    normalized_settings["enabled"] = True
    price_rows = _normalize_rows(rows)
    if not price_rows:
        raise ValueError("백테스트에 사용할 유효한 일봉 데이터가 없습니다")

    test_rows = [row for row in price_rows if start <= str(row["date"]) <= end]
    if not test_rows:
        raise ValueError("선택한 기간에 유효한 거래일 데이터가 없습니다")

    filter_by_session = _build_filter_by_session(price_rows, normalized_settings)
    cash = float(seed)
    position = None
    trades = []
    reference_events = []
    daily_log = []
    total_fees = 0.0
    mark_to_market_peak_value = float(seed)
    mark_to_market_max_drawdown = 0.0
    filter_passed_days = 0
    filter_excluded_days = 0
    insufficient_history_days = 0
    trigger_missed_days = 0
    buy_days = 0

    for row in test_rows:
        current_date = str(row["date"])
        sell_executed = False
        closed_trade = None
        if position is not None:
            exit_price = float(row["open"])
            gross_proceeds = float(position["shares"] * exit_price)
            sell_fee = float(gross_proceeds * fee_rate)
            net_proceeds = float(gross_proceeds - sell_fee)
            cash += net_proceeds
            total_fees += sell_fee
            total_cost = float(position["gross_cost"] + position["buy_fee"])
            profit_loss = float(net_proceeds - total_cost)
            profit_loss_pct = (
                float(profit_loss / total_cost * 100.0) if total_cost > 0 else 0.0
            )
            closed_trade = {
                "entry_date": str(position["entry_date"]),
                "entry_price": float(position["entry_price"]),
                "exit_date": current_date,
                "exit_price": exit_price,
                "shares": int(position["shares"]),
                "gross_cost": float(position["gross_cost"]),
                "gross_proceeds": gross_proceeds,
                "buy_fee": float(position["buy_fee"]),
                "sell_fee": sell_fee,
                "total_fees": float(position["buy_fee"] + sell_fee),
                "profit_loss": profit_loss,
                "profit_loss_pct": profit_loss_pct,
                "exit_reason": "NEXT_OPEN",
                "atr_context": deepcopy(position["atr_context"]),
                "holding_days": 1,
                "atr_pct": float(position["atr_pct"]),
                "cutoff_atr_pct": float(position["cutoff_atr_pct"]),
                "entry_account_value": float(position["entry_account_value"]),
                "equity_after_trade": float(cash),
                "account_return_pct": float(
                    (cash / float(position["entry_account_value"]) - 1.0) * 100.0
                    if float(position["entry_account_value"]) > 0
                    else 0.0
                ),
            }
            trades.append(closed_trade)
            reference_events.append(
                {
                    "date": str(position["entry_date"]),
                    "account_value": float(cash),
                }
            )
            position = None
            sell_executed = True

        filter_result = dict(filter_by_session.get(current_date) or {})
        buy_executed = False
        trigger_price = None
        if not bool(filter_result.get("ready")):
            filter_status = "INSUFFICIENT_HISTORY"
            insufficient_history_days += 1
        elif not bool(filter_result.get("passed")):
            filter_status = "FILTERED_OUT"
            filter_excluded_days += 1
        else:
            filter_status = "PASSED"
            filter_passed_days += 1
            trigger_price = float(
                float(row["open"])
                * (1.0 + float(normalized_settings["offset_pct"]) / 100.0)
            )
            if float(row["high"]) + 1e-12 >= float(trigger_price):
                entry_account_value = float(cash)
                allocation_amount = max(
                    0.0,
                    min(
                        cash,
                        cash
                        * float(normalized_settings["allocation_pct"])
                        / 100.0,
                    ),
                )
                shares = _integer_share_count(
                    allocation_amount,
                    trigger_price,
                    fee_rate,
                )
                if shares > 0:
                    gross_cost = float(shares * trigger_price)
                    buy_fee = float(gross_cost * fee_rate)
                    cash = max(0.0, cash - gross_cost - buy_fee)
                    total_fees += buy_fee
                    position = {
                        "entry_date": current_date,
                        "entry_price": float(trigger_price),
                        "shares": int(shares),
                        "gross_cost": gross_cost,
                        "buy_fee": buy_fee,
                        "entry_account_value": entry_account_value,
                        "atr_context": freeze_atr_context(normalized_settings,
                            session_date=current_date, basis_date=str(filter_result.get("market_date") or ""),
                            history_start=price_rows[0]["date"]),
                        "atr_pct": float(filter_result.get("atr_pct") or 0.0),
                        "cutoff_atr_pct": float(
                            filter_result.get("cutoff_atr_pct") or 0.0
                        ),
                    }
                    buy_executed = True
                    buy_days += 1
                else:
                    filter_status = "NO_BUYING_POWER"
            else:
                filter_status = "TRIGGER_NOT_REACHED"
                trigger_missed_days += 1

        position_value = (
            float(position["shares"] * float(row["close"]))
            if position is not None
            else 0.0
        )
        account_value = float(cash + position_value)
        mark_to_market_peak_value = max(mark_to_market_peak_value, account_value)
        drawdown = (
            (mark_to_market_peak_value - account_value) / mark_to_market_peak_value
            if mark_to_market_peak_value > 0
            else 0.0
        )
        mark_to_market_max_drawdown = max(mark_to_market_max_drawdown, drawdown)
        daily_log.append(
            {
                "date": current_date,
                "open_price": float(row["open"]),
                "high_price": float(row["high"]),
                "close_price": float(row["close"]),
                "filter_status": filter_status,
                "filter_passed": bool(filter_result.get("passed")),
                "atr_context": freeze_atr_context(normalized_settings,
                    session_date=current_date, basis_date=str(filter_result.get("market_date") or ""),
                    history_start=price_rows[0]["date"]),
                "atr_pct": (
                    float(filter_result["atr_pct"])
                    if filter_result.get("atr_pct") is not None
                    else None
                ),
                "cutoff_atr_pct": (
                    float(filter_result["cutoff_atr_pct"])
                    if filter_result.get("cutoff_atr_pct") is not None
                    else None
                ),
                "trigger_price": trigger_price,
                "buy_executed": bool(buy_executed),
                "sell_executed": bool(sell_executed),
                "closed_profit_loss": (
                    float(closed_trade["profit_loss"]) if closed_trade else 0.0
                ),
                "cash": float(cash),
                "position_value": position_value,
                "account_value": account_value,
                "peak_value": float(mark_to_market_peak_value),
                "drawdown": float(drawdown * 100.0),
            }
        )

    estimated_exit_fee = 0.0
    active_positions = []
    if position is not None:
        final_close = float(test_rows[-1]["close"])
        final_gross_proceeds = float(position["shares"] * final_close)
        estimated_exit_fee = float(final_gross_proceeds * fee_rate)
        reference_final_value = float(cash + final_gross_proceeds - estimated_exit_fee)
        reference_events.append(
            {
                "date": str(position["entry_date"]),
                "account_value": reference_final_value,
            }
        )
        active_positions.append(
            {
                "entry_date": str(position["entry_date"]),
                "entry_price": float(position["entry_price"]),
                "shares": int(position["shares"]),
                "market_price": final_close,
                "market_value": final_gross_proceeds,
                "estimated_exit_fee": estimated_exit_fee,
                "exit_rule": "다음 거래일 시가 전량 매도",
            }
        )
    else:
        reference_final_value = float(cash)

    events_by_date = {
        str(event["date"]): float(event["account_value"])
        for event in reference_events
    }
    reference_value = float(seed)
    reference_peak_value = float(seed)
    reference_max_drawdown = 0.0
    for row in daily_log:
        event_value = events_by_date.get(str(row["date"]))
        if event_value is not None:
            reference_value = float(event_value)
        reference_peak_value = max(reference_peak_value, reference_value)
        reference_drawdown = (
            (reference_peak_value - reference_value) / reference_peak_value
            if reference_peak_value > 0
            else 0.0
        )
        reference_max_drawdown = max(reference_max_drawdown, reference_drawdown)
        row["reference_account_value"] = float(reference_value)
        row["reference_peak_value"] = float(reference_peak_value)
        row["reference_drawdown"] = float(reference_drawdown * 100.0)

    final_value = float(reference_final_value)
    mark_to_market_final_value = float(daily_log[-1]["account_value"])
    total_return = float(final_value - seed)
    total_return_pct = float(total_return / seed * 100.0)
    start_dt = datetime.strptime(str(daily_log[0]["date"]), "%Y-%m-%d")
    end_dt = datetime.strptime(str(daily_log[-1]["date"]), "%Y-%m-%d")
    elapsed_days = max(0, int((end_dt - start_dt).days))
    years = elapsed_days / 365.0
    cagr = (
        ((final_value / seed) ** (1.0 / years) - 1.0) * 100.0
        if years > 0 and final_value > 0
        else total_return_pct
    )
    winning_trades = [trade for trade in trades if float(trade["profit_loss"]) > 0]
    losing_trades = [trade for trade in trades if float(trade["profit_loss"]) < 0]
    gross_profit = sum(float(trade["profit_loss"]) for trade in winning_trades)
    gross_loss = abs(sum(float(trade["profit_loss"]) for trade in losing_trades))
    benchmark_base_close = float(test_rows[0]["close"])
    benchmark_series = [
        {
            "date": str(row["date"]),
            "account_value": float(seed * float(row["close"]) / benchmark_base_close),
        }
        for row in test_rows
    ]
    benchmark_final = float(benchmark_series[-1]["account_value"])
    return {
        "strategy_id": "atr_breakout_v1",
        "strategy_label": "ATR 필터 돌파",
        "symbol": str(symbol or "SOXL").upper(),
        "start_date": str(daily_log[0]["date"]),
        "end_date": str(daily_log[-1]["date"]),
        "requested_start_date": start,
        "requested_end_date": end,
        "settings": normalized_settings,
        "commission_rate": fee_pct,
        "calculation_profile": "integer_shares_v2",
        "assumptions": {
            "entry": "전일 ATR%를 그 이전 비교구간과 대조한 뒤 당일 고가가 시가×(1+오프셋)에 닿으면 계산상 정확한 가격으로 체결",
            "exit": "매수 다음 거래일 시가 전량 매도",
            "position_sizing": "가용자산의 배정비율 안에서 수수료 포함 1주 비용 기준 정수주를 매수",
            "mdd": "원문 MDD는 거래별 실현 복리자산 기준이며 종가평가 MDD를 별도로 제공",
            "last_session": "마지막 거래일 신규 포지션은 종가 가상청산값으로 성과에 반영하고 미청산으로 표시",
            "order_rounding": "매수 가격은 계산값을 사용하고 체결 수량은 실제 주문처럼 정수 내림",
            "slippage": "일봉 한계로 슬리피지는 별도 반영하지 않음",
        },
        "summary": {
            "initial_seed": float(seed),
            "final_seed": final_value,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "cagr": float(cagr),
            "max_drawdown": float(reference_max_drawdown * 100.0),
            "mark_to_market_max_drawdown": float(
                mark_to_market_max_drawdown * 100.0
            ),
            "mark_to_market_final_seed": mark_to_market_final_value,
            "total_trades": int(len(trades)),
            "winning_trades": int(len(winning_trades)),
            "losing_trades": int(len(losing_trades)),
            "win_rate": float(
                len(winning_trades) / len(trades) * 100.0 if trades else 0.0
            ),
            "average_trade_return_pct": float(
                sum(float(trade["profit_loss_pct"]) for trade in trades) / len(trades)
                if trades
                else 0.0
            ),
            "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else None,
            "total_fees": float(total_fees + estimated_exit_fee),
            "estimated_exit_fee": float(estimated_exit_fee),
            "trading_days": int(len(daily_log)),
            "filter_passed_days": int(filter_passed_days),
            "filter_excluded_days": int(filter_excluded_days),
            "insufficient_history_days": int(insufficient_history_days),
            "trigger_missed_days": int(trigger_missed_days),
            "buy_days": int(buy_days),
            "exposure_pct": float(buy_days / len(daily_log) * 100.0),
            "active_positions_count": int(len(active_positions)),
            "benchmark_final_seed": benchmark_final,
            "benchmark_return_pct": float((benchmark_final / seed - 1.0) * 100.0),
        },
        "yearly": _build_yearly_metrics(daily_log, trades, seed),
        "trades": trades,
        "active_positions": active_positions,
        "daily_log": daily_log,
        "benchmark": {
            "symbol": str(symbol or "SOXL").upper(),
            "series": benchmark_series,
        },
    }
