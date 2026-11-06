"""
The carrier payee registry routes (carrier_routes.py).

Pinned here: an operator signed in with a password registers a payee and a different person,
reviewer or above, verifies it with a callback note; the one-click guest session and a caller
with no person behind them can do neither; the person recorded is the signed-in one, never a
name in the request; the registry's refusals come back with its status and message; and the
listing is for a reviewer.

carrier_routes is checked through the real app, so that, before the routes exist, the tests
fail instead of the file failing to collect.
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
NEW_PAYEE = "accounts@mekong-haulage.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


KEY_ONLY = {"X-VF-API-Key": TEST_KEY}


class TestTheCarrierRoutes(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER,
            "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": OPERATOR,
        })
        self.people.start()
        self.client = app.test_client()
        self.name = f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"
        self.key = "".join(c for c in self.name.lower() if c.isalnum())

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def _register(self, payee=PAYEE, headers=None, **extra):
        return self.client.post(
            "/api/v1/carriers/payee",
            json={"carrier_name": self.name, "payee_email": payee, **extra},
            headers=headers or as_person(OPERATOR),
        )

    def _verify(self, version=1, headers=None, note=NOTE, key=None):
        return self.client.post(
            f"/api/v1/carriers/{key or self.key}/payee/{version}/verify",
            json={"callback_note": note}, headers=headers or as_person(REVIEWER),
        )

    def _list(self, headers=None):
        return self.client.get("/api/v1/carriers", headers=headers or as_person(REVIEWER))

    def test_an_operator_registers_a_payee_that_is_then_pending(self):
        r = self._register()
        assert r.status_code == 201
        assert r.get_json() == {"carrier_key": self.key, "version": 1, "status": "pending_verification"}
        item = next(i for i in self._list().get_json()["items"] if i["carrier_key"] == self.key)
        assert item["current"] is None
        assert [p["payee_email"] for p in item["pending"]] == [PAYEE]

    def test_a_different_person_verifies_it_and_it_becomes_the_payee(self):
        self._register()
        r = self._verify()
        assert r.status_code == 200
        assert r.get_json() == {"carrier_key": self.key, "version": 1, "status": "verified"}
        item = next(i for i in self._list().get_json()["items"] if i["carrier_key"] == self.key)
        assert item["current"]["payee_email"] == PAYEE
        assert item["current"]["verified_by"] == REVIEWER
        assert item["pending"] == []

    def test_the_person_recorded_is_the_signed_in_one_not_a_name_in_the_request(self):
        self._register(registered_by="someone-else@forwarder.example", actor="someone-else@forwarder.example")
        item = next(i for i in self._list().get_json()["items"] if i["carrier_key"] == self.key)
        assert item["pending"][0]["registered_by"] == OPERATOR

    def test_the_registrant_cannot_verify_their_own_payee(self):
        self._register()
        r = self._verify(headers=as_person(OPERATOR))
        assert r.status_code == 403
        assert "different person" in r.get_json()["error"]

    def test_a_verification_without_a_callback_note_is_refused(self):
        self._register()
        r = self._verify(note="")
        assert r.status_code == 400
        assert "callback note" in r.get_json()["error"]

    def test_a_reviewer_cannot_register_a_payee(self):
        r = self._register(headers=as_person(REVIEWER))
        assert r.status_code == 403

    @pytest.mark.parametrize(
        "headers", [KEY_ONLY, as_person(OPERATOR, "one_click")], ids=["key only", "one-click guest"],
    )
    def test_nobody_without_a_password_sign_in_can_register_or_verify(self, headers):
        assert self._register(headers=headers).status_code in (401, 403)
        self._register()
        assert self._verify(headers=headers).status_code in (401, 403)
        item = next(i for i in self._list().get_json()["items"] if i["carrier_key"] == self.key)
        assert item["current"] is None

    def test_the_listing_is_for_a_reviewer(self):
        assert self._list(headers=as_person("nobody@forwarder.example")).status_code == 403
        assert self._list(headers={"X-Anything": "1"}).status_code in (401, 403)
        assert self._list().status_code == 200

    def test_the_registry_refusals_come_back_with_their_status(self):
        assert self._register(payee="nope").status_code == 400
        self._register()
        assert self._register(payee=PAYEE).status_code == 201
        assert self._verify(version=9).status_code == 404
        assert self._verify(version=1).status_code == 200
        assert self._verify(version=1, headers=as_person("third@forwarder.example")).status_code in (403, 409)

    def test_a_version_that_is_not_a_number_is_refused(self):
        r = self._verify(version="one")
        assert r.status_code == 400
        assert "not a version number" in r.get_json()["error"]

    def test_a_carrier_key_with_unexpected_characters_is_refused(self):
        r = self._verify(key="BAD-KEY")
        assert r.status_code == 400
        assert "not a carrier key" in r.get_json()["error"]

    def test_a_changed_payee_waits_while_the_old_one_stays_in_force(self):
        self._register()
        self._verify(version=1)
        assert self._register(payee=NEW_PAYEE).get_json()["version"] == 2
        item = next(i for i in self._list().get_json()["items"] if i["carrier_key"] == self.key)
        assert item["current"]["payee_email"] == PAYEE
        assert [p["payee_email"] for p in item["pending"]] == [NEW_PAYEE]
