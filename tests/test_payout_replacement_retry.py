"""
A person asks for the new payout, and PayPal's events follow it (re-check of review F08, 2026-11-05).

Pinned here: a retry of a settlement that names a dead batch stores who asked, why and which batch, before anything is sent; a retry
of any other failed settlement stores no such request; PayPal's item events on <key>-payout-2 reach the same case.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_paypal_webhook import hook, settled  # noqa: E402, F401

KEY = "exec-evapr-9"
SENT = {"status": "done", "detail": {"message_id": "MSG-2"}}
ACTOR = "ops@forwarder.example"


def run(coro):
    return asyncio.run(coro)


def retry_mod():
    return importlib.import_module("vf_logistics.settlement_retry")


class TestTheRequestForANewPayout(Base):
    def failed(self, **detail):
        run(self.store.add_audit({
            "case_id": "CASE-D", "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": KEY, "message_id": "MSG-1", "approver": "first@forwarder.example"}, "at": "2026-11-05T04:00:00+00:00",
        }))
        run(self.store.add_audit({
            "case_id": "CASE-D", "action": "settle_payment", "status": "failed",
            "detail": {"idempotency_key": KEY, "outcome": "RELEASED_BY_HUMAN", **detail}, "at": "2026-11-05T04:01:00+00:00",
        }))

    def requests(self):
        rows, _ = run(self.store.query_audit(case_id=f"PAYNEW-{KEY}", limit=5))
        return rows

    def test_the_person_the_reason_and_the_batch_are_stored(self):
        self.failed(dead_batch="BATCH-D")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)):
            run(retry_mod().retry("CASE-D", " PayPal denied it ", ACTOR))
        detail = self.requests()[0]["detail"]
        assert detail == {"dead_batch_id": "BATCH-D", "reason": "PayPal denied it", "approved_by": ACTOR}

    def test_the_request_is_stored_even_when_the_send_fails(self):
        self.failed(dead_batch="BATCH-D")
        with patch("vf_logistics.tools.publish_decision", AsyncMock(side_effect=RuntimeError("topic down"))):
            try:
                run(retry_mod().retry("CASE-D", "PayPal denied it", ACTOR))
            except retry_mod().RetryError as exc:
                assert exc.status == 503
        assert len(self.requests()) == 1

    def test_a_failed_settlement_with_no_dead_batch_stores_no_request(self):
        self.failed()
        with patch("vf_logistics.tools.publish_decision", AsyncMock(return_value=SENT)):
            run(retry_mod().retry("CASE-D", "topped up the balance", ACTOR))
        assert self.requests() == []


def test_an_item_event_on_the_new_batch_reaches_the_case(hook):  # noqa: F811
    module, _ = hook
    case = settled()
    reply = run(module._payout_item("WH-N", "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {
        "sender_batch_id": f"{case['tag']}-payout-2", "transaction_status": "SUCCESS",
    }, None))
    assert reply["matched"] is True
    assert reply["case_id"] == case["case_id"]
