import traceback
import unittest
from unittest.mock import Mock, patch

import requests

from telegram_notify import (
    MAX_TEXT_LENGTH,
    TelegramNotificationError,
    TelegramNotifier,
    format_order_notification,
)


TOKEN = "123456789:abcdefghijklmnopqrstuvwxyz_123456789"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "-1001234567890"}
ORDER = {"ticker": "SOXL", "side": "BUY", "quantity": 1, "price": "144.79", "order_type": "LOC", "round": 3}
PLAN = {"preset": "KMW", "order_date": "2026-10-12", "generated_at": "2026-10-12T08:01:00+00:00", "orders": [ORDER]}
RESULT = {"order": ORDER, "state": "RESERVED", "response": {"data": {"oder_rsev_mst_no": 23}}}


def reply(body=None, status=200):
    response = Mock()
    response.status_code = status
    response.json.return_value = body
    return response


class TelegramNotifierTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.notifier = TelegramNotifier.from_env(ENV, session=self.session)

    def assert_secret_safe(self, error):
        rendered = "".join(traceback.format_exception(error))
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn("secret-response", rendered)
        self.assertNotIn("secret-account", rendered)

    def test_unconfigured_notification_is_disabled_and_does_not_use_network(self):
        self.assertIsNone(TelegramNotifier.from_env({}, session=self.session))
        self.assertIsNone(TelegramNotifier.from_env({"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, session=self.session))
        self.session.post.assert_not_called()

    def test_from_env_uses_environment_when_mapping_is_not_given(self):
        with patch.dict("os.environ", ENV, clear=True):
            self.assertIsInstance(TelegramNotifier.from_env(session=self.session), TelegramNotifier)
        self.session.post.assert_not_called()

    def test_partial_configuration_is_rejected_before_network(self):
        for env in ({"TELEGRAM_BOT_TOKEN": TOKEN}, {"TELEGRAM_CHAT_ID": "12345"}):
            with self.subTest(env=list(env)), self.assertRaises(TelegramNotificationError) as caught:
                TelegramNotifier.from_env(env, session=self.session)
            self.assert_secret_safe(caught.exception)
        self.session.post.assert_not_called()

    def test_invalid_token_never_appears_in_error_or_repr(self):
        for token in ("", "secret-response", TOKEN + "/redirect", "https://secret-account", TOKEN + "\n", None):
            with self.subTest(token_type=type(token)), self.assertRaises(TelegramNotificationError) as caught:
                TelegramNotifier(token, "12345", session=self.session)
            self.assert_secret_safe(caught.exception)
        self.assertNotIn(TOKEN, repr(self.notifier))
        self.session.post.assert_not_called()

    def test_numeric_private_and_negative_group_chat_ids_are_supported(self):
        for identifier in ("1234567890", 1234567890, "-1001234567890", -1001234567890):
            with self.subTest(identifier=identifier):
                notifier = TelegramNotifier(TOKEN, identifier, session=self.session)
                self.session.post.return_value = reply({"ok": True, "result": {"message_id": 12}})
                self.assertEqual(notifier.send("테스트"), 12)
                self.assertEqual(self.session.post.call_args.kwargs["json"]["chat_id"], int(identifier))

    def test_chat_id_must_be_explicit_nonzero_numeric_identifier(self):
        for identifier in ("", "0", "-0", "@username", "00123", "123\n", " 123", True, None, 2 ** 52):
            with self.subTest(identifier=identifier), self.assertRaises(TelegramNotificationError):
                TelegramNotifier(TOKEN, identifier, session=self.session)
        self.session.post.assert_not_called()

    def test_success_posts_utf8_text_once_over_https_without_redirect_or_parse_mode(self):
        self.session.post.return_value = reply({"ok": True, "result": {"message_id": 42}})
        self.assertEqual(self.notifier.send("KMW 매수 1주 예약 접수"), 42)
        self.session.post.assert_called_once_with(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": -1001234567890, "text": "KMW 매수 1주 예약 접수"},
            timeout=10,
            allow_redirects=False,
        )

    def test_invalid_and_oversized_text_are_rejected_before_network(self):
        for text in ("", "한" * (MAX_TEXT_LENGTH + 1), None, 123, "\ud800"):
            with self.subTest(text_type=type(text)), self.assertRaises(TelegramNotificationError):
                self.notifier.send(text)
        self.session.post.assert_not_called()

    def test_maximum_character_count_is_supported_for_utf8_text(self):
        self.session.post.return_value = reply({"ok": True, "result": {"message_id": 1}})
        self.assertEqual(self.notifier.send("한" * MAX_TEXT_LENGTH), 1)

    def test_transport_failures_are_sanitized_unknown_and_never_retried(self):
        for error_type in (requests.Timeout, requests.ConnectionError, requests.RequestException):
            with self.subTest(error=error_type):
                self.session.reset_mock()
                self.session.post.side_effect = error_type(f"https://api.telegram.org/bot{TOKEN}/sendMessage secret-response secret-account")
                with self.assertRaises(TelegramNotificationError) as caught:
                    self.notifier.send("KMW")
                self.assertTrue(caught.exception.delivery_unknown)
                self.assert_secret_safe(caught.exception)
                self.session.post.assert_called_once()

    def test_http_errors_and_redirects_do_not_parse_or_print_response_body(self):
        for status, uncertain in ((301, True), (400, False), (401, False), (403, False), (408, True), (429, False), (500, True), (502, True)):
            with self.subTest(status=status):
                self.session.reset_mock()
                response = reply({"description": TOKEN + " secret-response"}, status=status)
                self.session.post.return_value = response
                with self.assertRaises(TelegramNotificationError) as caught:
                    self.notifier.send("KMW")
                self.assertEqual(caught.exception.delivery_unknown, uncertain)
                self.assert_secret_safe(caught.exception)
                response.json.assert_not_called()
                self.session.post.assert_called_once()

    def test_telegram_rejection_with_http200_is_not_success(self):
        self.session.post.return_value = reply({"ok": False, "error_code": 400, "description": TOKEN + " secret-response"})
        with self.assertRaises(TelegramNotificationError) as caught:
            self.notifier.send("KMW")
        self.assertFalse(caught.exception.delivery_unknown)
        self.assert_secret_safe(caught.exception)
        self.session.post.assert_called_once()

    def test_unreadable_response_is_unknown_and_sanitized(self):
        response = reply()
        response.json.side_effect = ValueError(TOKEN + " secret-response")
        self.session.post.return_value = response
        with self.assertRaises(TelegramNotificationError) as caught:
            self.notifier.send("KMW")
        self.assertTrue(caught.exception.delivery_unknown)
        self.assert_secret_safe(caught.exception)
        self.session.post.assert_called_once()

    def test_http200_requires_true_ok_and_positive_integer_message_id(self):
        malformed = (None, [], {}, {"ok": "true", "result": {"message_id": 1}},
                     {"ok": True}, {"ok": True, "result": []},
                     *({"ok": True, "result": {"message_id": value}} for value in (None, True, False, "1", 0, -1, 1.1)))
        for body in malformed:
            with self.subTest(body=body):
                self.session.reset_mock()
                self.session.post.return_value = reply(body)
                with self.assertRaises(TelegramNotificationError) as caught:
                    self.notifier.send("KMW")
                self.assertTrue(caught.exception.delivery_unknown)
                self.session.post.assert_called_once()


class OrderNotificationFormattingTests(unittest.TestCase):
    def test_jeh_notification_identifies_the_account_and_preserves_receipt(self):
        result = {**RESULT, "state": "DIRECT_ACCEPTED", "response": {"data": {"oder_no": 1234}}}
        text = format_order_notification({**PLAN, "preset": "JEH", "order_route": "direct"}, [result])
        self.assertTrue(text.startswith("JEH 자동매매 · SOXL\n"))
        self.assertIn("LOC 주문 접수 완료", text)
        self.assertIn("주문번호 1234", text)
        self.assertIn("매수 1주 LOC $144.79", text)

    def test_kmw2_notification_is_labeled_with_the_second_account_preset(self):
        text = format_order_notification({**PLAN, "preset": "KMW2"}, [RESULT])
        self.assertTrue(text.startswith("KMW2 자동매매 · SOXL\n"))
        self.assertIn("예약번호 23", text)

    def test_success_distinguishes_reservation_from_fill(self):
        text = format_order_notification(PLAN, [RESULT])
        for value in ("KMW", "SOXL", "예약주문 접수 완료", "미국 주문일: 2026-10-12", "KST", "2026-10-12 17:01:00", "매수 1주 LOC $144.79", "3회차", "예약번호 23", "체결 완료 알림이 아닙니다"):
            self.assertIn(value, text)
        self.assertLess(len(text), 600)

    def test_moc_sell_does_not_display_zero_as_a_limit_price(self):
        order = {**ORDER, "side": "SELL", "order_type": "MOC", "price": "0"}
        text = format_order_notification(PLAN, [{**RESULT, "order": order}])
        self.assertIn("매도 1주 MOC 종가 시장가", text)
        self.assertNotIn("$0.00", text)

    def test_partial_failure_lists_accepted_and_stopped_order(self):
        failed = {**ORDER, "side": "SELL", "round": 4, "price": "145.65"}
        text = format_order_notification(PLAN, [RESULT], error=RuntimeError(TOKEN), failed_order=failed, failed_state="REJECTED", not_sent_count=2)
        self.assertIn("일부 예약 접수 후 전송 중단", text)
        self.assertIn("예약번호 23", text)
        self.assertIn("실패: 매도 1주 LOC $145.65 · 4회차", text)
        self.assertIn("남은 2건은 전송하지 않았습니다", text)
        self.assertNotIn(TOKEN, text)

    def test_unknown_submission_is_clearly_reported_without_retry(self):
        text = format_order_notification(PLAN, [], error=RuntimeError(TOKEN), failed_order=ORDER, failed_state="UNKNOWN", not_sent_count=1)
        self.assertIn("접수 여부 불확실", text)
        self.assertIn("불확실: 매수", text)
        self.assertIn("주문을 다시 보내지 마세요", text)
        self.assertNotIn("접수 완료", text)
        self.assertNotIn(TOKEN, text)

    def test_preflight_failure_does_not_claim_order_was_placed(self):
        text = format_order_notification(PLAN, [], error=RuntimeError("secret-account"))
        self.assertIn("주문 전 확인 과정이 중단", text)
        self.assertNotIn("접수 완료", text)
        self.assertNotIn("secret-account", text)

    def test_formatter_never_includes_arbitrary_broker_response_or_plan_metadata(self):
        plan = {**PLAN, "account": "secret-account", "app_key": TOKEN, "snapshot_source": "secret-response", "preset": TOKEN}
        result = {**RESULT, "response": {"data": {"oder_rsev_mst_no": 23, "token": TOKEN, "account": "secret-account"}, "rsp_msg": "secret-response"}}
        text = format_order_notification(plan, [result], error=RuntimeError(TOKEN))
        for forbidden in (TOKEN, "secret-account", "secret-response"):
            self.assertNotIn(forbidden, text)

    def test_invalid_allowlisted_field_values_are_not_echoed(self):
        order = {"side": TOKEN, "quantity": "secret-account", "order_type": "LOC", "price": "secret-response", "round": TOKEN}
        plan = {"order_date": TOKEN, "generated_at": "secret-account"}
        result = {"order": order, "state": "RESERVED", "response": {"data": {"oder_rsev_mst_no": TOKEN}}}
        text = format_order_notification(plan, [result])
        for forbidden in (TOKEN, "secret-account", "secret-response"):
            self.assertNotIn(forbidden, text)
        self.assertIn("확인 필요", text)

    def test_large_result_batches_stay_within_telegram_limit_and_keep_fill_note(self):
        text = format_order_notification(PLAN, [RESULT] * 1000, error=RuntimeError("fail"), failed_order=ORDER, failed_state="UNKNOWN", not_sent_count=1000)
        self.assertLessEqual(len(text), MAX_TEXT_LENGTH)
        self.assertIn("추가 주문 내역", text)
        self.assertIn("체결 완료 알림이 아닙니다", text)
        self.assertIn("주문을 다시 보내지 마세요", text)

    def test_empty_batch_is_reported_as_no_orders(self):
        text = format_order_notification(PLAN, [])
        self.assertIn("주문 없음", text)
        self.assertNotIn("접수 완료", text)


if __name__ == "__main__":
    unittest.main()
