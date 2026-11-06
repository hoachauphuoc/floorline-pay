"""
Dual approval of a large release (dual_approval.py with evidence.py).

Pinned here: the threshold defaults to 2500 and reads PAYMENT_DUAL_APPROVAL_USD, and a
value that cannot be read means every payout needs two people; a release at or above it is
marked as needing a second approver and a block never is; such a release cannot be executed
until a different person, signed in with a password, has approved it with a note; and each
refusal comes back with its status and what to do next.

dual_approval is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import importlib
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import REVIEWER, Base, approve_open, run  # noqa: E402
from test_origin_inputs import plywood  # noqa: E402
from vf_logistics import evidence  # noqa: E402

SECOND = "second.reviewer@forwarder.example"
NOTE = "checked the invoice and the booking against the carrier contract"


def dual():
    return importlib.import_module("vf_logistics.dual_approval")


def payment(amount, **over):
    return plywood(
        payout_amount=amount, paypal_authorization_id="0VF52814937998046",
        carrier_payee_email="payments@mekong-haulage.example", **over,
    )


class TestTheRule:
    def test_the_threshold_is_2500_until_the_environment_says_otherwise(self, monkeypatch):
        monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
        assert dual().threshold() == Decimal("2500")
        monkeypatch.setenv("PAYMENT_DUAL_APPROVAL_USD", "1000")
        assert dual().threshold() == Decimal("1000")

    @pytest.mark.parametrize("raw", ["abc", "-5", "nan", "inf"])
    def test_a_threshold_that_cannot_be_read_makes_every_payout_need_two_people(self, monkeypatch, raw):
        monkeypatch.setenv("PAYMENT_DUAL_APPROVAL_USD", raw)
        assert dual().threshold() == 0
        assert dual().needed(payment(1.0)) is True

    def test_a_payout_needs_two_people_from_the_threshold_up(self, monkeypatch):
        monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
        assert dual().needed(payment(2499.99)) is False
        assert dual().needed(payment(2500)) is True
        assert dual().needed(payment(9000)) is True

    def test_a_shipment_that_moves_no_money_never_needs_two_people(self):
        assert dual().needed(plywood()) is False

    def test_a_payment_case_without_a_readable_payout_needs_two_people(self):
        assert dual().needed(plywood(paypal_authorization_id="0VF52814937998046")) is True
        assert dual().needed(payment("not a number")) is True
        assert dual().needed(payment(0)) is True


class TestTheSecondApproval(Base):
    def _released(self, amount=3000.0, action="release"):
        self.put(shipment=payment(amount))
        self.request_and_submit()
        run(evidence.reevaluate("CASE-1"))
        result = run(approve_open("CASE-1", action, REVIEWER, note="Reviewed", actor_auth="password"))
        return result["approval"]["approval_id"]

    def _second(self, approval_id, who=SECOND, note=NOTE, auth="password"):
        return run(evidence.second_approve("CASE-1", approval_id, who, note, actor_auth=auth))

    def _refused(self, approval_id, status, phrase, **kwargs):
        with pytest.raises(evidence.EvidenceError) as raised:
            self._second(approval_id, **kwargs)
        assert raised.value.status == status
        assert phrase in raised.value.message

    def test_a_large_release_is_marked_as_needing_a_second_approver(self):
        approval_id = self._released(3000.0)
        approval = self.stored()["verification"]["approvals"][0]
        assert approval["approval_id"] == approval_id
        assert approval["requires_second"] is True
        assert "second_approval" not in approval

    def test_a_small_release_and_a_block_are_not(self):
        self._released(2000.0)
        assert "requires_second" not in self.stored()["verification"]["approvals"][0]

    def test_a_large_block_is_not_marked(self):
        self._released(3000.0, action="block")
        assert "requires_second" not in self.stored()["verification"]["approvals"][0]

    def test_the_release_cannot_be_executed_until_the_second_approval(self):
        approval_id = self._released()
        approval, reason = evidence.approval_is_executable(self.stored(), approval_id)
        assert approval is None
        assert "second approver" in reason
        with pytest.raises(evidence.EvidenceError) as raised:
            run(evidence.execute_approval("CASE-1", approval_id, REVIEWER, actor_auth="password"))
        assert raised.value.status == 409
        assert "second approver" in raised.value.message
        self._second(approval_id)
        approval, reason = evidence.approval_is_executable(self.stored(), approval_id)
        assert reason is None
        assert approval["approval_id"] == approval_id

    def test_the_second_approval_keeps_who_when_and_why(self):
        approval_id = self._released()
        result = self._second(approval_id)
        assert result["state"] == "APPROVED"
        stored = self.stored()["verification"]["approvals"][0]["second_approval"]
        assert stored["approver"] == SECOND
        assert stored["approver_auth"] == "password"
        assert stored["note"] == NOTE
        assert stored["at"]
        rows = [r for r in run(self.store.list_audit(limit=500)) if r["action"] == "evidence_second_approval"]
        assert len(rows) == 1
        assert rows[0]["actor"] == SECOND
        assert rows[0]["detail"]["first_approver"] == REVIEWER

    def test_the_first_approver_cannot_second_their_own_release(self):
        approval_id = self._released()
        self._refused(approval_id, 403, "different person", who=REVIEWER)
        self._refused(approval_id, 403, "different person", who=REVIEWER.upper())

    def test_a_one_click_session_cannot_second_a_release(self):
        approval_id = self._released()
        self._refused(approval_id, 403, "password sign-in", auth="one_click")

    def test_a_second_approval_needs_a_note(self):
        approval_id = self._released()
        self._refused(approval_id, 400, "needs a note", note="   ")

    def test_a_release_that_does_not_need_two_refuses_a_second_approval(self):
        approval_id = self._released(2000.0)
        self._refused(approval_id, 409, "does not need a second approver")

    def test_a_release_is_seconded_only_once(self):
        approval_id = self._released()
        self._second(approval_id)
        self._refused(approval_id, 409, "already has its second approval", who="third@forwarder.example")

    def test_an_unknown_approval_is_not_found(self):
        self._released()
        self._refused("evapr-nope", 404, "approval not found")
