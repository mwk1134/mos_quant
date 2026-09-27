import unittest
from datetime import datetime

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class MAAlignmentSwitchTests(unittest.TestCase):
    CURRENT_DATE = datetime(2026, 4, 1)

    def setUp(self):
        self.trader = SOXLQuantTrader(
            initial_capital=100_000,
            auto_update_rsi=False,
        )
        self.trader.current_mode = "SF"

    @classmethod
    def _history(cls, prior_closes, current_close=10_000.0):
        prior_dates = pd.bdate_range(
            end=pd.Timestamp(cls.CURRENT_DATE) - pd.offsets.BDay(1),
            periods=len(prior_closes),
        )
        history = pd.DataFrame(
            {"Close": [float(value) for value in prior_closes]},
            index=prior_dates,
        )
        # This row must never participate in the signal used for CURRENT_DATE.
        history.loc[pd.Timestamp(cls.CURRENT_DATE), "Close"] = float(current_close)
        return history

    def test_alignment_uses_only_prior_days_and_requires_strict_order(self):
        equal_prior_history = self._history([100.0] * 60, current_close=1_000_000.0)

        status = self.trader.get_ma_alignment_status(
            self.CURRENT_DATE,
            equal_prior_history,
        )

        self.assertFalse(status["condition_met"])
        self.assertEqual(status["basis_date"], "2026-03-31")
        self.assertEqual(status["sma5"], 100.0)
        self.assertEqual(status["sma20"], 100.0)
        self.assertEqual(status["sma60"], 100.0)

        aligned_history = self._history(range(1, 61), current_close=0.01)
        aligned = self.trader.get_ma_alignment_status(
            self.CURRENT_DATE,
            aligned_history,
        )

        self.assertTrue(aligned["condition_met"])
        self.assertAlmostEqual(aligned["sma5"], 58.0)
        self.assertAlmostEqual(aligned["sma20"], 50.5)
        self.assertAlmostEqual(aligned["sma60"], 30.5)

    def test_opt_in_is_required_before_aligned_config_becomes_active(self):
        history = self._history(range(1, 61))

        disabled_status = self.trader.get_ma_alignment_status(
            self.CURRENT_DATE,
            history,
        )
        disabled_config = self.trader.get_mode_config(
            "SF",
            self.CURRENT_DATE,
            history,
        )

        self.assertTrue(disabled_status["condition_met"])
        self.assertFalse(disabled_status["enabled"])
        self.assertFalse(disabled_status["active"])
        self.assertEqual(disabled_config, self.trader.sf_config)

        self.trader.set_ma_alignment_switch(True)
        enabled_status = self.trader.get_ma_alignment_status(
            self.CURRENT_DATE,
            history,
        )
        enabled_config = self.trader.get_mode_config(
            "SF",
            self.CURRENT_DATE,
            history,
        )

        self.assertTrue(enabled_status["condition_met"])
        self.assertTrue(enabled_status["enabled"])
        self.assertTrue(enabled_status["active"])
        self.assertEqual(enabled_status["strategy_name"], "dongpa_ma_v1_1_r")
        self.assertEqual(enabled_config["strategy_name"], "dongpa_ma_v1_1_r")
        self.assertEqual(enabled_config["buy_threshold"], 6.5)
        self.assertEqual(enabled_config["sell_threshold"], 1.8)
        self.assertEqual(enabled_config["max_hold_days"], 30)
        self.assertEqual(enabled_config["split_count"], 10)

    def test_existing_position_count_continues_round_and_sell_rules_are_frozen(self):
        base_config = self.trader.sf_config.copy()
        for day in (datetime(2026, 3, 27), datetime(2026, 3, 30)):
            self.trader._active_buy_config = base_config.copy()
            try:
                self.assertTrue(
                    self.trader.execute_buy(
                        target_price=100.0,
                        actual_price=100.0,
                        current_date=day,
                        mode="SF",
                    )
                )
            finally:
                self.trader._active_buy_config = None

        self.assertEqual(len(self.trader.positions), 2)
        self.assertEqual(self.trader.current_round, 3)

        self.trader.set_ma_alignment_switch(True)
        aligned_config = self.trader.get_mode_config(
            "SF",
            self.CURRENT_DATE,
            self._history(range(1, 61)),
        )
        self.trader._active_buy_config = aligned_config.copy()
        try:
            self.assertTrue(
                self.trader.execute_buy(
                    target_price=100.0,
                    actual_price=100.0,
                    current_date=self.CURRENT_DATE,
                    mode="SF",
                )
            )
        finally:
            self.trader._active_buy_config = None

        first, second, switched = self.trader.positions
        self.assertEqual(switched["round"], 3)
        self.assertEqual(switched["shares"], 20)  # 100,000 * 2.00% / $100
        self.assertEqual(switched["strategy_name"], "dongpa_ma_v1_1_r")
        self.assertEqual(switched["sell_threshold"], 1.8)
        self.assertEqual(switched["max_hold_days"], 30)

        for existing in (first, second):
            self.assertEqual(existing["sell_threshold"], 1.1)
            self.assertEqual(existing["max_hold_days"], 35)
            self.assertNotIn("strategy_name", existing)

    def test_active_strategy_split_cap_overrides_base_mode_cap(self):
        self.trader.set_ma_alignment_switch(True)
        history = self._history(range(1, 61))
        aligned_sf = self.trader.get_mode_config("SF", self.CURRENT_DATE, history)
        aligned_ag = self.trader.get_mode_config("AG", self.CURRENT_DATE, history)

        self.trader._active_buy_config = aligned_sf
        self.trader.current_round = 8
        self.assertTrue(self.trader.can_buy_next_round())
        self.trader.current_round = 11
        self.assertFalse(self.trader.can_buy_next_round())

        self.trader._active_buy_config = aligned_ag
        self.trader.current_round = 5
        self.assertFalse(self.trader.can_buy_next_round())

    def test_switch_state_is_isolated_per_trader_and_restored_from_snapshot(self):
        restored_user = SOXLQuantTrader(
            initial_capital=100_000,
            auto_update_rsi=False,
        )
        unrelated_user = SOXLQuantTrader(
            initial_capital=100_000,
            auto_update_rsi=False,
        )

        self.trader.set_ma_alignment_switch(True)
        snapshot_state = self.trader.get_ma_alignment_snapshot_state()

        restored_user.restore_ma_alignment_switch_from_snapshot(snapshot_state)

        self.assertTrue(self.trader.ma_alignment_switch_enabled)
        self.assertTrue(restored_user.ma_alignment_switch_enabled)
        self.assertFalse(unrelated_user.ma_alignment_switch_enabled)
        self.assertEqual(snapshot_state, {"ma_alignment_switch_enabled": True})

        restored_user.restore_ma_alignment_switch_from_snapshot(
            {"ma_alignment_switch_enabled": False}
        )
        self.assertFalse(restored_user.ma_alignment_switch_enabled)

    def test_explicit_user_choice_wins_over_stale_snapshot_setting(self):
        self.trader.set_ma_alignment_switch(True)

        restored = self.trader.restore_ma_alignment_switch_from_snapshot(
            {"ma_alignment_switch_enabled": False}
        )

        self.assertFalse(restored)
        self.assertTrue(self.trader.ma_alignment_switch_enabled)

    def test_aligned_position_rules_survive_snapshot_round_trip(self):
        snapshot = {
            "3_2026-04-01": {
                "shares": 20,
                "buy_price": 100.0,
                "amount": 2_000.0,
                "round": 3,
                "mode": "SF",
                "buy_threshold": 6.5,
                "sell_threshold": 1.8,
                "max_hold_days": 30,
                "strategy_name": "dongpa_ma_v1_1_r",
            },
            "available_cash": 98_000.0,
            "as_of_date": "2026-04-01",
        }

        positions, _, _ = self.trader._snapshot_to_positions_and_state(snapshot)
        config = self.trader.get_position_config(positions[0])

        self.assertEqual(config["buy_threshold"], 6.5)
        self.assertEqual(config["sell_threshold"], 1.8)
        self.assertEqual(config["max_hold_days"], 30)
        self.assertEqual(config["split_count"], 10)


if __name__ == "__main__":
    unittest.main()
