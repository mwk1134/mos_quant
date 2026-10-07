import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class PositionModeCorrectionTests(unittest.TestCase):
    BUY_DATE = datetime(2026, 10, 5)
    TODAY = datetime(2026, 10, 6)
    CONFIRMED_FIELDS = ("shares", "buy_price", "amount", "round", "buy_date")
    RULE_FIELDS = ("buy_threshold", "sell_threshold", "max_hold_days")

    def setUp(self):
        self.trader = SOXLQuantTrader(initial_capital=100_000, auto_update_rsi=False)
        dates = pd.bdate_range("2026-04-01", self.TODAY)
        closes = pd.Series(163.87, index=dates)
        # Keep both sessions below the stale SF and corrected AG sell targets.
        # Oct 2 also permits the Oct 5 LOC buy under either mode's buy limit.
        closes.loc["2026-10-05":] = 164.27
        self.market_data = pd.DataFrame({
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": 1_000_000,
        })

    def position(self, mode="SF", config=None):
        config = config or self.trader.sf_config
        position = {
            "round": 1,
            "buy_date": self.BUY_DATE,
            "buy_price": 164.27,
            "shares": 29,
            "amount": 4_763.83,
            "mode": mode,
        }
        position.update({key: config[key] for key in self.RULE_FIELDS})
        if config.get("strategy_name"):
            position["strategy_name"] = config["strategy_name"]
        return position

    def snapshot(self, position):
        return {
            "1_2026-10-05": position.copy(),
            "available_cash": 95_236.17,
            "as_of_date": "2026-10-05",
        }

    def assert_confirmed_unchanged(self, position, original):
        for key in self.CONFIRMED_FIELDS:
            with self.subTest(field=key):
                self.assertEqual(position[key], original[key])

    def assert_rules(self, position, config):
        for key in self.RULE_FIELDS:
            with self.subTest(field=key):
                self.assertEqual(position[key], config[key])

    def recommendation(self, correct_mode):
        self.trader.current_mode = correct_mode
        self.trader.current_week_friday = datetime(2026, 10, 9)
        self.trader.current_round = 2
        with (
            redirect_stdout(StringIO()),
            patch.object(self.trader, "get_today_date", return_value=self.TODAY),
            patch.object(self.trader, "is_regular_session_closed_now", return_value=True),
            patch.object(self.trader, "get_stock_data", return_value=self.market_data.copy()),
            patch.object(self.trader, "get_rsi_from_reference", return_value=60.0),
            patch.object(self.trader, "_is_mode_case_matched", return_value=(False, None)),
            patch.object(
                self.trader,
                "_calculate_week_mode_recursive_with_reference",
                return_value=(correct_mode, True),
            ),
        ):
            result = self.trader.get_daily_recommendation(skip_simulate=True)
        self.assertNotIn("error", result)
        self.assertEqual(len(result["sell_recommendations"]), 1)
        self.assertEqual(len(result["sell_debug_info"]), 1)
        return result

    def test_historical_mode_correction_refreshes_sell_target_and_stop_date(self):
        position = self.position()
        original = position.copy()
        self.trader.positions = [position]

        result = self.recommendation("AG")

        corrected = result["sell_recommendations"][0]["position"]
        debug = result["sell_debug_info"][0]
        self.assertEqual(corrected["mode"], "AG")
        self.assert_rules(corrected, self.trader.ag_config)
        self.assert_confirmed_unchanged(corrected, original)
        self.assertEqual(debug["target_sell_price"], 170.02)
        self.assertEqual(debug["stop_loss_date"], "2026-10-14")
        self.assertEqual(debug["max_hold_days"], 7)
        self.assertFalse(result["sell_recommendations"][0]["will_sell"])

    def test_reverse_historical_correction_refreshes_safe_mode_rules(self):
        position = self.position("AG", self.trader.ag_config)
        original = position.copy()
        self.trader.positions = [position]

        result = self.recommendation("SF")

        self.assertEqual(position["mode"], "SF")
        self.assert_rules(position, self.trader.sf_config)
        self.assert_confirmed_unchanged(position, original)
        self.assertEqual(result["sell_debug_info"][0]["target_sell_price"], 166.08)
        self.assertEqual(result["sell_debug_info"][0]["max_hold_days"], 35)

    def test_correction_uses_the_presets_configured_mode_rules(self):
        self.trader.ag_config.update({
            "buy_threshold": 8.0,
            "sell_threshold": 4.2,
            "max_hold_days": 11,
        })
        position = self.position()
        original = position.copy()
        self.trader.positions = [position]

        self.recommendation("AG")

        self.assert_rules(position, self.trader.ag_config)
        self.assert_confirmed_unchanged(position, original)

    def test_alignment_position_keeps_entry_strategy_when_switch_is_disabled(self):
        position = self.position("SF", self.trader.MA_ALIGNMENT_SF_CONFIG)
        original = position.copy()
        self.trader.positions = [position]
        self.assertFalse(self.trader.ma_alignment_switch_enabled)

        result = self.recommendation("AG")

        self.assertEqual(position["strategy_name"], self.trader.MA_ALIGNMENT_STRATEGY_NAME)
        self.assert_rules(position, self.trader.MA_ALIGNMENT_AG_CONFIG)
        self.assert_confirmed_unchanged(position, original)
        self.assertEqual(result["sell_debug_info"][0]["target_sell_price"], 175.44)
        self.assertEqual(result["sell_debug_info"][0]["stop_loss_date"], "2026-10-14")

    def test_already_correct_custom_position_rules_remain_frozen(self):
        custom_config = {"buy_threshold": 9.9, "sell_threshold": 4.8, "max_hold_days": 12}
        position = self.position("AG", custom_config)
        original = position.copy()
        self.trader.positions = [position]

        self.recommendation("AG")

        self.assertEqual(position, original)

    def test_mode_correction_preserves_custom_entry_rules_in_known_strategy_family(self):
        custom_config = {"buy_threshold": 9.9, "sell_threshold": 4.8, "max_hold_days": 12}
        for strategy_name in (None, self.trader.MA_ALIGNMENT_STRATEGY_NAME):
            with self.subTest(strategy_name=strategy_name):
                position = self.position("SF", custom_config)
                if strategy_name:
                    position["strategy_name"] = strategy_name
                original = position.copy()
                self.trader.positions = [position]

                self.recommendation("AG")

                self.assertEqual(position["mode"], "AG")
                self.assert_rules(position, custom_config)
                self.assert_confirmed_unchanged(position, original)
                self.assertEqual(position.get("strategy_name"), strategy_name)

    def test_mode_correction_preserves_unknown_named_strategy_entry_rules(self):
        position = self.position()
        position["strategy_name"] = "custom_entry_strategy"
        original = position.copy()
        self.trader.positions = [position]

        self.recommendation("AG")

        self.assertEqual(position["mode"], "AG")
        self.assertEqual(position["strategy_name"], "custom_entry_strategy")
        self.assert_rules(position, self.trader.sf_config)
        self.assert_confirmed_unchanged(position, original)

    def test_mode_correction_fills_missing_canonical_entry_rule_fields(self):
        for missing_fields in [(key,) for key in self.RULE_FIELDS] + [self.RULE_FIELDS]:
            with self.subTest(missing_fields=missing_fields):
                position = self.position()
                original = position.copy()
                for key in missing_fields:
                    position.pop(key)

                self.trader._correct_position_mode(position, "AG")

                self.assertEqual(position["mode"], "AG")
                self.assert_rules(position, self.trader.ag_config)
                self.assert_confirmed_unchanged(position, original)

    def test_restore_repairs_ag_position_with_legacy_sf_rule_tuple(self):
        original = self.position("AG", self.trader.sf_config)

        positions, resume_date, cash = self.trader._snapshot_to_positions_and_state(
            self.snapshot(original)
        )

        self.assertEqual(positions[0]["mode"], "AG")
        self.assert_rules(positions[0], self.trader.ag_config)
        self.assert_confirmed_unchanged(positions[0], original)
        self.assertEqual(resume_date, "2026-10-05")
        self.assertEqual(cash, 95_236.17)

    def test_restore_repairs_sf_position_with_legacy_ag_rule_tuple(self):
        original = self.position("SF", self.trader.ag_config)

        positions, _, _ = self.trader._snapshot_to_positions_and_state(self.snapshot(original))

        self.assertEqual(positions[0]["mode"], "SF")
        self.assert_rules(positions[0], self.trader.sf_config)
        self.assert_confirmed_unchanged(positions[0], original)

    def test_restore_preserves_correct_custom_rule_tuple(self):
        custom_config = {"buy_threshold": 9.9, "sell_threshold": 4.8, "max_hold_days": 12}
        original = self.position("AG", custom_config)

        positions, _, _ = self.trader._snapshot_to_positions_and_state(self.snapshot(original))

        self.assert_rules(positions[0], custom_config)
        self.assert_confirmed_unchanged(positions[0], original)

    def test_missing_snapshot_mode_refreshes_frozen_rules_after_recomputation(self):
        original = self.position()
        snapshot = self.snapshot(original)
        snapshot["1_2026-10-05"].pop("mode")
        positions, _, _ = self.trader._snapshot_to_positions_and_state(snapshot)
        with (
            redirect_stdout(StringIO()),
            patch.object(self.trader, "get_stock_data", return_value=self.market_data.copy()),
            patch.object(self.trader, "get_rsi_from_reference", return_value=60.0),
            patch.object(self.trader, "_is_mode_case_matched", return_value=(False, None)),
            patch.object(
                self.trader,
                "_calculate_week_mode_recursive_with_reference",
                return_value=("AG", True),
            ),
        ):
            self.trader._recompute_missing_position_modes(positions)

        self.assertEqual(positions[0]["mode"], "AG")
        self.assert_rules(positions[0], self.trader.ag_config)
        self.assert_confirmed_unchanged(positions[0], original)
        self.assertFalse(positions[0].get("_mode_needs_recalc"))

    def test_stale_snapshot_rules_are_corrected_before_backtest_resume(self):
        original = self.position()
        # A close above the obsolete $166.08 SF target must leave the original
        # lot held because it is still below the corrected $170.02 AG target.
        self.market_data.loc["2026-10-06", ["Open", "High", "Low", "Close"]] = 166.50
        with (
            redirect_stdout(StringIO()),
            patch.object(self.trader, "get_today_date", return_value=self.TODAY),
            patch.object(self.trader, "get_us_eastern_now", return_value=datetime(2026, 10, 6, 17)),
            patch.object(self.trader, "get_latest_trading_day", return_value=self.TODAY),
            patch.object(self.trader, "is_regular_session_closed_now", return_value=True),
            patch.object(self.trader, "get_stock_data", return_value=self.market_data.copy()),
            patch.object(self.trader, "get_rsi_from_reference", return_value=60.0),
            patch.object(self.trader, "_is_mode_case_matched", return_value=(False, None)),
            patch.object(
                self.trader,
                "_calculate_week_mode_recursive_with_reference",
                return_value=("AG", True),
            ),
            patch.object(self.trader, "run_backtest", wraps=self.trader.run_backtest) as run_backtest,
        ):
            result = self.trader.simulate_from_snapshot_to_today(self.snapshot(original), "2026-10-05")

        self.assertNotIn("error", result)
        run_backtest.assert_called_once()
        resumed_position = run_backtest.call_args.kwargs["initial_positions"][0]
        self.assertEqual(resumed_position["mode"], "AG")
        self.assert_rules(resumed_position, self.trader.ag_config)
        self.assert_confirmed_unchanged(resumed_position, original)
        self.assertEqual(run_backtest.call_args.args, ("2026-10-06", "2026-10-06"))
        retained_lots = [p for p in self.trader.positions if p["buy_date"] == self.BUY_DATE]
        self.assertEqual(len(retained_lots), 1)
        self.assert_confirmed_unchanged(retained_lots[0], original)
        self.assertEqual(result["daily_records"][0]["close_price"], 166.50)
        self.assertEqual(result["daily_records"][0]["daily_realized"], 0)


if __name__ == "__main__":
    unittest.main()
