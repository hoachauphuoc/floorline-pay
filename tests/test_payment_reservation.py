"""
A payout reserves its amount before it is paid, so two settlements at the same moment cannot both pass a daily limit.

usage_today counted only payouts already paid, so settlements that checked the limit at the same moment each saw
room for one more payout and together passed it. reserve_payout writes a reservation first and then totals the paid
payouts and the other open reservations: the later of two concurrent settlements sees the earlier one and gives way
(retried the next day), never the reverse. A reservation is dropped when its payout is paid (the paid row counts it
instead) or when the settlement gives it up, and a decision delivered twice holds one reservation.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def controls():
    return importlib.import_module("vf_logistics.payment_controls")


def reserve(key, amount="4000", carrier="mekong"):
    return run(controls().reserve_payout(key, Decimal(amount), carrier))


class TestAPayoutIsReservedBeforeItIsPaid(Base):
    def test_unpaid_reservations_count_against_the_carriers_daily_limit(self):
        assert reserve("k1") is None
        assert reserve("k2") is None
        refused = reserve("k3")
        assert refused is not None
        status, reason = refused
        assert status == "retry"
        assert "daily limit of 10000.0 USD" in reason

    def test_a_refused_reservation_gives_itself_up(self):
        reserve("k1")
        reserve("k2")
        assert reserve("k3") is not None
        run(controls().cancel_reservations("k1"))
        run(controls().cancel_reservations("k2"))
        assert reserve("k4", amount="7000") is None

    def test_giving_up_a_reservation_frees_its_amount(self):
        reserve("k1")
        reserve("k2")
        run(controls().cancel_reservations("k2"))
        assert reserve("k3") is None

    def test_a_reservation_that_was_paid_is_counted_once(self):
        assert reserve("k1") is None
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": "CASE-PAID",
            "action": "settle_payment", "status": "settled",
            "detail": {"amount": "4000", "carrier_key": "mekong", "idempotency_key": "k1"},
            "at": datetime.now(timezone.utc).isoformat(),
        }))
        assert reserve("k2") is None
        assert reserve("k3") is not None

    def test_the_same_decision_reserved_twice_holds_one_reservation(self):
        assert reserve("k1") is None
        assert reserve("k1") is None
        assert reserve("k2") is None

    def test_a_payout_with_no_decision_key_is_held(self):
        status, reason = reserve("")
        assert status == "held"
        assert "no decision key" in reason
