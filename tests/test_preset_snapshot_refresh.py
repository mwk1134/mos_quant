import copy
import unittest
from unittest.mock import patch

from preset_snapshot_refresh import SnapshotRefreshBlocked, refresh_preset_snapshot


RULES = {"mode": "AG", "buy_threshold": 2.0, "sell_threshold": 2.8,
         "max_hold_days": 6, "strategy_name": "공세"}


def document():
    return {"JEH": {"1_2026-10-08": {"shares": 100, "buy_price": 100.0,
                "amount": 10000.0, "round": 1, **RULES},
            "as_of_date": "2026-10-09", "available_cash": 10000.0,
            "compound_seed": 20000.0, "compound_reference_seed": 20000.0,
            "compound_profit_rate": 0.70, "compound_loss_rate": 0.20,
            "compound_settlements": [], "processed_seed_dates": [],
            "ma_alignment_switch_enabled": True,
            "pending_buy": {"round": 2, "order_date": "2026-10-12", "quantity": 56,
                            "target_price": 104.0, "basis_date": "2026-10-09", **RULES}},
            "KMW": {"as_of_date": "2026-10-09", "unchanged": [1, 2]},
            "_preset_configs": {"JEH": {"initial_capital": 20000.0,
                "session_start_date": "2026-10-01", "seed_increases": []}}}


def fill(side="BUY", qty=56, price=101, order_id="123", **kwargs):
    return {"order_id": order_id, "trade_date": "2026-10-12", "side": side,
            "quantity": qty, "price": price, **kwargs}


def refresh(doc=None, fills=None, quantity=156, basis="2026-10-12", **kwargs):
    return refresh_preset_snapshot(document() if doc is None else doc, preset="JEH",
        basis_date=basis, fills=[fill()] if fills is None else fills,
        broker_quantity=quantity, buy_rules={"2026-10-12": RULES}, **kwargs)


class SnapshotRefreshTests(unittest.TestCase):
    def assert_blocked(self, expected, **kwargs):
        with self.assertRaises(SnapshotRefreshBlocked) as raised:
            refresh(**kwargs)
        self.assertEqual(raised.exception.code, expected)

    def test_confirmed_buy_adds_actual_lot_and_advances_source_date(self):
        result = refresh()
        snapshot = result["JEH"]
        self.assertEqual(snapshot["2_2026-10-12"]["shares"], 56)
        self.assertEqual(snapshot["2_2026-10-12"]["buy_price"], 101)
        self.assertEqual(snapshot["available_cash"], 4344)
        self.assertEqual(snapshot["as_of_date"], "2026-10-12")
        self.assertNotIn("pending_buy", snapshot)
        self.assertEqual(snapshot["compound_seed"], 20000)
        self.assertEqual(snapshot["2_2026-10-12"]["sell_threshold"], 2.8)

    def test_confirmed_notional_is_used_with_bounded_average_rounding(self):
        result = refresh(fills=[fill(price="101.00001", notional="5656.00")])
        self.assertEqual(result["JEH"]["available_cash"], 4344)
        self.assertEqual(result["JEH"]["2_2026-10-12"]["buy_price"], 101)
        self.assert_blocked("FILL_AMOUNT_MISMATCH", fills=[fill(notional="5600")])

    def test_document_is_copied_other_presets_config_and_entry_rules_are_preserved(self):
        original = document()
        before = copy.deepcopy(original)
        result = refresh(original)
        self.assertEqual(original, before)
        self.assertEqual(result["KMW"], original["KMW"])
        self.assertEqual(result["_preset_configs"], original["_preset_configs"])
        self.assertEqual(result["JEH"]["1_2026-10-08"], original["JEH"]["1_2026-10-08"])
        result["KMW"]["unchanged"].append(3)
        self.assertEqual(original["KMW"]["unchanged"], [1, 2])

    def test_pending_buy_or_a_high_close_never_creates_or_removes_holdings(self):
        result = refresh(fills=[], quantity=100)
        self.assertIn("1_2026-10-08", result["JEH"])
        self.assertNotIn("2_2026-10-12", result["JEH"])
        self.assertEqual(result["JEH"]["available_cash"], 10000)
        self.assertNotIn("pending_buy", result["JEH"])

    def test_baseline_day_executions_are_already_in_snapshot(self):
        prior = fill(qty=100, trade_date="2026-10-09", price=999)
        result = refresh(fills=[prior], quantity=100)
        self.assertEqual(result["JEH"]["available_cash"], 10000)
        self.assertEqual(result["JEH"]["broker_sync"]["executions"], {})

    def test_partial_cumulative_buy_changes_only_new_quantity_and_notional(self):
        partial = refresh(fills=[fill(qty=28, price=101)], quantity=128)
        result = refresh(partial, fills=[fill(qty=56, price=102)], quantity=156)
        snapshot = result["JEH"]
        self.assertEqual(snapshot["2_2026-10-12"]["shares"], 56)
        self.assertEqual(snapshot["2_2026-10-12"]["buy_price"], 102)
        self.assertEqual(snapshot["2_2026-10-12"]["amount"], 5712)
        self.assertEqual(snapshot["available_cash"], 4288)
        self.assertEqual(snapshot["compound_trading_days"], 1)
        self.assertEqual(refresh(result, fills=[fill(qty=56, price=102)], quantity=156), result)

    def test_partial_buy_still_cannot_exceed_original_recommendation_after_pending_removed(self):
        partial = refresh(fills=[fill(qty=28)], quantity=128)
        self.assert_blocked("BUY_QUANTITY_MISMATCH", doc=partial,
                            fills=[fill(qty=57)], quantity=157)

    def test_two_broker_orders_cannot_overfill_one_pending_lot(self):
        self.assert_blocked("BUY_QUANTITY_MISMATCH",
                            fills=[fill(qty=30, order_id="one"), fill(qty=30, order_id="two")],
                            quantity=160)

    def test_partial_sell_keeps_lot_cost_and_creates_only_actual_compound_profit(self):
        result = refresh(fills=[fill("SELL", qty=30, price=110,
                         position_key="1_2026-10-08", fees="1.5")], quantity=70)
        snapshot = result["JEH"]
        self.assertEqual(snapshot["1_2026-10-08"]["shares"], 70)
        self.assertEqual(snapshot["1_2026-10-08"]["amount"], 7000)
        self.assertEqual(snapshot["available_cash"], 13298.5)
        self.assertEqual(snapshot["compound_settlements"], [{"trade_date": "2026-10-12",
                           "settlement_date": "2026-10-19", "pnl": 298.5}])
        self.assertEqual(refresh(result, fills=[fill("SELL", qty=30, price=110,
            position_key="1_2026-10-08", fees="1.5")], quantity=70), result)

    def test_final_cumulative_sell_applies_delta_once_then_removes_empty_lot(self):
        first = refresh(fills=[fill("SELL", qty=30, price=110,
                                  position_key="1_2026-10-08")], quantity=70)
        result = refresh(first, fills=[fill("SELL", qty=100, price=112)], quantity=0)
        self.assertNotIn("1_2026-10-08", result["JEH"])
        self.assertEqual(result["JEH"]["available_cash"], 21200)
        self.assertEqual(sum(s["pnl"] for s in result["JEH"]["compound_settlements"]), 1200)
        self.assertEqual(result["JEH"]["cash_snapshot_date"], "2026-10-12")
        self.assertEqual(refresh(result, fills=[fill("SELL", qty=100, price=112)], quantity=0), result)

    def test_unique_exact_lot_can_match_manual_sell_but_ambiguous_sell_blocks(self):
        result = refresh(fills=[fill("SELL", qty=100, price=110)], quantity=0)
        self.assertNotIn("1_2026-10-08", result["JEH"])
        doc = document()
        doc["JEH"]["2_2026-10-07"] = {**doc["JEH"]["1_2026-10-08"], "round": 2}
        self.assert_blocked("SELL_LOT_MISMATCH", doc=doc,
                            fills=[fill("SELL", qty=100, price=110)], quantity=100)

    def test_unresolved_orders_block_without_mutating_input(self):
        doc = document()
        before = copy.deepcopy(doc)
        self.assert_blocked("UNRESOLVED_ORDER", doc=doc, unresolved_orders=[{"state": "UNKNOWN"}])
        self.assertEqual(doc, before)

    def test_actual_holdings_are_required_before_new_checkpoint_is_returned(self):
        self.assert_blocked("HOLDINGS_MISMATCH", fills=[], quantity=156)

    def test_buy_must_match_pending_date_round_quantity_and_strategy(self):
        cases = [(fill(trade_date="2026-10-13"), "2026-10-13", "BUY_LOT_MISMATCH"),
                 (fill(round=3), "2026-10-12", "BUY_LOT_MISMATCH"),
                 (fill(qty=57), "2026-10-12", "BUY_QUANTITY_MISMATCH"),
                 (fill(mode="SF"), "2026-10-12", "BUY_RULES_MISMATCH"),
                 (fill(sell_threshold="3.5"), "2026-10-12", "BUY_RULES_MISMATCH")]
        for record, basis, code in cases:
            with self.subTest(code=code):
                self.assert_blocked(code, fills=[record], basis=basis)

    def test_numeric_string_rules_match_exact_saved_entry_rules(self):
        result = refresh(fills=[fill(sell_threshold="2.8", max_hold_days="6")])
        self.assertEqual(result["JEH"]["2_2026-10-12"]["sell_threshold"], 2.8)

    def test_buy_requires_entry_mode_not_a_silent_default(self):
        doc = document()
        for field in ("mode", *_STRATEGY_FIELDS_FOR_TEST):
            doc["JEH"]["pending_buy"].pop(field, None)
        with self.assertRaises(SnapshotRefreshBlocked) as raised:
            refresh_preset_snapshot(doc, preset="JEH", basis_date="2026-10-12",
                                    fills=[fill()], broker_quantity=156)
        self.assertEqual(raised.exception.code, "BUY_RULES_MISSING")

    def test_cumulative_fill_cannot_regress_or_be_duplicate(self):
        first = refresh(fills=[fill(qty=28)], quantity=128)
        self.assert_blocked("FILL_CHANGED", doc=first, fills=[fill(qty=27)], quantity=127)
        self.assert_blocked("DUPLICATE_FILL", fills=[fill(), fill()])

    def test_fees_are_applied_once_even_when_published_after_quantity(self):
        first = refresh()
        result = refresh(first, fills=[fill(fees=2)])
        self.assertEqual(result["JEH"]["available_cash"], 4342)
        self.assertEqual(refresh(result, fills=[fill(fees=2)]), result)
        self.assertEqual(result["JEH"]["broker_sync"]["executions"]["2026-10-12:123"]["recommended_quantity"], 56)

    def test_seed_deposits_advance_both_compound_bases_once_and_skip_future_seed(self):
        doc = document()
        doc["_preset_configs"]["JEH"]["seed_increases"] = [
            {"date": "2026-10-10", "amount": 200}, {"date": "2026-10-10", "amount": 50},
            {"date": "2026-10-15", "amount": 1000}]
        result = refresh(doc, fills=[], quantity=100)
        self.assertEqual(result["JEH"]["available_cash"], 10250)
        self.assertEqual(result["JEH"]["compound_seed"], 20250)
        self.assertEqual(result["JEH"]["compound_reference_seed"], 20250)
        self.assertEqual(result["JEH"]["processed_seed_dates"], ["2026-10-10"])
        self.assertEqual(refresh(result, fills=[], quantity=100), result)

    def test_unprocessed_seed_before_baseline_is_not_applied_a_second_time(self):
        doc = document()
        doc["_preset_configs"]["JEH"]["seed_increases"] = [{"date": "2026-10-08", "amount": 200}]
        self.assert_blocked("SEED_CHECKPOINT_MISMATCH", doc=doc, fills=[], quantity=100)

    def test_uses_original_settlement_method_for_profit_and_loss_without_double_processing(self):
        doc = document()
        doc["JEH"]["compound_settlements"] = [
            {"trade_date": "2026-10-05", "settlement_date": "2026-10-12", "pnl": 100},
            {"trade_date": "2026-10-05", "settlement_date": "2026-10-12", "pnl": -50}]
        from soxl_quant_system import SOXLQuantTrader
        original = SOXLQuantTrader._process_compounding_for_date
        calls = []
        def recording(state, day):
            calls.append(day.strftime("%Y-%m-%d"))
            return original(state, day)
        with patch.object(SOXLQuantTrader, "_process_compounding_for_date", recording):
            result = refresh(doc, fills=[], quantity=100)
        self.assertEqual(calls, ["2026-10-12"])
        self.assertEqual(result["JEH"]["compound_seed"], 20060)
        self.assertEqual(result["JEH"]["compound_reference_seed"], 20060)
        self.assertEqual(result["JEH"]["compound_settlements"], [])
        self.assertEqual(refresh(result, fills=[], quantity=100), result)

    def test_compound_reference_renews_after_tenth_confirmed_session(self):
        doc = document()
        doc["JEH"]["compound_trading_days"] = 9
        doc["JEH"]["compound_seed"] = 21000
        result = refresh(doc, fills=[], quantity=100)
        self.assertEqual(result["JEH"]["compound_reference_seed"], 21000)
        self.assertEqual(result["JEH"]["compound_trading_days"], 10)

    def test_confirmed_cash_is_comparison_and_not_order_estimate_replacement(self):
        result = refresh(broker_cash="4344.00")
        self.assertEqual(result["JEH"]["available_cash"], 4344)
        self.assert_blocked("CASH_MISMATCH", broker_cash=99999)

    def test_holiday_or_weekend_basis_cannot_create_an_order_day_checkpoint(self):
        for basis in ("2026-10-11", "2026-11-26", "2026-12-25"):
            with self.subTest(basis=basis):
                self.assert_blocked("INVALID_BASIS_DATE", basis=basis, fills=[], quantity=100)

    def test_future_partial_data_does_not_pollute_completed_session(self):
        result = refresh(fills=[fill(trade_date="2026-10-13")], quantity=100)
        self.assertNotIn("2_2026-10-13", result["JEH"])
        self.assertEqual(result["JEH"]["available_cash"], 10000)

    def test_late_correction_requires_reconciliation_and_does_not_reset_cursor(self):
        first = refresh()
        later = refresh(first, fills=[fill()], basis="2026-10-13")
        self.assert_blocked("LATE_EXECUTION", doc=later,
                            fills=[fill(qty=56, price=101, fees=2)], basis="2026-10-13")

    def test_transport_secrets_do_not_enter_failure_or_execution_cursor(self):
        record = fill(extra_secret="do-not-retain-private-token")
        result = refresh(fills=[record])
        self.assertNotIn("extra_secret", next(iter(result["JEH"]["broker_sync"]["executions"].values())))
        record["order_id"] = "secret/invalid-token"
        with self.assertRaises(SnapshotRefreshBlocked) as raised:
            refresh(fills=[record])
        self.assertNotIn("secret/invalid-token", str(raised.exception))


_STRATEGY_FIELDS_FOR_TEST = ("buy_threshold", "sell_threshold", "max_hold_days", "strategy_name")


if __name__ == "__main__":
    unittest.main()
