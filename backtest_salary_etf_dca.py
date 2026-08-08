"""Backtest the salary-based ETF DCA strategy shown in the user's screenshot.

The script downloads split-adjusted closes and split-adjusted dividend events from
Yahoo Finance's chart endpoint, simulates fractional-share purchases, and writes an
auditable JSON result. It keeps external cash flows separate from investment gains.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


START_DATE = dt.date(2015, 1, 1)
REQUEST_END_DATE = dt.date(2026, 8, 5)  # exclusive; covers 2026-08-04 FX/KRX data
MONTHLY_KRW = 5_000_000.0
BONUS_KRW = 5_000_000.0
US_DIVIDEND_NET_RATE = 0.85
KR_DIVIDEND_NET_RATE = 0.846
CORE_WEIGHTS = {"VOO": 0.50, "NASDAQ100": 0.30, "SCHD": 0.20}
QQQM_INCEPTION = dt.date(2020, 10, 13)
TIGER_TICKER = "133690.KS"

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; salary-etf-backtest/1.0)"


@dataclass
class MarketSeries:
    symbol: str
    currency: str
    closes: dict[dt.date, float]
    dividends: dict[dt.date, float]
    splits: dict[dt.date, float]
    first_date: dt.date
    last_date: dt.date
    source_url: str


def unix_seconds(day: dt.date) -> int:
    return int(dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp())


def timestamp_date(value: int) -> dt.date:
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).date()


def download_series(symbol: str) -> MarketSeries:
    params = {
        "period1": unix_seconds(START_DATE),
        "period2": unix_seconds(REQUEST_END_DATE),
        "interval": "1d",
        "events": "div,splits",
        "includePrePost": "false",
    }
    url = YAHOO_CHART_URL.format(symbol=symbol)
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    payload = response.json()["chart"]
    if payload.get("error"):
        raise RuntimeError(f"Yahoo error for {symbol}: {payload['error']}")
    result = payload["result"][0]
    timestamps = result.get("timestamp") or []
    close_values = result["indicators"]["quote"][0]["close"]
    closes = {
        timestamp_date(ts): float(price)
        for ts, price in zip(timestamps, close_values)
        if price is not None and math.isfinite(price)
    }
    if not closes:
        raise RuntimeError(f"No closes returned for {symbol}")

    events = result.get("events") or {}
    dividends: dict[dt.date, float] = {}
    for event in (events.get("dividends") or {}).values():
        day = timestamp_date(int(event["date"]))
        dividends[day] = dividends.get(day, 0.0) + float(event["amount"])

    splits: dict[dt.date, float] = {}
    for event in (events.get("splits") or {}).values():
        day = timestamp_date(int(event["date"]))
        numerator = event.get("numerator")
        denominator = event.get("denominator")
        if numerator is not None and denominator:
            ratio = float(numerator) / float(denominator)
        else:
            left, right = str(event["splitRatio"]).split(":", 1)
            ratio = float(left) / float(right)
        splits[day] = splits.get(day, 1.0) * ratio

    return MarketSeries(
        symbol=symbol,
        currency=result["meta"].get("currency", ""),
        closes=closes,
        dividends=dividends,
        splits=splits,
        first_date=min(closes),
        last_date=max(closes),
        source_url=response.url,
    )


def on_or_after(days: list[dt.date], target: dt.date) -> dt.date:
    for day in days:
        if day >= target:
            return day
    raise ValueError(f"No trading date on/after {target}")


def on_or_before(days: list[dt.date], target: dt.date) -> dt.date:
    for day in reversed(days):
        if day <= target:
            return day
    raise ValueError(f"No trading date on/before {target}")


def price_on_or_before(series: MarketSeries, target: dt.date) -> float:
    eligible = [day for day in series.closes if day <= target]
    if not eligible:
        raise ValueError(f"No {series.symbol} price on/before {target}")
    return series.closes[max(eligible)]


def xnpv(rate: float, flows: list[tuple[dt.date, float]]) -> float:
    base = flows[0][0]
    return sum(value / ((1.0 + rate) ** ((day - base).days / 365.0)) for day, value in flows)


def xirr(flows: Iterable[tuple[dt.date, float]]) -> float | None:
    ordered = sorted(flows)
    if not ordered or not any(value < 0 for _, value in ordered) or not any(value > 0 for _, value in ordered):
        return None
    lo = -0.999999
    hi = 1.0
    f_lo = xnpv(lo, ordered)
    f_hi = xnpv(hi, ordered)
    while f_lo * f_hi > 0 and hi < 1_000_000:
        hi *= 2.0
        f_hi = xnpv(hi, ordered)
    if f_lo * f_hi > 0:
        return None
    for _ in range(220):
        mid = (lo + hi) / 2.0
        f_mid = xnpv(mid, ordered)
        if abs(f_mid) < 0.01:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def money_weighted_period_return(annualized_xirr: float | None, start: dt.date, end: dt.date) -> float | None:
    if annualized_xirr is None:
        return None
    return (1.0 + annualized_xirr) ** ((end - start).days / 365.0) - 1.0


def build_monthly_dates(us_days: list[dt.date], end_date: dt.date) -> set[dt.date]:
    dates: set[dt.date] = set()
    year, month = START_DATE.year, START_DATE.month
    while (year, month) <= (end_date.year, end_date.month):
        target = dt.date(year, month, 26)
        try:
            trade_day = on_or_after(us_days, target)
        except ValueError:
            break
        if trade_day.month == month and trade_day <= end_date:
            dates.add(trade_day)
        month += 1
        if month == 13:
            year += 1
            month = 1
    return dates


def build_quarter_ends(us_days: list[dt.date], end_date: dt.date) -> set[dt.date]:
    dates: set[dt.date] = set()
    for year in range(START_DATE.year, end_date.year + 1):
        for month in (3, 6, 9, 12):
            target = dt.date(year, month, calendar.monthrange(year, month)[1])
            day = on_or_before(us_days, target)
            if START_DATE <= day <= end_date:
                dates.add(day)
    return dates


def build_bonus_dates(tiger_days: list[dt.date], end_date: dt.date) -> dict[dt.date, dt.date]:
    """Map the US valuation day to the actual last KRX trade day used for the bonus purchase."""
    result: dict[dt.date, dt.date] = {}
    for year in range(START_DATE.year, end_date.year + 1):
        if year == end_date.year and end_date.month < 12:
            continue
        target = dt.date(year, 12, 31)
        kr_day = on_or_before(tiger_days, target)
        if kr_day <= end_date:
            result[kr_day] = kr_day
    return result


def simulate(
    market: dict[str, MarketSeries],
    include_bonus: bool,
) -> dict[str, Any]:
    us_symbols = ["VOO", "QQQ", "QQQM", "SCHD", "QLD"]
    end_date = min(market[symbol].last_date for symbol in ["VOO", "QQQM", "SCHD", "QLD"])
    us_days = sorted(day for day in market["VOO"].closes if START_DATE <= day <= end_date)
    tiger_days = sorted(day for day in market[TIGER_TICKER].closes if START_DATE <= day <= end_date)
    monthly_dates = build_monthly_dates(us_days, end_date)
    quarter_ends = build_quarter_ends(us_days, end_date)
    bonus_dates = build_bonus_dates(tiger_days, end_date) if include_bonus else {}

    shares = {symbol: 0.0 for symbol in us_symbols}
    tiger_shares = 0.0
    dividend_cash_usd = 0.0
    dividend_cash_krw = 0.0
    external_flows: list[tuple[dt.date, float]] = []
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    last_prices: dict[str, float] = {}
    qqq_swapped = False
    total_gross_dividends_krw = 0.0
    total_dividend_tax_krw = 0.0
    total_dividend_reinvested_krw = 0.0

    # KRX-only dates matter for TIGER dividends and bonus purchases. Process those
    # events on their actual date; their values are carried into the next US valuation.
    all_days = sorted(set(us_days) | set(tiger_days))
    us_day_set = set(us_days)
    tiger_day_set = set(tiger_days)
    previous_us_value_krw: float | None = None
    twr_index = 1.0

    for day in all_days:
        if day > end_date:
            break

        fx = price_on_or_before(market["KRW=X"], day)

        # Yahoo chart closes and dividend amounts are already restated for later
        # share splits. Applying the split events to modeled shares would therefore
        # double-count corporate actions (e.g. SCHD's 3:1 split in October 2024).
        # Gross distributions are converted to net cash after withholding.
        for symbol in us_symbols:
            amount = market[symbol].dividends.get(day)
            if amount and shares[symbol] > 0:
                gross_usd = shares[symbol] * amount
                net_usd = gross_usd * US_DIVIDEND_NET_RATE
                dividend_cash_usd += net_usd
                total_gross_dividends_krw += gross_usd * fx
                total_dividend_tax_krw += gross_usd * (1.0 - US_DIVIDEND_NET_RATE) * fx
                trades.append({
                    "date": day.isoformat(), "type": "DIVIDEND_US", "ticker": symbol,
                    "gross_local": gross_usd, "net_local": net_usd, "currency": "USD", "fx_krw_per_usd": fx,
                })

        tiger_dividend = market[TIGER_TICKER].dividends.get(day)
        if tiger_dividend and tiger_shares > 0:
            gross_krw = tiger_shares * tiger_dividend
            net_krw = gross_krw * KR_DIVIDEND_NET_RATE
            dividend_cash_krw += net_krw
            total_gross_dividends_krw += gross_krw
            total_dividend_tax_krw += gross_krw * (1.0 - KR_DIVIDEND_NET_RATE)
            trades.append({
                "date": day.isoformat(), "type": "DIVIDEND_KR", "ticker": TIGER_TICKER,
                "gross_local": gross_krw, "net_local": net_krw, "currency": "KRW", "fx_krw_per_usd": fx,
            })

        external_flow_today = 0.0

        # Annual performance-bonus scenario: one monthly salary invested on the
        # final KRX trading day of each completed calendar year.
        if include_bonus and day in bonus_dates and day in tiger_day_set:
            tiger_price = market[TIGER_TICKER].closes[day]
            bought = BONUS_KRW / tiger_price
            tiger_shares += bought
            external_flows.append((day, -BONUS_KRW))
            external_flow_today += BONUS_KRW
            trades.append({
                "date": day.isoformat(), "type": "BONUS_BUY", "ticker": TIGER_TICKER,
                "amount_krw": BONUS_KRW, "price": tiger_price, "shares": bought, "currency": "KRW",
            })

        if day not in us_day_set:
            continue

        # Update all US closing prices on valuation days.
        for symbol in us_symbols:
            if day in market[symbol].closes:
                last_prices[symbol] = market[symbol].closes[day]

        # QQQ is a pre-inception return proxy. Exchange it into QQQM at QQQM's
        # first close without recognizing taxes/fees (a modeling continuity step).
        if not qqq_swapped and day >= QQQM_INCEPTION and shares["QQQ"] > 0:
            usd_value = shares["QQQ"] * last_prices["QQQ"]
            new_shares = usd_value / last_prices["QQQM"]
            trades.append({
                "date": day.isoformat(), "type": "PROXY_SWAP", "ticker": "QQQ->QQQM",
                "amount_usd": usd_value, "price": last_prices["QQQM"], "shares": new_shares, "currency": "USD",
            })
            shares["QQQ"] = 0.0
            shares["QQQM"] += new_shares
            qqq_swapped = True

        if day in monthly_dates:
            external_flows.append((day, -MONTHLY_KRW))
            external_flow_today += MONTHLY_KRW
            usd_total = MONTHLY_KRW / fx
            target_tickers = {"VOO": 0.50, "QQQM" if day >= QQQM_INCEPTION else "QQQ": 0.30, "SCHD": 0.20}
            for ticker, weight in target_tickers.items():
                amount_usd = usd_total * weight
                bought = amount_usd / last_prices[ticker]
                shares[ticker] += bought
                trades.append({
                    "date": day.isoformat(), "type": "MONTHLY_BUY", "ticker": ticker,
                    "amount_krw": MONTHLY_KRW * weight, "amount_usd": amount_usd,
                    "price": last_prices[ticker], "shares": bought, "currency": "USD",
                    "fx_krw_per_usd": fx,
                })

        if day in quarter_ends and (dividend_cash_usd > 0 or dividend_cash_krw > 0):
            usd_to_invest = dividend_cash_usd + dividend_cash_krw / fx
            bought = usd_to_invest / last_prices["QLD"]
            invested_krw = usd_to_invest * fx
            shares["QLD"] += bought
            total_dividend_reinvested_krw += invested_krw
            trades.append({
                "date": day.isoformat(), "type": "DIVIDEND_REINVEST", "ticker": "QLD",
                "amount_krw": invested_krw, "amount_usd": usd_to_invest,
                "price": last_prices["QLD"], "shares": bought, "currency": "USD",
                "fx_krw_per_usd": fx,
            })
            dividend_cash_usd = 0.0
            dividend_cash_krw = 0.0

        us_value = sum(shares[symbol] * last_prices.get(symbol, 0.0) for symbol in us_symbols)
        tiger_price = price_on_or_before(market[TIGER_TICKER], day)
        tiger_value_krw = tiger_shares * tiger_price
        total_value_krw = (us_value + dividend_cash_usd) * fx + tiger_value_krw + dividend_cash_krw

        if previous_us_value_krw is not None and previous_us_value_krw > 0:
            # Cash is invested at the close, so today's external flow does not earn
            # today's market return (end-of-period cash-flow convention).
            daily_factor = (total_value_krw - external_flow_today) / previous_us_value_krw
            twr_index *= daily_factor
        previous_us_value_krw = total_value_krw

        daily.append({
            "date": day.isoformat(), "value_krw": total_value_krw,
            "external_flow_krw": external_flow_today, "twr_index": twr_index,
            "fx_krw_per_usd": fx, "us_value_usd": us_value,
            "tiger_value_krw": tiger_value_krw,
            "dividend_cash_usd": dividend_cash_usd, "dividend_cash_krw": dividend_cash_krw,
        })

    if not daily:
        raise RuntimeError("Simulation produced no daily valuations")

    final_day = dt.date.fromisoformat(daily[-1]["date"])
    final_value = daily[-1]["value_krw"]
    final_fx = daily[-1]["fx_krw_per_usd"]
    total_contributions = -sum(value for _, value in external_flows)
    overall_flows = external_flows + [(final_day, final_value)]
    overall_xirr = xirr(overall_flows)
    first_investment_day = min(monthly_dates)
    twr_years = (final_day - first_investment_day).days / 365.0
    twr_annualized = twr_index ** (1.0 / twr_years) - 1.0

    # Annual cash-flow and return report.
    by_year: list[dict[str, Any]] = []
    prior_end_value = 0.0
    daily_by_date = {dt.date.fromisoformat(row["date"]): row for row in daily}
    for year in range(START_DATE.year, final_day.year + 1):
        year_days = [day for day in daily_by_date if day.year == year]
        if not year_days:
            continue
        year_start_day = min(year_days)
        year_end_day = max(year_days)
        year_flows = [(day, value) for day, value in external_flows if day.year == year]
        contributions = -sum(value for _, value in year_flows)
        ending_value = daily_by_date[year_end_day]["value_krw"]
        net_gain = ending_value - prior_end_value - contributions
        irr_flows: list[tuple[dt.date, float]] = []
        period_start = dt.date(year, 1, 1)
        if prior_end_value > 0:
            irr_flows.append((period_start, -prior_end_value))
        irr_flows.extend(year_flows)
        irr_flows.append((year_end_day, ending_value))
        annualized_irr = xirr(irr_flows)
        period_irr = money_weighted_period_return(annualized_irr, period_start, year_end_day)

        year_rows = [daily_by_date[day] for day in sorted(year_days)]
        twr_start = year_rows[0]["twr_index"]
        if year == START_DATE.year:
            twr_return = year_rows[-1]["twr_index"] - 1.0
        else:
            # Divide by the last TWR index of the previous calendar year.
            previous_days = [day for day in daily_by_date if day < year_start_day]
            previous_index = daily_by_date[max(previous_days)]["twr_index"]
            twr_return = year_rows[-1]["twr_index"] / previous_index - 1.0

        by_year.append({
            "year": year, "period_end": year_end_day.isoformat(),
            "beginning_value_krw": prior_end_value, "contributions_krw": contributions,
            "ending_value_krw": ending_value, "net_gain_krw": net_gain,
            "money_weighted_period_return": period_irr,
            "money_weighted_annualized_xirr": annualized_irr,
            "time_weighted_return": twr_return,
        })
        prior_end_value = ending_value

    # Unitized maximum drawdown, which removes the effect of salary/bonus deposits.
    peak = -math.inf
    max_drawdown = 0.0
    max_drawdown_day = final_day
    peak_day = START_DATE
    drawdown_peak_day = START_DATE
    for row in daily:
        idx = row["twr_index"]
        day = dt.date.fromisoformat(row["date"])
        if idx > peak:
            peak = idx
            peak_day = day
        drawdown = idx / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_day = day
            drawdown_peak_day = peak_day

    holdings = []
    for symbol in us_symbols:
        if shares[symbol] <= 0:
            continue
        value_usd = shares[symbol] * last_prices[symbol]
        holdings.append({
            "ticker": symbol, "shares": shares[symbol], "price_local": last_prices[symbol],
            "currency": "USD", "value_krw": value_usd * final_fx,
            "weight": value_usd * final_fx / final_value,
        })
    if tiger_shares > 0:
        tiger_price = price_on_or_before(market[TIGER_TICKER], final_day)
        value_krw = tiger_shares * tiger_price
        holdings.append({
            "ticker": TIGER_TICKER, "shares": tiger_shares, "price_local": tiger_price,
            "currency": "KRW", "value_krw": value_krw, "weight": value_krw / final_value,
        })
    if dividend_cash_usd or dividend_cash_krw:
        cash_value = dividend_cash_usd * final_fx + dividend_cash_krw
        holdings.append({
            "ticker": "DIVIDEND_CASH", "shares": None, "price_local": None,
            "currency": "MIXED", "value_krw": cash_value, "weight": cash_value / final_value,
        })

    return {
        "scenario": "salary_plus_one_month_bonus" if include_bonus else "salary_only",
        "start_date": START_DATE.isoformat(), "end_date": final_day.isoformat(),
        "monthly_contribution_krw": MONTHLY_KRW,
        "annual_bonus_krw": BONUS_KRW if include_bonus else 0.0,
        "contribution_count": sum(1 for trade in trades if trade["type"] == "MONTHLY_BUY" and trade["ticker"] == "VOO"),
        "bonus_count": sum(1 for trade in trades if trade["type"] == "BONUS_BUY"),
        "total_contributions_krw": total_contributions,
        "final_value_krw": final_value,
        "profit_krw": final_value - total_contributions,
        "simple_roi": final_value / total_contributions - 1.0,
        "money_weighted_annualized_xirr": overall_xirr,
        "time_weighted_cumulative_return": twr_index - 1.0,
        "time_weighted_annualized_return": twr_annualized,
        "max_drawdown": max_drawdown,
        "max_drawdown_peak_date": drawdown_peak_day.isoformat(),
        "max_drawdown_trough_date": max_drawdown_day.isoformat(),
        "gross_dividends_krw": total_gross_dividends_krw,
        "dividend_tax_krw": total_dividend_tax_krw,
        "dividend_reinvested_krw": total_dividend_reinvested_krw,
        "unreinvested_dividend_cash_krw": dividend_cash_usd * final_fx + dividend_cash_krw,
        "final_fx_krw_per_usd": final_fx,
        "holdings": sorted(holdings, key=lambda row: row["value_krw"], reverse=True),
        "annual": by_year,
        "daily": daily,
        "trades": trades,
    }


def compact_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scenario.items() if key not in {"daily", "trades"}}


def validate_results(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    annual = scenario["annual"]
    checks = []
    checks.append({
        "name": "annual_contributions_tie",
        "actual": sum(row["contributions_krw"] for row in annual),
        "expected": scenario["total_contributions_krw"],
    })
    checks.append({
        "name": "profit_tie",
        "actual": scenario["final_value_krw"] - scenario["total_contributions_krw"],
        "expected": scenario["profit_krw"],
    })
    checks.append({
        "name": "holdings_tie",
        "actual": sum(row["value_krw"] for row in scenario["holdings"]),
        "expected": scenario["final_value_krw"],
    })
    checks.append({
        "name": "annual_ending_value_tie",
        "actual": annual[-1]["ending_value_krw"],
        "expected": scenario["final_value_krw"],
    })
    for check in checks:
        check["difference"] = check["actual"] - check["expected"]
        check["status"] = "OK" if abs(check["difference"]) < 1.0 else "FAIL"
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="outputs/019fcbe1-ea63-75f0-bfc1-5e76f6f01c02/backtest_results.json",
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    symbols = ["VOO", "QQQ", "QQQM", "SCHD", "QLD", "KRW=X", TIGER_TICKER]
    market = {symbol: download_series(symbol) for symbol in symbols}
    salary_only = simulate(market, include_bonus=False)
    with_bonus = simulate(market, include_bonus=True)
    checks = {
        salary_only["scenario"]: validate_results(salary_only),
        with_bonus["scenario"]: validate_results(with_bonus),
    }
    if any(check["status"] != "OK" for group in checks.values() for check in group):
        raise RuntimeError(f"Validation failed: {checks}")

    sources = [
        {
            "symbol": series.symbol,
            "currency": series.currency,
            "first_date": series.first_date.isoformat(),
            "last_date": series.last_date.isoformat(),
            "source_url": series.source_url,
        }
        for series in market.values()
    ]
    payload = {
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "methodology": {
            "monthly_rule": "KRW 5,000,000 at the close of the first US trading day on/after the 26th",
            "weights": CORE_WEIGHTS,
            "qqqm_proxy": "QQQ before 2020-10-13; modeled tax-free/fee-free swap to QQQM on inception",
            "dividend_rule": "Accumulate net dividends and buy QLD at each calendar quarter's last US close",
            "dividend_withholding": {"US_ETF": 0.15, "Korean_ETF": 0.154},
            "fractional_shares": True,
            "fx": "Yahoo KRW=X close on or before each valuation/trade date",
            "split_convention": "Yahoo closes/dividends are split-adjusted; split events are not applied again",
            "fees_and_slippage": 0.0,
            "capital_gains_tax": "not recognized because positions are not liquidated",
            "bonus_scenario": "KRW 5,000,000 invested in 133690.KS on each completed year's final KRX trading day",
        },
        "sources": sources,
        "checks": checks,
        "scenarios": {
            "salary_only": salary_only,
            "salary_plus_one_month_bonus": with_bonus,
        },
        "summary": {
            "salary_only": compact_scenario(salary_only),
            "salary_plus_one_month_bonus": compact_scenario(with_bonus),
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for scenario in [salary_only, with_bonus]:
        print("\n", scenario["scenario"])
        for key in [
            "start_date", "end_date", "contribution_count", "bonus_count", "total_contributions_krw",
            "final_value_krw", "profit_krw", "simple_roi", "money_weighted_annualized_xirr",
            "max_drawdown", "gross_dividends_krw", "dividend_tax_krw", "dividend_reinvested_krw",
        ]:
            print(f"{key}: {scenario[key]}")
        print("annual:")
        for row in scenario["annual"]:
            print(row)
    print(f"\nSaved {output_path.resolve()}")


if __name__ == "__main__":
    main()
