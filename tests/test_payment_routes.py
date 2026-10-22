"""
Floorline Pay's booking routes (payment_routes.py): create the PayPal order a shipper
pays, then authorize it once the shipper has approved it.

Pinned here: the routes are operator-only; the caller's amount and ids reach PayPal only
in the right shape (an order id with unexpected characters never reaches a PayPal URL);
every PayPal refusal and every reply that lacks the id the caller needs is an error
status (400, 502 or 503), never an empty success; and nothing here pays anyone.

payment_routes is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
ORDER = {"status": "ok", "http_status": 201, "body": {"id": "5O190127TN364715T", "status": "CREATED"}, "error": None}
AUTHORIZED = {
    "status": "ok", "http_status": 201, "error": None,
    "body": {"id": "5O190127TN364715T", "purchase_units": [
        {"reference_id": "SHP-PAY-1", "payments": {"authorizations": [{
            "id": "0VF52814937998046", "status": "CREATED",
            "amount": {"currency_code": "USD", "value": "1300.00"}}]}},
    ]},
}


def refusal(status, http_status=None, error=None):
    return {"status": status, "http_status": http_status, "body": None, "error": error}


@pytest.fixture
def routes():
    return importlib.import_module("vf_logistics.payment_routes")


class TestTheBookingRoutes(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def _create(self, body=None, headers=KEYED):
        payload = {"amount": 1250.5, "currency": "USD", "reference_id": "SHP-PAY-1"}
        return self.client.post("/api/v1/payments/orders", json=body or payload, headers=headers)

    def _authorize(self, order_id="5O190127TN364715T", headers=KEYED):
        return self.client.post(f"/api/v1/payments/orders/{order_id}/authorize", headers=headers)

    def test_an_order_is_created_with_the_callers_amount_and_reference(self, routes):
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)) as create:
            r = self._create({"amount": 1250.5, "currency": "USD", "reference_id": "SHP-PAY-1", "request_id": "req-1"})
        assert r.status_code == 201
        assert r.get_json() == {"order_id": "5O190127TN364715T", "paypal_status": "CREATED"}
        create.assert_awaited_once_with(1250.5, "USD", "req-1", "SHP-PAY-1")

    def test_an_order_without_a_request_id_gets_a_generated_one(self, routes):
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)) as create:
            self._create()
        assert create.await_args.args[2].startswith("order-")

    def test_an_anonymous_caller_cannot_create_an_order(self, routes):
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)) as create:
            r = self._create(headers={})
        assert r.status_code in (401, 403)
        create.assert_not_called()

    def test_an_anonymous_caller_cannot_authorize_an_order(self, routes):
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=AUTHORIZED)) as authorize:
            r = self._authorize(headers={})
        assert r.status_code in (401, 403)
        authorize.assert_not_called()

    @pytest.mark.parametrize("receipt,code", [
        (refusal("bad_input", error="amount, currency and reference_id are required"), 400),
        (refusal("not_configured", error="PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are not set"), 503),
        (refusal("http_error", 422, "UNPROCESSABLE_ENTITY"), 502),
        (refusal("transport_error", error="ConnectError"), 502),
        (refusal("auth_failed", 401, "token request refused"), 502),
    ])
    def test_a_paypal_refusal_is_an_error_status_with_its_reason(self, routes, receipt, code):
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=receipt)):
            r = self._create()
        assert r.status_code == code
        assert r.get_json()["paypal_status"] == receipt["status"]
        assert r.get_json()["error"] == receipt["error"]

    def test_an_order_reply_without_an_id_is_not_a_success(self, routes):
        empty = {"status": "ok", "http_status": 201, "body": {"status": "CREATED"}, "error": None}
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=empty)):
            r = self._create()
        assert r.status_code == 502
        assert "no order id" in r.get_json()["error"]

    def test_an_approved_order_returns_its_authorization_id(self, routes):
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=AUTHORIZED)) as authorize:
            r = self._authorize()
        assert r.status_code == 200
        assert r.get_json() == {"order_id": "5O190127TN364715T", "authorization_id": "0VF52814937998046"}
        authorize.assert_awaited_once_with("5O190127TN364715T", "authorize-5O190127TN364715T")

    @pytest.mark.parametrize("order_id", ["lower-case", "BAD$ID", "A" * 65])
    def test_an_order_id_with_unexpected_characters_never_reaches_paypal(self, routes, order_id):
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=AUTHORIZED)) as authorize:
            r = self._authorize(order_id)
        assert r.status_code == 400
        assert "not a PayPal order id" in r.get_json()["error"]
        authorize.assert_not_called()

    def test_an_order_the_shipper_has_not_approved_is_refused(self, routes):
        not_approved = refusal("http_error", 422, "ORDER_NOT_APPROVED")
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=not_approved)):
            r = self._authorize()
        assert r.status_code == 502
        assert r.get_json()["error"] == "ORDER_NOT_APPROVED"

    @pytest.mark.parametrize("body", [
        {}, {"purchase_units": []}, {"purchase_units": [{"payments": {"authorizations": []}}]},
        {"purchase_units": [{"payments": {"authorizations": [{"id": "bad id"}]}}]},
    ])
    def test_an_authorize_reply_without_an_authorization_id_is_not_a_success(self, routes, body):
        reply = {"status": "ok", "http_status": 201, "body": body, "error": None}
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=reply)):
            r = self._authorize()
        assert r.status_code == 502
        assert "no authorization id" in r.get_json()["error"]
        assert "nothing is booked" in r.get_json()["error"]

    def test_the_booking_routes_never_pay_anyone(self, routes):
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)), \
                patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=AUTHORIZED)), \
                patch.object(routes.paypal_client, "capture_authorization", AsyncMock()) as capture, \
                patch.object(routes.paypal_client, "create_payout", AsyncMock()) as payout:
            self._create()
            self._authorize()
        capture.assert_not_called()
        payout.assert_not_called()
