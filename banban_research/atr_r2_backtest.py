"""Offline two-sleeve backtest; the existing R2 engine retains its own rules."""

from copy import deepcopy
import math

from .atr_breakout import normalize_atr_breakout_settings
from .atr_breakout_backtest import _build_filter_by_session, _integer_share_count, _normalize_rows
from .atr_sessions import session_open_utc
from .atr_settings import freeze_atr_context, scope_ids
from .backtester import Backtester
from .jsd_backtest_costs import MODEL, validate_slippage
from .monthly_rebalance import R2_STRATEGY_ID, amount, plan_monthly_rebalance, settle_monthly_rebalance


class AtrR2MonthlyBacktester(Backtester):
    def __init__(self, *, rows, market_provider, settings, start_date, end_date,
                 initial_seed=10000, commission_rate=0.05, user_id, account_id, atr_weight_pct=50,
                 buy_slippage_pct=0, sell_slippage_pct=0):
        scope_ids(user_id, account_id)
        if market_provider is None:
            raise ValueError("an explicit historical market provider is required")
        seed = float(initial_seed)
        if not math.isfinite(seed) or seed <= 0:
            raise ValueError("invalid initial_seed")
        self.rate = float(commission_rate) / 100
        if not math.isfinite(self.rate) or not 0 <= self.rate <= 0.05:
            raise ValueError("invalid commission_rate")
        self.owner_user_id, self.owner_account_id = user_id, account_id
        self.slippage = validate_slippage({'buy_slippage_pct': buy_slippage_pct, 'sell_slippage_pct': sell_slippage_pct})
        self.buy_slip, self.sell_slip = self.slippage['buy_slippage_pct'] / 100, self.slippage['sell_slippage_pct'] / 100
        if isinstance(atr_weight_pct, bool) or not math.isfinite(float(atr_weight_pct)) or not 0 <= float(atr_weight_pct) <= 100:
            raise ValueError('invalid ATR weight')
        self.atr_weight_pct = float(atr_weight_pct)
        self.settings, _ = normalize_atr_breakout_settings(settings, strict=True)
        self.settings["settings_version"] = int(settings.get("settings_version", 0))
        self.raw_rows = _normalize_rows(rows)
        self.raw = {row["date"]: row for row in self.raw_rows}
        self.filters = _build_filter_by_session(self.raw_rows, self.settings)
        self.atr_cash, self.atr_position = seed * self.atr_weight_pct / 100, None
        self.atr_log, self.atr_trades, self.rebalance_events = [], [], []
        self._rebalance_month = None
        self._monthly_ledger_revision = 0
        super().__init__(start_date, end_date, initial_seed=seed * (100 - self.atr_weight_pct) / 100,
                         enable_reinvestment=True, cash_flows=[], trading_symbol="SOXL",
                         strategy_id=R2_STRATEGY_ID, market_provider=market_provider)

    def check_buy_signal(self, current_price, previous_close):
        candidate = super().check_buy_signal(current_price, previous_close)
        if candidate is None or not (self.rate + self.buy_slip):
            return candidate
        cash = min(candidate["target_amount"], self.live_cash)
        quantity = math.floor(cash / (candidate["buy_order_price"] * (1 + self.rate + self.buy_slip)) + 1e-10)
        quantity = min(int(candidate["shares"]), quantity)
        return {**candidate, "shares": quantity} if quantity > 0 else None

    def execute_buy(self, buy_signal, current_date):
        super().execute_buy(buy_signal, current_date)
        fee = buy_signal["price"] * buy_signal["shares"] * self.rate
        slip = buy_signal['price'] * buy_signal['shares'] * self.buy_slip
        self.live_cash -= fee + slip
        self.positions[-1]["_entry_cost"] = fee + slip
        self.positions[-1]['_entry_slippage'] = slip

    def execute_sell(self, position, exit_reason, exit_price, current_date):
        fee = position["shares"] * exit_price * self.rate
        slip = position['shares'] * exit_price * self.sell_slip
        entry_fee = position.get("_entry_cost", 0)
        super().execute_sell(position, exit_reason, exit_price, current_date)
        self.live_cash -= fee + slip
        self.trades[-1]["profit_loss"] -= fee + slip + entry_fee
        cost_basis = position['shares'] * position['entry_price'] + entry_fee
        self.trades[-1]['profit_loss_pct'] = self.trades[-1]['profit_loss'] / cost_basis * 100
        self.trades[-1]["live_cash_after"] = self.live_cash
        self.trades[-1]["research_costs"] = fee + slip + entry_fee
        self.trades[-1]['slippage_cost'] = slip + position.get('_entry_slippage', 0)
        self.trades[-1]['effective_entry_price'] = position['entry_price'] * (1 + self.buy_slip)
        self.trades[-1]['effective_exit_price'] = exit_price * (1 - self.sell_slip)
        self.settlements[-1]["profit_loss"] = self.trades[-1]["profit_loss"]

    def _rebalance(self, current_date, open_price):
        positions = []
        originals = {}
        for position in self.positions:
            identity = f"{position['entry_date']}:{position['round']}:{position['mode']}"
            originals[identity] = position
            positions.append({**deepcopy(position), "id": identity, "user_id": self.owner_user_id,
                              "account_id": self.owner_account_id, "round_number": position["round"],
                              "effective_shares": int(position["shares"]), "status": "OPEN"})
        ledger = {"user_id": self.owner_user_id, "account_id": self.owner_account_id,
                  "revision": self._monthly_ledger_revision, "r2_strategy_id": R2_STRATEGY_ID,
                  "atr": {"cash": self.atr_cash, "reference_seed": self.atr_cash, "positions": []},
                  "r2": {"cash": self.live_cash, "reference_seed": self.reference_seed,
                         "seed": self.seed, "positions": positions}}
        plan = plan_monthly_rebalance(ledger, session_date=current_date, open_price=open_price,
            fee_rate=self.rate, atr_weight_pct=self.atr_weight_pct, sell_slippage_rate=self.sell_slip)
        timestamp = session_open_utc(current_date).isoformat()
        fills = [{"user_id": self.owner_user_id, "account_id": self.owner_account_id,
                  "session_date": current_date, "position_id": sale["position_id"],
                  "quantity": sale["quantity"], "price": open_price,
                  "side": "SELL", "type": "MARKET", "symbol": "SOXL",
                  "fee": sale["quantity"] * open_price * self.rate, "filled_at": timestamp,
                  'slippage_cost': str(sale['quantity'] * amount(open_price) * amount(self.sell_slip)),
                  "verification": "VERIFIED", "broker_order_no": "BACKTEST:" + sale["position_id"]}
                 for sale in plan["sales"]]
        updated, audit = settle_monthly_rebalance(plan, ledger, fills=fills,
                                                  valuation_price=open_price, valuation_at=timestamp)
        after_by_id = {position["id"]: position for position in updated["r2"]["positions"]}
        for sale in plan["sales"]:
            position = originals[sale["position_id"]]
            index = self.positions.index(position)
            original = deepcopy(position)
            sold = sale["quantity"]
            position["shares"] = sold
            position["target_amount"] = original.get("target_amount", 0) * sold / original["shares"]
            position["_entry_cost"] = original.get("_entry_cost", 0) * sold / original["shares"]
            position['_entry_slippage'] = original.get('_entry_slippage', 0) * sold / original['shares']
            self.execute_sell(position, "REBALANCE_OPEN", open_price, current_date)
            self.cumulative_realized_pl += self.trades[-1]["profit_loss"]
            remaining = after_by_id[sale["position_id"]]
            if remaining["effective_shares"]:
                original.update(shares=remaining["effective_shares"],
                                target_amount=float(remaining.get("target_amount", 0)),
                                _entry_cost=float(remaining.get("_entry_cost", 0)),
                                _entry_slippage=original.get('_entry_slippage', 0) * remaining['effective_shares'] / original['shares'])
                self.positions.insert(index, original)
        self.atr_cash = float(updated["atr"]["cash"])
        delta = float(audit["transfer_to_r2"])
        self.cash_flows.append({"date": current_date, "amount": delta})
        self._monthly_ledger_revision = updated["revision"]
        self.rebalance_events.append(audit)
        return delta

    def process_cash_flows(self, current_date):
        if not self.start_date <= current_date <= self.end_date:
            return super().process_cash_flows(current_date)
        row = self.raw[current_date]
        opening = row["open"]
        # Overnight ATR exit always precedes the filter and monthly rebalance.
        if self.atr_position is not None:
            position = self.atr_position
            exit_fee = position["shares"] * opening * self.rate
            exit_slippage = position['shares'] * opening * self.sell_slip
            self.atr_cash += position["shares"] * opening - exit_fee - exit_slippage
            self.atr_trades.append({**position, "exit_date": current_date, "exit_price": opening,
                                   "exit_fee": exit_fee, "exit_reason": "NEXT_OPEN",
                                   'exit_slippage_cost': exit_slippage, 'effective_exit_price': opening * (1 - self.sell_slip),
                                   "next_sell_shares": position['shares']})
            self.atr_position = None
        month = current_date[:7]
        if self._rebalance_month is not None and self._rebalance_month != month:
            self._rebalance(current_date, opening)
        self._rebalance_month = month
        additions, withdrawals = super().process_cash_flows(current_date)
        filt = self.filters.get(current_date, {"passed": False})
        if filt.get("passed"):
            trigger = opening * (1 + self.settings["offset_pct"] / 100)
            if row["high"] + 1e-12 >= trigger:
                allocation = min(self.atr_cash, self.atr_cash * self.settings["allocation_pct"] / 100)
                quantity = _integer_share_count(allocation, trigger, self.rate + self.buy_slip)
                if quantity > 0:
                    fee = quantity * trigger * self.rate
                    slip = quantity * trigger * self.buy_slip
                    self.atr_cash -= quantity * trigger + fee + slip
                    self.atr_position = {"entry_date": current_date, "entry_price": trigger,
                                         "shares": quantity, "entry_fee": fee,
                                         'entry_slippage_cost': slip, 'effective_entry_price': trigger * (1 + self.buy_slip),
                                         "user_id": self.owner_user_id, "account_id": self.owner_account_id,
                                         "strategy_id": "atr_breakout_v1", "mode": "ATR", "round_number": 1,
                                         "position_id": f"ATR:{self.owner_user_id}:{self.owner_account_id}:{current_date}",
                                         "target_amount": allocation, "order_price": trigger,
                                         "expected_shares": quantity,
                                         "atr_context": freeze_atr_context(self.settings,
                                             session_date=current_date, basis_date=filt["market_date"],
                                             history_start=self.raw_rows[0]["date"])}
        quantity = self.atr_position["shares"] if self.atr_position else 0
        self.atr_log.append({"date": current_date, "cash": self.atr_cash, "shares": quantity,
                             "account_value": self.atr_cash + quantity * row["close"]})
        return additions, withdrawals

    def run(self):
        report = super().run()
        if [row["date"] for row in report["daily_log"]] != [row["date"] for row in self.atr_log]:
            raise ValueError("mixed backtest session mismatch")
        combined = [{"date": atr["date"], "atr_asset": atr["account_value"],
                     "r2_asset": r2["account_value"], "account_value": atr["account_value"] + r2["account_value"]}
                    for atr, r2 in zip(self.atr_log, report["daily_log"])]
        r2_slippage = sum(t.get('slippage_cost', 0) for t in self.trades) + sum(p.get('_entry_slippage', 0) for p in self.positions)
        r2_fees = sum(max(0, t.get('research_costs', 0) - t.get('slippage_cost', 0)) for t in self.trades)
        r2_fees += sum(max(0, p.get('_entry_cost', 0) - p.get('_entry_slippage', 0)) for p in self.positions)
        atr_slippage = sum(t.get('entry_slippage_cost', 0) + t.get('exit_slippage_cost', 0) for t in self.atr_trades)
        atr_fees = sum(t['entry_fee'] + t['exit_fee'] for t in self.atr_trades)
        if self.atr_position:
            atr_slippage += self.atr_position.get('entry_slippage_cost', 0)
            atr_fees += self.atr_position['entry_fee']
        return {"r2": report, "atr_daily_log": self.atr_log, "atr_trades": self.atr_trades,
                "combined_daily_log": combined, "rebalance_events": self.rebalance_events,
                'execution_cost_totals': {'commission_usd': r2_fees + atr_fees, 'slippage_usd': r2_slippage + atr_slippage},
                "assumptions": {"execution": "FIRST_US_SESSION_OPEN", "commission_rate_pct": self.rate * 100,
                                "slippage": 0 if not (self.buy_slip or self.sell_slip) else dict(self.slippage),
                                **self.slippage, 'slippage_model': MODEL, 'reference_prices_unchanged': True,
                                "initial_atr_pct": self.atr_weight_pct, "initial_r2_pct": 100 - self.atr_weight_pct,
                                "live_execution_enabled": False}}

    def generate_report(self):
        # Monthly partial exits occur before the base engine creates its daily row.
        # Rebuild reporting from the net, fee-inclusive trade ledger, not that row's gross counters.
        by_day = {}
        for trade in self.trades:
            bucket = by_day.setdefault(trade['exit_date'], {'profit': 0.0, 'count': 0})
            bucket['profit'] += trade['profit_loss']
            bucket['count'] += 1
        cumulative = 0.0
        for row in self.daily_log:
            closed = by_day.get(row['date'], {'profit': 0.0, 'count': 0})
            cumulative += closed['profit']
            row.update(daily_realized_pl=closed['profit'], trades_closed_today=closed['count'],
                       cumulative_realized_pl=cumulative)
        self.cumulative_realized_pl = cumulative
        return super().generate_report()
