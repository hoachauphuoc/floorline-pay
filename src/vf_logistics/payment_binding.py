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
BOOKING_FIELDS = frozenset({
    "shipment_id", "shipper_company", "shipper_tax_id", "shipper_country", "receiver_country", "origin", "destination", "cargo_description", "hs_code",
    "weight_kg", "declared_value", "shipping_cost", "carrier_name", "carrier_payee_email", "payout_amount",
})
MAX_BOOKING_VALUE = 500


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


async def record_order(order_id: str, reference_id: str, tenant_id: str | None = None) -> bool:
    """Claim a PayPal order for the tenant that created it. True when this tenant holds it, by this call or an earlier one.

    An order id is not a secret and PayPal answers a replayed request with the first order. The row's id is the order's and
    audit ids are unique across tenants, so the first tenant to create an order holds it and another tenant's claim writes
    nothing.
    """
    await get_store().add_audit_once({
        "audit_id": f"payorder-{order_id}",
        "case_id": f"ORDER-{order_id}",
        "action": "payment_order_created",
        "status": "done",
        "detail": {"order_id": order_id, "reference_id": reference_id},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return await find_order(order_id, tenant_id) is not None


async def find_order(order_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """What this tenant recorded when it created the order, or None when the order is not this tenant's."""
    if not order_id:
        return None
    rows, _ = await get_store().query_audit(case_id=f"ORDER-{order_id}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    return dict(detail) if isinstance(detail, dict) else None


def booking_problem(booking: Any, reference_id: str) -> str | None:
    """Why this form cannot be kept with an order for this shipment, or None."""
    if not isinstance(booking, dict):
        return "booking must be a JSON object; nothing is booked"
    if set(booking) - BOOKING_FIELDS:
        return "booking has an unknown field; send only the booking form; nothing is booked"
    if any(not isinstance(value, str) or len(value) > MAX_BOOKING_VALUE for value in booking.values()):
        return f"every booking value must be text of at most {MAX_BOOKING_VALUE} characters; nothing is booked"
    if booking.get("shipment_id") != reference_id:
        return "booking shipment_id must equal reference_id; nothing is booked"
    return None


async def record_booking(order_id: str, booking: dict[str, str], tenant_id: str | None = None) -> bool:
    """Keep the form under its order, so a closed tab cannot leave a hold with no case. The first booking stands. True when this call wrote it."""
    written = await get_store().add_audit_once({
        "audit_id": f"payorderb-{order_id}",
        "case_id": f"ORDERBOOK-{order_id}",
        "action": "payment_booking_kept",
        "status": "done",
        "detail": {"order_id": order_id, "booking": booking},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return bool(written)


async def find_booking(order_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """The form this tenant kept under the order, or None."""
    rows, _ = await get_store().query_audit(case_id=f"ORDERBOOK-{order_id}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    booking = detail.get("booking") if isinstance(detail, dict) else None
    return dict(booking) if isinstance(booking, dict) else None


async def record_order_authorization(order_id: str, authorization_id: str, tenant_id: str | None = None) -> bool:
    """Remember which authorization this order produced, so a reload answers with it. True when this call wrote it."""
    written = await get_store().add_audit_once({
        "audit_id": f"payorderauth-{order_id}",
        "case_id": f"ORDERAUTH-{order_id}",
        "action": "payment_order_authorized",
        "status": "done",
        "detail": {"order_id": order_id, "authorization_id": authorization_id},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return bool(written)


async def find_order_authorization(order_id: str, tenant_id: str | None = None) -> str | None:
    """The authorization this tenant recorded for the order, or None."""
    rows, _ = await get_store().query_audit(case_id=f"ORDERAUTH-{order_id}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    value = detail.get("authorization_id") if isinstance(detail, dict) else None
    return str(value) if value else None


async def unbound_orders(tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Authorized orders no case holds: money on hold that only a return to the booking page can tie to a shipment."""
    rows, _ = await get_store().query_audit(action="payment_order_authorized", limit=limit, tenant_id=tenant_id)
    cases = await get_store().list_cases(limit=1000, tenant_id=tenant_id)
    held = {str((case.get("shipment") or {}).get("paypal_authorization_id") or "") for case in cases}
    orders: list[dict[str, Any]] = []
    for row in rows:
        detail = row.get("detail")
        if isinstance(detail, dict) and str(detail.get("authorization_id") or "") not in held:
            orders.append({"order_id": detail.get("order_id"), "authorization_id": detail.get("authorization_id"), "at": row.get("at")})
    return orders


async def record_renewal(old_id: str, new_id: str, tenant_id: str | None = None) -> bool:
    """Remember the id PayPal issued when it reauthorized `old_id`: every later capture or void must use it. True when this call wrote it."""
    written = await get_store().add_audit_once({
        "audit_id": f"payauthn-{old_id}",
        "case_id": f"AUTHN-{old_id}",
        "action": "payment_authorization_renewed",
        "status": "done",
        "detail": {"old_authorization_id": old_id, "new_authorization_id": new_id},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return bool(written)


async def find_renewal(authorization_id: str, tenant_id: str | None = None) -> str | None:
    """The id that replaced this authorization when PayPal reauthorized it, or None when it was never renewed."""
    if not authorization_id:
        return None
    rows, _ = await get_store().query_audit(case_id=f"AUTHN-{authorization_id}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    value = detail.get("new_authorization_id") if isinstance(detail, dict) else None
    return str(value) if value else None
