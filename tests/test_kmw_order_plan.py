import contextlib
from copy import deepcopy
from datetime import datetime
import io
import json
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from kmw_order_plan import _KMWPlanTrader, build_kmw_plan, order_price
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


if __name__ == "__main__":
    unittest.main()
