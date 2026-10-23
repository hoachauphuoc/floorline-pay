"""
The carrier payee registry of Floorline Pay: the only source of who a carrier may be paid.

A shipment event states its own payee, its own "payee on file" and its own change date, and
all three come from whoever sent the event, so a check built on them can be satisfied by the
sender. This registry is kept on the server and changes only through two steps by two people:
one registers a payee, a different one verifies it by calling the carrier back and records
what they were told. Until a payee is verified it is not the carrier's payee, and the old
one stays in force.

Before a payment is judged (at intake, when the case page shows its findings, and again when
the settler is about to pay) enrich() overwrites the fields the sender must not state with
what the registry says, so a claim in the event cannot stand in for the record. A payee
changed through the registry carries the time the change was verified, which is what the
payment checks' cooling-off window counts from.

The registry is event-sourced on audit rows under the case id "CARRIER-<key>", written with
ids derived from the carrier and version so a step happens once and nothing is rewritten.
The carrier is identified by its name with everything but letters and digits dropped, so a
name that differs after that is another carrier and is held as unverified.
"""

from __future__ import annotations

import re
from typing import Any

from vf_logistics import change_screen, payment_checks
from vf_logistics.store import get_store, utcnow

REGISTERED = "carrier_payee_registered"
VERIFIED = "carrier_payee_verified"

_TRIES = 5
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_REGISTERED_FIELD = "carrier_registered_payee_email"
_CHANGED_FIELD = "carrier_payee_changed_at"
_REQUESTED_FIELD = "carrier_payee_email"
_FLAGGED_FIELD = "carrier_payee_change_flagged"


class RegistryError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def carrier_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())[:80]


def _case(key: str) -> str:
    return f"CARRIER-{key}"


def _state(
    rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    registered: dict[int, dict[str, Any]] = {}
    verified: dict[int, dict[str, Any]] = {}
    for row in rows:
        raw = row.get("detail")
        detail: dict[str, Any] = raw if isinstance(raw, dict) else {}
        version = detail.get("version")
        if not isinstance(version, int):
            continue
        entry = {**detail, "at": row.get("at")}
        if row.get("action") == REGISTERED:
            registered[version] = entry
        elif row.get("action") == VERIFIED:
            verified[version] = entry
    return registered, verified


async def _events(key: str, tenant_id: str | None) -> list[dict[str, Any]]:
    rows, _ = await get_store().query_audit(case_id=_case(key), limit=200, tenant_id=tenant_id)
    return list(rows)


def _current(
    registered: dict[int, dict[str, Any]], verified: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    versions = [v for v in verified if v in registered]
    if not versions:
        return None
    version = max(versions)
    reg, ver = registered[version], verified[version]
    return {
        "carrier_key": reg.get("carrier_key"),
        "carrier_name": reg.get("carrier_name"),
        "version": version,
        "payee_email": reg.get("payee_email"),
        "registered_by": reg.get("registered_by"),
        "registered_at": reg.get("at"),
        "verified_by": ver.get("verified_by"),
        "verified_at": ver.get("at"),
        "changed_at": ver.get("at") if reg.get("previous_payee_email") else None,
        "change_request_flagged": bool((reg.get("screen") or {}).get("flagged")),
    }


async def current(name: Any, tenant_id: str | None = None) -> dict[str, Any] | None:
    """The carrier's verified payee, or None when it has none."""
    key = carrier_key(name)
    if not key:
        return None
    registered, verified = _state(await _events(key, tenant_id))
    return _current(registered, verified)


async def register_payee(
    name: Any, payee_email: Any, actor: str, actor_auth: str | None, tenant_id: str | None = None,
    request_text: Any = None,
) -> dict[str, Any]:
    """Record a payee for a carrier, pending until a different person verifies it."""
    key = carrier_key(name)
    if not key:
        raise RegistryError("a carrier name is required to register a payee", 400)
    payee = str(payee_email or "").strip().lower()
    if not _EMAIL.match(payee):
        raise RegistryError("the payee must be an email address such as carrier@example.com", 400)
    if not actor:
        raise RegistryError("a payee is recorded against a person; sign in with a personal account", 403)
    registered, verified = _state(await _events(key, tenant_id))
    live = _current(registered, verified)
    if live is not None and live["payee_email"] == payee:
        raise RegistryError("this is already the verified payee of this carrier", 409)
    screened = await change_screen.screen(request_text) if str(request_text or "").strip() else None
    version = max(registered, default=0) + 1
    for _ in range(_TRIES):
        written = await get_store().add_audit_once({
            "audit_id": f"carrier-{key}-v{version}",
            "case_id": _case(key),
            "action": REGISTERED,
            "status": "pending",
            "detail": {
                "carrier_key": key,
                "carrier_name": str(name).strip(),
                "version": version,
                "payee_email": payee,
                "registered_by": actor,
                "registered_auth": actor_auth,
                "previous_payee_email": live["payee_email"] if live else None,
                "screen": screened,
            },
            "at": utcnow(),
        }, tenant_id=tenant_id)
        if written:
            return {"carrier_key": key, "version": version, "status": "pending_verification"}
        version += 1
    raise RegistryError("another registration was recorded at the same moment; try again", 409)


async def verify_payee(
    key: str, version: int, actor: str, actor_auth: str | None, callback_note: Any,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Make a registered payee the carrier's payee, as a person other than its registrant."""
    note = str(callback_note or "").strip()
    if not note:
        raise RegistryError(
            "a callback note is required: who was called, on what number, and what they confirmed", 400,
        )
    if not actor:
        raise RegistryError("a verification is recorded against a person; sign in with a personal account", 403)
    registered, verified = _state(await _events(key, tenant_id))
    if version not in registered:
        raise RegistryError("no such registration for this carrier", 404)
    if version in verified:
        raise RegistryError("this registration is already verified", 409)
    if str(registered[version].get("registered_by") or "").lower() == actor.lower():
        raise RegistryError("a payee must be verified by a different person than the one who registered it", 403)
    written = await get_store().add_audit_once({
        "audit_id": f"carrier-{key}-v{version}-verified",
        "case_id": _case(key),
        "action": VERIFIED,
        "status": "done",
        "detail": {
            "carrier_key": key,
            "version": version,
            "verified_by": actor,
            "verified_auth": actor_auth,
            "callback_note": note,
        },
        "at": utcnow(),
    }, tenant_id=tenant_id)
    if not written:
        raise RegistryError("this registration is already verified", 409)
    return {"carrier_key": key, "version": version, "status": "verified"}


async def carriers(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Every carrier with a registration: its verified payee and the registrations still pending."""
    rows, _ = await get_store().query_audit(action=REGISTERED, limit=200, tenant_id=tenant_id)
    keys: list[str] = []
    for row in rows:
        raw = row.get("detail")
        detail: dict[str, Any] = raw if isinstance(raw, dict) else {}
        key = str(detail.get("carrier_key") or "")
        if key and key not in keys:
            keys.append(key)
    listing: list[dict[str, Any]] = []
    for key in keys:
        registered, verified = _state(await _events(key, tenant_id))
        pending = [
            {
                "version": v,
                "payee_email": reg.get("payee_email"),
                "registered_by": reg.get("registered_by"),
                "change_request_flagged": bool((reg.get("screen") or {}).get("flagged")),
                "change_request_reasons": list((reg.get("screen") or {}).get("reasons") or []),
                "registered_at": reg.get("at"),
            }
            for v, reg in sorted(registered.items()) if v not in verified
        ]
        any_reg = next(iter(registered.values()), {})
        listing.append({
            "carrier_key": key,
            "carrier_name": any_reg.get("carrier_name"),
            "current": _current(registered, verified),
            "pending": pending,
        })
    return listing


async def enrich(shipment: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
    """
    Put the registry's word where the sender's claim was, on a payment shipment, in place.

    The payee on file and the date it changed are removed and set again from the registry,
    so a sender who states them gets nothing for it, and a carrier the registry does not
    know is left with no payee on file, which the payment checks hold as unverified. A
    requested payee is kept, so a request for any other account is seen as a mismatch;
    when none was requested the registry's payee is used. A shipment that moves no money
    is returned as it was.
    """
    if not payment_checks.is_payment_case(shipment):
        return shipment
    shipment.pop(_REGISTERED_FIELD, None)
    shipment.pop(_CHANGED_FIELD, None)
    shipment.pop(_FLAGGED_FIELD, None)
    live = await current(shipment.get("carrier_name"), tenant_id=tenant_id)
    if live is None:
        return shipment
    shipment[_REGISTERED_FIELD] = live["payee_email"]
    if live["change_request_flagged"]:
        shipment[_FLAGGED_FIELD] = True
    if live["changed_at"]:
        shipment[_CHANGED_FIELD] = live["changed_at"]
    if not str(shipment.get(_REQUESTED_FIELD) or "").strip():
        shipment[_REQUESTED_FIELD] = live["payee_email"]
    return shipment
