"""
What the payment layers say about a case (payment_status.payment_risk, GET /api/v1/payments/cases/<id>/risk)
and the payout state in the ledger (payout_tracking.confirmations, payment_status.payment_ledger).

Pinned here: the ledger carries the payout state of each case, read in one pass, a failed status beating a
confirmed one and a case with no status being unconfirmed; the risk of a payment case lists the findings of the
checks and of the AI answers stored on it without quoting a payee address, its credit standing and cap, and who
has approved a release that needs two; a case that moves no money says so; an unknown case is a 404; and the
route is read-only.

The new functions are looked up inside each test so that, before they exist, every test fails instead of the
file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics import store as store_mod  # noqa: E402
from vf_logistics.store import utcnow  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def mod(name):
    return importlib.import_module(f"vf_logistics.{name}")


def payment_case(payout=3000.0, **extra):
    tag = uuid.uuid4().hex[:8]
    case_id, carrier = f"CASE-{tag}", f"Mekong Haulage {tag} Co"
    shipment = {
        "shipment_id": case_id, "currency": "USD", "shipping_cost": 3500.0, "payout_amount": payout,
        "carrier_name": carrier, "carrier_payee_email": "payments@mekong.example",
        "paypal_authorization_id": "0VF52814937998046",
    }
    case = {"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": shipment, **extra}
    run(store_mod.get_store().put_case(case))
    return case_id, carrier


def settle_row(case_id, status="settled"):
    run(store_mod.get_store().add_audit({
        "audit_id": f"settle-{case_id}-{status}", "case_id": case_id, "action": "settle_payment", "status": status,
        "detail": {"reason": "captured and paid", "idempotency_key": case_id, "carrier_key": "x"}, "at": utcnow(),
    }))


class TestThePayoutStateInTheLedger:
    def test_each_case_in_the_ledger_carries_the_state_of_its_payout(self):
        confirmed, failed, unknown = payment_case()[0], payment_case()[0], payment_case()[0]
        for case_id in (confirmed, failed, unknown):
            settle_row(case_id)
        pt = mod("payout_tracking")
        run(pt.apply(confirmed, f"e-{confirmed}", "SUCCESS", "Some Carrier Co"))
        run(pt.apply(failed, f"e-{failed}-1", "SUCCESS", "Some Carrier Co"))
        run(pt.apply(failed, f"e-{failed}-2", "RETURNED", "Some Carrier Co"))
        items = {i["case_id"]: i for i in run(mod("payment_status").payment_ledger(limit=500))["items"]}
        assert items[confirmed]["payout_status"]["state"] == "confirmed"
        assert items[failed]["payout_status"]["state"] == "failed"
        assert items[unknown]["payout_status"] == {"state": "unconfirmed", "status": None, "at": None}

    def test_confirmations_reads_the_cases_named_and_a_case_without_a_status_is_unconfirmed(self):
        case_id, _ = payment_case()
        run(mod("payout_tracking").apply(case_id, f"e-{case_id}", "PENDING", "Some Carrier Co"))
        out = run(mod("payout_tracking").confirmations([case_id, "CASE-NONE"]))
        assert out[case_id]["state"] == "pending"
        assert out["CASE-NONE"]["state"] == "unconfirmed"


class TestThePaymentRisk:
    def test_an_unknown_case_has_no_risk(self):
        assert run(mod("payment_status").payment_risk("CASE-NOBODY")) is None

    def test_a_case_that_moves_no_money_says_so(self):
        case_id = f"CASE-{uuid.uuid4().hex[:8]}"
        run(store_mod.get_store().put_case({"case_id": case_id, "state": "HELD_FOR_REVIEW", "shipment": {"shipment_id": case_id}}))
        assert run(mod("payment_status").payment_risk(case_id)) == {"case_id": case_id, "payment_case": False}

    def test_a_payment_case_lists_the_findings_the_ai_answers_the_standing_and_the_approvals(self):
        ai = {
            "analyst": {"verdict": "elevated", "confidence": 0.9, "reasons": ["payee changed days before"], "suggested_tier": "D"},
            "identity": {"status": "not_found", "reason": "no search result names the carrier", "urls": []},
        }
        approvals = [{
            "approval_id": "evapr-1", "action": "release", "status": "VALID", "approver": "first@forwarder.example",
            "second_approval": {"approver": "second@forwarder.example", "note": "checked"},
        }]
        case_id, _ = payment_case(3000.0, payment_ai=ai, verification={"approvals": approvals})
        out = run(mod("payment_status").payment_risk(case_id))
        codes = [f["code"] for f in out["findings"]]
        assert "PAYMENT_AI_ELEVATED" in codes
        assert "CARRIER_NOT_FOUND_ONLINE" in codes
        assert out["analyst"]["verdict"] == "elevated"
        assert out["identity"]["status"] == "not_found"
        assert out["credit"]["baseline"] == "C"
        assert out["credit"]["suggested"] == "D"
        assert out["credit"]["tier"] == "D"
        assert out["credit"]["cap_usd"] == "500"
        assert out["dual_approval"] == {
            "needed": True, "threshold_usd": "2500", "first_approver": "first@forwarder.example",
            "second_approver": "second@forwarder.example",
        }

    def test_no_finding_quotes_a_payee_address(self):
        case_id, _ = payment_case()
        out = run(mod("payment_status").payment_risk(case_id))
        assert "mekong.example" not in str(out)
        assert out["credit"]["tier"] == "C"
        assert out["credit"]["cap_usd"] == "2000"

    def test_a_small_payout_does_not_need_a_second_approver(self):
        case_id, _ = payment_case(1000.0)
        out = run(mod("payment_status").payment_risk(case_id))
        assert out["dual_approval"]["needed"] is False
        assert out["dual_approval"]["first_approver"] is None
        assert out["analyst"] is None


class TestTheRoute(Base):
    def test_anyone_signed_in_reads_the_risk_of_a_case(self):
        from vf_logistics.app import app

        case_id, _ = payment_case()
        r = app.test_client().get(f"/api/v1/payments/cases/{case_id}/risk", headers={"X-VF-API-Key": TEST_KEY})
        assert r.status_code == 200
        assert r.get_json()["payment_case"] is True

    def test_an_unknown_case_is_a_404_and_the_route_only_reads(self):
        from vf_logistics.app import app

        client = app.test_client()
        headers = {"X-VF-API-Key": TEST_KEY}
        assert client.get("/api/v1/payments/cases/CASE-NOBODY/risk", headers=headers).status_code == 404
        assert client.post("/api/v1/payments/cases/CASE-1/risk", headers=headers, json={}).status_code == 405
