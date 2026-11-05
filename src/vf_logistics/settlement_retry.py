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
    await get_store().add_audit({
        "case_id": case_id,
        "action": ACTION,
        "status": "requested",
        "detail": {"reason": note, "retried_by": actor, "idempotency_key": key},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    decision = {
        "outcome": RELEASE, "action": "release", "approver": actor, "executor": "settlement_retry", "idempotency_key": key,
    }
    try:
        receipt = await tools.publish_decision(case_id, decision, tenant_id=tenant_id, redeliver=True)
    except Exception as exc:  # noqa: BLE001 - a publish that raised sent nothing the console can prove
        raise RetryError(NOT_SENT, 503) from exc
    if published_message_id(receipt) is None:
        raise RetryError(NOT_SENT, 503)
    return {"status": "redelivered", "case_id": case_id}
