"""
The credit standing of a carrier (credit_profile.py) and the cap it puts on one payout
(payment_controls.check_payout, settler.py).

Pinned here: the standing the settlement history gives (frozen is D, never paid is C, paid once
or twice is B, three or more is A); the analyst's suggestion can lower the standing and never
raise it, and a suggestion that is not A to D is ignored; each standing's cap; a payout must
pass the cap and the limits a person published, so the smaller wins, and the person's limit is
named first when it is the one exceeded; and in the settler a new carrier's large payout is held
while an established carrier's is paid.

credit_profile is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_settler_controls import NOTE, OPERATOR, PAYEE, REVIEWER, pp, run as run_c  # noqa: E402, F401
from vf_logistics import store as store_mod  # noqa: E402
from vf_logistics.store import utcnow  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def mod(name="credit_profile"):
    return importlib.import_module(f"vf_logistics.{name}")


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


# The pp fixture has already put a mock in place of get_authorization; its answer is changed here, not the mock replaced,
# because replacing it with a second patch leaks the first mock into every test that runs after this one.
LIVE_BIG = {"status": "ok", "http_status": 200, "error": None, "body": {
    "id": "A", "status": "CREATED", "amount": {"currency_code": "USD", "value": "3000.00"}}}


def big_decision(name, payout):
    """A release of a payout up to 3000 USD, on a booking and a freight figure that cover it."""
    registry = mod("carrier_registry")
    if run(registry.current(name)) is None:
        made = run(registry.register_payee(name, PAYEE, OPERATOR, "password"))
        run(registry.verify_payee(made["carrier_key"], made["version"], REVIEWER, "password", NOTE))
    authorization = f"AUTH{uuid.uuid4().hex[:12].upper()}"
    run(mod("payment_binding").record_authorization(authorization, "SHP-CRD-1", "3000.00", "USD", "ORDER-1"))
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(store_mod.get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": {
        "shipment_id": "SHP-CRD-1", "currency": "USD", "shipping_cost": 3000.0,
        "payout_amount": payout, "carrier_name": name, "paypal_authorization_id": authorization,
    }}))
    return {"case_id": case_id, "outcome": "RELEASED_BY_HUMAN", "approval_id": "apr-1",
            "idempotency_key": f"exec-{uuid.uuid4().hex[:8]}"}


def paid(name, times):
    key = mod("carrier_registry").carrier_key(name)
    for n in range(times):
        run(store_mod.get_store().add_audit({
            "audit_id": f"settle-{key}-{n}-{uuid.uuid4().hex[:6]}", "case_id": f"CASE-{key}-{n}",
            "action": "settle_payment", "status": "settled",
            "detail": {"amount": "1000", "carrier_key": key, "idempotency_key": f"seed-{key}-{n}"},
            "at": utcnow(),
        }))


@pytest.mark.parametrize("times, expected", [(0, "C"), (1, "B"), (2, "B"), (3, "A"), (10, "A")])
def test_the_history_gives_the_standing(times, expected):
    cp, name = mod(), carrier()
    paid(name, times)
    record = run(cp.history(name))
    assert record == {"paid_count": times, "frozen": False}
    assert cp.baseline(record) == expected


def test_a_frozen_carrier_is_d_whatever_it_has_been_paid():
    cp, name = mod(), carrier()
    paid(name, 5)
    run(mod("payment_freeze").freeze("carrier", name, "a dispute was opened", "event-1"))
    record = run(cp.history(name))
    assert record["frozen"] is True
    assert cp.baseline(record) == "D"


def test_only_settled_payments_count_on_a_store_that_filters_by_one_field(monkeypatch):
    store = store_mod.get_store()
    real = store.query_audit

    async def one_filter(case_id=None, action=None, status=None, cursor=None, limit=50, tenant_id=None):
        return await real(case_id=case_id, action=None if case_id else action, status=None, cursor=cursor,
                          limit=limit, tenant_id=tenant_id)

    monkeypatch.setattr(store, "query_audit", one_filter)
    cp, name = mod(), carrier()
    paid(name, 2)
    key = mod("carrier_registry").carrier_key(name)
    run(store.add_audit({
        "audit_id": f"held-{key}", "case_id": f"CASE-{key}-held", "action": "settle_payment", "status": "held",
        "detail": {"carrier_key": key, "idempotency_key": f"seed-{key}-held"}, "at": utcnow(),
    }))
    assert run(cp.history(name))["paid_count"] == 2


def test_other_carriers_payments_do_not_count():
    cp, name = mod(), carrier()
    paid(carrier(), 4)
    assert run(cp.history(name))["paid_count"] == 0


@pytest.mark.parametrize("first, second, expected", [
    ("A", "C", "C"), ("C", "A", "C"), ("B", "B", "B"), ("A", None, "A"), ("C", None, "C"),
    ("A", "Z", "D"), ("A", "d", "D"), ("B", "D", "D"),
])
def test_the_worse_of_two_standings_applies(first, second, expected):
    assert mod().tighter(first, second) == expected


@pytest.mark.parametrize("tier, expected", [
    ("A", None), ("B", Decimal("5000")), ("C", Decimal("2000")), ("D", Decimal("500")), ("Z", Decimal("500")),
])
def test_each_standing_has_its_cap_and_an_unknown_one_has_the_lowest(tier, expected):
    assert mod().cap(tier) == expected


@pytest.mark.parametrize("ai, expected", [
    ({"analyst": {"suggested_tier": "B"}}, "B"),
    ({"analyst": {"suggested_tier": "Z"}}, None),
    ({"analyst": {"suggested_tier": None}}, None),
    ({"analyst": "B"}, None),
    ({}, None), (None, None), ("B", None),
])
def test_only_a_suggestion_of_a_to_d_is_read(ai, expected):
    assert mod().suggestion(ai) == expected


def test_a_suggestion_lowers_an_established_carrier_and_cannot_raise_a_new_one():
    cp = mod()
    old, new = carrier(), carrier()
    paid(old, 4)
    lowered = run(cp.profile(old, {"analyst": {"suggested_tier": "C"}}))
    assert (lowered["baseline"], lowered["suggested"], lowered["tier"]) == ("A", "C", "C")
    assert lowered["cap_usd"] == Decimal("2000")
    raised = run(cp.profile(new, {"analyst": {"suggested_tier": "A"}}))
    assert (raised["baseline"], raised["suggested"], raised["tier"]) == ("C", "A", "C")
    assert raised["cap_usd"] == Decimal("2000")


def test_the_profile_of_an_established_carrier_has_no_cap_of_its_own():
    cp, name = mod(), carrier()
    paid(name, 3)
    out = run(cp.profile(name, None))
    assert out["tier"] == "A"
    assert out["cap_usd"] is None
    assert out["paid_count"] == 3


@pytest.mark.parametrize("payout, cap, reason", [
    (Decimal("2500"), Decimal("2000"), "credit tier C allows this carrier"),
    (Decimal("2000"), Decimal("2000"), None),
    (Decimal("1500"), Decimal("2000"), None),
    (Decimal("4500"), None, None),
])
def test_a_payout_must_pass_the_cap_of_the_standing(payout, cap, reason):
    controls = mod("payment_controls")
    out = run(controls.check_payout("USD", payout, "somecarrier", tier_cap=cap, tier="C"))
    if reason is None:
        assert out is None
    else:
        assert out[0] == "held"
        assert reason in out[1]
        assert "2000" in out[1]


def test_the_limit_a_person_published_is_named_when_it_is_the_one_exceeded():
    out = run(mod("payment_controls").check_payout("USD", Decimal("6000"), "somecarrier", tier_cap=Decimal("2000"), tier="C"))
    assert out[0] == "held"
    assert "per-payout limit" in out[1]


def test_a_cap_above_the_published_limit_changes_nothing():
    out = run(mod("payment_controls").check_payout("USD", Decimal("4000"), "somecarrier", tier_cap=Decimal("9000"), tier="B"))
    assert out is None


def test_the_settler_holds_a_new_carriers_large_payout(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    settler.paypal_client.get_authorization.return_value = LIVE_BIG
    result = run_c(settler.settle(big_decision(carrier(), 2500.0)))
    assert result["status"] == "held"
    assert "credit tier C" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_the_settler_pays_an_established_carriers_large_payout(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    settler.paypal_client.get_authorization.return_value = LIVE_BIG
    name = carrier()
    paid(name, 3)
    assert run_c(settler.settle(big_decision(name, 2500.0)))["status"] == "settled"
    payout.assert_awaited_once()


def test_the_settler_reads_the_analysts_suggestion_from_the_case(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    settler.paypal_client.get_authorization.return_value = LIVE_BIG
    name = carrier()
    paid(name, 3)
    d = big_decision(name, 2500.0)
    case = run(store_mod.get_store().get_case(d["case_id"]))
    case["payment_ai"] = {"analyst": {"suggested_tier": "C"}}
    run(store_mod.get_store().put_case(case))
    result = run_c(settler.settle(d))
    assert result["status"] == "held"
    assert "credit tier C" in result["reason"]
