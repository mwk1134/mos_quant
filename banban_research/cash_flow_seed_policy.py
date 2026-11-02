"""Account cash-flow policy shared by live sizing and backtests."""

from datetime import datetime
from decimal import Decimal, InvalidOperation


CASH_FLOW_SEED_POLICY_EFFECTIVE_DATE_SUFFIX = "cash_flow_seed_policy_effective_date"


def normalize_policy_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def account_policy_setting_key(account_id):
    aid = int(account_id)
    if aid <= 0:
        raise ValueError("account_id must be positive")
    return f"account_config:{aid}:{CASH_FLOW_SEED_POLICY_EFFECTIVE_DATE_SUFFIX}"


def should_apply_cash_flow_to_reference_seed(
    flow_date,
    amount,
    effective_date=None,
    as_of_date=None,
):
    """
    Return whether a cash flow changes seed/reference_seed.

    The corrected default applies every deposit and withdrawal immediately. A
    per-account cutover preserves the historical calculation only while the
    requested date is before the cutover. From the cutover date onward, earlier
    withdrawals are caught up without rewriting historical positions.
    """
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return False

    if amount_value >= 0:
        return True

    cutoff = normalize_policy_date(effective_date)
    if not cutoff:
        return True

    evaluation_date = normalize_policy_date(as_of_date) or normalize_policy_date(flow_date)
    if not evaluation_date:
        return True
    return evaluation_date >= cutoff


def should_catch_up_deferred_withdrawal(
    flow_date,
    amount,
    effective_date=None,
    as_of_date=None,
    base_date=None,
):
    """Return whether a legacy withdrawal omitted by a base row is now due."""
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        return False
    if amount_value >= 0:
        return False

    cutoff = normalize_policy_date(effective_date)
    flow = normalize_policy_date(flow_date)
    evaluation = normalize_policy_date(as_of_date)
    base = normalize_policy_date(base_date)
    if not cutoff or not flow or not evaluation or not base:
        return False

    return flow < cutoff <= evaluation and flow <= base < cutoff


def summarize_cash_flows_after_date(cash_flows, base_date, as_of_date):
    """Summarize valid cash flows not yet represented by a dated daily row."""
    base = normalize_policy_date(base_date)
    through = normalize_policy_date(as_of_date)
    summary = {
        "applied": False,
        "amount": 0.0,
        "count": 0,
        "base_date": str(base or ""),
        "through_date": str(through or ""),
    }
    if not base or not through or through <= base:
        return summary

    total = Decimal("0")
    count = 0
    for flow in list(cash_flows or []):
        if not isinstance(flow, dict):
            continue
        flow_date = normalize_policy_date(flow.get("date"))
        if not flow_date or not (base < flow_date <= through):
            continue
        try:
            amount = Decimal(str(flow.get("amount")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite():
            continue
        total += amount
        count += 1

    summary.update(
        {
            "applied": bool(count),
            "amount": float(total),
            "count": int(count),
        }
    )
    return summary
