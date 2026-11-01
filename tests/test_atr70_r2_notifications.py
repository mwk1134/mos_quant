from telegram_notify import format_order_notification


def test_mixed_open_exit_and_intraday_buy_are_not_mislabeled_close_orders():
    orders = [{"side": "SELL", "quantity": 8, "price": "0", "order_type": "MOO", "strategy": "ATR"},
              {"side": "BUY", "quantity": 9, "price": "60", "order_type": "MARKET", "strategy": "ATR"}]
    results = [{"order": order, "state": "DIRECT_ACCEPTED", "response": {"data": {"oder_no": i + 1}}}
               for i, order in enumerate(orders)]
    text = format_order_notification({"preset": "KMW2", "order_route": "direct", "orders": orders}, results)
    assert "혼합 주문 접수 완료" in text
    assert "MOO 시가 시장가" in text
    assert "장중 시장가" in text
    assert "종가 주문" not in text
    assert "체결 완료 알림이 아닙니다" in text
