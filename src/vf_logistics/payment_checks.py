"""
Deterministic payment checks for Floorline Pay, run by verifier.validate().

They answer one question before a carrier is paid: is the money going to the party
that did the work, in the amount that was booked? The pattern they target is the
vendor-bank-change fraud behind most freight payment diversion: the carrier's payee
is switched shortly before a payout, or the payout is inflated above the booked
freight.

They run only on a payment case, a shipment that carries a payout amount, a PayPal
authorization or a carrier payee. Every other shipment gets no findings, so the
floors of cases that move no money are unchanged.

Inside a payment case they fail closed: a payee, a freight figure or a change time
that is absent or unreadable is a finding, never a pass. No detail quotes a payee
address: the findings reach the model in the debate prompt, and a payee address is
the one value an attacker wants a model to repeat back as verified.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

PAYMENT_FIELDS = ("payout_amount", "paypal_authorization_id", "carrier_payee_email")

# A payee changed this shortly before a payout is the signature of payment diversion:
# the account is switched first, and the next invoice is paid into it.
PAYEE_CHANGE_WINDOW = timedelta(days=14)


def is_payment_case(shipment: dict[str, Any]) -> bool:
    return any(shipment.get(field) not in (None, "") for field in PAYMENT_FIELDS)


def _amount(value: Any) -> float | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def _finding(code: str, severity: str, floor: int, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "floor": floor,
        "blocks_auto_clear": True,
        "detail": detail,
    }


def _check_payee(shipment: dict[str, Any]) -> list[dict[str, Any]]:
    payee = str(shipment.get("carrier_payee_email") or "").strip().lower()
    registered = str(shipment.get("carrier_registered_payee_email") or "").strip().lower()
    if not payee:
        return [_finding(
            "PAYEE_MISSING", "HIGH", 75,
            "The payout names no carrier payee, so there is no account to verify.",
        )]
    if not registered:
        return [_finding(
            "PAYEE_UNVERIFIED", "MEDIUM", 45,
            "No registered payee is on file for this carrier, so the payee cannot be checked.",
        )]
    if payee != registered:
        return [_finding(
            "PAYEE_MISMATCH", "HIGH", 80,
            "The payee on this payout is not the payee your records hold for this carrier.",
        )]
    return []


def _check_payee_change(shipment: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    raw = shipment.get("carrier_payee_changed_at")
    if raw in (None, ""):
        return []
    try:
        changed: datetime | None = datetime.fromisoformat(str(raw))
    except ValueError:
        changed = None
    if changed is None or changed.tzinfo is None:
        return [_finding(
            "PAYEE_CHANGE_TIME_UNREADABLE", "MEDIUM", 45,
            "The payee change time cannot be read, so a recent change cannot be ruled out.",
        )]
    if changed > now:
        return [_finding(
            "PAYEE_RECENTLY_CHANGED", "HIGH", 70,
            "The carrier's payee change is dated in the future.",
        )]
    if now - changed <= PAYEE_CHANGE_WINDOW:
        days = (now - changed).days
        finding = _finding(
            "PAYEE_RECENTLY_CHANGED", "HIGH", 70,
            f"The carrier's payee was changed {days} day(s) before this payout.",
        )
        finding["measured"] = {"days_since_payee_change": days}
        return [finding]
    return []


def _check_change_request(shipment: dict[str, Any]) -> list[dict[str, Any]]:
    # Set only by carrier_registry.enrich from the screen of the request that put the payee on
    # file, never by the sender.
    if shipment.get("carrier_payee_change_flagged") is not True:
        return []
    return [_finding(
        "PAYEE_CHANGE_REQUEST_FLAGGED", "HIGH", 75,
        "The request that put this payee on file carried wording typical of payment-diversion fraud; "
        "register and verify the payee again through a callback.",
    )]


def _check_amount(shipment: dict[str, Any]) -> list[dict[str, Any]]:
    raw = shipment.get("payout_amount")
    if raw in (None, ""):
        return [_finding(
            "PAYOUT_AMOUNT_MISSING", "MEDIUM", 45,
            "The payment case states no payout amount, so the amount cannot be checked.",
        )]
    payout = _amount(raw)
    if payout is None:
        return [_finding(
            "PAYOUT_AMOUNT_UNREADABLE", "MEDIUM", 45,
            "The payout amount is not a positive number.",
        )]
    freight = _amount(shipment.get("shipping_cost")) or _amount(shipment.get("freight_cost"))
    if freight is None:
        return [_finding(
            "PAYOUT_FREIGHT_UNVERIFIED", "MEDIUM", 45,
            "No booked freight figure is on the shipment, so the payout cannot be checked against it.",
        )]
    if payout > freight + 0.005:
        finding = _finding(
            "PAYOUT_ABOVE_FREIGHT", "HIGH", 75,
            f"The payout of {payout:.2f} is above the booked freight of {freight:.2f}.",
        )
        finding["measured"] = {"payout_amount": payout, "booked_freight": freight}
        return [finding]
    return []


# An analyst verdict below this confidence adds nothing.
AI_MIN_CONFIDENCE = 0.6


def check_payment_ai(shipment: dict[str, Any], ai: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    Findings from the payment-risk analyst and the carrier look-up (payment_ai.py).

    They can only add findings: a verdict of "normal" or "unknown", a weak "elevated", an answer
    that cannot be read and no answer at all add none and take none away. A look-up that did not
    run is a finding, not a pass: an outage is not evidence that the carrier exists.
    """
    if not isinstance(ai, dict) or not is_payment_case(shipment):
        return []
    findings: list[dict[str, Any]] = []
    analyst = ai.get("analyst")
    if isinstance(analyst, dict) and analyst.get("verdict") == "elevated":
        try:
            confidence = float(analyst.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence >= AI_MIN_CONFIDENCE:
            raw = analyst.get("reasons")
            reasons = "; ".join(str(r) for r in (raw if isinstance(raw, list) else [])[:3])
            finding = _finding(
                "PAYMENT_AI_ELEVATED", "MEDIUM", 55,
                "An automated analyst rated this payout as elevated risk" + (f": {reasons}." if reasons else "."),
            )
            finding["measured"] = {"confidence": confidence}
            findings.append(finding)
    who = ai.get("identity")
    status = who.get("status") if isinstance(who, dict) else None
    if status == "unverified":
        findings.append(_finding(
            "CARRIER_IDENTITY_UNVERIFIED", "MEDIUM", 50,
            "The web check of the carrier did not run, so nothing confirms that the carrier exists.",
        ))
    elif status == "not_found":
        findings.append(_finding(
            "CARRIER_NOT_FOUND_ONLINE", "HIGH", 65,
            "A web search found nothing that names this carrier.",
        ))
    return findings


def check_payment(
    shipment: dict[str, Any], now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Findings for a payment case; an empty list for a shipment that moves no money."""
    if not is_payment_case(shipment):
        return []
    now = now or datetime.now(timezone.utc)
    return (
        _check_payee(shipment) + _check_payee_change(shipment, now) + _check_amount(shipment)
        + _check_change_request(shipment)
    )
