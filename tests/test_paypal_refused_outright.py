"""
Only a plain refusal proves that PayPal took nothing (follow-up review F01, 2026-11-07).

The settler gives a payout's reservation up only on proof that nothing went out. Pinned here: a 4xx refusal is that proof, unless it
is a timeout, a conflict or a rate limit, or it speaks of a repeated key; anything else proves nothing.
"""

from __future__ import annotations

import pytest

from vf_logistics import paypal_client


def refusal(http, body):
    return {"status": "http_error", "http_status": http, "body": body, "error": "refused"}


@pytest.mark.parametrize("http", [400, 403, 404, 422])
def test_a_plain_4xx_refusal_took_nothing(http):
    assert paypal_client.refused_outright(refusal(http, {"name": "INSUFFICIENT_FUNDS"})) is True


def test_a_refusal_with_an_empty_body_took_nothing():
    assert paypal_client.refused_outright(refusal(400, {})) is True


@pytest.mark.parametrize("http", [408, 409, 429, 500, 503])
def test_a_status_that_may_mean_later_proves_nothing(http):
    assert paypal_client.refused_outright(refusal(http, {"name": "BUSY"})) is False


@pytest.mark.parametrize("body", [
    {"name": "USER_BUSINESS_ERROR", "message": "Batch with given sender_batch_id already exists"},
    {"name": "DUPLICATE_REQUEST"},
    {"name": "VALIDATION_ERROR", "details": [{"issue": "duplicate sender_batch_id"}]},
])
def test_a_refusal_about_a_repeated_key_proves_nothing(body):
    assert paypal_client.refused_outright(refusal(400, body)) is False


@pytest.mark.parametrize("receipt", [
    {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"},
    {"status": "bad_response", "http_status": 200, "body": None, "error": "not json"},
    {"status": "ok", "http_status": 201, "body": {}, "error": None},
    refusal(400, None),
])
def test_anything_else_proves_nothing(receipt):
    assert paypal_client.refused_outright(receipt) is False
