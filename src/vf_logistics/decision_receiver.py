"""
The receiving side of the case-decisions topic, as part of Floorline (N01).

Floorline publishes every executed human decision at least once: a retry after an
unknown outcome reuses the approval's idempotency key, and Pub/Sub itself redelivers
a message whose acknowledgement was lost. This receiver turns that into once. It is
a Pub/Sub push endpoint (POST /api/v1/receiver/decision) that records each decision
in the store under its key, and the record IS the application: the create that
claims the key and the write that applies it are one transaction, so a repeat is
counted (deliveries) and never applied twice. Systems downstream read the applied
record instead of the topic.

Only a push Google signed for the configured service account and audience is
accepted; without that configuration nothing is (fail closed). A message that cannot
be deduplicated (no key, no case, no tenant where one is required) is refused with
422 and never applied: Pub/Sub keeps it, redelivers it or dead-letters it, for a
person to see. The same topic also carries pipeline status notifications, which
are acknowledged without applying a human decision. docs/RECEIVER_CONTRACT.md is
the contract this implements.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import os
from typing import Any

from vf_logistics import settler
from vf_logistics.store import get_store
from vf_logistics.tenant import multi_tenant_enabled


# These are the exact keyless payloads emitted by orchestrator.advance(). They
# describe pipeline progress, not an executed human approval. A payload carrying
# approval metadata, a body-only key, or an unknown shape must still be refused.
_PIPELINE_NOTIFICATION_FIELDS = {
    "AUTO_CLEARED": {"case_id", "outcome", "effective_risk", "at"},
    "HELD_FOR_REVIEW": {"case_id", "outcome", "effective_risk", "at"},
    "ESCALATED": {"case_id", "outcome", "fraud_pattern", "exposure_estimate", "at"},
}


def _pipeline_notification(body: Any, message_id: Any) -> bool:
    if not isinstance(body, dict) or not isinstance(message_id, str) or not message_id.strip():
        return False
    outcome = body.get("outcome")
    return (
        isinstance(outcome, str)
        and outcome in _PIPELINE_NOTIFICATION_FIELDS
        and set(body) == _PIPELINE_NOTIFICATION_FIELDS[outcome]
        and isinstance(body.get("case_id"), str)
        and bool(body["case_id"].strip())
    )


def verify_push_token(authorization: str | None) -> str | None:
    """
    The service account a Pub/Sub push came from, when Google signed its OIDC token
    for the configured account and audience; otherwise None.

    Read at call time so a deployment can change the account without a rebuild.
    """
    account = os.getenv("RECEIVER_PUSH_SA", "").strip()
    audience = os.getenv("RECEIVER_PUSH_AUDIENCE", "").strip()
    if not account or not audience:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        # By name, not `from google.oauth2 import id_token`: CI's mypy resolves the
        # google namespace differently from a local run, and its error count is at
        # the ratchet ceiling.
        id_token: Any = importlib.import_module("google.oauth2.id_token")
        google_requests: Any = importlib.import_module("google.auth.transport.requests")
        claims = dict(id_token.verify_oauth2_token(
            authorization[len("Bearer "):], google_requests.Request(), audience=audience,
        ))
    except Exception:  # noqa: BLE001 - any verification failure is a refusal
        return None
    if claims.get("email") != account or claims.get("email_verified") is not True:
        return None
    return account


async def receive(envelope: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """
    Apply one pushed decision unless its key was applied before.

    Returns the HTTP status and body for Pub/Sub: 200 acknowledges ("applied",
    "duplicate", or "ignored" for a pipeline notification); 422 leaves a decision
    that cannot be deduplicated with Pub/Sub.
    """
    message = envelope.get("message")
    if not isinstance(message, dict):
        return 422, {"outcome": "rejected", "reason": "no Pub/Sub message in the request"}
    attributes = message.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    key = str(attributes.get("idempotency_key") or "")
    owner = attributes.get("tenant_id") or None
    try:
        body = json.loads(base64.b64decode(message.get("data") or ""))
    except ValueError:
        body = None
    case_id = body.get("case_id") if isinstance(body, dict) else None
    message_id = message.get("messageId") or message.get("message_id") or ""
    notification = not key and _pipeline_notification(body, message_id)
    if not case_id or (not key and not notification):
        return 422, {"outcome": "rejected", "reason": "a decision needs an idempotency_key attribute and a case_id"}
    if owner is None and multi_tenant_enabled():
        return 422, {"outcome": "rejected", "reason": "a multi-tenant decision needs a tenant_id attribute"}
    if notification:
        # No shipment action and no applied_decisions write: acknowledging the
        # same informational message again has no business side effect.
        logging.info(
            "receiver ignored pipeline notification: outcome=%s case_id=%s message_id=%s",
            body["outcome"], case_id, message_id,
        )
        return 200, {"outcome": "ignored", "reason": "pipeline status notification; no human decision to apply"}
    record = {
        "case_id": str(case_id),
        "outcome": body.get("outcome"),
        "approval_id": body.get("approval_id"),
        "first_message_id": str(message_id),
    }
    result = await get_store().apply_decision_once(key, record, tenant_id=owner)
    settlement = await settler.settle({**record, "idempotency_key": key}, tenant_id=owner)
    reply = {
        "outcome": "applied" if result["applied"] else "duplicate",
        "deliveries": result["deliveries"],
        "settlement": settlement,
    }
    # An unknown settlement outcome is not acknowledged: Pub/Sub delivers the decision
    # again, and the PayPal request ids make the repeat safe.
    return (503 if settlement["status"] == settler.RETRY else 200), reply
