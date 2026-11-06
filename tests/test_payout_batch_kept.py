"""
The settler keeps its own record of a payout (review F08, 2026-11-05).

PayPal refuses a repeated sender_batch_id for 30 days only, and its refusal may carry no link. Pinned here: an attempt is written
before PayPal is asked and the batch id as soon as PayPal answers; a kept batch is read and never sent again; an attempt with no
batch, older than PayPal keeps the key, is held for a person; the settlement says how a payout was recovered.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))
from test_payout_recovery import DUPLICATE, batch  # noqa: E402
from test_settler import DOWN, PAID_OUT, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store, utcnow  # noqa: E402


def rows(key):
    found, _ = run(get_store().query_audit(case_id=f"PAYBATCH-{key}-payout", limit=10))
    return found


def kept(key, batch_id="BATCH-DUP"):
    run(get_store().add_audit({
        "case_id": f"PAYBATCH-{key}-payout", "action": "settle_payout_batch", "status": "done",
        "detail": {"batch_id": batch_id}, "at": utcnow(),
    }))


def started(key, at):
    run(get_store().add_audit({
        "audit_id": f"payatt-{key}-payout", "case_id": f"PAYBATCH-{key}-payout", "action": "settle_payout_started",
        "status": "done", "detail": {}, "at": at,
    }))


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_a_payout_paypal_accepted_keeps_its_batch_id(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    assert run(settler.settle(d))["status"] == "settled"
    found = [r for r in rows(d["idempotency_key"]) if r["action"] == "settle_payout_batch"]
    assert found[0]["detail"]["batch_id"] == "PAYOUT-1"


def test_the_attempt_is_written_before_paypal_is_asked(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    seen = []

    async def ask(*args):
        found, _ = await get_store().query_audit(case_id=f"PAYBATCH-{d['idempotency_key']}-payout", limit=10)
        seen.extend(r["action"] for r in found)
        return PAID_OUT

    payout.side_effect = ask
    run(settler.settle(d))
    assert seen == ["settle_payout_started"]


def test_a_kept_batch_is_read_and_never_sent_again(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    kept(d["idempotency_key"])
    read = AsyncMock(return_value=batch(d["idempotency_key"]))
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", read)
    result = run(settler.settle(d))
    assert result["status"] == "settled"
    assert result["recovered_from"] == "kept_batch"
    assert read.await_args.args[0] == "BATCH-DUP"
    payout.assert_not_awaited()


def test_a_kept_batch_that_cannot_be_read_asks_for_redelivery_and_sends_nothing(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    kept(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=DOWN))
    assert run(settler.settle(d))["status"] == "retry"
    payout.assert_not_awaited()


def test_a_kept_batch_that_is_another_payout_fails_and_sends_nothing(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    kept(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"], sender="exec-other-payout")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "did not pay the carrier" in result["reason"]
    payout.assert_not_awaited()


def test_an_attempt_older_than_the_key_is_held_for_a_person(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    started(d["idempotency_key"], days_ago(40))
    result = run(settler.settle(d))
    assert result["status"] == "held"
    assert "older than PayPal keeps" in result["reason"]
    payout.assert_not_awaited()


def test_an_attempt_whose_date_cannot_be_read_is_held(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    started(d["idempotency_key"], "not a date")
    assert run(settler.settle(d))["status"] == "held"
    payout.assert_not_awaited()


def test_a_recent_attempt_with_no_batch_is_sent_under_the_same_key(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(new_case())
    started(d["idempotency_key"], days_ago(1))
    assert run(settler.settle(d))["status"] == "settled"
    assert payout.await_args.args[3] == f"{d['idempotency_key']}-payout"


def test_a_batch_found_through_a_duplicate_refusal_is_kept_and_named(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    payout.return_value = DUPLICATE
    d = decision(new_case())
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(d["idempotency_key"])))
    result = run(settler.settle(d))
    assert result["recovered_from"] == "duplicate_refusal"
    assert any(r["detail"].get("batch_id") == "BATCH-DUP" for r in rows(d["idempotency_key"]))


def test_a_payout_sent_normally_names_no_recovery(pp):  # noqa: F811
    settler, capture, payout, void = pp
    assert "recovered_from" not in run(settler.settle(decision(new_case())))
