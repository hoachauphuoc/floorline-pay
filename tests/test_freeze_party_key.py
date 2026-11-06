"""
One party, however its legal form is written (review F02).

A freeze was kept under the exact letters of the party's name, so a shipper blocked as "X Co" was not held when a later case wrote
"X Company Ltd". The key now drops a legal form that is a whole trailing word. Pinned here: other spellings of the form are one
party; a name that merely ends in the letters of a form is another; a freeze made under the old exact-spelling key still holds; a
lift under another spelling clears the old freeze too; and a new freeze after a lift holds again.
"""

from __future__ import annotations

import asyncio
import uuid

from vf_logistics import carrier_registry, payment_freeze
from vf_logistics.store import get_store, utcnow


def run(coro):
    return asyncio.run(coro)


def tag():
    return uuid.uuid4().hex[:8]


def old_freeze(name, why="a dispute was opened"):
    """A freeze as it was written before the legal form was dropped: under the exact-spelling key."""
    key = carrier_registry.carrier_key(name)
    run(get_store().add_audit({
        "audit_id": f"legacy-{uuid.uuid4().hex[:8]}", "case_id": f"PAYMENT-FREEZE-shipper-{key}",
        "action": payment_freeze.FROZEN, "status": "done",
        "detail": {"kind": "shipper", "party": key, "name": name, "reason": why, "source": "event-old"},
        "at": utcnow(),
    }))


def test_one_shipper_written_with_another_legal_form_is_the_same_party():
    t = tag()
    assert run(payment_freeze.freeze("shipper", f"Saigon Plywood {t} Co", "a dispute was opened", f"event-{t}"))
    for other in (f"Saigon Plywood {t} Company Ltd", f"SAIGON PLYWOOD {t}, Co., Ltd.", f"Saigon Plywood {t}"):
        assert run(payment_freeze.frozen("shipper", other)) is not None, other


def test_a_name_that_only_ends_in_the_letters_of_a_legal_form_is_another_party():
    t = tag()
    assert run(payment_freeze.freeze("shipper", f"Mekong {t} Disco", "a dispute was opened", f"event-{t}"))
    assert run(payment_freeze.frozen("shipper", f"Mekong {t} Dis")) is None


def test_a_new_freeze_is_one_row_under_the_new_key_and_none_under_the_old_one():
    t = tag()
    name = f"Saigon Plywood {t} Co"
    assert run(payment_freeze.freeze("shipper", name, "a dispute was opened", f"event-{t}"))
    rows, _ = run(get_store().query_audit(case_id=f"PAYMENT-FREEZE-shipper-{carrier_registry.carrier_key(name)}", limit=5))
    assert rows == []


def test_a_freeze_made_under_the_old_exact_key_still_holds():
    t = tag()
    name = f"Saigon Plywood {t} Co"
    old_freeze(name, "a refund was reported")
    found = run(payment_freeze.frozen("shipper", name))
    assert found is not None
    assert found["reason"] == "a refund was reported"


def test_a_lift_under_another_spelling_clears_the_old_freeze_too():
    t = tag()
    name = f"Saigon Plywood {t} Co"
    old_freeze(name)
    assert run(payment_freeze.freeze("shipper", name, "a dispute was opened", f"event-{t}"))
    run(payment_freeze.lift("shipper", f"Saigon Plywood {t} Company Limited", "checked with the shipper", "admin@example.com", "password"))
    assert run(payment_freeze.frozen("shipper", name)) is None
    assert run(payment_freeze.frozen("shipper", f"Saigon Plywood {t} Ltd")) is None


def test_a_new_freeze_after_a_lift_holds_again():
    t = tag()
    name = f"Saigon Plywood {t} Co"
    assert run(payment_freeze.freeze("shipper", name, "a dispute was opened", f"event-a-{t}"))
    run(payment_freeze.lift("shipper", name, "checked with the shipper", "admin@example.com", "password"))
    assert run(payment_freeze.frozen("shipper", name)) is None
    assert run(payment_freeze.freeze("shipper", f"Saigon Plywood {t} Company Ltd", "a second dispute", f"event-b-{t}"))
    assert run(payment_freeze.frozen("shipper", name)) is not None
