"""
How a PayPal payout event finds its settlement (settler.py, paypal_webhook.py).

A payout event names the batch "<decision key>-payout". The webhook scanned only the newest settled rows for that key, so a
failed or returned payout on an older settlement matched nothing and froze nobody. Pinned here: the settler indexes each
decision key once under "PAYOUT-<key>", and the webhook finds the settlement through that index however many settlements came
after it.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402


def apply(event_type, resource):
    webhook = importlib.import_module("vf_logistics.paypal_webhook")
    return run(webhook._apply(f"WH-{uuid.uuid4().hex[:8]}", event_type, resource, None))


def test_the_settler_indexes_the_payout_key_once(pp):  # noqa: F811
    settler, capture, payout, void = pp
    case_id = new_case()
    d = decision(case_id)
    assert run(settler.settle(d))["status"] == "settled"
    assert run(settler.settle(d))["status"] == "settled"
    rows, _ = run(get_store().query_audit(case_id=f"PAYOUT-{d['idempotency_key']}", limit=10))
    assert len(rows) == 1
    assert rows[0]["detail"]["settled_case_id"] == case_id


def test_a_payout_event_finds_its_settlement_past_the_scan_window(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    webhook = importlib.import_module("vf_logistics.paypal_webhook")
    first_case = new_case()
    first = decision(first_case)
    assert run(settler.settle(first))["status"] == "settled"
    assert run(settler.settle(decision(new_case())))["status"] == "settled"
    monkeypatch.setattr(webhook, "_PAGES", 1)
    monkeypatch.setattr(webhook, "_PAGE", 1)
    effect = apply("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"sender_batch_id": f"{first['idempotency_key']}-payout"})
    assert effect["matched"] is True
    assert effect["case_id"] == first_case


def test_a_payout_event_for_an_unknown_batch_matches_nothing(pp):  # noqa: F811
    settler, capture, payout, void = pp
    assert run(settler.settle(decision(new_case())))["status"] == "settled"
    assert apply("PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"sender_batch_id": "exec-nobody-payout"}) == {"matched": False}
