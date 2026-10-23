"""
What became of a payout after PayPal accepted it.

The settler records "settled" when PayPal accepts the payout batch, which is not the same as
the carrier having the money: a payout can later fail, be blocked, or come back. PayPal says
so in a webhook (paypal_webhook.py) or when asked (refresh below), and each answer is an audit
row on the case. The state of a case is the worst thing any of them said: one failed row
makes it failed, else one confirmed row makes it confirmed, else it is pending. A case with
no row at all is "unconfirmed", never "confirmed". A payout that failed, was blocked, was
returned or refunded freezes the carrier (payment_freeze.py), because the account the
money was meant for did not take it.
"""

from __future__ import annotations

from typing import Any

from vf_logistics import payment_freeze, paypal_client
from vf_logistics.store import get_store, utcnow

ACTION = "payout_status"
_ROWS = 100

CONFIRMED = "confirmed"
FAILED = "failed"
PENDING = "pending"
UNCONFIRMED = "unconfirmed"

_CONFIRMED_STATUSES = frozenset({"SUCCESS", "SUCCEEDED"})
_FAILED_STATUSES = frozenset({
    "FAILED", "RETURNED", "BLOCKED", "DENIED", "REFUNDED", "REVERSED", "CANCELED",
})


class PayoutError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def classify(status: Any) -> str:
    """confirmed, failed or pending for a PayPal payout status; anything unknown is pending."""
    word = str(status or "").upper()
    if word in _CONFIRMED_STATUSES:
        return CONFIRMED
    return FAILED if word in _FAILED_STATUSES else PENDING


async def apply(
    case_id: str, source: str, status: Any, carrier_name: Any, tenant_id: str | None = None,
) -> str:
    """Record one payout status for a case; a failed one freezes the carrier. Returns its class."""
    state = classify(status)
    await get_store().add_audit_once({
        "audit_id": f"payout-{source}",
        "case_id": case_id,
        "action": ACTION,
        "status": "done",
        "detail": {"transaction_status": str(status or "").upper(), "source": source},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    if state == FAILED:
        await payment_freeze.freeze(
            "carrier", carrier_name, f"PayPal reports the payout as {str(status).upper()}", source, tenant_id,
        )
    return state


async def confirmation(case_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """The state of a case's payout from the statuses recorded for it."""
    rows, _ = await get_store().query_audit(
        case_id=case_id, limit=_ROWS, tenant_id=tenant_id,
    )
    rows = [r for r in rows if r.get("action") == ACTION]
    if not rows:
        return {"state": UNCONFIRMED, "status": None, "at": None}
    ranked = []
    for row in rows:
        detail = row.get("detail")
        status = detail.get("transaction_status") if isinstance(detail, dict) else None
        ranked.append((classify(status), status, row.get("at")))
    for wanted in (FAILED, CONFIRMED):
        for state, status, at in ranked:
            if state == wanted:
                return {"state": state, "status": status, "at": at}
    return {"state": PENDING, "status": ranked[0][1], "at": ranked[0][2]}


def _overall(body: dict[str, Any]) -> str:
    items = body.get("items")
    statuses = [
        str(i.get("transaction_status") or "") for i in (items if isinstance(items, list) else [])
        if isinstance(i, dict)
    ]
    header = body.get("batch_header")
    if not statuses and isinstance(header, dict):
        statuses = [str(header.get("batch_status") or "")]
    for status in statuses:
        if classify(status) == FAILED:
            return status
    for status in statuses:
        if classify(status) != CONFIRMED:
            return status or "UNKNOWN"
    return statuses[0] if statuses else "UNKNOWN"


async def refresh(case_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Ask PayPal about the payout batch of a settled case and record what it says."""
    store = get_store()
    rows, _ = await store.query_audit(
        case_id=case_id, limit=_ROWS, tenant_id=tenant_id,
    )
    rows = [r for r in rows if r.get("action") == "settle_payment" and r.get("status") == "settled"]
    detail = rows[0].get("detail") if rows else None
    payout = detail.get("payout") if isinstance(detail, dict) else None
    batch = str(payout.get("paypal_id") or "") if isinstance(payout, dict) else ""
    if not batch:
        raise PayoutError("this case has no settled payout to ask PayPal about", 409)
    receipt = await paypal_client.get_payout_batch(batch, f"refresh-{batch}")
    if receipt["status"] != paypal_client.OK or not isinstance(receipt.get("body"), dict):
        code = 503 if receipt["status"] == paypal_client.NOT_CONFIGURED else 502
        raise PayoutError("PayPal could not tell the state of the payout; nothing was recorded", code)
    status = _overall(receipt["body"])
    case = await store.get_case(case_id, tenant_id=tenant_id)
    carrier = ((case or {}).get("shipment") or {}).get("carrier_name")
    await apply(case_id, f"refresh-{batch}-{status}", status, carrier, tenant_id)
    return await confirmation(case_id, tenant_id)
