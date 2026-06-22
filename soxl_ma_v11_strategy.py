from datetime import datetime
from typing import Optional

import pandas as pd


V11_STRATEGY_NAME = "정배열 V1.1"
BASE_STRATEGY_NAME = "동파 변형-공격형"


class MovingAverageV11StrategyMixin:
    """Apply MA V1.1 only when prior-day SOXL SMA(5) > SMA(20) > SMA(60)."""

    def get_bull_alignment_status(
        self,
        current_date: Optional[datetime] = None,
        soxl_history: Optional[pd.DataFrame] = None,
    ) -> dict:
        """Return the prior-bar SOXL SMA alignment status used by MA V1.1."""
        if soxl_history is None:
            try:
                soxl_history = self.get_stock_data("SOXL", "6mo")
            except Exception as exc:
                return {
                    "available": False,
                    "is_aligned": False,
                    "reason": str(exc),
                }

        if soxl_history is None or len(soxl_history) < 60 or "Close" not in soxl_history.columns:
            return {
                "available": False,
                "is_aligned": False,
                "reason": "SOXL 60일선 계산에 필요한 데이터가 부족합니다.",
            }

        if current_date is not None:
            soxl_history = soxl_history[soxl_history.index < pd.Timestamp(current_date)]

        close = soxl_history["Close"].dropna()
        if len(close) < 60:
            return {
                "available": False,
                "is_aligned": False,
                "reason": "판정 기준일 이전 SOXL 60일선 데이터가 부족합니다.",
            }

        ma5 = close.rolling(window=5).mean().iloc[-1]
        ma20 = close.rolling(window=20).mean().iloc[-1]
        ma60 = close.rolling(window=60).mean().iloc[-1]
        is_aligned = bool(
            pd.notna(ma5)
            and pd.notna(ma20)
            and pd.notna(ma60)
            and ma5 > ma20 > ma60
        )
        basis_date = close.index[-1]
        if hasattr(basis_date, "strftime"):
            basis_date = basis_date.strftime("%Y-%m-%d")
        else:
            basis_date = str(basis_date)

        return {
            "available": True,
            "is_aligned": is_aligned,
            "basis_date": basis_date,
            "ma5": float(ma5),
            "ma20": float(ma20),
            "ma60": float(ma60),
        }

    def _is_soxl_bull_alignment(
        self,
        soxl_history: Optional[pd.DataFrame],
        current_date: Optional[datetime] = None,
    ) -> bool:
        status = self.get_bull_alignment_status(current_date, soxl_history)
        return bool(status.get("available") and status.get("is_aligned"))

    def get_mode_config(
        self,
        mode: str,
        current_date: Optional[datetime] = None,
        soxl_history: Optional[pd.DataFrame] = None,
    ) -> dict:
        config = super().get_mode_config(mode, current_date, soxl_history).copy()

        if self._is_soxl_bull_alignment(soxl_history, current_date):
            if mode == "SF":
                config["buy_threshold"] = 6.25
                config["sell_threshold"] = 1.5
                config["max_hold_days"] = 35
            else:
                config["buy_threshold"] = 15.25
                config["sell_threshold"] = 6.8
                config["max_hold_days"] = 7

            split_count = int(
                config.get("split_count")
                or len(config.get("split_ratios") or [])
                or 1
            )
            split_count = max(1, split_count)
            config["split_count"] = split_count
            config["split_ratios"] = [1.0 / split_count] * split_count
            config["strategy_name"] = V11_STRATEGY_NAME
            return config

        config["strategy_name"] = BASE_STRATEGY_NAME
        return config

    def get_current_config(self) -> dict:
        """Return the current mode config with the MA V1.1 overlay when applicable."""
        mode = self.current_mode or "SF"
        soxl_history = None
        try:
            soxl_history = self.get_stock_data("SOXL", "6mo")
        except Exception:
            soxl_history = None
        return self.get_mode_config(mode, self.get_today_date(), soxl_history)
