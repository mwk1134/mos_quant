"""
Market Mode Manager
시장 모드 결정 로직 (공세 AG vs 안전 SF)
"""

from .strategy_presets import DEFAULT_STRATEGY_ID, get_mode_config_for_strategy, get_strategy_context


def determine_mode(rsi_t1, rsi_t2, previous_mode=None):
    """
    QQQ 주봉 RSI 기반 모드 결정

    Args:
        rsi_t1: 지난주 RSI (Week N-1)
        rsi_t2: 지지난주 RSI (Week N-2)
        previous_mode: 이전 모드 ('AG' or 'SF')

    Returns:
        str: 'AG' (공세) or 'SF' (안전)

    규칙:
    - 명시된 조건에만 모드 전환
    - 조건 미충족 시 이전 모드 유지
    - 35~40, 60~65 등 중간 구간은 이전 모드 유지
    """

    # 상승/하락 판단
    is_rising = rsi_t1 > rsi_t2
    is_falling = rsi_t1 < rsi_t2

    # 공세 모드 (Aggressive) 전환 조건
    # 1. 35 이하에서 상승 전환
    if is_rising and rsi_t2 <= 35:
        return 'AG'

    # 2. 50 이하에서 50 상향 돌파
    if rsi_t2 <= 50 and rsi_t1 > 50:
        return 'AG'

    # 3. 50~60 구간에서 상승 전환
    if is_rising and 50 < rsi_t2 <= 60:
        return 'AG'

    # 안전 모드 (Safe) 전환 조건
    # 1. 65 이상에서 하락 전환
    if is_falling and rsi_t2 >= 65:
        return 'SF'

    # 2. 50 이상에서 50 하향 돌파
    if rsi_t2 >= 50 and rsi_t1 < 50:
        return 'SF'

    # 3. 40~50 구간에서 하락 전환
    if is_falling and 40 <= rsi_t2 < 50:
        return 'SF'

    # 조건 미충족 시 이전 모드 유지
    # (35~40, 60~65 등 중간 구간 포함)
    if previous_mode:
        return previous_mode

    # 이전 모드가 없으면 기본값 (안전 모드)
    return 'SF'


def get_mode_config(mode, strategy_id=None, custom_params=None, strategy_context=None):
    """
    모드에 해당하는 설정 반환

    Args:
        mode: 'AG' or 'SF'

    Returns:
        dict: 모드 설정
    """
    context = strategy_context
    if context is None:
        resolved_strategy_id = strategy_id or DEFAULT_STRATEGY_ID
        context = get_strategy_context(strategy_id=resolved_strategy_id, custom_params=custom_params)
    return get_mode_config_for_strategy(mode, strategy_context=context)


def get_mode_trigger_reason(rsi_t1, rsi_t2, mode):
    """
    모드가 트리거된 이유 반환

    Returns:
        str: 트리거 이유 설명
    """
    if mode == 'AG':
        if rsi_t2 <= 50 and rsi_t1 > 50:
            return f"50 상향 돌파 ({rsi_t2:.1f} → {rsi_t1:.1f})"
        elif 50 < rsi_t1 <= 60 and rsi_t1 > rsi_t2:
            return f"중구간 상승 ({rsi_t2:.1f} → {rsi_t1:.1f})"
        elif rsi_t1 < 35 and rsi_t1 > rsi_t2:
            return f"과매도 반등 ({rsi_t2:.1f} → {rsi_t1:.1f})"
    else:  # SF
        if rsi_t2 > 65 and rsi_t1 < rsi_t2:
            return f"과열권 하락 ({rsi_t2:.1f} → {rsi_t1:.1f})"
        elif 40 <= rsi_t1 < 50 and rsi_t1 < rsi_t2:
            return f"약세구간 하락 ({rsi_t2:.1f} → {rsi_t1:.1f})"
        elif rsi_t2 >= 50 and rsi_t1 < 50:
            return f"50 하향 돌파 ({rsi_t2:.1f} → {rsi_t1:.1f})"

    return f"이전 모드 유지 ({rsi_t2:.1f} → {rsi_t1:.1f})"
