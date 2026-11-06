"""
The payment ledger (payment_status.py): GET /api/v1/payments/ledger.

Pinned here: one line per case, from that case's newest settle_payment audit row; a count
per state; other audit actions are not payments; a status the module does not know is
"unknown", never a success; and no payee address is ever in the reply.

payment_status.payment_ledger is read inside each test so that, before it exists, every
test fails instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
PAYEE = "payments@mekong-haulage.example"


def run(coro):
    return asyncio.run(coro)


class TestThePaymentLedger(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def _row(self, case_id, status, action="settle_payment", **detail):
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": case_id, "action": action,
            "status": status, "detail": {"reason": f"reason for {status}", **detail},
            "at": "2026-11-02T10:00:00+00:00",
        }))

    def _ledger(self):
        r = self.client.get("/api/v1/payments/ledger", headers=KEYED)
        assert r.status_code == 200
        return r.get_json()

    def test_an_empty_ledger_has_no_items_and_no_counts(self):
        body = self._ledger()
        assert body["items"] == []
        assert body["counts"] == {}

    def test_each_case_appears_once_with_its_state(self):
        self._row("CASE-A", "settled", capture={"paypal_id": "CAP-1"}, payout={"paypal_id": "PAYOUT-1"})
        self._row("CASE-B", "voided", void={"paypal_id": "VOID-1"})
        body = self._ledger()
        by_case = {item["case_id"]: item for item in body["items"]}
        assert set(by_case) == {"CASE-A", "CASE-B"}
        assert by_case["CASE-A"]["state"] == "paid"
        assert by_case["CASE-A"]["capture"]["paypal_id"] == "CAP-1"
        assert by_case["CASE-A"]["payout"]["paypal_id"] == "PAYOUT-1"
        assert by_case["CASE-B"]["state"] == "voided"
        assert by_case["CASE-B"]["reason"] == "reason for voided"
        assert body["counts"] == {"paid": 1, "voided": 1}

    def test_a_case_retried_and_then_settled_appears_once_as_paid(self):
        self._row("CASE-A", "retry")
        self._row("CASE-A", "settled", capture={"paypal_id": "CAP-1"})
        body = self._ledger()
        assert [item["state"] for item in body["items"]] == ["paid"]
        assert body["counts"] == {"paid": 1}

    def test_counts_are_per_state(self):
        for case_id, status in [("CASE-1", "held"), ("CASE-2", "held"), ("CASE-3", "failed")]:
            self._row(case_id, status)
        assert self._ledger()["counts"] == {"held": 2, "failed": 1}

    def test_other_audit_actions_are_not_payments(self):
        self._row("CASE-A", "done", action="publish_decision")
        self._row("CASE-B", "settled", action="release_shipment")
        assert self._ledger()["items"] == []

    def test_a_status_the_module_does_not_know_is_never_shown_as_a_success(self):
        self._row("CASE-A", "refunded")
        body = self._ledger()
        assert body["items"][0]["state"] == "unknown"
        assert body["counts"] == {"unknown": 1}

    def test_only_the_named_fields_of_a_row_are_passed_on(self):
        self._row("CASE-A", "settled", carrier_payee_email=PAYEE, capture={"paypal_id": "CAP-1"})
        text = json.dumps(self._ledger())
        assert "CASE-A" in text
        assert "CAP-1" in text
        assert PAYEE not in text
