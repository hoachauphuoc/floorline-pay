"""
A credit standing for each carrier, A to D, and the most one payout to it may be.

A carrier nobody has paid before is a risk that no document can show, and a carrier PayPal has
frozen is one the settler already refuses. In between, the amount a single payout may reach
should grow with a record of being paid without trouble. The standing is worked out here from
the settlement history, never from what a shipment says:

  D  frozen now (payment_freeze)                         up to  500 USD
  C  never paid by Floorline                             up to 2000 USD
  B  paid once or twice                                  up to 5000 USD
  A  paid three times or more                            twice its largest confirmed payout, at least 5000 USD

A payment-risk analyst (payment_ai.py, a model, run when the case is analysed) may suggest a
standing. The standing applied is the worse of the two, so a suggestion can lower a carrier's
cap and can never raise it, and a suggestion that is not one of A to D is ignored. The cap sits
beside the limits a person publishes (payment_controls.py): a payout must pass both, which is
the smaller of the two. Nothing here loosens a limit; a carrier moves up by being paid.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import carrier_registry, payment_freeze, payout_tracking
from vf_logistics.store import get_store

TIERS = ("A", "B", "C", "D")
CAPS: dict[str, Decimal] = {
    "A": Decimal("5000"), "B": Decimal("5000"), "C": Decimal("2000"), "D": Decimal("500"),
}

# The cap of A is a floor: above it, a carrier may receive this many times its largest confirmed payout, so a record of small
# payouts cannot unlock a large one.
A_GROWTH = Decimal("2")

_PAGE = 200
_PAGES = 10


def _paid_amount(value: Any) -> Decimal:
    """The amount a settlement recorded; one that cannot be read counts as nothing, so it raises no cap."""
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")
    return amount if amount.is_finite() else Decimal("0")


async def history(carrier_name: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """How many payouts to the carrier PayPal has confirmed, the largest of them, and whether it is frozen now.

    A payment the settler recorded as settled counts only once PayPal confirms its payout: one PayPal only accepted, or later
    returned, does not raise the standing.
    """
    key = carrier_registry.carrier_key(carrier_name)
    settled: dict[str, Decimal] = {}
    cursor: str | None = None
    for _ in range(_PAGES if key else 0):
        # One filter only (Firestore applies just one of case_id, action and status), so the
        # status is checked here.
        rows, cursor = await get_store().query_audit(
            action="settle_payment", cursor=cursor, limit=_PAGE, tenant_id=tenant_id,
        )
        for row in rows:
            detail = row.get("detail")
            if row.get("status") == "settled" and isinstance(detail, dict) and detail.get("carrier_key") == key:
                case_id = str(row.get("case_id") or "")
                settled[case_id] = max(settled.get(case_id, Decimal("0")), _paid_amount(detail.get("amount")))
        if not cursor:
            break
    paid = 0
    largest = Decimal("0")
    for case_id, amount in settled.items():
        if case_id and (await payout_tracking.confirmation(case_id, tenant_id))["state"] == payout_tracking.CONFIRMED:
            paid += 1
            largest = max(largest, amount)
    frozen = await payment_freeze.frozen("carrier", carrier_name, tenant_id) is not None
    return {"paid_count": paid, "largest_paid": largest, "frozen": frozen}


def baseline(record: dict[str, Any]) -> str:
    """The standing the settlement history alone gives."""
    if record.get("frozen"):
        return "D"
    paid = int(record.get("paid_count") or 0)
    return "C" if paid == 0 else ("B" if paid < 3 else "A")


def tighter(*tiers: str | None) -> str:
    """The worst of the standings given; one that is not A to D counts as D."""
    worst = "A"
    for tier in tiers:
        if tier is None:
            continue
        letter = tier if tier in TIERS else "D"
        if TIERS.index(letter) > TIERS.index(worst):
            worst = letter
    return worst


def cap(tier: str, largest_paid: Decimal = Decimal("0")) -> Decimal:
    """The most one payout may be at this standing; only A grows, with the largest payout PayPal confirmed."""
    if tier == "A":
        return max(CAPS["A"], largest_paid * A_GROWTH)
    return CAPS[tier] if tier in CAPS else CAPS["D"]


def suggestion(ai: Any) -> str | None:
    """The standing the analyst suggested, if it suggested one of A to D."""
    analyst = ai.get("analyst") if isinstance(ai, dict) else None
    tier = analyst.get("suggested_tier") if isinstance(analyst, dict) else None
    return tier if tier in TIERS else None


async def profile(carrier_name: Any, ai: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """The standing applied to a carrier, the cap it sets and how it was reached."""
    record = await history(carrier_name, tenant_id)
    base, suggested = baseline(record), suggestion(ai)
    applied = tighter(base, suggested)
    return {
        "tier": applied, "baseline": base, "suggested": suggested, "cap_usd": cap(applied, record["largest_paid"]),
        "paid_count": record["paid_count"], "frozen": record["frozen"],
    }
