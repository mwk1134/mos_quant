def calculate_rsi(data, window=14):
    """
    Simple rolling mean을 사용한 RSI 계산 (사용자 방식)

    Args:
        data: DataFrame with 'Close' column
        window: RSI 기간 (기본 14)

    Returns:
        Series with RSI values
    """
    delta = data['Close'].diff()

    # 상승분과 하락분 분리
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    # Simple rolling mean (사용자 방식)
    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()

    # RS와 RSI 계산
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi
