"""Completed MOC sale and same-session LOC buy use actual executed shares."""
from copy import deepcopy
from decimal import Decimal

import pytest

from preset_snapshot_refresh import refresh_preset_snapshot
from preset_snapshot_sync import _attach_lot_identifiers, _open_orders, normalize_confirmed_fills
from datetime import date


def test_friday_moc_sale_and_new_buy_replace_old_lot_and_reconcile_574_shares():
    sf = {"mode": "SF", "buy_threshold": 3.5, "sell_threshold": 1.1,
          "max_hold_days": 35, "strategy_name": "기본"}
    source = {"KMW": {
        "3_2026-11-04": {"round": 3, "shares": 171, "buy_price": 66.68,
                         "amount": 11402.28, **sf, "mode": "AG", "max_hold_days": 7},
        "2_2026-11-12": {"round": 2, "shares": 203, "buy_price": 65.53,
                         "amount": 13302.59, **sf},
        "as_of_date": "2026-11-12", "available_cash": 60000,
        "compound_seed": 90000, "compound_reference_seed": 90000,
        "compound_settlements": [], "compound_trading_days": 0,
        "processed_seed_dates": ["2026-06-21"],
        "broker_sync": {"version": 1, "baseline_date": "2026-11-12", "executions": {}},
        "pending_buy": {"round": 3, "quantity": 371, "order_date": "2026-11-13", **sf}},
        "KMW2": {"preserve": [1, 2]},
        "_preset_configs": {"KMW": {"initial_capital": 90000, "session_start_date": "2026-11-01",
                                      "seed_increases": [], "position_edits": {}}}}
    before = deepcopy(source)
    history = [
        {"iscd": "SOXL", "oder_date": "20261113", "oder_no": "901", "seln_byov_clcd": "1",
         "cntg_dqty": "171", "cntg_crnc_prc": "58.32", "cntg_camt": "9972.72", "aspr_type_code": "7"},
        {"iscd": "SOXL", "oder_date": "20261113", "oder_no": "902", "seln_byov_clcd": "1",
         "cntg_dqty": "0", "cntg_crnc_prc": "0", "cntg_camt": "0", "aspr_type_code": "5"},
        {"iscd": "SOXL", "oder_date": "20261113", "oder_no": "903", "seln_byov_clcd": "2",
         "cntg_dqty": "371", "cntg_crnc_prc": "58.32", "cntg_camt": "21636.72", "aspr_type_code": "5"},
    ]
    fills = _attach_lot_identifiers(normalize_confirmed_fills(history), {
        ("2026-11-13", "901"): {"side": "SELL", "quantity": 171, "position_key": "3_2026-11-04", "round": 3},
        ("2026-11-13", "903"): {"side": "BUY", "quantity": 371, "position_key": "3_2026-11-13", "round": 3}})
    assert len(fills) == 2  # An unfilled 203-share sale cannot reduce holdings.
    old_unfilled = [{"iscd": "SOXL", "oder_date": "20261113", "oder_no": "902", "odqt": "203",
                     "cntg_qty": "0", "cncl_cnfr_qty": "0", "seln_byov_clcd": "1", "aspr_type_code": "5",
                     "ovrs_stck_oder_stat_cls_code": "0"}]
    assert _open_orders(old_unfilled, completed_basis=date(2026, 11, 13)) == []
    result = refresh_preset_snapshot(source, preset="KMW", basis_date="2026-11-13",
        fills=fills, broker_quantity=574, buy_rules={"2026-11-13": sf})
    snapshot = result["KMW"]
    assert "3_2026-11-04" not in snapshot
    assert snapshot["2_2026-11-12"]["shares"] == 203
    assert snapshot["3_2026-11-13"]["shares"] == 371
    assert snapshot["3_2026-11-13"]["buy_price"] == 58.32
    assert snapshot["3_2026-11-13"]["mode"] == "SF"
    assert snapshot["available_cash"] == 48336
    assert snapshot["as_of_date"] == "2026-11-13"
    assert Decimal(str(snapshot["compound_settlements"][0]["pnl"])) == Decimal("-1429.56")
    again = refresh_preset_snapshot(result, preset="KMW", basis_date="2026-11-13",
        fills=fills, broker_quantity=574, buy_rules={"2026-11-13": sf})
    assert again == result
    assert result["KMW2"] == before["KMW2"] and result["_preset_configs"] == before["_preset_configs"]
    assert source == before


@pytest.mark.parametrize('preset,older,recent,purchased,total', [
    ('KMW', 203, 371, 417, 991),
    ('JEH', 81, 147, 166, 394),
    ('JEH2', 9, 18, 20, 47),
    ('KHW', 40, 74, 84, 198),
])
def test_monday_confirmed_buy_appears_as_open_lot_without_losing_previous_holdings(
        preset, older, recent, purchased, total):
    rules = {'mode': 'SF', 'buy_threshold': 3.5, 'sell_threshold': 1.1,
             'max_hold_days': 35, 'strategy_name': '기본'}
    source = {preset: {
        '2_2026-11-12': {'round': 2, 'shares': older, 'buy_price': 65.53,
                         'amount': float(Decimal(older) * Decimal('65.53')), **rules},
        '3_2026-11-13': {'round': 3, 'shares': recent, 'buy_price': 58.32,
                         'amount': float(Decimal(recent) * Decimal('58.32')), **rules},
        'as_of_date': '2026-11-13', 'available_cash': 50000,
        'compound_seed': 90000, 'compound_reference_seed': 90000,
        'compound_settlements': [], 'compound_trading_days': 0,
        'processed_seed_dates': ['2026-06-21', '2026-06-16'],
        'broker_sync': {'version': 1, 'baseline_date': '2026-11-13', 'executions': {}},
        'pending_buy': {'round': 3, 'quantity': purchased, 'order_date': '2026-11-16', **rules}},
        '_preset_configs': {preset: {'initial_capital': 90000, 'session_start_date': '2026-11-01',
                                     'seed_increases': [], 'position_edits': {}}}}
    before = deepcopy(source)
    raw = [{'iscd': 'SOXL', 'oder_date': '20261116', 'oder_no': '904', 'seln_byov_clcd': '2',
            'cntg_dqty': str(purchased), 'cntg_crnc_prc': '48.76',
            'cntg_camt': str(Decimal(purchased) * Decimal('48.76')), 'aspr_type_code': '5'}]
    fills = _attach_lot_identifiers(normalize_confirmed_fills(raw), {
        ('2026-11-16', '904'): {'side': 'BUY', 'quantity': purchased,
                              'position_key': '3_2026-11-16', 'round': 3}})
    updated = refresh_preset_snapshot(source, preset=preset, basis_date='2026-11-16',
        fills=fills, broker_quantity=total, buy_rules={'2026-11-16': rules})
    snapshot = updated[preset]
    assert snapshot['2_2026-11-12'] == before[preset]['2_2026-11-12']
    assert snapshot['3_2026-11-13'] == before[preset]['3_2026-11-13']
    assert snapshot['3_2026-11-16']['shares'] == purchased
    assert snapshot['3_2026-11-16']['buy_price'] == 48.76
    assert sum(lot['shares'] for lot in snapshot.values()
               if isinstance(lot, dict) and 'shares' in lot) == total
    assert snapshot['as_of_date'] == '2026-11-16'
    assert refresh_preset_snapshot(updated, preset=preset, basis_date='2026-11-16',
        fills=fills, broker_quantity=total, buy_rules={'2026-11-16': rules}) == updated
    assert source == before
