"""
The indexes that tie a PayPal event to its settlement exist before the payout (review F05, 2026-11-05).

The settler wrote "CAPTURE-<capture id>" and "PAYOUT-<key>" only after the payout succeeded. A capture that completed and whose
payout then failed had no index, so PayPal's refund or dispute on that capture matched nothing and froze nobody. Pinned here:
after a completed capture whose payout PayPal refused, or could not answer, both rows exist and a refund event on the capture
finds the case and freezes the shipper; a capture that did not complete writes no index.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import REFUSED, UNAVAILABLE, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402


def captured(capture_id, state="COMPLETED"):
    return {"status": "ok", "http_status": 201, "body": {"id": capture_id, "status": state}, "error": None}


def rows(case_id):
    found, _ = run(get_store().query_audit(case_id=case_id, limit=10))
    return found


def test_a_refused_payout_leaves_both_indexes_behind(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    payout.return_value = REFUSED
    case_id = new_case()
    d = decision(case_id)
    assert run(settler.settle(d))["status"] == "failed"
    assert rows(f"CAPTURE-{capture_id}")[0]["detail"]["settled_case_id"] == case_id
    assert rows(f"PAYOUT-{d['idempotency_key']}")[0]["detail"]["settled_case_id"] == case_id


def test_an_unanswered_payout_leaves_both_indexes_behind_and_one_row_each_after_the_retry(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    payout.return_value = UNAVAILABLE
    case_id = new_case()
    d = decision(case_id)
    assert run(settler.settle(d))["status"] == "retry"
    assert len(rows(f"CAPTURE-{capture_id}")) == 1
    assert len(rows(f"PAYOUT-{d['idempotency_key']}")) == 1
    payout.return_value = {"status": "ok", "http_status": 201, "body": {"batch_header": {"payout_batch_id": "B1"}}, "error": None}
    assert run(settler.settle(d))["status"] == "settled"
    assert len(rows(f"CAPTURE-{capture_id}")) == 1
    assert len(rows(f"PAYOUT-{d['idempotency_key']}")) == 1


def test_a_refund_on_a_capture_whose_payout_failed_freezes_the_shipper(pp):  # noqa: F811
    settler, capture, payout, void = pp
    webhook = importlib.import_module("vf_logistics.paypal_webhook")
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    payout.return_value = REFUSED
    case_id = new_case()
    assert run(settler.settle(decision(case_id)))["status"] == "failed"
    effect = run(webhook._apply(f"WH-{uuid.uuid4().hex[:8]}", "PAYMENT.CAPTURE.REFUNDED", {"id": capture_id}, None))
    assert effect["matched"] is True
    assert effect["case_id"] == case_id
    assert "shipper" in effect["frozen"]


def test_a_capture_that_did_not_complete_writes_no_index(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id, state="PENDING")
    d = decision(new_case())
    assert run(settler.settle(d))["status"] == "retry"
    payout.assert_not_called()
    assert rows(f"CAPTURE-{capture_id}") == []
    assert rows(f"PAYOUT-{d['idempotency_key']}") == []
