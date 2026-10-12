"""Small, fail-closed Meritz overseas trading API client.

Credentials belong to one account and are only loaded by ``from_env``. Read
methods return the documented ``data`` object/list, preserving numeric strings.
Order submission returns the complete accepted response. No request is
retried automatically, including token issuance and ambiguous order submissions.
"""

from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

import requests


BASE_URL = "https://openapi.imeritz.com:9443"
QUERY_CODES = frozenset({"5762", "5766", "5820"})


class MeritzError(RuntimeError):
    """An API/transport error. Messages deliberately omit credentials and bodies."""


class MeritzAPIError(MeritzError):
    def __init__(self, message: str, *, response: Optional[dict] = None):
        super().__init__(message)
        self.response = response


class MeritzWarning(MeritzAPIError):
    """Broker warning: stop and review; never automatically acknowledge/re-send."""


class AmbiguousSubmission(MeritzError):
    """An order may have been received; reconcile before submitting anything else."""

    def __init__(self, message: str, *, response: Optional[dict] = None):
        super().__init__(message)
        self.response = response


def _date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("Date must be YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("Date must be a real calendar date") from None
    return value


def _date_range(start_date: str, end_date: str) -> tuple:
    start, end = _date(start_date), _date(end_date)
    if start > end:
        raise ValueError("Start date must not follow end date")
    return start, end


def _choice(value: str, choices: set, name: str) -> str:
    if value not in choices:
        raise ValueError("Invalid " + name)
    return value


def _decimal(value: Any, *, positive: bool, max_length: int, name: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid " + name) from None
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise ValueError("Invalid " + name)
    result = format(number, "f")
    if len(result) > max_length:
        raise ValueError(name + " exceeds API field length")
    return result


def _positive_integer(value: Any, *, max_length: int, name: str) -> int:
    number = _decimal(value, positive=True, max_length=max_length, name=name)
    if Decimal(number) != Decimal(number).to_integral_value():
        raise ValueError(name + " must be a positive integer")
    return int(Decimal(number))


class MeritzClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        mac_address: str,
        *,
        session: Optional[requests.Session] = None,
        timeout: float = 15.0,
    ):
        if not app_key or not app_secret:
            raise ValueError("MERITZ_APP_KEY and MERITZ_APP_SECRET are required")
        if not isinstance(mac_address, str) or not re.fullmatch(r"[0-9a-fA-F]{12}", mac_address):
            raise ValueError("MERITZ_MAC_ADDRESS must be 12 hexadecimal characters without separators")
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self._app_key = app_key
        self._app_secret = app_secret
        self._mac_address = mac_address.upper()
        self._session = session or requests.Session()
        self._timeout = timeout
        self._access_token = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls, *, env: Optional[Mapping[str, str]] = None, **kwargs) -> MeritzClient:
        source = os.environ if env is None else env
        return cls(
            source.get("MERITZ_APP_KEY", ""),
            source.get("MERITZ_APP_SECRET", ""),
            source.get("MERITZ_MAC_ADDRESS", ""),
            **kwargs,
        )

    @staticmethod
    def _json(response, *, submission: bool = False) -> dict:
        try:
            body = response.json()
        except (ValueError, requests.RequestException):
            if submission:
                raise AmbiguousSubmission("Unreadable order response; reconcile orders before retrying") from None
            raise MeritzAPIError("Meritz returned a non-JSON response") from None
        if not isinstance(body, dict):
            if submission:
                raise AmbiguousSubmission("Invalid order response; reconcile orders before retrying")
            raise MeritzAPIError("Meritz returned an invalid response body")
        return body

    def authenticate(self) -> None:
        """Obtain and cache an OAuth token in memory; never write or print it."""
        try:
            response = self._session.request(
                "POST",
                BASE_URL + "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._app_key,
                    "client_secret": self._app_secret,
                    "scope": "login",
                },
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise MeritzError("Meritz token request failed") from None
        body = self._json(response)
        if response.status_code != 200:
            raise MeritzAPIError("Meritz token request was rejected")
        token = body.get("access_token")
        try:
            expires_in = float(body.get("expires_in", 0))
        except (TypeError, ValueError):
            expires_in = 0
        token_type = body.get("token_type")
        if (
            not isinstance(token, str) or not token or not math.isfinite(expires_in) or expires_in <= 0
            or not isinstance(token_type, str) or token_type.lower() != "bearer"
        ):
            raise MeritzAPIError("Meritz returned an invalid token response")
        self._access_token = token
        self._token_expires_at = time.monotonic() + max(0, expires_in - 60)

    def _request(
        self, method: str, path: str, *, params: Optional[dict] = None,
        payload: Optional[dict] = None, query_codes=QUERY_CODES,
        success_codes=frozenset({"5620"}),
    ) -> dict:
        if not self._access_token or time.monotonic() >= self._token_expires_at:
            self.authenticate()
        submission = method == "POST"
        try:
            response = self._session.request(
                method,
                BASE_URL + path,
                params=params,
                json=payload,
                headers={
                    "Authorization": "Bearer " + self._access_token,
                    "mac_address": self._mac_address,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            if submission:
                raise AmbiguousSubmission("Order request interrupted; reconcile orders before retrying") from None
            raise MeritzError("Meritz query request failed") from None
        body = self._json(response, submission=submission)
        code = body.get("rsp_cd")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", code):
            if submission:
                raise AmbiguousSubmission("Order response has no result code; reconcile before retrying", response=body)
            raise MeritzAPIError("Meritz query response has no result code", response=body)
        accepted_codes = success_codes if submission else query_codes
        if code not in accepted_codes:
            if submission and code == "0000":
                raise AmbiguousSubmission("Order response has an unexpected success code; reconcile before retrying", response=body)
            raise MeritzAPIError("Meritz rejected request (rsp_cd=" + code + ")", response=body)
        if response.status_code != 200:
            if submission:
                raise AmbiguousSubmission("Order response HTTP status is inconsistent; reconcile before retrying", response=body)
            raise MeritzAPIError("Meritz query returned an inconsistent HTTP status", response=body)
        return body

    def _rows(self, path: str, params: Optional[dict] = None, *, paginated: bool = False) -> List[dict]:
        query = dict(params or {})
        rows = []
        seen_keys = set()
        for _ in range(100):
            body = self._request("GET", path, params=query)
            data = body.get("data", [] if body["rsp_cd"] == "5820" else None)
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise MeritzAPIError("Meritz query returned an invalid data list", response=body)
            rows.extend(data)
            if str(body.get("tr_cont", "0")) != "1":
                if body["rsp_cd"] == "5762":
                    raise MeritzAPIError("Meritz query continuation is incomplete", response=body)
                return rows
            if not paginated:
                raise MeritzAPIError("Unexpected paginated response; query is incomplete", response=body)
            key = body.get("tr_cont_key")
            if not isinstance(key, str) or not key or key in seen_keys:
                raise MeritzAPIError("Meritz query has an invalid/repeated continuation key", response=body)
            seen_keys.add(key)
            query.update(tr_cont="1", tr_cont_key=key)
        raise MeritzAPIError("Meritz query exceeded the continuation page limit")

    def _object(self, path: str, params: dict, *, query_codes=QUERY_CODES) -> dict:
        body = self._request("GET", path, params=params, query_codes=query_codes)
        if not isinstance(body.get("data"), dict):
            raise MeritzAPIError("Meritz reference query has no data object", response=body)
        return body["data"]

    def exchanges(self, market_code: Optional[str] = "01") -> List[dict]:
        query = {} if market_code is None else {"ovrs_stck_mrkt_tr_cls_code": self._market(market_code)}
        return self._rows("/reference/v1/overseas/exchanges", query, paginated=True)

    @staticmethod
    def _market(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}", value):
            raise ValueError("Market code must be exactly two ASCII digits")
        return value

    @staticmethod
    def _vendor(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,6}", value) or value in {"0000", "0787", "XLON"}:
            raise ValueError("Use an unambiguous exchange vendor code from exchanges()")
        return value

    def market_hours(self, market_code: str = "01") -> dict:
        return self._object("/reference/v1/overseas/market-hours", {"ovrs_stck_mrkt_tr_cls_code": self._market(market_code)})

    def market_days(self, exchange_vendor_code: str) -> dict:
        return self._object("/reference/v1/overseas/market-days", {"ovrs_exch_vndr_idno": self._vendor(exchange_vendor_code)})

    def quote(self, symbol: str, exchange_code: str, delayed: bool = False) -> dict:
        """Read one symbol. Zero price-unit fields do not establish a valid tick size."""
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", symbol):
            raise ValueError("Symbol must be an uppercase exchange symbol")
        _choice(exchange_code, {"OQ", "NY", "AX", "HK", "SH", "SZ", "JP"}, "quote exchange code")
        data = self._object("/market/v1/overseas/prices", {
            "mrkt_div_code": "OV", "mrkt_cls_code": exchange_code,
            "iscd": symbol, "dely_rltm_cls_code": "1" if delayed else "0",
        }, query_codes={"0000"})
        if data.get("iscd") != symbol:
            raise MeritzAPIError("Meritz quote did not return the requested symbol")
        return data

    def holdings(self) -> List[dict]:
        return self._rows("/accounts/v1/me/overseas/holdings")

    def order_estimate(self, integrated_margin: bool = False) -> List[dict]:
        return self._rows("/trading/v1/overseas/orders/estimate", {"intg_marg_iqry_yn": "Y" if integrated_margin else "N"})

    def orders_today(self, side: str = "0", execution: str = "0") -> List[dict]:
        return self._rows("/trading/v1/overseas/orders/today", {
            "seln_byov_clcd": _choice(side, {"0", "1", "2"}, "side filter"),
            "cntg_cls_code": _choice(execution, {"0", "1", "2"}, "execution filter"),
        })

    def orders_history(self, start_date: str, end_date: str, side: str = "0", execution: str = "0") -> List[dict]:
        start, end = _date_range(start_date, end_date)
        return self._rows("/trading/v1/overseas/orders/history", {
            "oder_sta_date": start, "oder_fin_date": end,
            "seln_byov_clcd": _choice(side, {"0", "1", "2"}, "side filter"),
            "cntg_cls_code": _choice(execution, {"0", "1", "2"}, "execution filter"),
        }, paginated=True)

    def order_detail(self, order_date: str, order_no: Any) -> List[dict]:
        number = _decimal(order_no, positive=True, max_length=20, name="order number")
        if Decimal(number) != Decimal(number).to_integral_value():
            raise ValueError("Order number must be a positive integer")
        return self._rows("/trading/v1/overseas/orders/detail", {"oder_sta_date": _date(order_date), "oder_no": number})

    def reserved_orders(self, start_date: str, end_date: str, side: str = "0", active_only: bool = False) -> List[dict]:
        start, end = _date_range(start_date, end_date)
        return self._rows("/trading/v1/overseas/reserved-orders", {
            "iqry_sta_date": start, "iqry_fin_date": end,
            "seln_byov_clcd": _choice(side, {"0", "1", "2"}, "side filter"),
            "iqry_cls_code": "2", "iqry_idnt_clcd": "2" if active_only else "0",
        }, paginated=True)

    @staticmethod
    def _submission_receipt(
        body: dict, number_field: str, *, warning_status: bool = True,
        warning_codes: Optional[set] = None,
    ) -> dict:
        data = body.get("data")
        if not isinstance(data, dict):
            raise AmbiguousSubmission("Order response has no receipt; reconcile before retrying", response=body)
        if warning_status:
            if "warn_cls_code" not in data:
                raise AmbiguousSubmission("Order receipt has no warning status; reconcile before retrying", response=body)
            warning_code = str(data["warn_cls_code"])
            if warning_codes is not None and warning_code not in warning_codes:
                raise AmbiguousSubmission("Order receipt has an unknown warning status; reconcile before retrying", response=body)
            if warning_code != "0":
                raise MeritzWarning("Meritz order warning requires manual review; order was not acknowledged", response=body)
        try:
            number = Decimal(str(data.get(number_field, "")))
            valid_receipt = number.is_finite() and number > 0 and number == number.to_integral_value()
        except InvalidOperation:
            valid_receipt = False
        if not valid_receipt:
            raise AmbiguousSubmission("Order receipt has no valid " + number_field + "; reconcile before retrying", response=body)
        return body

    def submit_order(
        self, *, side: str, symbol: str, exchange_vendor_code: str,
        quantity: Any, price: Any, order_type: str = "LOC",
    ) -> Dict[str, Any]:
        """Submit one direct LOC/MOC order; receipt is acceptance, not execution.

        The broker determines when direct close orders can be received. This
        method never falls back to a reservation or acknowledges a warning.
        """
        _choice(side, {"buy", "sell"}, "order side")
        _choice(order_type, {"LOC", "MOC"}, "order type")
        if order_type == "MOC" and side != "sell":
            raise ValueError("This client supports MOC for expiry sells only")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", symbol):
            raise ValueError("Symbol must be an uppercase exchange symbol")
        amount = _positive_integer(quantity, max_length=19, name="quantity")
        limit = _decimal(price, positive=order_type == "LOC", max_length=20, name="price")
        if order_type == "MOC" and Decimal(limit) != 0:
            raise ValueError("MOC price must be zero")
        body = self._request("POST", "/trading/v1/overseas/orders/" + side, payload={
            "ovrs_stck_symb_iscd": symbol,
            "ovrs_exch_vndr_idno": self._vendor(exchange_vendor_code),
            "oder_cls_code": "32" if order_type == "LOC" else "34",
            "oder_cond_cls_code": "7", "odqt": amount,
            "ovrs_stck_oder_prc": limit, "warn_cnfr_yn": "N",
        }, success_codes={"0001"})
        return self._submission_receipt(body, "oder_no", warning_codes={"0", "1", "2", "3"})

    def cancel_reserved_order(self, *, reservation_date: str, reservation_no: Any) -> Dict[str, Any]:
        """Cancel once; callers must reconcile reservation status afterward."""
        payload = {
            "oder_rsev_mst_date": _date(reservation_date),
            "oder_rsev_mst_no": str(_positive_integer(reservation_no, max_length=11, name="reservation number")),
        }
        body = self._request(
            "POST", "/trading/v1/overseas/reserved-orders/cancel", payload=payload,
            success_codes={"5620", "0000"},
        )
        return self._submission_receipt(body, "pros_srno", warning_status=False)

    def cancel_order(
        self, *, order_date: str, order_no: Any, quantity: Any, order_type: str = "LOC",
    ) -> Dict[str, Any]:
        """Request a direct LOC/MOC cancellation once, then reconcile status."""
        _choice(order_type, {"LOC", "MOC"}, "order type")
        payload = {
            "oder_date": _date(order_date),
            "orgl_oder_no": _positive_integer(order_no, max_length=11, name="order number"),
            "oder_cls_code": "32" if order_type == "LOC" else "34",
            "odqt": _positive_integer(quantity, max_length=19, name="quantity"),
            "warn_cnfr_yn": "N",
        }
        body = self._request("POST", "/trading/v1/overseas/orders/cancel", payload=payload, success_codes={"0001"})
        return self._submission_receipt(body, "oder_no", warning_codes={"0", "1", "2", "3"})

    def submit_reserved_order(
        self, *, side: str, symbol: str, exchange_vendor_code: str,
        quantity: Any, price: Any, order_type: str = "LOC",
        reservation_class: str = "1", order_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit once. Normal reservations are issued at market open, not at LOC cutoff.

        Class 4 requires an explicit same start/end date. Its broker-side date
        interpretation must be verified in a live test before scheduled use.
        This method never acknowledges a broker warning.
        """
        _choice(side, {"buy", "sell"}, "order side")
        _choice(order_type, {"LOC", "MOC"}, "order type")
        _choice(reservation_class, {"1", "4"}, "reservation class")
        if order_type == "MOC" and side != "sell":
            raise ValueError("This client supports MOC for expiry sells only")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", symbol):
            raise ValueError("Symbol must be an uppercase exchange symbol")
        amount = _decimal(quantity, positive=True, max_length=19, name="quantity")
        limit = _decimal(price, positive=order_type == "LOC", max_length=20, name="price")
        if order_type == "MOC" and Decimal(limit) != 0:
            raise ValueError("MOC price must be zero")
        payload = {
            "ovrs_stck_symb_iscd": symbol,
            "ovrs_exch_vndr_idno": self._vendor(exchange_vendor_code),
            "rsev_aspr_tpcd": "5" if order_type == "LOC" else "7",
            "odqt": amount, "ovrs_stck_oder_prc": limit,
            "oder_rsev_cls_code": reservation_class,
            "oder_rsev_hour_clcd": "0", "warn_cnfr_yn": "N",
        }
        if reservation_class == "4":
            if order_date is None:
                raise ValueError("Specific-day reservation requires order_date")
            payload.update(oder_sta_date=_date(order_date), oder_fin_date=_date(order_date))
        elif order_date is not None:
            raise ValueError("order_date only applies to specific-day reservations")
        body = self._request("POST", "/trading/v1/overseas/reserved-orders/" + side, payload=payload)
        return self._submission_receipt(body, "oder_rsev_mst_no")
