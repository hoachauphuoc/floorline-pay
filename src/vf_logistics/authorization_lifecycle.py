"""
Where a PayPal authorization is in its life, read from what PayPal itself says about it.

PayPal guarantees the funds of an authorization for a three-day honor period. After that a
capture may fail for want of funds until the authorization is renewed (reauthorized), and
after 29 days it is gone. The settler asks for this before it captures: inside the honor
period it captures, between three and 29 days it renews first, and past 29 days, or when the
reply gives no time it can read, it pays nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

HONORED = "honored"
REAUTHORIZE = "reauthorize"
EXPIRED = "expired"
UNKNOWN = "unknown"

HONOR_PERIOD = timedelta(days=3)
EXPIRY = timedelta(days=29)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def stage(facts: dict[str, Any], now: datetime | None = None) -> str:
    """The stage of an authorization from PayPal's create_time and expiration_time."""
    created = _time(facts.get("create_time"))
    if created is None:
        return UNKNOWN
    moment = now or datetime.now(timezone.utc)
    expires = _time(facts.get("expiration_time"))
    if moment >= created + EXPIRY or (expires is not None and moment >= expires):
        return EXPIRED
    return REAUTHORIZE if moment >= created + HONOR_PERIOD else HONORED
