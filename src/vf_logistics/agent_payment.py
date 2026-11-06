"""
When the agent may pay a clean case by itself, inside a limit a person published.

A clean payment case is paid without a person only if every condition below holds; one that is missing, unreadable or not what it must
be is a refusal, and the case goes to a person. The analysis asks before it auto-clears a case, and the settler asks again before any
money moves, because a limit can be withdrawn and a standing can change in between. No model is called here: what the analyst and the
web check said is read from the case, and an answer can only refuse. The permission itself is the person's limit
(payment_controls.agent_limit), and every check a person's release goes through still runs in the settler after this one.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import credit_profile, dual_approval, payment_checks, payment_controls

OUTCOME = "AUTO_CLEARED"
# The state of a case while the agent's decision on it is going out: the analysis publishes first and stores second.
DECIDING = "SPECIALISTS_DONE"


def key_for(case_id: str) -> str:
    """The idempotency key of the agent's decision: fixed per case, so a repeat can never pay twice."""
    return f"agent-{case_id}"


def _part(parent: Any, name: str) -> dict[str, Any]:
    value = parent.get(name) if isinstance(parent, dict) else None
    return value if isinstance(value, dict) else {}


def _payout(shipment: dict[str, Any]) -> Decimal | None:
    try:
        amount = Decimal(str(shipment.get("payout_amount")))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _sure_it_is_normal(analyst: dict[str, Any]) -> bool:
    confidence = analyst.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    return analyst.get("verdict") == "normal" and confidence >= payment_checks.AI_MIN_CONFIDENCE


async def _refusals(
    case: dict[str, Any], shipment: dict[str, Any], limit: dict[str, Any], tenant_id: str | None,
) -> list[str]:
    found: list[str] = []
    ai = case.get("payment_ai")
    if not isinstance(ai, dict):
        found.append("the case holds no payment analysis")
    who = _part(ai, "identity")
    if not _sure_it_is_normal(_part(ai, "analyst")):
        found.append("the analyst did not read this payout as normal with confidence")
    if who.get("status") != "found":
        found.append("the web check did not find the carrier")
    if _part(case, "validation").get("findings") != []:
        found.append("the checks raised findings or left no record of them")
    if _part(case, "reconciliation").get("auto_clear_permitted") is not True:
        found.append("the deterministic floor did not permit an automated decision")
    if str(shipment.get("currency") or "USD") != "USD":
        found.append("the agent limit is in USD and this payout is not")
    payout = _payout(shipment)
    if payout is None:
        found.append("the payout amount cannot be read")
    elif payout >= limit["limit_usd"]:
        found.append(f"the payout of {payout} USD is not below the agent limit of {limit['limit_usd']} USD")
    if dual_approval.needed(shipment):
        found.append("the payout needs a second approver")
    standing = await credit_profile.profile(shipment.get("carrier_name"), ai, tenant_id)
    tier = standing.get("tier")
    if tier != "A":
        found.append(f"the carrier's standing is {tier}, and the agent pays only standing A")
    # Standing A is three payouts PayPal confirmed, the record payment_ai.payee_proven asks for; a payee changed since is unproven.
    proven = tier == "A" and not shipment.get("carrier_payee_changed_at")
    if who.get("payee_site") != "on_site" and not proven:
        found.append("the payee is neither on the carrier's website nor proven by three confirmed payouts to it")
    return found


async def review(
    case: dict[str, Any], shipment: dict[str, Any], tenant_id: str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """The limit in force and why the agent may not pay this case; an empty list is the only yes. Never raises."""
    try:
        limit = await payment_controls.agent_limit(tenant_id)
        if limit is None:
            return None, ["no person has published an agent limit"]
        return limit, await _refusals(case, shipment, limit, tenant_id)
    except Exception as exc:  # noqa: BLE001 - a record that cannot be read is a refusal, never a pass
        return None, [f"the agent limit or the carrier's record could not be read ({type(exc).__name__})"]
