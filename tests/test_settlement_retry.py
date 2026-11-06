"""
settlement_retry: a person sends a release whose settlement failed to the settler again.

Pinned here: the decision is published again under its first key, and settle is never called from here, because money moves
only in the settler downstream; the reason is mandatory and the person is stored before anything is sent; a settlement that
is not failed, one that already settled, the settlement of a block and a retry with no person are refused; a publish that
proves nothing is answered 503. The last test pins the redeliver flag of tools.publish_decision: without it a key that went
out once is never sent again.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

KEY = "human-CASE-R-release"
SENT = {"status": "done", "detail": {"message_id": "MSG-2"}}


def run(coro):
    return asyncio.run(coro)


def retry_mod():
    return importlib.import_module("vf_logistics.settlement_retry")


class TestRetryingASettlement(Base):
    def settlement(self, status, outcome="RELEASED_BY_HUMAN"):
        run(self.store.add_audit({
            "case_id": "CASE-R", "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": KEY, "approver": "reviewer@forwarder.example", "message_id": "MSG-1"},
            "at": "2026-11-05T04:02:00+00:00",
        }))
        run(self.store.add_audit({
            "case_id": "CASE-R", "action": "settle_payment", "status": status,
            "detail": {"status": status, "idempotency_key": KEY, "outcome": outcome}, "at": "2026-11-05T04:02:10+00:00",
        }))

    def refusal(self, reason="checked the balance", actor="ops@forwarder.example"):
        try:
            run(retry_mod().retry("CASE-R", reason, actor))
        except retry_mod().RetryError as exc:
            return exc
        raise AssertionError("expected a RetryError")

    def test_a_failed_release_is_published_again_under_its_key_and_never_settled_here(self):
        self.settlement("failed")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish, patch(
            "vf_logistics.settler.settle", AsyncMock(),
        ) as settle:
            result = run(retry_mod().retry("CASE-R", " topped up the balance ", "ops@forwarder.example"))
        assert result == {"status": "redelivered", "case_id": "CASE-R"}
        settle.assert_not_awaited()
        assert publish.await_args.args[0] == "CASE-R"
        decision = publish.await_args.args[1]
        assert decision["idempotency_key"] == KEY
        assert decision["outcome"] == "RELEASED_BY_HUMAN"
        assert decision["approver"] == "reviewer@forwarder.example"
        assert decision["sent_by"] == "ops@forwarder.example"
        assert publish.await_args.kwargs["redeliver"] is True

    def test_the_person_and_the_reason_are_stored_even_when_the_send_fails(self):
        self.settlement("failed")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(side_effect=RuntimeError("topic down"))):
            exc = self.refusal(reason=" topped up the balance ")
        assert exc.status == 503
        assert "could not be sent again" in exc.message
        rows, _ = run(self.store.query_audit(case_id="CASE-R", action="retry_settlement", limit=5))
        assert rows[0]["detail"]["retried_by"] == "ops@forwarder.example"
        assert rows[0]["detail"]["reason"] == "topped up the balance"
        assert rows[0]["detail"]["idempotency_key"] == KEY

    def test_a_publish_that_proves_no_message_is_answered_503(self):
        self.settlement("failed")
        failed = {"status": "failed", "detail": {"error": "no topic"}}
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=failed)):
            exc = self.refusal()
        assert exc.status == 503
        assert "nothing moved" in exc.message

    def test_a_retry_without_a_reason_is_refused_and_sends_nothing(self):
        self.settlement("failed")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal(reason="  ")
        assert exc.status == 400
        assert "reason is required" in exc.message
        publish.assert_not_awaited()

    def test_a_retry_with_no_person_is_refused(self):
        self.settlement("failed")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal(actor="")
        assert exc.status == 403
        assert "signed in with a password" in exc.message
        publish.assert_not_awaited()

    def test_a_case_with_no_settlement_is_refused(self):
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "only a settlement that ended failed" in exc.message
        publish.assert_not_awaited()

    def test_a_held_settlement_waits_for_a_person_and_is_not_sent_again(self):
        self.settlement("held")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "only a settlement that ended failed" in exc.message
        publish.assert_not_awaited()

    def test_a_settlement_that_settled_is_not_sent_again_whichever_row_is_newest(self):
        self.settlement("failed")
        self.settlement("settled")
        self.settlement("failed")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "only a settlement that ended failed" in exc.message
        publish.assert_not_awaited()

    def test_the_settlement_of_a_block_is_not_retried(self):
        self.settlement("failed", outcome="BLOCKED_BY_HUMAN")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            exc = self.refusal()
        assert exc.status == 409
        assert "only the settlement of a release" in exc.message
        publish.assert_not_awaited()

    def test_redeliver_sends_a_key_that_already_went_out_and_a_plain_publish_does_not(self):
        tools = importlib.import_module("vf_logistics.tools")
        run(self.store.add_audit({
            "case_id": "CASE-R", "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": KEY, "message_id": "MSG-1"}, "at": "2026-11-05T04:02:03+00:00",
        }))
        decision = {"outcome": "RELEASED_BY_HUMAN", "idempotency_key": KEY}
        with patch("vf_logistics.executor_client.configured", return_value=False), patch(
            "vf_logistics.tools.publish_decision_direct", AsyncMock(return_value=SENT),
        ) as direct:
            plain = run(tools.publish_decision("CASE-R", decision))
            direct.assert_not_awaited()
            assert plain["detail"]["message_id"] == "MSG-1"
            again = run(tools.publish_decision("CASE-R", decision, redeliver=True))
        direct.assert_awaited_once()
        assert again == SENT
