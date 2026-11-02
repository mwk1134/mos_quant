"""
Backtester
백테스팅 엔진
"""

import json
import pandas as pd
import pytz
from datetime import datetime, timedelta
from .buy_sizing import calculate_cash_capped_buy_size
from .cash_flow_seed_policy import normalize_policy_date, should_apply_cash_flow_to_reference_seed
from .rsi_calculator import calculate_rsi
from .mode_manager import determine_mode, get_mode_config, get_mode_trigger_reason
from .config import TRADING_SYMBOL, INDICATOR_SYMBOL, RSI_PERIOD
from .market_data_provider import MarketDataProvider
from .strategy_presets import DEFAULT_STRATEGY_ID, get_strategy_context


class Backtester:
    def __init__(
        self,
        start_date,
        end_date,
        initial_seed=10000,
        enable_reinvestment=True,
        cash_flows=None,
        trading_symbol=TRADING_SYMBOL,
        strategy_id=DEFAULT_STRATEGY_ID,
        custom_params=None,
        strategy_context=None,
        strategy_timeline=None,
        cash_flow_seed_policy_effective_date=None,
        market_provider=None,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_seed = initial_seed
        print("🚀 [Backtester] Ver 5.1: Cash Flow & Attribute Fix Active")
        self.enable_reinvestment = enable_reinvestment  # 재투자 활성화 여부
        self.cash_flows = cash_flows if cash_flows else []
        self.cash_flow_seed_policy_effective_date = normalize_policy_date(
            cash_flow_seed_policy_effective_date
        )
        self.cash_flow_policy_events = []
        self.deferred_reference_seed_withdrawals = []

        # 날짜 형식 정규화 (YYYY-MM-DD) 및 정렬
        for cf in self.cash_flows:
            cf['date'] = cf['date'].replace('.', '-').replace(' ', '')
        self.cash_flows.sort(key=lambda x: x['date'])

        # 상태 변수
        self.seed = initial_seed
        self.positions = []  # [{round, mode, entry_date, entry_price, shares}, ...]
        self.trades = []  # 완료된 거래
        self.settlements = []  # 대기 중인 정산
        self.seed_history = []  # 일일 시드 기록
        self.mode_history = []  # 모드 변경 기록
        self.current_mode = 'SF'  # 기본 안전 모드
        self.daily_log = []  # 일별 거래 로그
        self.cumulative_realized_pl = 0  # 누적 실현 손익
        self.peak_value = initial_seed  # MDD 계산을 위한 최고점
        self.max_drawdown = 0  # 최대 낙폭

        # 전략 컨텍스트 SSOT
        if strategy_context is None:
            self.strategy_context = get_strategy_context(strategy_id=strategy_id, custom_params=custom_params)
        else:
            self.strategy_context = dict(strategy_context)

        self.strategy_id = str(self.strategy_context.get("strategy_id") or strategy_id or DEFAULT_STRATEGY_ID)
        self.strategy_timeline = self._normalize_strategy_timeline(strategy_timeline)
        self.active_strategy_id = str(self.strategy_id)
        self.active_strategy_context = dict(self.strategy_context)
        self.reference_seed_renewal_days = 10
        self.settlement_delay_days = 7
        self.profit_reflection_rate = 0.0
        self.loss_reflection_rate = 0.0
        self._set_strategy_state_for_date(self.start_date)

        # 신규 추가 상태
        self.reference_seed = initial_seed  # 고정 시드(갱신 주기는 strategy_context 기준)
        self.reference_seed_date = None  # 현금흐름과 무관한 실제 N거래일 갱신일
        self.trading_day_count = 0  # 거래일 카운터
        self.current_season = 0  # 현재 시즌
        self.season_log = []  # 시즌 히스토리 (시작일, 종료일)
        self.is_in_season = False
        self.round_in_season = 0  # 시즌 내 현재 회차
        self.live_cash = initial_seed  # 리포트용 실시간 예수금 (매수/매도 시 즉시 반영)
        self.total_invested_capital = initial_seed  # 총 투자 원금 (수익률 계산용)
        self.cash_flow_idx = 0  # 현재 처리할 현금 흐름 인덱스
        self.market_provider = market_provider if market_provider is not None else MarketDataProvider()
        self.data_warnings = []
        self.trading_symbol = str(trading_symbol).upper()

    def _normalize_date_text(self, value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            return None

    def _normalize_strategy_timeline(self, strategy_timeline):
        if not isinstance(strategy_timeline, list):
            return [
                {
                    "start_date": str(self.start_date),
                    "strategy_id": str(self.strategy_id),
                    "strategy_context": dict(self.strategy_context),
                }
            ]

        timeline = []
        for row in strategy_timeline:
            if not isinstance(row, dict):
                continue
            start_date = self._normalize_date_text(row.get("start_date"))
            if not start_date:
                continue
            row_context = row.get("strategy_context")
            if not isinstance(row_context, dict):
                sid = str(row.get("strategy_id") or self.strategy_id or DEFAULT_STRATEGY_ID)
                row_context = get_strategy_context(strategy_id=sid)
            row_strategy_id = str(row_context.get("strategy_id") or row.get("strategy_id") or self.strategy_id or DEFAULT_STRATEGY_ID)
            timeline.append(
                {
                    "start_date": str(start_date),
                    "strategy_id": str(row_strategy_id),
                    "strategy_context": dict(row_context),
                }
            )

        if not timeline:
            timeline = [
                {
                    "start_date": str(self.start_date),
                    "strategy_id": str(self.strategy_id),
                    "strategy_context": dict(self.strategy_context),
                }
            ]

        timeline.sort(key=lambda x: str(x.get("start_date") or ""))
        merged = []
        for row in timeline:
            sid = str(row.get("strategy_id") or "")
            context = dict(row.get("strategy_context") or {})
            signature = json.dumps(
                {
                    "strategy_id": str(sid),
                    "strategy_context": context,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if merged and signature == str(merged[-1].get("_signature") or ""):
                continue
            merged.append(
                {
                    "start_date": str(row.get("start_date")),
                    "strategy_id": str(sid),
                    "strategy_context": context,
                    "_signature": signature,
                }
            )
        return [
            {
                "start_date": str(item.get("start_date") or ""),
                "strategy_id": str(item.get("strategy_id") or ""),
                "strategy_context": dict(item.get("strategy_context") or {}),
            }
            for item in merged
        ]

    def _select_strategy_context_for_date(self, current_date):
        current = str(current_date or "")
        selected = None
        for row in self.strategy_timeline:
            start_date = str(row.get("start_date") or "")
            if start_date and start_date <= current:
                selected = row
            elif start_date > current:
                break
        if selected is None:
            selected = self.strategy_timeline[0]
        return dict(selected.get("strategy_context") or self.strategy_context)

    def _is_soxl_ma_aligned_before_date(self, current_date):
        """전일 확정 종가 기준 5MA > 20MA > 60MA 정배열 여부."""
        data = getattr(self, "soxl_data", None)
        if data is None or getattr(data, "empty", True):
            return False
        try:
            current_ts = pd.Timestamp(str(current_date))
            history = data[data.index < current_ts]
            closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
        except Exception:
            return False
        if len(closes) < 60:
            return False

        ma5 = float(closes.tail(5).mean())
        ma20 = float(closes.tail(20).mean())
        ma60 = float(closes.tail(60).mean())
        return bool(ma5 > ma20 > ma60)

    def _set_strategy_state_for_date(self, current_date):
        context = self._select_strategy_context_for_date(current_date)
        if bool(context.get("ma_alignment_required")) and not self._is_soxl_ma_aligned_before_date(current_date):
            context = get_strategy_context(strategy_id=DEFAULT_STRATEGY_ID)
        self.strategy_context = dict(context)
        self.active_strategy_context = dict(context)
        self.active_strategy_id = str(context.get("strategy_id") or self.strategy_id or DEFAULT_STRATEGY_ID)

        self.reference_seed_renewal_days = int(context.get("reference_seed_renewal_days") or 10)
        if self.reference_seed_renewal_days <= 0:
            self.reference_seed_renewal_days = 10
        self.settlement_delay_days = int(context.get("settlement_delay_days") or 7)
        if self.settlement_delay_days < 0:
            self.settlement_delay_days = 7
        self.profit_reflection_rate = float(context.get("profit_reflection_rate") or 0.0)
        self.loss_reflection_rate = float(context.get("loss_reflection_rate") or 0.0)

    def _get_position_strategy_context(self, position):
        raw = (position or {}).get("strategy_context")
        if isinstance(raw, dict):
            return dict(raw)
        sid = str((position or {}).get("strategy_id") or self.strategy_id or DEFAULT_STRATEGY_ID)
        if sid == self.active_strategy_id:
            return dict(self.active_strategy_context)
        entry_date = self._normalize_date_text((position or {}).get("entry_date"))
        if entry_date:
            entry_ctx = self._select_strategy_context_for_date(entry_date)
            entry_sid = str(entry_ctx.get("strategy_id") or DEFAULT_STRATEGY_ID)
            if entry_sid == sid:
                return dict(entry_ctx)
        return get_strategy_context(strategy_id=sid)

    def _get_settlement_strategy_context(self, settlement):
        raw = (settlement or {}).get("strategy_context")
        if isinstance(raw, dict):
            return dict(raw)
        sid = str((settlement or {}).get("strategy_id") or self.strategy_id or DEFAULT_STRATEGY_ID)
        if sid == self.active_strategy_id:
            return dict(self.active_strategy_context)
        trade_date = self._normalize_date_text((settlement or {}).get("trade_date"))
        if trade_date:
            trade_ctx = self._select_strategy_context_for_date(trade_date)
            trade_sid = str(trade_ctx.get("strategy_id") or DEFAULT_STRATEGY_ID)
            if trade_sid == sid:
                return dict(trade_ctx)
        return get_strategy_context(strategy_id=sid)

    def get_market_data(self):
        """시장 데이터 다운로드"""
        # 날짜 포맷 견고하게 처리
        try:
            start_dt = pd.to_datetime(self.start_date.replace('.', '-'))
            end_dt = pd.to_datetime(self.end_date.replace('.', '-'))

            # 파싱된 날짜로 재설정 (내부 일관성 위해)
            self.start_date = start_dt.strftime('%Y-%m-%d')
            self.end_date = end_dt.strftime('%Y-%m-%d')
        except Exception as e:
            print(f"날짜 파싱 실패: {e}. 기본 포맷 시도.")

        print(f"시장 데이터 다운로드 중... ({self.start_date} ~ {self.end_date})")

        # 충분한 RSI 계산을 위해 더 이전 데이터 필요
        start_date_obj = datetime.strptime(self.start_date, '%Y-%m-%d')
        extended_start = (start_date_obj - timedelta(days=180)).strftime('%Y-%m-%d')

        # yfinance의 end 파라미터는 exclusive이므로 1일 추가
        end_date_obj = datetime.strptime(self.end_date, '%Y-%m-%d')
        end_date_inclusive = (end_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')

        soxl_result = self.market_provider.get_daily_bars(
            symbol=self.trading_symbol,
            start_date=extended_start,
            end_date=self.end_date,
        )
        self.soxl_data = soxl_result["dataframe"]
        self.data_warnings.extend(soxl_result.get("warnings", []))

        # EOD SSOT 보정: market_eod에 저장된 날짜/종가가 있으면 일봉 Close를 덮어쓴다.
        # (브로커 체결 기준 수동 보정값 포함)
        try:
            eod_rows = self.market_provider.db.get_market_eod_rows(
                symbol=self.trading_symbol,
                start_date=extended_start,
                end_date=self.end_date,
            )
            override_count = 0
            appended_count = 0
            for row in eod_rows:
                date_str = str(row.get("date") or "")
                close_value = float(row.get("close") or 0)
                if not date_str or close_value <= 0:
                    continue
                ts = pd.to_datetime(date_str)
                if ts in self.soxl_data.index:
                    self.soxl_data.at[ts, "Close"] = float(close_value)
                    override_count += 1
                else:
                    self.soxl_data.loc[ts, ["Open", "High", "Low", "Close", "Volume"]] = [
                        float(close_value),
                        float(close_value),
                        float(close_value),
                        float(close_value),
                        0.0,
                    ]
                    appended_count += 1
            if appended_count > 0:
                self.soxl_data = self.soxl_data.sort_index()
                self.data_warnings.append(f"{self.trading_symbol} market_eod_appended={appended_count}")
            if override_count > 0:
                self.data_warnings.append(f"{self.trading_symbol} market_eod_overrides={override_count}")
        except Exception as e:
            self.data_warnings.append(f"{self.trading_symbol} market_eod_override_failed:{e}")

        qqq_result = self.market_provider.get_daily_bars(
            symbol=INDICATOR_SYMBOL,
            start_date=extended_start,
            end_date=self.end_date,
        )
        self.qqq_data = qqq_result["dataframe"]
        self.data_warnings.extend(qqq_result.get("warnings", []))

        # 주봉으로 리샘플링 (금요일 종가 기준)
        self.qqq_weekly = self.qqq_data.resample('W-FRI').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()

        # 주봉 RSI 계산
        self.qqq_weekly['RSI'] = calculate_rsi(self.qqq_weekly, window=RSI_PERIOD)

        print(f"✓ SOXL 데이터: {len(self.soxl_data)} 일")
        print(f"✓ QQQ 주봉 데이터: {len(self.qqq_weekly)} 주")

    def _build_benchmark_series(self):
        series = []
        for idx, row in self.soxl_data.iterrows():
            date_str = idx.strftime("%Y-%m-%d")
            if date_str < self.start_date or date_str > self.end_date:
                continue
            series.append({"date": date_str, "close": float(row.get("Close", 0) or 0)})
        return {"symbol": self.trading_symbol, "series": series}

    def update_mode(self, current_date):
        """주봉 RSI 기반 모드 업데이트 (성능 최적화: searchsorted 사용)"""
        # 현재 날짜
        current_dt = pd.Timestamp(current_date)

        # 타임존 맞추기 (QQQ 데이터가 timezone aware인 경우)
        if self.qqq_weekly.index.tz is not None:
            if current_dt.tz is None:
                current_dt = current_dt.tz_localize(self.qqq_weekly.index.tz)

        # searchsorted를 사용하여 현재 날짜가 삽입될 위치(즉, 현재 날짜보다 작은 데이터의 개수)를 찾음
        # side='left': current_dt와 같은 날짜가 있다면 그 인덱스를 반환 -> 슬라이싱 시 포함되지 않음 (미래 참조 방지)
        idx = self.qqq_weekly.index.searchsorted(current_dt)

        # 데이터가 최소 2개(지난주, 지지난주) 있어야 함
        if idx < 2:
            return

        # Week N-1 (지난주), Week N-2 (지지난주)
        # iloc 접근은 O(1)
        rsi_t1 = self.qqq_weekly.iloc[idx-1]['RSI']
        rsi_t2 = self.qqq_weekly.iloc[idx-2]['RSI']

        if pd.isna(rsi_t1) or pd.isna(rsi_t2):
            return

        # 모드 결정
        new_mode = determine_mode(rsi_t1, rsi_t2, self.current_mode)
        trigger_reason = get_mode_trigger_reason(rsi_t1, rsi_t2, new_mode)

        if new_mode != self.current_mode:
            self.mode_history.append({
                'date': current_date,
                'mode': new_mode,
                'rsi_t1': float(rsi_t1),
                'rsi_t2': float(rsi_t2),
                'trigger_reason': trigger_reason
            })
            self.current_mode = new_mode

    def get_next_round(self):
        """다음 사용 가능한 회차 (사용자 규칙: 현재 보유 개수 + 1)"""
        config = get_mode_config(self.current_mode, strategy_context=self.strategy_context)
        total_rounds = len(config['rounds'])

        # 사용자 규칙: 현재 보유 중인 포지션 덩어리 개수 + 1
        active_count = len(self.positions)
        next_round = active_count + 1

        # print(f"🔍 [get_next_round] Date: ???, Active: {active_count}, Result: {next_round}")

        if next_round <= total_rounds:
            return next_round
        return None

    def process_settlements(self, current_date):
        """정산 처리"""
        current_dt = datetime.strptime(current_date, '%Y-%m-%d')

        for settlement in self.settlements[:]:
            settlement_dt = datetime.strptime(settlement['settlement_date'], '%Y-%m-%d')

            if current_dt >= settlement_dt:
                profit_loss = settlement['profit_loss']
                settlement_ctx = self._get_settlement_strategy_context(settlement)
                profit_reflection_rate = float(settlement_ctx.get("profit_reflection_rate") or self.profit_reflection_rate)
                loss_reflection_rate = float(settlement_ctx.get("loss_reflection_rate") or self.loss_reflection_rate)

                # 재투자가 활성화된 경우에만 시드에 반영
                if self.enable_reinvestment:
                    if profit_loss > 0:
                        seed_change = profit_loss * profit_reflection_rate
                    else:
                        seed_change = profit_loss * loss_reflection_rate

                    self.seed += seed_change

                self.settlements.remove(settlement)

    def check_buy_signal(self, current_price, previous_close):
        """매수 신호 확인"""
        config = get_mode_config(self.current_mode, strategy_context=self.strategy_context)

        # 매수 주문가 계산 (전일 종가 * threshold)
        buy_order_price = previous_close * config['buy_threshold']

        # 구글 시트 로직 적용: 종가(체결가)가 매수주문가보다 낮을 때만 체결
        if current_price > buy_order_price:
            return None

        next_round = self.get_next_round()
        if next_round is None:
            return None

        # 포지션 크기 계산 (10일 주기 고정 시드 기준)
        round_ratio = config['rounds'][next_round - 1]
        buy_amount = self.reference_seed * round_ratio

        sizing = calculate_cash_capped_buy_size(
            allocation_amount=buy_amount,
            limit_price=buy_order_price,
            available_cash=self.live_cash,
        )
        shares = int(sizing["shares"])

        if shares == 0:
            return None

        return {
            'round': next_round,
            'shares': shares,
            'price': current_price,
            'buy_order_price': buy_order_price,  # 추가
            'target_amount': buy_amount,
            'amount': sizing["spendable_amount"],
            'order_amount': sizing["order_amount"],
            'available_cash': sizing["available_cash"],
            'cash_cap_applied': sizing["cash_cap_applied"],
            'cash_shortfall': sizing["cash_shortfall"],
        }

    def check_sell_signals(self, current_price, previous_close, current_date):
        """매도 신호 확인 (사용자 규칙: 진입가 기반 수익 달성 시)"""
        _ = previous_close
        to_sell = []

        for position in self.positions:
            pos_ctx = self._get_position_strategy_context(position)
            config = get_mode_config(position['mode'], strategy_context=pos_ctx)

            # 영업일 기준 보유일 계산 (거래일만 카운트)
            entry_date = position['entry_date']
            entry_idx = self.soxl_data.index.get_loc(entry_date)
            current_idx = self.soxl_data.index.get_loc(current_date)
            holding_days = current_idx - entry_idx

            # LOC 매도 (사용자 규칙: 진입가 대비 목표 수익 달성 시)
            sell_target = position['entry_price'] * config['sell_threshold']

            if current_price >= sell_target:
                # LOC 매도: 당일 종가로 매도 (목표가 도달 시)
                to_sell.append((position, 'LOC', current_price))
            # MOC 매도 (보유 기간 초과 시)
            elif holding_days >= config['max_holding_days']:
                # MOC 매도: 당일 종가로 매도
                to_sell.append((position, 'MOC', current_price))

        return to_sell

    def execute_buy(self, buy_signal, current_date):
        """매수 실행"""
        cost = buy_signal['price'] * buy_signal['shares']
        self.live_cash -= cost
        position_ctx = dict(self.active_strategy_context)

        self.positions.append({
            'round': buy_signal['round'],
            'mode': self.current_mode,
            'strategy_id': str(position_ctx.get("strategy_id") or self.active_strategy_id or self.strategy_id),
            'strategy_context': position_ctx,
            'entry_date': current_date,
            'entry_price': buy_signal['price'],
            'shares': buy_signal['shares'],
            'target_amount': buy_signal['target_amount']  # 추가
        })

    def execute_sell(self, position, exit_reason, exit_price, current_date):
        """매도 실행"""
        # 보유일 계산 (영업일 기준)
        entry_idx = self.soxl_data.index.get_loc(position['entry_date'])
        exit_idx = self.soxl_data.index.get_loc(current_date)
        holding_days = exit_idx - entry_idx

        exit_dt = datetime.strptime(current_date, '%Y-%m-%d')

        proceeds = exit_price * position['shares']
        self.live_cash += proceeds

        profit_loss = (exit_price - position['entry_price']) * position['shares']
        profit_loss_pct = ((exit_price - position['entry_price']) / position['entry_price']) * 100

        # 거래 기록
        self.trades.append({
            'round': position['round'],
            'mode': position['mode'],
            'strategy_id': str(position.get('strategy_id') or self.strategy_id),
            'strategy_context': self._get_position_strategy_context(position),
            'entry_date': position['entry_date'],
            'entry_price': position['entry_price'],
            'exit_date': current_date,
            'exit_price': exit_price,
            'shares': position['shares'],
            'profit_loss': profit_loss,
            'profit_loss_pct': profit_loss_pct,
            'exit_reason': exit_reason,
            'holding_days': holding_days,
            'target_amount': position.get('target_amount', 0),  # 추가
            'live_cash_after': self.live_cash # 추가
        })

        # T+10 정산 추가 (사용자 엑셀과 일치시키기 위해 10일 후로 계산)
        exit_dt = datetime.strptime(current_date, '%Y-%m-%d')
        position_ctx = self._get_position_strategy_context(position)
        settlement_delay_days = int(position_ctx.get("settlement_delay_days") or self.settlement_delay_days or 7)
        if settlement_delay_days < 0:
            settlement_delay_days = int(self.settlement_delay_days or 7)
        settlement_dt = exit_dt + timedelta(days=settlement_delay_days)
        self.settlements.append({
            'profit_loss': profit_loss,
            'trade_date': current_date,
            'settlement_date': settlement_dt.strftime('%Y-%m-%d'),
            'strategy_id': str(position.get('strategy_id') or self.strategy_id),
            'strategy_context': position_ctx,
        })

        # 포지션 제거
        self.positions.remove(position)

    def process_cash_flows(self, current_date):
        """현금 흐름 (증액/인출) 처리"""
        daily_added = 0
        daily_withdrawn = 0

        while self.cash_flow_idx < len(self.cash_flows):
            flow = self.cash_flows[self.cash_flow_idx]
            if flow['date'] <= current_date:
                amount = float(flow['amount'])
                apply_to_reference_seed = should_apply_cash_flow_to_reference_seed(
                    flow_date=flow.get('date'),
                    amount=amount,
                    effective_date=self.cash_flow_seed_policy_effective_date,
                    as_of_date=current_date,
                )
                seed_before = float(self.seed)
                reference_seed_before = float(self.reference_seed)
                self.live_cash += amount
                self.total_invested_capital += amount
                if apply_to_reference_seed:
                    self.seed += amount
                    self.reference_seed += amount
                elif amount < 0:
                    self.deferred_reference_seed_withdrawals.append(
                        {'flow_date': str(flow.get('date') or ''), 'amount': float(amount)}
                    )

                self.cash_flow_policy_events.append(
                    {
                        'flow_date': str(flow.get('date') or ''),
                        'processed_date': str(current_date),
                        'amount': float(amount),
                        'reference_seed_applied': bool(apply_to_reference_seed),
                        'policy_effective_date': self.cash_flow_seed_policy_effective_date,
                        'seed_before': float(seed_before),
                        'seed_after': float(self.seed),
                        'reference_seed_before': float(reference_seed_before),
                        'reference_seed_after': float(self.reference_seed),
                        'event_type': 'cash_flow',
                    }
                )

                if amount >= 0:
                    daily_added += amount
                else:
                    daily_withdrawn += abs(amount)

                # print(f"💰 현금 흐름 발생 ({current_date}): {amount:+,} (시드: {self.seed:,.2f})")
                self.cash_flow_idx += 1
            else:
                break

        if (
            self.cash_flow_seed_policy_effective_date
            and str(current_date) >= self.cash_flow_seed_policy_effective_date
            and self.deferred_reference_seed_withdrawals
        ):
            catchup_amount = float(
                sum(float(item.get('amount') or 0.0) for item in self.deferred_reference_seed_withdrawals)
            )
            seed_before = float(self.seed)
            reference_seed_before = float(self.reference_seed)
            self.seed += catchup_amount
            self.reference_seed += catchup_amount
            self.cash_flow_policy_events.append(
                {
                    'flow_date': self.cash_flow_seed_policy_effective_date,
                    'processed_date': str(current_date),
                    'amount': catchup_amount,
                    'reference_seed_applied': True,
                    'policy_effective_date': self.cash_flow_seed_policy_effective_date,
                    'seed_before': seed_before,
                    'seed_after': float(self.seed),
                    'reference_seed_before': reference_seed_before,
                    'reference_seed_after': float(self.reference_seed),
                    'event_type': 'cutover_catchup',
                    'source_flow_dates': [
                        str(item.get('flow_date') or '')
                        for item in self.deferred_reference_seed_withdrawals
                    ],
                }
            )
            self.deferred_reference_seed_withdrawals = []
        return daily_added, daily_withdrawn
    def run(self):
        """백테스트 실행기"""
        self.get_market_data()
        if self.soxl_data.empty:
            raise ValueError("SOXL 일봉 데이터를 불러오지 못했습니다. 네트워크 또는 캐시 상태를 확인하세요.")

        print(f"\n백테스트 시작: {self.start_date} ~ {self.end_date}")
        print(f"초기 시드: ${self.initial_seed:,.2f}")
        print("=" * 60)

        # 루프 최적화: 현재 미국 시간 미리 계산
        us_eastern = pytz.timezone('America/New_York')
        now_us = datetime.now(us_eastern)
        today_us = now_us.strftime('%Y-%m-%d')
        current_hour_us = now_us.hour

        # 전체 데이터 사용 (이전 종가 계산을 위해)
        for i in range(len(self.soxl_data)):
            current_date = self.soxl_data.index[i].strftime('%Y-%m-%d')
            self._set_strategy_state_for_date(current_date)

            # 1. 현금 흐름 처리 (증액/인출)
            added_today, withdrawn_today = self.process_cash_flows(current_date)
            # 2. 정산 처리 (장 시작 전 오전 개념: 오늘 정산금을 오늘부터 바로 사용 가능)
            self.process_settlements(current_date)

            # start_date 이전이라도 모드 상태는 추적해야 함 (RSI 계승을 위해)
            if current_date < self.start_date:
                self.update_mode(current_date)
                continue

            # end_date 이후는 종료
            if current_date > self.end_date:
                break

            # 3. 전략 정의 기준 시드 갱신 체크 (기본 10거래일 주기)
            self.trading_day_count += 1
            is_renewal = False
            # 사용자 규칙: 1일차(초기화), N일차, 2N일차... 에 시드 증액 반영
            if self.trading_day_count == 1 or self.trading_day_count % int(self.reference_seed_renewal_days) == 0:
                self.reference_seed = self.seed
                self.reference_seed_date = current_date
                is_renewal = True

            current_price = self.soxl_data.iloc[i]['Close']
            # 이전 종가 (i=0 이어도 extended_start 덕분에 존재함)
            previous_close = self.soxl_data.iloc[i-1]['Close']

            # 장 미마감 세션 체크 (실행 유보용)
            is_live_session = False  # Wrapper accepts completed historical sessions only.

            # 시즌 종료 체크 (모든 포지션 청산 시)
            if self.is_in_season and len(self.positions) == 0:
                self.is_in_season = False

            # 당일 거래 로그 초기화
            daily_record = {
                'date': current_date,
                'close_price': current_price,
                'mode': self.current_mode,
                'strategy_id': str(self.active_strategy_id),
                'rsi_t1': None,
                'rsi_t2': None,
                'buy_order_price': None,
                'buy_executed': False,
                'active_positions_count': len(self.positions),
                'sell_targets': [],
                'stop_loss_dates': [],
                'trades_closed_today': 0,
                'daily_realized_pl': 0,
                'cumulative_realized_pl': self.cumulative_realized_pl,
                'seed': self.seed, # Reference Seed (buying power)
                'live_cash': self.live_cash, # 실제 예수금 (이미지 AS열)
                'reference_seed': self.reference_seed, # 10일 주기 고정 시드
                'seed_date': self.reference_seed_date,
                'is_renewal': is_renewal, # 갱신 여부
                'account_value': 0, # 총자산 (이미지 AT열)
                'equity_value': 0, # 평가금 (이미지 AR열)
                'drawdown': 0,
                'mdd': 0,
                'season': self.current_season, # 추가
                'cash_added': added_today,    # 신규: 시드 증액
                'cash_withdrawn': withdrawn_today # 신규: 인출
            }

            # RSI 값 가져오기
            period = pd.Timestamp(current_date).to_period('W-FRI')
            current_week = period.end_time
            if self.qqq_weekly.index.tz is not None:
                current_week = current_week.tz_localize(self.qqq_weekly.index.tz)

            weekly_data = self.qqq_weekly[self.qqq_weekly.index <= current_week]
            if len(weekly_data) >= 2:
                daily_record['rsi_t1'] = weekly_data.iloc[-1]['RSI']
                daily_record['rsi_t2'] = weekly_data.iloc[-2]['RSI']

            # 모드 업데이트
            self.update_mode(current_date)
            daily_record['mode'] = self.current_mode

            # 매도 목표가 및 손절일 기록 (각 포지션의 모드 기준)
            for position in self.positions:
                pos_ctx = self._get_position_strategy_context(position)
                pos_config = get_mode_config(position['mode'], strategy_context=pos_ctx)
                sell_target = position['entry_price'] * pos_config['sell_threshold']
                from .trading_days import get_trading_date_after_n_days
                stop_loss_date = get_trading_date_after_n_days(
                    position['entry_date'],
                    pos_config['max_holding_days'],
                    market_data=self.soxl_data
                )
                daily_record['sell_targets'].append({'round': position['round'], 'target_price': sell_target})
                daily_record['stop_loss_dates'].append(stop_loss_date if stop_loss_date else "N/A")

            # 매도/매수 신호 확인 및 실행
            # 1. 매수 신호 확인 (장 시작 전/중 주문을 미리 걸어두는 개념)
            # 아직 매도가 일어나기 전의 보유 수량 기준으로 '몇 회차'를 살지 결정함.
            buy_signal = self.check_buy_signal(current_price, previous_close)

            # 리포트용 '회차' 기록: 오늘 주문할(한) 회차 번호
            # 매수 신호가 있으면 그 회차를, 없으면 살 수 있는 다음 회차를 표시
            daily_record['round'] = buy_signal['round'] if buy_signal else self.get_next_round()

            # 2. 매도 신호 확인
            sell_signals = self.check_sell_signals(current_price, previous_close, current_date)

            # 3. 매매 실행 (장 미마감 세션인 경우 체결 건너뜀)
            if is_live_session:
                # 장 미마감 세션: 체결은 안 시키되, 주문 정보만 기록
                daily_record['buy_executed'] = False
                if buy_signal:
                    daily_record['buy_order_price'] = buy_signal['buy_order_price']
                else:
                    current_config = get_mode_config(self.current_mode, strategy_context=self.strategy_context)
                    daily_record['buy_order_price'] = previous_close * current_config['buy_threshold']
            else:
                # 일반 세션: 매도 먼저 실행
                for position, exit_reason, exit_price in sell_signals:
                    self.execute_sell(position, exit_reason, exit_price, current_date)
                    daily_record['trades_closed_today'] += 1
                    profit_loss = (exit_price - position['entry_price']) * position['shares']
                    daily_record['daily_realized_pl'] += profit_loss
                    self.cumulative_realized_pl += profit_loss

                # 그다음에 매수 주문 실행
                if buy_signal:
                    if not self.is_in_season:
                        self.current_season += 1
                        self.is_in_season = True

                    daily_record['season'] = self.current_season
                    daily_record['buy_executed'] = True
                    self.execute_buy(buy_signal, current_date)

                    # 매수 정보 기록
                    current_config = get_mode_config(self.current_mode, strategy_context=self.strategy_context)
                    daily_record['buy_order_price'] = previous_close * current_config['buy_threshold']
                else:
                    current_config = get_mode_config(self.current_mode, strategy_context=self.strategy_context)
                    daily_record['buy_order_price'] = previous_close * current_config['buy_threshold']
                    daily_record['buy_executed'] = False

            # MDD 계산 (예수금 + 보유 포지션 평가 가치)
            equity_value = sum(p['shares'] * current_price for p in self.positions)
            account_value = self.live_cash + equity_value

            if account_value > self.peak_value:
                self.peak_value = account_value

            # 구글 시트 수식 적용: (누적 최고점 - 현재 총자산) / 누적 최고점
            drawdown = (self.peak_value - account_value) / self.peak_value if self.peak_value > 0 else 0
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown

            daily_record['equity_value'] = equity_value
            daily_record['live_cash'] = self.live_cash
            daily_record['account_value'] = account_value
            daily_record['drawdown'] = drawdown * 100  # 양수 퍼센트로 저장 (예: 25.43)
            daily_record['mdd'] = self.max_drawdown * 100  # 양수 퍼센트로 저장

            # end_date 이후는 종료 (이미 위에서 체크함)

            # 일별 로그 저장
            self.daily_log.append(daily_record)

            # 시드 기록
            self.seed_history.append({
                'date': current_date,
                'seed': self.seed,
                'reference_seed': self.reference_seed,
                'seed_date': self.reference_seed_date,
                'live_cash': self.live_cash,
                'equity_value': account_value - self.live_cash,
                'account_value': account_value,
                'positions_count': len(self.positions)
            })

        return self.generate_report()

    def generate_report(self):
        """백테스트 결과 리포트 생성"""
        total_trades = len(self.trades)

        # 최종 자산 가치 계산 (예수금 + 남은 포지션 평가금)
        # self.live_cash는 이미 정산되지 않은 모든 수익이 포함된 상태 (이미지 AS열)
        remaining_equity = sum(p['shares'] * self.soxl_data.iloc[-1]['Close'] for p in self.positions)
        final_account_value = self.live_cash + remaining_equity

        if total_trades == 0:
            return {
                'summary': {
                    'initial_seed': self.initial_seed,
                    'total_invested_capital': self.total_invested_capital,
                    'final_seed': final_account_value,
                    'total_return': final_account_value - self.total_invested_capital,
                    'total_return_pct': ((final_account_value - self.total_invested_capital) / self.total_invested_capital) * 100 if self.total_invested_capital > 0 else 0,
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'win_rate': 0,
                    'total_profit': 0,
                    'total_loss': 0,
                    'avg_profit': 0,
                    'avg_loss': 0,
                    'max_drawdown': self.max_drawdown * 100
                },
                'trades': [],
                'seed_history': self.seed_history,
                'mode_history': self.mode_history,
                'active_positions': self.positions,
                'pending_settlements': self.settlements,
                'daily_log': self.daily_log,
                'benchmark': self._build_benchmark_series(),
                'data_warnings': self.data_warnings,
                'cash_flow_policy_events': self.cash_flow_policy_events,
            }

        winning_trades = [t for t in self.trades if t['profit_loss'] > 0]
        losing_trades = [t for t in self.trades if t['profit_loss'] < 0]

        total_profit = sum(t['profit_loss'] for t in winning_trades)
        total_loss = sum(t['profit_loss'] for t in losing_trades)

        # CAGR 계산
        start_dt = datetime.strptime(self.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(self.end_date, '%Y-%m-%d')
        days = (end_dt - start_dt).days
        years = days / 365.25

        if years > 0 and self.total_invested_capital > 0:
            # CAGR은 복리 수익률이므로 (현재가치 / 투자원금) ^ (1/연수) - 1
            cagr = ((final_account_value / self.total_invested_capital) ** (1 / years) - 1) * 100
        else:
            cagr = 0

        summary = {
            'initial_seed': self.initial_seed,
            'total_invested_capital': self.total_invested_capital,
            'final_seed': final_account_value,
            'total_return': final_account_value - self.total_invested_capital,
            'total_return_pct': ((final_account_value - self.total_invested_capital) / self.total_invested_capital) * 100 if self.total_invested_capital > 0 else 0,
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'avg_profit': total_profit / len(winning_trades) if winning_trades else 0,
            'avg_loss': total_loss / len(losing_trades) if losing_trades else 0,
            'max_drawdown': self.max_drawdown * 100,
            'cagr': cagr
        }

        return {
            'summary': summary,
            'trades': self.trades,
            'seed_history': self.seed_history,
            'mode_history': self.mode_history,
            'active_positions': self.positions,
            'pending_settlements': self.settlements,
            'daily_log': self.daily_log,  # 일별 거래 로그 추가
            'benchmark': self._build_benchmark_series(),
            'data_warnings': self.data_warnings,
            'cash_flow_policy_events': self.cash_flow_policy_events,
        }
