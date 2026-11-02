from decimal import Decimal
from .atr_settings import AtrSettingsError


MODEL = 'ADVERSE_NOTIONAL_COST_V1'


DEFAULTS = {'buy_slippage_pct': 0.0, 'sell_slippage_pct': 0.0}


def validate_slippage(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULTS):
        raise AtrSettingsError('매수·매도 슬리피지 두 값을 입력해주세요')
    result = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AtrSettingsError('슬리피지는 각각 0~5% 숫자여야 합니다')
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= 5 or number != number.quantize(Decimal('.0001')):
            raise AtrSettingsError('슬리피지는 각각 0~5%, 소수점 넷째 자리까지 입력해주세요')
        result[key] = float(number)
    return result
