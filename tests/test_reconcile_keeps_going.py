"""
One kept event PayPal cannot answer for does not stop the others (re-check of review F05, 2026-11-05).

A dispute kept while PayPal answered raised out of reconcile() when its lookup failed, so a refund kept beside it froze nobody.
Pinned here: the other events are applied, and the event that failed stays in the queue for the next run.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))
from test_paypal_webhook import HEADERS, event, hook, run  # noqa: E402, F401
from vf_logistics.store import get_store, utcnow  # noqa: E402

QUEUE = "PAYPAL-UNMATCHED"
NO_ANSWER = {"status": "transport_error", "body": None}


def settled_capture(capture_id):
    """A settlement indexed under this capture, and the carrier a freeze must reach."""
    tag = uuid.uuid4().hex[:8]
    case_id, carrier = f"CASE-{tag}", f"Mekong {tag} Co"
    run(get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": {
        "shipment_id": case_id, "carrier_name": carrier, "shipper_company": f"Saigon {tag} Co"}}))
    run(get_store().add_audit_once({
        "audit_id": f"capidx-{capture_id}", "case_id": f"CAPTURE-{capture_id}", "action": "settle_capture_index",
        "status": "done", "detail": {"settled_case_id": case_id, "idempotency_key": tag}, "at": utcnow(),
    }))
    return carrier


def kept_dispute():
    event_id = f"WH-{uuid.uuid4().hex[:8]}"
    run(get_store().add_audit_once({
        "audit_id": f"ppunm-{event_id}", "case_id": QUEUE, "action": "paypal_event_pending", "status": "pending",
        "detail": {"event_id": event_id, "event_type": "CUSTOMER.DISPUTE.CREATED",
                   "resource": {"id": "D-1", "dispute_id": "D-1", "links": []}},
        "at": utcnow(),
    }))


def kept_refund(module, capture_id):
    refund = event("PAYMENT.CAPTURE.REFUNDED", {
        "id": f"REF-{capture_id}", "links": [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}],
    })
    run(module.receive(HEADERS, refund))


def frozen(name):
    return run(importlib.import_module("vf_logistics.payment_freeze").frozen("carrier", name))


def test_a_refund_is_applied_although_a_dispute_cannot_be_looked_up(hook, monkeypatch):  # noqa: F811
    module, _ = hook
    capture_id = f"CAP{uuid.uuid4().hex[:10].upper()}"
    kept_refund(module, capture_id)
    carrier = settled_capture(capture_id)
    kept_dispute()
    monkeypatch.setattr(module.paypal_client, "get_dispute", AsyncMock(return_value=NO_ANSWER))
    result = run(module.reconcile(None))
    assert result["applied"] >= 1
    assert result["pending"] >= 1
    assert frozen(carrier) is not None


def test_the_order_of_the_events_does_not_matter(hook, monkeypatch):  # noqa: F811
    module, _ = hook
    kept_dispute()
    capture_id = f"CAP{uuid.uuid4().hex[:10].upper()}"
    kept_refund(module, capture_id)
    carrier = settled_capture(capture_id)
    monkeypatch.setattr(module.paypal_client, "get_dispute", AsyncMock(return_value=NO_ANSWER))
    run(module.reconcile(None))
    assert frozen(carrier) is not None


def test_the_event_that_failed_stays_for_the_next_run(hook, monkeypatch):  # noqa: F811
    module, _ = hook
    kept_dispute()
    monkeypatch.setattr(module.paypal_client, "get_dispute", AsyncMock(return_value=NO_ANSWER))
    run(module.reconcile(None))
    assert run(module.reconcile(None, apply=False))["pending"] >= 1
