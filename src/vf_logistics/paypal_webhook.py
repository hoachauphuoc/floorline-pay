"""
PayPal's webhook receiver for Floorline Pay: how the settlement hears what happened after
the money moved.

PayPal posts events to a public address. Anyone can post to that address, so nothing is
believed until PayPal itself says the delivery is its own: the five transmission headers and
the event are sent to PayPal's verify-webhook-signature call with this webhook's id
(PAYPAL_WEBHOOK_ID), and only a verification_status of SUCCESS is accepted. With no webhook id
set the receiver is off and answers 503. A certificate address that is not PayPal's is refused
before anything is sent, and a PayPal that cannot be reached is a 503, so PayPal delivers the
event again.

What a verified event does:
  PAYMENT.PAYOUTS-ITEM.*     records the payout's status on its case (payout_tracking.py); a
                             failed, blocked, returned or refunded payout freezes the carrier
  PAYMENT.CAPTURE.DENIED,
  .REFUNDED, .REVERSED       freezes the carrier and the shipper of the case
  CUSTOMER.DISPUTE.CREATED   freezes the carrier and the shipper of the case
  anything else              recorded and left alone
An event is tied to its case through the settlement the settler recorded: the capture id for
captures and disputes, the payout request id for payout items. One that matches no settlement
is recorded as unmatched. Effects come before the event is recorded, and each is idempotent,
so a redelivery after a failure finishes the work and a redelivery after success does nothing.

A freeze is lifted only by a person (payment_freeze.py). The receiver never pays anyone and
never loosens anything.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any
from urllib.parse import urlparse

from vf_logistics import payment_freeze, payout_tracking, paypal_client
from vf_logistics.store import get_store, utcnow

CASE = "PAYPAL-EVENTS"

_HEADERS = (
    "paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
    "paypal-transmission-sig", "paypal-transmission-time",
)
_PAYOUT_ITEM = "PAYMENT.PAYOUTS-ITEM."
_CAPTURE_TROUBLE = frozenset({
    "PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED",
})
_DISPUTE_OPENED = "CUSTOMER.DISPUTE.CREATED"
_PAGE = 200
_PAGES = 10


def _reply(status: int, outcome: str, reason: str, **more: Any) -> tuple[int, dict[str, Any]]:
    return status, {"outcome": outcome, "reason": reason, **more}


def _from_paypal(url: Any) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "paypal.com" or host.endswith(".paypal.com"))


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("detail")
    return raw if isinstance(raw, dict) else {}


async def _settlements(tenant_id: str | None) -> AsyncIterator[dict[str, Any]]:
    cursor: str | None = None
    for _ in range(_PAGES):
        rows, cursor = await get_store().query_audit(
            action="settle_payment", status="settled", cursor=cursor, limit=_PAGE,
            tenant_id=tenant_id,
        )
        for row in rows:
            yield row
        if not cursor:
            return


async def _find(match: Callable[[dict[str, Any]], bool], tenant_id: str | None) -> dict[str, Any] | None:
    async for row in _settlements(tenant_id):
        if match(_detail(row)):
            return row
    return None


async def _parties(case_id: str, tenant_id: str | None) -> tuple[Any, Any]:
    case = await get_store().get_case(case_id, tenant_id=tenant_id)
    shipment = (case or {}).get("shipment") or {}
    return shipment.get("carrier_name"), shipment.get("shipper_name")


async def _payout_item(
    event_id: str, event_type: str, resource: dict[str, Any], tenant_id: str | None,
) -> dict[str, Any]:
    batch = str(resource.get("sender_batch_id") or "")
    key = batch.removesuffix("-payout") if batch.endswith("-payout") else ""
    row = await _find(lambda d: bool(key) and d.get("idempotency_key") == key, tenant_id)
    if row is None:
        return {"matched": False}
    case_id = str(row.get("case_id") or "")
    status = str(resource.get("transaction_status") or event_type.removeprefix(_PAYOUT_ITEM))
    carrier, _ = await _parties(case_id, tenant_id)
    state = await payout_tracking.apply(case_id, f"event-{event_id}", status, carrier, tenant_id)
    return {"matched": True, "case_id": case_id, "payout_status": state}


async def _freeze_for(
    event_id: str, captures: list[str], reason: str, tenant_id: str | None,
) -> dict[str, Any]:
    wanted = {c for c in captures if c}
    if not wanted:
        return {"matched": False}

    def match(detail: dict[str, Any]) -> bool:
        capture = detail.get("capture")
        return isinstance(capture, dict) and capture.get("paypal_id") in wanted

    row = await _find(match, tenant_id)
    if row is None:
        return {"matched": False}
    case_id = str(row.get("case_id") or "")
    carrier, shipper = await _parties(case_id, tenant_id)
    frozen = []
    for kind, name in (("carrier", carrier), ("shipper", shipper)):
        if await payment_freeze.freeze(kind, name, reason, f"event-{event_id}", tenant_id):
            frozen.append(kind)
    return {"matched": True, "case_id": case_id, "frozen": frozen}


async def _apply(
    event_id: str, event_type: str, resource: dict[str, Any], tenant_id: str | None,
) -> dict[str, Any]:
    if event_type.startswith(_PAYOUT_ITEM):
        return await _payout_item(event_id, event_type, resource, tenant_id)
    if event_type in _CAPTURE_TROUBLE:
        return await _freeze_for(
            event_id, [str(resource.get("id") or "")], f"PayPal reported {event_type}", tenant_id,
        )
    if event_type == _DISPUTE_OPENED:
        disputed = resource.get("disputed_transactions")
        captures = [
            str(t.get("seller_transaction_id") or "")
            for t in (disputed if isinstance(disputed, list) else []) if isinstance(t, dict)
        ]
        return await _freeze_for(event_id, captures, "a dispute was opened on a payment", tenant_id)
    return {}


async def receive(headers: Mapping[str, str], event: Any) -> tuple[int, dict[str, Any]]:
    """Verify one webhook delivery and apply it. Returns the HTTP status and the reply body."""
    sent = {str(k).lower(): str(v) for k, v in headers.items()}
    if any(not sent.get(name) for name in _HEADERS):
        # No signature to check: the caller is unauthenticated, whether or not the webhook is enabled.
        return _reply(401, "rejected", "the PayPal signature headers are missing")
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
    if not webhook_id:
        return _reply(503, "rejected", "PAYPAL_WEBHOOK_ID is not set; the PayPal webhook is not enabled")
    if not isinstance(event, dict) or not event.get("id") or not isinstance(event.get("event_type"), str):
        return _reply(400, "rejected", "the event needs an id and an event_type")
    if not _from_paypal(sent["paypal-cert-url"]):
        return _reply(401, "rejected", "the certificate address is not PayPal's")
    try:
        verdict = await paypal_client.verify_webhook_signature(
            sent, event, webhook_id, f"verify-{sent['paypal-transmission-id']}",
        )
        if verdict["status"] != paypal_client.OK:
            http = verdict.get("http_status")
            unreachable = verdict["status"] in (
                paypal_client.TRANSPORT_ERROR, paypal_client.NOT_CONFIGURED, paypal_client.AUTH_FAILED,
            ) or (isinstance(http, int) and http >= 500)
            return _reply(
                503 if unreachable else 401, "rejected",
                "PayPal could not confirm the signature; nothing was applied",
            )
        body = verdict.get("body")
        if not isinstance(body, dict) or body.get("verification_status") != "SUCCESS":
            return _reply(401, "rejected", "the signature is not PayPal's; nothing was applied")
        tenant_id = os.getenv("PAYPAL_WEBHOOK_TENANT_ID", "").strip() or None
        event_id, event_type = str(event["id"]), str(event["event_type"])
        resource = event.get("resource")
        effect = await _apply(event_id, event_type, resource if isinstance(resource, dict) else {}, tenant_id)
        await get_store().add_audit_once({
            "audit_id": f"ppevt-{event_id}",
            "case_id": CASE,
            "action": "paypal_event",
            "status": "done",
            "detail": {"event_type": event_type, **effect},
            "at": utcnow(),
        }, tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001 - a 503 makes PayPal deliver the event again
        return _reply(503, "retry", f"the event could not be applied ({type(exc).__name__}); PayPal will send it again")
    matched = effect.get("matched")
    outcome = "recorded" if matched is None else ("applied" if matched else "unmatched")
    return 200, {"outcome": outcome, "event_type": event_type, **effect}
