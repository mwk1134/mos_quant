import requests
import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import os

import sys
import io
from contextlib import redirect_stdout
from pathlib import Path


class SOXLQuantTrader:
    """SOXL ??명닾???쒖뒪??""

    
    def _resolve_data_path(self, filename: str) -> Path:
        base_dir = Path(__file__).resolve().parent
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / filename

    def load_rsi_reference_data(self, filename: str = "weekly_rsi_reference.json") -> dict:
        """
        RSI 李몄“ ?곗씠??濡쒕뱶 (JSON ?뺤떇)
        Args:
            filename: RSI 李몄“ ?뚯씪紐?        Returns:
            dict: RSI 李몄“ ?곗씠??        """
        try:
            # PyInstaller ?ㅽ뻾?뚯씪?먯꽌 ?뚯씪 寃쎈줈 泥섎━
            if getattr(sys, 'frozen', False):
                # ?ㅽ뻾?뚯씪濡??ㅽ뻾??寃쎌슦
                if hasattr(sys, '_MEIPASS'):
                    # PyInstaller???꾩떆 ?대뜑
                    application_path = sys._MEIPASS
                else:
                    # ?쇰컲 ?ㅽ뻾?뚯씪
                    application_path = os.path.dirname(sys.executable)
                file_path = os.path.join(application_path, filename)
            else:
                # ?ㅽ겕由쏀듃濡??ㅽ뻾??寃쎌슦
                file_path = str(self._resolve_data_path(filename))
            
            # data ?대뜑媛 ?놁쑝硫??앹꽦
            data_dir = os.path.dirname(file_path)
            if data_dir and not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
                print(f"?뱚 {data_dir} ?대뜑 ?앹꽦 ?꾨즺")
            
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    rsi_data = json.load(f)
                
                # 硫뷀??곗씠??異쒕젰
                metadata = rsi_data.get('metadata', {})
                total_weeks = metadata.get('total_weeks', 0)
                last_updated = metadata.get('last_updated', 'Unknown')
                
                print(f"[INFO] RSI 李몄“ ?곗씠??濡쒕뱶 ?꾨즺")
                print(f"   - ?뚯씪 寃쎈줈: {file_path}")
                print(f"   - 珥?{len(rsi_data)-1}媛??곕룄 ?곗씠??({total_weeks}二쇱감)")
                print(f"   - 留덉?留??낅뜲?댄듃: {last_updated}")
                
                return rsi_data
            else:
                print(f"?좑툘 RSI 李몄“ ?뚯씪???놁뒿?덈떎: {file_path}")
                return {}
        except Exception as e:
            print(f"[ERROR] RSI 李몄“ ?곗씠??濡쒕뱶 ?ㅻ쪟: {e}")
            return {}
    
    def get_rsi_from_reference(self, date: datetime, rsi_data: dict) -> float:
        """
        ?뱀젙 ?좎쭨??RSI 媛믪쓣 李몄“ ?곗씠?곗뿉??媛?몄삤湲?(JSON ?뺤떇)
        JSON ?뚯씪 ?꾩껜?먯꽌 ?대떦 ?좎쭨瑜?李얜뒗 媛뺣젰??寃??濡쒖쭅
        Args:
            date: ?뺤씤???좎쭨
            rsi_data: RSI 李몄“ ?곗씠??(JSON)
        Returns:
            float: RSI 媛?(?놁쑝硫?None)
        """
        try:
            if not rsi_data:
                return None
            
            date_str = date.strftime('%Y-%m-%d')
            
            # 1?④퀎: 紐⑤뱺 ?곕룄?먯꽌 ?대떦 ?좎쭨媛 ?ы븿?섎뒗 二쇱감 李얘린
            available_years = [y for y in rsi_data.keys() if y != 'metadata']
            available_years.sort(reverse=True)  # 理쒖떊 ?곕룄遺??寃??            
            for year in available_years:
                if 'weeks' not in rsi_data[year]:
                    continue
                    
                weeks = rsi_data[year]['weeks']
                
                # ?대떦 ?좎쭨媛 ?ы븿?섎뒗 二쇱감 李얘린
                for week_data in weeks:
                    start_date = week_data['start']
                    end_date = week_data['end']
                    if start_date <= date_str <= end_date:
                        return float(week_data['rsi'])
            
            # 2?④퀎: ?뺥솗??二쇱감媛 ?놁쑝硫?媛??媛源뚯슫 ?댁쟾 二쇱감??RSI ?ъ슜
            # 紐⑤뱺 ?곕룄??紐⑤뱺 二쇱감瑜??좎쭨?쒖쑝濡??뺣젹?섏뿬 寃??            all_weeks = []
            for year in available_years:
                if 'weeks' not in rsi_data[year]:
                    continue
                for week_data in rsi_data[year]['weeks']:
                    week_data_copy = week_data.copy()
                    week_data_copy['year'] = year
                    all_weeks.append(week_data_copy)
            
            # 醫낅즺??湲곗??쇰줈 ?뺣젹
            all_weeks.sort(key=lambda x: x['end'])
            
            # ?대떦 ?좎쭨蹂대떎 ?댁쟾??媛??媛源뚯슫 二쇱감 李얘린
            for week_data in reversed(all_weeks):
                if week_data['end'] <= date_str:
                    return float(week_data['rsi'])
            
            # 3?④퀎: 洹몃옒???놁쑝硫?媛??理쒓렐 二쇱감??RSI ?ъ슜
            if all_weeks:
                return float(all_weeks[-1]['rsi'])
            
            return None
        except Exception as e:
            print(f"[ERROR] RSI 李몄“ ?곗씠??議고쉶 ?ㅻ쪟: {e}")
            return None
    
    def check_and_update_rsi_data(self, filename: str = "weekly_rsi_reference.json") -> bool:
        """
        RSI 李몄“ ?곗씠?곌? 理쒖떊?몄? ?뺤씤?섍퀬 ?꾩슂???낅뜲?댄듃 (JSON ?뺤떇)
        Args:
            filename: RSI 李몄“ ?뚯씪紐?        Returns:
            bool: ?낅뜲?댄듃 ?깃났 ?щ?
        """
        try:
            today = datetime.now()
            
            # PyInstaller ?ㅽ뻾?뚯씪?먯꽌 ?뚯씪 寃쎈줈 泥섎━
            if getattr(sys, 'frozen', False):
                # ?ㅽ뻾?뚯씪濡??ㅽ뻾??寃쎌슦
                application_path = os.path.dirname(sys.executable)
                file_path = os.path.join(application_path, filename)
            else:
                # ?ㅽ겕由쏀듃濡??ㅽ뻾??寃쎌슦
                file_path = str(self._resolve_data_path(filename))
            
            # data ?대뜑媛 ?놁쑝硫??앹꽦
            data_dir = os.path.dirname(file_path)
            if data_dir and not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
                print(f"?뱚 {data_dir} ?대뜑 ?앹꽦 ?꾨즺")
            
            # 湲곗〈 RSI ?곗씠??濡쒕뱶
            if os.path.exists(file_path):
                #print(f"?뵇 JSON ?뚯씪 濡쒕뱶 ?쒕룄: {file_path}")
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                
                # ?붾쾭源? 濡쒕뱶???곗씠??援ъ“ ?뺤씤
                print(f"[SUCCESS] JSON ?뚯씪 濡쒕뱶 ?깃났!")
                #print(f"   - ?뚯씪 ?ш린: {os.path.getsize(file_path)} bytes")
                #print(f"   - 濡쒕뱶???ㅻ뱾: {list(existing_data.keys())}")
                #print(f"   - 珥??곕룄 ?? {len([k for k in existing_data.keys() if k != 'metadata'])}")
                
                # 2024?? 2025???곗씠???뺤씤
                if '2024' in existing_data:
                    print(f"   - 2024???곗씠?? {len(existing_data['2024']['weeks'])}二쇱감")
                if '2025' in existing_data:
                    print(f"   - 2025???곗씠?? {len(existing_data['2025']['weeks'])}二쇱감")
                
                metadata = existing_data.get('metadata', {})
                last_updated = metadata.get('last_updated', '')
                
                if last_updated:
                    last_update_date = datetime.strptime(last_updated, '%Y-%m-%d')
                    print(f"?뱟 RSI 李몄“ ?곗씠??留덉?留??낅뜲?댄듃: {last_updated}")
                    
                    # 留덉?留??낅뜲?댄듃媛 ?ㅻ뒛濡쒕???1二쇱씪 ?대궡硫??낅뜲?댄듃 遺덊븘??                    if (today - last_update_date).days <= 7:
                        print("[SUCCESS] RSI 李몄“ ?곗씠?곌? 理쒖떊 ?곹깭?낅땲??")
                        return True
                    
                    print(f"?좑툘 RSI 李몄“ ?곗씠?곌? {(today - last_update_date).days}?????곗씠?곗엯?덈떎. ?낅뜲?댄듃媛 ?꾩슂?⑸땲??")
                else:
                    print("?좑툘 RSI 李몄“ ?곗씠??硫뷀??곗씠?곌? ?놁뒿?덈떎.")
            else:
                print("?좑툘 RSI 李몄“ ?뚯씪???놁뒿?덈떎. ?꾩껜 ?곗씠???앹꽦???꾩슂?⑸땲??")
            
            # ?ъ슜?먯뿉寃??낅뜲?댄듃 ?뺤씤
            print("\n[INFO] RSI 李몄“ ?곗씠???낅뜲?댄듃媛 ?꾩슂?⑸땲??")
            print("[INFO] ?쒓났?댁＜??2010??2025??RSI ?곗씠?곕? 紐⑤몢 異붽??섏떆寃좎뒿?덇퉴?")
            print("   (???묒뾽? ??踰덈쭔 ?섑뻾?섎㈃ ?⑸땲??")
            
            return False
            
        except Exception as e:
            print(f"[ERROR] RSI ?곗씠???뺤씤 ?ㅻ쪟: {e}")
            return False
    
    def update_rsi_reference_file(self, filename: str = "weekly_rsi_reference.json") -> bool:
        """
        RSI 李몄“ ?뚯씪??理쒖떊 ?곗씠?곕줈 ?낅뜲?댄듃 (JSON ?뺤떇)
        ?ㅻ뒛 ?좎쭨源뚯???二쇨컙 RSI瑜??먮룞?쇰줈 怨꾩궛?섏뿬 ?낅뜲?댄듃
        Args:
            filename: RSI 李몄“ ?뚯씪紐?        Returns:
            bool: ?낅뜲?댄듃 ?깃났 ?щ?
        """
        try:
            print("[INFO] RSI 李몄“ ?곗씠???낅뜲?댄듃 以?..")
            print("[INFO] ?ㅻ뒛 ?좎쭨源뚯???二쇨컙 RSI瑜??먮룞 怨꾩궛?섏뿬 ?낅뜲?댄듃?⑸땲??")
            
            # PyInstaller ?ㅽ뻾?뚯씪?먯꽌 ?뚯씪 寃쎈줈 泥섎━
            if getattr(sys, 'frozen', False):
                # ?ㅽ뻾?뚯씪濡??ㅽ뻾??寃쎌슦
                application_path = os.path.dirname(sys.executable)
                file_path = os.path.join(application_path, filename)
            else:
                # ?ㅽ겕由쏀듃濡??ㅽ뻾??寃쎌슦
                file_path = str(self._resolve_data_path(filename))
            
            # data ?대뜑媛 ?놁쑝硫??앹꽦
            data_dir = os.path.dirname(file_path)
            if data_dir and not os.path.exists(data_dir):
                os.makedirs(data_dir, exist_ok=True)
                print(f"?뱚 {data_dir} ?대뜑 ?앹꽦 ?꾨즺")
            
            # 湲곗〈 JSON ?곗씠??濡쒕뱶
            existing_data = {}
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
            
            # ?꾩옱 ?곕룄? 二쇱감 怨꾩궛
            today = datetime.now()
            current_year = today.strftime('%Y')
            
            # QQQ ?곗씠??媛?몄삤湲?(理쒓렐 1??
            print("[INFO] QQQ ?곗씠??媛?몄삤??以?..")
            qqq_data = self.get_stock_data("QQQ", "1y")
            if qqq_data is None:
                print("[ERROR] QQQ ?곗씠?곕? 媛?몄삱 ???놁뒿?덈떎.")
                return False
            
            # 二쇨컙 ?곗씠?곕줈 蹂??            weekly_data = qqq_data.resample('W-FRI').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
            
            print(f"[INFO] 二쇨컙 ?곗씠??{len(weekly_data)}二?怨꾩궛 ?꾨즺")
            
            # ?꾩옱 ?곕룄 ?곗씠??珥덇린??            if current_year not in existing_data:
                existing_data[current_year] = {
                    "description": f"{current_year}??二쇨컙 RSI ?곗씠??,
                    "weeks": []
                }
            
            # 理쒓렐 12二?RSI 怨꾩궛 諛??낅뜲?댄듃
            recent_weeks = weekly_data.tail(12)  # 理쒓렐 12二?            
            for i, (week_end, week_row) in enumerate(recent_weeks.iterrows()):
                # ?대떦 二쇱쓽 ?쒖옉??怨꾩궛 (?붿슂??
                week_start = week_end - timedelta(days=4)  # 湲덉슂?쇱뿉??4????= ?붿슂??                
                # 二쇱감 踰덊샇 怨꾩궛 (?대떦 ?곕룄??紐?踰덉㎏ 二쇱씤吏)
                week_num = week_start.isocalendar()[1]
                
                # RSI 怨꾩궛
                data_until_week = qqq_data[qqq_data.index <= week_end]
                if len(data_until_week) >= 20:  # 異⑸텇???곗씠?곌? ?덉쓣 ??                    rsi_value = self.calculate_weekly_rsi(data_until_week)
                    if rsi_value is not None:
                        # 湲곗〈 ?곗씠?곗뿉???대떦 二쇱감 李얘린
                        week_exists = False
                        for j, existing_week in enumerate(existing_data[current_year]['weeks']):
                            if existing_week['week'] == week_num:
                                # 湲곗〈 ?곗씠???낅뜲?댄듃
                                existing_data[current_year]['weeks'][j] = {
                                    "start": week_start.strftime('%Y-%m-%d'),
                                    "end": week_end.strftime('%Y-%m-%d'),
                                    "week": week_num,
                                    "rsi": round(rsi_value, 2)
                                }
                                week_exists = True
                                break
                        
                        if not week_exists:
                            # ?덈줈??二쇱감 ?곗씠??異붽?
                            existing_data[current_year]['weeks'].append({
                                "start": week_start.strftime('%Y-%m-%d'),
                                "end": week_end.strftime('%Y-%m-%d'),
                                "week": week_num,
                                "rsi": round(rsi_value, 2)
                            })
                        
                        print(f"   二쇱감 {week_num}: {week_start.strftime('%m-%d')} ~ {week_end.strftime('%m-%d')} | RSI: {rsi_value:.2f}")
            
            # 二쇱감蹂꾨줈 ?뺣젹
            existing_data[current_year]['weeks'].sort(key=lambda x: x['week'])
            
            # 硫뷀??곗씠???낅뜲?댄듃
            total_weeks = sum(len(year_data['weeks']) for year, year_data in existing_data.items() if year != 'metadata')
            existing_data['metadata'] = {
                "last_updated": today.strftime('%Y-%m-%d'),
                "total_years": len([k for k in existing_data.keys() if k != 'metadata']),
                "total_weeks": total_weeks,
                "description": "QQQ 二쇨컙 RSI 李몄“ ?곗씠??(14二?Wilder's RSI)"
            }
            
            # JSON ?뚯씪濡????            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)
            
            print("[SUCCESS] RSI 李몄“ ?곗씠???낅뜲?댄듃 ?꾨즺!")
            print(f"   - {current_year}???곗씠???낅뜲?댄듃")
            print(f"   - 珥?{total_weeks}媛?二쇱감 ?곗씠??)
            print(f"   - 留덉?留??낅뜲?댄듃: {today.strftime('%Y-%m-%d')}")
            
            return True
            
        except Exception as e:
            print(f"[ERROR] RSI 李몄“ ?뚯씪 ?낅뜲?댄듃 ?ㅻ쪟: {e}")
            return False
    
    def __init__(self, initial_capital: float = 40000, sf_config: Optional[Dict] = None, ag_config: Optional[Dict] = None):
        """
        珥덇린??        Args:
            initial_capital: ?ъ옄?먭툑 (湲곕낯媛? 40000?щ윭)
            sf_config: SF 紐⑤뱶 ?ㅼ젙 (None?대㈃ 湲곕낯媛??ъ슜)
            ag_config: AG 紐⑤뱶 ?ㅼ젙 (None?대㈃ 湲곕낯媛??ъ슜)
        """
        self.initial_capital = initial_capital
        
        # ?깅뒫 理쒖쟻?붾? ?꾪븳 罹먯떆
        self._stock_data_cache = {}  # 二쇱떇 ?곗씠??罹먯떆
        self._simulation_cache = {}  # ?쒕??덉씠??寃곌낵 罹먯떆

        self.current_mode = None  # RSI 湲곗????곕씪 ?숈쟻?쇰줈 寃곗젙
        
        # 誘멸뎅 二쇱떇 ?쒖옣 ?댁옣??紐⑸줉 (2024-2025)
        self.us_holidays = [
            # 2024???댁옣??            "2024-01-01",  # New Year's Day
            "2024-01-15",  # Martin Luther King Jr. Day
            "2024-02-19",  # Washington's Birthday
            "2024-03-29",  # Good Friday
            "2024-05-27",  # Memorial Day
            "2024-06-19",  # Juneteenth
            "2024-07-04",  # Independence Day
            "2024-09-02",  # Labor Day
            "2024-11-28",  # Thanksgiving Day
            "2024-12-25",  # Christmas Day
            
            # 2025???댁옣??            "2025-01-01",  # New Year's Day
            "2025-01-20",  # Martin Luther King Jr. Day
            "2025-02-17",  # Washington's Birthday
            "2025-04-18",  # Good Friday
            "2025-05-26",  # Memorial Day
            "2025-06-19",  # Juneteenth
            "2025-07-04",  # Independence Day
            "2025-09-01",  # Labor Day
            "2025-11-27",  # Thanksgiving Day
            "2025-12-25",  # Christmas Day
            
            # ?밸퀎 ?댁옣??            "2025-01-09",  # Jimmy Carter National Day of Mourning
        ]
        
        # RSI 李몄“ ?곗씠???뺤씤 諛??낅뜲?댄듃
        if not self.check_and_update_rsi_data():
            print("[INFO] RSI 李몄“ ?곗씠???낅뜲?댄듃 以?..")
            if self.update_rsi_reference_file():
                print("[SUCCESS] RSI 李몄“ ?곗씠???낅뜲?댄듃 ?꾨즺")
            else:
                print("[ERROR] RSI 李몄“ ?곗씠???낅뜲?댄듃 ?ㅽ뙣")
        
        # SF紐⑤뱶 ?ㅼ젙 (?ъ슜??吏???먮뒗 湲곕낯媛?
        if sf_config is not None:
            self.sf_config = sf_config.copy()
        else:
            self.sf_config = {
                "buy_threshold": 3.5,   # ?꾩씪 醫낃? ?鍮?+3.5%??留ㅼ닔 (留ㅼ닔媛)
                "sell_threshold": 1.4,  # ?꾩씪 醫낃? ?鍮?+1.4%??留ㅻ룄 (留ㅻ룄媛)
                "max_hold_days": 30,    # 理쒕? 蹂댁쑀湲곌컙 30??                "split_count": 7,       # 7??遺꾪븷留ㅼ닔
                "split_ratios": [0.049, 0.127, 0.230, 0.257, 0.028, 0.169, 0.140]
            }
        
        # AG紐⑤뱶 ?ㅼ젙 (?ъ슜??吏???먮뒗 湲곕낯媛?
        if ag_config is not None:
            self.ag_config = ag_config.copy()
        else:
            self.ag_config = {
                "buy_threshold": 3.6,   # ?꾩씪 醫낃? ?鍮?+3.6%??留ㅼ닔 (留ㅼ닔媛)
                "sell_threshold": 3.5,  # ?꾩씪 醫낃? ?鍮?+3.5%??留ㅻ룄 (留ㅻ룄媛)
                "max_hold_days": 7,     # 理쒕? 蹂댁쑀湲곌컙 7??                "split_count": 8,       # 8??遺꾪븷留ㅼ닔
                "split_ratios": [0.062, 0.134, 0.118, 0.148, 0.150, 0.182, 0.186, 0.020]
            }
        
        # ?ъ???愿由?(?뚯감蹂?
        self.positions = []  # [{"round": 1, "buy_date": date, "buy_price": price, "shares": shares, "amount": amount}]
        self.current_round = 1
        self.available_cash = initial_capital
        

        # ?ъ옄?먭툑 愿由?(10嫄곕옒?쇰쭏???낅뜲?댄듃)
        self.current_investment_capital = initial_capital
        self.trading_days_count = 0  # 嫄곕옒??移댁슫??        
        # ?쒕뱶利앹븸 愿由?        self.seed_increases = []  # [{"date": "2025-10-21", "amount": 31000, "description": "?쒕뱶利앹븸"}]
        
        # ?몄뀡 ?곹깭: ?ъ슜???낅젰 ?쒖옉??(?뚯씪 ????놁쓬)
        self.session_start_date: Optional[str] = None
        
        # ?뚯뒪?몄슜 ?ㅻ뒛 ?좎쭨 ?ㅻ쾭?쇱씠??(YYYY-MM-DD)
        self.test_today_override: Optional[str] = None

    def set_test_today(self, date_str: Optional[str]):
        """?뚯뒪?몄슜 ?ㅻ뒛 ?좎쭨 ?ㅼ젙/?댁젣. None ?먮뒗 鍮덈Ц?먮㈃ ?댁젣."""
        if not date_str:
            self.test_today_override = None
            print("?㎦ ?뚯뒪???좎쭨 ?댁젣??(?ㅼ젣 ?ㅻ뒛 ?좎쭨 ?ъ슜)")
            return
        try:
            # ?뺤떇 寃利?            _ = datetime.strptime(date_str, "%Y-%m-%d")
            self.test_today_override = date_str
            print(f"?㎦ ?뚯뒪???좎쭨 ?ㅼ젙: {date_str}")
        except ValueError:
            print("???좎쭨 ?뺤떇???щ컮瑜댁? ?딆뒿?덈떎. YYYY-MM-DD ?뺤떇?쇰줈 ?낅젰?댁＜?몄슂.")

    def get_today_date(self) -> datetime:
        """?ㅻ쾭?쇱씠?쒕맂 ?ㅻ뒛 ?좎쭨(?먯젙)瑜?datetime?쇰줈 諛섑솚."""
        if self.test_today_override:
            d = datetime.strptime(self.test_today_override, "%Y-%m-%d").date()
            return datetime(d.year, d.month, d.day)
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    def add_seed_increase(self, date: str, amount: float, description: str = ""):
        """?쒕뱶利앹븸 異붽?"""
        seed_increase = {
            "date": date,
            "amount": amount,
            "description": description
        }
        self.seed_increases.append(seed_increase)
        # ?좎쭨?쒖쑝濡??뺣젹
        self.seed_increases.sort(key=lambda x: x["date"])
        print(f"?쒕뱶利앹븸 異붽?: {date} - ${amount:,.0f} ({description})")
    
    def get_seed_increases_for_date(self, date: str) -> List[Dict]:
        """?뱀젙 ?좎쭨???쒕뱶利앹븸 紐⑸줉 諛섑솚"""
        return [si for si in self.seed_increases if si["date"] == date]

    def simulate_from_start_to_today(self, start_date: str, quiet: bool = True) -> Dict:
        """
        ?쒖옉?쇰???理쒓렐 嫄곕옒?쇨퉴吏 ?쒕??덉씠???섑뻾?섏뿬 ?꾩옱 ?ъ????곹깭瑜?留욎텣??
        Args:
            start_date: YYYY-MM-DD ?뺤떇 ?쒖옉??            quiet: 異쒕젰 ?듭젣 ?щ? (湲곕낯 True)
        Returns:
            Dict: run_backtest ?붿빟 寃곌낵
        """
        # 罹먯떆 ???앹꽦 (?쒖옉??+ ?ъ옄湲?+ ?뚯뒪?몃궇吏?
        cache_key = f"{start_date}_{self.initial_capital}_{self.test_today_override or 'real'}"
        
        # 罹먯떆??寃곌낵媛 ?덇퀬 2遺??대궡硫??ъ궗??        if cache_key in self._simulation_cache:
            cached_result, cache_time = self._simulation_cache[cache_key]
            if (datetime.now() - cache_time).seconds < 30:  # 30珥?罹먯떆
                print(f"???쒕??덉씠??寃곌낵 罹먯떆?먯꽌 濡쒕뱶 ({start_date})")
                return cached_result
        
        latest_trading_day = self.get_latest_trading_day()
        end_date_str = latest_trading_day.strftime('%Y-%m-%d')

        # ?쒖옉?쇱씠 理쒖떊 嫄곕옒?쇰낫????뒗 寃쎌슦(?? ?쒖옉???ㅻ뒛, ??誘몃쭏媛먯쑝濡?理쒖떊 嫄곕옒???댁젣)
        # 諛깊뀒?ㅽ듃瑜?嫄대꼫?곌퀬 ?ы듃?대━?ㅻ쭔 珥덇린?뷀븯???뱀씪 異붿쿇??媛?ν븯?꾨줉 泥섎━
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = latest_trading_day.date()
            if start_dt > end_dt:
                if not quiet:
                    print(f"?좑툘 諛깊뀒?ㅽ듃 ?ㅽ궢: ?쒖옉??{start_dt})??理쒖떊 嫄곕옒??{end_dt})蹂대떎 ??쓬")
                self.reset_portfolio()
                minimal_result = {"skipped": True, "start_date": start_date, "end_date": end_date_str}
                self._simulation_cache[cache_key] = (minimal_result, datetime.now())
                return minimal_result
        except Exception:
            pass
        
        if quiet:
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = self.run_backtest(start_date, end_date_str)
        else:
            result = self.run_backtest(start_date, end_date_str)
        
        # 罹먯떆?????        self._simulation_cache[cache_key] = (result, datetime.now())
        
        return result
    
    def is_market_closed(self, date: datetime) -> bool:
        """
        二쇱떇 ?쒖옣 ?댁옣???뺤씤
        Args:
            date: ?뺤씤???좎쭨
        Returns:
            bool: ?댁옣?쇱씠硫?True, 嫄곕옒?쇱씠硫?False
        """
        # 二쇰쭚 ?뺤씤 (?좎슂??5, ?쇱슂??6)
        if date.weekday() >= 5:
            return True
        
        # ?댁옣???뺤씤
        date_str = date.strftime("%Y-%m-%d")
        if date_str in self.us_holidays:
            return True
        
        return False

    def _is_dst_approx(self, dt_utc: datetime) -> bool:
        """誘멸뎅 ?쒕㉧???????먮퀎 (3~10?붿? DST?쇨퀬 媛??."""
        return 3 <= dt_utc.month <= 10

    def get_us_eastern_now(self) -> datetime:
        """誘멸뎅 ?숇??쒓컙(ET) ?꾩옱?쒓컖 (DST ?⑥닚 媛??."""
        if self.test_today_override:
            # ?뚯뒪???좎쭨 湲곗? ?뺤삤(12:00) ET濡?媛꾩＜
            d = datetime.strptime(self.test_today_override, "%Y-%m-%d")
            return datetime(d.year, d.month, d.day, 12, 0, 0)
        now_utc = datetime.utcnow()
        offset_hours = 4 if self._is_dst_approx(now_utc) else 5
        return now_utc - timedelta(hours=offset_hours)

    def is_regular_session_closed_now(self) -> bool:
        """?뺢퇋??09:30~16:00 ET) 湲곗??쇰줈 ?ㅻ뒛 ?μ씠 留덇컧?먮뒗吏 ?щ?."""
        et_now = self.get_us_eastern_now()
        # 嫄곕옒?쇱씠 ?꾨땲硫?二쇰쭚/?댁옣?? '?대? 留덇컧'?쇰줈 媛꾩＜
        if not self.is_trading_day(et_now):
            return True
        # 16:00 ET ?댄썑硫?留덇컧
        return et_now.hour > 16 or (et_now.hour == 16 and et_now.minute >= 0)
    
    def get_latest_trading_day(self) -> datetime:
        """
        媛??理쒓렐 嫄곕옒??李얘린 (誘멸뎅 ?쒖옣 留덇컧 湲곗?)
        Returns:
            datetime: 媛??理쒓렐 嫄곕옒??        """
        # 誘멸뎅 ?쒖옣??留덇컧?섏뿀?붿? ?뺤씤
        if self.is_regular_session_closed_now():
            # ?쒖옣??留덇컧?섏뿀?쇰㈃ ?ㅻ뒛??理쒖떊 嫄곕옒?쇰줈 ?ъ슜
            today = self.get_today_date()
            print(f"?뱤 誘멸뎅 ?쒖옣 留덇컧??- ?ㅻ뒛??理쒖떊 嫄곕옒?쇰줈 ?ъ슜: {today.strftime('%Y-%m-%d')}")
            return today
        else:
            # ?쒖옣???꾩쭅 ?대젮?덉쑝硫??댁젣瑜?理쒖떊 嫄곕옒?쇰줈 ?ъ슜
            yesterday = self.get_today_date() - timedelta(days=1)
            print(f"?뱤 誘멸뎅 ?쒖옣 媛쒖옣 以?- ?댁젣瑜?理쒖떊 嫄곕옒?쇰줈 ?ъ슜: {yesterday.strftime('%Y-%m-%d')}")
            return yesterday
        
        # ?곗씠?곕? 媛?몄삱 ???녿뒗 寃쎌슦 湲곗〈 濡쒖쭅 ?ъ슜
        today = self.get_today_date()
        while self.is_market_closed(today):
            today -= timedelta(days=1)
        print(f"?좑툘 ?곗씠???놁쓬, 怨꾩궛??理쒖떊 嫄곕옒?? {today.strftime('%Y-%m-%d')}")
        return today
        
    def get_stock_data(self, symbol: str, period: str = "1mo") -> Optional[pd.DataFrame]:
        """
        Yahoo Finance API瑜??듯빐 二쇱떇 ?곗씠??媛?몄삤湲?(罹먯떛 ?곸슜)
        Args:
            symbol: 二쇱떇 ?щ낵 (?? "SOXL", "QQQ")
            period: 湲곌컙 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
        Returns:
            DataFrame: 二쇱떇 ?곗씠??(Date, Open, High, Low, Close, Volume)
        """
        # 罹먯떆 ???앹꽦
        cache_key = f"{symbol}_{period}"
        current_time = datetime.now()
        
        # 罹먯떆???곗씠?곌? ?덇퀬 1遺??대궡硫??ъ궗??(???먯＜ ?낅뜲?댄듃)
        if cache_key in self._stock_data_cache:
            cached_data, cache_time = self._stock_data_cache[cache_key]
            if (current_time - cache_time).seconds < 60:  # 1遺?罹먯떆
                print(f"?뱤 {symbol} ?곗씠??罹먯떆?먯꽌 濡쒕뱶 (湲곌컙: {period})")
                return cached_data
        
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            # 15y媛 吏?먮릺吏 ?딆쑝硫?10y濡??泥?            if period == "15y":
                # 癒쇱? 15y ?쒕룄, ?ㅽ뙣?섎㈃ 10y濡??泥?                params_list = [{'range': '15y', 'interval': '1d'}, {'range': '10y', 'interval': '1d'}]
            else:
                params_list = [{'range': period, 'interval': '1d'}]
            
            print(f"[INFO] {symbol} ?곗씠??媛?몄삤??以?..")
            
            # ?щ윭 ?뚮씪誘명꽣 ?쒕룄
            for i, params in enumerate(params_list):
                try:
                    print(f"   ?쒕룄 {i+1}/{len(params_list)}: range={params['range']}")
                    response = requests.get(url, headers=headers, params=params, timeout=15)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                            result = data['chart']['result'][0]
                            
                            if 'timestamp' in result and 'indicators' in result:
                                timestamps = result['timestamp']
                                quote_data = result['indicators']['quote'][0]
                                
                                # DataFrame ?앹꽦
                                df_data = {
                                    'Date': [datetime.fromtimestamp(ts) for ts in timestamps],
                                    'Open': quote_data.get('open', [None] * len(timestamps)),
                                    'High': quote_data.get('high', [None] * len(timestamps)),
                                    'Low': quote_data.get('low', [None] * len(timestamps)),
                                    'Close': quote_data.get('close', [None] * len(timestamps)),
                                    'Volume': quote_data.get('volume', [None] * len(timestamps))
                                }
                                
                                df = pd.DataFrame(df_data)
                                df = df.dropna()  # NaN 媛??쒓굅
                                df.set_index('Date', inplace=True)
                                
                                # 罹먯떆?????                                self._stock_data_cache[cache_key] = (df, current_time)
                                
                                print(f"[SUCCESS] {symbol} ?곗씠??媛?몄삤湲??깃났! ({len(df)}?쇱튂 ?곗씠??")
                                return df
                            else:
                                print(f"   ??李⑦듃 ?곗씠??援ъ“ ?ㅻ쪟")
                        else:
                            print(f"   ??李⑦듃 寃곌낵 ?놁쓬")
                    else:
                        print(f"   ??HTTP ?ㅻ쪟: {response.status_code}")
                        
                except Exception as e:
                    print(f"   ???붿껌 ?ㅻ쪟: {e}")
                    
                # 留덉?留??쒕룄媛 ?꾨땲硫?怨꾩냽
                if i < len(params_list) - 1:
                    print(f"   ?ㅼ쓬 ?뚮씪誘명꽣濡??ъ떆??..")
            
            print(f"??{symbol} 紐⑤뱺 ?뚮씪誘명꽣 ?쒕룄 ?ㅽ뙣")
            return None
                
        except Exception as e:
            print(f"??{symbol} ?곗씠??媛?몄삤湲??ㅻ쪟: {e}")
            return None
    
    def get_intraday_last_price(self, symbol: str) -> Optional[Tuple[datetime, float]]:
        """
        遺꾨큺(1m) 湲곗??쇰줈 ?ㅻ뒛??理쒖떊 媛寃⑹쓣 媛?몄삩??
        Returns:
            Optional[Tuple[datetime, float]]: (留덉?留??쒓컖, 留덉?留?媛寃?
        """
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            params = {'range': '1d', 'interval': '1m'}
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code != 200:
                return None
            data = response.json()
            result = data.get('chart', {}).get('result', [])
            if not result:
                return None
            result0 = result[0]
            timestamps = result0.get('timestamp') or []
            indicators = result0.get('indicators', {})
            quotes = indicators.get('quote', [])
            if not timestamps or not quotes:
                return None
            closes = quotes[0].get('close') or []
            # 留덉?留??좏슚 媛?李얘린
            for ts, close_val in reversed(list(zip(timestamps, closes))):
                if close_val is not None:
                    ts_dt = datetime.utcfromtimestamp(ts)
                    return ts_dt, float(close_val)
            return None
        except Exception:
            return None


    def calculate_weekly_rsi(self, df: pd.DataFrame, window: int = 14) -> float:
        """

        二쇨컙 RSI 怨꾩궛 (?쒓났???⑥닔 諛⑹떇 ?곸슜)
        Args:
            df: ?쇱씪 二쇨? ?곗씠??
            window: RSI 怨꾩궛 湲곌컙 (湲곕낯媛? 14)
        Returns:
            float: 理쒖떊 二쇨컙 RSI 媛?        """
        try:
            # 二쇨컙 ?곗씠?곕줈 蹂??(湲덉슂??湲곗?)
            weekly_df = df.resample('W-FRI').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
            

            # ?붾쾭源? 二쇨컙 ?곗씠???뺤씤
            print(f"   二쇨컙 ?곗씠??蹂??寃곌낵:")
            print(f"   - 湲곌컙: {weekly_df.index[0].strftime('%Y-%m-%d')} ~ {weekly_df.index[-1].strftime('%Y-%m-%d')}")
            print(f"   - 二쇨컙 ?곗씠???? {len(weekly_df)}二?)
            print(f"   - 理쒓렐 5二?醫낃?: {weekly_df['Close'].tail(5).values}")
            
            if len(weekly_df) < window + 1:
                print(f"??二쇨컙 RSI 怨꾩궛???꾪븳 ?곗씠??遺議?(?꾩슂: {window+1}二? ?꾩옱: {len(weekly_df)}二?")
                return None
            

            # ?쒓났???⑥닔 諛⑹떇?쇰줈 RSI 怨꾩궛
            delta = weekly_df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            

            # ?붾쾭源??뺣낫 異쒕젰
            latest_rsi = rsi.iloc[-1]
            print(f"?뱢 QQQ 二쇨컙 RSI: {latest_rsi:.2f}")

            print(f"   ?곗씠??湲곌컙: {weekly_df.index[0].strftime('%Y-%m-%d')} ~ {weekly_df.index[-1].strftime('%Y-%m-%d')}")
            print(f"   二쇨컙 ?곗씠???? {len(weekly_df)}二?)
            print(f"   理쒓렐 3媛?RSI: {[f'{x:.2f}' if not np.isnan(x) else 'NaN' for x in rsi.tail(3).values]}")
            
            # ?곸꽭 怨꾩궛 怨쇱젙 異쒕젰
            print(f"   理쒓렐 3媛?怨꾩궛 怨쇱젙:")
            for i in range(-3, 0):
                if i + len(weekly_df) >= 0:
                    date_str = weekly_df.index[i].strftime('%Y-%m-%d')
                    delta_val = delta.iloc[i]
                    gain_val = gain.iloc[i]
                    loss_val = loss.iloc[i]
                    rs_val = rs.iloc[i]
                    rsi_val = rsi.iloc[i]
                    print(f"   {date_str}: delta={delta_val:+.4f}, gain={gain_val:.4f}, loss={loss_val:.4f}, RS={rs_val:.4f}, RSI={rsi_val:.2f}")
            
            return latest_rsi
            
        except Exception as e:
            print(f"??二쇨컙 RSI 怨꾩궛 ?ㅻ쪟: {e}")
            return None
    

    def determine_mode(self, current_rsi: float, prev_rsi: float, prev_mode: str = "SF") -> str:
        """
        援ш??ㅽ봽?덈뱶?쒗듃 ?섏떇 湲곕컲 紐⑤뱶 ?먮떒
        Args:
            current_rsi: 1二쇱쟾 RSI (?꾩옱 ?곸슜??RSI)
            prev_rsi: 2二쇱쟾 RSI (?댁쟾 RSI)
            prev_mode: ?꾩＜ 紐⑤뱶
        Returns:
            str: "SF" (?덉쟾紐⑤뱶) ?먮뒗 "AG" (怨듭꽭紐⑤뱶)
        """
        # RSI 媛믪씠 None??寃쎌슦 諛깊뀒?ㅽ똿 以묐떒
        if current_rsi is None or prev_rsi is None:
            raise ValueError(f"RSI ?곗씠?곌? ?놁뒿?덈떎. current_rsi: {current_rsi}, prev_rsi: {prev_rsi}")
        
        # ?덉쟾紐⑤뱶 議곌굔??(OR濡??곌껐)
        safe_conditions = [
            # RSI > 65 ?곸뿭?먯꽌 ?섎씫 (2二쇱쟾 RSI > 65?닿퀬 2二쇱쟾 > 1二쇱쟾)
            prev_rsi > 65 and prev_rsi > current_rsi,
            
            # 40 < RSI < 50?먯꽌 ?섎씫 (2二쇱쟾 RSI媛 40~50 ?ъ씠?닿퀬 2二쇱쟾 > 1二쇱쟾)
            40 < prev_rsi < 50 and prev_rsi > current_rsi,
            
            # RSI媛 50 諛묒쑝濡??섎씫 (2二쇱쟾 >= 50?닿퀬 1二쇱쟾 < 50)
            prev_rsi >= 50 and current_rsi < 50
        ]
        
        # 怨듭꽭紐⑤뱶 議곌굔??(OR濡??곌껐)
        aggressive_conditions = [
            # RSI媛 50 ?꾨줈 ?곸듅 (2二쇱쟾 < 50?닿퀬 2二쇱쟾 < 1二쇱쟾?닿퀬 1二쇱쟾 > 50)
            prev_rsi < 50 and prev_rsi < current_rsi and current_rsi > 50,
            
            # 50 < RSI < 60?먯꽌 ?곸듅 (2二쇱쟾 RSI媛 50~60 ?ъ씠?닿퀬 2二쇱쟾 < 1二쇱쟾)
            50 < prev_rsi < 60 and prev_rsi < current_rsi,
            
            # RSI < 35 ?곸뿭?먯꽌 ?곸듅 (2二쇱쟾 < 35?닿퀬 2二쇱쟾 < 1二쇱쟾)
            prev_rsi < 35 and prev_rsi < current_rsi
        ]
        
        # ?덉쟾紐⑤뱶 議곌굔 ?뺤씤
        if any(safe_conditions):
            return "SF"
        
        # 怨듭꽭紐⑤뱶 議곌굔 ?뺤씤
        if any(aggressive_conditions):
            return "AG"
        
        # 議곌굔???놁쑝硫??꾩＜ 紐⑤뱶 ?좎?
        return prev_mode
    
    def update_mode(self, qqq_data: pd.DataFrame) -> str:
        """
        QQQ 二쇨컙 RSI 湲곕컲?쇰줈 紐⑤뱶 ?낅뜲?댄듃
        Args:
            qqq_data: QQQ 二쇨? ?곗씠??        Returns:
            str: ?낅뜲?댄듃??紐⑤뱶
        """
        try:
            # 二쇨컙 RSI 怨꾩궛
            current_rsi = self.calculate_weekly_rsi(qqq_data)
            if current_rsi is None:
                print("?좑툘 RSI 怨꾩궛 ?ㅽ뙣, ?꾩옱 紐⑤뱶 ?좎?")
                return self.current_mode
            
            # 珥덇린 紐⑤뱶媛 ?녿뒗 寃쎌슦 RSI 湲곗??쇰줈 寃곗젙
            if self.current_mode is None:
                # RSI 50??湲곗??쇰줈 珥덇린 紐⑤뱶 寃곗젙
                if current_rsi >= 50:
                    self.current_mode = "SF"  # ?덉쟾紐⑤뱶
                else:
                    self.current_mode = "AG"  # 怨듭꽭紐⑤뱶
                print(f"?렞 珥덇린 紐⑤뱶 寃곗젙: {self.current_mode} (RSI: {current_rsi:.2f})")
                return self.current_mode
            
            # ?꾩＜ RSI 怨꾩궛 (二쇨컙 ?곗씠?곗뿉??
            weekly_df = qqq_data.resample('W-FRI').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()
            
            if len(weekly_df) < 15:
                print("?좑툘 二쇨컙 ?곗씠??遺議? ?꾩옱 紐⑤뱶 ?좎?")
                return self.current_mode
            
            # ?쒓났???⑥닔 諛⑹떇?쇰줈 ?꾩＜ RSI 怨꾩궛
            delta = weekly_df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            
            prev_rsi = rsi.iloc[-2] if len(rsi) >= 2 else 50.0
            
            # 紐⑤뱶 寃곗젙
            new_mode = self.determine_mode(current_rsi, prev_rsi, self.current_mode)
            
            if new_mode != self.current_mode:
                print(f"?봽 紐⑤뱶 ?꾪솚: {self.current_mode} ??{new_mode}")
                print(f"   ?꾩옱 RSI: {current_rsi:.2f}, ?꾩＜ RSI: {prev_rsi:.2f}")
                self.current_mode = new_mode
            else:
                print(f"?뱤 ?꾩옱 紐⑤뱶 ?좎?: {self.current_mode} (RSI: {current_rsi:.2f})")
            
            return self.current_mode
            
        except Exception as e:
            print(f"??紐⑤뱶 ?낅뜲?댄듃 ?ㅻ쪟: {e}")
            return self.current_mode
    
    def get_current_config(self) -> Dict:
        """?꾩옱 紐⑤뱶???곕Ⅸ ?ㅼ젙 諛섑솚"""
        return self.sf_config if self.current_mode == "SF" else self.ag_config
    
    def calculate_buy_sell_prices(self, current_price: float) -> Tuple[float, float]:
        """
        留ㅼ닔/留ㅻ룄 媛寃?怨꾩궛
        Args:
            current_price: ?꾩옱 二쇨? (?꾩씪 醫낃?)
        Returns:
            Tuple[float, float]: (留ㅼ닔媛寃? 留ㅻ룄媛寃?
        """
        config = self.get_current_config()
        

        # 留ㅼ닔媛: ?꾩씪 醫낃? ?鍮??곸듅??媛寃?(留ㅼ닔媛 > 醫낃?)
        buy_price = current_price * (1 + config["buy_threshold"] / 100)

        # 留ㅻ룄媛: ?꾩씪 醫낃? ?鍮??곸듅??媛寃?(留ㅻ룄媛 < 醫낃?)
        sell_price = current_price * (1 + config["sell_threshold"] / 100)
        
        return buy_price, sell_price
    
    def calculate_position_size(self, round_num: int) -> float:
        """
        ?뚯감蹂?留ㅼ닔 湲덉븸 怨꾩궛
        Args:
            round_num: 留ㅼ닔 ?뚯감 (1遺???쒖옉)
        Returns:
            float: ?대떦 ?뚯감 留ㅼ닔 湲덉븸
        """
        config = self.get_current_config()
        
        if round_num <= len(config["split_ratios"]):
            ratio = config["split_ratios"][round_num - 1]
            # ?ъ옄?먭툑 ?ъ슜 (10嫄곕옒?쇰쭏??珥앹옄?곗쑝濡??낅뜲?댄듃??
            amount = self.current_investment_capital * ratio
            return amount
        else:
            return 0.0
    

    def calculate_stop_loss_date(self, buy_date: datetime, max_hold_days: int) -> str:
        """
        嫄곕옒??湲곗? ?먯젅?덉젙??怨꾩궛 (二쇰쭚 + 誘멸뎅利앹떆 ?댁옣???쒖쇅)
        Args:
            buy_date: 留ㅼ닔??            max_hold_days: 理쒕? 蹂댁쑀 嫄곕옒????        Returns:
            str: ?먯젅?덉젙??(MM.DD.(?붿씪) ?뺤떇)
        """
        try:
            # ?붿씪???쒓?濡?蹂??            weekdays_korean = ['??, '??, '??, '紐?, '湲?, '??, '??]
            
            # 嫄곕옒??湲곗??쇰줈 ?좎쭨 怨꾩궛 (二쇰쭚 + ?댁옣???쒖쇅)
            current_date = buy_date
            trading_days_count = 0
            
            while trading_days_count < max_hold_days:
                current_date += timedelta(days=1)
                
                # 嫄곕옒?쇱씤吏 ?뺤씤 (二쇰쭚???꾨땲怨??댁옣?쇱씠 ?꾨땶 寃쎌슦)
                if self.is_trading_day(current_date):
                    trading_days_count += 1
            
            weekday_korean = weekdays_korean[current_date.weekday()]
            return current_date.strftime(f"%m.%d.({weekday_korean})")
            
        except Exception as e:
            print(f"?좑툘 ?먯젅?덉젙??怨꾩궛 ?ㅻ쪟: {e}")
            # ?ㅻ쪟 ??湲곕낯媛?諛섑솚
            fallback_date = buy_date + timedelta(days=max_hold_days)
            weekday_korean = weekdays_korean[fallback_date.weekday()]
            return fallback_date.strftime(f"%m.%d.({weekday_korean})")
    
    def is_trading_day(self, date: datetime) -> bool:
        """
        ?대떦 ?좎쭨媛 嫄곕옒?쇱씤吏 ?뺤씤 (二쇰쭚 + 誘멸뎅利앹떆 ?댁옣???쒖쇅)
        Args:
            date: ?뺤씤???좎쭨
        Returns:
            bool: 嫄곕옒?쇱씠硫?True, ?꾨땲硫?False
        """
        # 二쇰쭚 ?뺤씤 (?좎슂??5, ?쇱슂??6)
        if date.weekday() >= 5:
            return False
        
        # 誘멸뎅利앹떆 ?댁옣???뺤씤
        date_str = date.strftime("%Y-%m-%d")
        if date_str in self.us_holidays:
            return False
        
        return True
    
    def can_buy_next_round(self) -> bool:
        """?ㅼ쓬 ?뚯감 留ㅼ닔 媛???щ? ?뺤씤"""
        config = self.get_current_config()
        
        # 理쒕? 遺꾪븷留ㅼ닔 ?잛닔 ?뺤씤
        if self.current_round > config["split_count"]:
            return False
        
        # ?쒕뱶 ?뺤씤
        next_amount = self.calculate_position_size(self.current_round)
        if self.available_cash < next_amount:
            return False
        
        return True
    
    def execute_buy(self, target_price: float, actual_price: float, current_date: datetime) -> bool:
        """
        留ㅼ닔 ?ㅽ뻾 (紐⑺몴媛 湲곗? ?섎웾?쇰줈 怨꾩궛?섏뿬 ?ㅼ젣媛??留ㅼ닔)
        Args:
            target_price: 紐⑺몴 留ㅼ닔媛 (?섎웾 怨꾩궛??
            actual_price: ?ㅼ젣 留ㅼ닔媛 (?뱀씪 醫낃?)
            current_date: 留ㅼ닔 ?좎쭨
        Returns:
            bool: 留ㅼ닔 ?깃났 ?щ?
        """
        if not self.can_buy_next_round():
            return False
        

        # 1?뚯떆??湲덉븸 怨꾩궛
        target_amount = self.calculate_position_size(self.current_round)
        
        # 紐⑺몴媛 湲곗??쇰줈 留ㅼ닔???섎웾 怨꾩궛
        target_shares = int(target_amount / target_price)  # 紐⑺몴媛 湲곗? ?섎웾
        
        if target_shares <= 0:
            return False
        
        # ?ㅼ젣 留ㅼ닔 湲덉븸 怨꾩궛 (紐⑺몴 ?섎웾 횞 ?ㅼ젣 媛寃?
        actual_amount = target_shares * actual_price
        
        # ?덉닔湲덉씠 遺議깊븳 寃쎌슦 ?덉닔湲덉쑝濡?留ㅼ닔 媛?ν븳 ?섎웾 ?ш퀎??        if actual_amount > self.available_cash:
            max_shares = int(self.available_cash / actual_price)
            if max_shares <= 0:
                return False
            actual_shares = max_shares
            actual_amount = actual_shares * actual_price
        else:
            actual_shares = target_shares
        
        # ?ъ???異붽?
        position = {
            "round": self.current_round,
            "buy_date": current_date,
            "buy_price": actual_price,  # ?ㅼ젣 留ㅼ닔媛
            "shares": actual_shares,    # ?ㅼ젣 留ㅼ닔 ?섎웾
            "target_price": target_price,  # 紐⑺몴媛 (李몄“??
            "amount": actual_amount,    # ?ㅼ젣 ?ъ옄湲덉븸
            "mode": self.current_mode
        }
        
        self.positions.append(position)

        self.available_cash -= actual_amount
        self.current_round += 1  # 留ㅼ닔 ?깃났 ?쒖뿉留??뚯감 利앷?
        

        print(f"??{self.current_round-1}?뚯감 留ㅼ닔 ?ㅽ뻾: {actual_shares}二?@ ${actual_price:.2f} (紐⑺몴媛: ${target_price:.2f}, ?ㅼ젣?ъ옄: ${actual_amount:,.0f})")
        
        return True
    
    def reconcile_positions_with_close_history(self, soxl_data: pd.DataFrame):
        """
        怨쇨굅 醫낃?媛 留ㅻ룄 紐⑺몴媛瑜??곗튂???ъ??섏쓣 蹂댁젙?섏뿬 ?먮룞 留ㅻ룄 泥섎━?쒕떎.
        LOC 留ㅻ룄 ?뱀꽦??醫낃?媛 紐⑺몴媛 ?댁긽?대㈃ 泥닿껐?섎뒗 寃껋쓣 諛섏쁺?섍린 ?꾪븿.
        Args:
            soxl_data (DataFrame): 理쒓렐 SOXL ?쇰퀎 ?곗씠??(Close ?꾩닔)
        """
        if not self.positions or soxl_data is None or len(soxl_data) == 0:
            return

        sold_rounds = []
        # 由ъ뒪??蹂듭궗蹂몄쓣 ?ъ슜?섏뿬 諛섎났 以??덉쟾?섍쾶 ?쒓굅
        for position in list(self.positions):
            buy_date = position["buy_date"]

            # 留ㅼ닔???댄썑(?꾧꺽?섍쾶 珥덇낵) ?곗씠?곕쭔 ?뺤씤
            future_data = soxl_data[soxl_data.index > buy_date]
            if future_data.empty:
                continue

            position_config = self.sf_config if position["mode"] == "SF" else self.ag_config
            target_price = position["buy_price"] * (1 + position_config["sell_threshold"] / 100)

            hit_rows = future_data[future_data["Close"] >= target_price]
            if hit_rows.empty:
                continue

            sell_row = hit_rows.iloc[0]
            sell_date = sell_row.name
            sell_close = sell_row["Close"]

            proceeds = position["shares"] * sell_close
            profit = proceeds - position["amount"]
            profit_rate = (profit / position["amount"]) * 100 if position["amount"] else 0.0

            self.positions.remove(position)
            self.available_cash += proceeds
            sold_rounds.append(position["round"])

            print("?㎨ 怨쇨굅 醫낃? 留ㅻ룄 蹂댁젙 ?ㅽ뻾")
            print(f"   - ?뚯감: {position['round']}?뚯감")
            print(f"   - 留ㅼ닔?? {buy_date.strftime('%Y-%m-%d')} | 留ㅼ닔媛: ${position['buy_price']:.2f}")
            print(f"   - 紐⑺몴媛: ${target_price:.2f}")
            print(f"   - sell_date: {sell_date.strftime('%Y-%m-%d')} | 醫낃?: ${sell_close:.2f}")
            print(f"   - ?ㅽ쁽?먯씡: ${profit:,.0f} ({profit_rate:+.2f}%)")

        if sold_rounds:
            sold_count = len(sold_rounds)
            self.current_round = max(1, self.current_round - sold_count)
            print(f"?봽 蹂댁젙 ??current_round: {self.current_round} (珥?{sold_count}媛??뚯감 蹂댁젙 留ㅻ룄)")

    def check_sell_conditions(self, row: pd.Series, current_date: datetime, prev_close: float) -> List[Dict]:
        """
        留ㅻ룄 議곌굔 ?뺤씤
        Args:
            row: ?뱀씪 二쇨? ?곗씠??(Open, High, Low, Close)
            current_date: ?꾩옱 ?좎쭨
            prev_close: ?꾩씪 醫낃?
        Returns:
            List[Dict]: 留ㅻ룄???ъ???由ъ뒪??        """
        sell_positions = []
        
        # ?붾쾭源? 蹂댁쑀 ?ъ??????뺤씤
        print(f"?뵇 留ㅻ룄 議곌굔 ?뺤씤: 蹂댁쑀 ?ъ???{len(self.positions)}媛?)
        
        for position in self.positions:
            buy_date = position["buy_date"]

            # 嫄곕옒??湲곗??쇰줈 蹂댁쑀湲곌컙 怨꾩궛
            hold_days = 0
            temp_date = buy_date
            while temp_date < current_date:
                temp_date += timedelta(days=1)
                if self.is_trading_day(temp_date):
                    hold_days += 1
            
            # ?대떦 ?ъ??섏쓽 紐⑤뱶 ?ㅼ젙 媛?몄삤湲?            position_config = self.sf_config if position["mode"] == "SF" else self.ag_config
            

            # ?대떦 ?ъ??섏쓽 留ㅼ닔泥닿껐媛 湲곗??쇰줈 留ㅻ룄媛 怨꾩궛
            position_buy_price = position["buy_price"]
            sell_price = position_buy_price * (1 + position_config["sell_threshold"] / 100)
            
            # ?붾쾭源? 留ㅻ룄 議곌굔 ?곸꽭 ?뺣낫
            daily_close = row['Close']
            print(f"   ?벀 {position['round']}?뚯감: 留ㅼ닔媛 ${position_buy_price:.2f} ??留ㅻ룄紐⑺몴媛 ${sell_price:.2f} (?꾩옱媛 ${daily_close:.2f})")
            print(f"      蹂댁쑀湲곌컙: {hold_days}??(理쒕?: {position_config['max_hold_days']}??")
            
            # 1. LOC 留ㅻ룄 議곌굔: 醫낃?媛 留ㅻ룄紐⑺몴媛???꾨떖?덉쓣 ??(醫낃? >= 留ㅻ룄紐⑺몴媛)
            if daily_close >= sell_price:
                print(f"      ??留ㅻ룄 議곌굔 1: 紐⑺몴媛 ?꾨떖 (${daily_close:.2f} >= ${sell_price:.2f})")
                sell_positions.append({
                    "position": position,
                    "reason": "紐⑺몴媛 ?꾨떖",
                    "sell_price": daily_close  # 醫낃???留ㅻ룄
                })
            
            # 2. 蹂댁쑀湲곌컙 珥덇낵 ??留ㅻ룄 (?뱀씪 醫낃???留ㅻ룄)
            elif hold_days > position_config["max_hold_days"]:
                print(f"      ??留ㅻ룄 議곌굔 2: 蹂댁쑀湲곌컙 珥덇낵 ({hold_days}??> {position_config['max_hold_days']}??")
                sell_positions.append({
                    "position": position,
                    "reason": f"蹂댁쑀湲곌컙 珥덇낵 ({hold_days+1}??",
                    "sell_price": row['Close']  # 醫낃???留ㅻ룄
                })
        
        # ?붾쾭源? 留ㅻ룄 異붿쿇 寃곌낵
        if sell_positions:
            print(f"??留ㅻ룄 異붿쿇 {len(sell_positions)}嫄??앹꽦??)
        else:
            print("??留ㅻ룄 異붿쿇 ?놁쓬")
        
        return sell_positions
    

    def execute_sell(self, sell_info: Dict) -> tuple:
        """
        留ㅻ룄 ?ㅽ뻾
        Args:
            sell_info: 留ㅻ룄 ?뺣낫
        Returns:

            tuple: (留ㅻ룄 ?섏씡湲? 留ㅻ룄???뚯감)
        """
        position = sell_info["position"]
        sell_price = sell_info["sell_price"]

        sold_round = position["round"]
        
        proceeds = position["shares"] * sell_price
        profit = proceeds - position["amount"]
        profit_rate = (profit / position["amount"]) * 100
        
        # ?ъ????쒓굅
        self.positions.remove(position)
        self.available_cash += proceeds
        

        print(f"??{sold_round}?뚯감 留ㅻ룄 ?ㅽ뻾: {position['shares']}二?@ ${sell_price:.2f}")
        print(f"   留ㅻ룄 ?ъ쑀: {sell_info['reason']}")
        print(f"   ?섏씡: ${profit:,.0f} ({profit_rate:+.2f}%)")
        

        return proceeds, sold_round
    
    def get_daily_recommendation(self) -> Dict:
        """
        ?쇱씪 留ㅻℓ 異붿쿇 ?앹꽦
        Returns:
            Dict: 留ㅻℓ 異붿쿇 ?뺣낫
        """
        print("=" * 60)
        print("?? SOXL ??명닾???쇱씪 留ㅻℓ 異붿쿇")
        print("=" * 60)
        
        # ?꾩옱 ?곹깭瑜?理쒖떊?쇰줈 ?낅뜲?댄듃 (?쒖옉?쇰????꾩옱源뚯? ?쒕??덉씠??
        if self.session_start_date:
            print("?봽 ?몃젅?대뜑 ?곹깭瑜?理쒖떊?쇰줈 ?낅뜲?댄듃 以?..")
            sim_result = self.simulate_from_start_to_today(self.session_start_date, quiet=True)
            if "error" in sim_result:
                return {"error": f"?곹깭 ?낅뜲?댄듃 ?ㅽ뙣: {sim_result['error']}"}

        # ?쒖옣 ?댁옣???뺤씤 (?뚯뒪???좎쭨 ?ㅻ쾭?쇱씠??怨좊젮)
        today = self.get_today_date()
        is_market_closed = self.is_market_closed(today)
        
        if is_market_closed:
            latest_trading_day = self.get_latest_trading_day()
            if today.weekday() >= 5:
                print(f"?뱟 二쇰쭚?낅땲?? 理쒖떊 嫄곕옒??{latest_trading_day.strftime('%Y-%m-%d')}) ?곗씠?곕? ?ъ슜?⑸땲??")
            else:
                print(f"?뱟 ?댁옣?쇱엯?덈떎. 理쒖떊 嫄곕옒??{latest_trading_day.strftime('%Y-%m-%d')}) ?곗씠?곕? ?ъ슜?⑸땲??")
        
        # 1. SOXL ?곗씠??媛?몄삤湲?        soxl_data = self.get_stock_data("SOXL", "1mo")
        if soxl_data is None:
            return {"error": "SOXL ?곗씠?곕? 媛?몄삱 ???놁뒿?덈떎."}
        
        # 2. QQQ ?곗씠??媛?몄삤湲?(二쇨컙 RSI 怨꾩궛??
        qqq_data = self.get_stock_data("QQQ", "6mo")  # 異⑸텇???곗씠???뺣낫
        if qqq_data is None:
            return {"error": "QQQ ?곗씠?곕? 媛?몄삱 ???놁뒿?덈떎."}

        # 3. 怨쇨굅 醫낃? 湲곕컲 ?ъ???蹂댁젙 (LOC 留ㅻ룄)
        self.reconcile_positions_with_close_history(soxl_data)

        # 4. QQQ 二쇨컙 RSI 湲곕컲 紐⑤뱶 ?먮룞 ?꾪솚
        self.update_mode(qqq_data)
        
        # QQQ 二쇨컙 RSI 怨꾩궛 (?쒖떆??
        weekly_rsi = self.calculate_weekly_rsi(qqq_data)
        if weekly_rsi is None:
            return {"error": "QQQ 二쇨컙 RSI瑜?怨꾩궛?????놁뒿?덈떎."}
        

        # 5. 理쒖떊 SOXL 媛寃??뺣낫 (理쒖냼 2???곗씠???꾩슂)
        if len(soxl_data) < 2:
            return {"error": "?곗씠?곌? 遺議깊빀?덈떎. 理쒖냼 2?쇱쓽 ?곗씠?곌? ?꾩슂?⑸땲??"}
        
        latest_soxl = soxl_data.iloc[-1]
        current_price = latest_soxl['Close']
        current_date = soxl_data.index[-1]

        # ?꾩씪 醫낃? 怨꾩궛
        # - ?곗씠?곗쓽 留덉?留??좎쭨媛 ?ㅻ뒛蹂대떎 ?댁쟾(二쇰쭚/?댁옣/?μ쟾)??寃쎌슦: 留덉?留?醫낃?瑜?湲곗??쇰줈 留ㅼ닔媛 怨꾩궛
        # - ?곗씠?곗쓽 留덉?留??좎쭨媛 ?ㅻ뒛(?대? ?ㅻ뒛 醫낃?媛 議댁옱)??寃쎌슦: 洹??꾨궇 醫낃?瑜?湲곗??쇰줈 怨꾩궛
        last_data_date = current_date.date()
        today_date = today.date()  # ?뚯뒪???좎쭨 ?ㅻ쾭?쇱씠??怨좊젮
        if last_data_date < today_date:
            # 理쒖떊 嫄곕옒??醫낃?瑜??꾩씪 醫낃?濡?媛꾩＜
            prev_close = soxl_data.iloc[-1]['Close']
            prev_close_basis_date = soxl_data.index[-1].strftime("%Y-%m-%d")
            display_date = today_date.strftime("%Y-%m-%d")  # ?μ쨷/?댁옣?쇱뿉???붾㈃ ?좎쭨???ㅻ뒛濡??쒖떆
        else:
            # ?ㅻ뒛 醫낃?媛 ?ы븿?섏뼱 ?덉쑝誘濡?諛붾줈 ?꾨궇 醫낃? ?ъ슜
            prev_close = soxl_data.iloc[-2]['Close']
            prev_close_basis_date = soxl_data.index[-2].strftime("%Y-%m-%d")
            display_date = current_date.strftime("%Y-%m-%d")
        
        # 6. 留ㅼ닔/留ㅻ룄 媛寃?怨꾩궛

        buy_price, sell_price = self.calculate_buy_sell_prices(prev_close)
        
        # 7. 留ㅻ룄 議곌굔 ?뺤씤

        sell_recommendations = self.check_sell_conditions(latest_soxl, current_date, prev_close)
        
        # 8. 留ㅼ닔 議곌굔 ?뺤씤
        can_buy = self.can_buy_next_round()
        next_buy_amount = self.calculate_position_size(self.current_round) if can_buy else 0
        
        # 9. ?ы듃?대━???꾪솴
        total_position_value = sum([pos["shares"] * current_price for pos in self.positions])
        total_invested = sum([pos["amount"] for pos in self.positions])
        unrealized_pnl = total_position_value - total_invested
        
        recommendation = {
            "date": display_date,  # ?붾㈃ ?쒖떆???좎쭨 (媛?ν븯硫??ㅻ뒛)
            "basis_date": prev_close_basis_date,  # 留ㅼ닔媛 怨꾩궛???ъ슜??湲곗? 醫낃????좎쭨
            "mode": self.current_mode,
            "qqq_weekly_rsi": weekly_rsi,
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
                "total_portfolio_value": self.available_cash + total_position_value
            }
        }
        
        return recommendation
    
    def print_recommendation(self, rec: Dict):
        """留ㅻℓ 異붿쿇 異쒕젰"""
        if "error" in rec:
            print(f"???ㅻ쪟: {rec['error']}")
            return
        
        print(f"?뱟 ?좎쭨: {rec['date']}")

        mode_name = "?덉쟾紐⑤뱶" if rec['mode'] == "SF" else "怨듭꽭紐⑤뱶"
        print(f"?렞 紐⑤뱶: {rec['mode']} ({mode_name})")
        print(f"?뱤 QQQ 二쇨컙 RSI: {rec['qqq_weekly_rsi']:.2f}")
        print(f"?뮥 SOXL ?꾩옱媛: ${rec['soxl_current_price']:.2f}")
        print()
        
        print("?뱥 ?ㅻ뒛??留ㅻℓ 異붿쿇:")
        print("-" * 40)
        
        # 留ㅼ닔 異붿쿇
        if rec['can_buy']:
            print(f"?윟 留ㅼ닔 異붿쿇: {rec['next_buy_round']}?뚯감")
            print(f"   留ㅼ닔媛: ${rec['buy_price']:.2f} (LOC 二쇰Ц)")
            print(f"   留ㅼ닔湲덉븸: ${rec['next_buy_amount']:,.0f}")
            shares = int(rec['next_buy_amount'] / rec['buy_price'])
            print(f"   留ㅼ닔二쇱떇?? {shares}二?)
        else:
            if self.current_round > self.get_current_config()["split_count"]:
                print("?뵶 留ㅼ닔 遺덇?: 紐⑤뱺 遺꾪븷留ㅼ닔 ?꾨즺")
            else:
                print("?뵶 留ㅼ닔 遺덇?: ?쒕뱶 遺議?)
        
        print()
        
        # 留ㅻ룄 異붿쿇
        if rec['sell_recommendations']:
            print(f"?뵶 留ㅻ룄 異붿쿇: {len(rec['sell_recommendations'])}嫄?)
            for sell_info in rec['sell_recommendations']:
                pos = sell_info['position']
                print(f"   {pos['round']}?뚯감 留ㅻ룄: {pos['shares']}二?@ ${sell_info['sell_price']:.2f}")
                print(f"   留ㅻ룄 ?ъ쑀: {sell_info['reason']}")
        else:
            # 蹂댁쑀 ?ъ??섏씠 ?덉쑝硫?留ㅻ룄 紐⑺몴媛 ?덈궡
            if self.positions:
                print("?뱥 蹂댁쑀 ?ъ???LOC 留ㅻ룄 紐⑺몴媛 ?덈궡:")
                for pos in self.positions:
                    config = self.sf_config if pos['mode'] == "SF" else self.ag_config
                    target_sell_price = pos['buy_price'] * (1 + config['sell_threshold'] / 100)
                    current_price = rec['soxl_current_price']
                    price_diff = target_sell_price - current_price
                    price_diff_pct = (price_diff / current_price) * 100
                    
                    print(f"   ?벀 {pos['round']}?뚯감: 紐⑺몴媛 ${target_sell_price:.2f}")
                    print(f"      留ㅼ닔媛: ${pos['buy_price']:.2f} | 蹂댁쑀: {pos['shares']}二?)
            else:
                print("?윞 留ㅻ룄 異붿쿇 ?놁쓬")
        
        print()
        print("?뮳 ?ы듃?대━???꾪솴:")
        print("-" * 40)
        portfolio = rec['portfolio']
        print(f"蹂댁쑀 ?ъ??? {portfolio['positions_count']}媛?)
        print(f"?ъ옄?먭툑: ${portfolio['total_invested']:,.0f}")
        print(f"?됯?湲덉븸: ${portfolio['total_position_value']:,.0f}")
        print(f"?됯??먯씡: ${portfolio['unrealized_pnl']:,.0f} ({(portfolio['unrealized_pnl']/portfolio['total_invested']*100) if portfolio['total_invested'] > 0 else 0:+.2f}%)")
        print(f"?꾧툑?붽퀬: ${portfolio['available_cash']:,.0f}")
        print(f"珥??먯궛: ${portfolio['total_portfolio_value']:,.0f}")
        
        print()
        print("?뱤 蹂댁쑀 ?ъ????곸꽭:")
        print("-" * 40)
        if self.positions:
            for pos in self.positions:
                hold_days = (datetime.now() - pos['buy_date']).days
                current_value = pos['shares'] * rec['soxl_current_price']
                pnl = current_value - pos['amount']
                pnl_rate = (pnl / pos['amount']) * 100
                
                print(f"{pos['round']}?뚯감: {pos['shares']}二?@ ${pos['buy_price']:.2f} ({hold_days}??蹂댁쑀)")
                print(f"        ?됯?: ${current_value:,.0f} | ?먯씡: ${pnl:,.0f} ({pnl_rate:+.2f}%)")
        else:
            print("蹂댁쑀 ?ъ????놁쓬")
    
    def reset_portfolio(self):
        """?ы듃?대━??珥덇린??(諛깊뀒?ㅽ똿??"""
        self.positions = []
        self.current_round = 1
        self.available_cash = self.initial_capital

        
        # ?ъ옄?먭툑 愿由?珥덇린??        self.current_investment_capital = self.initial_capital
        self.trading_days_count = 0
    
    def clear_cache(self):
        """罹먯떆 珥덇린??(?ㅼ젙 蹂寃????몄텧)"""
        self._stock_data_cache.clear()
        self._simulation_cache.clear()
        print("?㏏ 罹먯떆 珥덇린???꾨즺")
    
    def check_backtest_starting_state(self, start_date: str, rsi_ref_data: dict) -> dict:
        """
        諛깊뀒?ㅽ똿 ?쒖옉 ?쒖젏???곹깭 ?뺤씤
        Args:
            start_date: 諛깊뀒?ㅽ똿 ?쒖옉??            rsi_ref_data: RSI 李몄“ ?곗씠??        Returns:
            dict: ?쒖옉 ?쒖젏 ?곹깭 ?뺣낫
        """
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            
            # ?쒖옉?쇱쓽 二쇱감? RSI ?뺤씤
            days_until_friday = (4 - start_dt.weekday()) % 7
            if days_until_friday == 0 and start_dt.weekday() != 4:
                days_until_friday = 7
            start_week_friday = start_dt + timedelta(days=days_until_friday)
            
            # ?쒖옉 二쇱감??RSI? 紐⑤뱶 ?뺤씤
            start_week_rsi = self.get_rsi_from_reference(start_week_friday, rsi_ref_data)
            
            # 1二쇱쟾, 2二쇱쟾 RSI ?뺤씤
            prev_week_friday = start_week_friday - timedelta(days=7)
            two_weeks_ago_friday = start_week_friday - timedelta(days=14)
            
            prev_week_rsi = self.get_rsi_from_reference(prev_week_friday, rsi_ref_data)
            two_weeks_ago_rsi = self.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)
            
            # ?쒖옉 紐⑤뱶 寃곗젙
            if prev_week_rsi is not None and two_weeks_ago_rsi is not None:
                start_mode = self.determine_mode(prev_week_rsi, two_weeks_ago_rsi, "SF")
            else:
                print(f"[ERROR] 諛깊뀒?ㅽ똿 ?쒖옉 ?쒖젏??RSI ?곗씠?곌? ?놁뒿?덈떎.")
                print(f"   ?쒖옉 二쇱감 RSI: {start_week_rsi}")
                print(f"   1二쇱쟾 RSI: {prev_week_rsi}")
                print(f"   2二쇱쟾 RSI: {two_weeks_ago_rsi}")
                return {
                    "error": f"諛깊뀒?ㅽ똿 ?쒖옉 ?쒖젏??RSI ?곗씠?곌? ?놁뒿?덈떎. 1二쇱쟾: {prev_week_rsi}, 2二쇱쟾: {two_weeks_ago_rsi}",
                    "start_mode": "SF",
                    "start_round": 1,
                    "start_week_rsi": None,
                    "prev_week_rsi": None,
                    "two_weeks_ago_rsi": None
                }
            
            # ?대떦 紐⑤뱶?먯꽌 紐??뚯감源뚯? 留ㅼ닔?덈뒗吏 異붿젙
            # (?ㅼ젣濡쒕뒗 怨쇨굅 留ㅼ닔 湲곕줉???덉뼱???뺥솗?섏?留? ?ш린?쒕뒗 媛꾨떒??異붿젙)
            estimated_round = 1  # 湲곕낯媛?            
            print(f"[INFO] 諛깊뀒?ㅽ똿 ?쒖옉 ?곹깭:")
            print(f"   - ?쒖옉?? {start_date}")
            print(f"   - ?쒖옉 二쇱감 RSI: {start_week_rsi:.2f}")
            print(f"   - 1二쇱쟾 RSI: {prev_week_rsi:.2f}")
            print(f"   - 2二쇱쟾 RSI: {two_weeks_ago_rsi:.2f}")
            print(f"   - ?쒖옉 紐⑤뱶: {start_mode}")
            print(f"   - ?쒖옉 ?뚯감: {estimated_round}?뚯감")
            
            return {
                "start_mode": start_mode,
                "start_round": estimated_round,
                "start_week_rsi": start_week_rsi,
                "prev_week_rsi": prev_week_rsi,
                "two_weeks_ago_rsi": two_weeks_ago_rsi
            }
            
        except Exception as e:
            print(f"[ERROR] 諛깊뀒?ㅽ똿 ?쒖옉 ?곹깭 ?뺤씤 ?ㅻ쪟: {e}")
            return {
                "start_mode": "SF",
                "start_round": 1,
                "start_week_rsi": None,
                "prev_week_rsi": None,
                "two_weeks_ago_rsi": None
            }
    
    def run_backtest(self, start_date: str, end_date: str = None) -> Dict:
        """
        諛깊뀒?ㅽ똿 ?ㅽ뻾
        Args:
            start_date: ?쒖옉 ?좎쭨 (YYYY-MM-DD ?뺤떇)
            end_date: 醫낅즺 ?좎쭨 (None?대㈃ ?ㅻ뒛源뚯?)
        Returns:
            Dict: 諛깊뀒?ㅽ똿 寃곌낵
        """
        print(f"?봽 諛깊뀒?ㅽ똿 ?쒖옉: {start_date} ~ {end_date or '?ㅻ뒛'}")
        
        # 濡쒓렇 ??μ슜 由ъ뒪??珥덇린??        self.backtest_logs = []

        
        # RSI 李몄“ ?곗씠??濡쒕뱶
        rsi_ref_data = self.load_rsi_reference_data()
        
        # ?ы듃?대━??珥덇린??        self.reset_portfolio()

        
        # 諛깊뀒?ㅽ똿 ?쒖옉 ?곹깭 ?뺤씤
        starting_state = self.check_backtest_starting_state(start_date, rsi_ref_data)
        
        # RSI ?곗씠?곌? ?녿뒗 寃쎌슦 諛깊뀒?ㅽ똿 以묐떒
        if "error" in starting_state:
            return {"error": starting_state["error"]}
        
        # ?쒖옉 紐⑤뱶? ?뚯감 ?ㅼ젙
        self.current_mode = starting_state["start_mode"]
        self.current_round = starting_state["start_round"]
        
        print(f"?렞 諛깊뀒?ㅽ똿 ?쒖옉 ?ㅼ젙:")
        print(f"   - 紐⑤뱶: {self.current_mode}")
        print(f"   - ?뚯감: {self.current_round}")
        print(f"   - 1?뚯떆???덉긽: ${self.initial_capital * self.get_current_config()['split_ratios'][self.current_round-1]:,.0f}")
        
        # ?좎쭨 ?뚯떛 (醫낅즺?쇱? ?대떦 ?좎쭨??23:59:59濡??ㅼ젙?섏뿬 ?뱀씪 ?곗씠???ы븿)
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
            else:
                end_dt = datetime.now()
        except ValueError:
            return {"error": "?좎쭨 ?뺤떇???щ컮瑜댁? ?딆뒿?덈떎. YYYY-MM-DD ?뺤떇?쇰줈 ?낅젰?댁＜?몄슂."}
        
        # ??留덇컧 ?꾩뿉??醫낅즺?쇱쓣 ?뺤젙??理쒖떊 嫄곕옒?쇰줈 媛뺤젣 蹂댁젙
        try:
            if not self.is_regular_session_closed_now():
                latest_trading_day = self.get_latest_trading_day().date()
                effective_end_date = min(end_dt.date(), latest_trading_day)
                end_dt = datetime(effective_end_date.year, effective_end_date.month, effective_end_date.day, 23, 59, 59)
        except Exception:
            pass
        

        # 異⑸텇??湲곌컙???곗씠??媛?몄삤湲?        data_start = start_dt - timedelta(days=180)
        

        # SOXL ?곗씠??媛?몄삤湲?(2011?꾨????곗씠???뺣낫)
        period_days = (datetime.now() - data_start).days
        if period_days <= 365:
            period = "1y"
        elif period_days <= 730:
            period = "2y"

        elif period_days <= 1825:  # 5??            period = "5y"

        elif period_days <= 3650:  # 10??            period = "10y"
        else:
            period = "15y"  # 15??(SOXL? 2010??異쒖떆)
            
        soxl_data = self.get_stock_data("SOXL", period)
        if soxl_data is None:
            return {"error": "SOXL ?곗씠?곕? 媛?몄삱 ???놁뒿?덈떎."}
        
        # QQQ ?곗씠??媛?몄삤湲?        qqq_data = self.get_stock_data("QQQ", period)
        if qqq_data is None:
            return {"error": "QQQ ?곗씠?곕? 媛?몄삱 ???놁뒿?덈떎."}
        
        # ?뺢퇋??誘몃쭏媛먯씠怨? 留덉?留??몃뜳???좎쭨媛 ?ㅻ뒛?대㈃ 臾댁“嫄??쒖쇅 (怨듦툒??議곌린 ?앹꽦 ?쇰큺 諛⑹?)
        try:
            today_date = datetime.now().date()
            # ?ㅻ뒛??嫄곕옒?쇱씠怨??뺢퇋?μ씠 ?꾩쭅 留덇컧?섏? ?딆븯?ㅻ㈃ ?ㅻ뒛 ?곗씠???쒖쇅
            if self.is_trading_day(datetime.now()) and not self.is_regular_session_closed_now():
                if len(soxl_data) > 0 and soxl_data.index.max().date() == today_date:
                    soxl_data = soxl_data[soxl_data.index.date < today_date]
                if len(qqq_data) > 0 and qqq_data.index.max().date() == today_date:
                    qqq_data = qqq_data[qqq_data.index.date < today_date]
        except Exception:
            pass

        # 醫낅즺?쇱씠 ?곗씠?곗쓽 留덉?留??좎쭨? 媛숆퀬, ?뺢퇋?μ씠 ?꾩쭅 留덇컧?섏? ?딆븯?ㅻ㈃ 留덉?留????쒖쇅
        # ?? 諛깊뀒?ㅽ똿 醫낅즺?쇱씠 怨쇨굅 ?좎쭨?쇰㈃ ?대? ?뺤젙???곗씠?곗씠誘濡??ы븿
        try:
            if end_date:
                end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
                last_date = soxl_data.index.max().date() if len(soxl_data) > 0 else None
                today_date = datetime.now().date()
                
                # 醫낅즺?쇱씠 ?ㅻ뒛???꾨땲嫄곕굹, ?ㅻ뒛?대뜑?쇰룄 ?뺢퇋?μ씠 留덇컧?섏뿀?ㅻ㈃ ?ы븿
                if last_date and end_d == last_date:
                    if end_d < today_date or (end_d == today_date and self.is_regular_session_closed_now()):
                        # 怨쇨굅 ?좎쭨?닿굅???ㅻ뒛 ?뺢퇋?μ씠 留덇컧?섏뿀?ㅻ㈃ ?ы븿
                        pass
                    else:
                        # ?ㅻ뒛?닿퀬 ?뺢퇋?μ씠 ?꾩쭅 留덇컧?섏? ?딆븯?ㅻ㈃ ?쒖쇅
                        soxl_data = soxl_data[soxl_data.index.date < last_date]
                        qqq_data = qqq_data[qqq_data.index.date < last_date]
        except Exception:
            pass
        
        # 諛깊뀒?ㅽ똿 湲곌컙 ?곗씠???꾪꽣留?(湲곗〈 諛⑹떇: ??꾩뒪?ы봽 鍮꾧탳)
        soxl_backtest = soxl_data[soxl_data.index >= start_dt]
        soxl_backtest = soxl_backtest[soxl_backtest.index <= end_dt]
        
        if len(soxl_backtest) == 0:
            return {"error": "?대떦 湲곌컙??????곗씠?곌? ?놁뒿?덈떎."}
        

        # 留ㅻℓ 湲곕줉 ??μ슜 (?ㅼ젣 ?묒떇??留욊쾶)
        daily_records = []  # ?쇰퀎 湲곕줉
        current_week_rsi = starting_state["start_week_rsi"]  # ?쒖옉 二쇱감 RSI
        current_mode = starting_state["start_mode"]  # ?쒖옉 紐⑤뱶
        current_week = 0  # ?꾩옱 二쇱감 (泥?踰덉㎏ 二쇱감 泥섎━ ??1????
        total_realized_pnl = 0  # ?꾩쟻 ?ㅽ쁽?먯씡
        total_invested = 0  # 珥??ъ옄湲?        cash_balance = self.initial_capital  # ?꾧툑 ?붽퀬
        
        print(f"?뱤 珥?{len(soxl_backtest)}??諛깊뀒?ㅽ똿 吏꾪뻾...")
        

        # 諛깊뀒?ㅽ똿 ?쒖옉?쇱쓽 ?꾩씪 醫낃? ?ㅼ젙
        prev_close = None

        if len(soxl_backtest) > 0:
            # ?쒖옉???꾨궇??醫낃?瑜?李얘린 ?꾪빐 ?꾩껜 ?곗씠?곗뿉??寃??            start_date_prev = start_dt - timedelta(days=1)
            prev_data = soxl_data[soxl_data.index <= start_date_prev]
            if len(prev_data) > 0:
                prev_close = prev_data.iloc[-1]['Close']
                print(f"?뱟 諛깊뀒?ㅽ똿 ?쒖옉 ?꾩씪 醫낃?: {prev_close:.2f} (?좎쭨: {prev_data.index[-1].strftime('%Y-%m-%d')})")
            else:
                print("?좑툘 諛깊뀒?ㅽ똿 ?쒖옉 ?꾩씪 ?곗씠?곕? 李얠쓣 ???놁뒿?덈떎.")
        
        current_week_friday = None  # ?꾩옱 二쇱감??湲덉슂??        previous_day_sold_rounds = 0  # ?꾨궇 留ㅻ룄???뚯감 ??異붿쟻
        
        for i, (current_date, row) in enumerate(soxl_backtest.iterrows()):
            current_price = row['Close']
            
            # 留ㅻ룄 ??current_round瑜?蹂댁쑀 以묒씤 ?뚯감 ??+ 1濡??ш퀎??            if previous_day_sold_rounds > 0:
                # ?꾩옱 蹂댁쑀 以묒씤 ?뚯감 ??怨꾩궛
                holding_rounds = len(self.positions)
                # ?ㅼ쓬 留ㅼ닔 ?뚯감 = 蹂댁쑀 ?뚯감 ??+ 1
                self.current_round = holding_rounds + 1
                print(f"?봽 ?꾨궇 留ㅻ룄 ?꾨즺: {previous_day_sold_rounds}媛??뚯감 留ㅻ룄 ??蹂댁쑀: {holding_rounds}媛????ㅼ쓬 留ㅼ닔: {self.current_round}?뚯감")
                previous_day_sold_rounds = 0  # 諛섏쁺 ??珥덇린??            

            # 嫄곕옒??移댁슫??利앷? (嫄곕옒?쇱씤 寃쎌슦?먮쭔)
            if self.is_trading_day(current_date):
                self.trading_days_count += 1
                
                # ?쒕뱶利앹븸 諛섏쁺 (?대떦 ?좎쭨???쒕뱶利앹븸???덈뒗 寃쎌슦)
                current_date_str = current_date.strftime('%Y-%m-%d')
                seed_increases_today = self.get_seed_increases_for_date(current_date_str)
                if seed_increases_today:
                    # ?꾩옱 珥앹옄??怨꾩궛 (?꾧툑 + 蹂댁쑀二쇱떇 ?됯?湲덉븸)
                    total_shares = sum([pos["shares"] for pos in self.positions])
                    current_total_assets = self.available_cash + (total_shares * current_price)
                    
                    # ?쒕뱶利앹븸 珥앺빀 怨꾩궛
                    total_seed_increase = sum([si["amount"] for si in seed_increases_today])
                    
                    # ?쒕뱶利앹븸???꾧툑?붽퀬??異붽?
                    self.available_cash += total_seed_increase
                    
                    # ?ъ옄?먭툑???꾩옱 珥앹옄??+ ?쒕뱶利앹븸?쇰줈 媛깆떊
                    new_investment_capital = current_total_assets + total_seed_increase
                    old_capital = self.current_investment_capital
                    self.current_investment_capital = new_investment_capital
                    
                    print(f"?뮥 ?쒕뱶利앹븸 諛섏쁺: {current_date_str} - ${total_seed_increase:,.0f} 異붽?")
                    print(f"   ?꾩옱 珥앹옄?? ${current_total_assets:,.0f} + ?쒕뱶利앹븸: ${total_seed_increase:,.0f} = ${new_investment_capital:,.0f}")
                    print(f"   ?ъ옄?먭툑 媛깆떊: ${old_capital:,.0f} ??${new_investment_capital:,.0f}")
                
                # 10嫄곕옒?쇰쭏???ъ옄?먭툑 ?낅뜲?댄듃 (10, 20, 30, ... 嫄곕옒?쇱㎏)
                if self.trading_days_count % 10 == 0 and self.trading_days_count > 0:
                    # ?꾩옱 珥앹옄??怨꾩궛 (?꾧툑 + 蹂댁쑀二쇱떇 ?됯?湲덉븸)
                    total_shares = sum([pos["shares"] for pos in self.positions])
                    total_assets = self.available_cash + (total_shares * current_price)
                    
                    # ?ъ옄?먭툑 ?낅뜲?댄듃
                    old_capital = self.current_investment_capital
                    self.current_investment_capital = total_assets
                    
                    print(f"?뮥 ?ъ옄?먭툑 ?낅뜲?댄듃: {self.trading_days_count}嫄곕옒?쇱㎏ - ${old_capital:,.0f} ??${total_assets:,.0f}")
            
            # ?꾩옱 ?좎쭨媛 ?랁븯??二쇱감??湲덉슂??怨꾩궛
            days_until_friday = (4 - current_date.weekday()) % 7  # 湲덉슂??4)源뚯????쇱닔
            if days_until_friday == 0 and current_date.weekday() != 4:  # 湲덉슂?쇱씠 ?꾨땶??怨꾩궛??0?대㈃ ?ㅼ쓬 二?湲덉슂??                days_until_friday = 7
            this_week_friday = current_date + timedelta(days=days_until_friday)
            
            # ?덈줈??二쇱감?몄? ?뺤씤 (湲덉슂?쇱씠 諛붾뚯뿀?붿?)
            if current_week_friday != this_week_friday:
                current_week_friday = this_week_friday
                
                # ?덈줈??二쇱감??RSI 媛?媛?몄삤湲?(?대떦 二쇱감??湲덉슂??湲곗?)
                current_week_rsi = self.get_rsi_from_reference(this_week_friday, rsi_ref_data)
                
                # 紐⑤뱶 ?낅뜲?댄듃 (2二쇱쟾 RSI? 1二쇱쟾 RSI 鍮꾧탳)
                # 2二쇱쟾怨?1二쇱쟾 RSI 怨꾩궛
                prev_week_friday = this_week_friday - timedelta(days=7)  # 1二쇱쟾
                two_weeks_ago_friday = this_week_friday - timedelta(days=14)  # 2二쇱쟾
                
                prev_week_rsi = self.get_rsi_from_reference(prev_week_friday, rsi_ref_data)  # 1二쇱쟾 RSI
                two_weeks_ago_rsi = self.get_rsi_from_reference(two_weeks_ago_friday, rsi_ref_data)  # 2二쇱쟾 RSI
                
                # RSI ?곗씠?곌? ?녿뒗 寃쎌슦 諛깊뀒?ㅽ똿 以묐떒
                if prev_week_rsi is None or two_weeks_ago_rsi is None:
                    return {"error": f"RSI ?곗씠?곌? ?놁뒿?덈떎. 1二쇱쟾 RSI: {prev_week_rsi}, 2二쇱쟾 RSI: {two_weeks_ago_rsi}"}
                
                # 紐⑤뱶 寃곗젙 (2二쇱쟾 vs 1二쇱쟾 鍮꾧탳)
                new_mode = self.determine_mode(prev_week_rsi, two_weeks_ago_rsi, current_mode)
                if new_mode != current_mode:
                    prev_rsi_display = f"{prev_week_rsi:.2f}" if prev_week_rsi is not None else "None"
                    two_weeks_rsi_display = f"{two_weeks_ago_rsi:.2f}" if two_weeks_ago_rsi is not None else "None"
                    print(f"?봽 諛깊뀒?ㅽ똿 紐⑤뱶 ?꾪솚: {current_mode} ??{new_mode} (1二쇱쟾 RSI: {prev_rsi_display}, 2二쇱쟾 RSI: {two_weeks_rsi_display})")
                    print(f"   ?꾩옱 ?뚯감: {self.current_round} ??理쒕? ?뚯감: {7 if new_mode == 'SF' else 8}")
                    current_mode = new_mode
                    self.current_mode = new_mode  # ?대옒??蹂?섎룄 ?낅뜲?댄듃
                    # 紐⑤뱶 蹂寃???current_round ?좎? (理쒕? ?뚯감留?蹂寃?
                
                current_week += 1  # 二쇱감 踰덊샇 利앷? (0 ??1, 1 ??2, ...)
                current_rsi_display = f"{current_week_rsi:.2f}" if current_week_rsi is not None else "None"
                print(f"?뱟 二쇱감 {current_week}: ~{this_week_friday.strftime('%m-%d')} | RSI: {current_rsi_display}")
            
            # 留ㅻℓ ?ㅽ뻾 (?꾩씪 醫낃?媛 ?덈뒗 寃쎌슦留?
            if prev_close is not None:

                # ?꾩옱 紐⑤뱶 ?ㅼ젙 媛?몄삤湲?                config = self.sf_config if current_mode == "SF" else self.ag_config
                

                # 留ㅼ닔/留ㅻ룄 媛寃?怨꾩궛 (?꾩씪 醫낃? 湲곗?)
                buy_price = prev_close * (1 + config["buy_threshold"] / 100)  # 留ㅼ닔媛
                sell_price = prev_close * (1 + config["sell_threshold"] / 100)  # 留ㅻ룄媛 (?꾩떆, 留ㅼ닔 泥닿껐 ???ш퀎?곕맖)
                
                # 留ㅻ룄 議곌굔 ?뺤씤 諛??ㅽ뻾
                sell_recommendations = self.check_sell_conditions(row, current_date, prev_close)

                daily_realized = 0
                sell_date = ""
                sell_executed_price = 0
                
                sold_rounds = []  # 留ㅻ룄???뚯감??異붿쟻
                sold_positions = []  # 留ㅻ룄???ъ??섎뱾 (留ㅼ닔 ?됱뿉 湲곕줉??
                
                for sell_info in sell_recommendations:

                    position = sell_info["position"]
                    proceeds, sold_round = self.execute_sell(sell_info)
                    realized_pnl = proceeds - position["amount"]
                    daily_realized += realized_pnl
                    total_realized_pnl += realized_pnl
                    cash_balance += proceeds
                    sold_rounds.append(sold_round)
                    
                    # 留ㅻ룄 ?뺣낫瑜?留ㅼ닔 ?됱뿉 湲곕줉?섍린 ?꾪빐 ???                    # ?붿씪???쒓?濡?蹂??                    weekdays_korean = ['??, '??, '??, '紐?, '湲?, '??, '??]
                    weekday_korean = weekdays_korean[current_date.weekday()]
                    sold_positions.append({
                        "round": sold_round,
                        "sell_date": current_date.strftime(f"%m.%d.({weekday_korean})"),
                        "sell_price": sell_info["sell_price"],

                        "realized_pnl": realized_pnl
                    })
                
                # 留ㅼ닔 議곌굔 ?뺤씤 諛??ㅽ뻾 (留ㅻ룄? 愿怨꾩뾾???쒖감?곸쑝濡??뚯감 利앷?)
                buy_executed = False
                buy_price_executed = 0
                buy_quantity = 0
                buy_amount = 0
                current_round_before_buy = self.current_round  # 留ㅼ닔 ???뚯감 ???                
                if self.can_buy_next_round():
                    # LOC 留ㅼ닔 議곌굔: 留ㅼ닔媛媛 醫낃?蹂대떎 ?좊━????(留ㅼ닔媛 > 醫낃?)
                    daily_close = row['Close']
                    
                    # ?붾쾭源? 留ㅼ닔 議곌굔 ?뺤씤
                    log_msg = f"?뵇 {current_date.strftime('%Y-%m-%d')} 留ㅼ닔 議곌굔 ?뺤씤:\n"
                    log_msg += f"   ?꾩씪 醫낃?(prev_close): ${prev_close:.2f}\n"
                    log_msg += f"   ?뱀씪 醫낃?(daily_close): ${daily_close:.2f}\n"
                    log_msg += f"   留ㅼ닔媛(buy_price): ${buy_price:.2f} = prev_close * {1 + config['buy_threshold'] / 100}\n"
                    log_msg += f"   留ㅼ닔 議곌굔: {buy_price:.2f} > {daily_close:.2f} = {buy_price > daily_close}\n"
                    log_msg += f"   ?꾩옱 ?뚯감: {self.current_round}, ?꾧툑?붽퀬: ${self.available_cash:,.0f}"
                    
                    print(log_msg)
                    self.backtest_logs.append(log_msg)
                    
                    if buy_price > daily_close:
                        success_msg = f"??留ㅼ닔 議곌굔 異⑹”! 留ㅼ닔 ?ㅽ뻾 ?쒕룄..."
                        print(success_msg)
                        self.backtest_logs.append(success_msg)
                        
                        if self.execute_buy(buy_price, daily_close, current_date):  # 紐⑺몴媛 湲곗? ?섎웾?쇰줈 怨꾩궛?섏뿬 醫낃???留ㅼ닔
                            exec_msg = f"??留ㅼ닔 泥닿껐 ?깃났!"
                            print(exec_msg)
                            self.backtest_logs.append(exec_msg)
                            
                            buy_executed = True
                            position = self.positions[-1]
                            buy_price_executed = position["buy_price"]
                            buy_quantity = position["shares"]
                            buy_amount = position["amount"]
                            total_invested += buy_amount
                            cash_balance -= buy_amount
                            
                            # 留ㅼ닔 泥닿껐 ??留ㅻ룄紐⑺몴媛 ?ш퀎??(留ㅼ닔泥닿껐???좎쓽 醫낃? 湲곗?)
                            sell_price = daily_close * (1 + config["sell_threshold"] / 100)
                            
                            # 留ㅼ닔 ?됱뿉??留ㅻ룄 ?뺣낫 珥덇린??(?섏쨷??留ㅻ룄?섎㈃ ?낅뜲?댄듃??
                            sell_date = ""
                            sell_executed_price = 0
                        else:
                            fail_msg = f"??留ㅼ닔 ?ㅽ뻾 ?ㅽ뙣 (execute_buy returned False)"
                            print(fail_msg)
                            self.backtest_logs.append(fail_msg)
                    else:
                        nocond_msg = f"??留ㅼ닔 議곌굔 遺덉땐議? {buy_price:.2f} <= {daily_close:.2f}"
                        print(nocond_msg)
                        self.backtest_logs.append(nocond_msg)
                else:
                    nobuy_msg = f"??留ㅼ닔 遺덇??? can_buy_next_round() = False"
                    print(nobuy_msg)
                    self.backtest_logs.append(nobuy_msg)
                
                # 留ㅻ룄???뚯감瑜??ㅼ쓬??current_round 怨꾩궛??諛섏쁺
                if sold_rounds:
                    sold_count = len(sold_rounds)
                    previous_day_sold_rounds = sold_count  # ?ㅼ쓬??諛섏쁺???꾪빐 ???                    print(f"?봽 留ㅻ룄 ?꾨즺: {sold_count}媛??뚯감 留ㅻ룄 ???ㅼ쓬??current_round??諛섏쁺 ?덉젙")
                
                # ?꾩옱 蹂댁쑀 二쇱떇?섏? ?됯??먯씡 怨꾩궛
                total_shares = sum([pos["shares"] for pos in self.positions])
                position_value = total_shares * current_price
                
                # 蹂댁쑀 二쇱떇??留ㅼ닔 ?먭? 怨꾩궛
                total_buy_cost = sum([pos["amount"] for pos in self.positions])
                
                
                # ?쇰퀎 湲곕줉 ?앹꽦
                # ?붿씪???쒓?濡?蹂??                weekdays_korean = ['??, '??, '??, '紐?, '湲?, '??, '??]
                weekday_korean = weekdays_korean[current_date.weekday()]
                
                # 留ㅻ룄 ?뺣낫 珥덇린??(?꾩옱 ?좎쭨??留ㅼ닔 ?됱뿉??留ㅻ룄 ?뺣낫 ?놁쓬)
                sell_date_final = ""
                sell_executed_price_final = 0
                realized_pnl_final = 0
                
                daily_record = {
                    "date": current_date.strftime("%Y-%m-%d"),  # ?쒖? ISO ?뺤떇?쇰줈 蹂寃?                    "week": current_week,
                    "rsi": current_week_rsi or 50.0,
                    "mode": current_mode,
                    "current_round": min(current_round_before_buy, 7 if current_mode == "SF" else 8),  # 留ㅼ닔 ???뚯감 ?ъ슜 (理쒕?媛??쒗븳)
                    "seed_amount": self.calculate_position_size(current_round_before_buy) if buy_executed else 0,
                    "buy_order_price": buy_price,
                    "close_price": current_price,
                    "sell_target_price": sell_price,
                    "stop_loss_date": self.calculate_stop_loss_date(current_date, config["max_hold_days"]),
                    "d": 0,  # D 而щ읆 (?섎? 遺덈챸)
                    "trading_days": i + 1,
                    "buy_executed_price": buy_price_executed,
                    "buy_quantity": buy_quantity,
                    "buy_amount": buy_amount,
                    "buy_round": current_round_before_buy if buy_executed else 0,  # 留ㅼ닔 ?뚯감 ???                    "commission": 0.0,
                    "sell_date": sell_date_final,
                    "sell_executed_price": sell_executed_price_final,
                    "holding_days": 0,  # 蹂댁쑀湲곌컙 (嫄곕옒??湲곗?)
                    "holdings": total_shares,
                    "realized_pnl": realized_pnl_final,
                    "cumulative_realized": total_realized_pnl,
                    "daily_realized": daily_realized,
                    "update": False,
                    "investment_update": self.initial_capital,
                    "withdrawal": False,
                    "withdrawal_amount": 0,
                    "seed_increase": 0,
                    "position_value": position_value,
                    "cash_balance": cash_balance,
                    "total_assets": cash_balance + position_value
                }
                
                daily_records.append(daily_record)
                
                # ?ㅻ뒛 留ㅻ룄???ъ??섎뱾???뺣낫瑜?怨쇨굅 留ㅼ닔 ?됱뿉 湲곕줉 (daily_record ?앹꽦 ??
                if sold_positions:
                    for sold_pos in sold_positions:
                        
                        # ?대떦 ?뚯감??留ㅼ닔 ?됱쓣 李얠븘??留ㅻ룄 ?뺣낫 ?낅뜲?댄듃
                        found = False
                        for record in daily_records:
                            if (record.get('buy_executed_price', 0) > 0 and 
                                record.get('buy_quantity', 0) > 0 and
                                record.get('sell_date', '') == ''):  # ?꾩쭅 留ㅻ룄?섏? ?딆? ??                                
                                # ?대떦 ?뚯감?몄? ?뺤씤 (buy_round濡??뺥솗??留ㅼ묶)
                                if record.get('buy_round', 0) == sold_pos["round"]:
                                    # 蹂댁쑀湲곌컙 怨꾩궛 (嫄곕옒??湲곗?)
                                    try:
                                        buy_date_str = record['date']
                                        sell_date_str = sold_pos["sell_date"]
                                        
                                        # ?좎쭨 ?뚯떛 (?? "25.01.02.(紐?" -> "2025-01-02")
                                        buy_date_str_clean = buy_date_str.split('(')[0].strip().rstrip('.')
                                        sell_date_str_clean = sell_date_str.split('(')[0].strip().rstrip('.')
                                        
                                        buy_date = datetime.strptime(buy_date_str_clean, "%y.%m.%d")
                                        sell_date = datetime.strptime(sell_date_str_clean, "%m.%d")
                                        
                                        # ?곕룄 蹂댁젙 (留ㅻ룄?쇱뿉???곕룄媛 ?놁쑝誘濡?
                                        if sell_date.month < buy_date.month or (sell_date.month == buy_date.month and sell_date.day < buy_date.day):
                                            sell_date = sell_date.replace(year=buy_date.year + 1)
                                        else:

                                            sell_date = sell_date.replace(year=buy_date.year)
                                        
                                        # 嫄곕옒??怨꾩궛 (二쇰쭚 + ?댁옣???쒖쇅)
                                        holding_days = 0
                                        temp_date = buy_date
                                        while temp_date <= sell_date:
                                            if self.is_trading_day(temp_date):
                                                holding_days += 1
                                            temp_date += timedelta(days=1)
                                        
                                        record['holding_days'] = holding_days
                                        
                                    except Exception as e:
                                        print(f"?좑툘 蹂댁쑀湲곌컙 怨꾩궛 ?ㅻ쪟: {e}")
                                        record['holding_days'] = 0
                                    
                                    record['sell_date'] = sold_pos["sell_date"]
                                    record['sell_executed_price'] = sold_pos["sell_price"]
                                    record['realized_pnl'] = sold_pos["realized_pnl"]
                                    found = True
                                    break
                        
            
            # 吏꾪뻾?곹솴 異쒕젰
            if (i + 1) % 10 == 0:
                print(f"吏꾪뻾: {i+1}/{len(soxl_backtest)}??({(i+1)/len(soxl_backtest)*100:.1f}%)")

            
            prev_close = current_price
        
        # 理쒖쥌 寃곌낵 怨꾩궛
        
        # 諛깊뀒?ㅽ똿 ?꾨즺 ??current_round瑜??щ컮瑜닿쾶 ?ㅼ젙
        # 蹂댁쑀 以묒씤 ?뚯감 ??+ 1 = ?ㅼ쓬 留ㅼ닔 ?뚯감
        holding_rounds = len(self.positions)
        self.current_round = holding_rounds + 1
        print(f"?봽 諛깊뀒?ㅽ똿 ?꾨즺 ??current_round ?ㅼ젙: 蹂댁쑀 {holding_rounds}媛????ㅼ쓬 留ㅼ닔 {self.current_round}?뚯감")

        final_value = daily_records[-1]["total_assets"] if daily_records else self.initial_capital
        total_return = ((final_value - self.initial_capital) / self.initial_capital) * 100
        
        summary = {
            "start_date": start_date,
            "end_date": end_date or datetime.now().strftime("%Y-%m-%d"),

            "trading_days": len(soxl_backtest),
            "initial_capital": self.initial_capital,
            "final_value": final_value,
            "total_return": total_return,
            "final_positions": len(self.positions),

            "daily_records": daily_records,
            "logs": self.backtest_logs if hasattr(self, 'backtest_logs') else []
        }

        
        # MDD 怨꾩궛 諛?異쒕젰
        mdd_info = self.calculate_mdd(daily_records)
        
        print("??諛깊뀒?ㅽ똿 ?꾨즺!")

        print(f"\n?뱤 諛깊뀒?ㅽ똿 寃곌낵 ?붿빟:")
        print(f"   ?뱟 湲곌컙: {start_date} ~ {end_date or datetime.now().strftime('%Y-%m-%d')}")
        print(f"   ?뮥 珥덇린?먮낯: ${self.initial_capital:,.0f}")
        print(f"   ?뮥 理쒖쥌?먯궛: ${final_value:,.0f}")
        print(f"   ?뱢 珥앹닔?듬쪧: {total_return:+.2f}%")
        print(f"   ?벀 理쒖쥌蹂댁쑀?ъ??? {len(self.positions)}媛?)
        print(f"\n?좑툘 由ъ뒪??吏??")
        print(f"   ?뱣 MDD (理쒕??숉룺): {mdd_info.get('mdd_percent', 0.0):.2f}%")
        print(f"   ?뱟 MDD 諛쒖깮?? {mdd_info.get('mdd_date', '')}")
        print(f"   ?뮥 理쒖??먯궛: ${mdd_info.get('mdd_value', 0.0):,.0f}")
        print(f"   ?뱟 MDD 諛쒖깮 理쒓퀬?먯궛?? {mdd_info.get('mdd_peak_date', '')}")
        print(f"   ?뱟 理쒓퀬?먯궛?? {mdd_info.get('overall_peak_date', '')}")
        print(f"   ?뮥 理쒓퀬?먯궛: ${mdd_info.get('overall_peak_value', 0.0):,.0f}")
        
        return summary
    

    
    def get_week_number(self, date: datetime) -> int:
        """?좎쭨濡쒕???二쇱감 怨꾩궛"""
        year = date.year
        week_num = date.isocalendar()[1]
        return f"{year}W{week_num:02d}"
    
    def calculate_mdd(self, daily_records: List[Dict]) -> Dict:
        """
        MDD (Maximum Drawdown) 怨꾩궛
        Args:
            daily_records: ?쇰퀎 諛깊뀒?ㅽ똿 湲곕줉
        Returns:
            Dict: MDD ?뺣낫
        """
        if not daily_records:
            return {
                "mdd_percent": 0.0, 
                "mdd_date": "", 
                "mdd_value": 0.0, 
                "mdd_peak_date": "",  # MDD 怨꾩궛 ?쒖젏??理쒓퀬?먯궛??                "overall_peak_date": "",  # ?꾩껜 湲곌컙 理쒓퀬?먯궛??                "overall_peak_value": 0.0  # ?꾩껜 湲곌컙 理쒓퀬?먯궛
            }
        
        max_assets = 0.0
        max_drawdown = 0.0
        mdd_peak_date = ""  # MDD 怨꾩궛 ?쒖젏??理쒓퀬?먯궛??        mdd_date = ""
        mdd_value = 0.0
        
        # ?꾩껜 湲곌컙 理쒓퀬?먯궛 異붿쟻
        overall_max_assets = 0.0
        overall_peak_date = ""
        
        # MDD 怨꾩궛??蹂?섎뱾
        current_peak_assets = 0.0
        current_peak_date = ""
        
        for record in daily_records:
            current_assets = record.get('total_assets', 0.0)
            
            # ?꾩껜 湲곌컙 理쒓퀬?먯궛 媛깆떊
            if current_assets > overall_max_assets:
                overall_max_assets = current_assets
                overall_peak_date = record.get('date', '')
            
            # ?덈줈??理쒓퀬?먯궛 媛깆떊 (MDD 怨꾩궛??
            if current_assets > current_peak_assets:
                current_peak_assets = current_assets
                current_peak_date = record.get('date', '')
            
            # ?꾩옱 ?먯궛???꾩옱 理쒓퀬?먯궛蹂대떎 ??쑝硫??숉룺 怨꾩궛
            if current_peak_assets > 0:
                drawdown = (current_peak_assets - current_assets) / current_peak_assets * 100
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                    mdd_date = record.get('date', '')
                    mdd_value = current_assets
                    mdd_peak_date = current_peak_date  # MDD 諛쒖깮 ?쒖젏??湲곗? 理쒓퀬?먯궛??        
        return {
            "mdd_percent": max_drawdown,
            "mdd_date": mdd_date,
            "mdd_value": mdd_value,
            "mdd_peak_date": mdd_peak_date,  # MDD 怨꾩궛 ?쒖젏??理쒓퀬?먯궛??            "overall_peak_date": overall_peak_date,  # ?꾩껜 湲곌컙 理쒓퀬?먯궛??            "overall_peak_value": overall_max_assets  # ?꾩껜 湲곌컙 理쒓퀬?먯궛
        }
    
    def export_backtest_to_excel(self, backtest_result: Dict, filename: str = None):
        """
        諛깊뀒?ㅽ똿 寃곌낵瑜??묒? ?뚯씪濡??대낫?닿린
        Args:
            backtest_result: 諛깊뀒?ㅽ똿 寃곌낵
            filename: ?뚯씪紐?(None?대㈃ ?먮룞 ?앹꽦)
        """
        if "error" in backtest_result:
            print(f"???묒? ?대낫?닿린 ?ㅽ뙣: {backtest_result['error']}")
            return
        
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"SOXL_諛깊뀒?ㅽ똿_{backtest_result['start_date']}_{timestamp}.xlsx"
        
        # ?묒? ?뚰겕遺??앹꽦
        wb = openpyxl.Workbook()

        
        # 媛?대뜲 ?뺣젹 ?ㅼ젙
        center_alignment = Alignment(horizontal='center', vertical='center')
        
        # ?붿빟 ?쒗듃
        ws_summary = wb.active
        ws_summary.title = "諛깊뀒?ㅽ똿 ?붿빟"

        
        # 泥?踰덉㎏ ??怨좎젙 (?ㅻ뜑 怨좎젙)
        ws_summary.freeze_panes = "A2"
        
        # MDD 怨꾩궛
        mdd_info = self.calculate_mdd(backtest_result['daily_records'])
        
        # ?붿빟 ?곗씠???묒꽦
        summary_data = [
            ["SOXL ??명닾??諛깊뀒?ㅽ똿 寃곌낵", ""],
            ["", ""],
            ["?쒖옉??, backtest_result['start_date']],
            ["醫낅즺??, backtest_result['end_date']],
            ["嫄곕옒?쇱닔", f"{backtest_result['trading_days']}??],
            ["", ""],
            ["珥덇린?먮낯", f"${backtest_result['initial_capital']:,.0f}"],
            ["理쒖쥌?먯궛", f"${backtest_result['final_value']:,.0f}"],
            ["珥앹닔?듬쪧", f"{backtest_result['total_return']:+.2f}%"],

            ["理쒖쥌蹂댁쑀?ъ???, f"{backtest_result['final_positions']}媛?],
            ["", ""],

            ["=== 由ъ뒪??吏??===", ""],
            ["MDD (理쒕??숉룺)", f"{mdd_info.get('mdd_percent', 0.0):.2f}%"],
            ["MDD 諛쒖깮??, mdd_info.get('mdd_date', '')],
            ["理쒖??먯궛", f"${mdd_info.get('mdd_value', 0.0):,.0f}"],
            ["MDD 諛쒖깮 理쒓퀬?먯궛??, mdd_info.get('mdd_peak_date', '')],
            ["理쒓퀬?먯궛??, mdd_info.get('overall_peak_date', '')],
            ["理쒓퀬?먯궛", f"${mdd_info.get('overall_peak_value', 0.0):,.0f}"]
        ]
        
        for row_idx, (label, value) in enumerate(summary_data, 1):

            cell1 = ws_summary.cell(row=row_idx, column=1, value=label)
            cell2 = ws_summary.cell(row=row_idx, column=2, value=value)
            cell1.alignment = center_alignment
            cell2.alignment = center_alignment
        
        # ?ㅽ????곸슜
        title_font = Font(size=16, bold=True)

        title_cell = ws_summary.cell(row=1, column=1)
        title_cell.font = title_font
        title_cell.alignment = center_alignment
        
        # ?곸꽭 嫄곕옒 ?댁뿭 ?쒗듃

        ws_detail = wb.create_sheet("留ㅻℓ ?곸꽭?댁뿭")
        

        # 泥?踰덉㎏ ??怨좎젙 (?ㅻ뜑 怨좎젙)
        ws_detail.freeze_panes = "A2"
        
        # ?ㅻ뜑 ?묒꽦 (?ㅼ젣 ?묒떇??留욊쾶)
        headers = [

            "?좎쭨", "二쇱감", "RSI", "紐⑤뱶", "?꾩옱?뚯감", "1?뚯떆??, 
            "留ㅼ닔二쇰Ц媛", "醫낃?", "留ㅻ룄紐⑺몴媛", "?먯젅?덉젙??, "嫄곕옒?쇱닔", 
            "留ㅼ닔泥닿껐", "?섎웾", "留ㅼ닔?湲?, "留ㅻ룄??, "留ㅻ룄泥닿껐", "蹂댁쑀湲곌컙",
            "蹂댁쑀", "?ㅽ쁽?먯씡", "?꾩쟻?ㅽ쁽", "?뱀씪?ㅽ쁽",
            "?덉닔湲?, "珥앹옄??
        ]
        
        header_font = Font(size=11, bold=True)
        
        for col_idx, header in enumerate(headers, 1):
            cell = ws_detail.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = PatternFill(start_color="E6E6FA", end_color="E6E6FA", fill_type="solid")

            cell.alignment = center_alignment
        
        # ?곗씠???묒꽦

        prev_close_price = None  # ?꾩씪 醫낃? 異붿쟻??        
        for row_idx, record in enumerate(backtest_result['daily_records'], 2):
            # ?좎쭨 (泥??곗씠?곗? 留ㅼ＜ ?붿슂?쇱? 蹂쇰뱶泥?
            cell = ws_detail.cell(row=row_idx, column=1, value=record['date'])
            cell.alignment = center_alignment
            
            # 泥??곗씠???먮뒗 ?붿슂??泥댄겕
            if row_idx == 2:  # 泥??곗씠??                cell.font = Font(bold=True)
            else:
                # ?좎쭨?먯꽌 ?붿씪 異붿텧 (?? "25.01.02.(紐?" -> "??)
                date_str = record['date']
                if '(??' in date_str:
                    cell.font = Font(bold=True)
            
            # 二쇱감
            cell = ws_detail.cell(row=row_idx, column=2, value=record['week'])
            cell.alignment = center_alignment
            
            # RSI
            rsi_value = record.get('rsi', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=3, value=f"{rsi_value:.2f}")
            cell.alignment = center_alignment
            
            # 紐⑤뱶 (SF: 珥덈줉??湲?? AG: 二쇳솴??湲??
            cell = ws_detail.cell(row=row_idx, column=4, value=record['mode'])
            cell.alignment = center_alignment
            
            if record['mode'] == 'SF':
                cell.font = Font(color="008000")  # 珥덈줉??湲??            elif record['mode'] == 'AG':
                cell.font = Font(color="FF8C00")  # 二쇳솴??湲??            
            # ?꾩옱?뚯감
            cell = ws_detail.cell(row=row_idx, column=5, value=record['current_round'])
            cell.alignment = center_alignment
            
            # 1?뚯떆??            seed_amount = record.get('seed_amount', 0.0) or 0.0
            if seed_amount > 0:
                cell = ws_detail.cell(row=row_idx, column=6, value=f"${seed_amount:,.0f}")
            else:
                cell = ws_detail.cell(row=row_idx, column=6, value="")
            cell.alignment = center_alignment
            
            # 留ㅼ닔二쇰Ц媛
            buy_order_price = record.get('buy_order_price', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=7, value=f"${buy_order_price:.2f}")
            cell.alignment = center_alignment
            
            # 醫낃? (?댁젣 ?鍮??곸듅: 鍮④컙?? ?섎씫: ?뚮???
            close_price = record.get('close_price', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=8, value=f"{close_price:.2f}")
            cell.alignment = center_alignment
            
            # ?꾩씪 ?鍮??곸듅/?섎씫 ?됱긽 ?곸슜
            if prev_close_price is not None:
                if close_price > prev_close_price:
                    cell.font = Font(color="FF0000")  # 鍮④컙??                elif close_price < prev_close_price:
                    cell.font = Font(color="0000FF")  # ?뚮???            
            prev_close_price = close_price  # ?ㅼ쓬 ?됱쓣 ?꾪빐 ???            
            # 留ㅻ룄紐⑺몴媛
            sell_target_price = record.get('sell_target_price', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=9, value=f"${sell_target_price:.2f}")
            cell.alignment = center_alignment
            
            # ?먯젅?덉젙??            cell = ws_detail.cell(row=row_idx, column=10, value=record['stop_loss_date'])
            cell.alignment = center_alignment
            
            # 嫄곕옒?쇱닔
            cell = ws_detail.cell(row=row_idx, column=11, value=record['trading_days'])
            cell.alignment = center_alignment
            
            # 留ㅼ닔泥닿껐 (鍮④컙??
            buy_executed_price = record.get('buy_executed_price', 0.0) or 0.0
            if buy_executed_price > 0:
                cell = ws_detail.cell(row=row_idx, column=12, value=f"${buy_executed_price:.2f}")
                cell.font = Font(color="FF0000")  # 鍮④컙??            else:
                cell = ws_detail.cell(row=row_idx, column=12, value="")
            cell.alignment = center_alignment
            
            # ?섎웾 (留ㅼ닔泥닿껐 ??鍮④컙??
            buy_quantity = record.get('buy_quantity', 0) or 0
            if buy_quantity > 0:
                cell = ws_detail.cell(row=row_idx, column=13, value=buy_quantity)
                cell.font = Font(color="FF0000")  # 鍮④컙??            else:
                cell = ws_detail.cell(row=row_idx, column=13, value="")
            cell.alignment = center_alignment
            
            # 留ㅼ닔?湲?(留ㅼ닔泥닿껐 ??鍮④컙??
            buy_amount = record.get('buy_amount', 0.0) or 0.0
            if buy_amount > 0:
                cell = ws_detail.cell(row=row_idx, column=14, value=f"${buy_amount:,.0f}")
                cell.font = Font(color="FF0000")  # 鍮④컙??            else:
                cell = ws_detail.cell(row=row_idx, column=14, value="")
            cell.alignment = center_alignment
            
            # 留ㅻ룄??(?뚮???湲??
            cell = ws_detail.cell(row=row_idx, column=15, value=record['sell_date'])
            cell.alignment = center_alignment
            if record['sell_date']:  # 留ㅻ룄?쇱씠 ?덈뒗 寃쎌슦?먮쭔 ?뚮????곸슜
                cell.font = Font(color="0000FF")  # ?뚮???湲??            
            # 留ㅻ룄泥닿껐 (?뚮???湲??
            sell_executed_price = record.get('sell_executed_price', 0.0) or 0.0
            if sell_executed_price > 0:
                cell = ws_detail.cell(row=row_idx, column=16, value=f"${sell_executed_price:.2f}")
                cell.font = Font(color="0000FF")  # ?뚮???湲??            else:
                cell = ws_detail.cell(row=row_idx, column=16, value="")
            cell.alignment = center_alignment
            
            # 蹂댁쑀湲곌컙
            holding_days = record.get('holding_days', 0) or 0
            if holding_days > 0:
                cell = ws_detail.cell(row=row_idx, column=17, value=f"{holding_days}??)
            else:
                cell = ws_detail.cell(row=row_idx, column=17, value="")
            cell.alignment = center_alignment
            
            # 蹂댁쑀
            cell = ws_detail.cell(row=row_idx, column=18, value=record['holdings'])
            cell.alignment = center_alignment
            
            # ?ㅽ쁽?먯씡
            realized_pnl = record.get('realized_pnl', 0.0) or 0.0
            if realized_pnl != 0:
                cell = ws_detail.cell(row=row_idx, column=19, value=f"${realized_pnl:,.0f}")
            else:
                cell = ws_detail.cell(row=row_idx, column=19, value="")
            cell.alignment = center_alignment
            
            # ?꾩쟻?ㅽ쁽
            cumulative_realized = record.get('cumulative_realized', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=20, value=f"${cumulative_realized:,.0f}")
            cell.alignment = center_alignment
            cell.font = Font(color="FF0000")  # 鍮④컙??            
            # ?뱀씪?ㅽ쁽
            daily_realized = record.get('daily_realized', 0.0) or 0.0
            if daily_realized != 0:
                cell = ws_detail.cell(row=row_idx, column=21, value=f"${daily_realized:,.0f}")
            else:
                cell = ws_detail.cell(row=row_idx, column=21, value="")
            cell.alignment = center_alignment
            
            # ?덉닔湲?            cash_balance = record.get('cash_balance', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=22, value=f"${cash_balance:,.0f}")
            cell.alignment = center_alignment
            
            # 珥앹옄??(?レ옄 ?뺤떇?쇰줈 ???
            total_assets = record.get('total_assets', 0.0) or 0.0
            cell = ws_detail.cell(row=row_idx, column=23, value=total_assets)
            cell.alignment = center_alignment
            # ?レ옄 ?뺤떇?쇰줈 ?쒖떆 (泥??⑥쐞 援щ텇???ы븿)
            cell.number_format = '#,##0'
        
        # ???덈퉬 ?먮룞 議곗젙
        for ws in [ws_summary, ws_detail]:
            for column in ws.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass

                adjusted_width = min(max_length + 2, 25)
                ws.column_dimensions[column_letter].width = adjusted_width
        
        # ?뚯씪 ???        try:
            wb.save(filename)
            print(f"??諛깊뀒?ㅽ똿 寃곌낵媛 ?묒? ?뚯씪濡???λ릺?덉뒿?덈떎: {filename}")
            return filename
        except Exception as e:
            print(f"???묒? ?뚯씪 ????ㅽ뙣: {e}")
            return None

def main():
    """硫붿씤 ?ㅽ뻾 ?⑥닔"""
    print("?? SOXL ??명닾???쒖뒪??)
    print("=" * 50)
    

    # ?ъ옄?먭툑 ?ъ슜???낅젰
    while True:
        try:
            initial_capital_input = input("?뮥 珥덇린 ?ъ옄湲덉쓣 ?낅젰?섏꽭??(?щ윭): ").strip()
            if not initial_capital_input:
                initial_capital = 40000  # 湲곕낯媛?                print(f"?뮥 ?ъ옄?먭툑: ${initial_capital:,.0f} (湲곕낯媛?")
                break
            
            initial_capital = float(initial_capital_input)
            if initial_capital <= 0:
                print("???ъ옄湲덉? 0蹂대떎 ??媛믪씠?댁빞 ?⑸땲??")
                continue
                
            print(f"?뮥 ?ъ옄?먭툑: ${initial_capital:,.0f}")
            break
            
        except ValueError:
            print("???щ컮瑜??レ옄瑜??낅젰?댁＜?몄슂.")
            continue
    
    # ?몃젅?대뜑 珥덇린??    trader = SOXLQuantTrader(initial_capital)
    
    # ?쒖옉???낅젰(?뷀꽣 ??1????
    start_date_input = input("?뱟 ?ъ옄 ?쒖옉?쇱쓣 ?낅젰?섏꽭??(YYYY-MM-DD, ?뷀꽣??1????: ").strip()
    if not start_date_input:
        start_date_input = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    trader.session_start_date = start_date_input
    
    while True:
        print("\n" + "=" * 50)
        print("硫붾돱瑜??좏깮?섏꽭??")
        print("1. ?ㅻ뒛??留ㅻℓ 異붿쿇 蹂닿린")
        print("2. ?ы듃?대━???꾪솴 蹂닿린")
        print("3. 諛깊뀒?ㅽ똿 ?ㅽ뻾")
        print("4. 留ㅼ닔 ?ㅽ뻾 (?뚯뒪??")
        print("5. 留ㅻ룄 ?ㅽ뻾 (?뚯뒪??")
        print("T. ?뚯뒪???좎쭨(?ㅻ뒛) ?ㅼ젙/?댁젣")
        print("6. 醫낅즺")
        
        choice = input("\n?좏깮 (1-6): ").strip()
        
        if choice == '1':
            # ??λ맂 ?쒖옉?쇰????ㅻ뒛源뚯? ?쒕??덉씠?섏쑝濡??꾩옱 ?곹깭 ?곗텧
            start_date = trader.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            sim_result = trader.simulate_from_start_to_today(start_date, quiet=True)
            if "error" in sim_result:
                print(f"???쒕??덉씠???ㅽ뙣: {sim_result['error']}")
            
            # ?꾩옱 ?곹깭 湲곕컲 ?ㅻ뒛??異붿쿇 異쒕젰
            recommendation = trader.get_daily_recommendation()
            trader.print_recommendation(recommendation)
            
        elif choice == '2':
            # ??λ맂 ?쒖옉?쇰????ㅻ뒛源뚯? ?쒕??덉씠?섏쑝濡??꾪솴 ?ш퀎??            start_date = trader.session_start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            sim_result = trader.simulate_from_start_to_today(start_date, quiet=True)
            if "error" in sim_result:
                print(f"???쒕??덉씠???ㅽ뙣: {sim_result['error']}")
            
            # 湲곗〈 ?뺤떇 ?좎??섏뿬 ?꾪솴 異쒕젰
            if trader.positions:
                print("\n?뮳 ?꾩옱 ?ы듃?대━??")
                print("-" * 30)
                for pos in trader.positions:
                    hold_days = (datetime.now() - pos['buy_date']).days
                    print(f"{pos['round']}?뚯감: {pos['shares']}二?@ ${pos['buy_price']:.2f} ({hold_days}??")
                print(f"\n?꾧툑?붽퀬: ${trader.available_cash:,.0f}")
            else:
                print("\n蹂댁쑀 ?ъ??섏씠 ?놁뒿?덈떎.")
                print(f"?꾧툑?붽퀬: ${trader.available_cash:,.0f}")
        
        elif choice == '3':
            # 諛깊뀒?ㅽ똿 ?ㅽ뻾
            print("\n?뱤 諛깊뀒?ㅽ똿 ?ㅽ뻾")
            print("-" * 30)
            
            start_date = input("?쒖옉 ?좎쭨瑜??낅젰?섏꽭??(YYYY-MM-DD): ").strip()
            if not start_date:
                print("?좎쭨瑜??낅젰?댁＜?몄슂.")
                continue
            
            end_date = input("醫낅즺 ?좎쭨瑜??낅젰?섏꽭??(YYYY-MM-DD, ?뷀꽣???ㅻ뒛源뚯?): ").strip()
            if not end_date:
                end_date = None
            
            print("\n諛깊뀒?ㅽ똿???쒖옉?⑸땲??..")
            backtest_result = trader.run_backtest(start_date, end_date)
            
            if "error" in backtest_result:
                print(f"??諛깊뀒?ㅽ똿 ?ㅽ뙣: {backtest_result['error']}")
                continue
            

            # MDD 怨꾩궛
            mdd_info = trader.calculate_mdd(backtest_result['daily_records'])
            
            # 寃곌낵 異쒕젰
            print("\n" + "=" * 60)
            print("?뱤 諛깊뀒?ㅽ똿 寃곌낵 ?붿빟")
            print("=" * 60)
            print(f"湲곌컙: {backtest_result['start_date']} ~ {backtest_result['end_date']}")
            print(f"嫄곕옒?쇱닔: {backtest_result['trading_days']}??)
            print(f"珥덇린?먮낯: ${backtest_result['initial_capital']:,.0f}")
            print(f"理쒖쥌?먯궛: ${backtest_result['final_value']:,.0f}")
            print(f"珥앹닔?듬쪧: {backtest_result['total_return']:+.2f}%")

            print(f"理쒕? MDD: {mdd_info.get('mdd_percent', 0.0):.2f}%")
            print(f"理쒖쥌蹂댁쑀?ъ??? {backtest_result['final_positions']}媛?)

            print(f"珥?嫄곕옒?쇱닔: {len(backtest_result['daily_records'])}??)
            
            # ?묒? ?대낫?닿린 ?щ? ?뺤씤
            export_choice = input("\n?묒? ?뚯씪濡??대낫?댁떆寃좎뒿?덇퉴? (y/n): ").strip().lower()
            if export_choice == 'y':
                filename = trader.export_backtest_to_excel(backtest_result)
                if filename:
                    print(f"?뱚 ?뚯씪 ?꾩튂: {os.path.abspath(filename)}")
            
        elif choice == '4':
            print("\n?뵩 留ㅼ닔 ?뚯뒪??湲곕뒫 (媛쒕컻 以?")
            
        elif choice == '5':
            print("\n?뵩 留ㅻ룄 ?뚯뒪??湲곕뒫 (媛쒕컻 以?")
            
        elif choice.lower() == 't':
            print("\n?㎦ ?뚯뒪???좎쭨 ?ㅼ젙")
            print("- 鍮꾩슦怨??뷀꽣?섎㈃ ?댁젣?⑸땲??)
            test_date = input("?뚯뒪???ㅻ뒛 ?좎쭨 (YYYY-MM-DD): ").strip()
            trader.set_test_today(test_date if test_date else None)
            
        elif choice == '6':
            print("?꾨줈洹몃옩??醫낅즺?⑸땲??")
            break
            
        else:
            print("?щ컮瑜??좏깮吏瑜??낅젰?섏꽭??")

if __name__ == "__main__":
    main()

