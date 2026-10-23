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
inside the limits. A payout is counted once it has been paid, so two settlements at the very
same moment could together pass a daily limit by one payout.

Pausing blocks money going out and not money going back: a block still voids the
authorization. The switch and the limits are audit rows under the case id
"PAYMENT-CONTROLS", newest first, never rewritten.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics.store import get_store, new_id, utcnow

CASE = "PAYMENT-CONTROLS"
PUBLISHED = "payment_limits_published"
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


class ControlsError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


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


async def _newest(action: str, tenant_id: str | None) -> dict[str, Any] | None:
    rows, _ = await get_store().query_audit(
        case_id=CASE, action=action, limit=1, tenant_id=tenant_id,
    )
    return rows[0] if rows else None


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


async def check_payout(
    currency: str, payout: Decimal | None, carrier_key: str, tenant_id: str | None = None,
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
    used = await usage_today(tenant_id)
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
