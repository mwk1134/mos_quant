#!/usr/bin/env python3
"""
시뮬레이션을 실행하여 positions_snapshots.json을 정확한 값으로 보정합니다.
3/12 등 잘못된 스냅샷 데이터를 시뮬레이션 결과로 덮어씁니다.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 path에 추가
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from soxl_quant_system import SOXLQuantTrader

# 앱과 동일한 기본 설정
DEFAULT_SF_CONFIG = {
    "buy_threshold": 3.5,
    "sell_threshold": 1.1,
    "max_hold_days": 35,
    "split_count": 7,
    "split_ratios": [0.049, 0.127, 0.230, 0.257, 0.028, 0.169, 0.140]
}
DEFAULT_AG_CONFIG = {
    "buy_threshold": 3.6,
    "sell_threshold": 3.5,
    "max_hold_days": 7,
    "split_count": 8,
    "split_ratios": [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020]
}

PRESETS = {
    "KMW": {
        "initial_capital": 9000.0,
        "session_start_date": "2025-08-27",
        "seed_increases": [{"date": "2025-10-21", "amount": 31000.0}],
    },
    "JEH": {
        "initial_capital": 2793.0,
        "session_start_date": "2025-10-30",
        "seed_increases": [
            {"date": "2025-12-22", "amount": 13499.0},
            {"date": "2026-01-15", "amount": 2035.0}
        ],
    },
    "JSD": {
        "initial_capital": 17300.0,
        "session_start_date": "2025-10-30",
        "seed_increases": [],
    },
    "JEH2": {
        "initial_capital": 2704.0,
        "session_start_date": "2025-12-22",
        "seed_increases": [{"date": "2026-01-15", "amount": 678.0}],
    },
}

SNAPSHOT_PATH = CURRENT_DIR / "data" / "positions_snapshots.json"


def run_simulation_and_build_snapshot(preset_name: str) -> dict:
    """프리셋에 대해 시뮬레이션 실행 후 스냅샷 딕셔너리 반환"""
    preset = PRESETS[preset_name]
    trader = SOXLQuantTrader(
        initial_capital=preset["initial_capital"],
        sf_config=DEFAULT_SF_CONFIG,
        ag_config=DEFAULT_AG_CONFIG
    )
    for si in preset["seed_increases"]:
        trader.add_seed_increase(si["date"], si["amount"], f"시드증액 {si['date']}")

    start_date = preset["session_start_date"]
    print(f"\n[{preset_name}] Simulation: {start_date} ~ today")
    sim_result = trader.simulate_from_start_to_today(start_date, quiet=True)
    if "error" in sim_result:
        print(f"   FAIL: {sim_result['error']}")
        return {}

    snapshot = {}
    for pos in trader.positions:
        buy_date = pos.get("buy_date")
        if buy_date is None:
            continue
        if hasattr(buy_date, "strftime"):
            buy_date_str = buy_date.strftime("%Y-%m-%d")
        else:
            buy_date_str = str(buy_date)
        snap_key = f"{pos['round']}_{buy_date_str}"
        snapshot[snap_key] = {
            "shares": int(pos["shares"]),
            "buy_price": float(pos["buy_price"]),
            "amount": float(pos["amount"]),
            "round": int(pos["round"])
        }
        print(f"   {snap_key}: {pos['shares']} shares @ ${pos['buy_price']:.2f}")

    return snapshot


def main():
    print("=" * 60)
    print("[FIX] Simulation-based snapshot correction")
    print("=" * 60)

    # 기존 스냅샷 로드 (다른 프리셋 유지)
    if SNAPSHOT_PATH.exists():
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            all_data = json.load(f)
    else:
        all_data = {}

    for preset_name in PRESETS:
        snapshot = run_simulation_and_build_snapshot(preset_name)
        if snapshot:
            all_data[preset_name] = snapshot
            print(f"   OK {preset_name}: {len(snapshot)} positions")

    # 저장
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to {SNAPSHOT_PATH}")
    print("Push to GitHub for app sync.")


if __name__ == "__main__":
    main()
