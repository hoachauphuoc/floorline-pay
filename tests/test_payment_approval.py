"""
The approval flow of the booking routes (payment_routes.py): the shipper is sent to PayPal
to approve the payment and comes back to the console.

Pinned here: no return address is accepted unless its origin is listed in
PAYMENT_RETURN_ORIGINS (unset means none); the approval address handed back must be a
PayPal address; an order made for approval carries PayPal's return and cancel addresses and
the AUTHORIZE intent; and the order without return addresses behaves as before.

create_order_for_approval is read through paypal_client inside each test so that, before it
exists, every test fails instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
SITE = "https://floorline-pay.example"
RETURN = f"{SITE}/book/return"
CANCEL = f"{SITE}/book"
APPROVAL = "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T"


def created(links):
    return {"status": "ok", "http_status": 201, "error": None,
            "body": {"id": "5O190127TN364715T", "status": "PAYER_ACTION_REQUIRED", "links": links}}


GOOD = created([
    {"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T"},
    {"rel": "payer-action", "href": APPROVAL},
])


@pytest.fixture
def pp():
    return importlib.import_module("vf_logistics.paypal_client")


@pytest.fixture
def routes():
    return importlib.import_module("vf_logistics.payment_routes")


class TestTheApprovalFlow(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()
        self.origins = patch.dict(os.environ, {"PAYMENT_RETURN_ORIGINS": SITE})
        self.origins.start()

    def teardown_method(self):
        self.origins.stop()
        super().teardown_method()

    def _create(self, **extra):
        body = {"amount": 1250.5, "currency": "USD", "reference_id": "SHP-PAY-1",
                "return_url": RETURN, "cancel_url": CANCEL, **extra}
        return self.client.post("/api/v1/payments/orders", json=body, headers=KEYED)

    def test_an_order_for_approval_returns_the_paypal_approval_address(self, pp, routes):
        with patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=GOOD)) as make:
            r = self._create(request_id="req-1")
        assert r.status_code == 201
        assert r.get_json() == {
            "order_id": "5O190127TN364715T", "paypal_status": "PAYER_ACTION_REQUIRED",
            "approval_url": APPROVAL,
        }
        make.assert_awaited_once_with(1250.5, "USD", "req-1", "SHP-PAY-1", RETURN, CANCEL)

    def test_an_order_without_return_addresses_is_made_as_before(self, pp, routes):
        plain = {"status": "ok", "http_status": 201, "body": {"id": "5O190127TN364715T", "status": "CREATED"}, "error": None}
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=plain)) as make, \
                patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock()) as approval:
            r = self.client.post("/api/v1/payments/orders", headers=KEYED,
                                 json={"amount": 10, "currency": "USD", "reference_id": "SHP-1"})
        assert r.status_code == 201
        assert "approval_url" not in r.get_json()
        make.assert_awaited_once()
        approval.assert_not_called()

    @pytest.mark.parametrize("return_url", [
        "https://evil.example/book/return",
        "http://floorline-pay.example/book/return",
        "https://floorline-pay.example.evil.example/x",
        "https://user@floorline-pay.example/book/return",
        "javascript:alert(1)",
        "/book/return",
    ])
    def test_a_return_address_off_the_listed_origins_is_refused(self, pp, routes, return_url):
        with patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=GOOD)) as make:
            r = self._create(return_url=return_url)
        assert r.status_code == 400
        assert "PAYMENT_RETURN_ORIGINS" in r.get_json()["error"]
        make.assert_not_called()

    def test_a_cancel_address_off_the_listed_origins_is_refused(self, pp, routes):
        with patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=GOOD)) as make:
            r = self._create(cancel_url="https://evil.example/book")
        assert r.status_code == 400
        make.assert_not_called()

    def test_with_no_origins_configured_no_return_address_is_accepted(self, pp, routes):
        with patch.dict(os.environ, {"PAYMENT_RETURN_ORIGINS": ""}), \
                patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=GOOD)) as make:
            r = self._create()
        assert r.status_code == 400
        make.assert_not_called()

    @pytest.mark.parametrize("links", [
        [],
        [{"rel": "self", "href": APPROVAL}],
        [{"rel": "payer-action", "href": "https://evil.example/checkoutnow?token=X"}],
        [{"rel": "payer-action", "href": "http://www.sandbox.paypal.com/checkoutnow"}],
        [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com.evil.example/x"}],
    ])
    def test_a_reply_without_a_paypal_approval_address_is_not_a_success(self, pp, routes, links):
        with patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=created(links))):
            r = self._create()
        assert r.status_code == 502
        assert "no approval link" in r.get_json()["error"]
        assert "nothing is booked" in r.get_json()["error"]

    def test_a_paypal_refusal_is_an_error_status(self, pp, routes):
        refused = {"status": "http_error", "http_status": 422, "body": None, "error": "UNPROCESSABLE_ENTITY"}
        with patch.object(routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=refused)):
            r = self._create()
        assert r.status_code == 502
        assert r.get_json()["error"] == "UNPROCESSABLE_ENTITY"


def test_the_order_for_approval_asks_paypal_to_authorize_and_to_send_the_shipper_back(pp):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok-1"})
        return httpx.Response(201, json={"id": "5O190127TN364715T"})

    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    with patch.dict(os.environ, {"PAYPAL_CLIENT_ID": "id", "PAYPAL_CLIENT_SECRET": "secret"}), \
            patch.object(pp.httpx, "AsyncClient", client):
        receipt = asyncio.run(pp.create_order_for_approval(1250.5, "usd", "req-1", "SHP-PAY-1", RETURN, CANCEL))
    assert receipt["status"] == pp.OK
    sent = seen[1]
    assert sent.url.path == "/v2/checkout/orders"
    assert sent.headers["PayPal-Request-Id"] == "req-1"
    body = json.loads(sent.content)
    assert body["intent"] == "AUTHORIZE"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "1250.50"}
    context = body["payment_source"]["paypal"]["experience_context"]
    assert context["return_url"] == RETURN
    assert context["cancel_url"] == CANCEL
    assert context["shipping_preference"] == "NO_SHIPPING"


@pytest.mark.parametrize("args", [
    (0, "USD", "req-1", "SHP-1", RETURN, CANCEL),
    (10, "USD", "req-1", "", RETURN, CANCEL),
    (10, "USD", "req-1", "SHP-1", "", CANCEL),
    (10, "USD", "req-1", "SHP-1", RETURN, ""),
])
def test_an_order_for_approval_needs_an_amount_a_reference_and_both_addresses(pp, args):
    with patch.dict(os.environ, {"PAYPAL_CLIENT_ID": "id", "PAYPAL_CLIENT_SECRET": "secret"}), \
            patch.object(pp.httpx, "AsyncClient", side_effect=AssertionError("no request may be sent")):
        receipt = asyncio.run(pp.create_order_for_approval(*args))
    assert receipt["status"] == pp.BAD_INPUT
    assert "return_url" in receipt["error"]
