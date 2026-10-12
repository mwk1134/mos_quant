import unittest
from unittest.mock import Mock, patch

import requests

from meritz_api import (
    BASE_URL,
    AmbiguousSubmission,
    MeritzAPIError,
    MeritzClient,
    MeritzError,
    MeritzWarning,
)


def reply(body, status=200):
    response = Mock()
    response.status_code = status
    response.json.return_value = body
    return response


TOKEN = {"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 3600}
EMPTY = {"rsp_cd": "5820", "tr_cont": "0"}
RECEIPT = {"rsp_cd": "5620", "data": {"oder_rsev_mst_no": 23, "warn_cls_code": "0", "warn_msg": ""}}


class MeritzClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.client = MeritzClient("fake-app-key", "fake-app-secret", "aabbccddeeff", session=self.session)

    def prepare(self, *responses):
        self.session.request.side_effect = [reply(TOKEN), *responses]

    def submit(self, **overrides):
        values = dict(side="buy", symbol="SOXL", exchange_vendor_code="0066", quantity=3, price="45.12")
        values.update(overrides)
        return self.client.submit_reserved_order(**values)

    def test_from_env_is_lazy_and_mac_must_be_explicit(self):
        env = {"MERITZ_APP_KEY": "fake-key", "MERITZ_APP_SECRET": "fake-secret", "MERITZ_MAC_ADDRESS": "ABCDEF123456"}
        client = MeritzClient.from_env(env=env, session=self.session)
        self.assertIsInstance(client, MeritzClient)
        self.session.request.assert_not_called()
        for mac in ("", "aa:bb:cc:dd:ee:ff", "abcdefghijkl", "00112233445", "001122334455 "):
            with self.subTest(mac=mac), self.assertRaises(ValueError):
                MeritzClient.from_env(env={**env, "MERITZ_MAC_ADDRESS": mac})

    def test_oauth_uses_form_and_caches_token_in_memory(self):
        self.prepare(reply(EMPTY), reply(EMPTY))
        self.client.holdings()
        self.client.holdings()
        self.assertEqual(self.session.request.call_count, 3)
        auth = self.session.request.call_args_list[0]
        self.assertEqual(auth.args, ("POST", BASE_URL + "/oauth2/token"))
        self.assertEqual(auth.kwargs["data"], {"grant_type": "client_credentials", "client_id": "fake-app-key", "client_secret": "fake-app-secret", "scope": "login"})
        query = self.session.request.call_args_list[1]
        self.assertEqual(query.kwargs["headers"]["Authorization"], "Bearer test-access-token")
        self.assertEqual(query.kwargs["headers"]["mac_address"], "AABBCCDDEEFF")
        self.assertFalse(query.kwargs["allow_redirects"])
        self.assertFalse(auth.kwargs["allow_redirects"])

    def test_token_expiry_is_refreshed_before_next_query(self):
        self.prepare(reply(EMPTY), reply(TOKEN), reply(EMPTY))
        with patch("meritz_api.time.monotonic", return_value=100):
            self.client.holdings()
        with patch("meritz_api.time.monotonic", return_value=4000):
            self.client.holdings()
        self.assertEqual(self.session.request.call_args_list[2].args[1], BASE_URL + "/oauth2/token")

    def test_token_http200_without_valid_token_is_rejected(self):
        for body in (
            {"error": "invalid_client"}, {**TOKEN, "expires_in": 0}, {**TOKEN, "expires_in": "NaN"},
            {**TOKEN, "token_type": "Other"}, {**TOKEN, "token_type": None},
        ):
            with self.subTest(body=body), self.assertRaises(MeritzAPIError):
                self.session.request.side_effect = [reply(body)]
                self.client.authenticate()

    def test_queries_validate_rsp_code_even_if_http200(self):
        self.prepare(reply({"rsp_cd": "7936", "rsp_msg": "invalid query"}))
        with self.assertRaises(MeritzAPIError) as error:
            self.client.holdings()
        self.assertEqual(error.exception.response["rsp_cd"], "7936")
        self.assertEqual(self.session.request.call_count, 2)

    def test_reference_0000_http500_is_not_success(self):
        self.prepare(reply({"rsp_cd": "0000"}, status=500))
        with self.assertRaises(MeritzAPIError):
            self.client.market_hours()

    def test_quote_uses_endpoint_specific_success_code_and_matches_symbol(self):
        quote = {"iscd": "SOXL", "shnu_aspr_unit": "0.0000", "shnu_oder_qty_unit": 1}
        self.prepare(reply({"rsp_cd": "0000", "data": quote}))
        self.assertEqual(self.client.quote("SOXL", "AX"), quote)
        self.assertEqual(self.session.request.call_args.kwargs["params"], {
            "mrkt_div_code": "OV", "mrkt_cls_code": "AX", "iscd": "SOXL", "dely_rltm_cls_code": "0",
        })

    def test_quote_http200_and_0000_with_empty_symbol_is_failure(self):
        self.prepare(reply({"rsp_cd": "0000", "data": {"iscd": "", "prpr": "0"}}))
        with self.assertRaises(MeritzAPIError):
            self.client.quote("SOXL", "AX")

    def test_missing_query_data_fails_closed(self):
        self.prepare(reply({"rsp_cd": "5766"}))
        with self.assertRaises(MeritzAPIError):
            self.client.holdings()

    def test_no_data_code_returns_empty_rows(self):
        self.prepare(reply(EMPTY))
        self.assertEqual(self.client.holdings(), [])

    def test_exchange_pagination_preserves_trailing_space_key(self):
        self.prepare(
            reply({"rsp_cd": "5762", "data": [{"ovrs_stck_exch_idno": "AX"}], "tr_cont": "1", "tr_cont_key": "next page   "}),
            reply({"rsp_cd": "5766", "data": [{"ovrs_stck_exch_idno": "NY"}], "tr_cont": "0"}),
        )
        rows = self.client.exchanges()
        self.assertEqual([row["ovrs_stck_exch_idno"] for row in rows], ["AX", "NY"])
        self.assertEqual(self.session.request.call_args.kwargs["params"], {"ovrs_stck_mrkt_tr_cls_code": "01", "tr_cont": "1", "tr_cont_key": "next page   "})

    def test_repeated_pagination_key_aborts_instead_of_looping(self):
        page = {"rsp_cd": "5762", "data": [{}], "tr_cont": "1", "tr_cont_key": "same"}
        self.prepare(reply(page), reply(page))
        with self.assertRaises(MeritzAPIError):
            self.client.orders_history("20261001", "20261012")
        self.assertEqual(self.session.request.call_count, 3)

    def test_unexpected_pagination_cannot_silently_truncate_holdings(self):
        self.prepare(reply({"rsp_cd": "5762", "data": [], "tr_cont": "1", "tr_cont_key": "next"}))
        with self.assertRaises(MeritzAPIError):
            self.client.holdings()

    def test_reservation_query_supplies_all_three_required_filters(self):
        self.prepare(reply(EMPTY))
        self.assertEqual(self.client.reserved_orders("20261001", "20261012"), [])
        self.assertEqual(self.session.request.call_args.kwargs["params"], {"iqry_sta_date": "20261001", "iqry_fin_date": "20261012", "seln_byov_clcd": "0", "iqry_cls_code": "2", "iqry_idnt_clcd": "0"})

    def test_query_dates_filters_and_market_codes_validate_before_network(self):
        invalid_queries = (
            lambda: self.client.orders_history("20260231", "20261012"),
            lambda: self.client.orders_history("20261013", "20261012"),
            lambda: self.client.orders_history("20261001", "20261012", execution=""),
            lambda: self.client.reserved_orders("20261001", "20261012", side="3"),
            lambda: self.client.market_hours("1"),
            lambda: self.client.market_hours("０１"),
            lambda: self.client.market_days("0000"),
            lambda: self.client.market_days("0066 "),
        )
        for query in invalid_queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                query()
        self.session.request.assert_not_called()

    def test_loc_payload_uses_single_general_reservation_at_market_open(self):
        self.prepare(reply(RECEIPT))
        self.assertEqual(self.submit(), RECEIPT)
        call = self.session.request.call_args
        self.assertEqual(call.args, ("POST", BASE_URL + "/trading/v1/overseas/reserved-orders/buy"))
        self.assertEqual(call.kwargs["json"], {
            "ovrs_stck_symb_iscd": "SOXL", "ovrs_exch_vndr_idno": "0066", "rsev_aspr_tpcd": "5",
            "odqt": "3", "ovrs_stck_oder_prc": "45.12", "oder_rsev_cls_code": "1",
            "oder_rsev_hour_clcd": "0", "warn_cnfr_yn": "N",
        })

    def test_expiry_sell_uses_moc_with_zero_price(self):
        self.prepare(reply(RECEIPT))
        self.submit(side="sell", order_type="MOC", price=0)
        call = self.session.request.call_args
        self.assertTrue(call.args[1].endswith("/sell"))
        self.assertEqual(call.kwargs["json"]["rsev_aspr_tpcd"], "7")
        self.assertEqual(call.kwargs["json"]["ovrs_stck_oder_prc"], "0")

    def test_specific_day_reservation_has_same_valid_start_end(self):
        self.prepare(reply(RECEIPT))
        self.submit(reservation_class="4", order_date="20261012")
        payload = self.session.request.call_args.kwargs["json"]
        self.assertEqual(payload["oder_sta_date"], "20261012")
        self.assertEqual(payload["oder_fin_date"], "20261012")

    def test_invalid_orders_never_reach_network(self):
        invalid_orders = (
            {"quantity": 0}, {"quantity": -1}, {"quantity": "NaN"}, {"price": "Infinity"},
            {"price": 0}, {"symbol": "soxl"}, {"side": "bad"},
            {"reservation_class": "3"}, {"reservation_class": "4"},
            {"reservation_class": "4", "order_date": "20260231"}, {"order_date": "20261012"},
            {"order_type": "MOC", "price": 0}, {"order_type": "MOC", "side": "sell", "price": 5},
        )
        for overrides in invalid_orders:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.submit(**overrides)
        self.session.request.assert_not_called()

    def test_broker_warning_is_never_acknowledged_or_retried(self):
        warning = {"rsp_cd": "5620", "data": {"warn_cls_code": "1", "warn_msg": "review price", "oder_rsev_mst_no": 0}}
        self.prepare(reply(warning))
        with self.assertRaises(MeritzWarning) as error:
            self.submit()
        self.assertEqual(error.exception.response, warning)
        self.assertEqual(self.session.request.call_count, 2)
        self.assertEqual(self.session.request.call_args.kwargs["json"]["warn_cnfr_yn"], "N")

    def test_submission_timeout_or_connection_failure_is_ambiguous_and_not_retried(self):
        for failure in (requests.Timeout("contains-secret"), requests.ConnectionError("contains-secret")):
            with self.subTest(failure=type(failure)):
                self.client._access_token = None
                self.session.reset_mock()
                self.prepare(failure)
                with self.assertRaises(AmbiguousSubmission) as error:
                    self.submit()
                self.assertNotIn("contains-secret", str(error.exception))
                self.assertEqual(self.session.request.call_count, 2)

    def test_unreadable_submission_response_is_ambiguous(self):
        invalid = reply(None)
        invalid.json.side_effect = ValueError("includes-secret")
        self.prepare(invalid)
        with self.assertRaises(AmbiguousSubmission):
            self.submit()
        self.assertEqual(self.session.request.call_count, 2)

    def test_incomplete_or_inconsistent_receipt_is_ambiguous(self):
        for body, status in (
            ({"rsp_cd": "5620", "data": {"oder_rsev_mst_no": 23}}, 200),
            ({"rsp_cd": "5620", "data": {"warn_cls_code": "0"}}, 200),
            (RECEIPT, 500),
            ({"data": RECEIPT["data"]}, 200),
        ):
            with self.subTest(body=body, status=status):
                self.client._access_token = None
                self.prepare(reply(body, status))
                with self.assertRaises(AmbiguousSubmission):
                    self.submit()

    def test_explicit_order_rejection_is_not_success_and_is_not_retried(self):
        self.prepare(reply({"rsp_cd": "3174", "rsp_msg": "invalid input"}, status=500))
        with self.assertRaises(MeritzAPIError):
            self.submit()
        self.assertEqual(self.session.request.call_count, 2)

    def test_token_transport_errors_do_not_expose_request_or_secrets(self):
        self.session.request.side_effect = requests.ConnectionError("fake-app-secret test-access-token")
        with self.assertRaises(MeritzError) as error:
            self.client.holdings()
        self.assertNotIn("fake-app-secret", str(error.exception))
        self.assertNotIn("test-access-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
