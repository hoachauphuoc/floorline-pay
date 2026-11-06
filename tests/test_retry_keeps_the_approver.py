"""
A retried release is the first decision, sent again (review F03, 2026-11-05).

Pinned here: the approver and the approval id come from the first publish of the key, because the settler looks the approval up
by id and a large release without it is held; the person who retried is recorded apart, as sent_by; a first publish that names
nobody sends nothing.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

KEY = "exec-evapr-1"
SENT = {"status": "done", "detail": {"message_id": "MSG-2"}}
ACTOR = "ops@forwarder.example"


def run(coro):
    return asyncio.run(coro)


def retry_mod():
    return importlib.import_module("vf_logistics.settlement_retry")


class TestTheRetryKeepsTheDecision(Base):
    def failed(self, **published):
        run(self.store.add_audit({
            "case_id": "CASE-K", "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": KEY, "message_id": "MSG-1", **published}, "at": "2026-11-05T04:00:00+00:00",
        }))
        run(self.store.add_audit({
            "case_id": "CASE-K", "action": "settle_payment", "status": "failed",
            "detail": {"idempotency_key": KEY, "outcome": "RELEASED_BY_HUMAN"}, "at": "2026-11-05T04:01:00+00:00",
        }))

    def sent(self):
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            run(retry_mod().retry("CASE-K", "topped up the balance", ACTOR))
        return publish.await_args.args[1]

    def test_the_approver_of_the_first_publish_stays_and_the_sender_is_apart(self):
        self.failed(approver="first@forwarder.example", approval_id="evapr-1")
        decision = self.sent()
        assert decision["approver"] == "first@forwarder.example"
        assert decision["sent_by"] == ACTOR

    def test_the_approval_id_goes_with_the_retry(self):
        self.failed(approver="first@forwarder.example", approval_id="evapr-1")
        assert self.sent()["approval_id"] == "evapr-1"

    def test_a_decision_that_had_no_approval_id_gains_none(self):
        self.failed(approver="first@forwarder.example")
        assert "approval_id" not in self.sent()

    def test_a_first_publish_that_names_nobody_sends_nothing(self):
        self.failed()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)) as publish:
            try:
                run(retry_mod().retry("CASE-K", "topped up the balance", ACTOR))
            except retry_mod().RetryError as exc:
                assert exc.status == 409
                assert "does not name who approved" in exc.message
            else:
                raise AssertionError("expected a RetryError")
        publish.assert_not_awaited()
