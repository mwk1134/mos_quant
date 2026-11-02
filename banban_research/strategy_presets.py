"""
Strategy Presets SSOT
백테스트/모드 계산에서 공통으로 쓰는 전략 파라미터 정의.
"""
from __future__ import annotations
import copy
DEFAULT_STRATEGY_ID = 'dongpa_modified'
STRATEGY_ID_ALIASES = {'dongpa_ma_v11_r': 'dongpa_ma_v1_1_r', 'dongpa_ma_v11_r2': 'dongpa_ma_v1_1_r2'}
ALLOWED_STRATEGY_IDS = {'dongpa_modified', 'dongpa_ma_v1', 'dongpa_ma_v1_1', 'dongpa_ma_v1_1_r', 'dongpa_ma_v1_1_r2', 'dongpa_original', 'dongpa_original_custom', 'dongpa_modified_custom', 'custom'}
ACCOUNT_ALLOWED_STRATEGY_IDS = {'dongpa_modified', 'dongpa_ma_v1', 'dongpa_ma_v1_1', 'dongpa_ma_v1_1_r', 'dongpa_ma_v1_1_r2', 'dongpa_original', 'dongpa_original_custom', 'dongpa_modified_custom'}
ACCOUNT_RUNTIME_ALLOWED_STRATEGY_IDS = {'dongpa_modified', 'dongpa_ma_v1', 'dongpa_ma_v1_1', 'dongpa_ma_v1_1_r', 'dongpa_ma_v1_1_r2', 'dongpa_original', 'dongpa_original_custom', 'dongpa_modified_custom'}

def _to_float(value, default=0.0):
    try:
        if isinstance(value, str):
            value = value.replace(',', '').strip()
        return float(value)
    except Exception:
        return float(default)

def _to_int(value, default=0):
    try:
        if isinstance(value, str):
            value = value.replace(',', '').strip()
        return int(float(value))
    except Exception:
        return int(default)

def _to_bool(value, default=False):
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {'1', 'true', 't', 'yes', 'y', 'on'}:
        return True
    if text in {'0', 'false', 'f', 'no', 'n', 'off'}:
        return False
    return bool(default)

def _uniform_rounds(count):
    n = max(1, int(count))
    value = 1.0 / float(n)
    rounds = [float(value) for _ in range(n)]
    if n >= 1:
        rounds[-1] = float(round(1.0 - sum(rounds[:-1]), 12))
    return rounds

def _rounds_from_percent_values(values):
    rounds = [float(v) / 100.0 for v in values]
    if rounds:
        rounds[-1] = float(round(1.0 - sum(rounds[:-1]), 12))
    return rounds
_DONGPA_MODIFIED = {'strategy_id': 'dongpa_modified', 'label': '기본', 'safe_mode': {'name': 'SF', 'max_holding_days': 35, 'buy_threshold': 1.035, 'sell_threshold': 1.011, 'rounds': [0.049, 0.127, 0.23, 0.257, 0.028, 0.169, 0.14]}, 'aggressive_mode': {'name': 'AG', 'max_holding_days': 7, 'buy_threshold': 1.036, 'sell_threshold': 1.035, 'rounds': [0.062, 0.134, 0.118, 0.148, 0.15, 0.182, 0.186, 0.02]}, 'settlement_delay_days': 7, 'profit_reflection_rate': 0.72, 'loss_reflection_rate': 0.213, 'reference_seed_renewal_days': 10}
_DONGPA_ORIGINAL = {'strategy_id': 'dongpa_original', 'label': '순정 모드', 'safe_mode': {'name': 'SF', 'max_holding_days': 30, 'buy_threshold': 1.03, 'sell_threshold': 1.002, 'rounds': _uniform_rounds(7)}, 'aggressive_mode': {'name': 'AG', 'max_holding_days': 7, 'buy_threshold': 1.05, 'sell_threshold': 1.025, 'rounds': _uniform_rounds(7)}, 'settlement_delay_days': 7, 'profit_reflection_rate': 0.8, 'loss_reflection_rate': 0.3, 'reference_seed_renewal_days': 10}
_DONGPA_MA_V1 = {'strategy_id': 'dongpa_ma_v1', 'label': '정배열 V1', 'description': 'SOXL 5일선 > 20일선 > 60일선 정배열일 때만 V1 매수/매도 기준을 적용하고, 정배열이 아니면 기본 매매법으로 자동 전환됩니다.', 'ma_alignment_required': True, 'safe_mode': {'name': 'SF', 'max_holding_days': 35, 'buy_threshold': 1.06, 'sell_threshold': 1.012, 'rounds': list(_DONGPA_MODIFIED['safe_mode']['rounds'])}, 'aggressive_mode': {'name': 'AG', 'max_holding_days': 7, 'buy_threshold': 1.165, 'sell_threshold': 1.054, 'rounds': list(_DONGPA_MODIFIED['aggressive_mode']['rounds'])}, 'settlement_delay_days': _DONGPA_MODIFIED['settlement_delay_days'], 'profit_reflection_rate': _DONGPA_MODIFIED['profit_reflection_rate'], 'loss_reflection_rate': _DONGPA_MODIFIED['loss_reflection_rate'], 'reference_seed_renewal_days': _DONGPA_MODIFIED['reference_seed_renewal_days']}

def _build_ma_v1_candidate(strategy_id, label, safe_buy_pct, safe_sell_pct, ag_buy_pct, ag_sell_pct, backtest_compare_only=True):
    context = copy.deepcopy(_DONGPA_MA_V1)
    availability_text = '현재는 관리자 전략비교 백테스트 전용 후보입니다.' if backtest_compare_only else '매매스위치에서 선택할 수 있는 실거래 프리셋입니다.'
    context.update({'strategy_id': str(strategy_id), 'label': str(label), 'description': f'SOXL 5일선 > 20일선 > 60일선 정배열일 때만 후보 매수/매도 기준을 적용하고, 정배열이 아니면 기본 매매법으로 자동 전환됩니다. {availability_text}', 'backtest_compare_only': bool(backtest_compare_only)})
    context['safe_mode']['buy_threshold'] = float(1.0 + float(safe_buy_pct) / 100.0)
    context['safe_mode']['sell_threshold'] = float(1.0 + float(safe_sell_pct) / 100.0)
    context['aggressive_mode']['buy_threshold'] = float(1.0 + float(ag_buy_pct) / 100.0)
    context['aggressive_mode']['sell_threshold'] = float(1.0 + float(ag_sell_pct) / 100.0)
    return context
_DONGPA_MA_V1_1 = _build_ma_v1_candidate(strategy_id='dongpa_ma_v1_1', label='정배열 V1.1', safe_buy_pct=6.25, safe_sell_pct=1.5, ag_buy_pct=15.25, ag_sell_pct=6.8, backtest_compare_only=False)
_DONGPA_MA_V1_1_R = _build_ma_v1_candidate(strategy_id='dongpa_ma_v1_1_r', label='정배열 V1.1-R', safe_buy_pct=6.5, safe_sell_pct=1.8, ag_buy_pct=16.0, ag_sell_pct=6.8, backtest_compare_only=False)
_DONGPA_MA_V1_1_R['description'] = 'SOXL 5일선 > 20일선 > 60일선 정배열일 때만 V1.1-R 리스크컷 기준을 적용하고, 정배열이 아니면 기본 매매법으로 자동 전환됩니다. 매매스위치에서 선택할 수 있는 실거래 프리셋입니다.'
_DONGPA_MA_V1_1_R['safe_mode']['max_holding_days'] = 30
_DONGPA_MA_V1_1_R['safe_mode']['rounds'] = _rounds_from_percent_values([13.97, 8.56, 2.0, 22.19, 9.55, 5.65, 20.28, 3.35, 8.12, 6.32])
_DONGPA_MA_V1_1_R['aggressive_mode']['max_holding_days'] = 7
_DONGPA_MA_V1_1_R['aggressive_mode']['rounds'] = _rounds_from_percent_values([20.41, 19.59, 30.0, 30.0])
_DONGPA_MA_V1_1_R2 = _build_ma_v1_candidate(strategy_id='dongpa_ma_v1_1_r2', label='정배열 V1.1-R2', safe_buy_pct=6.25, safe_sell_pct=1.5, ag_buy_pct=15.25, ag_sell_pct=6.8, backtest_compare_only=False)
_DONGPA_MA_V1_1_R2['description'] = 'SOXL 5일선 > 20일선 > 60일선 정배열일 때만 V1.1-R2 실전형 기준을 적용하고, 정배열이 아니면 기본 매매법으로 자동 전환됩니다. 현금 한도 안에서만 매수 수량을 산정하는 현재 엔진 기준으로 검증한 실거래 프리셋입니다.'
_DONGPA_MA_V1_1_R2['aggressive_mode']['rounds'] = _rounds_from_percent_values([20.0, 20.0, 30.0, 30.0])

def _canonical_strategy_id(strategy_id):
    sid = str(strategy_id or DEFAULT_STRATEGY_ID).strip().lower()
    if sid == 'custom':
        return 'dongpa_original_custom'
    if sid in STRATEGY_ID_ALIASES:
        return STRATEGY_ID_ALIASES[sid]
    return sid

def _normalize_strategy_id(strategy_id):
    sid = _canonical_strategy_id(strategy_id)
    if sid not in ALLOWED_STRATEGY_IDS:
        raise ValueError(f'지원하지 않는 strategy_id 입니다: {sid}')
    return sid

def ensure_account_strategy_id(strategy_id):
    sid = _canonical_strategy_id(strategy_id)
    if sid not in ACCOUNT_RUNTIME_ALLOWED_STRATEGY_IDS:
        raise ValueError(f'지원하지 않는 account strategy_id 입니다: {sid}')
    return sid

def normalize_account_strategy_id(strategy_id):
    sid = _canonical_strategy_id(strategy_id)
    if sid not in ACCOUNT_RUNTIME_ALLOWED_STRATEGY_IDS:
        return str(DEFAULT_STRATEGY_ID)
    return sid

def _validate_threshold_pct(name, value):
    num = float(_to_float(value, 0.0))
    if num < -99.0 or num > 300.0:
        raise ValueError(f'{name} 값이 허용 범위를 벗어났습니다')
    return num

def _validate_ratio_pct(name, value):
    num = float(_to_float(value, 0.0))
    if num < 0.0 or num > 100.0:
        raise ValueError(f'{name} 값은 0~100 사이여야 합니다')
    return num

def _round_threshold_to_pct(threshold):
    return round((float(threshold) - 1.0) * 100.0, 6)

def _normalize_rounds(values, default_rounds, name):
    if values is None:
        return list(default_rounds)
    if not isinstance(values, list):
        raise ValueError(f'{name}는 list여야 합니다')
    if len(values) != len(default_rounds):
        raise ValueError(f'{name}는 {len(default_rounds)}개여야 합니다')
    parsed = []
    for raw in values:
        number = float(_to_float(raw, -1.0))
        if number < 0:
            raise ValueError(f'{name}에는 음수를 사용할 수 없습니다')
        parsed.append(number)
    total_raw = sum(parsed)
    if total_raw <= 0:
        raise ValueError(f'{name} 합계는 0보다 커야 합니다')
    if max(parsed) > 1.0 or total_raw > 1.5:
        parsed = [v / 100.0 for v in parsed]
    total = sum(parsed)
    if total <= 0:
        raise ValueError(f'{name} 합계는 0보다 커야 합니다')
    normalized = [float(v / total) for v in parsed]
    normalized[-1] = float(round(1.0 - sum(normalized[:-1]), 12))
    return normalized

def _build_original_custom_context(custom_params=None):
    params = dict(custom_params or {})
    seed_split_count = _to_int(params.get('seed_split_count', 7), 7)
    if seed_split_count <= 0 or seed_split_count > 20:
        raise ValueError('seed_split_count는 1~20 사이여야 합니다')
    safe_buy_pct = _validate_threshold_pct('safe_buy_pct', params.get('safe_buy_pct', 3.0))
    safe_sell_pct = _validate_threshold_pct('safe_sell_pct', params.get('safe_sell_pct', 0.2))
    safe_holding_days = _to_int(params.get('safe_holding_days', 30), 30)
    if safe_holding_days <= 0 or safe_holding_days > 365:
        raise ValueError('safe_holding_days는 1~365 사이여야 합니다')
    aggressive_buy_pct = _validate_threshold_pct('aggressive_buy_pct', params.get('aggressive_buy_pct', 5.0))
    aggressive_sell_pct = _validate_threshold_pct('aggressive_sell_pct', params.get('aggressive_sell_pct', 2.5))
    aggressive_holding_days = _to_int(params.get('aggressive_holding_days', 7), 7)
    if aggressive_holding_days <= 0 or aggressive_holding_days > 365:
        raise ValueError('aggressive_holding_days는 1~365 사이여야 합니다')
    renewal_days = _to_int(params.get('renewal_days', 10), 10)
    if renewal_days <= 0 or renewal_days > 120:
        raise ValueError('renewal_days는 1~120 사이여야 합니다')
    profit_reflection_pct = _validate_ratio_pct('profit_reflection_pct', params.get('profit_reflection_pct', 80.0))
    loss_reflection_pct = _validate_ratio_pct('loss_reflection_pct', params.get('loss_reflection_pct', 30.0))
    settlement_delay_days = _to_int(params.get('settlement_delay_days', 7), 7)
    if settlement_delay_days < 0 or settlement_delay_days > 120:
        raise ValueError('settlement_delay_days는 0~120 사이여야 합니다')
    rounds = _uniform_rounds(seed_split_count)
    return {'strategy_id': 'dongpa_original_custom', 'label': '순정 커스텀', 'safe_mode': {'name': 'SF', 'max_holding_days': int(safe_holding_days), 'buy_threshold': float(1.0 + safe_buy_pct / 100.0), 'sell_threshold': float(1.0 + safe_sell_pct / 100.0), 'rounds': list(rounds)}, 'aggressive_mode': {'name': 'AG', 'max_holding_days': int(aggressive_holding_days), 'buy_threshold': float(1.0 + aggressive_buy_pct / 100.0), 'sell_threshold': float(1.0 + aggressive_sell_pct / 100.0), 'rounds': list(rounds)}, 'settlement_delay_days': int(settlement_delay_days), 'profit_reflection_rate': float(profit_reflection_pct / 100.0), 'loss_reflection_rate': float(loss_reflection_pct / 100.0), 'reference_seed_renewal_days': int(renewal_days), 'custom_params': {'seed_split_count': int(seed_split_count), 'safe_buy_pct': float(safe_buy_pct), 'safe_sell_pct': float(safe_sell_pct), 'safe_holding_days': int(safe_holding_days), 'aggressive_buy_pct': float(aggressive_buy_pct), 'aggressive_sell_pct': float(aggressive_sell_pct), 'aggressive_holding_days': int(aggressive_holding_days), 'renewal_days': int(renewal_days), 'profit_reflection_pct': float(profit_reflection_pct), 'loss_reflection_pct': float(loss_reflection_pct), 'settlement_delay_days': int(settlement_delay_days)}}

def _build_modified_custom_context(custom_params=None):
    params = dict(custom_params or {})
    safe_base = _DONGPA_MODIFIED['safe_mode']
    ag_base = _DONGPA_MODIFIED['aggressive_mode']
    safe_buy_pct = _validate_threshold_pct('safe_buy_pct', params.get('safe_buy_pct', _round_threshold_to_pct(safe_base['buy_threshold'])))
    safe_sell_pct = _validate_threshold_pct('safe_sell_pct', params.get('safe_sell_pct', _round_threshold_to_pct(safe_base['sell_threshold'])))
    safe_holding_days = _to_int(params.get('safe_holding_days', safe_base['max_holding_days']), safe_base['max_holding_days'])
    if safe_holding_days <= 0 or safe_holding_days > 365:
        raise ValueError('safe_holding_days는 1~365 사이여야 합니다')
    aggressive_buy_pct = _validate_threshold_pct('aggressive_buy_pct', params.get('aggressive_buy_pct', _round_threshold_to_pct(ag_base['buy_threshold'])))
    aggressive_sell_pct = _validate_threshold_pct('aggressive_sell_pct', params.get('aggressive_sell_pct', _round_threshold_to_pct(ag_base['sell_threshold'])))
    aggressive_holding_days = _to_int(params.get('aggressive_holding_days', ag_base['max_holding_days']), ag_base['max_holding_days'])
    if aggressive_holding_days <= 0 or aggressive_holding_days > 365:
        raise ValueError('aggressive_holding_days는 1~365 사이여야 합니다')
    renewal_days = _to_int(params.get('renewal_days', _DONGPA_MODIFIED['reference_seed_renewal_days']), 10)
    if renewal_days <= 0 or renewal_days > 120:
        raise ValueError('renewal_days는 1~120 사이여야 합니다')
    profit_reflection_pct = _validate_ratio_pct('profit_reflection_pct', params.get('profit_reflection_pct', _DONGPA_MODIFIED['profit_reflection_rate'] * 100.0))
    loss_reflection_pct = _validate_ratio_pct('loss_reflection_pct', params.get('loss_reflection_pct', _DONGPA_MODIFIED['loss_reflection_rate'] * 100.0))
    settlement_delay_days = _to_int(params.get('settlement_delay_days', _DONGPA_MODIFIED['settlement_delay_days']), 7)
    if settlement_delay_days < 0 or settlement_delay_days > 120:
        raise ValueError('settlement_delay_days는 0~120 사이여야 합니다')
    safe_equal_split = _to_bool(params.get('safe_equal_split'), False)
    aggressive_equal_split = _to_bool(params.get('aggressive_equal_split'), False)
    safe_rounds = _uniform_rounds(len(safe_base['rounds'])) if safe_equal_split else _normalize_rounds(params.get('safe_rounds_pct', params.get('safe_rounds')), safe_base['rounds'], 'safe_rounds')
    aggressive_rounds = _uniform_rounds(len(ag_base['rounds'])) if aggressive_equal_split else _normalize_rounds(params.get('aggressive_rounds_pct', params.get('aggressive_rounds')), ag_base['rounds'], 'aggressive_rounds')
    return {'strategy_id': 'dongpa_modified_custom', 'label': '커스텀', 'safe_mode': {'name': 'SF', 'max_holding_days': int(safe_holding_days), 'buy_threshold': float(1.0 + safe_buy_pct / 100.0), 'sell_threshold': float(1.0 + safe_sell_pct / 100.0), 'rounds': list(safe_rounds)}, 'aggressive_mode': {'name': 'AG', 'max_holding_days': int(aggressive_holding_days), 'buy_threshold': float(1.0 + aggressive_buy_pct / 100.0), 'sell_threshold': float(1.0 + aggressive_sell_pct / 100.0), 'rounds': list(aggressive_rounds)}, 'settlement_delay_days': int(settlement_delay_days), 'profit_reflection_rate': float(profit_reflection_pct / 100.0), 'loss_reflection_rate': float(loss_reflection_pct / 100.0), 'reference_seed_renewal_days': int(renewal_days), 'custom_params': {'safe_buy_pct': float(safe_buy_pct), 'safe_sell_pct': float(safe_sell_pct), 'safe_holding_days': int(safe_holding_days), 'aggressive_buy_pct': float(aggressive_buy_pct), 'aggressive_sell_pct': float(aggressive_sell_pct), 'aggressive_holding_days': int(aggressive_holding_days), 'safe_equal_split': bool(safe_equal_split), 'aggressive_equal_split': bool(aggressive_equal_split), 'safe_rounds_pct': [float(v * 100.0) for v in safe_rounds], 'aggressive_rounds_pct': [float(v * 100.0) for v in aggressive_rounds], 'renewal_days': int(renewal_days), 'profit_reflection_pct': float(profit_reflection_pct), 'loss_reflection_pct': float(loss_reflection_pct), 'settlement_delay_days': int(settlement_delay_days)}}

def get_strategy_context(strategy_id=None, custom_params=None):
    sid = _normalize_strategy_id(strategy_id)
    if sid == 'dongpa_modified':
        return copy.deepcopy(_DONGPA_MODIFIED)
    if sid == 'dongpa_ma_v1':
        return copy.deepcopy(_DONGPA_MA_V1)
    if sid == 'dongpa_ma_v1_1':
        return copy.deepcopy(_DONGPA_MA_V1_1)
    if sid == 'dongpa_ma_v1_1_r':
        return copy.deepcopy(_DONGPA_MA_V1_1_R)
    if sid == 'dongpa_ma_v1_1_r2':
        return copy.deepcopy(_DONGPA_MA_V1_1_R2)
    if sid == 'dongpa_original':
        return copy.deepcopy(_DONGPA_ORIGINAL)
    if sid == 'dongpa_modified_custom':
        return _build_modified_custom_context(custom_params=custom_params)
    return _build_original_custom_context(custom_params=custom_params)

def get_mode_config_for_strategy(mode, strategy_context=None):
    context = strategy_context or get_strategy_context(DEFAULT_STRATEGY_ID)
    mode_norm = str(mode or 'SF').upper()
    if mode_norm == 'AG':
        return copy.deepcopy(context['aggressive_mode'])
    return copy.deepcopy(context['safe_mode'])

def serialize_strategy_context_for_cache(strategy_context):
    context = strategy_context or {}
    serialized = {'strategy_id': str(context.get('strategy_id') or DEFAULT_STRATEGY_ID), 'safe_mode': dict(context.get('safe_mode') or {}), 'aggressive_mode': dict(context.get('aggressive_mode') or {}), 'settlement_delay_days': int(_to_int(context.get('settlement_delay_days', 7), 7)), 'profit_reflection_rate': float(_to_float(context.get('profit_reflection_rate', 0.0), 0.0)), 'loss_reflection_rate': float(_to_float(context.get('loss_reflection_rate', 0.0), 0.0)), 'reference_seed_renewal_days': int(_to_int(context.get('reference_seed_renewal_days', 10), 10)), 'custom_params': dict(context.get('custom_params') or {})}
    if 'ma_alignment_required' in context:
        serialized['ma_alignment_required'] = bool(context.get('ma_alignment_required'))
    if 'backtest_compare_only' in context:
        serialized['backtest_compare_only'] = bool(context.get('backtest_compare_only'))
    return serialized
