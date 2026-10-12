import ast
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from soxl_quant_system import recommendation_buy_order_terms


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class AppStartupCompatibilityTests(unittest.TestCase):
    def test_startup_imports_work_with_already_loaded_strategy_module(self):
        """A deployment must not require a new symbol from a cached module."""
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
        imports = [
            node for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "soxl_quant_system"
        ]
        self.assertTrue(imports)
        stale_strategy = ModuleType("soxl_quant_system")
        stale_strategy.SOXLQuantTrader = object()
        stale_strategy.calculate_cash_limited_order = object()
        namespace = {}

        with patch.dict(sys.modules, {"soxl_quant_system": stale_strategy}):
            exec(
                compile(ast.Module(body=imports, type_ignores=[]), str(APP_PATH), "exec"),
                namespace,
            )

        self.assertIs(namespace["SOXLQuantTrader"], stale_strategy.SOXLQuantTrader)
        self.assertIs(
            namespace["calculate_cash_limited_order"],
            stale_strategy.calculate_cash_limited_order,
        )
        self.assertNotIn("recommendation_buy_order_terms", namespace)

    def test_engine_terms_match_existing_ui_expressions(self):
        """Evaluate only the actual display expressions, without running the app."""
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
        display = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "show_daily_recommendation"
        )
        price_message = next(
            node.args[0] for node in ast.walk(display)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "info"
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
            and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and "매수가:" in value.value
                for value in node.args[0].values
            )
        )
        price_value = next(
            value for value in price_message.values
            if isinstance(value, ast.FormattedValue)
        )
        quantity_value = next(
            node.value for node in ast.walk(display)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "shares"
                for target in node.targets
            )
        )
        price_expression = ast.Expression(body=ast.JoinedStr(values=[price_value]))
        quantity_expression = ast.Expression(body=quantity_value)
        ast.fix_missing_locations(price_expression)
        ast.fix_missing_locations(quantity_expression)
        price_code = compile(price_expression, str(APP_PATH), "eval")
        quantity_code = compile(quantity_expression, str(APP_PATH), "eval")

        for price, budget, expected in (
            (100.005, 1000.05, {"price": "100.00", "quantity": 10}),
            (144.79135430908204, 20655.01588802063,
             {"price": "144.79", "quantity": 142}),
        ):
            with self.subTest(price=price, budget=budget):
                namespace = {
                    "recommendation": {"buy_price": price, "next_buy_amount": budget},
                }
                displayed = {
                    "price": eval(price_code, namespace),
                    "quantity": eval(quantity_code, namespace),
                }
                self.assertEqual(displayed, expected)
                self.assertEqual(recommendation_buy_order_terms(price, budget), displayed)


if __name__ == "__main__":
    unittest.main()
