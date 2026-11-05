"""
One new payout after PayPal denied the first batch (re-check of review F08, 2026-11-05).

The money is captured, the batch paid nobody, and the same key can only hit the same batch again. Pinned here: the settlement names
the dead batch; without a person's recorded request nothing new is sent; with it, PayPal must still show that batch as dead, and the
new payout goes under <key>-payout-2, once; anything PayPal cannot prove sends nothing.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))
from test_payout_batch_kept import kept  # noqa: E402
from test_payout_recovery import DUPLICATE, batch  # noqa: E402
from test_settler import DOWN, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store, utcnow  # noqa: E402


def replacement(key, batch_id="BATCH-DUP"):
    run(get_store().add_audit({
        "audit_id": f"paynew-{key}", "case_id": f"PAYNEW-{key}", "action": "payout_replacement", "status": "requested",
        "detail": {"dead_batch_id": batch_id, "reason": "PayPal denied it", "approved_by": "ops@forwarder.example"}, "at": utcnow(),
    }))


def test_a_duplicate_whose_batch_was_denied_names_it(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="DENIED")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert result["dead_batch"] == "BATCH-DUP"
    assert "one new payout" in result["reason"]


def test_a_kept_batch_that_was_canceled_names_it_and_sends_nothing(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    kept(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="CANCELED")))
    result = run(settler.settle(d))
    assert result["dead_batch"] == "BATCH-DUP"
    payout.assert_not_awaited()


def test_a_batch_in_an_unknown_state_names_no_dead_batch(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="SOMETHING_NEW")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "dead_batch" not in result


def test_with_no_recorded_request_no_new_batch_is_sent(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="DENIED")))
    run(settler.settle(d))
    run(settler.settle(d))
    assert {call.args[3] for call in payout.await_args_list} <= {f"{d['idempotency_key']}-payout"}


def test_a_recorded_request_and_a_dead_batch_send_one_new_payout(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    replacement(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="DENIED")))
    assert run(settler.settle(d))["status"] == "settled"
    payout.assert_awaited_once()
    assert payout.await_args.args[3] == f"{d['idempotency_key']}-payout-2"


def test_a_batch_paypal_no_longer_shows_as_dead_stops_the_new_payout(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    replacement(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], status="SUCCESS")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "no second payout" in result["reason"]
    payout.assert_not_awaited()


def test_a_dead_batch_that_cannot_be_read_asks_for_redelivery(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    replacement(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=DOWN))
    assert run(settler.settle(d))["status"] == "retry"
    payout.assert_not_awaited()


def test_the_new_payout_is_not_sent_twice(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    key = d["idempotency_key"]
    replacement(key)
    run(get_store().add_audit({
        "case_id": f"PAYBATCH-{key}-payout-2", "action": "settle_payout_batch", "status": "done",
        "detail": {"batch_id": "BATCH-2"}, "at": utcnow(),
    }))

    async def read(batch_id, request_id):
        if batch_id == "BATCH-2":
            return batch(key, sender=f"{key}-payout-2")
        return batch(key, status="DENIED")

    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", read)
    assert run(settler.settle(d))["status"] == "settled"
    payout.assert_not_awaited()
