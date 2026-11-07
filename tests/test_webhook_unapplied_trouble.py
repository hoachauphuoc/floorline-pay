"""
The settler can ask whether a kept PayPal event about a capture has not taken effect (follow-up review F05, 2026-11-07).

reconcile() logs an event it cannot apply and goes on, so the absence of a freeze does not prove a capture is clean. Pinned here: an
unapplied refund, reversal, denial or dispute that names the capture counts; one that names another capture, one already applied, a
payout event and a dispute kept without its transactions do not; and kept rows that cannot all be read count, because the capture
cannot be cleared.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest

from vf_logistics import store as store_mod
from vf_logistics.store import get_store, utcnow

QUEUE = "PAYPAL-UNMATCHED"


def run(coro):
    return asyncio.run(coro)


def webhook():
    return importlib.import_module("vf_logistics.paypal_webhook")


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(store_mod, "_store", None)


def up_link(capture_id):
    return [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}]


def keep(event_type, resource):
    event_id = f"WH-{uuid.uuid4().hex[:8]}"
    run(get_store().add_audit_once({
        "audit_id": f"ppunm-{event_id}", "case_id": QUEUE, "action": "paypal_event_pending", "status": "pending",
        "detail": {"event_id": event_id, "event_type": event_type, "resource": resource},
        "at": utcnow(),
    }))
    return event_id


def mark_applied(event_id):
    run(get_store().add_audit_once({
        "audit_id": f"ppdone-{event_id}", "case_id": QUEUE, "action": "paypal_event_applied", "status": "done",
        "detail": {"event_id": event_id}, "at": utcnow(),
    }))


@pytest.mark.parametrize("event_type", ["PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"])
def test_a_kept_refund_or_reversal_that_links_to_the_capture_counts(event_type):
    keep(event_type, {"id": "REF-1", "links": up_link("CAP-9")})
    assert run(webhook().unapplied_trouble("CAP-9")) is True


def test_a_kept_denial_counts_through_the_capture_id_it_carries():
    keep("PAYMENT.CAPTURE.DENIED", {"id": "CAP-9"})
    assert run(webhook().unapplied_trouble("CAP-9")) is True


def test_a_kept_dispute_that_lists_the_capture_counts():
    keep("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "D-1", "disputed_transactions": [{"seller_transaction_id": "CAP-9"}]})
    assert run(webhook().unapplied_trouble("CAP-9")) is True


def test_nothing_kept_is_no_trouble():
    assert run(webhook().unapplied_trouble("CAP-9")) is False


def test_a_kept_event_about_another_capture_does_not_count():
    keep("PAYMENT.CAPTURE.REFUNDED", {"id": "REF-1", "links": up_link("CAP-OTHER")})
    keep("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "D-1", "disputed_transactions": [{"seller_transaction_id": "CAP-OTHER"}]})
    assert run(webhook().unapplied_trouble("CAP-9")) is False


def test_an_event_already_applied_does_not_count():
    mark_applied(keep("PAYMENT.CAPTURE.REFUNDED", {"id": "REF-1", "links": up_link("CAP-9")}))
    assert run(webhook().unapplied_trouble("CAP-9")) is False


def test_a_payout_event_does_not_count():
    keep("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"id": "CAP-9"})
    assert run(webhook().unapplied_trouble("CAP-9")) is False


def test_a_dispute_kept_without_its_transactions_names_no_capture():
    keep("CUSTOMER.DISPUTE.CREATED", {"id": "D-1", "dispute_id": "D-1", "links": []})
    assert run(webhook().unapplied_trouble("CAP-9")) is False


def test_kept_rows_that_cannot_all_be_read_count(monkeypatch):
    keep("PAYMENT.CAPTURE.REFUNDED", {"id": "REF-1", "links": up_link("CAP-OTHER")})
    keep("PAYMENT.CAPTURE.REFUNDED", {"id": "REF-2", "links": up_link("CAP-OTHER")})
    monkeypatch.setattr(webhook(), "_PAGE", 1)
    monkeypatch.setattr(webhook(), "_PAGES", 1)
    assert run(webhook().unapplied_trouble("CAP-9")) is True
