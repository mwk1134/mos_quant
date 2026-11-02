"""Buy-order sizing policy shared by live signals and backtests.

SSOT:
- Theoretical allocation is still based on reference_seed * round_ratio.
- Executable order size is capped by available live cash.
- If available cash cannot buy at least 1 share at the LOC limit price, no buy
  signal should be emitted.
"""

import math


def calculate_cash_capped_buy_size(allocation_amount, limit_price, available_cash):
    """Return a cash-capped buy sizing payload.

    Args:
        allocation_amount: The strategy's theoretical round allocation.
        limit_price: LOC order limit price used for buying-power calculation.
        available_cash: Cash that can be used for the buy order.
    """
    theoretical_amount = max(0.0, float(allocation_amount or 0.0))
    order_price = float(limit_price or 0.0)
    cash = max(0.0, float(available_cash or 0.0))

    spendable_amount = min(theoretical_amount, cash)
    shares = int(math.floor(spendable_amount / order_price)) if order_price > 0 else 0
    order_amount = float(shares * order_price)
    cash_shortfall = max(0.0, theoretical_amount - cash)

    return {
        "shares": int(shares),
        "theoretical_amount": float(theoretical_amount),
        "available_cash": float(cash),
        "spendable_amount": float(spendable_amount),
        "order_amount": float(order_amount),
        "cash_cap_applied": bool(cash + 1e-9 < theoretical_amount),
        "cash_shortfall": float(cash_shortfall),
    }
