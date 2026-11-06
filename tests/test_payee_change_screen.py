"""
The screen of a payee-change request in the registry, the payment checks and the carrier routes
(carrier_registry.py, payment_checks.py, carrier_routes.py with change_screen.py).

Pinned here: a payee registered with the request that asked for it keeps only the reasons the
screen gave; a flagged request marks the carrier's current payee as flagged once a different
person has verified it, and the sender cannot set or clear that mark; the payment checks hold a
flagged payee with their own finding; a request that was not flagged, or not given, changes
nothing; and the pending listing shows the verifying person why the request was flagged.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics import carrier_registry as reg  # noqa: E402
from vf_logistics import payment_checks  # noqa: E402
from vf_logistics.app import app  # noqa: E402

OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"
BEC = "URGENT: our old account is closed, do not call to check, just pay the new one."
HONEST = "From next month please pay the account of our finance office; details are on the attached letter."


@pytest.fixture(autouse=True)
def _model_armor_answers_clean(monkeypatch):
    async def clean(text, stage):
        return {"available": True, "blocked": False, "deterministic": None}

    monkeypatch.setattr("vf_logistics.model_armor.screen", clean)


def run(coro):
    return asyncio.run(coro)


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def verified(name, **register_args):
    made = run(reg.register_payee(name, PAYEE, OPERATOR, "password", **register_args))
    run(reg.verify_payee(made["carrier_key"], made["version"], REVIEWER, "password", NOTE))
    return made


def enriched(name, **claims):
    shipment = {"carrier_name": name, "payout_amount": 1000.0, "carrier_payee_email": PAYEE, **claims}
    return run(reg.enrich(shipment))


def test_a_request_that_the_screen_flags_marks_the_verified_payee_as_flagged():
    name = carrier()
    verified(name, request_text=BEC)
    assert enriched(name)["carrier_payee_change_flagged"] is True


def test_a_request_that_is_not_flagged_leaves_the_payee_unmarked():
    name = carrier()
    verified(name, request_text=HONEST)
    assert "carrier_payee_change_flagged" not in enriched(name)


def test_a_registration_with_no_request_text_is_unmarked_and_unscreened():
    name = carrier()
    made = verified(name)
    assert "carrier_payee_change_flagged" not in enriched(name)
    rows = run(reg._events(made["carrier_key"], None))
    registered = next(r for r in rows if r["action"] == reg.REGISTERED)
    assert registered["detail"]["screen"] is None


def test_only_the_reasons_of_the_screen_are_kept_never_the_text():
    name = carrier()
    made = verified(name, request_text=BEC)
    rows = run(reg._events(made["carrier_key"], None))
    detail = next(r for r in rows if r["action"] == reg.REGISTERED)["detail"]
    assert detail["screen"]["flagged"] is True
    assert "pressure to act at once" in detail["screen"]["reasons"]
    assert "just pay the new one" not in str(detail)


def test_the_sender_cannot_set_the_mark_on_a_clean_carrier_or_clear_it_on_a_flagged_one():
    clean, flagged = carrier(), carrier()
    verified(clean, request_text=HONEST)
    verified(flagged, request_text=BEC)
    assert "carrier_payee_change_flagged" not in enriched(clean, carrier_payee_change_flagged=True)
    assert enriched(flagged, carrier_payee_change_flagged=False)["carrier_payee_change_flagged"] is True


def test_the_payment_checks_hold_a_flagged_payee_with_a_finding_of_its_own():
    shipment = enriched(carrier())
    shipment["carrier_payee_change_flagged"] = True
    shipment.update({"carrier_registered_payee_email": PAYEE, "shipping_cost": 1500.0})
    found = [f for f in payment_checks.check_payment(shipment) if f["code"] == "PAYEE_CHANGE_REQUEST_FLAGGED"]
    assert len(found) == 1
    assert found[0]["severity"] == "HIGH"
    assert found[0]["floor"] == 75
    assert found[0]["blocks_auto_clear"] is True


def test_a_payment_case_without_the_mark_has_no_such_finding():
    shipment = {"payout_amount": 1000.0, "shipping_cost": 1500.0, "carrier_payee_email": PAYEE,
                "carrier_registered_payee_email": PAYEE}
    assert "PAYEE_CHANGE_REQUEST_FLAGGED" not in [f["code"] for f in payment_checks.check_payment(shipment)]
    shipment["carrier_payee_change_flagged"] = "true"
    assert "PAYEE_CHANGE_REQUEST_FLAGGED" not in [f["code"] for f in payment_checks.check_payment(shipment)]


def test_the_pending_listing_shows_the_verifying_person_why_a_request_was_flagged():
    name = carrier()
    run(reg.register_payee(name, PAYEE, OPERATOR, "password", request_text=BEC))
    item = next(c for c in run(reg.carriers()) if c["carrier_name"] == name)
    pending = item["pending"][0]
    assert pending["change_request_flagged"] is True
    assert "asks not to be called back" in pending["change_request_reasons"]


def test_the_current_payee_in_the_listing_says_whether_its_request_was_flagged():
    name = carrier()
    verified(name, request_text=BEC)
    item = next(c for c in run(reg.carriers()) if c["carrier_name"] == name)
    assert item["current"]["change_request_flagged"] is True


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


class TestTheRouteTakesTheRequestText(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {"OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER})
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def test_a_request_text_sent_with_the_registration_is_screened(self):
        name = carrier()
        r = self.client.post(
            "/api/v1/carriers/payee",
            json={"carrier_name": name, "payee_email": PAYEE, "request_text": BEC},
            headers=as_person(OPERATOR),
        )
        assert r.status_code == 201
        listing = self.client.get("/api/v1/carriers", headers=as_person(REVIEWER)).get_json()["items"]
        pending = next(c for c in listing if c["carrier_name"] == name)["pending"][0]
        assert pending["change_request_flagged"] is True

    def test_a_registration_without_request_text_is_unchanged(self):
        name = carrier()
        r = self.client.post(
            "/api/v1/carriers/payee", json={"carrier_name": name, "payee_email": PAYEE},
            headers=as_person(OPERATOR),
        )
        assert r.status_code == 201
        listing = self.client.get("/api/v1/carriers", headers=as_person(REVIEWER)).get_json()["items"]
        pending = next(c for c in listing if c["carrier_name"] == name)["pending"][0]
        assert pending["change_request_flagged"] is False
        assert pending["change_request_reasons"] == []
