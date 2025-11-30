"""
SOXL (2017-01-01 ~ 오늘) 구간에서
- 목표: MDD를 35% 이하로 낮추면서 수익률 하락을 최소화
- 방법: 매수/매도 임계값 및 분할매수 비중 조합 탐색

사용:
  python optimize_soxl_2017_mdd35.py
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from soxl_quant_system import SOXLQuantTrader
from backtester import _calculate_mdd  # 동일 로직 재사용


def run_one(
    start_date: str,
    end_date: Optional[str],
    initial_capital: float,
    sf_config: Dict,
    ag_config: Dict,
) -> Dict:
    trader = SOXLQuantTrader(
        initial_capital=initial_capital,
        sf_config=sf_config,
        ag_config=ag_config,
    )
    # 출력이 많을 수 있어 조용히 실행
    result = trader.run_backtest(start_date, end_date)
    if "error" in result:
        return {"error": result["error"]}

    mdd_info = _calculate_mdd(result.get("daily_records", []))
    return {
        "total_return": result.get("total_return", 0.0),
        "final_value": result.get("final_value", 0.0),
        "mdd_percent": mdd_info.get("mdd_percent", 0.0),
        "mdd_date": mdd_info.get("mdd_date", ""),
        "trading_days": result.get("trading_days", 0),
        "sf_config": sf_config,
        "ag_config": ag_config,
    }


def candidate_split_sets_sf() -> List[List[float]]:
    # 안전(SF) 모드 분할매수 후보 세트
    return [
        [0.049, 0.127, 0.230, 0.257, 0.028, 0.169, 0.140],  # 기본
        [0.04, 0.10, 0.18, 0.22, 0.12, 0.22, 0.12],         # 균형형
        [0.03, 0.08, 0.15, 0.20, 0.15, 0.25, 0.14],         # 초기 낮춤
        [0.02, 0.06, 0.12, 0.20, 0.20, 0.30, 0.10],         # 초기 더 낮춤
    ]


def candidate_split_sets_ag() -> List[List[float]]:
    # 공세(AG) 모드 분할매수 후보 세트
    return [
        [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020],  # 기본
        [0.05, 0.10, 0.12, 0.15, 0.15, 0.18, 0.20, 0.05],          # 균형형
        [0.04, 0.08, 0.12, 0.15, 0.15, 0.20, 0.20, 0.06],          # 초기 낮춤
        [0.02, 0.05, 0.10, 0.15, 0.18, 0.30, 0.18, 0.02],          # 초기 더 낮춤
    ]


def generate_candidates() -> List[Tuple[Dict, Dict]]:
    # 확장 탐색: 수익률 유지(공격적)와 MDD 개선(보수적) 영역을 모두 포함
    sf_buy_range = [3.0, 3.2, 3.3, 3.5, 3.8, 4.0]
    sf_sell_range = [1.8, 2.0, 2.2, 2.5, 2.8]
    sf_hold = [25, 28, 30, 32, 35]

    ag_buy_range = [3.4, 3.6, 3.8, 4.0]
    ag_sell_range = [3.0, 3.2, 3.5, 3.8, 4.0]
    ag_hold = [5, 6, 7, 9]

    sf_splits = candidate_split_sets_sf()
    ag_splits = candidate_split_sets_ag()

    candidates: List[Tuple[Dict, Dict]] = []
    for sb in sf_buy_range:
        for ss in sf_sell_range:
            for sh in sf_hold:
                for sf_ratios in sf_splits:
                    sf_config = {
                        "buy_threshold": sb,
                        "sell_threshold": ss,
                        "max_hold_days": sh,
                        "split_count": 7,
                        "split_ratios": sf_ratios,
                    }
                    for ab in ag_buy_range:
                        for as_ in ag_sell_range:
                            for ah in ag_hold:
                                for ag_ratios in ag_splits:
                                    ag_config = {
                                        "buy_threshold": ab,
                                        "sell_threshold": as_,
                                        "max_hold_days": ah,
                                        "split_count": 8,
                                        "split_ratios": ag_ratios,
                                    }
                                    candidates.append((sf_config, ag_config))
    return candidates


def optimize(
    start_date: str = "2017-01-01",
    end_date: Optional[str] = None,
    initial_capital: float = 40_000,
    target_mdd: float = 35.0,
    max_tests: int = 300,  # 확장 탐색
    required_return: float = 13000.0,  # 최소 수익률 기준(%)
) -> Dict:
    """
    후보 조합을 순차 탐색하며 MDD <= target_mdd를 만족하는 최고 수익률 조합을 찾는다.
    못 찾으면 MDD 최소 조합을 반환.
    """
    print("=" * 80)
    print(f"SOXL 최적화 (기간: {start_date} ~ {end_date or '오늘'}) - 목표 MDD <= {target_mdd:.2f}%")
    print("=" * 80)

    candidates = generate_candidates()
    tested = 0
    results: List[Dict] = []
    best_mdd_hit: Optional[Dict] = None
    best_target_zone: Optional[Dict] = None  # (MDD<=target) and (return>=required_return)
    best_overall: Optional[Dict] = None

    for sf_cfg, ag_cfg in candidates:
        tested += 1
        if tested > max_tests:
            break
        r = run_one(start_date, end_date, initial_capital, sf_cfg, ag_cfg)
        if "error" in r:
            print(f"[{tested}] ERROR: {r['error']}")
            continue
        results.append(r)
        mdd = r["mdd_percent"]
        ret = r["total_return"]
        print(f"[{tested}] 수익률 {ret:+.2f}% | MDD {mdd:.2f}% | SF({sf_cfg['buy_threshold']}/{sf_cfg['sell_threshold']}/{sf_cfg['max_hold_days']}) AG({ag_cfg['buy_threshold']}/{ag_cfg['sell_threshold']}/{ag_cfg['max_hold_days']})")

        if (best_overall is None) or (ret > best_overall["total_return"]):
            best_overall = r
        if mdd <= target_mdd:
            if (best_mdd_hit is None) or (ret > best_mdd_hit["total_return"]):
                best_mdd_hit = r
            if ret >= required_return:
                if (best_target_zone is None) or (mdd < best_target_zone["mdd_percent"]) or (
                    abs(mdd - best_target_zone["mdd_percent"]) < 1e-9 and ret > best_target_zone["total_return"]
                ):
                    best_target_zone = r

    results_sorted_ret = sorted(results, key=lambda x: x["total_return"], reverse=True)
    results_sorted_mdd = sorted(results, key=lambda x: x["mdd_percent"])

    summary = {
        "tested": tested,
        "target_mdd": target_mdd,
        "required_return": required_return,
        "best_mdd_hit": best_mdd_hit,
        "best_target_zone": best_target_zone,
        "best_overall": best_overall,
        "top5_by_return": results_sorted_ret[:5],
        "top5_by_lowest_mdd": results_sorted_mdd[:5],
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"opt_results_SOXL_2017_targetMDD{int(target_mdd)}_{ts}.json"
    with open(out_name, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n결과가 {out_name} 파일에 저장되었습니다.")

    if best_target_zone:
        print("\n✅ [목표 MDD 달성 + 최소 수익률 충족] 요약")
        print(f"  수익률: {best_target_zone['total_return']:+.2f}% (요구 {required_return:+.2f}% 이상)")
        print(f"  MDD   : {best_target_zone['mdd_percent']:.2f}% (목표 {target_mdd:.2f}%)")
        print("  SF:", best_target_zone["sf_config"])
        print("  AG:", best_target_zone["ag_config"])
    elif best_mdd_hit:
        print("\n✅ [목표 MDD 달성 + 최고 수익률] 요약")
        print(f"  수익률: {best_mdd_hit['total_return']:+.2f}%")
        print(f"  MDD   : {best_mdd_hit['mdd_percent']:.2f}% (목표 {target_mdd:.2f}%)")
        print("  SF:", best_mdd_hit["sf_config"])
        print("  AG:", best_mdd_hit["ag_config"])
    else:
        low = results_sorted_mdd[0] if results_sorted_mdd else None
        if low:
            print("\n⚠️ 목표 MDD 미달성 - 최저 MDD 조합 요약")
            print(f"  수익률: {low['total_return']:+.2f}%")
            print(f"  MDD   : {low['mdd_percent']:.2f}%")
            print("  SF:", low["sf_config"])
            print("  AG:", low["ag_config"])
        else:
            print("\n결과가 비어있습니다. (실패)")

    return summary


if __name__ == "__main__":
    optimize(
        start_date="2017-01-01",
        end_date=None,  # 오늘까지
        initial_capital=40_000,
        target_mdd=35.0,
        max_tests=120,  # 필요 시 늘려가며 탐색
    )


