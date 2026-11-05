"""
The settler reserves a payout's amount before it moves money, and gives the reservation up when nothing was paid.

Two settlements at the same moment each saw only what had been paid, so together they could pass a daily limit. The
settler now calls payment_controls.reserve_payout before it captures: a payout that would pass the limit once other
settlements' reservations are counted is retried and nothing moves. A settlement that ends without paying (a PayPal
refusal, for instance) gives its reservation up, so a later payout is not refused for money that never left.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
from test_settler_controls import ADMIN, LIMITS, PAID_OUT, carrier, decision, mod, pp, run  # noqa: E402, F401

REFUSED = {"status": "http_error", "http_status": 400, "body": {}, "error": "PayPal refused the payout"}
ROOM_FOR_ONE = {**LIMITS, "carrier_daily_usd": 2000, "tenant_daily_usd": 5000, "carrier_daily_count": 5}


def test_a_payout_is_retried_when_other_settlements_have_reserved_the_days_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    run(mod("payment_controls").publish_limits(ROOM_FOR_ONE, "room for one", ADMIN, "password"))
    name = carrier()
    other = mod("carrier_registry").carrier_key(name)
    assert run(mod("payment_controls").reserve_payout("other-settlement", Decimal("1250"), other)) is None
    result = run(settler.settle(decision(name)))
    assert result["status"] == "retry"
    assert "daily limit of 2000.0 USD" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_settlement_that_pays_nothing_gives_its_reservation_up(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    run(mod("payment_controls").publish_limits(ROOM_FOR_ONE, "room for one", ADMIN, "password"))
    name = carrier()
    payout.side_effect = [REFUSED, PAID_OUT]
    first = run(settler.settle(decision(name)))
    assert first["status"] == "failed"
    second = run(settler.settle(decision(name)))
    assert second["status"] == "settled"
