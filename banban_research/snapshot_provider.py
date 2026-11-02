"""Explicit in-memory OHLC input; no downloads, database, broker or account state."""

from copy import deepcopy
from datetime import datetime
import math

import pandas as pd


def normalize_bars(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Nonempty OHLC rows required')
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict) or set(raw) - {'date', 'open', 'high', 'low', 'close', 'volume'}:
            raise ValueError('Only OHLCV market fields are accepted')
        day = str(raw.get('date', ''))
        if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
            raise ValueError('ISO session date required')
        prices = {key: float(raw[key]) for key in ('open', 'high', 'low', 'close')}
        if not all(math.isfinite(value) and value > 0 for value in prices.values()):
            raise ValueError('Positive finite prices required')
        if prices['low'] > min(prices['open'], prices['close']) or prices['high'] < max(prices['open'], prices['close']):
            raise ValueError('Inconsistent OHLC')
        volume = float(raw.get('volume', 0))
        if not math.isfinite(volume) or volume < 0:
            raise ValueError('Invalid volume')
        normalized.append({'date': day, **prices, 'volume': volume})
    dates = [row['date'] for row in normalized]
    if dates != sorted(set(dates)):
        raise ValueError('Duplicate or unsorted sessions')
    return normalized


class SnapshotProvider:
    def __init__(self, snapshot):
        if not isinstance(snapshot, dict) or set(snapshot) != {'bars'} or set(snapshot['bars']) != {'SOXL', 'QQQ'}:
            raise ValueError('Snapshot must contain only bars.SOXL and bars.QQQ')
        self.bars = {symbol: normalize_bars(rows) for symbol, rows in snapshot['bars'].items()}
        if [row['date'] for row in self.bars['SOXL']] != [row['date'] for row in self.bars['QQQ']]:
            raise ValueError('SOXL/QQQ session coverage mismatch')
        self.db = self

    def get_daily_bars(self, symbol, start_date, end_date):
        rows = [row for row in self.bars[symbol] if start_date <= row['date'] <= end_date]
        frame = pd.DataFrame(deepcopy(rows), columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop('date')), name='Date')
        frame.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        return {'dataframe': frame, 'warnings': []}

    def get_market_eod_rows(self, symbol, start_date, end_date):
        return []
