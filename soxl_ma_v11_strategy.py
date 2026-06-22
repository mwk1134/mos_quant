from datetime import datetime
from typing import Optional

import pandas as pd


V11_STRATEGY_NAME = "정배열 V1.1"
BASE_STRATEGY_NAME = "동파 변형-공격형"


class MovingAverageV11StrategyMixin:
    """Apply MA V1.1 only when prior-day SOXL SMA(5) > SMA(20) > SMA(60)."""

    def _is_soxl_bull_alignment(
        self,
        soxl_history: Optional[pd.DataFrame],
        current_date: Optional[datetime] = None,
    ) -> bool:
        if soxl_history is None or len(soxl_history) < 60 or "Close" not in soxl_history.columns:
            return False

        if current_date is not None:
            soxl_history = soxl_history[soxl_history.index < pd.Timestamp(current_date)]
            if len(soxl_history) < 60:
                return False

        close = soxl_history["Close"].dropna()
        if len(close) < 60:
            return False

        ma5 = close.rolling(window=5).mean().iloc[-1]
        ma20 = close.rolling(window=20).mean().iloc[-1]
        ma60 = close.rolling(window=60).mean().iloc[-1]

        return bool(
            pd.notna(ma5)
            and pd.notna(ma20)
            and pd.notna(ma60)
            and ma5 > ma20 > ma60
        )

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
