import unittest

from soxl_quant_system import calculate_cash_limited_order


class CashLimitedOrderTests(unittest.TestCase):
    def test_shortfall_is_target_minus_cash_not_the_cash_balance(self):
        result = calculate_cash_limited_order(
            target_amount=13_722,
            available_cash=13_104,
            buy_price=110.47,
        )

        self.assertEqual(result["cash_shortfall"], 618)
        self.assertEqual(result["possible_shares"], 118)
        self.assertAlmostEqual(result["possible_amount"], 13_035.46)
        self.assertAlmostEqual(result["remaining_cash"], 68.54)

    def test_no_shortfall_when_cash_covers_target(self):
        result = calculate_cash_limited_order(
            target_amount=10_000,
            available_cash=12_000,
            buy_price=101,
        )

        self.assertEqual(result["cash_shortfall"], 0)

    def test_nonpositive_buy_price_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_cash_limited_order(10_000, 9_000, 0)


if __name__ == "__main__":
    unittest.main()
