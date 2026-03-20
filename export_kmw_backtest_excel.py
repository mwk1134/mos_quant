#!/usr/bin/env python3
"""
app.py KMW 프리셋과 동일한 설정으로 백테스트 후 엑셀 저장.
실행: python export_kmw_backtest_excel.py [출력경로.xlsx]
  생략 시 data/KMW_SOXL_backtest_YYYYMMDD_HHMMSS.xlsx
"""
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from soxl_quant_system import SOXLQuantTrader

# app.py kmw_preset / sf_config / ag_config 와 동일
DEFAULT_SF_CONFIG = {
    "buy_threshold": 3.5,
    "sell_threshold": 1.1,
    "max_hold_days": 35,
    "split_count": 7,
    "split_ratios": [0.049, 0.127, 0.230, 0.257, 0.028, 0.169, 0.140],
}
DEFAULT_AG_CONFIG = {
    "buy_threshold": 3.6,
    "sell_threshold": 3.5,
    "max_hold_days": 7,
    "split_count": 8,
    "split_ratios": [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020],
}

KMW = {
    "initial_capital": 9000.0,
    "session_start_date": "2025-08-27",
    "seed_increases": [{"date": "2025-10-21", "amount": 31000.0}],
}


def main():
    out_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if out_arg:
        out_path = Path(out_arg)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = CURRENT_DIR / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"KMW_SOXL_backtest_{ts}.xlsx"

    trader = SOXLQuantTrader(
        initial_capital=KMW["initial_capital"],
        sf_config=DEFAULT_SF_CONFIG,
        ag_config=DEFAULT_AG_CONFIG,
    )
    for si in KMW["seed_increases"]:
        trader.add_seed_increase(si["date"], si["amount"], f"시드증액 {si['date']}")

    end_dt = trader.get_latest_trading_day()
    end_str = end_dt.strftime("%Y-%m-%d")
    start_str = KMW["session_start_date"]

    print(f"KMW 백테스트: {start_str} ~ {end_str}")
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = trader.run_backtest(start_str, end_str)

    if "error" in result:
        print("ERROR:", result["error"])
        sys.exit(1)

    trader.export_backtest_to_excel(result, str(out_path))
    if not out_path.exists():
        print("ERROR: 엑셀 저장 실패")
        sys.exit(1)

    print(f"저장 완료: {out_path.resolve()}")
    print(f"  최종자산: ${result['final_value']:,.0f}  수익률: {result['total_return']:+.2f}%")


if __name__ == "__main__":
    main()
