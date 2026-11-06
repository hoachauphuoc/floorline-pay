"""
The list of frozen parties (payment_freeze.frozen_parties), a gap found by the API review of 25 Oct 2026.

A freeze is keyed by the party's full name, and the name is stored cut to 120 characters. The list looked each party up again
from the stored name, so a long name whose first 120 characters hold fewer letters and digits than its key got another key,
found nothing, and the frozen party disappeared from the list a governance admin lifts freezes from.
"""

from __future__ import annotations

import asyncio
import uuid

from vf_logistics import carrier_registry, payment_freeze


def run(coro):
    return asyncio.run(coro)


def test_a_frozen_party_whose_stored_name_was_cut_is_still_listed():
    name = " ".join(uuid.uuid4().hex * 3)
    party = carrier_registry.carrier_key(name)
    assert run(payment_freeze.freeze("carrier", name, "a dispute was opened on a payment", f"event-{uuid.uuid4().hex[:8]}"))
    listed = run(payment_freeze.frozen_parties())
    assert any(f["kind"] == "carrier" and f["party"] == party for f in listed)


def test_a_party_with_a_short_name_is_listed_as_before():
    name = f"Carrier {uuid.uuid4().hex[:8]}"
    party = carrier_registry.carrier_key(name)
    assert run(payment_freeze.freeze("carrier", name, "a dispute was opened on a payment", f"event-{uuid.uuid4().hex[:8]}"))
    listed = run(payment_freeze.frozen_parties())
    assert any(f["kind"] == "carrier" and f["party"] == party and f["name"] == name for f in listed)
