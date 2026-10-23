"""
PayPal's webhook receiver (paypal_webhook.py).

Pinned here, with PayPal's verification faked: the receiver is off until PAYPAL_WEBHOOK_ID is
set; nothing is applied unless PayPal says the delivery is its own, and a PayPal that cannot be
reached is a 503 so the event comes again; a payout status is recorded on its case and a
failed one freezes the carrier; a reversed capture and an opened dispute freeze the carrier and
the shipper of the case; a resolved dispute and an event of no interest are recorded and change
nothing; an event that matches no settlement is recorded as unmatched; and the same event
delivered twice is applied once.

paypal_webhook is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from unittest.mock import AsyncMock

import pytest

from vf_logistics.store import get_store, utcnow

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig-1",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-23T01:00:00Z",
}
GENUINE = {"status": "ok", "http_status": 200, "error": None, "body": {"verification_status": "SUCCESS"}}


def run(coro):
    return asyncio.run(coro)


def mod(name="paypal_webhook"):
    return importlib.import_module(f"vf_logistics.{name}")


@pytest.fixture
def hook(monkeypatch):
    webhook = mod()
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WEBHOOK-9")
    verify = AsyncMock(return_value=GENUINE)
    monkeypatch.setattr(webhook.paypal_client, "verify_webhook_signature", verify)
    return webhook, verify


def settled():
    tag = uuid.uuid4().hex[:8]
    case_id, carrier, shipper = f"CASE-{tag}", f"Mekong Haulage {tag} Co", f"Saigon Plywood {tag} Co"
    shipment = {"shipment_id": case_id, "payout_amount": 1250.0, "carrier_name": carrier, "shipper_name": shipper}
    run(get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": shipment}))
    run(get_store().add_audit({
        "audit_id": f"settle-{tag}", "case_id": case_id, "action": "settle_payment", "status": "settled",
        "detail": {
            "payout": {"paypal_id": f"BATCH-{tag}"}, "capture": {"paypal_id": f"CAP-{tag}"},
            "idempotency_key": tag,
        },
        "at": utcnow(),
    }))
    return {"tag": tag, "case_id": case_id, "carrier": carrier, "shipper": shipper}


def event(event_type, resource, event_id=None):
    return {"id": event_id or f"WH-{uuid.uuid4().hex[:8]}", "event_type": event_type, "resource": resource}


def frozen(kind, name):
    return run(mod("payment_freeze").frozen(kind, name))


def test_the_receiver_is_off_until_a_webhook_id_is_set(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    status, reply = run(mod().receive(HEADERS, event("PAYMENT.CAPTURE.REVERSED", {"id": "CAP-1"})))
    assert status == 503
    assert "PAYPAL_WEBHOOK_ID" in reply["reason"]


@pytest.mark.parametrize("body", [None, [], {}, {"id": "WH-1"}, {"event_type": "X"}, {"id": "WH-1", "event_type": 5}])
def test_a_body_that_is_not_an_event_is_refused(hook, body):
    webhook, verify = hook
    status, reply = run(webhook.receive(HEADERS, body))
    assert status == 400
    assert "event_type" in reply["reason"]
    verify.assert_not_called()


def test_missing_transmission_headers_are_unauthenticated_and_nothing_is_sent(hook):
    webhook, verify = hook
    status, reply = run(webhook.receive({}, event("X", {})))
    assert status == 401
    assert "signature headers are missing" in reply["reason"]
    verify.assert_not_called()


def test_a_post_with_no_signature_is_unauthenticated_even_when_the_webhook_is_off(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    status, reply = run(mod().receive({}, {}))
    assert status == 401
    assert "headers are missing" in reply["reason"]


@pytest.mark.parametrize("url", [
    "http://api.sandbox.paypal.com/certs/1", "https://evil.example/paypal.com", "https://paypal.com.evil.example/c",
    "https://notpaypal.com/c", "not a url",
])
def test_a_certificate_address_that_is_not_paypals_is_refused_before_anything_is_sent(hook, url):
    webhook, verify = hook
    status, reply = run(webhook.receive({**HEADERS, "PAYPAL-CERT-URL": url}, event("X", {})))
    assert status == 401
    assert "not PayPal's" in reply["reason"]
    verify.assert_not_called()


def test_header_names_are_read_in_any_case(hook):
    webhook, verify = hook
    lower = {k.lower(): v for k, v in HEADERS.items()}
    assert run(webhook.receive(lower, event("X", {})))[0] == 200
    sent = verify.await_args.args
    assert sent[0]["paypal-transmission-id"] == "tx-1"
    assert sent[2] == "WEBHOOK-9"


def test_a_delivery_paypal_does_not_vouch_for_applies_nothing(hook):
    webhook, verify = hook
    case = settled()
    verify.return_value = {**GENUINE, "body": {"verification_status": "FAILURE"}}
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.CAPTURE.REVERSED", {"id": f"CAP-{case['tag']}"})))
    assert status == 401
    assert "not PayPal's" in reply["reason"]
    assert frozen("carrier", case["carrier"]) is None


@pytest.mark.parametrize("receipt, expected", [
    ({"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}, 503),
    ({"status": "http_error", "http_status": 503, "body": {}, "error": "503"}, 503),
    ({"status": "not_configured", "http_status": None, "body": None, "error": "x"}, 503),
    ({"status": "http_error", "http_status": 400, "body": {}, "error": "400"}, 401),
    ({"status": "ok", "http_status": 200, "body": "SUCCESS", "error": None}, 401),
])
def test_a_verification_that_fails_is_a_503_when_paypal_may_try_again_and_a_401_when_not(hook, receipt, expected):
    webhook, verify = hook
    verify.return_value = receipt
    assert run(webhook.receive(HEADERS, event("X", {})))[0] == expected


def test_a_payout_status_is_recorded_on_its_case(hook):
    webhook, _ = hook
    case = settled()
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {
        "sender_batch_id": f"{case['tag']}-payout", "transaction_status": "SUCCESS",
    })))
    assert status == 200
    assert reply["outcome"] == "applied"
    assert reply["case_id"] == case["case_id"]
    assert run(mod("payout_tracking").confirmation(case["case_id"]))["state"] == "confirmed"
    assert frozen("carrier", case["carrier"]) is None


@pytest.mark.parametrize("kind", ["FAILED", "RETURNED", "BLOCKED", "DENIED", "REFUNDED"])
def test_a_payout_that_did_not_reach_the_carrier_freezes_the_carrier(hook, kind):
    webhook, _ = hook
    case = settled()
    status, reply = run(webhook.receive(HEADERS, event(f"PAYMENT.PAYOUTS-ITEM.{kind}", {
        "sender_batch_id": f"{case['tag']}-payout",
    })))
    assert status == 200
    assert reply["payout_status"] == "failed"
    assert kind in frozen("carrier", case["carrier"])["reason"]
    assert frozen("shipper", case["shipper"]) is None


@pytest.mark.parametrize("kind", ["DENIED", "REFUNDED", "REVERSED"])
def test_a_capture_that_is_taken_back_freezes_the_carrier_and_the_shipper(hook, kind):
    webhook, _ = hook
    case = settled()
    status, reply = run(webhook.receive(HEADERS, event(f"PAYMENT.CAPTURE.{kind}", {"id": f"CAP-{case['tag']}"})))
    assert status == 200
    assert reply["frozen"] == ["carrier", "shipper"]
    assert kind in frozen("carrier", case["carrier"])["reason"]
    assert frozen("shipper", case["shipper"]) is not None


def test_an_opened_dispute_freezes_the_carrier_and_the_shipper(hook):
    webhook, _ = hook
    case = settled()
    status, reply = run(webhook.receive(HEADERS, event("CUSTOMER.DISPUTE.CREATED", {
        "disputed_transactions": [{"seller_transaction_id": "CAP-OTHER"}, {"seller_transaction_id": f"CAP-{case['tag']}"}],
    })))
    assert status == 200
    assert reply["outcome"] == "applied"
    assert "dispute" in frozen("shipper", case["shipper"])["reason"]
    assert frozen("carrier", case["carrier"]) is not None


@pytest.mark.parametrize("event_type", ["CUSTOMER.DISPUTE.RESOLVED", "CUSTOMER.DISPUTE.UPDATED", "PAYMENT.CAPTURE.COMPLETED"])
def test_events_that_loosen_nothing_are_recorded_and_freeze_nothing(hook, event_type):
    webhook, _ = hook
    case = settled()
    status, reply = run(webhook.receive(HEADERS, event(event_type, {
        "id": f"CAP-{case['tag']}", "disputed_transactions": [{"seller_transaction_id": f"CAP-{case['tag']}"}],
    })))
    assert status == 200
    assert reply["outcome"] == "recorded"
    assert frozen("carrier", case["carrier"]) is None
    assert frozen("shipper", case["shipper"]) is None


def test_an_event_that_matches_no_settlement_is_recorded_as_unmatched(hook):
    webhook, _ = hook
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.CAPTURE.REVERSED", {"id": "CAP-NOBODY"})))
    assert status == 200
    assert reply["outcome"] == "unmatched"
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.PAYOUTS-ITEM.FAILED", {"sender_batch_id": "nobody-payout"})))
    assert reply["outcome"] == "unmatched"
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.PAYOUTS-ITEM.FAILED", {"sender_batch_id": "-payout"})))
    assert reply["outcome"] == "unmatched"


def test_the_same_event_delivered_twice_is_applied_once(hook):
    webhook, _ = hook
    case = settled()
    delivery = event("PAYMENT.CAPTURE.REVERSED", {"id": f"CAP-{case['tag']}"}, event_id="WH-TWICE-" + case["tag"])
    run(webhook.receive(HEADERS, delivery))
    run(mod("payment_freeze").lift("carrier", case["carrier"], "checked", "admin@forwarder.example", "password"))
    status, reply = run(webhook.receive(HEADERS, delivery))
    assert status == 200
    assert reply["outcome"] == "applied"
    assert frozen("carrier", case["carrier"]) is None
    rows, _ = run(get_store().query_audit(case_id="PAYPAL-EVENTS", action="paypal_event", limit=200))
    assert [r["audit_id"] for r in rows].count("ppevt-WH-TWICE-" + case["tag"]) == 1


def test_a_failure_while_applying_asks_paypal_to_deliver_the_event_again(hook, monkeypatch):
    webhook, _ = hook
    monkeypatch.setattr(webhook, "_apply", AsyncMock(side_effect=RuntimeError("store down")))
    status, reply = run(webhook.receive(HEADERS, event("PAYMENT.CAPTURE.REVERSED", {"id": "CAP-1"})))
    assert status == 503
    assert reply["outcome"] == "retry"
    assert "RuntimeError" in reply["reason"]
