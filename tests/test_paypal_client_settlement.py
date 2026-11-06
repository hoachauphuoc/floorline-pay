"""
The PayPal client's calls for what happens after the money moves (paypal_client.py): read a
payout batch, renew an authorization after its honor period, and ask PayPal whether a webhook
delivery is genuine.

Pinned here: each call goes to the sandbox with its request id; the input refusals (no batch,
no authorization, a missing transmission header or webhook id) send nothing; and the webhook
call carries the event and the webhook id exactly as PayPal's verify endpoint wants them.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest

_RealAsyncClient = httpx.AsyncClient

HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "paypal-transmission-id": "tx-1",
    "paypal-transmission-sig": "sig-1",
    "paypal-transmission-time": "2026-10-23T01:00:00Z",
}
EVENT = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.REVERSED", "resource": {"id": "CAP-1"}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def paypal(monkeypatch):
    pp = importlib.import_module("vf_logistics.paypal_client")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok-1"})
        return httpx.Response(200, json={"id": "X1", "verification_status": "SUCCESS"})

    def client(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setattr(pp.httpx, "AsyncClient", client)
    return pp, calls


def test_a_payout_batch_is_read_by_id(paypal):
    pp, calls = paypal
    receipt = run(pp.get_payout_batch("BATCH-1", "req-1"))
    assert receipt["status"] == pp.OK
    sent = calls[1]
    assert sent.method == "GET"
    assert sent.url.path == "/v1/payments/payouts/BATCH-1"
    assert sent.headers["PayPal-Request-Id"] == "req-1"


def test_reading_a_payout_batch_without_an_id_sends_nothing(paypal):
    pp, calls = paypal
    assert run(pp.get_payout_batch("", "req-1"))["status"] == pp.BAD_INPUT
    assert calls == []


def test_an_authorization_is_renewed_by_id(paypal):
    pp, calls = paypal
    receipt = run(pp.reauthorize_authorization("AUTH-1", "req-2"))
    assert receipt["status"] == pp.OK
    sent = calls[1]
    assert sent.method == "POST"
    assert sent.url.path == "/v2/payments/authorizations/AUTH-1/reauthorize"
    assert sent.headers["PayPal-Request-Id"] == "req-2"


def test_renewing_without_an_authorization_sends_nothing(paypal):
    pp, calls = paypal
    assert run(pp.reauthorize_authorization("", "req-2"))["status"] == pp.BAD_INPUT
    assert calls == []


def test_a_webhook_delivery_is_verified_with_its_headers_event_and_webhook_id(paypal):
    pp, calls = paypal
    receipt = run(pp.verify_webhook_signature(HEADERS, EVENT, "WEBHOOK-9", "req-3"))
    assert receipt["status"] == pp.OK
    sent = calls[1]
    assert sent.url.path == "/v1/notifications/verify-webhook-signature"
    assert json.loads(sent.content) == {
        "auth_algo": "SHA256withRSA",
        "cert_url": HEADERS["paypal-cert-url"],
        "transmission_id": "tx-1",
        "transmission_sig": "sig-1",
        "transmission_time": "2026-10-23T01:00:00Z",
        "webhook_id": "WEBHOOK-9",
        "webhook_event": EVENT,
    }


@pytest.mark.parametrize("missing", sorted(HEADERS))
def test_a_delivery_missing_a_transmission_header_is_not_sent(paypal, missing):
    pp, calls = paypal
    partial = {k: v for k, v in HEADERS.items() if k != missing}
    assert run(pp.verify_webhook_signature(partial, EVENT, "WEBHOOK-9", "req-3"))["status"] == pp.BAD_INPUT
    assert calls == []


def test_a_delivery_without_a_webhook_id_is_not_sent(paypal):
    pp, calls = paypal
    assert run(pp.verify_webhook_signature(HEADERS, EVENT, "", "req-3"))["status"] == pp.BAD_INPUT
    assert calls == []
