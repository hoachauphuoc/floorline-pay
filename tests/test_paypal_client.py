"""
Floorline Pay's PayPal client (paypal_client.py) over a fake network.

The rule pinned here is the module's own: a payment that did not happen must not look
like one that did. Only status "ok" means PayPal accepted the request. A missing
credential, a refused token, an HTTP error, a transport error, a non-JSON reply and a
malformed amount each come back as a named failure, and the input refusals send nothing.

The module is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest

_RealAsyncClient = httpx.AsyncClient


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def paypal(monkeypatch):
    pp = importlib.import_module("vf_logistics.paypal_client")
    calls: list[httpx.Request] = []
    replies: dict[str, object] = {
        "/v1/oauth2/token": (200, {"access_token": "tok-1"}),
        "*": (201, {"id": "X1", "status": "CREATED"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        reply = replies.get(request.url.path, replies["*"])
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    def client(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setattr(pp.httpx, "AsyncClient", client)
    return pp, calls, replies


def test_the_base_url_is_the_sandbox(paypal):
    pp, _, _ = paypal
    assert pp.SANDBOX_BASE == "https://api-m.sandbox.paypal.com"


def test_create_order_holds_funds_with_intent_authorize_and_a_request_id(paypal):
    pp, calls, _ = paypal
    receipt = run(pp.create_order("1250.5", "usd", "req-1", "CASE-1"))
    assert receipt["status"] == pp.OK
    assert receipt["body"]["id"] == "X1"
    sent = calls[1]
    assert sent.url.host == "api-m.sandbox.paypal.com"
    assert sent.url.path == "/v2/checkout/orders"
    assert sent.headers["PayPal-Request-Id"] == "req-1"
    assert sent.headers["Authorization"] == "Bearer tok-1"
    body = json.loads(sent.content)
    assert body["intent"] == "AUTHORIZE"
    assert body["purchase_units"][0] == {
        "reference_id": "CASE-1", "amount": {"currency_code": "USD", "value": "1250.50"},
    }


def test_capture_and_void_hit_the_authorization_endpoints(paypal):
    pp, calls, replies = paypal
    captured = run(pp.capture_authorization("AUTH-1", "req-2"))
    assert captured["status"] == pp.OK
    assert calls[1].url.path == "/v2/payments/authorizations/AUTH-1/capture"
    assert json.loads(calls[1].content) == {"final_capture": True}
    replies["/v2/payments/authorizations/AUTH-1/void"] = (204, None)
    voided = run(pp.void_authorization("AUTH-1", "req-3"))
    assert voided["status"] == pp.OK
    assert voided["http_status"] == 204
    assert calls[3].url.path == "/v2/payments/authorizations/AUTH-1/void"


def test_a_payout_pays_the_carrier_email_under_the_request_id(paypal):
    pp, calls, _ = paypal
    receipt = run(pp.create_payout("carrier@example.com", 800, "USD", "req-9", "Load 42"))
    assert receipt["status"] == pp.OK
    assert calls[1].url.path == "/v1/payments/payouts"
    body = json.loads(calls[1].content)
    assert body["sender_batch_header"]["sender_batch_id"] == "req-9"
    item = body["items"][0]
    assert item["receiver"] == "carrier@example.com"
    assert item["amount"] == {"value": "800.00", "currency": "USD"}


def test_without_credentials_nothing_is_sent(paypal, monkeypatch):
    pp, calls, _ = paypal
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET")
    receipt = run(pp.create_order("10", "USD", "req-1", "CASE-1"))
    assert receipt["status"] == pp.NOT_CONFIGURED
    assert receipt["status"] in pp.FAILED_STATUSES
    assert "PAYPAL_CLIENT_SECRET" in receipt["error"]
    assert pp.configured() is False
    assert calls == []


@pytest.mark.parametrize("amount,currency", [
    ("0", "USD"), ("-5", "USD"), ("abc", "USD"), ("NaN", "USD"), ("10", "US"),
])
def test_a_malformed_amount_is_refused_before_any_request(paypal, amount, currency):
    pp, calls, _ = paypal
    receipt = run(pp.create_order(amount, currency, "req-1", "CASE-1"))
    assert receipt["status"] == pp.BAD_INPUT
    assert "amount" in receipt["error"]
    assert calls == []


def test_a_payout_without_a_receiver_email_is_refused(paypal):
    pp, calls, _ = paypal
    receipt = run(pp.create_payout("not-an-email", 800, "USD", "req-9", "Load 42"))
    assert receipt["status"] == pp.BAD_INPUT
    assert "receiver email" in receipt["error"]
    assert calls == []


def test_a_blank_id_or_request_id_is_refused(paypal):
    pp, calls, _ = paypal
    blank_id = run(pp.capture_authorization("", "req-2"))
    assert blank_id["status"] == pp.BAD_INPUT
    assert "authorization_id" in blank_id["error"]
    blank_key = run(pp.capture_authorization("AUTH-1", ""))
    assert blank_key["status"] == pp.BAD_INPUT
    assert "request_id" in blank_key["error"]
    assert calls == []


def test_a_refused_token_is_auth_failed_and_no_payment_is_sent(paypal):
    pp, calls, replies = paypal
    replies["/v1/oauth2/token"] = (401, {"error": "invalid_client"})
    receipt = run(pp.create_payout("carrier@example.com", 800, "USD", "req-9", "Load 42"))
    assert receipt["status"] == pp.AUTH_FAILED
    assert receipt["http_status"] == 401
    assert len(calls) == 1


def test_an_http_error_carries_paypal_error_name(paypal):
    pp, _, replies = paypal
    replies["/v2/checkout/orders"] = (422, {"name": "UNPROCESSABLE_ENTITY"})
    receipt = run(pp.create_order("10", "USD", "req-1", "CASE-1"))
    assert receipt["status"] == pp.HTTP_ERROR
    assert receipt["http_status"] == 422
    assert receipt["error"] == "UNPROCESSABLE_ENTITY"
    assert receipt["status"] in pp.FAILED_STATUSES


def test_a_transport_error_is_reported_not_raised(paypal):
    pp, _, replies = paypal
    replies["/v1/payments/payouts"] = httpx.ConnectError("down")
    receipt = run(pp.create_payout("carrier@example.com", 800, "USD", "req-9", "Load 42"))
    assert receipt["status"] == pp.TRANSPORT_ERROR
    assert receipt["error"] == "ConnectError"


def test_a_non_json_reply_is_bad_response(paypal):
    pp, _, replies = paypal
    replies["/v2/checkout/orders"] = (200, b"<html>maintenance</html>")
    receipt = run(pp.create_order("10", "USD", "req-1", "CASE-1"))
    assert receipt["status"] == pp.BAD_RESPONSE
    assert receipt["status"] in pp.FAILED_STATUSES
