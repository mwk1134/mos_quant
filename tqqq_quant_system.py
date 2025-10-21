import os
from datetime import datetime, timedelta
from typing import Dict

import pandas as pd

from soxl_quant_system import SOXLQuantTrader


class TQQQQuantTrader(SOXLQuantTrader):
	"""TQQQ 퀀트투자 시스템 (티커만 TQQQ로 변경, 로직 동일)"""

	def get_latest_trading_day(self) -> datetime:
		"""가장 최근 거래일 찾기 (TQQQ 데이터 기준)"""
		# 실제 TQQQ 데이터의 마지막 거래일을 기준으로 찾기
		tqqq_data = self.get_stock_data("TQQQ", "1mo")
		if tqqq_data is not None and len(tqqq_data) > 0:
			latest_date = tqqq_data.index[-1].to_pydatetime()
			print(f"📊 TQQQ 데이터 기준 최신 거래일: {latest_date.strftime('%Y-%m-%d')}")
			return latest_date
		# 데이터를 가져올 수 없는 경우, 부모 로직으로 계산
		return super().get_latest_trading_day()

	def get_daily_recommendation(self) -> Dict:
		"""일일 매매 추천 생성 (TQQQ 기준)"""
		print("=" * 60)
		print("🚀 TQQQ 퀀트투자 일일 매매 추천")
		print("=" * 60)

		# 현재 상태를 최신으로 업데이트 (시작일부터 현재까지 시뮬레이션)
		if self.session_start_date:
			print("🔄 트레이더 상태를 최신으로 업데이트 중...")
			sim_result = self.simulate_from_start_to_today(self.session_start_date, quiet=True)
			if "error" in sim_result:
				return {"error": f"상태 업데이트 실패: {sim_result['error']}"}

		# 시장 휴장일 확인
		today = datetime.now()
		is_market_closed = self.is_market_closed(today)
		if is_market_closed:
			latest_trading_day = self.get_latest_trading_day()
			if today.weekday() >= 5:
				print(f"📅 주말입니다. 최신 거래일({latest_trading_day.strftime('%Y-%m-%d')}) 데이터를 사용합니다.")
			else:
				print(f"📅 휴장일입니다. 최신 거래일({latest_trading_day.strftime('%Y-%m-%d')}) 데이터를 사용합니다.")

		# 1. TQQQ 데이터 가져오기
		tqqq_data = self.get_stock_data("TQQQ", "1mo")
		if tqqq_data is None:
			return {"error": "TQQQ 데이터를 가져올 수 없습니다."}

		# 2. QQQ 데이터 가져오기 (주간 RSI 계산용) - 동일
		qqq_data = self.get_stock_data("QQQ", "6mo")
		if qqq_data is None:
			return {"error": "QQQ 데이터를 가져올 수 없습니다."}

		# 3. QQQ 주간 RSI 기반 모드 자동 전환
		self.update_mode(qqq_data)

		# QQQ 주간 RSI 계산 (표시용)
		weekly_rsi = self.calculate_weekly_rsi(qqq_data)
		if weekly_rsi is None:
			return {"error": "QQQ 주간 RSI를 계산할 수 없습니다."}

		# 4. 최신 TQQQ 가격 정보 (최소 2일 데이터 필요)
		if len(tqqq_data) < 2:
			return {"error": "데이터가 부족합니다. 최소 2일의 데이터가 필요합니다."}

		latest_tqqq = tqqq_data.iloc[-1]
		current_price = latest_tqqq['Close']
		current_date = tqqq_data.index[-1]

		# 전일 종가 계산
		last_data_date = current_date.date()
		today_date = datetime.now().date()
		if last_data_date < today_date:
			prev_close = tqqq_data.iloc[-1]['Close']
			prev_close_basis_date = tqqq_data.index[-1].strftime("%Y-%m-%d")
			display_date = today_date.strftime("%Y-%m-%d")
		else:
			prev_close = tqqq_data.iloc[-2]['Close']
			prev_close_basis_date = tqqq_data.index[-2].strftime("%Y-%m-%d")
			display_date = current_date.strftime("%Y-%m-%d")

		# 5. 매수/매도 가격 계산 (전일 종가 기준)
		buy_price, sell_price = self.calculate_buy_sell_prices(prev_close)

		# 6. 매도 조건 확인 (TQQQ 최신 행으로 전달)
		sell_recommendations = self.check_sell_conditions(latest_tqqq, current_date, prev_close)

		# 7. 매수 가능 여부
		can_buy = self.can_buy_next_round()
		next_buy_amount = self.calculate_position_size(self.current_round) if can_buy else 0

		# 8. 포트폴리오 현황
		total_position_value = sum([pos["shares"] * current_price for pos in self.positions])
		total_invested = sum([pos["amount"] for pos in self.positions])
		unrealized_pnl = total_position_value - total_invested

		recommendation = {
			"date": display_date,
			"basis_date": prev_close_basis_date,
			"mode": self.current_mode,
			"qqq_weekly_rsi": weekly_rsi,
			"tqqq_current_price": current_price,
			# 부모 클래스의 print_recommendATION 호환 키
			"soxl_current_price": current_price,
			"buy_price": buy_price,
			"sell_price": sell_price,
			"can_buy": can_buy,
			"next_buy_round": self.current_round if can_buy else None,
			"next_buy_amount": next_buy_amount,
			"sell_recommendations": sell_recommendations,
			"portfolio": {
				"positions_count": len(self.positions),
				"total_invested": total_invested,
				"total_position_value": total_position_value,
				"unrealized_pnl": unrealized_pnl,
				"available_cash": self.available_cash,
				"total_portfolio_value": self.available_cash + total_position_value,
			},
		}
		return recommendation

	def run_backtest(self, start_date: str, end_date: str = None) -> Dict:
		"""TQQQ 전용 백테스팅 (SOXL 로직을 그대로 적용하되 티커만 교체)"""
		print(f"🔄 백테스팅 시작: {start_date} ~ {end_date or '오늘'}")

		self.backtest_logs = []
		rsi_ref_data = self.load_rsi_reference_data()
		self.reset_portfolio()

		starting_state = self.check_backtest_starting_state(start_date, rsi_ref_data)
		if "error" in starting_state:
			return {"error": starting_state["error"]}
		self.current_mode = starting_state["start_mode"]
		self.current_round = starting_state["start_round"]

		print("🎯 백테스팅 시작 설정:")
		print(f"   - 모드: {self.current_mode}")
		print(f"   - 회차: {self.current_round}")
		print(f"   - 1회시드 예상: ${self.initial_capital * self.get_current_config()['split_ratios'][self.current_round-1]:,.0f}")

		# 날짜 파싱
		try:
			start_dt = datetime.strptime(start_date, "%Y-%m-%d")
			if end_date:
				end_dt = datetime.strptime(end_date, "%Y-%m-%d")
				end_dt = end_dt.replace(hour=23, minute=59, second=59)
			else:
				end_dt = datetime.now()
		except ValueError:
			return {"error": "날짜 형식이 올바르지 않습니다. YYYY-MM-DD 형식으로 입력해주세요."}

		# 장 마감 전 종료일 보정
		try:
			if not self.is_regular_session_closed_now():
				latest_trading_day = self.get_latest_trading_day().date()
				effective_end_date = min(end_dt.date(), latest_trading_day)
				end_dt = datetime(effective_end_date.year, effective_end_date.month, effective_end_date.day, 23, 59, 59)
		except Exception:
			pass

		# 데이터 로드 기간 계산
		data_start = start_dt - timedelta(days=180)
		period_days = (datetime.now() - data_start).days
		if period_days <= 365:
			period = "1y"
		elif period_days <= 730:
			period = "2y"
		elif period_days <= 1825:
			period = "5y"
		elif period_days <= 3650:
			period = "10y"
		else:
			period = "15y"

		# TQQQ / QQQ 데이터 로드
		tqqq_data = self.get_stock_data("TQQQ", period)
		if tqqq_data is None:
			return {"error": "TQQQ 데이터를 가져올 수 없습니다."}
		qqq_data = self.get_stock_data("QQQ", period)
		if qqq_data is None:
			return {"error": "QQQ 데이터를 가져올 수 없습니다."}

		# 미마감 오늘자 제거
		try:
			today_date = datetime.now().date()
			if self.is_trading_day(datetime.now()) and not self.is_regular_session_closed_now():
				if len(tqqq_data) > 0 and tqqq_data.index.max().date() == today_date:
					tqqq_data = tqqq_data[tqqq_data.index.date < today_date]
				if len(qqq_data) > 0 and qqqq_data.index.max().date() == today_date:
					qqq_data = qqqq_data[qqqq_data.index.date < today_date]
		except Exception:
			pass

		# 종료일이 오늘이며 미마감이면 마지막 행 제외
		try:
			if end_date:
				end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
				last_date = tqqq_data.index.max().date() if len(tqqq_data) > 0 else None
				today_date = datetime.now().date()
				if last_date and end_d == last_date:
					if not (end_d < today_date or (end_d == today_date and self.is_regular_session_closed_now())):
						tqqq_data = tqqq_data[tqqq_data.index.date < last_date]
						qqq_data = qqqq_data[qqqq_data.index.date < last_date]
		except Exception:
			pass

		# 백테스트 구간 필터링
		tqqq_backtest = tqqq_data[tqqq_data.index >= start_dt]
		tqqq_backtest = tqqq_backtest[tqqq_backtest.index <= end_dt]
		if len(tqqq_backtest) == 0:
			return {"error": "해당 기간에 대한 데이터가 없습니다."}

		print(f"📊 총 {len(tqqq_backtest)}일 백테스팅 진행...")

		prev_close = None
		if len(tqqq_backtest) > 0:
			start_date_prev = start_dt - timedelta(days=1)
			prev_data = tqqq_data[tqqq_data.index <= start_date_prev]
			if len(prev_data) > 0:
				prev_close = prev_data.iloc[-1]['Close']
				print(f"📅 백테스팅 시작 전일 종가: {prev_close:.2f} (날짜: {prev_data.index[-1].strftime('%Y-%m-%d')})")
			else:
				print("⚠️ 백테스팅 시작 전일 데이터를 찾을 수 없습니다.")

		current_week_friday = None
		previous_day_sold_rounds = 0
		daily_records = []
		current_week_rsi = starting_state["start_week_rsi"]
		current_mode = starting_state["start_mode"]
		current_week = 0
		total_realized_pnl = 0
		total_invested = 0
		cash_balance = self.initial_capital

		for i, (current_date, row) in enumerate(tqqq_backtest.iterrows()):
			current_price = row['Close']

			# 전날 매도 반영: 보유 회차 수 + 1
			if previous_day_sold_rounds > 0:
				holding_rounds = len(self.positions)
				self.current_round = holding_rounds + 1
				print(f"🔄 전날 매도 완료: {previous_day_sold_rounds}개 회차 매도 → 보유: {holding_rounds}개 → 다음 매수: {self.current_round}회차")
				previous_day_sold_rounds = 0

			# 거래일 카운트 및 10거래일마다 투자원금 업데이트
			if self.is_trading_day(current_date):
				self.trading_days_count += 1
				if self.trading_days_count % 10 == 0 and self.trading_days_count > 0:
					total_shares = sum([pos["shares"] for pos in self.positions])
					total_assets = self.available_cash + (total_shares * current_price)
					old_capital = self.current_investment_capital
					self.current_investment_capital = total_assets
					print(f"💰 투자원금 업데이트: {self.trading_days_count}거래일째 - ${old_capital:,.0f} → ${total_assets:,.0f}")

			# 주간 RSI 갱신
			days_until_friday = (4 - current_date.weekday()) % 7
			if days_until_friday == 0 and current_date.weekday() != 4:
				days_until_friday = 7
			this_week_friday = current_date + timedelta(days=days_until_friday)
			if current_week_friday != this_week_friday:
				current_week_friday = this_week_friday
				current_week_rsi = self.get_rsi_from_reference(this_week_friday, rsi_ref_data)
				prev_week_friday = this_week_friday - timedelta(days=7)
				two_weeks_ago_friday = this_week_friday - timedelta(days=14)
				prev_week_rsi = self.get_rsi_from_reference(prev_week_friday, rsi_ref_data)
				two_weeks_ago_rsi = self.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
				if prev_week_rsi is None or two_weeks_ago_rsi is None:
					return {"error": f"RSI 데이터가 없습니다. 1주전 RSI: {prev_week_rsi}, 2주전 RSI: {two_weeks_ago_rsi}"}
				new_mode = self.determine_mode(prev_week_rsi, two_weeks_ago_rsi, current_mode)
				if new_mode != current_mode:
					print(f"🔄 백테스팅 모드 전환: {current_mode} → {new_mode}")
					print(f"   현재 RSI: {current_week_rsi if current_week_rsi is not None else 'None'}")
					current_mode = new_mode
					self.current_mode = new_mode
				current_week += 1
				print(f"📅 주차 {current_week}: ~{this_week_friday.strftime('%m-%d')} | RSI: {current_week_rsi if current_week_rsi is not None else 'None'}")

			# 매수/매도 로직 (전일 종가 기준)
			if prev_close is not None:
				config = self.sf_config if current_mode == "SF" else self.ag_config
				buy_price = prev_close * (1 + config["buy_threshold"] / 100)
				sell_price = prev_close * (1 + config["sell_threshold"] / 100)

				sell_recommendations = self.check_sell_conditions(row, current_date, prev_close)
				daily_realized = 0
				sold_rounds = []
				sold_positions = []

				for sell_info in sell_recommendations:
					position = sell_info["position"]
					proceeds, sold_round = self.execute_sell(sell_info)
					realized_pnl = proceeds - position["amount"]
					daily_realized += realized_pnl
					total_realized_pnl += realized_pnl
					cash_balance += proceeds
					sold_rounds.append(sold_round)

					# 매도 정보 기록용 준비
					weekdays_korean = ['월', '화', '수', '목', '금', '토', '일']
					weekday_korean = weekdays_korean[current_date.weekday()]
					sold_positions.append({
						"round": sold_round,
						"sell_date": current_date.strftime(f"%m.%d.({weekday_korean})"),
						"sell_price": sell_info["sell_price"],
						"realized_pnl": realized_pnl,
					})

				buy_executed = False
				buy_price_executed = 0
				buy_quantity = 0
				buy_amount = 0
				current_round_before_buy = self.current_round

				if self.can_buy_next_round():
					daily_close = row['Close']
					log_msg = f"🔍 {current_date.strftime('%Y-%m-%d')} 매수 조건 확인:\n"
					log_msg += f"   전일 종가(prev_close): ${prev_close:.2f}\n"
					log_msg += f"   당일 종가(daily_close): ${daily_close:.2f}\n"
					log_msg += f"   매수가(buy_price): ${buy_price:.2f}\n"
					log_msg += f"   매수 조건: {buy_price:.2f} > {daily_close:.2f} = {buy_price > daily_close}\n"
					log_msg += f"   현재 회차: {self.current_round}, 현금잔고: ${self.available_cash:,.0f}"
					print(log_msg)
					self.backtest_logs.append(log_msg)

					if buy_price > daily_close:
						print("✅ 매수 조건 충족! 매수 실행 시도...")
						self.backtest_logs.append("✅ 매수 조건 충족! 매수 실행 시도...")
						if self.execute_buy(buy_price, daily_close, current_date):
							print("✅ 매수 체결 성공!")
							self.backtest_logs.append("✅ 매수 체결 성공!")
							buy_executed = True
							position = self.positions[-1]
							buy_price_executed = position["buy_price"]
							buy_quantity = position["shares"]
							buy_amount = position["amount"]
							total_invested += buy_amount
							cash_balance -= buy_amount
							# 매수 체결 시 매도목표가 재계산
							sell_price = daily_close * (1 + config["sell_threshold"] / 100)

					else:
						print(f"❌ 매수 조건 불충족: {buy_price:.2f} <= {daily_close:.2f}")
						self.backtest_logs.append(f"❌ 매수 조건 불충족: {buy_price:.2f} <= {daily_close:.2f}")
				else:
					print("❌ 매수 불가능: can_buy_next_round() = False")
					self.backtest_logs.append("❌ 매수 불가능: can_buy_next_round() = False")

				# 다음날 회차 조정을 위해 매도된 회차 저장
				if sold_rounds:
					previous_day_sold_rounds = len(sold_rounds)
					print(f"🔄 매도 완료: {previous_day_sold_rounds}개 회차 매도 → 다음날 current_round에 반영 예정")

				# 일별 기록 저장
				weekdays_korean = ['월', '화', '수', '목', '금', '토', '일']
				weekday_korean = weekdays_korean[current_date.weekday()]
				daily_record = {
					"date": current_date.strftime("%Y-%m-%d"),
					"week": current_week,
					"rsi": current_week_rsi or 50.0,
					"mode": current_mode,
					"current_round": min(current_round_before_buy, 7 if current_mode == "SF" else 8),
					"seed_amount": self.calculate_position_size(current_round_before_buy) if buy_executed else 0,
					"buy_order_price": buy_price,
					"close_price": current_price,
					"sell_target_price": sell_price,
					"stop_loss_date": self.calculate_stop_loss_date(current_date, config["max_hold_days"]),
					"d": 0,
					"trading_days": i + 1,
					"buy_executed_price": buy_price_executed,
					"buy_quantity": buy_quantity,
					"buy_amount": buy_amount,
					"buy_round": current_round_before_buy if buy_executed else 0,
					"commission": 0.0,
					"sell_date": "",
					"sell_executed_price": 0,
					"holding_days": 0,
					"holdings": sum([pos["shares"] for pos in self.positions]),
					"realized_pnl": 0,
					"cumulative_realized": total_realized_pnl,
					"daily_realized": daily_realized,
					"update": False,
					"investment_update": self.initial_capital,
					"withdrawal": False,
					"withdrawal_amount": 0,
					"seed_increase": 0,
					"position_value": sum([pos["shares"] for pos in self.positions]) * current_price,
					"cash_balance": cash_balance,
					"total_assets": cash_balance + sum([pos["shares"] for pos in self.positions]) * current_price,
				}
				daily_records.append(daily_record)

			if (i + 1) % 10 == 0:
				print(f"진행: {i+1}/{len(tqqq_backtest)}일 ({(i+1)/len(tqqq_backtest)*100:.1f}%)")

			prev_close = current_price

		# 백테스트 종료 후 current_round = 보유 회차 수 + 1
		holding_rounds = len(self.positions)
		self.current_round = holding_rounds + 1
		print(f"🔄 백테스팅 완료 후 current_round 설정: 보유 {holding_rounds}개 → 다음 매수 {self.current_round}회차")

		final_value = daily_records[-1]["total_assets"] if daily_records else self.initial_capital
		total_return = ((final_value - self.initial_capital) / self.initial_capital) * 100

		summary = {
			"start_date": start_date,
			"end_date": end_date or datetime.now().strftime("%Y-%m-%d"),
			"trading_days": len(tqqq_backtest),
			"initial_capital": self.initial_capital,
			"final_value": final_value,
			"total_return": total_return,
			"final_positions": len(self.positions),
			"daily_records": daily_records,
			"logs": self.backtest_logs if hasattr(self, 'backtest_logs') else [],
		}

		# 리스크 지표 출력은 부모 export에서 계산하므로 여기선 생략/요약 출력
		print("✅ 백테스팅 완료!")
		print(f"   💰 최종자산: ${final_value:,.0f}")
		print(f"   📈 총수익률: {total_return:+.2f}%")
		return summary

	def export_backtest_to_excel(self, backtest_result: Dict, filename: str = None):
		"""파일명/표기만 TQQQ로 변경하여 내보내기"""
		if "error" in backtest_result:
			print(f"❌ 엑셀 내보내기 실패: {backtest_result['error']}")
			return
		if filename is None:
			timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
			filename = f"TQQQ_백테스팅_{backtest_result['start_date']}_{timestamp}.xlsx"
		# 부모의 내보내기 로직 사용 (내용은 동일, 파일명만 변경)
		return super().export_backtest_to_excel(backtest_result, filename)


def main():
	print("🚀 TQQQ 퀀트투자 시스템")
	print("=" * 50)
	try:
		initial_capital_input = input("💰 초기 투자금을 입력하세요 (달러): ").strip()
		initial_capital = float(initial_capital_input) if initial_capital_input else 9000
	except Exception:
		initial_capital = 9000
	print(f"💰 투자원금: ${initial_capital:,.0f}")

	trader = TQQQQuantTrader(initial_capital)
	start_date_input = input("📅 투자 시작일을 입력하세요 (YYYY-MM-DD, 엔터시 1년 전): ").strip()
	if not start_date_input:
		start_date_input = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
	trader.session_start_date = start_date_input

	rec = trader.get_daily_recommendation()
	if "error" in rec:
		print(f"❌ 추천 생성 실패: {rec['error']}")
		return
	trader.print_recommendation(rec)


if __name__ == "__main__":
	main()
