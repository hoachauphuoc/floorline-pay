"""
The settlement routes (settlement_routes.py): PayPal's webhook, the freezes and the payout state.

Pinned here: the webhook needs neither a session nor an API key and answers what the
receiver decides; anyone signed in reads the frozen parties; a governance admin signed in with
a password lifts a freeze with a reason and the person recorded is the signed-in one; a
reviewer and the one-click guest session cannot; and an operator asks PayPal for a payout's
state while a reviewer cannot.

settlement_routes is checked through the real app, so that, before the routes exist, the tests
fail instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
TRANSMISSION = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig-1",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-23T01:00:00Z",
}


def run(coro):
    return asyncio.run(coro)


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


def freezes():
    return importlib.import_module("vf_logistics.payment_freeze")


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


class TestTheSettlementRoutes(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "ADMIN_EMAILS": ADMIN, "OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER,
            "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": ADMIN,
        })
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def test_the_webhook_needs_no_session_and_no_api_key(self):
        with patch.dict(os.environ, {"PAYPAL_WEBHOOK_ID": "WEBHOOK-9"}), patch(
            "vf_logistics.paypal_client.verify_webhook_signature",
            AsyncMock(return_value={"status": "ok", "http_status": 200, "error": None,
                                    "body": {"verification_status": "SUCCESS"}}),
        ):
            r = self.client.post(
                "/api/v1/paypal/webhook", headers=TRANSMISSION,
                json={"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}},
            )
        assert r.status_code == 200
        assert r.get_json()["outcome"] == "recorded"

    def test_the_webhook_is_off_without_a_webhook_id(self):
        with patch.dict(os.environ, {"PAYPAL_WEBHOOK_ID": ""}):
            r = self.client.post("/api/v1/paypal/webhook", headers=TRANSMISSION, json={"id": "WH-1", "event_type": "X"})
        assert r.status_code == 503
        assert "PAYPAL_WEBHOOK_ID" in r.get_json()["reason"]

    def test_anyone_signed_in_reads_the_frozen_parties(self):
        name = carrier()
        run(freezes().freeze("carrier", name, "a dispute was opened", "event-1"))
        r = self.client.get("/api/v1/payments/freezes", headers=as_person(REVIEWER))
        assert r.status_code == 200
        assert name in [f["name"] for f in r.get_json()["frozen"]]

    def test_a_governance_admin_lifts_a_freeze_and_is_recorded_as_themself(self):
        name = carrier()
        run(freezes().freeze("carrier", name, "a dispute was opened", "event-1"))
        r = self.client.post(
            "/api/v1/payments/freezes/lift", headers=as_person(ADMIN),
            json={"kind": "carrier", "name": name, "reason": "settled with the carrier", "lifted_by": "someone-else@x.example"},
        )
        assert r.status_code == 200
        assert name not in [f["name"] for f in r.get_json()["frozen"]]
        assert run(freezes().frozen("carrier", name)) is None
        rows, _ = run(self.store.query_audit(action=freezes().LIFTED, limit=5))
        assert rows[0]["detail"]["lifted_by"] == ADMIN

    def test_a_reviewer_and_an_operator_cannot_lift_a_freeze(self):
        name = carrier()
        run(freezes().freeze("carrier", name, "x", "event-1"))
        for who in (REVIEWER, OPERATOR):
            r = self.client.post(
                "/api/v1/payments/freezes/lift", headers=as_person(who),
                json={"kind": "carrier", "name": name, "reason": "checked"},
            )
            assert r.status_code == 403
        assert run(freezes().frozen("carrier", name)) is not None

    def test_the_one_click_guest_session_cannot_lift_a_freeze(self):
        name = carrier()
        run(freezes().freeze("carrier", name, "x", "event-1"))
        r = self.client.post(
            "/api/v1/payments/freezes/lift", headers=as_person(ADMIN, amr="one_click"),
            json={"kind": "carrier", "name": name, "reason": "checked"},
        )
        assert r.status_code == 403
        assert run(freezes().frozen("carrier", name)) is not None

    def test_a_lift_without_a_reason_or_for_a_party_that_is_not_frozen_is_refused(self):
        name = carrier()
        run(freezes().freeze("carrier", name, "x", "event-1"))
        r = self.client.post(
            "/api/v1/payments/freezes/lift", headers=as_person(ADMIN),
            json={"kind": "carrier", "name": name, "reason": " "},
        )
        assert r.status_code == 400
        assert "reason is required" in r.get_json()["error"]
        r = self.client.post(
            "/api/v1/payments/freezes/lift", headers=as_person(ADMIN),
            json={"kind": "carrier", "name": carrier(), "reason": "checked"},
        )
        assert r.status_code == 409
        assert "not frozen" in r.get_json()["error"]

    def test_an_operator_asks_paypal_for_a_payout_state(self):
        ask = AsyncMock(return_value={"state": "confirmed", "status": "SUCCESS", "at": "2026-10-23T01:00:00+00:00"})
        with patch("vf_logistics.payout_tracking.refresh", ask):
            r = self.client.post("/api/v1/payments/cases/CASE-1/payout/refresh", headers=as_person(OPERATOR))
        assert r.status_code == 200
        assert r.get_json()["state"] == "confirmed"
        assert ask.await_args.args[0] == "CASE-1"

    def test_a_payout_refresh_that_cannot_be_answered_comes_back_with_its_status(self):
        r = self.client.post("/api/v1/payments/cases/CASE-NEVER-PAID/payout/refresh", headers=as_person(OPERATOR))
        assert r.status_code == 409
        assert "no settled payout" in r.get_json()["error"]

    def test_a_reviewer_cannot_ask_paypal_for_a_payout_state(self):
        r = self.client.post("/api/v1/payments/cases/CASE-1/payout/refresh", headers=as_person(REVIEWER))
        assert r.status_code == 403


class TestRetryingASettlementFromTheConsole(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "ADMIN_EMAILS": ADMIN, "OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER,
            "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": ADMIN,
        })
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def test_an_operator_signed_in_with_a_password_sends_a_failed_release_again(self):
        again = AsyncMock(return_value={"status": "redelivered", "case_id": "CASE-1"})
        with patch("vf_logistics.settlement_retry.retry", again):
            r = self.client.post(
                "/api/v1/payments/cases/CASE-1/decision/redeliver", headers=as_person(OPERATOR),
                json={"reason": "topped up the balance", "retried_by": "someone-else@x.example"},
            )
        assert r.status_code == 200
        assert r.get_json()["status"] == "redelivered"
        assert again.await_args.args[:3] == ("CASE-1", "topped up the balance", OPERATOR)

    def test_a_reviewer_and_the_one_click_guest_session_cannot_send_it_again(self):
        again = AsyncMock(return_value={"status": "redelivered"})
        with patch("vf_logistics.settlement_retry.retry", again):
            for headers in (as_person(REVIEWER), as_person(ADMIN, amr="one_click")):
                r = self.client.post(
                    "/api/v1/payments/cases/CASE-1/decision/redeliver", headers=headers, json={"reason": "checked"},
                )
                assert r.status_code == 403
        again.assert_not_awaited()

    def test_a_refusal_comes_back_with_its_status_and_reason(self):
        r = self.client.post(
            "/api/v1/payments/cases/CASE-NEVER-PAID/decision/redeliver", headers=as_person(OPERATOR), json={"reason": "checked"},
        )
        assert r.status_code == 409
        assert "only a settlement that ended failed" in r.get_json()["error"]
        r = self.client.post(
            "/api/v1/payments/cases/CASE-NEVER-PAID/decision/redeliver", headers=as_person(OPERATOR), json={"reason": " "},
        )
        assert r.status_code == 400
        assert "reason is required" in r.get_json()["error"]
