"""
The carrier payee registry (carrier_registry.py): the only source of who a carrier may be paid.

Pinned here: a payee is pending until a person other than its registrant verifies it with a
callback note, and the old payee stays in force until then; a changed payee carries the time
it was verified, a first payee carries none; enrich() replaces what a shipment event claims
about its payee on file and the date it changed with what the registry says, on a payment
shipment only, at intake and on the case page as well as in the settler; and a claim the
registry does not back is a finding, not a pass.

carrier_registry is imported inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
NEW_PAYEE = "accounts@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"


def run(coro):
    return asyncio.run(coro)


def registry():
    return importlib.import_module("vf_logistics.carrier_registry")


def checks():
    return importlib.import_module("vf_logistics.payment_checks")


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def register_and_verify(name, payee, registrant=OPERATOR, verifier=REVIEWER):
    reg = registry()
    made = run(reg.register_payee(name, payee, registrant, "password"))
    run(reg.verify_payee(made["carrier_key"], made["version"], verifier, "password", NOTE))
    return made


def paid(name, **extra):
    return {
        "shipment_id": "SHP-REG-1", "carrier_name": name, "shipping_cost": 1300.0,
        "payout_amount": 1250.0, "paypal_authorization_id": "0VF52814937998046", **extra,
    }


class TestTheRegistry(Base):
    def test_a_carrier_is_known_by_its_name_without_punctuation_or_case(self):
        reg = registry()
        assert reg.carrier_key("Mekong Haulage Co.") == "mekonghaulageco"
        assert reg.carrier_key("  MEKONG-haulage, co ") == "mekonghaulageco"
        assert reg.carrier_key("") == ""
        assert reg.carrier_key(None) == ""

    def test_a_registered_payee_is_pending_and_not_yet_the_carriers_payee(self):
        reg = registry()
        name = carrier()
        made = run(reg.register_payee(name, PAYEE, OPERATOR, "password"))
        assert made == {"carrier_key": reg.carrier_key(name), "version": 1, "status": "pending_verification"}
        assert run(reg.current(name)) is None

    def test_a_different_person_verifying_makes_it_the_payee(self):
        reg = registry()
        name = carrier()
        made = register_and_verify(name, PAYEE.upper())
        live = run(reg.current(name))
        assert made["version"] == 1
        assert live["payee_email"] == PAYEE
        assert live["registered_by"] == OPERATOR
        assert live["verified_by"] == REVIEWER
        assert live["verified_at"]

    def test_the_first_payee_carries_no_change_date(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        assert run(reg.current(name))["changed_at"] is None

    @pytest.mark.parametrize("verifier", [OPERATOR, OPERATOR.upper()])
    def test_the_person_who_registered_a_payee_cannot_verify_it(self, verifier):
        reg = registry()
        name = carrier()
        made = run(reg.register_payee(name, PAYEE, OPERATOR, "password"))
        with pytest.raises(reg.RegistryError) as raised:
            run(reg.verify_payee(made["carrier_key"], 1, verifier, "password", NOTE))
        assert raised.value.status == 403
        assert "different person" in raised.value.message
        assert run(reg.current(name)) is None

    @pytest.mark.parametrize("note", ["", "   ", None])
    def test_a_verification_needs_a_callback_note(self, note):
        reg = registry()
        made = run(reg.register_payee(carrier(), PAYEE, OPERATOR, "password"))
        with pytest.raises(reg.RegistryError) as raised:
            run(reg.verify_payee(made["carrier_key"], 1, REVIEWER, "password", note))
        assert raised.value.status == 400
        assert "callback note" in raised.value.message

    def test_a_verification_needs_a_person_behind_it(self):
        reg = registry()
        made = run(reg.register_payee(carrier(), PAYEE, OPERATOR, "password"))
        with pytest.raises(reg.RegistryError) as raised:
            run(reg.verify_payee(made["carrier_key"], 1, "", None, NOTE))
        assert raised.value.status == 403
        assert "against a person" in raised.value.message

    def test_a_registration_that_does_not_exist_or_is_already_verified_is_refused(self):
        reg = registry()
        name = carrier()
        made = register_and_verify(name, PAYEE)
        with pytest.raises(reg.RegistryError) as missing:
            run(reg.verify_payee(made["carrier_key"], 7, REVIEWER, "password", NOTE))
        assert missing.value.status == 404
        assert "no such registration" in missing.value.message
        with pytest.raises(reg.RegistryError) as again:
            run(reg.verify_payee(made["carrier_key"], 1, "third@forwarder.example", "password", NOTE))
        assert again.value.status == 409
        assert "already verified" in again.value.message

    @pytest.mark.parametrize("name,payee,actor,status,phrase", [
        ("", PAYEE, OPERATOR, 400, "carrier name"),
        ("Mekong", "not-an-email", OPERATOR, 400, "email address"),
        ("Mekong", "a b@c.example", OPERATOR, 400, "email address"),
        ("Mekong", PAYEE, "", 403, "against a person"),
    ])
    def test_a_registration_needs_a_carrier_a_real_address_and_a_person(self, name, payee, actor, status, phrase):
        reg = registry()
        with pytest.raises(reg.RegistryError) as raised:
            run(reg.register_payee(name, payee, actor, "password"))
        assert raised.value.status == status
        assert phrase in raised.value.message

    def test_registering_the_payee_already_in_force_is_refused(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        with pytest.raises(reg.RegistryError) as raised:
            run(reg.register_payee(name, PAYEE.upper(), OPERATOR, "password"))
        assert raised.value.status == 409
        assert "already the verified payee" in raised.value.message

    def test_a_changed_payee_waits_for_verification_and_then_carries_its_time(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        second = run(reg.register_payee(name, NEW_PAYEE, OPERATOR, "password"))
        assert second["version"] == 2
        assert run(reg.current(name))["payee_email"] == PAYEE
        run(reg.verify_payee(second["carrier_key"], 2, REVIEWER, "password", NOTE))
        live = run(reg.current(name))
        assert live["payee_email"] == NEW_PAYEE
        assert live["version"] == 2
        assert live["changed_at"] == live["verified_at"]

    def test_the_listing_shows_each_carrier_with_its_payee_and_what_is_pending(self):
        reg = registry()
        verified, waiting = carrier(), carrier()
        register_and_verify(verified, PAYEE)
        run(reg.register_payee(verified, NEW_PAYEE, OPERATOR, "password"))
        run(reg.register_payee(waiting, ATTACKER, OPERATOR, "password"))
        by_key = {item["carrier_key"]: item for item in run(reg.carriers())}
        first = by_key[reg.carrier_key(verified)]
        assert first["current"]["payee_email"] == PAYEE
        assert [p["payee_email"] for p in first["pending"]] == [NEW_PAYEE]
        second = by_key[reg.carrier_key(waiting)]
        assert second["current"] is None
        assert [p["version"] for p in second["pending"]] == [1]
        assert second["pending"][0]["registered_by"] == OPERATOR


class TestWhatTheRegistryOverridesInAShipment(Base):
    def test_a_shipment_that_moves_no_money_is_left_alone(self):
        reg = registry()
        shipment = {"shipment_id": "SHP-2", "carrier_registered_payee_email": ATTACKER, "shipping_cost": 10.0}
        assert run(reg.enrich(shipment)) is shipment
        assert shipment["carrier_registered_payee_email"] == ATTACKER

    def test_a_claim_about_a_carrier_the_registry_does_not_know_is_removed(self):
        reg = registry()
        shipment = paid(
            carrier(), carrier_payee_email=ATTACKER, carrier_registered_payee_email=ATTACKER,
            carrier_payee_changed_at="2020-01-01T00:00:00+00:00",
        )
        run(reg.enrich(shipment))
        assert "carrier_registered_payee_email" not in shipment
        assert "carrier_payee_changed_at" not in shipment
        codes = [f["code"] for f in checks().check_payment(shipment)]
        assert "PAYEE_UNVERIFIED" in codes

    def test_a_shipment_with_no_carrier_name_has_nothing_on_file(self):
        reg = registry()
        shipment = paid(None, carrier_payee_email=PAYEE, carrier_registered_payee_email=PAYEE)
        shipment.pop("carrier_name")
        run(reg.enrich(shipment))
        assert "carrier_registered_payee_email" not in shipment
        assert "PAYEE_UNVERIFIED" in [f["code"] for f in checks().check_payment(shipment)]

    def test_a_sender_who_states_both_the_payee_and_the_payee_on_file_gets_nothing_for_it(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        shipment = paid(name, carrier_payee_email=ATTACKER, carrier_registered_payee_email=ATTACKER)
        run(reg.enrich(shipment))
        assert shipment["carrier_registered_payee_email"] == PAYEE
        assert shipment["carrier_payee_email"] == ATTACKER
        assert "PAYEE_MISMATCH" in [f["code"] for f in checks().check_payment(shipment)]

    def test_the_registered_payee_is_used_when_none_was_requested(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        shipment = paid(name)
        run(reg.enrich(shipment))
        assert shipment["carrier_payee_email"] == PAYEE
        assert checks().check_payment(shipment) == []

    def test_a_date_of_change_the_sender_states_is_never_believed(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        shipment = paid(name, carrier_payee_changed_at="2020-01-01T00:00:00+00:00")
        run(reg.enrich(shipment))
        assert "carrier_payee_changed_at" not in shipment

    def test_a_changed_payee_is_held_in_the_cooling_off_window_from_when_it_was_verified(self):
        reg = registry()
        name = carrier()
        register_and_verify(name, PAYEE)
        register_and_verify(name, NEW_PAYEE)
        shipment = paid(name, carrier_payee_changed_at="2020-01-01T00:00:00+00:00")
        run(reg.enrich(shipment))
        changed = datetime.fromisoformat(shipment["carrier_payee_changed_at"])
        soon = [f["code"] for f in checks().check_payment(shipment, now=changed + timedelta(days=3))]
        later = [f["code"] for f in checks().check_payment(shipment, now=changed + timedelta(days=15))]
        assert "PAYEE_RECENTLY_CHANGED" in soon
        assert later == []
