"""
A PayPal event that arrives before its settlement can be found is kept and applied later (review F05, 2026-11-05).

An event that matched no settlement was recorded as unmatched and nothing else, so a refund or dispute that arrived early was lost
and froze nobody. Pinned here: an unmatched event is kept under "PAYPAL-UNMATCHED" with only the fields needed to apply it and
no payer data; reconcile applies it once its settlement is indexed, freezes the carrier and the shipper, and applies it once; an
event that still matches nothing stays kept; and an operator, not a reviewer, runs the reconcile through the API.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_paypal_webhook import HEADERS, event, hook, run  # noqa: E402, F401
from test_settlement_routes import OPERATOR, REVIEWER, as_person  # noqa: E402
from vf_logistics.app import app  # noqa: E402
from vf_logistics.store import get_store, utcnow  # noqa: E402

QUEUE = "PAYPAL-UNMATCHED"


def webhook():
    return importlib.import_module("vf_logistics.paypal_webhook")


def refund(capture_id, event_id):
    resource = {
        "id": f"REF-{uuid.uuid4().hex[:6]}",
        "links": [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}],
        "payer": {"email_address": "buyer-secret@example.com"},
        "note_to_payer": "private note",
    }
    return event("PAYMENT.CAPTURE.REFUNDED", resource, event_id)


def kept(event_id):
    rows, _ = run(get_store().query_audit(case_id=QUEUE, limit=200))
    return [r for r in rows if (r.get("detail") or {}).get("event_id") == event_id]


def index_the_settlement(capture_id):
    tag = uuid.uuid4().hex[:8]
    case_id, carrier, shipper = f"CASE-{tag}", f"Mekong Haulage {tag} Co", f"Saigon Plywood {tag} Co"
    shipment = {"shipment_id": case_id, "payout_amount": 1250.0, "carrier_name": carrier, "shipper_company": shipper}
    run(get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": shipment}))
    run(get_store().add_audit_once({
        "audit_id": f"capidx-{capture_id}", "case_id": f"CAPTURE-{capture_id}", "action": "settle_capture_index",
        "status": "done", "detail": {"settled_case_id": case_id, "idempotency_key": tag}, "at": utcnow(),
    }))
    return case_id, carrier, shipper


def test_an_unmatched_event_is_kept_without_payer_data(hook):  # noqa: F811
    hook_module, _ = hook
    event_id = f"WH-{uuid.uuid4().hex[:8]}"
    capture_id = f"CAP{uuid.uuid4().hex[:10].upper()}"
    status, reply = run(hook_module.receive(HEADERS, refund(capture_id, event_id)))
    assert (status, reply["outcome"]) == (200, "unmatched")
    rows = kept(event_id)
    assert len(rows) == 1
    assert rows[0]["detail"]["event_type"] == "PAYMENT.CAPTURE.REFUNDED"
    assert capture_id in str(rows[0]["detail"]["resource"])
    text = str(rows[0])
    assert "buyer-secret" not in text
    assert "private note" not in text


def test_reconcile_applies_a_kept_event_once_its_settlement_is_indexed(hook):  # noqa: F811
    hook_module, _ = hook
    event_id = f"WH-{uuid.uuid4().hex[:8]}"
    capture_id = f"CAP{uuid.uuid4().hex[:10].upper()}"
    run(hook_module.receive(HEADERS, refund(capture_id, event_id)))
    case_id, carrier, shipper = index_the_settlement(capture_id)
    freeze = importlib.import_module("vf_logistics.payment_freeze")
    assert run(freeze.frozen("shipper", shipper)) is None
    first = run(webhook().reconcile(None))
    assert first["applied"] >= 1
    assert run(freeze.frozen("shipper", shipper)) is not None
    assert run(freeze.frozen("carrier", carrier)) is not None
    applied, _ = run(get_store().query_audit(case_id=QUEUE, action="paypal_event_applied", limit=200))
    assert [r for r in applied if (r.get("detail") or {}).get("event_id") == event_id]
    again = run(webhook().reconcile(None))
    assert again["applied"] == 0


def test_an_event_that_still_matches_nothing_stays_kept(hook):  # noqa: F811
    hook_module, _ = hook
    event_id = f"WH-{uuid.uuid4().hex[:8]}"
    run(hook_module.receive(HEADERS, refund(f"CAP{uuid.uuid4().hex[:10].upper()}", event_id)))
    result = run(webhook().reconcile(None))
    assert result["pending"] >= 1
    applied, _ = run(get_store().query_audit(case_id=QUEUE, action="paypal_event_applied", limit=200))
    assert not [r for r in applied if (r.get("detail") or {}).get("event_id") == event_id]
    assert len(kept(event_id)) == 1


class TestReconcilingFromTheConsole(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {"OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER})
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def test_an_operator_reconciles_and_gets_the_counts(self):
        r = self.client.post("/api/v1/payments/events/reconcile", headers=as_person(OPERATOR))
        assert r.status_code == 200
        assert set(r.get_json()) == {"applied", "pending"}

    def test_a_reviewer_cannot_reconcile(self):
        r = self.client.post("/api/v1/payments/events/reconcile", headers=as_person(REVIEWER))
        assert r.status_code == 403
