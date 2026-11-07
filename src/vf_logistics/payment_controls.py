"""
The payment controls of Floorline Pay: a pause switch and spending limits that sit outside
every model.

A model, a shipment event or a release decision can ask for a payout, but none of them can
change how much may leave or whether payments are running. Two things set that, and both
are people: an operator may pause payments, a governance admin resumes them and publishes
the limits. Stopping money is easy and starting it again is not.

The limits are per payout, per carrier per day (amount and count) and per tenant per day,
in US dollars. Until an admin publishes others, the defaults below are in force; a
published set that cannot be read back is treated as no limits at all being verifiable, and
nothing is paid. A payout above the per-payout limit is held for a person. A payout that
would pass a daily limit is retried, so it goes out when the day turns over, still
inside the limits. A payout reserves its amount before it is paid (reserve_payout), and the
reservation is counted with the paid ones, so two settlements at the same moment cannot both pass a daily limit.

A third control is how much the agent may pay by itself: nothing until a governance admin publishes a limit (agent_limit), always
below what needs a second approver. It is a ceiling and not a grant for any one payment: agent_payment.py decides each case.

Pausing blocks money going out and not money going back: a block still voids the
authorization. The switch and the limits are audit rows under the case id
"PAYMENT-CONTROLS", newest first, never rewritten.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import dual_approval
from vf_logistics.store import get_store, new_id, utcnow

CASE = "PAYMENT-CONTROLS"
PUBLISHED = "payment_limits_published"
AGENT_LIMIT = "agent_limit_published"
PAUSED = "payments_paused"
RESUMED = "payments_resumed"

DEFAULT_LIMITS: dict[str, float | int] = {
    "max_payout_usd": 5_000.0,
    "carrier_daily_usd": 10_000.0,
    "tenant_daily_usd": 50_000.0,
    "carrier_daily_count": 5,
}

_PAGE = 200
_PAGES = 10
RESERVED = "reserve_payout"
CANCELLED = "release_payout"
RESERVATIONS = "PAYOUT-RESERVATIONS"


class ControlsError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def validate_limits(raw: Any) -> dict[str, float | int]:
    """The limits as numbers, or a ControlsError that names the first thing wrong."""
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_LIMITS):
        raise ControlsError(
            "limits must state exactly: " + ", ".join(sorted(DEFAULT_LIMITS)), 400,
        )
    limits: dict[str, float | int] = {}
    for name in ("max_payout_usd", "carrier_daily_usd", "tenant_daily_usd"):
        value = _number(raw[name])
        if value is None:
            raise ControlsError(f"{name} must be a number above zero", 400)
        limits[name] = value
    count = raw["carrier_daily_count"]
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise ControlsError("carrier_daily_count must be a whole number from 1 to 1000", 400)
    limits["carrier_daily_count"] = count
    if not limits["max_payout_usd"] <= limits["carrier_daily_usd"] <= limits["tenant_daily_usd"]:
        raise ControlsError(
            "limits must satisfy max_payout_usd <= carrier_daily_usd <= tenant_daily_usd, "
            "so that one payout can always fit inside a day", 400,
        )
    return limits


# One filter only: Firestore applies just one of case_id, action and status in an audit query, so
# the rows of the case are read and the action is picked here.
async def _newest(action: str, tenant_id: str | None) -> dict[str, Any] | None:
    cursor: str | None = None
    for _ in range(_PAGES):
        rows, cursor = await get_store().query_audit(case_id=CASE, limit=_PAGE, cursor=cursor, tenant_id=tenant_id)
        found = next((r for r in rows if r.get("action") == action), None)
        if found is not None or not cursor:
            return found
    raise ControlsError("too many control changes to find the newest one; payments are held", 503)


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("detail")
    return raw if isinstance(raw, dict) else {}


async def limits(tenant_id: str | None = None) -> dict[str, float | int] | None:
    """The limits in force: the latest published set, else the defaults; None if unreadable."""
    row = await _newest(PUBLISHED, tenant_id)
    if row is None:
        return dict(DEFAULT_LIMITS)
    try:
        return validate_limits(_detail(row).get("limits"))
    except ControlsError:
        return None


async def publish_limits(
    raw: Any, reason: Any, actor: str, actor_auth: str | None, tenant_id: str | None = None,
) -> dict[str, float | int]:
    """Make a set of limits the ones in force."""
    published = validate_limits(raw)
    why = str(reason or "").strip()
    if not why:
        raise ControlsError("a reason is required to publish limits", 400)
    if not actor:
        raise ControlsError("limits are published by a person; sign in with a personal account", 403)
    await get_store().add_audit({
        "audit_id": new_id("audit"),
        "case_id": CASE,
        "action": PUBLISHED,
        "status": "done",
        "detail": {"limits": published, "reason": why, "published_by": actor, "auth": actor_auth},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return published


async def agent_limit(tenant_id: str | None = None) -> dict[str, Any] | None:
    """The limit the agent pays under, or None: none published, turned off, or a record no password sign-in stands behind."""
    row = await _newest(AGENT_LIMIT, tenant_id)
    if row is None:
        return None
    detail = _detail(row)
    try:
        limit = Decimal(str(detail.get("limit_usd")))
    except InvalidOperation:
        return None
    person = str(detail.get("published_by") or "")
    if not limit.is_finite() or limit <= 0 or not person or detail.get("auth") != "password":
        return None
    return {"limit_usd": limit, "published_by": person, "reason": detail.get("reason"), "at": row.get("at")}


def _agent_limit_amount(raw: Any) -> Decimal:
    """The amount asked for, zero meaning off, or a ControlsError that says what is wrong."""
    off = not isinstance(raw, bool) and raw == 0
    if not off and _number(raw) is None:
        raise ControlsError("limit_usd must be a number, zero or above; zero turns the agent off", 400)
    limit = Decimal(str(raw))
    threshold = dual_approval.threshold()
    if limit > 0 and limit >= threshold:
        raise ControlsError(
            f"the agent limit must stay below the second-approval threshold of {threshold} USD, "
            "so that a payment needing two people is never paid by the agent", 400,
        )
    return limit


async def publish_agent_limit(
    raw: Any, reason: Any, actor: str, actor_auth: str | None, tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Set the most the agent may pay by itself, or turn it off with zero. Returns the limit now in force."""
    limit = _agent_limit_amount(raw)
    why = str(reason or "").strip()
    if not why:
        raise ControlsError("a reason is required to publish the agent limit", 400)
    if not actor:
        raise ControlsError("the agent limit is published by a person; sign in with a personal account", 403)
    if actor_auth != "password":
        raise ControlsError("the agent limit needs a password sign-in", 403)
    await get_store().add_audit({
        "audit_id": new_id("audit"),
        "case_id": CASE,
        "action": AGENT_LIMIT,
        "status": "done",
        "detail": {"limit_usd": str(limit), "reason": why, "published_by": actor, "auth": actor_auth},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return await agent_limit(tenant_id)


async def pause_state(tenant_id: str | None = None) -> dict[str, Any]:
    """Whether payments are paused, why, and by whom: the newest pause or resume decides."""
    rows = [r for r in (await _newest(PAUSED, tenant_id), await _newest(RESUMED, tenant_id)) if r]
    if not rows:
        return {"paused": False, "reason": None, "by": None, "at": None}
    row = max(rows, key=lambda r: str(r.get("at") or ""))
    detail = _detail(row)
    return {
        "paused": row.get("action") == PAUSED,
        "reason": detail.get("reason"),
        "by": detail.get("paused_by") or detail.get("resumed_by"),
        "at": row.get("at"),
    }


async def _switch(
    action: str, who_field: str, reason: Any, actor: str, actor_auth: str | None,
    tenant_id: str | None,
) -> dict[str, Any]:
    why = str(reason or "").strip()
    if not why:
        raise ControlsError("a reason is required", 400)
    if not actor:
        raise ControlsError("payments are paused and resumed by a person; sign in with a personal account", 403)
    await get_store().add_audit({
        "audit_id": new_id("audit"),
        "case_id": CASE,
        "action": action,
        "status": "done",
        "detail": {"reason": why, who_field: actor, "auth": actor_auth},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return await pause_state(tenant_id)


async def pause(
    reason: Any, actor: str, actor_auth: str | None, tenant_id: str | None = None,
) -> dict[str, Any]:
    return await _switch(PAUSED, "paused_by", reason, actor, actor_auth, tenant_id)


async def resume(
    reason: Any, actor: str, actor_auth: str | None, tenant_id: str | None = None,
) -> dict[str, Any]:
    return await _switch(RESUMED, "resumed_by", reason, actor, actor_auth, tenant_id)


def _amount(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


async def usage_today(tenant_id: str | None = None) -> dict[str, Any]:
    """What has been paid since midnight UTC: in all, and per carrier (amount and count)."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    total = Decimal(0)
    carriers: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    for _ in range(_PAGES):
        rows, cursor = await get_store().query_audit(
            action="settle_payment", status="settled", cursor=cursor, limit=_PAGE,
            tenant_id=tenant_id,
        )
        for row in rows:
            if str(row.get("at") or "") < start:
                continue
            detail = _detail(row)
            paid = _amount(detail.get("amount"))
            if paid is None:
                continue
            total += paid
            entry = carriers.setdefault(str(detail.get("carrier_key") or ""), {"usd": Decimal(0), "count": 0})
            entry["usd"] += paid
            entry["count"] += 1
        if not cursor or (rows and str(rows[-1].get("at") or "") < start):
            return {"tenant_usd": total, "carriers": carriers}
    raise ControlsError("too many payments today to total them", 503)


def _daily_refusal(
    live: dict[str, float | int], used: dict[str, Any], carrier_key: str, payout: Decimal,
) -> tuple[str, str] | None:
    """("retry", reason) when the payout would pass a daily limit given what is already counted, else None."""
    mine = used["carriers"].get(carrier_key, {"usd": Decimal(0), "count": 0})
    if mine["count"] >= int(live["carrier_daily_count"]):
        return "retry", (
            f"this carrier has reached its limit of {live['carrier_daily_count']} payouts today; "
            "the decision is retried when the day turns over"
        )
    if mine["usd"] + payout > Decimal(str(live["carrier_daily_usd"])):
        return "retry", (
            f"this payout would take the carrier past its daily limit of "
            f"{live['carrier_daily_usd']} USD; the decision is retried when the day turns over"
        )
    if used["tenant_usd"] + payout > Decimal(str(live["tenant_daily_usd"])):
        return "retry", (
            f"this payout would take today's payments past the daily limit of "
            f"{live['tenant_daily_usd']} USD; the decision is retried when the day turns over"
        )
    return None


async def _rows_today(action: str, status: str, tenant_id: str | None) -> list[dict[str, Any]]:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_PAGES):
        rows, cursor = await get_store().query_audit(
            action=action, status=status, cursor=cursor, limit=_PAGE, tenant_id=tenant_id,
        )
        found.extend(row for row in rows if str(row.get("at") or "") >= start)
        if not cursor or (rows and str(rows[-1].get("at") or "") < start):
            return found
    raise ControlsError("too many payments today to total them", 503)


async def _open_reservations(tenant_id: str | None) -> list[dict[str, Any]]:
    """Today's reservations that are neither paid (the paid row counts them) nor given up."""
    paid = {str(_detail(row).get("idempotency_key") or "") for row in await _rows_today("settle_payment", "settled", tenant_id)}
    given_up = {str(_detail(row).get("reservation_id") or "") for row in await _rows_today(CANCELLED, "cancelled", tenant_id)}
    return [
        row for row in await _rows_today(RESERVED, "reserved", tenant_id)
        if str(_detail(row).get("idempotency_key") or "") not in paid and str(row.get("audit_id") or "") not in given_up
    ]


async def cancel_reservations(key: str, tenant_id: str | None = None) -> None:
    """Give up every open reservation of this decision, so its amount stops counting."""
    for row in await _open_reservations(tenant_id):
        if str(_detail(row).get("idempotency_key") or "") != key:
            continue
        await get_store().add_audit_once({
            "audit_id": f"cancel-{row['audit_id']}",
            "case_id": RESERVATIONS,
            "action": CANCELLED,
            "status": "cancelled",
            "detail": {"reservation_id": row["audit_id"], "idempotency_key": key},
            "at": utcnow(),
        }, tenant_id=tenant_id)


async def reserve_payout(
    key: str, payout: Decimal, carrier_key: str, tenant_id: str | None = None,
) -> tuple[str, str] | None:
    """
    Reserve this payout's amount, then check the daily limits against what is paid and what others have
    reserved. None when it fits; else (status, reason) and a reservation this call made is given up again. The reservation is
    written before the totals are read, so of two settlements at the same moment the later one sees the earlier one.
    """
    if not key:
        return "held", "the payout has no decision key to reserve it under; nothing is paid"
    live = await limits(tenant_id)
    if live is None:
        return "held", "the published payment limits cannot be read; nothing is paid"
    open_now = await _open_reservations(tenant_id)
    # A reservation still open from an earlier delivery may stand for a payout PayPal holds, so only the settler may give it up.
    kept = any(str(_detail(row).get("idempotency_key") or "") == key for row in open_now)
    if not kept:
        await get_store().add_audit({
            "audit_id": new_id("RSV"),
            "case_id": RESERVATIONS,
            "action": RESERVED,
            "status": "reserved",
            "detail": {"amount": str(payout), "carrier_key": carrier_key, "idempotency_key": key},
            "at": utcnow(),
        }, tenant_id=tenant_id)
    used = await usage_today(tenant_id)
    for row in await _open_reservations(tenant_id):
        detail = _detail(row)
        if str(detail.get("idempotency_key") or "") == key:
            continue
        amount = _amount(detail.get("amount"))
        if amount is None:
            continue
        used["tenant_usd"] += amount
        entry = used["carriers"].setdefault(str(detail.get("carrier_key") or ""), {"usd": Decimal(0), "count": 0})
        entry["usd"] += amount
        entry["count"] += 1
    refusal = _daily_refusal(live, used, carrier_key, payout)
    if refusal is not None and not kept:
        await cancel_reservations(key, tenant_id)
    return refusal


async def check_payout(
    currency: str, payout: Decimal | None, carrier_key: str, tenant_id: str | None = None,
    tier_cap: Decimal | None = None, tier: str = "", key: str = "",
) -> tuple[str, str] | None:
    """
    None when the payout fits the limits, else (status, reason): "held" for one that no
    wait can fix, "retry" for one that fits tomorrow.
    """
    if payout is None:
        return "held", "the payout amount is not a positive number; nothing is paid"
    if currency != "USD":
        return "held", f"the limits are in USD and the payout is in {currency}; nothing is paid"
    live = await limits(tenant_id)
    if live is None:
        return "held", "the published payment limits cannot be read; nothing is paid"
    if payout > Decimal(str(live["max_payout_usd"])):
        return "held", (
            f"the payout of {payout} USD is above the per-payout limit of "
            f"{live['max_payout_usd']} USD; nothing is paid"
        )
    if tier_cap is not None and payout > tier_cap:
        return "held", (
            f"the payout of {payout} USD is above the {tier_cap} USD that credit tier {tier} allows this carrier; "
            "it is paid once the carrier has a record of being paid without trouble; nothing is paid"
        )
    refusal = _daily_refusal(live, await usage_today(tenant_id), carrier_key, payout)
    if refusal is not None or not key:
        return refusal
    # A payout that fits is reserved under its decision key before any money moves, so a settlement running at the same
    # moment counts it; the settler gives the reservation up when nothing was paid.
    return await reserve_payout(key, payout, carrier_key, tenant_id)
