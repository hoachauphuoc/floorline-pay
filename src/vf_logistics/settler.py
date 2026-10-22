"""
The downstream settler of Floorline Pay: the only code that moves money.

It runs on the receiving side of case-decisions (decision_receiver.receive), after a
person's decision has been applied, never in the analysis or agent runtime. A published
RELEASED_BY_HUMAN captures the shipper's PayPal authorization and pays the carrier; a
published BLOCKED_BY_HUMAN voids the authorization so the money goes back.

Every refusal is a named status and nothing is paid:
  skipped  the case moves no money, or there is nothing to void
  held     the payment checks hold this payout; a person's release does not override them
  retry    the outcome is not final yet (the case is still executing, or PayPal could
           not be reached); the receiver asks Pub/Sub to deliver the decision again
  failed   PayPal refused, PayPal is not configured, or the case is not released

A redelivery runs the same steps again. Each PayPal call carries a request id derived
from the decision's idempotency key, so PayPal returns the first result instead of
moving money a second time. settle() never raises.
"""

from __future__ import annotations

from typing import Any

from vf_logistics import payment_checks, paypal_client
from vf_logistics.store import get_store, utcnow

SKIPPED = "skipped"
HELD = "held"
RETRY = "retry"
FAILED = "failed"
SETTLED = "settled"
VOIDED = "voided"

# Case states in which a published release may still become final; any other state that
# is not RELEASED_BY_HUMAN means the release did not happen.
_NOT_FINAL = frozenset({"APPROVED", "EXECUTING"})
_RETRYABLE = frozenset({paypal_client.TRANSPORT_ERROR})


def _result(status: str, reason: str, **steps: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **steps}


def _step(receipt: dict[str, Any]) -> dict[str, Any]:
    # Receipts keep the PayPal status and ids, never the request or the payee address.
    raw = receipt.get("body")
    body: dict[str, Any] = raw if isinstance(raw, dict) else {}
    header = body.get("batch_header")
    batch_id = header.get("payout_batch_id") if isinstance(header, dict) else None
    return {
        "status": receipt.get("status"),
        "http_status": receipt.get("http_status"),
        "paypal_id": body.get("id") or batch_id,
        "error": receipt.get("error"),
    }


def _retryable(receipt: dict[str, Any]) -> bool:
    http = receipt.get("http_status")
    return receipt.get("status") in _RETRYABLE or (
        receipt.get("status") == paypal_client.HTTP_ERROR and isinstance(http, int) and http >= 500
    )


async def _release(
    case: dict[str, Any], shipment: dict[str, Any], key: str,
) -> dict[str, Any]:
    state = case.get("state")
    if state in _NOT_FINAL:
        return _result(RETRY, f"the case is still {state}; deliver the decision again")
    if state != "RELEASED_BY_HUMAN":
        return _result(FAILED, f"the case is {state}, not RELEASED_BY_HUMAN; nothing is paid")
    held = payment_checks.check_payment(shipment)
    if held:
        codes = ", ".join(f["code"] for f in held)
        return _result(HELD, f"the payment checks hold this payout ({codes}); nothing is paid")
    authorization = str(shipment.get("paypal_authorization_id") or "")
    payee = str(shipment.get("carrier_payee_email") or "")
    currency = str(shipment.get("currency") or "USD")
    captured = await paypal_client.capture_authorization(authorization, f"{key}-capture")
    if captured["status"] != paypal_client.OK:
        status = RETRY if _retryable(captured) else FAILED
        return _result(status, "PayPal did not capture the authorization; nothing is paid",
                       capture=_step(captured))
    paid = await paypal_client.create_payout(
        payee, shipment.get("payout_amount"), currency, f"{key}-payout",
        f"Floorline freight payment for {shipment.get('shipment_id') or case.get('case_id')}",
    )
    if paid["status"] != paypal_client.OK:
        status = RETRY if _retryable(paid) else FAILED
        return _result(status, "PayPal captured the funds but did not pay the carrier",
                       capture=_step(captured), payout=_step(paid))
    return _result(SETTLED, "captured and paid", capture=_step(captured), payout=_step(paid))


async def _block(shipment: dict[str, Any], key: str) -> dict[str, Any]:
    authorization = str(shipment.get("paypal_authorization_id") or "")
    if not authorization:
        return _result(SKIPPED, "no PayPal authorization is held for this case")
    voided = await paypal_client.void_authorization(authorization, f"{key}-void")
    if voided["status"] != paypal_client.OK:
        status = RETRY if _retryable(voided) else FAILED
        return _result(status, "PayPal did not void the authorization", void=_step(voided))
    return _result(VOIDED, "authorization voided; the shipper's funds are released", void=_step(voided))


async def settle(decision: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
    """Move the money for one applied decision, or say why it did not move."""
    case_id = str(decision.get("case_id") or "")
    key = str(decision.get("idempotency_key") or "")
    outcome = decision.get("outcome")
    try:
        if outcome not in ("RELEASED_BY_HUMAN", "BLOCKED_BY_HUMAN") or not key or not case_id:
            return _result(SKIPPED, "not a keyed human release or block")
        case = await get_store().get_case(case_id, tenant_id=tenant_id)
        shipment = (case or {}).get("shipment") or {}
        if case is None or not payment_checks.is_payment_case(shipment):
            return _result(SKIPPED, "the case moves no money")
        if not paypal_client.configured():
            return _result(FAILED, "PayPal is not configured; nothing is paid")
        if outcome == "BLOCKED_BY_HUMAN":
            result = await _block(shipment, key)
        else:
            result = await _release(case, shipment, key)
    except Exception as exc:  # noqa: BLE001 - settle never raises; the status says what happened
        return _result(RETRY, f"settlement stopped on {type(exc).__name__}; deliver the decision again")
    await get_store().add_audit_once({
        "audit_id": f"settle-{key}-{result['status']}",
        "case_id": case_id,
        "action": "settle_payment",
        "status": result["status"],
        "detail": {**result, "idempotency_key": key, "outcome": outcome},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return result
