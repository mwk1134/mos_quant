#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
무한매수법 4.0 백테스터
- 일반모드 + 소진 후 리버스모드 구현
- docs/무한매수법4.0_일반모드.md, docs/무한매수법4.0_소진후_리버스모드.md 기반

사용법:
  python backtester_muhan_4.py
  python backtester_muhan_4.py -t TQQQ -c 20000 -s 40 --start 2020-01-01 --end 2024-12-31
  python backtester_muhan_4.py -t SOXL --start 2020-01-01
  python backtester_muhan_4.py -i   # 대화형 모드
"""

import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_stock_data(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """Yahoo Finance API로 일별 주가 데이터 조회"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        params = {
            "period1": int(datetime.strptime(start_date, "%Y-%m-%d").timestamp()),
            "period2": int(datetime.strptime(end_date, "%Y-%m-%d").timestamp()),
            "interval": "1d",
        }
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            return None
        data = response.json()
        if "chart" not in data or "result" not in data["chart"] or not data["chart"]["result"]:
            return None
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame({
            "Date": [datetime.fromtimestamp(ts) for ts in timestamps],
            "Open": quote.get("open", [None] * len(timestamps)),
            "High": quote.get("high", [None] * len(timestamps)),
            "Low": quote.get("low", [None] * len(timestamps)),
            "Close": quote.get("close", [None] * len(timestamps)),
            "Volume": quote.get("volume", [None] * len(timestamps)),
        })
        df = df.dropna(subset=["Close"])
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        return df
    except Exception as e:
        print(f"❌ 데이터 조회 오류: {e}")
        return None


def get_star_pct(ticker: str, split: int, t: float) -> float:
    """별% 계산 (일반모드)"""
    ticker = ticker.upper()
    if ticker == "TQQQ":
        return (15 - 1.5 * t) if split == 20 else (15 - 0.75 * t)
    if ticker == "SOXL":
        return (20 - 2 * t) if split == 20 else (20 - t)
    # 기타 3x Bull ETF: SOXL 공식 적용
    return (20 - 2 * t) if split == 20 else (20 - t)


def get_profit_target_pct(ticker: str) -> float:
    """지정가 매도 목표 수익률 (TQQQ 15%, SOXL 20%)"""
    return 15.0 if ticker.upper() == "TQQQ" else 20.0


class Muhan4Backtester:
    """무한매수법 4.0 백테스터"""

    def __init__(
        self,
        ticker: str = "TQQQ",
        initial_capital: float = 20000,
        split: int = 40,
        first_buy_premium_pct: float = 12.5,  # 처음 매수 시 전날 대비 % (10~15)
    ):
        self.ticker = ticker.upper()
        self.initial_capital = initial_capital
        self.split = split
        self.first_buy_premium_pct = first_buy_premium_pct
        self.profit_target_pct = get_profit_target_pct(self.ticker)

        # 상태
        self.cash = initial_capital
        self.shares = 0
        self.avg_price = 0.0
        self.t = 0.0
        self.mode = "normal"  # normal | reverse
        self._reverse_first_day = False  # 리버스모드 첫날 MOC 매도용
        self.daily_records: List[Dict] = []
        self.trades: List[Dict] = []

    def _buy_point(self, star_point: float) -> float:
        """매수점 = 별지점 - 0.01"""
        return round(star_point - 0.01, 2)

    def _sell_point(self, star_point: float) -> float:
        """매도점 = 별지점"""
        return round(star_point, 2)

    def _star_point_normal(self, t: float) -> float:
        """일반모드 별지점 = 평단 × (1 + 별%)"""
        if self.avg_price <= 0:
            return 0.0
        star_pct = get_star_pct(self.ticker, self.split, t)
        return round(self.avg_price * (1 + star_pct / 100), 2)

    def _star_point_reverse(self, df: pd.DataFrame, idx: int) -> float:
        """리버스모드 별지점 = 직전 5거래일 종가 평균"""
        if idx < 5:
            return df.iloc[idx]["Close"]
        prev5 = df.iloc[idx - 5 : idx]["Close"]
        return round(float(prev5.mean()), 2)

    def _buy_amount(self) -> float:
        """1회 매수액 = 잔금 / (분할수 - T)"""
        denom = self.split - self.t
        if denom <= 0:
            return 0.0
        return self.cash / denom

    def _is_first_half(self) -> bool:
        """전반전 여부 (T < 분할수/2)"""
        return self.t < self.split / 2

    def _is_exhausted(self) -> bool:
        """소진 여부 (T > 분할수-1)"""
        return self.t > self.split - 1

    def _run_normal_mode(self, row: pd.Series, df: pd.DataFrame, idx: int) -> None:
        """일반모드 일일 처리"""
        date = row["Date"]
        close = row["Close"]
        high = row["High"]
        prev_close = df.iloc[idx - 1]["Close"] if idx > 0 else close

        # 처음 매수 (보유 0, T=0) - 전날 종가 필요
        if self.shares <= 0:
            if idx > 0 and self.cash > 0:
                loc_price = prev_close * (1 + self.first_buy_premium_pct / 100)
                if close <= loc_price:
                    amount = min(self.cash / self.split, self.cash)  # 1회 매수액
                    if amount > 0:
                        qty = int(amount / close)
                        if qty > 0:
                            cost = qty * close
                            self.cash -= cost
                            self.shares += qty
                            self.avg_price = close
                            self.t = 1.0
                            self.trades.append({
                                "date": str(date), "action": "BUY", "qty": qty,
                                "price": close, "amount": cost, "t_after": self.t,
                            })
            return

        star_point = self._star_point_normal(self.t)
        buy_point = self._buy_point(star_point)
        sell_point = self._sell_point(star_point)
        buy_amount = self._buy_amount()
        limit_sell_price = round(self.avg_price * (1 + self.profit_target_pct / 100), 2)

        # 쿼터 매도 (보유량 1/4) - 별지점 LOC
        quarter_qty = self.shares // 4
        if quarter_qty > 0 and close >= sell_point:
            proceeds = quarter_qty * close
            self.cash += proceeds
            self.shares -= quarter_qty
            self.t = self.t * 0.75
            self.trades.append({
                "date": str(date), "action": "QUARTER_SELL", "qty": quarter_qty,
                "price": close, "amount": proceeds, "t_after": self.t,
            })
            # 매도 후 평단/별지점 재계산
            if self.shares > 0:
                star_point = self._star_point_normal(self.t)
                buy_point = self._buy_point(star_point)
                sell_point = self._sell_point(star_point)
                buy_amount = self._buy_amount()

        # 지정가 매도 (나머지 3/4) - 15%/20% 이익
        if self.shares > 0 and high >= limit_sell_price:
            sell_qty = self.shares
            proceeds = sell_qty * limit_sell_price
            self.cash += proceeds
            self.shares = 0
            self.t = 0.0
            self.avg_price = 0.0
            self.trades.append({
                "date": str(date), "action": "LIMIT_SELL", "qty": sell_qty,
                "price": limit_sell_price, "amount": proceeds, "t_after": 0,
            })
            return

        # 매수 (LOC)
        if self.shares > 0 and self.cash > 0 and close <= buy_point:
            orig_avg = self.avg_price
            if self._is_first_half():
                # 전반전: 절반 별지점 LOC, 절반 평단 LOC (각각 절반매수 +0.5)
                half_amt = buy_amount / 2
                if half_amt > 0 and self.cash > 0:
                    amt = min(half_amt, self.cash)
                    qty = int(amt / close)
                    if qty > 0:
                        cost = qty * close
                        self.cash -= cost
                        new_avg = (self.avg_price * self.shares + cost) / (self.shares + qty)
                        self.shares += qty
                        self.avg_price = new_avg
                        self.t += 0.5
                        self.trades.append({
                            "date": str(date), "action": "HALF_BUY", "qty": qty,
                            "price": close, "amount": cost, "t_after": self.t,
                        })
                # 평단 LOC (close <= 평단, 평단 < 별지점인 전반전)
                if orig_avg > 0 and close <= orig_avg and self.cash > 0 and half_amt > 0:
                    amt = min(half_amt, self.cash)
                    qty = int(amt / close)
                    if qty > 0:
                        cost = qty * close
                        self.cash -= cost
                        new_avg = (self.avg_price * self.shares + cost) / (self.shares + qty)
                        self.shares += qty
                        self.avg_price = new_avg
                        self.t += 0.5
                        self.trades.append({
                            "date": str(date), "action": "HALF_BUY", "qty": qty,
                            "price": close, "amount": cost, "t_after": self.t,
                        })
            else:
                # 후반전: 전체 별지점 LOC
                amt = min(buy_amount, self.cash)
                qty = int(amt / close)
                if qty > 0:
                    cost = qty * close
                    self.cash -= cost
                    new_avg = (self.avg_price * self.shares + cost) / (self.shares + qty)
                    self.shares += qty
                    self.avg_price = new_avg
                    self.t += 1.0
                    self.trades.append({
                        "date": str(date), "action": "BUY", "qty": qty,
                        "price": close, "amount": cost, "t_after": self.t,
                    })

        # 소진 체크 → 리버스모드
        if self._is_exhausted() and self.shares > 0:
            self.mode = "reverse"
            self._reverse_first_day = True

    def _run_reverse_mode(self, row: pd.Series, df: pd.DataFrame, idx: int) -> None:
        """리버스모드 일일 처리"""
        date = row["Date"]
        close = row["Close"]
        star_point = self._star_point_reverse(df, idx)

        # 첫날: MOC 매도만 (10등분/20등분)
        if self._reverse_first_day:
            div = 10 if self.split == 20 else 20
            sell_qty = self.shares // div
            if sell_qty > 0:
                proceeds = sell_qty * close
                self.cash += proceeds
                self.shares -= sell_qty
                self.t = self.t * (0.9 if self.split == 20 else 0.95)
                self.trades.append({
                    "date": str(date), "action": "REVERSE_MOC_SELL", "qty": sell_qty,
                    "price": close, "amount": proceeds, "t_after": self.t,
                })
            self._reverse_first_day = False
            return

        # 둘째날부터: 별지점 위 매도, 별지점 아래 쿼터매수
        div = 10 if self.split == 20 else 20
        sell_qty_target = self.shares // div

        # 매도 (별지점 위)
        if sell_qty_target > 0 and close >= star_point:
            sell_qty = min(sell_qty_target, self.shares)
            if sell_qty > 0:
                proceeds = sell_qty * close
                self.cash += proceeds
                self.shares -= sell_qty
                self.t = self.t * (0.9 if self.split == 20 else 0.95)
                self.trades.append({
                    "date": str(date), "action": "REVERSE_LOC_SELL", "qty": sell_qty,
                    "price": close, "amount": proceeds, "t_after": self.t,
                })

        # 쿼터매수 (별지점 아래, 잔금/4)
        if close < star_point and self.cash > 0:
            buy_amt = self.cash / 4
            if buy_amt > 0:
                qty = int(buy_amt / close)
                if qty > 0:
                    cost = qty * close
                    self.cash -= cost
                    if self.shares > 0:
                        new_avg = (self.avg_price * self.shares + cost) / (self.shares + qty)
                    else:
                        new_avg = close
                    self.shares += qty
                    self.avg_price = new_avg
                    self.t = self.t + (self.split - self.t) * 0.25
                    self.trades.append({
                        "date": str(date), "action": "QUARTER_BUY", "qty": qty,
                        "price": close, "amount": cost, "t_after": self.t,
                    })

        # 리버스모드 종료: 종가 > 평단 → 일반모드
        if self.shares > 0 and close > self.avg_price:
            self.mode = "normal"

    def run(self, start_date: str, end_date: str) -> Dict:
        """백테스트 실행"""
        df = get_stock_data(self.ticker, start_date, end_date)
        if df is None or len(df) == 0:
            return {"error": f"{self.ticker} 데이터를 가져올 수 없습니다."}

        df = df.sort_values("Date").reset_index(drop=True)
        self.cash = self.initial_capital
        self.shares = 0
        self.avg_price = 0.0
        self.t = 0.0
        self.mode = "normal"
        self.daily_records = []
        self.trades = []

        for idx in range(len(df)):
            row = df.iloc[idx]
            date = row["Date"]

            if self.mode == "normal":
                self._run_normal_mode(row, df, idx)
            else:
                self._run_reverse_mode(row, df, idx)

            # 일별 기록
            total_assets = self.cash + self.shares * row["Close"] if self.shares > 0 else self.cash
            self.daily_records.append({
                "date": str(date),
                "close": row["Close"],
                "shares": self.shares,
                "cash": self.cash,
                "total_assets": total_assets,
                "t": self.t,
                "mode": self.mode,
            })

        final_value = self.cash + self.shares * df.iloc[-1]["Close"] if self.shares > 0 else self.cash
        total_return = (final_value / self.initial_capital - 1) * 100 if self.initial_capital > 0 else 0

        return {
            "ticker": self.ticker,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": self.initial_capital,
            "final_value": round(final_value, 2),
            "total_return": round(total_return, 2),
            "trading_days": len(df),
            "daily_records": self.daily_records,
            "trades": self.trades,
            "final_positions": self.shares,
        }


def calculate_mdd(daily_records: List[Dict]) -> Dict:
    """최대 낙폭 계산"""
    if not daily_records:
        return {"mdd_percent": 0.0, "mdd_date": ""}
    peak = daily_records[0]["total_assets"]
    max_dd = 0.0
    mdd_date = ""
    for r in daily_records:
        if r["total_assets"] > peak:
            peak = r["total_assets"]
        dd = (peak - r["total_assets"]) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            mdd_date = r["date"]
    return {"mdd_percent": max_dd, "mdd_date": mdd_date}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="무한매수법 4.0 백테스터")
    parser.add_argument("--ticker", "-t", default="TQQQ", help="티커 (TQQQ, SOXL 등)")
    parser.add_argument("--capital", "-c", type=float, default=20000, help="원금 ($)")
    parser.add_argument("--split", "-s", type=int, default=40, choices=[20, 40], help="분할 (20 또는 40)")
    parser.add_argument("--start", default="", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="종료일 YYYY-MM-DD")
    parser.add_argument("--interactive", "-i", action="store_true", help="대화형 모드")
    args = parser.parse_args()

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")

    if args.interactive:
        print("=" * 60)
        print("  무한매수법 4.0 백테스터")
        print("=" * 60)
        ticker = input("\n티커 (TQQQ/SOXL 등, 기본 TQQQ): ").strip().upper() or "TQQQ"
        capital = input("원금 ($, 기본 20000): ").strip()
        capital = float(capital) if capital else 20000
        split = input("분할 (20/40, 기본 40): ").strip()
        split = int(split) if split in ("20", "40") else 40
        start_input = input(f"시작일 (기본 {start_date}): ").strip() or start_date
        end_input = input(f"종료일 (기본 {end_date}): ").strip() or end_date
    else:
        ticker = args.ticker.upper()
        capital = args.capital
        split = args.split
        start_input = args.start or start_date
        end_input = args.end or end_date

    print("=" * 60)
    print("  무한매수법 4.0 백테스터")
    print("=" * 60)
    print(f"티커: {ticker}, 원금: ${capital:,.0f}, 분할: {split}")
    print(f"기간: {start_input} ~ {end_input}")

    bt = Muhan4Backtester(ticker=ticker, initial_capital=capital, split=split)
    result = bt.run(start_input, end_input)

    if "error" in result:
        print(f"\n❌ {result['error']}")
        return

    mdd = calculate_mdd(result["daily_records"])
    years = result["trading_days"] / 252 if result["trading_days"] > 0 else 0
    cagr = ((result["final_value"] / result["initial_capital"]) ** (1 / years) - 1) * 100 if years > 0 else 0

    print("\n" + "=" * 60)
    print(f"  결과: {ticker} ({split}분할)")
    print("=" * 60)
    print(f"기간: {result['start_date']} ~ {result['end_date']}")
    print(f"거래일: {result['trading_days']}일")
    print(f"초기자본: ${result['initial_capital']:,.0f}")
    print(f"최종자산: ${result['final_value']:,.0f}")
    print(f"총수익률: {result['total_return']:+.2f}%")
    print(f"연평균(CAGR): {cagr:+.2f}%")
    print(f"최대낙폭(MDD): {mdd['mdd_percent']:.2f}% ({mdd['mdd_date']})")
    print(f"총 거래 횟수: {len(result['trades'])}회")
    print("=" * 60)


if __name__ == "__main__":
    main()
