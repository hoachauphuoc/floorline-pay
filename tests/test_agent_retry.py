"""
A failed agent payment, retried by a person, and the payment status of a case the agent paid.

Pinned here: a payment that failed after the capture is sent again under the agent's key, with the outcome it had and the person who
published the limit as the approver; a case handed to a person is not sent again, the person releases or blocks it; the status of a
paid case names the limit and the person, while a person's release shows none; and the status says when a case went to a person.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics import payment_status, settlement_retry
from vf_logistics import store as store_mod

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
AGENT_RECORD = {"limit_usd": "1000", "published_by": ADMIN}


def run(coro):
    return asyncio.run(coro)


def new_case(state="AUTO_CLEARED"):
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(store_mod.get_store().put_case({
        "case_id": case_id, "state": state,
        "shipment": {"shipment_id": case_id, "paypal_authorization_id": "AUTH-1", "payout_amount": 900.0},
    }))
    return case_id


def settle_row(case_id, status, outcome="AUTO_CLEARED", key=None, **detail):
    key = key or f"agent-{case_id}"
    run(store_mod.get_store().add_audit({
        "audit_id": f"settle-{key}-{status}", "case_id": case_id, "action": "settle_payment", "status": status,
        "detail": {"status": status, "reason": "the record", "idempotency_key": key, "outcome": outcome, **detail},
        "at": "2026-11-06T00:00:00+00:00",
    }))
    return key


def first_publish(case_id, key, approver=ADMIN):
    run(store_mod.get_store().add_audit({
        "audit_id": f"pub-{key}", "case_id": case_id, "action": "publish_decision", "status": "done",
        "detail": {"idempotency_key": key, "outcome": "AUTO_CLEARED", "approver": approver, "message_id": "m-1"},
        "at": "2026-11-06T00:00:00+00:00",
    }))


@pytest.fixture
def publish():
    receipt = {"status": "done", "detail": {"message_id": "m-2"}}
    with patch.object(settlement_retry.tools, "publish_decision", AsyncMock(return_value=receipt)) as sent:
        yield sent


def test_a_payment_that_failed_after_the_capture_is_sent_again_under_the_agents_key(publish):
    case_id = new_case()
    key = settle_row(case_id, "failed", agent=AGENT_RECORD)
    first_publish(case_id, key)
    result = run(settlement_retry.retry(case_id, "checked the carrier's PayPal account", OPERATOR))
    assert result == {"status": "redelivered", "case_id": case_id}
    decision = publish.await_args.args[1]
    assert decision["outcome"] == "AUTO_CLEARED"
    assert decision["idempotency_key"] == key
    assert decision["approver"] == ADMIN
    assert decision["sent_by"] == OPERATOR
    assert publish.await_args.kwargs["redeliver"] is True


def test_a_case_handed_to_a_person_is_not_sent_again(publish):
    case_id = new_case(state="PENDING_HUMAN")
    key = settle_row(case_id, "failed", agent=AGENT_RECORD, handed_to_person=True)
    first_publish(case_id, key)
    with pytest.raises(settlement_retry.RetryError) as raised:
        run(settlement_retry.retry(case_id, "try again", OPERATOR))
    assert raised.value.status == 409
    assert "release or block" in raised.value.message
    publish.assert_not_called()


def test_a_persons_release_is_still_sent_again_with_its_own_outcome(publish):
    case_id = new_case(state="RELEASED_BY_HUMAN")
    key = settle_row(case_id, "failed", outcome="RELEASED_BY_HUMAN", key=f"human-{case_id}-release")
    first_publish(case_id, key, approver="ana@forwarder.example")
    run(settlement_retry.retry(case_id, "checked", OPERATOR))
    assert publish.await_args.args[1]["outcome"] == "RELEASED_BY_HUMAN"


def test_another_outcome_is_still_not_sent_again(publish):
    case_id = new_case(state="BLOCKED_BY_HUMAN")
    key = settle_row(case_id, "failed", outcome="BLOCKED_BY_HUMAN", key=f"human-{case_id}-block")
    first_publish(case_id, key)
    with pytest.raises(settlement_retry.RetryError) as raised:
        run(settlement_retry.retry(case_id, "try again", OPERATOR))
    assert raised.value.status == 409
    assert "only the settlement of a release" in raised.value.message
    publish.assert_not_called()


def test_the_status_of_a_paid_case_names_the_limit_and_the_person():
    case_id = new_case()
    settle_row(case_id, "settled", agent=AGENT_RECORD)
    state = run(payment_status.payment_state(case_id))
    assert state["state"] == "paid"
    assert state["paid_by_agent"] == AGENT_RECORD
    assert state["handed_to_person"] is False


def test_the_status_of_a_persons_release_names_no_agent():
    case_id = new_case(state="RELEASED_BY_HUMAN")
    settle_row(case_id, "settled", outcome="RELEASED_BY_HUMAN", key=f"human-{case_id}-release")
    state = run(payment_status.payment_state(case_id))
    assert state["state"] == "paid"
    assert state["paid_by_agent"] is None


def test_the_status_says_when_the_agent_gave_a_case_to_a_person():
    case_id = new_case(state="PENDING_HUMAN")
    settle_row(case_id, "failed", agent=AGENT_RECORD, handed_to_person=True)
    state = run(payment_status.payment_state(case_id))
    assert state["state"] == "failed"
    assert state["handed_to_person"] is True
