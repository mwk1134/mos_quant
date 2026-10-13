import contextlib
from copy import deepcopy
from datetime import datetime
import io
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from kmw_order_plan import DEFAULT_JEH_CONFIG, DEFAULT_JEH2_CONFIG, _KMWPlanTrader, build_kmw_plan, order_price
from soxl_quant_system import SOXLQuantTrader


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 12, 17, 1, tzinfo=KST)


def snapshots():
    return {
        "KMW": {
            "1_2026-10-05": {
                "round": 1, "shares": 41, "buy_price": 164.27000427246094,
                "amount": 6735.070175170898, "mode": "AG",
                "buy_threshold": 3.6, "sell_threshold": 3.5, "max_hold_days": 7,
            },
            "available_cash": 10000.0,
            "as_of_date": "2026-10-09",
            "compound_seed": 20000.0,
            "compound_reference_seed": 20000.0,
            "processed_seed_dates": ["2025-10-21", "2026-06-21"],
            "pending_buy": {
                "round": 2, "quantity": 9999, "target_price": 0.01,
                "order_date": "2026-10-07", "basis_date": "2026-10-06",
            },
        },
        "_preset_configs": {"KMW": {"ma_alignment_switch_enabled": False}},
    }


def two_preset_snapshots():
    source = snapshots()
    source["KMW2"] = {
        "1_2026-10-06": {
            "round": 1, "shares": 17, "buy_price": 80.0,
            "amount": 1360.0, "mode": "AG",
            "buy_threshold": 3.6, "sell_threshold": 3.5, "max_hold_days": 7,
        },
        "available_cash": 4321.0,
        "as_of_date": "2026-10-09",
        "compound_seed": 67612.0,
        "compound_reference_seed": 67612.0,
        "processed_seed_dates": [],
    }
    return source


def three_preset_snapshots():
    source = two_preset_snapshots()
    source["JEH"] = {
        "1_2026-09-28": {"round": 1, "shares": 3, "buy_price": 100.0,
                         "amount": 300.0, "mode": "AG", "buy_threshold": 3.6,
                         "sell_threshold": 3.5, "max_hold_days": 7},
        "2_2026-10-09": {"round": 2, "shares": 11, "buy_price": 50.0,
                         "amount": 550.0, "mode": "SF", "buy_threshold": 3.5,
                         "sell_threshold": 1.1, "max_hold_days": 35},
        "available_cash": 5432.0,
        "as_of_date": "2026-10-09",
        "compound_seed": 40000.0,
        "compound_reference_seed": 40000.0,
        "processed_seed_dates": ["2025-12-22", "2026-01-15"],
        "pending_buy": {"round": 3, "quantity": 9876, "target_price": 0.01,
                        "order_date": "2026-10-07", "basis_date": "2026-10-06"},
    }
    return source


def four_preset_snapshots():
    source = three_preset_snapshots()
    source["JEH2"] = {
        "1_2026-09-28": {"round": 1, "shares": 2, "buy_price": 102.0,
                         "amount": 204.0, "mode": "AG", "buy_threshold": 3.6,
                         "sell_threshold": 3.5, "max_hold_days": 7},
        "2_2026-10-09": {"round": 2, "shares": 7, "buy_price": 55.0,
                         "amount": 385.0, "mode": "SF", "buy_threshold": 3.5,
                         "sell_threshold": 1.1, "max_hold_days": 35},
        "available_cash": 1234.0,
        "as_of_date": "2026-10-09",
        "compound_seed": 5000.0,
        "compound_reference_seed": 5000.0,
        "processed_seed_dates": ["2026-01-15", "2026-06-16"],
        "pending_buy": {"round": 3, "quantity": 9876, "target_price": 0.01,
                        "order_date": "2026-10-07", "basis_date": "2026-10-06"},
    }
    return source


def recommendation(self, **kwargs):
    assert kwargs == {"skip_simulate": True, "preserve_snapshot_shares": True}
    return {
        "basis_date": self._expected_basis_date.isoformat(),
        "buy_order_date": self._get_next_trading_day(self._expected_basis_date.isoformat()),
        "mode": "AG", "strategy_name": "기본", "can_buy": True,
        "buy_price": 100.005, "next_buy_amount": 1000.05,
        "next_buy_round": self.current_round,
        # A target hit was hidden by the app. The actual holding must survive.
        "sell_recommendations": [],
    }


class KMWOrderPlanTests(unittest.TestCase):
    def build(self, data=None, now=NOW):
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            return build_kmw_plan(data if data is not None else snapshots(), now)

    def test_plan_uses_current_calculation_not_pending_buy_and_preserves_source(self):
        source = snapshots()
        original = deepcopy(source)
        plan = self.build(source)
        buy = next(row for row in plan["orders"] if row["side"] == "BUY")
        sell = next(row for row in plan["orders"] if row["side"] == "SELL")
        self.assertEqual(buy["quantity"], 10)
        self.assertEqual(buy["price"], "100.00")
        self.assertEqual(buy["position_key"], "2_2026-10-12")
        self.assertEqual(sell["quantity"], 41)
        self.assertEqual(sell["price"], "170.02")
        self.assertEqual(sell["order_type"], "LOC")
        self.assertEqual(plan["snapshot_positions"][0]["quantity"], 41)
        self.assertEqual(source, original)
        json.dumps(plan, allow_nan=False)

    def test_expiry_uses_order_day_and_becomes_moc_even_with_high_target(self):
        source = snapshots()
        position = source["KMW"].pop("1_2026-10-05")
        source["KMW"]["1_2026-10-01"] = position  # Seventh later session is Oct 12.
        sell = next(row for row in self.build(source)["orders"] if row["side"] == "SELL")
        self.assertEqual(sell["expiry_date"], "2026-10-12")
        self.assertEqual(sell["order_type"], "MOC")
        self.assertEqual(sell["price"], "0")

    def test_buy_preserves_displayed_quantity_even_when_snapshot_cash_is_lower(self):
        source = snapshots()
        source["KMW"]["available_cash"] = 250
        plan = self.build(source)
        buy = next(row for row in plan["orders"] if row["side"] == "BUY")
        self.assertEqual(buy["quantity"], 10)
        self.assertEqual(buy["price"], "100.00")
        self.assertFalse(any("adjusted" in warning for warning in plan["warnings"]))

    def test_buy_budget_below_one_share_does_not_create_a_zero_quantity_order(self):
        def small_budget(trader, **kwargs):
            result = recommendation(trader, **kwargs)
            result["next_buy_amount"] = 50.0
            return result

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", small_budget):
            plan = build_kmw_plan(snapshots(), NOW)
        self.assertFalse(any(order["side"] == "BUY" for order in plan["orders"]))
        self.assertTrue(any("one whole share" in warning for warning in plan["warnings"]))

    def test_missing_stale_or_malformed_snapshot_is_not_simulated(self):
        malformed = []
        missing = snapshots()
        missing["KMW"] = {}
        malformed.append(missing)
        stale = snapshots()
        stale["KMW"]["as_of_date"] = "2026-10-06"
        malformed.append(stale)
        fractional = snapshots()
        fractional["KMW"]["1_2026-10-05"]["shares"] = 1.5
        malformed.append(fractional)
        forgotten_seed = snapshots()
        forgotten_seed["KMW"]["processed_seed_dates"] = []
        malformed.append(forgotten_seed)
        with patch.object(_KMWPlanTrader, "get_daily_recommendation") as get_rec:
            for source in malformed:
                with self.subTest(source=source), self.assertRaises(ValueError):
                    build_kmw_plan(source, NOW)
            get_rec.assert_not_called()

    def test_plan_remains_visible_using_completed_close_and_next_trading_day(self):
        cases = (
            # The reported Korean morning was already after Monday's US close.
            (datetime(2026, 10, 13, 8, 41, tzinfo=KST), "2026-10-12", "2026-10-13"),
            # Before and during Monday's session, Friday remains the basis.
            (datetime(2026, 10, 12, 17, 1, tzinfo=KST), "2026-10-09", "2026-10-12"),
            (datetime(2026, 10, 12, 23, 0, tzinfo=KST), "2026-10-09", "2026-10-12"),
            (datetime(2026, 10, 13, 4, 59, tzinfo=KST), "2026-10-09", "2026-10-12"),
            (datetime(2026, 10, 13, 5, 0, tzinfo=KST), "2026-10-12", "2026-10-13"),
            # Weekend and full-day holiday previews target the next session.
            (datetime(2026, 10, 18, 17, 1, tzinfo=KST), "2026-10-16", "2026-10-19"),
            (datetime(2026, 11, 26, 17, 1, tzinfo=KST), "2026-11-25", "2026-11-27"),
            # After DST ends, the 16:00 ET boundary moves to 06:00 KST.
            (datetime(2026, 11, 3, 5, 59, tzinfo=KST), "2026-10-30", "2026-11-02"),
            (datetime(2026, 11, 3, 6, 0, tzinfo=KST), "2026-11-02", "2026-11-03"),
        )
        for now, basis, order in cases:
            source = snapshots()
            source["KMW"]["as_of_date"] = basis
            with self.subTest(now=now):
                plan = self.build(source, now)
                self.assertEqual(plan["status"], "ready")
                self.assertEqual(plan["basis_date"], basis)
                self.assertEqual(plan["order_date"], order)
                self.assertTrue(plan["snapshot_fresh"])
                self.assertEqual(len(plan["orders"]), 2)
                buy = next(row for row in plan["orders"] if row["side"] == "BUY")
                self.assertEqual(buy["position_key"], f"2_{order}")

    def test_after_close_freshness_uses_the_newly_completed_session(self):
        now = datetime(2026, 10, 13, 8, 41, tzinfo=KST)
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            with self.assertRaisesRegex(ValueError, "expected 2026-10-12"):
                build_kmw_plan(snapshots(), now)
            plan = build_kmw_plan(snapshots(), now, allow_stale_snapshot=True)
        self.assertEqual(plan["basis_date"], "2026-10-12")
        self.assertEqual(plan["order_date"], "2026-10-13")
        self.assertFalse(plan["snapshot_fresh"])
        self.assertEqual(plan["snapshot_positions"][0]["quantity"], 41)
        self.assertEqual(plan["available_cash"], 10000)
        self.assertTrue(any("Preview only" in warning for warning in plan["warnings"]))

    def test_naive_datetime_and_wrong_recommendation_date_are_rejected(self):
        with self.assertRaises(ValueError):
            self.build(now=datetime(2026, 10, 12, 17, 1))
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", return_value={
            "basis_date": "2026-10-09", "can_buy": True,
            "buy_order_date": "2026-10-13",
        }), self.assertRaisesRegex(ValueError, "another order date"):
            build_kmw_plan(snapshots(), NOW)

    def test_market_fallback_must_include_latest_completed_session(self):
        trader = _KMWPlanTrader(now=NOW, expected_basis_date=NOW.date().replace(day=9))
        data = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime(["2026-10-08"]))
        with patch.object(SOXLQuantTrader, "get_stock_data", return_value=data):
            with self.assertRaisesRegex(ValueError, "Stale QQQ"):
                trader.get_stock_data("QQQ", "6mo")

    def test_cash_only_snapshot_supports_first_round(self):
        source = snapshots()
        del source["KMW"]["1_2026-10-05"]
        plan = self.build(source)
        self.assertEqual(plan["snapshot_positions"], [])
        self.assertEqual(len(plan["orders"]), 1)
        self.assertEqual(plan["orders"][0]["round"], 1)

    def test_stale_preview_keeps_old_state_and_is_explicitly_marked(self):
        source = snapshots()
        source["KMW"]["as_of_date"] = "2026-10-08"
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            plan = build_kmw_plan(source, NOW, allow_stale_snapshot=True)
        self.assertFalse(plan["snapshot_fresh"])
        self.assertEqual(plan["snapshot_date"], "2026-10-08")
        self.assertEqual(plan["basis_date"], "2026-10-09")
        self.assertEqual(plan["snapshot_positions"][0]["quantity"], 41)
        self.assertEqual(plan["available_cash"], 10000)
        self.assertTrue(any("Preview only" in warning for warning in plan["warnings"]))
        future = snapshots()
        future["KMW"]["as_of_date"] = "2026-10-12"
        with self.assertRaisesRegex(ValueError, "after the completed"):
            build_kmw_plan(future, NOW, allow_stale_snapshot=True)

    def test_persisted_kmw_seed_and_alignment_config_are_used(self):
        source = snapshots()
        source["_preset_configs"]["KMW"].update({
            "initial_capital": 12000,
            "ma_alignment_switch_enabled": True,
            "seed_increases": [
                {"date": "2026-06-21", "amount": 11293},
                {"date": "2026-10-04", "amount": 7068},
            ],
        })
        source["KMW"]["processed_seed_dates"].append("2026-10-04")
        captured = {}

        def capture(trader, **kwargs):
            captured.update({
                "capital": trader.initial_capital,
                "alignment": trader.ma_alignment_switch_enabled,
                "seeds": deepcopy(trader.seed_increases),
            })
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            build_kmw_plan(source, NOW)
        self.assertEqual(captured["capital"], 12000)
        self.assertTrue(captured["alignment"])
        self.assertEqual(captured["seeds"][-1]["amount"], 7068)

    def test_nonfinite_and_zero_loc_prices_rejected(self):
        for value in ("NaN", "Infinity", "0", "0.001", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                order_price(value)


class KMW2OrderPlanTests(unittest.TestCase):
    def build(self, source=None, *, preset="KMW2", now=NOW, allow_stale_snapshot=False):
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            return build_kmw_plan(
                source if source is not None else two_preset_snapshots(),
                now, preset=preset, allow_stale_snapshot=allow_stale_snapshot,
            )

    def test_kmw2_uses_only_its_factual_holdings_and_cash(self):
        source = two_preset_snapshots()
        original = deepcopy(source)
        plan = self.build(source)
        self.assertEqual(plan["preset"], "KMW2")
        self.assertEqual(plan["available_cash"], 4321)
        self.assertEqual(plan["snapshot_positions"][0]["position_key"], "1_2026-10-06")
        sell = next(order for order in plan["orders"] if order["side"] == "SELL")
        self.assertEqual(sell["quantity"], 17)
        self.assertEqual(sell["price"], "82.80")
        buy = next(order for order in plan["orders"] if order["side"] == "BUY")
        self.assertEqual(buy["quantity"], 10)
        self.assertEqual(buy["price"], "100.00")
        self.assertEqual(source, original)
        json.dumps(plan, allow_nan=False)

    def test_kmw2_defaults_match_app_and_never_inject_kmw_seed(self):
        captured = {}

        def capture(trader, **kwargs):
            captured.update({
                "capital": trader.initial_capital,
                "start": trader.session_start_date,
                "alignment": trader.ma_alignment_switch_enabled,
                "seeds": deepcopy(trader.seed_increases),
            })
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            build_kmw_plan(two_preset_snapshots(), NOW, preset="KMW2")
        self.assertEqual(captured, {
            "capital": 67612,
            "start": "2026-04-29",
            "alignment": False,
            "seeds": [],
        })

    def test_saved_kmw2_configuration_and_position_edits_are_selected_exactly(self):
        source = two_preset_snapshots()
        source["_preset_configs"]["KMW"] = {
            "initial_capital": 1234,
            "ma_alignment_switch_enabled": False,
            "seed_increases": [{"date": "2026-08-04", "amount": 999}],
        }
        source["_preset_configs"]["KMW2"] = {
            "initial_capital": 70000,
            "session_start_date": "2026-05-04",
            "ma_alignment_switch_enabled": True,
            "seed_increases": [{"date": "2026-08-03", "amount": 2000}],
            "position_edits": {
                "1_2026-10-06": {"shares": 19, "buy_price": 81},
            },
        }
        source["KMW2"]["processed_seed_dates"].append("2026-08-03")
        source["KMW2"]["ma_alignment_switch_enabled"] = False
        original = deepcopy(source)
        captured = {}

        def capture(trader, **kwargs):
            captured.update({
                "capital": trader.initial_capital,
                "start": trader.session_start_date,
                "alignment": trader.ma_alignment_switch_enabled,
                "seeds": deepcopy(trader.seed_increases),
            })
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            plan = build_kmw_plan(source, NOW, preset="KMW2")
        captured["seeds"] = [
            {"date": seed["date"], "amount": seed["amount"]}
            for seed in captured["seeds"]
        ]
        self.assertEqual(captured, {
            "capital": 70000,
            "start": "2026-05-04",
            "alignment": True,
            "seeds": [{"date": "2026-08-03", "amount": 2000.0}],
        })
        sell = next(order for order in plan["orders"] if order["side"] == "SELL")
        self.assertEqual(sell["quantity"], 19)
        self.assertEqual(sell["price"], "83.84")
        self.assertEqual(source, original)

    def test_snapshot_alignment_fallback_is_preset_specific(self):
        source = two_preset_snapshots()
        source["KMW"]["ma_alignment_switch_enabled"] = False
        source["KMW2"]["ma_alignment_switch_enabled"] = True
        captured = []

        def capture(trader, **kwargs):
            captured.append(trader.ma_alignment_switch_enabled)
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            build_kmw_plan(source, NOW, preset="KMW2")
            build_kmw_plan(source, NOW)
        self.assertEqual(captured, [True, False])

    def test_missing_kmw2_and_unsupported_presets_never_fall_back_to_kmw(self):
        with patch.object(_KMWPlanTrader, "get_daily_recommendation") as get_rec:
            for missing in (None, {}, [], "KMW"):
                source = snapshots()
                if missing is not None:
                    source["KMW2"] = missing
                with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "KMW2 portfolio"):
                    build_kmw_plan(source, NOW, preset="KMW2")
            for preset in ("UNREGISTERED", "kmw2", "", None, []):
                with self.subTest(preset=preset), self.assertRaisesRegex(ValueError, "Unsupported preset"):
                    build_kmw_plan(two_preset_snapshots(), NOW, preset=preset)
            get_rec.assert_not_called()

    def test_only_selected_saved_config_is_validated(self):
        source = two_preset_snapshots()
        source["_preset_configs"]["KMW"] = "malformed other account config"
        self.assertEqual(self.build(source)["preset"], "KMW2")
        source["_preset_configs"]["KMW2"] = "malformed selected account config"
        with self.assertRaisesRegex(ValueError, r"_preset_configs\.KMW2"):
            self.build(source)

    def test_kmw2_freshness_is_enforced_without_using_fresh_kmw_snapshot(self):
        source = two_preset_snapshots()
        source["KMW2"]["as_of_date"] = "2026-10-08"
        with self.assertRaisesRegex(ValueError, "Stale KMW2 snapshot"):
            self.build(source)
        plan = self.build(source, allow_stale_snapshot=True)
        self.assertFalse(plan["snapshot_fresh"])
        self.assertEqual(plan["snapshot_positions"][0]["quantity"], 17)
        self.assertTrue(any("Preview only" in warning for warning in plan["warnings"]))

    def test_kmw2_recommendation_stays_visible_after_close(self):
        source = two_preset_snapshots()
        source["KMW2"]["as_of_date"] = "2026-10-12"
        plan = self.build(source, now=datetime(2026, 10, 13, 8, 41, tzinfo=KST))
        self.assertEqual(plan["basis_date"], "2026-10-12")
        self.assertEqual(plan["order_date"], "2026-10-13")
        self.assertEqual(plan["status"], "ready")

    def test_legacy_kmw_default_still_enforces_its_mandatory_seed(self):
        source = two_preset_snapshots()
        source["_preset_configs"]["KMW"]["seed_increases"] = []
        captured = []

        def capture(trader, **kwargs):
            captured.extend(deepcopy(trader.seed_increases))
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            plan = build_kmw_plan(source, NOW)
        self.assertEqual(plan["preset"], "KMW")
        self.assertEqual(plan["available_cash"], 10000)
        self.assertEqual(plan["snapshot_positions"][0]["quantity"], 41)
        self.assertEqual(
            [{"date": seed["date"], "amount": seed["amount"]} for seed in captured],
            [{"date": "2026-06-21", "amount": 11293.0}],
        )


class KMWEngineIntegrationTests(unittest.TestCase):
    def _build_with_market_data(self, source, data, now):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(SOXLQuantTrader, "get_stock_data", return_value=data),
            patch.object(SOXLQuantTrader, "get_rsi_from_reference", return_value=60.0),
            patch.object(SOXLQuantTrader, "_revalidate_position_modes"),
            patch.object(SOXLQuantTrader, "update_mode", return_value="AG"),
            patch.object(SOXLQuantTrader, "_calculate_week_mode_recursive_with_reference", return_value=("AG", True)),
        ):
            return build_kmw_plan(source, now)

    def test_real_recommendation_retains_holding_after_historical_target_hit(self):
        # Both auto-sale reconciliation and LOC-backtest pruning would erase
        # this confirmed holding in the unmodified strategy engine.
        dates = pd.bdate_range("2026-04-01", "2026-10-09")
        data = pd.DataFrame({
            "Open": 200.0, "High": 200.0, "Low": 200.0,
            "Close": 200.0, "Volume": 1000000,
        }, index=dates)
        # A provider's current-day partial candle must not change the LOC limit.
        data.loc[pd.Timestamp("2026-10-12")] = [999, 999, 999, 999, 1000000]
        plan = self._build_with_market_data(snapshots(), data, NOW)
        sells = [order for order in plan["orders"] if order["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["quantity"], 41)
        self.assertEqual(sells[0]["price"], "170.02")
        self.assertEqual(plan["available_cash"], 10000)
        buys = [order for order in plan["orders"] if order["side"] == "BUY"]
        self.assertEqual(buys[0]["price"], "207.20")
        self.assertEqual(buys[0]["quantity"], int((20000 * 0.134) / 207.2))

    def test_intraday_real_recommendation_excludes_partial_candle(self):
        dates = pd.bdate_range("2026-04-01", "2026-10-09")
        data = pd.DataFrame({
            "Open": 200.0, "High": 200.0, "Low": 200.0,
            "Close": 200.0, "Volume": 1000000,
        }, index=dates)
        data.loc[pd.Timestamp("2026-10-12")] = [999, 999, 999, 999, 1000000]
        plan = self._build_with_market_data(
            snapshots(), data, datetime(2026, 10, 13, 4, 59, tzinfo=KST)
        )
        self.assertEqual(plan["basis_date"], "2026-10-09")
        self.assertEqual(plan["order_date"], "2026-10-12")
        buy = next(row for row in plan["orders"] if row["side"] == "BUY")
        self.assertEqual(buy["price"], "207.20")

    def test_korean_morning_real_recommendation_includes_latest_completed_close(self):
        dates = pd.bdate_range("2026-04-01", "2026-10-09")
        data = pd.DataFrame({
            "Open": 200.0, "High": 200.0, "Low": 200.0,
            "Close": 200.0, "Volume": 1000000,
        }, index=dates)
        data.loc[pd.Timestamp("2026-10-12")] = [250, 250, 250, 250, 1000000]
        # Even if a provider includes a future/partial next-day candle, it
        # cannot override the latest confirmed close used by the web app.
        data.loc[pd.Timestamp("2026-10-13")] = [999, 999, 999, 999, 1000000]
        source = snapshots()
        source["KMW"]["as_of_date"] = "2026-10-12"
        original = deepcopy(source)
        plan = self._build_with_market_data(
            source, data, datetime(2026, 10, 13, 8, 41, tzinfo=KST)
        )
        self.assertEqual(plan["basis_date"], "2026-10-12")
        self.assertEqual(plan["order_date"], "2026-10-13")
        buy = next(row for row in plan["orders"] if row["side"] == "BUY")
        self.assertEqual(buy["price"], "259.00")
        self.assertEqual(buy["position_key"], "2_2026-10-13")
        sell = next(row for row in plan["orders"] if row["side"] == "SELL")
        self.assertEqual(sell["quantity"], 41)
        self.assertEqual(plan["available_cash"], 10000)
        self.assertEqual(source, original)


class JEH2OrderPlanTests(unittest.TestCase):
    def build(self, source=None, *, allow_stale_snapshot=False):
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            return build_kmw_plan(source if source is not None else four_preset_snapshots(),
                                  NOW, preset="JEH2", allow_stale_snapshot=allow_stale_snapshot)

    def test_selected_account_preserves_independent_orders_and_cash(self):
        source = four_preset_snapshots()
        source["_preset_configs"].update({name: "unselected config" for name in ("KMW", "KMW2", "JEH")})
        original = deepcopy(source)
        plan = self.build(source)
        self.assertEqual(plan["preset"], "JEH2")
        self.assertEqual(plan["available_cash"], 1234)
        self.assertEqual([(row["side"], row["quantity"], row["price"], row["order_type"])
                          for row in plan["orders"]],
                         [("SELL", 2, "0", "MOC"), ("SELL", 7, "55.61", "LOC"),
                          ("BUY", 10, "100.00", "LOC")])
        self.assertEqual([row["quantity"] for row in plan["snapshot_positions"]], [2, 7])
        self.assertEqual(source, original)

    def test_entry_settings_match_webapp_and_keep_mandatory_june_seed(self):
        source = four_preset_snapshots()
        # The original app restores the June seed after applying saved settings.
        source["_preset_configs"]["JEH2"] = {"seed_increases": [{"date": "2026-01-15", "amount": 678.0}]}
        captured = {}

        def capture(trader, **kwargs):
            captured.update(capital=trader.initial_capital, start=trader.session_start_date,
                            seeds=[{"date": seed["date"], "amount": seed["amount"]}
                                   for seed in trader.seed_increases],
                            alignment=trader.ma_alignment_switch_enabled)
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            build_kmw_plan(source, NOW, preset="JEH2")
        self.assertEqual(captured, {"capital": 2704, "start": "2025-12-22",
                                    "seeds": [{"date": "2026-01-15", "amount": 678.0},
                                              {"date": "2026-06-16", "amount": 600.0}],
                                    "alignment": False})
        self.assertEqual(captured["seeds"], DEFAULT_JEH2_CONFIG["seed_increases"])

    def test_missing_or_stale_snapshot_cannot_borrow_another_accounts_holdings(self):
        with self.assertRaisesRegex(ValueError, "JEH2 portfolio"):
            self.build(three_preset_snapshots())
        source = four_preset_snapshots()
        source["JEH2"]["as_of_date"] = "2026-10-08"
        with self.assertRaisesRegex(ValueError, "Stale JEH2 snapshot"):
            self.build(source)
        plan = self.build(source, allow_stale_snapshot=True)
        self.assertFalse(plan["snapshot_fresh"])
        self.assertEqual(plan["snapshot_positions"][1]["quantity"], 7)

    def test_unprocessed_mandatory_seed_blocks_before_recommendation(self):
        source = four_preset_snapshots()
        source["JEH2"]["processed_seed_dates"].remove("2026-06-16")
        with self.assertRaisesRegex(ValueError, "seed increase 2026-06-16"):
            self.build(source)


class JEHOrderPlanTests(unittest.TestCase):
    def build(self, source=None, *, now=NOW, allow_stale_snapshot=False):
        with patch.object(_KMWPlanTrader, "get_daily_recommendation", recommendation):
            return build_kmw_plan(source if source is not None else three_preset_snapshots(),
                                  now, preset="JEH", allow_stale_snapshot=allow_stale_snapshot)

    def test_jeh_preserves_its_rows_prices_cash_and_source_without_other_accounts(self):
        source = three_preset_snapshots()
        # Other preset configuration cannot affect the selected account.
        source["_preset_configs"]["KMW"] = "not a selected account config"
        source["_preset_configs"]["KMW2"] = "not a selected account config"
        original = deepcopy(source)
        plan = self.build(source)
        self.assertEqual(plan["preset"], "JEH")
        self.assertEqual(plan["available_cash"], 5432)
        self.assertEqual([(row["side"], row["quantity"], row["price"], row["order_type"])
                          for row in plan["orders"]],
                         [("SELL", 3, "0", "MOC"), ("SELL", 11, "50.55", "LOC"),
                          ("BUY", 10, "100.00", "LOC")])
        self.assertEqual([row["position_key"] for row in plan["snapshot_positions"]],
                         ["1_2026-09-28", "2_2026-10-09"])
        self.assertEqual(source, original)

    def test_jeh_defaults_and_saved_seed_configuration_match_webapp(self):
        self.assertEqual(DEFAULT_JEH_CONFIG, {
            "initial_capital": 2793.0, "session_start_date": "2025-10-30",
            "seed_increases": [{"date": "2025-12-22", "amount": 13499.0},
                               {"date": "2026-01-15", "amount": 2035.0}],
            "position_edits": {}, "ma_alignment_switch_enabled": False,
        })
        source = three_preset_snapshots()
        seeds = deepcopy(DEFAULT_JEH_CONFIG["seed_increases"]) + [{"date": "2026-10-04", "amount": 7068.0}]
        source["_preset_configs"]["JEH"] = {"seed_increases": seeds,
                                            "ma_alignment_switch_enabled": True,
                                            "position_edits": {"2_2026-10-09": {"shares": 12, "buy_price": 51}}}
        source["JEH"]["processed_seed_dates"].append("2026-10-04")
        captured = {}

        def capture(trader, **kwargs):
            captured.update(capital=trader.initial_capital, start=trader.session_start_date,
                            seeds=[{"date": seed["date"], "amount": seed["amount"]}
                                   for seed in trader.seed_increases],
                            alignment=trader.ma_alignment_switch_enabled)
            return recommendation(trader, **kwargs)

        with patch.object(_KMWPlanTrader, "get_daily_recommendation", capture):
            plan = build_kmw_plan(source, NOW, preset="JEH")
        self.assertEqual(captured, {"capital": 2793, "start": "2025-10-30",
                                    "seeds": seeds, "alignment": True})
        self.assertNotIn("2026-06-21", [seed["date"] for seed in captured["seeds"]])
        self.assertEqual(plan["orders"][1]["quantity"], 12)
        self.assertEqual(plan["orders"][1]["price"], "51.56")

    def test_jeh_unprocessed_saved_seed_blocks_even_display(self):
        source = three_preset_snapshots()
        source["_preset_configs"]["JEH"] = {"seed_increases": [{"date": "2026-10-04", "amount": 7068.0}]}
        with self.assertRaisesRegex(ValueError, "seed increase 2026-10-04"):
            self.build(source)

    def test_jeh_missing_snapshot_never_falls_back_to_kmw_accounts(self):
        with self.assertRaisesRegex(ValueError, "JEH portfolio"):
            self.build(two_preset_snapshots())

    def test_jeh_stale_snapshot_is_display_only(self):
        source = three_preset_snapshots()
        source["JEH"]["as_of_date"] = "2026-10-08"
        with self.assertRaisesRegex(ValueError, "Stale JEH snapshot"):
            self.build(source)
        plan = self.build(source, allow_stale_snapshot=True)
        self.assertFalse(plan["snapshot_fresh"])
        self.assertIn("Preview only; submission is prohibited", plan["warnings"][0])


if __name__ == "__main__":
    unittest.main()
