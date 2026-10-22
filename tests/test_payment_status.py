"""
The payment state of a case (payment_status.py): GET /api/v1/payments/cases/<case_id>.

Pinned here: the state is read from the newest settle_payment audit row of that case and
no other; a payment case with no row is waiting for a person (or has no authorization);
a status the module does not know is "unknown", never a success; and no payee address is
ever in the reply.

payment_status is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
PAYEE = "payments@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"
SHIPMENT = {
    "shipment_id": "SHP-PAY-1",
    "shipping_cost": 1300.0,
    "payout_amount": 1250.0,
    "carrier_payee_email": PAYEE,
    "carrier_registered_payee_email": PAYEE,
    "carrier_payee_changed_at": "2026-08-01T08:00:00+07:00",
    "paypal_authorization_id": "0VF52814937998046",
}
STEP = {"status": "ok", "http_status": 201, "paypal_id": "CAP-1", "error": None}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def status_module():
    return importlib.import_module("vf_logistics.payment_status")


class TestThePaymentState(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def _case(self, shipment=None, state="RELEASED_BY_HUMAN"):
        case_id = f"CASE-{uuid.uuid4().hex[:10]}"
        run(self.store.put_case({"case_id": case_id, "state": state,
                                 "shipment": dict(SHIPMENT if shipment is None else shipment)}))
        return case_id

    def _settle_row(self, case_id, status, **detail):
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": case_id, "action": "settle_payment",
            "status": status, "detail": {"reason": f"reason for {status}", **detail},
            "at": "2026-11-02T10:00:00+00:00",
        }))

    def _get(self, case_id):
        return self.client.get(f"/api/v1/payments/cases/{case_id}", headers=KEYED)

    def test_an_unknown_case_is_a_404(self, status_module):
        r = self._get("CASE-DOES-NOT-EXIST")
        assert r.status_code == 404
        assert r.get_json()["error"] == "case not found"

    def test_a_case_that_moves_no_money_has_no_payment_state(self, status_module):
        r = self._get(self._case(shipment={"shipment_id": "SHP-2", "shipping_cost": 10.0}))
        assert r.status_code == 200
        assert r.get_json()["state"] == "none"
        assert r.get_json()["payment_case"] is False

    def test_a_payment_case_with_no_decision_yet_is_awaiting_a_person(self, status_module):
        body = self._get(self._case()).get_json()
        assert body["state"] == "awaiting_decision"
        assert body["payment_case"] is True
        assert body["findings"] == []

    def test_the_findings_that_would_hold_the_payout_are_listed_by_code(self, status_module):
        body = self._get(self._case(shipment={**SHIPMENT, "carrier_payee_email": ATTACKER})).get_json()
        assert body["state"] == "awaiting_decision"
        assert "PAYEE_MISMATCH" in body["findings"]

    def test_a_payment_case_without_an_authorization_says_so(self, status_module):
        body = self._get(self._case(shipment={**SHIPMENT, "paypal_authorization_id": None})).get_json()
        assert body["state"] == "no_authorization"
        assert "no PayPal authorization" in body["reason"]

    @pytest.mark.parametrize("status,state", [
        ("settled", "paid"), ("voided", "voided"), ("held", "held"),
        ("retry", "pending"), ("failed", "failed"),
    ])
    def test_each_settler_status_maps_to_one_state(self, status_module, status, state):
        case_id = self._case()
        self._settle_row(case_id, status)
        body = self._get(case_id).get_json()
        assert body["state"] == state
        assert body["reason"] == f"reason for {status}"

    def test_a_status_the_module_does_not_know_is_never_shown_as_a_success(self, status_module):
        case_id = self._case()
        self._settle_row(case_id, "refunded")
        assert self._get(case_id).get_json()["state"] == "unknown"

    def test_the_newest_row_wins(self, status_module):
        case_id = self._case()
        self._settle_row(case_id, "retry")
        self._settle_row(case_id, "settled", capture=STEP, payout={**STEP, "paypal_id": "PAYOUT-1"})
        body = self._get(case_id).get_json()
        assert body["state"] == "paid"
        assert body["capture"]["paypal_id"] == "CAP-1"
        assert body["payout"]["paypal_id"] == "PAYOUT-1"

    def test_another_cases_rows_are_ignored(self, status_module):
        paid = self._case()
        other = self._case()
        self._settle_row(paid, "settled", capture=STEP)
        assert self._get(other).get_json()["state"] == "awaiting_decision"

    def test_a_voided_case_reports_the_void_step(self, status_module):
        case_id = self._case(state="BLOCKED_BY_HUMAN")
        self._settle_row(case_id, "voided", void={**STEP, "http_status": 204})
        body = self._get(case_id).get_json()
        assert body["state"] == "voided"
        assert body["void"]["http_status"] == 204

    def test_no_payee_address_is_ever_in_the_reply(self, status_module):
        case_id = self._case(shipment={**SHIPMENT, "carrier_payee_email": ATTACKER})
        self._settle_row(case_id, "held")
        text = json.dumps(self._get(case_id).get_json())
        assert ATTACKER not in text
        assert PAYEE not in text
