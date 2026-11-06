"""
How a PayPal capture event finds its settlement (settler.py, paypal_webhook.py).

The webhook scanned only the newest settled rows, so a refund, reversal or dispute on an older capture matched nothing and
froze nobody. Pinned here: the settler indexes each capture once under "CAPTURE-<capture id>", and the webhook finds the
settlement through that index however many settlements came after it.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import OK, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402


def captured(capture_id):
    return {"status": "ok", "http_status": 201, "body": {"id": capture_id, "status": "COMPLETED"}, "error": None}


def test_the_settler_indexes_the_capture_once(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    case_id = new_case()
    d = decision(case_id)
    assert run(settler.settle(d))["status"] == "settled"
    assert run(settler.settle(d))["status"] == "settled"
    rows, _ = run(get_store().query_audit(case_id=f"CAPTURE-{capture_id}", limit=10))
    assert len(rows) == 1
    assert rows[0]["detail"]["settled_case_id"] == case_id


def test_a_reversed_capture_finds_its_settlement_past_the_scan_window(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    webhook = importlib.import_module("vf_logistics.paypal_webhook")
    capture_id = f"CAP{uuid.uuid4().hex[:12].upper()}"
    capture.return_value = captured(capture_id)
    case_id = new_case()
    assert run(settler.settle(decision(case_id)))["status"] == "settled"
    capture.return_value = OK
    assert run(settler.settle(decision(new_case())))["status"] == "settled"
    monkeypatch.setattr(webhook, "_PAGES", 1)
    monkeypatch.setattr(webhook, "_PAGE", 1)
    effect = run(webhook._apply(f"WH-{uuid.uuid4().hex[:8]}", "PAYMENT.CAPTURE.REVERSED", {"id": capture_id}, None))
    assert effect["matched"] is True
    assert effect["case_id"] == case_id
