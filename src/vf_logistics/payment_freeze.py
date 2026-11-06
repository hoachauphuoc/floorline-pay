"""
Freezes on the parties of a payment: a carrier or a shipper that PayPal has told us is in
trouble is paid nothing until a person has looked.

A dispute opened on a capture, a capture that PayPal denied, refunded or reversed, and a payout
that failed or came back all freeze the carrier and shipper of the case they belong to
(paypal_webhook.py, payout_tracking.py). The settler holds every release for a frozen party.
Only a governance admin signed in with a password lifts a freeze, and says why; nothing a
webhook, a model or a release decision does can lift one.

A freeze or a lift is an audit row per party, never rewritten; the newest one decides: the case id is
"PAYMENT-FREEZE-<kind>-<key>" with the party's key: its name in lower-case letters and digits, without a trailing legal form
(Co, Ltd, Company ...); freezes older than that sit under the exact spelling and are still read. A party
whose name has no usable key cannot be frozen, and the checks that need a registered carrier
already hold such a payout.
"""

from __future__ import annotations

import re
from typing import Any

from vf_logistics import carrier_registry
from vf_logistics.store import get_store, new_id, utcnow

FROZEN = "party_frozen"
LIFTED = "party_freeze_lifted"
KINDS = ("carrier", "shipper")

_PAGE = 200
_PAGES = 10


class FreezeError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


# A legal form written as a whole trailing word is not part of who the party is: "Saigon Plywood Co" and "Saigon Plywood Company Ltd"
# are one shipper. Only whole words count, so a name that merely ends in the letters "co" (Disco) keeps them.
_LEGAL_FORMS = frozenset({"co", "ltd", "inc", "llc", "jsc", "corp", "company", "limited"})


def _party_key(name: Any) -> str:
    words = re.findall(r"[a-z0-9]+", str(name or "").lower())
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return "".join(words)[:80]


def _keys(kind: str, name: Any) -> list[str]:
    """The keys a party can be kept under: the one without its legal form, then the exact-spelling one older freezes used."""
    if kind not in KINDS:
        return []
    return [key for key in dict.fromkeys((_party_key(name), carrier_registry.carrier_key(name))) if key]


async def _newest(kind: str, key: str, tenant_id: str | None) -> dict[str, Any] | None:
    # One filter only: Firestore applies just one of case_id, action and status in a query.
    # The case id holds only this party's freezes and lifts, so the newest row says which is in force.
    rows, _ = await get_store().query_audit(case_id=_case(kind, key), limit=1, tenant_id=tenant_id)
    return rows[0] if rows else None


def _rank(row: dict[str, Any]) -> tuple[str, bool]:
    """Newest first; on the same instant a freeze outranks a lift, because an unclear order must not release a payout."""
    return str(row.get("at") or ""), row.get("action") == FROZEN


def shipper_names(shipment: Any) -> list[str]:
    """Every name a case gives its shipper, once each: the booking form states a company, other intake paths a contact name.

    A freeze is looked up under each of them, so a block put on one name holds the release however the case names the shipper.
    """
    fields = shipment if isinstance(shipment, dict) else {}
    names = (str(fields.get(field) or "").strip() for field in ("shipper_company", "shipper_name"))
    return list(dict.fromkeys(name for name in names if name))


def _case(kind: str, key: str) -> str:
    return f"PAYMENT-FREEZE-{kind}-{key}"


async def freeze(
    kind: str, name: Any, reason: str, source: str, tenant_id: str | None = None,
) -> bool:
    """Freeze a party because of `source` (an event or refresh id); True when it is frozen."""
    keys = _keys(kind, name)
    if not keys:
        return False
    key = keys[0]
    await get_store().add_audit_once({
        "audit_id": f"freeze-{source}-{kind}-{key}",
        "case_id": _case(kind, key),
        "action": FROZEN,
        "status": "done",
        "detail": {"kind": kind, "party": key, "name": str(name)[:120], "reason": reason, "source": source},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return True


async def frozen(kind: str, name: Any, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Why a party is frozen, or None. The newest row of the party decides: a freeze or a lift."""
    # A party can hold rows under both keys: the newest row of either decides, so a lift under one spelling is not undone by an
    # older freeze under the other.
    newest: tuple[str, dict[str, Any]] | None = None
    for candidate in _keys(kind, name):
        row = await _newest(kind, candidate, tenant_id)
        if row is not None and (newest is None or _rank(row) > _rank(newest[1])):
            newest = (candidate, row)
    if newest is None or newest[1].get("action") != FROZEN:
        return None
    key, latest = newest
    detail = latest.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    return {
        "kind": kind, "party": key, "name": detail.get("name"),
        "reason": str(detail.get("reason") or "PayPal reported a problem"),
        "source": detail.get("source"), "at": latest.get("at"),
    }


async def lift(
    kind: str, name: Any, reason: Any, actor: str, actor_auth: str | None,
    tenant_id: str | None = None,
) -> None:
    """Lift a freeze: a person, with a reason."""
    keys = _keys(kind, name)
    if not keys:
        raise FreezeError("kind must be carrier or shipper, and the name must name a party", 400)
    why = str(reason or "").strip()
    if not why:
        raise FreezeError("a reason is required to lift a freeze", 400)
    if not actor:
        raise FreezeError("a freeze is lifted by a person; sign in with a personal account", 403)
    if await frozen(kind, name, tenant_id) is None:
        raise FreezeError("this party is not frozen", 409)
    for key in keys:
        row = await _newest(kind, key, tenant_id)
        if row is None or row.get("action") != FROZEN:
            continue
        await get_store().add_audit({
            "audit_id": new_id("audit"),
            "case_id": _case(kind, key),
            "action": LIFTED,
            "status": "done",
            "detail": {"kind": kind, "party": key, "reason": why, "lifted_by": actor, "auth": actor_auth},
            "at": utcnow(),
        }, tenant_id=tenant_id)


async def frozen_parties(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Every party that is frozen now, newest freeze first."""
    parties: dict[tuple[str, str], str] = {}
    cursor: str | None = None
    for _ in range(_PAGES):
        rows, cursor = await get_store().query_audit(
            action=FROZEN, cursor=cursor, limit=_PAGE, tenant_id=tenant_id,
        )
        for row in rows:
            detail = row.get("detail")
            detail = detail if isinstance(detail, dict) else {}
            kind, name = str(detail.get("kind") or ""), detail.get("name")
            parties.setdefault((kind, str(detail.get("party") or "")), str(name or ""))
        if not cursor:
            break
    found: list[dict[str, Any]] = []
    # By the stored key, not the stored name: the name is cut to 120 characters, and a cut name can give another key.
    for kind, party in parties:
        current = await frozen(kind, party, tenant_id)
        if current is not None:
            found.append(current)
    return sorted(found, key=lambda f: str(f.get("at") or ""), reverse=True)
