"""
Dual approval of a large release in Floorline Pay.

One compromised or coerced reviewer must not be able to send a large payout alone. A
release whose payout is at or above a threshold needs two different people: the first
approves it (evidence.approve) and a second, also signed in with a password, approves the
same release (evidence.second_approve). Until both have, the release cannot be executed
and the settler holds it.

The threshold is PAYMENT_DUAL_APPROVAL_USD, 2500 when it is not set. A value that cannot
be read is zero, so every payout then needs two people: a typo tightens the rule and
never loosens it. A payment case whose payout cannot be read as an amount needs two
people as well.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import payment_checks

DEFAULT_THRESHOLD_USD = Decimal("2500")


def threshold() -> Decimal:
    """The payout from which a release needs two approvers, in US dollars."""
    raw = os.environ.get("PAYMENT_DUAL_APPROVAL_USD")
    if raw is None or not raw.strip():
        return DEFAULT_THRESHOLD_USD
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return Decimal(0)
    return value if value.is_finite() and value >= 0 else Decimal(0)


def needed(shipment: dict[str, Any]) -> bool:
    """True when releasing this shipment's payout needs a second approver."""
    if not payment_checks.is_payment_case(shipment):
        return False
    try:
        payout = Decimal(str(shipment.get("payout_amount")))
    except InvalidOperation:
        return True
    if not payout.is_finite() or payout <= 0:
        return True
    return payout >= threshold()
