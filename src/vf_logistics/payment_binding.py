"""
The record that ties a PayPal authorization to the shipment it was made for.

When a shipper's approved order is authorized, the booking route writes one row here, with
what PayPal itself said about it: the reference the order was made for, the amount held and
the currency. The settler captures an authorization only if this row exists for the
shipment it is asked to pay, because the authorization id on a shipment event is typed by
whoever sent the event and proves nothing on its own.

The row is an audit row with an id derived from the authorization, so it is written once:
a second write for the same authorization changes nothing, and the first binding is the one
that stands. It is found by an exact case_id of "AUTH-<authorization id>", so a lookup never
depends on how many rows the audit trail holds.
"""

from __future__ import annotations

from typing import Any

from vf_logistics.store import get_store, utcnow

ACTION = "payment_authorized"


def _key(authorization_id: str) -> str:
    return f"AUTH-{authorization_id}"


async def record_authorization(
    authorization_id: str, reference_id: str, amount: str, currency: str, order_id: str,
    tenant_id: str | None = None,
) -> bool:
    """Bind an authorization to its shipment reference. True when this call wrote it."""
    written = await get_store().add_audit_once({
        "audit_id": f"payauth-{authorization_id}",
        "case_id": _key(authorization_id),
        "action": ACTION,
        "status": "done",
        "detail": {
            "authorization_id": authorization_id,
            "reference_id": reference_id,
            "amount": amount,
            "currency": currency,
            "order_id": order_id,
        },
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return bool(written)


async def find_authorization(
    authorization_id: str, tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """What the booking recorded for this authorization, or None when it recorded nothing."""
    if not authorization_id:
        return None
    rows, _ = await get_store().query_audit(
        case_id=_key(authorization_id), action=ACTION, limit=1, tenant_id=tenant_id,
    )
    if not rows:
        return None
    detail = rows[0].get("detail")
    return dict(detail) if isinstance(detail, dict) else None


async def record_mandate(authorization_id: str, mandate_id: str, tenant_id: str | None = None) -> bool:
    """Link an authorization to the shipper's mandate PayPal named on the order. True when this call wrote it."""
    written = await get_store().add_audit_once({
        "audit_id": f"payauthm-{authorization_id}",
        "case_id": f"AUTHM-{authorization_id}",
        "action": "payment_mandate_linked",
        "status": "done",
        "detail": {"authorization_id": authorization_id, "mandate_id": mandate_id},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return bool(written)


async def find_mandate(authorization_id: str, tenant_id: str | None = None) -> str | None:
    """The id of the mandate a booking was made under, or None when it was made under none."""
    if not authorization_id:
        return None
    rows, _ = await get_store().query_audit(
        case_id=f"AUTHM-{authorization_id}", limit=1, tenant_id=tenant_id,
    )
    detail = rows[0].get("detail") if rows else None
    value = detail.get("mandate_id") if isinstance(detail, dict) else None
    return str(value) if value else None
