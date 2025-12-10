"""
세 가지/네 가지 시나리오를 자동 백테스트합니다.
- 기간: 2011-01-01 ~ 2025-12-08
- 초기자본: 10,000달러
- 기반 클래스: input_quant_system.SOXLQuantTrader
"""

from datetime import datetime
from typing import Dict, Optional

import io
from contextlib import redirect_stdout

from input_quant_system import SOXLQuantTrader


class ScenarioTrader(SOXLQuantTrader):
    def __init__(
        self,
        initial_capital: float,
        sf_config: Optional[Dict] = None,
        ag_config: Optional[Dict] = None,
        force_sf_if_vix_gt: Optional[float] = None,
        position_cap_pct: Optional[float] = None,
        cash_buffer_pct: float = 0.0,
    ):
        # AG 파라미터를 시나리오별로 조정하기 위해 ag_config를 직접 전달
        super().__init__(initial_capital=initial_capital, sf_config=sf_config, ag_config=ag_config)
        self.force_sf_if_vix_gt = force_sf_if_vix_gt
        self.position_cap_pct = position_cap_pct
        self.cash_buffer_pct = cash_buffer_pct

    # 변동성(VIX) 필터: VIX > threshold면 AG 금지 → 강제 SF
    def update_mode(self, qqq_data):
        new_mode = super().update_mode(qqq_data)

        if self.force_sf_if_vix_gt:
            vix_data = self.get_stock_data("^VIX", "6mo")
            if vix_data is not None and len(vix_data) > 0:
                latest_vix = float(vix_data.iloc[-1]["Close"])
                if latest_vix > self.force_sf_if_vix_gt:
                    if new_mode != "SF":
                        print(f"⚠️ VIX {latest_vix:.2f} > {self.force_sf_if_vix_gt} → 모드 SF로 강제")
                    self.current_mode = "SF"
                    return "SF"

        return new_mode

    # 포지션 캡 & 현금 버퍼를 적용한 매수 실행
    def execute_buy(self, target_price: float, actual_price: float, current_date) -> bool:
        # 총자산과 포지션 가치를 계산 (현재가로 평가)
        current_value = sum(pos["shares"] * actual_price for pos in self.positions)
        total_value = current_value + self.available_cash

        # 포지션 비중 상한
        if self.position_cap_pct is not None:
            if current_value >= total_value * self.position_cap_pct:
                print(f"⛔ 포지션 캡 {self.position_cap_pct*100:.0f}% 도달 → 매수 생략")
                return False

        # 현금 버퍼 확보 후 사용 가능 현금 계산
        usable_cash = self.available_cash - total_value * self.cash_buffer_pct
        if usable_cash <= 0:
            print(f"⛔ 현금 버퍼 {self.cash_buffer_pct*100:.0f}% 유지로 매수 불가")
            return False

        # 원본 로직과 동일하되 usable_cash를 한도로 사용
        if not self.can_buy_next_round():
            return False

        target_amount = self.calculate_position_size(self.current_round)
        # 버퍼 반영
        target_amount = min(target_amount, usable_cash)

        target_shares = int(target_amount / target_price)
        if target_shares <= 0:
            return False

        actual_amount = target_shares * actual_price
        if actual_amount > usable_cash:
            max_shares = int(usable_cash / actual_price)
            if max_shares <= 0:
                return False
            target_shares = max_shares
            actual_amount = target_shares * actual_price

        position = {
            "round": self.current_round,
            "buy_date": current_date,
            "buy_price": actual_price,
            "shares": target_shares,
            "target_price": target_price,
            "amount": actual_amount,
            "mode": self.current_mode,
        }

        self.positions.append(position)
        self.available_cash -= actual_amount
        self.current_round += 1

        print(
            f"✅ {self.current_round-1}회차 매수 실행: {target_shares}주 @ ${actual_price:.2f} "
            f"(목표가: ${target_price:.2f}, 실제투자: ${actual_amount:,.0f})"
        )
        return True


def build_configs():
    # 기본과 4w 파라미터
    sf_4w = {
        "buy_threshold": 3.7,
        "sell_threshold": 1.7,
        "max_hold_days": 35,
        "split_count": 8,
        "split_ratios": [0.046, 0.143, 0.230, 0.046, 0.115, 0.143, 0.230, 0.046],
    }
    ag_base = {
        "buy_threshold": 3.6,
        "sell_threshold": 3.5,
        "max_hold_days": 7,
        "split_count": 8,
        "split_ratios": [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020],
    }
    return sf_4w, ag_base


def run_scenario(name, trader_kwargs):
    sf_cfg, ag_cfg = trader_kwargs.pop("sf_cfg"), trader_kwargs.pop("ag_cfg")
    trader = ScenarioTrader(
        initial_capital=10_000,
        sf_config=sf_cfg,
        ag_config=ag_cfg,
        **trader_kwargs,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):  # 장황한 로그 억제
        result = trader.run_backtest("2011-01-01", "2025-12-08")

    if "error" in result:
        print(f"[{name}] 실패: {result['error']}")
        return

    mdd = trader.calculate_mdd(result["daily_records"])
    # 간단한 CAGR 계산 (거래일 기준, 365일 환산)
    days = max(1, result.get("trading_days", 0))
    years = days / 365
    cagr = ((result["final_value"] / result["initial_capital"]) ** (1 / years) - 1) * 100 if years > 0 else None
    print(f"\n=== {name} ===")
    print(f"총수익률: {result['total_return']:+.2f}%")
    if cagr is not None:
        print(f"CAGR: {cagr:+.2f}%")
    print(f"최종자산: ${result['final_value']:,.0f}")
    print(f"MDD: {mdd.get('mdd_percent', 0.0):.2f}%")
    print(f"MDD 발생일: {mdd.get('mdd_date','')}")


def main():
    sf_4w, ag_base = build_configs()

    scenarios = [
        (
            "S1: SF=4w, AG=기본, VIX>20이면 AG 금지",
            dict(sf_cfg=sf_4w, ag_cfg=ag_base, force_sf_if_vix_gt=20, position_cap_pct=None, cash_buffer_pct=0.0),
        ),
        (
            "S2: S1 + AG max_hold_days=10",
            dict(
                sf_cfg=sf_4w,
                ag_cfg={**ag_base, "max_hold_days": 10},
                force_sf_if_vix_gt=20,
                position_cap_pct=None,
                cash_buffer_pct=0.0,
            ),
        ),
        (
            "S3: S1 + 포지션 캡 80% + 현금 버퍼 10%",
            dict(sf_cfg=sf_4w, ag_cfg=ag_base, force_sf_if_vix_gt=20, position_cap_pct=0.80, cash_buffer_pct=0.10),
        ),
        (
            "S4: SF=4w, AG=기본, AG 매수/매도 +0.15%p",
            dict(
                sf_cfg=sf_4w,
                ag_cfg={
                    **ag_base,
                    "buy_threshold": ag_base["buy_threshold"] + 0.15,
                    "sell_threshold": ag_base["sell_threshold"] + 0.15,
                },
                force_sf_if_vix_gt=None,
                position_cap_pct=None,
                cash_buffer_pct=0.0,
            ),
        ),
    ]

    for name, kwargs in scenarios:
        run_scenario(name, kwargs.copy())


if __name__ == "__main__":
    main()

