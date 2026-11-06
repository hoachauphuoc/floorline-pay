"""
The capture a refund or reversal event names (paypal_webhook._apply).

PAYMENT.CAPTURE.REFUNDED and .REVERSED carry the refund as their resource, so resource["id"] is the refund's, not the
capture's: the capture is the one the "up" link points to. The webhook read only resource["id"], matched nothing, answered
200 and froze nobody.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import decision, new_case, pp, run  # noqa: E402, F401


def captured(capture_id):
    return {"status": "ok", "http_status": 201, "body": {"id": capture_id, "status": "COMPLETED"}, "error": None}


def refund_resource(refund_id, capture_id):
    base = "https://api.sandbox.paypal.com/v2/payments"
    return {"id": refund_id, "links": [
        {"rel": "self", "href": f"{base}/refunds/{refund_id}"},
        {"rel": "up", "href": f"{base}/captures/{capture_id}"},
    ]}


def settled_capture(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    case_id = new_case()
    assert run(settler.settle(decision(case_id)))["status"] == "settled"
    return capture_id, case_id


def apply(event_type, resource):
    webhook = importlib.import_module("vf_logistics.paypal_webhook")
    return run(webhook._apply(f"WH-{uuid.uuid4().hex[:8]}", event_type, resource, None))


def test_a_refund_names_its_capture_through_the_up_link(pp):  # noqa: F811
    capture_id, case_id = settled_capture(pp)
    effect = apply("PAYMENT.CAPTURE.REFUNDED", refund_resource("REF" + uuid.uuid4().hex[:10].upper(), capture_id))
    assert effect["matched"] is True
    assert effect["case_id"] == case_id


def test_a_reversal_names_its_capture_through_the_up_link(pp):  # noqa: F811
    capture_id, case_id = settled_capture(pp)
    effect = apply("PAYMENT.CAPTURE.REVERSED", refund_resource("REV" + uuid.uuid4().hex[:10].upper(), capture_id))
    assert effect["matched"] is True
    assert effect["case_id"] == case_id


def test_a_denied_capture_still_matches_by_its_own_id(pp):  # noqa: F811
    capture_id, case_id = settled_capture(pp)
    effect = apply("PAYMENT.CAPTURE.DENIED", {"id": capture_id})
    assert effect["matched"] is True
    assert effect["case_id"] == case_id


def test_an_up_link_to_an_unknown_capture_matches_nothing(pp):  # noqa: F811
    settled_capture(pp)
    effect = apply("PAYMENT.CAPTURE.REFUNDED", refund_resource("REF" + uuid.uuid4().hex[:10].upper(), "CAPUNKNOWN0001"))
    assert effect == {"matched": False}
