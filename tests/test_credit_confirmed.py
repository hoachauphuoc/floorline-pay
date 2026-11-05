"""
Only a payout PayPal confirmed raises a carrier's standing (review F06, 2026-11-05).

credit_profile.history counted every settled payment, so a payout PayPal had only accepted, or had later returned, counted as a
success and lifted the carrier's cap. Pinned here: a settled payment counts once its payout is confirmed and not before; a payout
PayPal reports as failed or returned does not count even if an earlier status said success; three settled payments that nobody
confirmed leave the carrier at C.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_credit_profile import carrier, mod, paid, run, settle  # noqa: E402
from vf_logistics import store as store_mod  # noqa: E402
from vf_logistics.store import utcnow  # noqa: E402


def report(name, n, status):
    """PayPal's word on the payout of the n-th seeded payment of the carrier."""
    key = mod("carrier_registry").carrier_key(name)
    run(store_mod.get_store().add_audit({
        "audit_id": f"payout-seed-{key}-{n}-{uuid.uuid4().hex[:6]}", "case_id": f"CASE-{key}-{n}",
        "action": "payout_status", "status": "done",
        "detail": {"transaction_status": status, "source": f"seed-{uuid.uuid4().hex[:6]}"}, "at": utcnow(),
    }))


def test_three_settled_payments_nobody_confirmed_leave_the_carrier_at_c():
    cp, name = mod(), carrier()
    settle(name, 3)
    record = run(cp.history(name))
    assert record["paid_count"] == 0
    assert cp.baseline(record) == "C"


def test_a_payout_still_pending_at_paypal_does_not_count():
    cp, name = mod(), carrier()
    settle(name, 3)
    for n in range(3):
        report(name, n, "PENDING")
    assert run(cp.history(name))["paid_count"] == 0


def test_confirmed_payouts_count_and_three_make_a():
    cp, name = mod(), carrier()
    paid(name, 3)
    record = run(cp.history(name))
    assert record["paid_count"] == 3
    assert cp.baseline(record) == "A"


def test_a_payout_returned_after_it_was_confirmed_does_not_count():
    cp, name = mod(), carrier()
    paid(name, 3)
    report(name, 2, "RETURNED")
    record = run(cp.history(name))
    assert record["paid_count"] == 2
    assert cp.baseline(record) == "B"


def test_two_confirmed_and_one_unconfirmed_leave_the_carrier_at_b():
    cp, name = mod(), carrier()
    settle(name, 3)
    report(name, 0, "SUCCESS")
    report(name, 1, "SUCCESS")
    record = run(cp.history(name))
    assert record["paid_count"] == 2
    assert cp.baseline(record) == "B"
