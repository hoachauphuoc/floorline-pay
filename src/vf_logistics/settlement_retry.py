"""
Send a release whose settlement failed to the settler again.

A settlement that ended "failed" (PayPal refused the capture or the payout) is not final: delivered again, the settler runs
every step and every check, and each PayPal call carries the request id of the first attempt. This module does not settle
anything. It publishes the decision to the topic again, under its first key, and the receiver and the settler take it from
there exactly as they took the first release, so money still moves only in the settler. A person signed in with a password
asks for it and gives a reason; the record is written before the message is sent, so no retry leaves without one. Only a
release whose newest settlement is failed, and which never settled, is sent again.
"""

from __future__ import annotations

from typing import Any

from vf_logistics import settler, tools
from vf_logistics.evidence import published_message_id
from vf_logistics.store import get_store, utcnow

ACTION = "retry_settlement"
RELEASE = "RELEASED_BY_HUMAN"
NOT_SENT = "the decision could not be sent again; nothing moved; try again"
HUMAN_ACTION = {"RELEASED_BY_HUMAN": "release", "BLOCKED_BY_HUMAN": "block"}


class RetryError(Exception):
    """A refusal that names what is wrong and carries the HTTP status to answer with."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


async def retry(case_id: str, reason: Any, actor: str, tenant_id: str | None = None) -> dict[str, Any]:
    """Publish this case's failed release again and record who asked and why."""
    if not actor:
        raise RetryError("a settlement is retried by a person signed in with a password; nothing is sent", 403)
    note = str(reason or "").strip()
    if not note:
        raise RetryError("a reason is required to retry a settlement: say what you checked", 400)
    rows, _ = await get_store().query_audit(case_id=case_id, action="settle_payment", limit=100, tenant_id=tenant_id)
    if not rows:
        return await _send_unsent(case_id, note, actor, tenant_id)
    newest = rows[0] if rows else None
    if (
        newest is None
        or newest.get("status") != settler.FAILED
        or not isinstance(newest.get("detail"), dict)
        or any(row.get("status") == settler.SETTLED for row in rows)
    ):
        raise RetryError("only a settlement that ended failed can be retried; this case has none", 409)
    detail = newest["detail"]
    key = str(detail.get("idempotency_key") or "")
    if detail.get("outcome") != RELEASE or not key:
        raise RetryError("only the settlement of a release can be retried; nothing is sent", 409)
    first = await tools._earlier_publish(case_id, key, tenant_id)
    original = first.get("detail") if first else None
    approver = str(original.get("approver") or "") if isinstance(original, dict) else ""
    if not isinstance(original, dict) or not approver:
        raise RetryError("the first publish of this release does not name who approved it, so nothing was sent; check the case history with an admin", 409)
    await get_store().add_audit({
        "case_id": case_id,
        "action": ACTION,
        "status": "requested",
        "detail": {"reason": note, "retried_by": actor, "idempotency_key": key},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    dead_batch = str(detail.get("dead_batch") or "")
    if dead_batch:
        # The settler sends a new payout only on this row, and only while PayPal still shows the batch as dead.
        await get_store().add_audit_once({
            "audit_id": f"paynew-{key}",
            "case_id": f"PAYNEW-{key}",
            "action": "payout_replacement",
            "status": "requested",
            "detail": {"dead_batch_id": dead_batch, "reason": note, "approved_by": actor},
            "at": utcnow(),
        }, tenant_id=tenant_id)
    decision = {
        "outcome": RELEASE, "action": "release", "approver": approver, "sent_by": actor, "executor": "settlement_retry", "idempotency_key": key,
    }
    if original.get("approval_id"):
        # The settler finds the approval by this id; a large release sent without it comes back held.
        decision["approval_id"] = str(original["approval_id"])
    try:
        receipt = await tools.publish_decision(case_id, decision, tenant_id=tenant_id, redeliver=True)
    except Exception as exc:  # noqa: BLE001 - a publish that raised sent nothing the console can prove
        raise RetryError(NOT_SENT, 503) from exc
    if published_message_id(receipt) is None:
        raise RetryError(NOT_SENT, 503)
    return {"status": "redelivered", "case_id": case_id}


async def unsent_decision(case: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any] | None:
    """The release or block a person saved on this case that no publish proves went out, or None.

    A saved decision is only a decision once it reaches the topic: the settler captures or voids from the published message and never
    from the case state. Only a case that holds a PayPal authorization has money waiting, and a publish that carries a message id
    under the decision's first key shows it went out.
    """
    action = HUMAN_ACTION.get(str(case.get("state")))
    case_id = str(case.get("case_id") or "")
    if action is None or not case_id or not (case.get("shipment") or {}).get("paypal_authorization_id"):
        return None
    key = f"human-{case_id}-{action}"
    if await tools._earlier_publish(case_id, key, tenant_id) is not None:
        return None
    reviews = [r for r in case.get("reviews") or [] if isinstance(r, dict) and r.get("action") == action]
    approver = str(reviews[-1].get("reviewer") or "") if reviews else ""
    return {"outcome": str(case["state"]), "action": action, "idempotency_key": key, "approver": approver}


async def _send_unsent(case_id: str, note: str, actor: str, tenant_id: str | None) -> dict[str, Any]:
    """Publish the decision a person saved and that never went out, under its first key, and record who asked and why."""
    case = await get_store().get_case(case_id, tenant_id=tenant_id)
    unsent = await unsent_decision(case, tenant_id) if case else None
    if unsent is not None and not unsent["approver"]:
        raise RetryError("the case does not name who made this decision, so nothing was sent; check the case history with an admin", 409)
    if unsent is None:
        raise RetryError("only a settlement that ended failed can be retried; this case has none", 409)
    await get_store().add_audit({
        "case_id": case_id,
        "action": ACTION,
        "status": "requested",
        "detail": {"reason": note, "retried_by": actor, "idempotency_key": unsent["idempotency_key"]},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    decision = {**unsent, "sent_by": actor, "executor": "settlement_retry"}
    try:
        receipt = await tools.publish_decision(case_id, decision, tenant_id=tenant_id, after_attempt=True)
    except Exception as exc:  # noqa: BLE001 - a publish that raised sent nothing the console can prove
        raise RetryError(NOT_SENT, 503) from exc
    if published_message_id(receipt) is None:
        raise RetryError(NOT_SENT, 503)
    return {"status": "redelivered", "case_id": case_id}
