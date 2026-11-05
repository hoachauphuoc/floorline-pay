"""
A credit standing for each carrier, A to D, and the most one payout to it may be.

A carrier nobody has paid before is a risk that no document can show, and a carrier PayPal has
frozen is one the settler already refuses. In between, the amount a single payout may reach
should grow with a record of being paid without trouble. The standing is worked out here from
the settlement history, never from what a shipment says:

  D  frozen now (payment_freeze)                         up to  500 USD
  C  never paid by Floorline                             up to 2000 USD
  B  paid once or twice                                  up to 5000 USD
  A  paid three times or more                            no cap of its own

A payment-risk analyst (payment_ai.py, a model, run when the case is analysed) may suggest a
standing. The standing applied is the worse of the two, so a suggestion can lower a carrier's
cap and can never raise it, and a suggestion that is not one of A to D is ignored. The cap sits
beside the limits a person publishes (payment_controls.py): a payout must pass both, which is
the smaller of the two. Nothing here loosens a limit; a carrier moves up by being paid.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from vf_logistics import carrier_registry, payment_freeze, payout_tracking
from vf_logistics.store import get_store

TIERS = ("A", "B", "C", "D")
CAPS: dict[str, Decimal | None] = {
    "A": None, "B": Decimal("5000"), "C": Decimal("2000"), "D": Decimal("500"),
}

_PAGE = 200
_PAGES = 10


async def history(carrier_name: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """How many payouts to the carrier PayPal has confirmed, and whether it is frozen now.

    A payment the settler recorded as settled counts only once PayPal confirms its payout: one PayPal only accepted, or later
    returned, does not raise the standing.
    """
    key = carrier_registry.carrier_key(carrier_name)
    settled: list[str] = []
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
                settled.append(str(row.get("case_id") or ""))
        if not cursor:
            break
    paid = 0
    for case_id in dict.fromkeys(settled):
        if case_id and (await payout_tracking.confirmation(case_id, tenant_id))["state"] == payout_tracking.CONFIRMED:
            paid += 1
    frozen = await payment_freeze.frozen("carrier", carrier_name, tenant_id) is not None
    return {"paid_count": paid, "frozen": frozen}


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


def cap(tier: str) -> Decimal | None:
    """The most one payout may be at this standing, or None when the standing sets no cap."""
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
        "tier": applied, "baseline": base, "suggested": suggested, "cap_usd": cap(applied),
        "paid_count": record["paid_count"], "frozen": record["frozen"],
    }
