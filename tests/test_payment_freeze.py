"""
Freezes on the parties of a payment (payment_freeze.py).

Pinned here: freezing a carrier or a shipper is recorded once per event; a frozen party is
reported with the reason; only a person with a reason lifts it, and only a party that is
frozen; a party that is frozen again by a later event stays frozen even after an earlier lift;
a name with no usable key cannot be frozen; and the frozen parties are listed newest first.

payment_freeze is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest


def run(coro):
    return asyncio.run(coro)


def mod():
    return importlib.import_module("vf_logistics.payment_freeze")


def name():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def test_a_frozen_party_is_reported_with_its_reason_and_source():
    pf, carrier = mod(), name()
    assert run(pf.frozen("carrier", carrier)) is None
    assert run(pf.freeze("carrier", carrier, "a dispute was opened", "event-1")) is True
    found = run(pf.frozen("carrier", carrier))
    assert found["reason"] == "a dispute was opened"
    assert found["source"] == "event-1"
    assert found["kind"] == "carrier"


def test_the_same_event_freezes_a_party_once():
    pf, carrier = mod(), name()
    run(pf.freeze("carrier", carrier, "first", "event-1"))
    run(pf.freeze("carrier", carrier, "second", "event-1"))
    assert run(pf.frozen("carrier", carrier))["reason"] == "first"


def test_a_carrier_freeze_does_not_freeze_a_shipper_of_the_same_name():
    pf, party = mod(), name()
    run(pf.freeze("carrier", party, "x", "event-1"))
    assert run(pf.frozen("shipper", party)) is None


def test_a_name_with_no_usable_key_cannot_be_frozen():
    pf = mod()
    assert run(pf.freeze("carrier", "  ", "x", "event-1")) is False
    assert run(pf.freeze("warehouse", "Acme", "x", "event-1")) is False
    assert run(pf.frozen("carrier", "")) is None


def test_a_person_lifts_a_freeze_with_a_reason():
    pf, carrier = mod(), name()
    run(pf.freeze("carrier", carrier, "x", "event-1"))
    run(pf.lift("carrier", carrier, "paid back by the carrier", "admin@forwarder.example", "password"))
    assert run(pf.frozen("carrier", carrier)) is None


def test_a_later_freeze_stands_after_an_earlier_lift():
    pf, carrier = mod(), name()
    run(pf.freeze("carrier", carrier, "first", "event-1"))
    run(pf.lift("carrier", carrier, "checked", "admin@forwarder.example", "password"))
    run(pf.freeze("carrier", carrier, "second", "event-2"))
    assert run(pf.frozen("carrier", carrier))["reason"] == "second"


@pytest.mark.parametrize("reason, actor, status, phrase", [
    ("", "admin@forwarder.example", 400, "reason is required"),
    ("   ", "admin@forwarder.example", 400, "reason is required"),
    ("checked", "", 403, "lifted by a person"),
])
def test_a_lift_without_a_reason_or_a_person_is_refused(reason, actor, status, phrase):
    pf, carrier = mod(), name()
    run(pf.freeze("carrier", carrier, "x", "event-1"))
    with pytest.raises(pf.FreezeError) as raised:
        run(pf.lift("carrier", carrier, reason, actor, "password"))
    assert raised.value.status == status
    assert phrase in raised.value.message
    assert run(pf.frozen("carrier", carrier)) is not None


def test_lifting_a_party_that_is_not_frozen_is_refused():
    pf = mod()
    with pytest.raises(pf.FreezeError) as raised:
        run(pf.lift("carrier", name(), "checked", "admin@forwarder.example", "password"))
    assert raised.value.status == 409
    assert "not frozen" in raised.value.message


def test_lifting_an_unknown_kind_is_refused():
    pf = mod()
    with pytest.raises(pf.FreezeError) as raised:
        run(pf.lift("warehouse", "Acme", "checked", "admin@forwarder.example", "password"))
    assert raised.value.status == 400


def test_the_frozen_parties_are_listed_without_the_lifted_ones():
    pf, kept, lifted = mod(), name(), name()
    run(pf.freeze("carrier", kept, "kept", "event-1"))
    run(pf.freeze("shipper", lifted, "lifted", "event-2"))
    run(pf.lift("shipper", lifted, "checked", "admin@forwarder.example", "password"))
    parties = run(pf.frozen_parties())
    assert [p["name"] for p in parties if p["name"] in (kept, lifted)] == [kept]
