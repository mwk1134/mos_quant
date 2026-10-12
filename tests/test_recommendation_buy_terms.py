import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from soxl_quant_system import recommendation_buy_order_terms


class RecommendationBuyTermsTests(unittest.TestCase):
    def test_preserves_displayed_float_price_and_raw_price_quantity(self):
        self.assertEqual(
            recommendation_buy_order_terms(100.005, 1000.05),
            {"price": "100.00", "quantity": 10},
        )

    def test_current_kmw_recommendation_is_unchanged(self):
        self.assertEqual(
            recommendation_buy_order_terms(144.79135430908204, 20655.01588802063),
            {"price": "144.79", "quantity": 142},
        )

    def test_budget_below_one_share_returns_zero_quantity(self):
        self.assertEqual(recommendation_buy_order_terms(100.005, 50)["quantity"], 0)
        self.assertEqual(recommendation_buy_order_terms(100.005, 0)["quantity"], 0)

    def test_malformed_nonfinite_and_nonpositive_price_are_rejected(self):
        invalid = [
            (True, 1000), (100, False), ("bad", 1000), (100, None),
            ("NaN", 1000), ("Infinity", 1000), (0, 1000), (-1, 1000),
            (0.001, 1000), (100, "NaN"), (100, "Infinity"), (100, -1),
        ]
        for price, budget in invalid:
            with self.subTest(price=price, budget=budget), self.assertRaises(ValueError):
                recommendation_buy_order_terms(price, budget)

    def test_pending_metadata_matches_the_displayed_order_terms(self):
        # Load just the pure conversion function without starting Streamlit.
        path = Path(__file__).resolve().parents[1] / "app.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_pending_buy_from_recommendation")
        function.returns = None
        for argument in function.args.args:
            argument.annotation = None
        namespace = {"recommendation_buy_order_terms": recommendation_buy_order_terms}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        pending = namespace["_pending_buy_from_recommendation"](
            SimpleNamespace(),
            {"can_buy": True, "next_buy_round": 2, "buy_price": 100.005,
             "next_buy_amount": 1000.05, "basis_date": "2026-10-09",
             "buy_order_date": "2026-10-12"},
        )
        self.assertEqual(pending["quantity"], 10)
        self.assertEqual(pending["target_price"], 100.00)
        self.assertEqual(pending["order_date"], "2026-10-12")


if __name__ == "__main__":
    unittest.main()
