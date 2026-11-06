"""
The payment ledger lists a decision that never went out (review F03, 2026-11-05).

It read settle_payment rows only, so a saved decision the topic never took appeared on its case and nowhere else. Pinned here: such
a release or block is listed with a state of its own; a decision whose publish went out and a case that settled are not.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

SHIPMENT = {"shipment_id": "SHP-U", "paypal_authorization_id": "AUTH-U1"}


def run(coro):
    return asyncio.run(coro)


def ledger():
    return run(importlib.import_module("vf_logistics.payment_status").payment_ledger())


class TestTheLedgerShowsUnsentDecisions(Base):
    def decided(self, case_id="CASE-U", state="RELEASED_BY_HUMAN"):
        run(self.store.put_case({"case_id": case_id, "state": state, "shipment": dict(SHIPMENT)}))

    def test_a_saved_release_that_never_went_out_is_listed(self):
        self.decided()
        body = ledger()
        assert body["counts"] == {"decision_not_sent": 1}
        assert body["items"][0]["case_id"] == "CASE-U"
        assert body["items"][0]["state"] == "decision_not_sent"
        assert "never reached the settler" in body["items"][0]["reason"]

    def test_a_saved_block_that_never_went_out_is_listed(self):
        self.decided(state="BLOCKED_BY_HUMAN")
        assert ledger()["counts"] == {"decision_not_sent": 1}

    def test_a_decision_whose_publish_went_out_is_not_listed(self):
        self.decided()
        run(self.store.add_audit({
            "case_id": "CASE-U", "action": "publish_decision", "status": "done",
            "detail": {"idempotency_key": "human-CASE-U-release", "message_id": "MSG-1"}, "at": "2026-11-05T04:00:00+00:00",
        }))
        assert ledger()["items"] == []

    def test_a_case_that_settled_appears_once_with_its_outcome(self):
        self.decided()
        run(self.store.add_audit({
            "case_id": "CASE-U", "action": "settle_payment", "status": "settled", "detail": {"reason": "captured and paid"},
            "at": "2026-11-05T04:00:00+00:00",
        }))
        body = ledger()
        assert [item["state"] for item in body["items"]] == ["paid"]
