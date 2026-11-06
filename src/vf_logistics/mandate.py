"""
The mandate a shipper gives with a booking: what Floorline may pay out of this money, to whom, and until when.

It follows the shape of an AP2 Intent Mandate (a signed record of the limits a person sets before an agent acts for them:
a budget, who may be paid, a time window), and says no more than that. PayPal describes AP2 mandates as planned
capabilities, not a published API, and nothing here is one: the mandate is signed by this server with HMAC-SHA256 under
VF_MANDATE_SECRET, not by the shipper's device, and it is not a W3C Verifiable Credential. What it adds is real: the
shipper's limits travel with the booking and the settler refuses to pay outside them.

A mandate is issued when the order is created. Its id goes to PayPal in the order's custom_id, and PayPal echoes it when
the order is authorized, so the booking record that the settler trusts names the mandate. At settlement the signature, the
expiry, the shipment it was issued for, the carrier and the amount are all checked; a mandate that cannot be found or
verified, or a secret that is not set, means the payout is held, never paid. A booking without a mandate is judged by the
other layers as before.

The settler only reads. A mandate can narrow what is paid and never widen it: the limits a person publishes and the
credit standing still apply on top.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import carrier_registry
from vf_logistics.store import get_store, utcnow

ACTION = "payment_mandate_issued"
MAX_DAYS = 30


class MandateError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def _secret() -> bytes | None:
    value = os.getenv("VF_MANDATE_SECRET", "").strip()
    return value.encode() if len(value) >= 16 else None


def _sign(payload: dict[str, Any], secret: bytes) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def issue(
    shipper: str, max_payout_usd: Any, carriers: Any, valid_days: Any, reference_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A signed Intent Mandate for one shipment, or a MandateError that says what is wrong."""
    secret = _secret()
    if secret is None:
        raise MandateError("mandates cannot be signed here: VF_MANDATE_SECRET (16 characters or more) is not set", 503)
    if not shipper:
        raise MandateError("a mandate is given by a person; sign in with a personal account", 403)
    try:
        ceiling = Decimal(str(max_payout_usd))
    except InvalidOperation:
        ceiling = Decimal(0)
    if not ceiling.is_finite() or ceiling <= 0:
        raise MandateError("max_payout_usd must be a number above zero", 400)
    if ceiling > Decimal(10) ** 9:
        raise MandateError("max_payout_usd is above the most a mandate may name (1000000000 USD)", 400)
    keys = sorted({carrier_registry.carrier_key(c) for c in carriers} - {""}) if isinstance(carriers, list) else []
    if not keys:
        raise MandateError("a mandate names the carriers that may be paid", 400)
    if isinstance(valid_days, bool) or not isinstance(valid_days, int) or not 1 <= valid_days <= MAX_DAYS:
        raise MandateError(f"valid_days must be a whole number from 1 to {MAX_DAYS}", 400)
    if not reference_id:
        raise MandateError("a mandate is given for one shipment: reference_id is required", 400)
    moment = now or datetime.now(timezone.utc)
    payload = {
        "type": "intent_mandate",
        "mandate_id": f"MND-{uuid.uuid4().hex[:16].upper()}",
        "shipper": shipper,
        "reference_id": reference_id,
        "currency": "USD",
        "max_payout_usd": f"{ceiling:.2f}",
        "carriers": keys,
        "issued_at": moment.isoformat(),
        "expires_at": (moment + timedelta(days=valid_days)).isoformat(),
    }
    return {"mandate": payload, "signature": _sign(payload, secret)}


def refusal(
    signed: Any, payout: Decimal | None, carrier_key: str, reference_id: str, now: datetime | None = None,
) -> str | None:
    """Why this payout is outside the mandate, or None when it is inside and the signature holds."""
    secret = _secret()
    if secret is None:
        return "mandates cannot be verified here: VF_MANDATE_SECRET is not set; nothing is paid"
    payload = signed.get("mandate") if isinstance(signed, dict) else None
    signature = signed.get("signature") if isinstance(signed, dict) else None
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return "the mandate of this booking is unreadable; nothing is paid"
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return "the mandate of this booking does not carry a valid signature; nothing is paid"
    try:
        expires = datetime.fromisoformat(str(payload.get("expires_at")))
        ceiling = Decimal(str(payload.get("max_payout_usd")))
    except (ValueError, InvalidOperation):
        return "the mandate of this booking is unreadable; nothing is paid"
    if expires.tzinfo is None or (now or datetime.now(timezone.utc)) >= expires:
        return "the shipper's mandate has expired; nothing is paid"
    if payload.get("reference_id") != reference_id:
        return "the mandate was given for another shipment; nothing is paid"
    if carrier_key not in (payload.get("carriers") or []):
        return "the carrier is not one the shipper's mandate allows; nothing is paid"
    if payout is None or payout > ceiling:
        return f"the payout is above the {ceiling:.2f} USD the shipper's mandate allows; nothing is paid"
    return None


async def record(signed: dict[str, Any], tenant_id: str | None = None) -> bool:
    """Keep a signed mandate under its id. True when this call wrote it."""
    mandate_id = str(signed["mandate"]["mandate_id"])
    return bool(await get_store().add_audit_once({
        "audit_id": f"mandate-{mandate_id}",
        "case_id": f"MANDATE-{mandate_id}",
        "action": ACTION,
        "status": "done",
        "detail": signed,
        "at": utcnow(),
    }, tenant_id=tenant_id))


async def load(mandate_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """The signed mandate with this id, or None."""
    if not mandate_id:
        return None
    rows, _ = await get_store().query_audit(case_id=f"MANDATE-{mandate_id}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    return dict(detail) if isinstance(detail, dict) else None


async def refusal_for(
    mandate_id: str, payout: Decimal | None, carrier_key: str, reference_id: str, tenant_id: str | None = None,
) -> str | None:
    """refusal() for the mandate a booking names; a mandate that cannot be found is a refusal."""
    signed = await load(mandate_id, tenant_id)
    if signed is None:
        return "the mandate this booking names cannot be found; nothing is paid"
    return refusal(signed, payout, carrier_key, reference_id)
