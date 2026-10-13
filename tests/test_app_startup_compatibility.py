import ast
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from soxl_quant_system import SOXLQuantTrader, recommendation_buy_order_terms


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


class SessionState(dict):
    """Only the mapping and attribute behavior used by the real app helpers."""

    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value


def startup_helpers(session_state, **overrides):
    """Execute the app's real helpers without importing or starting Streamlit."""
    names = {"_ensure_current_strategy_class", "configure_app_trader",
             "_upgrade_session_trader", "initialize_trader"}
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    for function in functions:
        function.returns = None
        for node in ast.walk(function):
            if isinstance(node, ast.arg):
                node.annotation = None
    st = SimpleNamespace(session_state=session_state, error=Mock())
    namespace = {
        "SOXLQuantTrader": SOXLQuantTrader,
        "calculate_cash_limited_order": object(), "deepcopy": deepcopy,
        "datetime": datetime, "st": st,
        "APP_COMPOUNDING_ENABLED": True, "APP_PROFIT_COMPOUNDING_RATE": 0.70,
        "APP_LOSS_COMPOUNDING_RATE": 0.20, "APP_COMPOUNDING_SETTLEMENT_DAYS": 7,
        "APP_COMPOUNDING_RENEWAL_DAYS": 10,
        "MA_ALIGNMENT_SWITCH_KEY": "ma_alignment_switch_enabled",
        **overrides,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(APP_PATH), "exec"), namespace)
    return namespace


def reviewed_jeh_snapshot():
    """Frozen relevant fields of the confirmed 2026-10-12 JEH saved portfolio."""
    snapshot = {
        "available_cash": 13587.13958770752,
        "processed_seed_dates": ["2025-12-22", "2026-01-15", "2026-10-04"],
        "as_of_date": "2026-10-12", "compound_seed": 45085.54947540283,
        "compound_reference_seed": 45085.54947540283,
        "compound_profit_rate": 0.7, "compound_loss_rate": 0.2,
        "ma_alignment_switch_enabled": False, "compound_trading_days": 1,
        "compound_settlements": [],
        "broker_sync": {"version": 1, "baseline_date": "2026-10-09", "executions": {
            "2026-10-12:196695": {"trade_date": "2026-10-12", "side": "BUY",
                "quantity": 56, "notional": "7190.4000", "fees": "0",
                "position_key": "6_2026-10-12", "recommended_quantity": 56},
        }},
        "pending_buy": {"round": 7, "quantity": 63, "target_price": 133.02,
                        "order_date": "2026-10-13", "basis_date": "2026-10-12"},
    }
    rows = (
        (1, "2026-10-05", 10, 164.27000427246094),
        (2, "2026-10-06", 35, 164.25999450683594),
        (3, "2026-10-07", 31, 158.91000366210938),
        (4, "2026-10-08", 40, 142.52000427246094),
        (5, "2026-10-09", 45, 139.75999450683594),
        (6, "2026-10-12", 56, 128.4),
    )
    for round_num, date, shares, price in rows:
        snapshot[f"{round_num}_{date}"] = {
            "round": round_num, "shares": shares, "buy_price": price,
            "amount": shares * price, "mode": "AG", "buy_threshold": 3.6,
            "sell_threshold": 3.5, "max_hold_days": 7,
        }
    return snapshot


class SessionDeploymentCompatibilityTests(unittest.TestCase):
    def setUp(self):
        forbidden = Mock(side_effect=AssertionError("Real network access is forbidden"))
        for target in ("requests.Session.request", "requests.get"):
            patcher = patch(target, forbidden)
            patcher.start()
            self.addCleanup(patcher.stop)

    def state(self, trader):
        return SessionState(trader=trader, initial_capital=2793.0,
                            seed_increases=[{"date": "2025-12-22", "amount": 13499.0}],
                            ma_alignment_switch_enabled=True,
                            test_today_override="2026-10-13", active_preset="JEH")

    def trader(self):
        return SOXLQuantTrader(initial_capital=2793.0, auto_update_rsi=False)

    def test_healthy_strategy_module_is_not_reloaded(self):
        namespace = startup_helpers(self.state(None))
        with patch("importlib.reload") as reload_module, patch("importlib.invalidate_caches") as invalidate:
            self.assertIs(namespace["_ensure_current_strategy_class"](), SOXLQuantTrader)
        reload_module.assert_not_called()
        invalidate.assert_not_called()

    def test_cached_predeployment_strategy_module_is_reloaded_once(self):
        stale = ModuleType("soxl_quant_system")
        stale.SOXLQuantTrader = type("OldTrader", (), {})
        stale.calculate_cash_limited_order = object()
        current = ModuleType("soxl_quant_system")
        current.SOXLQuantTrader = SOXLQuantTrader
        current.calculate_cash_limited_order = object()
        namespace = startup_helpers(self.state(None), SOXLQuantTrader=stale.SOXLQuantTrader)
        with patch.dict(sys.modules, {"soxl_quant_system": stale}), \
                patch("importlib.reload", return_value=current) as reload_module, \
                patch("importlib.invalidate_caches") as invalidate:
            self.assertIs(namespace["_ensure_current_strategy_class"](), SOXLQuantTrader)
            self.assertIs(namespace["_ensure_current_strategy_class"](), SOXLQuantTrader)
        reload_module.assert_called_once_with(stale)
        invalidate.assert_called_once_with()
        self.assertIs(namespace["SOXLQuantTrader"], SOXLQuantTrader)
        self.assertIs(namespace["calculate_cash_limited_order"], current.calculate_cash_limited_order)

    def test_reload_without_confirmed_restore_fails_without_legacy_fallback(self):
        stale = ModuleType("soxl_quant_system")
        stale.SOXLQuantTrader = type("OldTrader", (), {})
        namespace = startup_helpers(self.state(None), SOXLQuantTrader=stale.SOXLQuantTrader)
        with patch.dict(sys.modules, {"soxl_quant_system": stale}), \
                patch("importlib.reload", return_value=stale), patch("importlib.invalidate_caches"):
            with self.assertRaises(RuntimeError):
                namespace["_ensure_current_strategy_class"]()
        self.assertIs(namespace["SOXLQuantTrader"], stale.SOXLQuantTrader)

    def test_no_existing_session_trader_is_left_for_normal_initialization(self):
        state = self.state(None)
        namespace = startup_helpers(state)
        self.assertIsNone(namespace["_upgrade_session_trader"]())
        self.assertIsNone(state.trader)

    def test_old_instance_is_replaced_without_losing_user_portfolio_or_settings(self):
        previous = SimpleNamespace(**deepcopy(vars(self.trader())))
        previous.positions = [{"round": 3, "shares": 11, "buy_price": 128.4,
                               "buy_date": datetime(2026, 10, 12), "amount": 1412.4}]
        previous.available_cash = 10000.0
        previous.seed_increases = [{"date": "2025-12-22", "amount": 13499.0}]
        previous.processed_seed_dates = {"2025-12-22"}
        previous.test_today_override = "2026-10-13"
        previous._simulation_cache = {"old": "cached"}
        previous._stock_data_fallbacks = {"SOXL": "old fallback"}
        original_positions = deepcopy(previous.positions)
        state = self.state(previous)
        namespace = startup_helpers(state)
        upgraded = namespace["_upgrade_session_trader"]()
        self.assertIs(type(upgraded), SOXLQuantTrader)
        self.assertIs(state.trader, upgraded)
        self.assertIsNot(upgraded, previous)
        self.assertEqual(upgraded.positions, original_positions)
        self.assertIsNot(upgraded.positions, previous.positions)
        self.assertEqual(upgraded.available_cash, 10000.0)
        self.assertEqual(upgraded.seed_increases, previous.seed_increases)
        self.assertEqual(upgraded.processed_seed_dates, {"2025-12-22"})
        self.assertEqual(upgraded.test_today_override, "2026-10-13")
        self.assertEqual(state.active_preset, "JEH")
        self.assertTrue(upgraded.profit_loss_compounding_enabled)
        self.assertTrue(upgraded.ma_alignment_switch_enabled)
        self.assertEqual(upgraded._simulation_cache, {})
        self.assertEqual(upgraded._stock_data_fallbacks, {})
        self.assertEqual(previous._simulation_cache, {"old": "cached"})
        self.assertEqual(previous.positions, original_positions)

    def test_current_class_with_missing_new_fields_gets_fresh_defaults(self):
        for missing in ("_broker_confirmed_positions", "_broker_sync_metadata"):
            with self.subTest(missing=missing):
                previous = self.trader()
                delattr(previous, missing)
                state = self.state(previous)
                upgraded = startup_helpers(state)["_upgrade_session_trader"]()
                self.assertIsNot(upgraded, previous)
                self.assertIs(type(upgraded), SOXLQuantTrader)
                self.assertFalse(upgraded._broker_confirmed_positions)
                self.assertIsNone(upgraded._broker_sync_metadata)

    def test_current_instance_with_noncallable_restore_is_upgraded(self):
        previous = self.trader()
        previous.restore_real_positions = None
        state = self.state(previous)
        upgraded = startup_helpers(state)["_upgrade_session_trader"]()
        self.assertIsNot(upgraded, previous)
        self.assertTrue(callable(upgraded.restore_real_positions))

    def test_healthy_session_keeps_same_instance_and_cache(self):
        previous = self.trader()
        previous._simulation_cache = {"healthy": "retain"}
        state = self.state(previous)
        with patch.object(previous, "clear_cache") as clear_cache:
            upgraded = startup_helpers(state)["_upgrade_session_trader"]()
        self.assertIs(upgraded, previous)
        self.assertEqual(previous._simulation_cache, {"healthy": "retain"})
        clear_cache.assert_not_called()

    def test_upgraded_session_restores_actual_217_share_jeh_snapshot_without_simulation(self):
        previous = SimpleNamespace(**deepcopy(vars(self.trader())))
        del previous._broker_confirmed_positions
        del previous._broker_sync_metadata
        state = self.state(previous)
        namespace = startup_helpers(state)
        snapshot = reviewed_jeh_snapshot()
        original = deepcopy(snapshot)
        with patch.object(SOXLQuantTrader, "run_backtest", side_effect=AssertionError("Price simulation is forbidden")) as backtest, \
                patch.object(SOXLQuantTrader, "get_stock_data", side_effect=AssertionError("Price queries are forbidden")) as prices:
            upgraded = namespace["_upgrade_session_trader"]()
            result = upgraded.restore_real_positions(snapshot)
        self.assertTrue(result["broker_confirmed"])
        self.assertNotIn("error", result)
        self.assertEqual(result["max_snap_date"], "2026-10-12")
        self.assertEqual([p["shares"] for p in upgraded.positions], [10, 35, 31, 40, 45, 56])
        self.assertEqual(sum(p["shares"] for p in upgraded.positions), 217)
        self.assertEqual(upgraded.available_cash, snapshot["available_cash"])
        self.assertEqual(upgraded.current_round, 7)
        self.assertEqual(upgraded.trading_days_count, 1)
        self.assertIsNone(upgraded._pending_buy_recommendation)
        self.assertEqual(snapshot, original)
        backtest.assert_not_called()
        prices.assert_not_called()

    def test_initialize_trader_actually_upgrades_before_using_existing_session(self):
        previous = SimpleNamespace(**deepcopy(vars(self.trader())))
        del previous._broker_confirmed_positions
        del previous._broker_sync_metadata
        state = self.state(previous)
        namespace = startup_helpers(state)
        with patch.object(SOXLQuantTrader, "check_and_update_rsi_data", side_effect=AssertionError("RSI refresh is forbidden")):
            namespace["initialize_trader"]()
        self.assertIs(type(state.trader), SOXLQuantTrader)
        self.assertIsNot(state.trader, previous)
        self.assertEqual(state.trader.seed_increases[0]["amount"], 13499.0)
        self.assertFalse(state.trader._broker_confirmed_positions)
        namespace["st"].error.assert_not_called()

    def test_initialize_trader_stops_safely_if_upgrade_fails(self):
        state = self.state(self.trader())
        namespace = startup_helpers(state)
        namespace["_upgrade_session_trader"] = Mock(side_effect=RuntimeError("PRIVATE_EXCEPTION_SENTINEL"))
        namespace["initialize_trader"]()
        self.assertIsNone(state.trader)
        namespace["st"].error.assert_called_once()
        self.assertNotIn("PRIVATE_EXCEPTION_SENTINEL", namespace["st"].error.call_args.args[0])

    def test_class_upgrade_runs_before_app_ui_startup(self):
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
        upgrade_call = next(node for node in tree.body
                            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name)
                            and node.value.func.id == "_ensure_current_strategy_class")
        page_setup = next(node for node in tree.body
                          if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                          and isinstance(node.value.func, ast.Attribute)
                          and node.value.func.attr == "set_page_config")
        self.assertLess(upgrade_call.lineno, page_setup.lineno)


if __name__ == "__main__":
    unittest.main()
