"""
A decision a person saved but that never reached the topic (review F03, 2026-11-05).

human_decide saves a release or a block and then publishes it. When the publish failed, the case said RELEASED_BY_HUMAN, the
PayPal hold stayed open and the console had nothing to press. Pinned here: a person signed in with a password can send such a
decision, release or block, under its first key human-{case_id}-{action}; the reason is mandatory and the person is stored
before the send; a decision whose publish went out (it carries a message id), a case nobody decided, a case without a PayPal
authorization and a send that proves nothing are not sent or are answered 503; and the payment state tells the console that the
decision was not sent.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_status import SHIPMENT  # noqa: E402

CASE = "CASE-U"
SENT = {"status": "done", "detail": {"message_id": "MSG-9"}}
ACTOR = "ops@forwarder.example"
DECIDER = "reviewer@forwarder.example"
ACTIONS = {"RELEASED_BY_HUMAN": "release", "BLOCKED_BY_HUMAN": "block"}


def run(coro):
    return asyncio.run(coro)


def retry_mod():
    return importlib.import_module("vf_logistics.settlement_retry")


@pytest.fixture(autouse=True)
def _shipment_fields_stand(monkeypatch):
    registry = importlib.import_module("vf_logistics.carrier_registry")
    monkeypatch.setattr(registry, "enrich", AsyncMock(side_effect=lambda s, tenant_id=None: s))


class TestSendingASavedDecision(Base):
    def decide(self, state="RELEASED_BY_HUMAN", shipment=None):
        self.put(
            CASE, shipment=dict(SHIPMENT if shipment is None else shipment), state=state,
            reviews=[{"action": ACTIONS.get(state), "reviewer": DECIDER}],
        )

    def published(self, key):
        run(self.store.add_audit({
            "case_id": CASE, "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": key, "message_id": "MSG-1"}, "at": "2026-11-05T04:02:03+00:00",
        }))

    def refusal(self, reason="checked the topic", actor=ACTOR):
        try:
            run(retry_mod().retry(CASE, reason, actor))
        except retry_mod().RetryError as exc:
            return exc
        raise AssertionError("expected a RetryError")

    def test_a_saved_release_that_never_went_out_is_published_under_its_first_key(self):
        self.decide()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish, patch(
            "vf_logistics.settler.settle", AsyncMock(),
        ) as settle:
            result = run(retry_mod().retry(CASE, " the topic was down ", ACTOR))
        assert result == {"status": "redelivered", "case_id": CASE}
        settle.assert_not_awaited()
        decision = publish.await_args.args[1]
        assert decision["idempotency_key"] == f"human-{CASE}-release"
        assert decision["outcome"] == "RELEASED_BY_HUMAN"
        assert decision["action"] == "release"
        assert decision["approver"] == DECIDER
        assert decision["sent_by"] == ACTOR
        assert decision["executor"] == "settlement_retry"
        assert publish.await_args.kwargs.get("after_attempt") is True
        assert publish.await_args.kwargs.get("redeliver") is not True

    def test_a_saved_block_that_never_went_out_is_published_under_its_first_key(self):
        self.decide(state="BLOCKED_BY_HUMAN")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            result = run(retry_mod().retry(CASE, "the hold must be voided", ACTOR))
        assert result == {"status": "redelivered", "case_id": CASE}
        decision = publish.await_args.args[1]
        assert decision["idempotency_key"] == f"human-{CASE}-block"
        assert decision["outcome"] == "BLOCKED_BY_HUMAN"
        assert decision["action"] == "block"

    def test_the_person_and_the_reason_are_stored_even_when_the_send_fails(self):
        self.decide()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(side_effect=RuntimeError("topic down"))):
            exc = self.refusal(reason=" the topic was down ")
        assert exc.status == 503
        assert "could not be sent again" in exc.message
        rows, _ = run(self.store.query_audit(case_id=CASE, action="retry_settlement", limit=5))
        assert rows[0]["detail"]["retried_by"] == ACTOR
        assert rows[0]["detail"]["reason"] == "the topic was down"
        assert rows[0]["detail"]["idempotency_key"] == f"human-{CASE}-release"

    def test_a_saved_decision_that_names_nobody_is_not_sent(self):
        self.put(CASE, shipment=dict(SHIPMENT), state="RELEASED_BY_HUMAN")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "does not name who made" in exc.message
        publish.assert_not_awaited()

    def test_a_decision_whose_publish_went_out_is_not_sent_again(self):
        self.decide()
        self.published(f"human-{CASE}-release")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "only a settlement that ended failed" in exc.message
        publish.assert_not_awaited()

    def test_a_case_nobody_decided_is_not_sent(self):
        self.decide(state="HELD_FOR_REVIEW")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        publish.assert_not_awaited()

    def test_a_decision_on_a_case_with_no_paypal_authorization_is_not_sent(self):
        self.decide(shipment={**SHIPMENT, "paypal_authorization_id": ""})
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        publish.assert_not_awaited()

    def test_a_send_without_a_reason_is_refused_and_sends_nothing(self):
        self.decide()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal(reason="  ")
        assert exc.status == 400
        assert "reason is required" in exc.message
        publish.assert_not_awaited()

    def test_a_send_with_no_person_is_refused_and_sends_nothing(self):
        self.decide()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal(actor="")
        assert exc.status == 403
        assert "password" in exc.message
        publish.assert_not_awaited()

    def test_a_send_that_proves_no_message_is_answered_503(self):
        self.decide()
        failed = {"status": "failed", "detail": {"error": "no topic"}}
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=failed)):
            exc = self.refusal()
        assert exc.status == 503
        assert "nothing moved" in exc.message

    def test_the_payment_state_says_the_decision_was_not_sent(self):
        payment_status = importlib.import_module("vf_logistics.payment_status")
        self.decide()
        assert run(payment_status.payment_state(CASE))["decision_not_sent"] is True
        self.published(f"human-{CASE}-release")
        assert run(payment_status.payment_state(CASE))["decision_not_sent"] is False

    def test_the_payment_state_of_an_undecided_case_does_not_say_so(self):
        payment_status = importlib.import_module("vf_logistics.payment_status")
        self.decide(state="HELD_FOR_REVIEW")
        assert run(payment_status.payment_state(CASE))["decision_not_sent"] is False
