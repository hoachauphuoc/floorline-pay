"""
Payment code on a store that filters an audit query by one field only.

The Firestore store applies one of case_id, action and status in an audit query (store.py:
"At most one of case_id/action/status is expected per call"), where the memory store applies
all three. Code that asks for a case id and an action therefore gets, on Firestore, the newest
row of the case whatever its action. Every payment record that shares a case id with another
kind of row has to be read by picking the action from the rows in code.

Pinned here, on a store that behaves like Firestore in that one respect: published limits are
still read after a pause; a freeze and a lift written in the same instant are ordered by which
came last; a payout's confirmation and refresh find their rows among the other rows of the case;
and the payment state of a case is its settlement even when payout statuses came after it.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from vf_logistics import payment_controls, payment_freeze, payment_status, payout_tracking
from vf_logistics import store as store_mod
from vf_logistics.store import utcnow

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
LIMITS = {
    "max_payout_usd": 1000, "carrier_daily_usd": 2000, "tenant_daily_usd": 4000, "carrier_daily_count": 2,
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def one_filter(monkeypatch):
    store = store_mod.get_store()
    real = store.query_audit

    async def query_audit(case_id=None, action=None, status=None, cursor=None, limit=50, tenant_id=None):
        if case_id:
            action = status = None
        elif action:
            status = None
        return await real(case_id=case_id, action=action, status=status, cursor=cursor, limit=limit, tenant_id=tenant_id)

    monkeypatch.setattr(store, "query_audit", query_audit)


def settled_case():
    tag = uuid.uuid4().hex[:8]
    case_id, carrier = f"CASE-{tag}", f"Mekong Haulage {tag} Co"
    run(store_mod.get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": {
        "shipment_id": case_id, "currency": "USD", "payout_amount": 1250.0, "carrier_name": carrier,
        "paypal_authorization_id": "0VF52814937998046",
    }}))
    run(store_mod.get_store().add_audit({
        "audit_id": f"settle-{tag}", "case_id": case_id, "action": "settle_payment", "status": "settled",
        "detail": {"payout": {"paypal_id": f"BATCH-{tag}"}, "idempotency_key": tag, "reason": "captured and paid"},
        "at": utcnow(),
    }))
    return case_id, carrier


def test_published_limits_are_still_read_after_a_pause(one_filter):
    run(payment_controls.publish_limits(LIMITS, "tighter", ADMIN, "password"))
    run(payment_controls.pause("a carrier was phished", OPERATOR, "password"))
    assert run(payment_controls.limits()) == LIMITS
    assert run(payment_controls.pause_state())["paused"] is True
    run(payment_controls.resume("checked", ADMIN, "password"))
    assert run(payment_controls.pause_state())["paused"] is False
    assert run(payment_controls.limits()) == LIMITS


def test_a_lift_written_in_the_same_instant_as_the_freeze_still_lifts_it(one_filter, monkeypatch):
    monkeypatch.setattr(payment_freeze, "utcnow", lambda: "2026-10-23T01:00:00+00:00")
    carrier = f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"
    run(payment_freeze.freeze("carrier", carrier, "a dispute was opened", "event-1"))
    assert run(payment_freeze.frozen("carrier", carrier)) is not None
    run(payment_freeze.lift("carrier", carrier, "checked", ADMIN, "password"))
    assert run(payment_freeze.frozen("carrier", carrier)) is None
    run(payment_freeze.freeze("carrier", carrier, "again", "event-2"))
    assert run(payment_freeze.frozen("carrier", carrier))["reason"] == "again"


def test_a_case_with_only_a_settlement_is_unconfirmed(one_filter):
    case_id, _ = settled_case()
    assert run(payout_tracking.confirmation(case_id))["state"] == "unconfirmed"


def test_a_payout_status_is_found_among_the_other_rows_of_the_case(one_filter):
    case_id, carrier = settled_case()
    run(payout_tracking.apply(case_id, f"event-{case_id}", "SUCCESS", carrier))
    run(store_mod.get_store().add_audit({
        "audit_id": f"note-{case_id}", "case_id": case_id, "action": "evidence_approval_release",
        "status": "done", "detail": {}, "at": utcnow(),
    }))
    assert run(payout_tracking.confirmation(case_id))["state"] == "confirmed"


def test_asking_paypal_finds_the_settled_payout_after_newer_rows(one_filter, monkeypatch):
    case_id, carrier = settled_case()
    run(payout_tracking.apply(case_id, f"event-{case_id}", "PENDING", carrier))
    ok = {"status": "ok", "http_status": 200, "error": None, "body": {"items": [{"transaction_status": "SUCCESS"}]}}
    monkeypatch.setattr(payout_tracking.paypal_client, "get_payout_batch", AsyncMock(return_value=ok))
    assert run(payout_tracking.refresh(case_id))["state"] == "confirmed"


def test_the_payment_state_is_the_settlement_even_when_payout_statuses_came_after(one_filter):
    case_id, carrier = settled_case()
    run(payout_tracking.apply(case_id, f"event-{case_id}", "SUCCESS", carrier))
    state = run(payment_status.payment_state(case_id))
    assert state["state"] == "paid"
    assert state["payout_status"]["state"] == "confirmed"
