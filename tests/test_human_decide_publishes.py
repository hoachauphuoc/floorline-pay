"""
A human release or block of a booked payment is published, so the settler can capture or void the hold.

human_decide recorded the decision and held or released the shipment but published nothing: only the evidence loop
(evidence.execute_approval) did. A case booked through /book has no verification record, so it was decided through
human_decide, no BLOCK or RELEASE reached the case-decisions topic, and the PayPal hold stayed open (seen on a real
sandbox booking, 30 Oct 2026). Pinned here: a decision on a case whose shipment carries a PayPal authorization is
published once, with a key fixed per case and action; any other case publishes nothing new; a publish that fails leaves
the human decision recorded and says so in the answer.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base, run  # noqa: E402
from test_origin_inputs import plywood  # noqa: E402
from vf_logistics import orchestrator  # noqa: E402

SENT = {"status": "done", "detail": {"message_id": "m-1"}}
HELD = {"action": "hold_shipment", "status": "done"}
RELEASED = {"action": "release_shipment", "status": "done"}


def booked():
    return {**plywood(), "paypal_authorization_id": "AUTH-1"}


class TestAHumanDecisionOnABookedPaymentIsPublished(Base):
    def decide(self, action, auth="password", note="reviewed"):
        with patch.object(orchestrator.tools, "hold_shipment", new=AsyncMock(return_value=HELD)), \
             patch.object(orchestrator.tools, "release_shipment", new=AsyncMock(return_value=RELEASED)):
            return run(orchestrator.human_decide("CASE-1", action, "ana", note, actor_auth=auth))

    def test_a_block_publishes_one_decision_with_a_key_fixed_per_case_and_action(self):
        self.put(shipment=booked())
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=SENT)) as publish:
            result = self.decide("block")
        assert result["ok"] is True and result["state"] == "BLOCKED_BY_HUMAN" and result["published"] is True
        publish.assert_awaited_once()
        case_id, body = publish.await_args.args[:2]
        assert case_id == "CASE-1"
        assert body["outcome"] == "BLOCKED_BY_HUMAN" and body["action"] == "block" and body["approver"] == "ana"
        assert body["idempotency_key"] == "human-CASE-1-block"

    def test_a_release_publishes_the_released_outcome(self):
        self.put(shipment=booked())
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=SENT)) as publish:
            result = self.decide("release", note="")
        assert result["state"] == "RELEASED_BY_HUMAN" and result["published"] is True
        body = publish.await_args.args[1]
        assert body["outcome"] == "RELEASED_BY_HUMAN" and body["idempotency_key"] == "human-CASE-1-release"

    def test_a_case_with_no_paypal_authorization_publishes_nothing_new(self):
        self.put()
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=SENT)) as publish:
            result = self.decide("block")
        assert result["ok"] is True and "published" not in result
        publish.assert_not_called()

    def test_asking_for_information_publishes_nothing(self):
        self.put(shipment=booked())
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=SENT)) as publish:
            result = self.decide("request_info", note="need the invoice")
        assert result["ok"] is True and "published" not in result
        publish.assert_not_called()

    def test_a_release_without_a_password_is_still_refused_and_publishes_nothing(self):
        self.put(shipment=booked())
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=SENT)) as publish:
            result = self.decide("release", auth="one_click", note="")
        assert result == {"ok": False, "error": "Releasing a shipment needs a password sign-in."}
        publish.assert_not_called()


class TestAFailedPublishIsSaid(Base):
    def decide_block(self):
        with patch.object(orchestrator.tools, "hold_shipment", new=AsyncMock(return_value=HELD)):
            return run(orchestrator.human_decide("CASE-1", "block", "ana", "reviewed", actor_auth="password"))

    def test_a_refused_publish_keeps_the_decision_and_says_it_was_not_sent(self):
        self.put(shipment=booked())
        refused = {"status": "failed", "detail": {"error": "403 permission denied"}}
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(return_value=refused)):
            result = self.decide_block()
        assert result["ok"] is True and result["state"] == "BLOCKED_BY_HUMAN"
        assert result["published"] is False and "decision was not published" in result["publish_error"]
        assert self.stored()["state"] == "BLOCKED_BY_HUMAN"

    def test_a_publish_that_raises_keeps_the_decision_and_names_the_error(self):
        self.put(shipment=booked())
        with patch.object(orchestrator.tools, "publish_decision", new=AsyncMock(side_effect=RuntimeError("topic gone"))):
            result = self.decide_block()
        assert result["ok"] is True and result["published"] is False
        assert "topic gone" in result["publish_error"]
        assert self.stored()["state"] == "BLOCKED_BY_HUMAN"
