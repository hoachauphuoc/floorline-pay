"""
Reading an authorization from PayPal (paypal_client.get_authorization), over a fake network.

The settler asks PayPal what it says about an authorization before it captures one. Pinned
here: it is a GET of exactly that authorization with the caller's request id, the reply is
returned as it came, and every failure is a named receipt rather than a guess.

get_authorization is looked up inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib

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
        "*": (200, {"id": "AUTH-1", "status": "CREATED",
                    "amount": {"currency_code": "USD", "value": "1300.00"}}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        reply = replies.get(request.url.path, replies["*"])
        status, body = reply
        return httpx.Response(status, json=body)

    def client(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setattr(pp.httpx, "AsyncClient", client)
    return pp, calls, replies


def test_it_reads_exactly_that_authorization_from_the_sandbox(paypal):
    pp, calls, _ = paypal
    receipt = run(pp.get_authorization("AUTH-1", "req-1"))
    assert receipt["status"] == pp.OK
    assert receipt["body"]["status"] == "CREATED"
    assert receipt["body"]["amount"] == {"currency_code": "USD", "value": "1300.00"}
    sent = calls[1]
    assert sent.method == "GET"
    assert sent.url.host == "api-m.sandbox.paypal.com"
    assert sent.url.path == "/v2/payments/authorizations/AUTH-1"
    assert sent.headers["PayPal-Request-Id"] == "req-1"
    assert sent.headers["Authorization"] == "Bearer tok-1"


def test_a_blank_authorization_id_is_refused_without_a_request(paypal):
    pp, calls, _ = paypal
    receipt = run(pp.get_authorization("", "req-1"))
    assert receipt["status"] == pp.BAD_INPUT
    assert "authorization_id" in receipt["error"]
    assert calls == []


def test_an_unknown_authorization_is_an_http_error_not_a_success(paypal):
    pp, _, replies = paypal
    replies["/v2/payments/authorizations/AUTH-9"] = (404, {"name": "RESOURCE_NOT_FOUND"})
    receipt = run(pp.get_authorization("AUTH-9", "req-1"))
    assert receipt["status"] == pp.HTTP_ERROR
    assert receipt["http_status"] == 404
    assert receipt["error"] == "RESOURCE_NOT_FOUND"
    assert receipt["status"] in pp.FAILED_STATUSES


def test_without_credentials_nothing_is_sent(paypal, monkeypatch):
    pp, calls, _ = paypal
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET")
    receipt = run(pp.get_authorization("AUTH-1", "req-1"))
    assert receipt["status"] == pp.NOT_CONFIGURED
    assert calls == []
