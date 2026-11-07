"""
A limit refusal does not drop a reservation an earlier delivery left open (follow-up review F01, 2026-11-07).

The settler keeps a reservation while a payout may stand at PayPal. reserve_payout gave up every reservation of the key it refused, so
a redelivery refused by the limits dropped it. Pinned here: a refusal gives up only a reservation that call made, and the kept one
goes on counting against other payouts.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def controls():
    return importlib.import_module("vf_logistics.payment_controls")


def reserve(key, amount="4000", carrier="mekong"):
    return run(controls().reserve_payout(key, Decimal(amount), carrier))


def open_keys():
    return [row["detail"]["idempotency_key"] for row in run(controls()._open_reservations(None))]


class TestAReservationAnEarlierDeliveryLeftOpen(Base):
    def test_a_refusal_keeps_the_reservation_that_was_already_open(self):
        assert reserve("k1") is None
        assert reserve("k2", amount="5000") is None
        assert reserve("k1", amount="6000") is not None
        assert open_keys().count("k1") == 1

    def test_the_kept_reservation_goes_on_counting_against_other_payouts(self):
        assert reserve("k1") is None
        assert reserve("k2", amount="5000") is None
        assert reserve("k1", amount="6000") is not None
        assert reserve("k3", amount="2000") is not None

    def test_a_new_reservation_that_is_refused_still_gives_itself_up(self):
        assert reserve("k1") is None
        assert reserve("k2") is None
        assert reserve("k3") is not None
        assert "k3" not in open_keys()
