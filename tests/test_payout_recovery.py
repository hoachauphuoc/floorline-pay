"""
A payout PayPal reports as a duplicate batch is checked at PayPal, not failed (review F08, 2026-11-05).

A repeated delivery after a payout whose result was lost got PayPal's duplicate-batch refusal, and the settler recorded failed
although the carrier had been paid. Pinned here: paypal_client.duplicate_batch_id finds the original batch in such a refusal and
nothing else; the settler then reads that batch and settles only when it carries the payout's own sender_batch_id, amount and
currency; a batch that differs, one that cannot be read, and a refusal that is not a duplicate stay failed; and a second payout
is never created.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import DOWN, REFUSED, decision, new_case, pp, run  # noqa: E402, F401

DUPLICATE = {
    "status": "http_error", "http_status": 400, "error": "USER_BUSINESS_ERROR",
    "body": {
        "name": "USER_BUSINESS_ERROR",
        "message": "Batch with given sender_batch_id already exists",
        "links": [{"rel": "self", "href": "https://api-m.sandbox.paypal.com/v1/payments/payouts/BATCH-DUP", "method": "GET"}],
    },
}


def batch(key, sender=None, value="1250.00", currency="USD"):
    return {"status": "ok", "http_status": 200, "error": None, "body": {"batch_header": {
        "payout_batch_id": "BATCH-DUP",
        "sender_batch_header": {"sender_batch_id": sender or f"{key}-payout"},
        "amount": {"currency": currency, "value": value},
    }}}


def client():
    return importlib.import_module("vf_logistics.paypal_client")


def test_the_original_batch_is_found_in_a_duplicate_refusal():
    assert client().duplicate_batch_id(DUPLICATE) == "BATCH-DUP"


def test_a_refusal_that_is_not_a_duplicate_names_no_batch():
    assert client().duplicate_batch_id(REFUSED) is None
    assert client().duplicate_batch_id(DOWN) is None
    assert client().duplicate_batch_id({"status": "ok", "http_status": 201, "body": DUPLICATE["body"], "error": None}) is None
    no_link = {**DUPLICATE, "body": {"name": "USER_BUSINESS_ERROR", "message": "already exists"}}
    assert client().duplicate_batch_id(no_link) is None
    server_error = {**DUPLICATE, "http_status": 503}
    assert client().duplicate_batch_id(server_error) is None


def test_a_duplicate_whose_original_batch_matches_is_settled_without_a_second_payout(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    read = AsyncMock(return_value=batch(d["idempotency_key"]))
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", read)
    result = run(settler.settle(d))
    assert result["status"] == "settled"
    assert result["payout"]["paypal_id"] == "BATCH-DUP"
    assert read.await_args.args[0] == "BATCH-DUP"
    payout.assert_awaited_once()


def test_a_duplicate_whose_original_batch_is_another_payout_stays_failed(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], sender="exec-other-payout")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "did not pay the carrier" in result["reason"]
    payout.assert_awaited_once()


def test_a_duplicate_whose_original_batch_has_another_amount_or_currency_stays_failed(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    for different in (batch("k", value="12.00"), batch("k", currency="EUR")):
        d = decision(new_case())
        different["body"]["batch_header"]["sender_batch_header"]["sender_batch_id"] = f"{d['idempotency_key']}-payout"
        monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=different))
        assert run(settler.settle(d))["status"] == "failed"


def test_a_duplicate_whose_original_batch_cannot_be_read_stays_failed(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=DOWN))
    assert run(settler.settle(decision(new_case())))["status"] == "failed"


def test_a_plain_refusal_never_asks_for_a_batch(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = REFUSED
    read = AsyncMock()
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", read)
    assert run(settler.settle(decision(new_case())))["status"] == "failed"
    read.assert_not_awaited()
