"""
The downstream settler of Floorline Pay: the only code that moves money.

It runs on the receiving side of case-decisions (decision_receiver.receive), after a
person's decision has been applied, never in the analysis or agent runtime. A published
RELEASED_BY_HUMAN captures the shipper's PayPal authorization and pays the carrier; a
published BLOCKED_BY_HUMAN voids the authorization so the money goes back.

Every refusal is a named status and nothing is paid:
  skipped  the case moves no money, or there is nothing to void
  held     the payment checks hold this payout, or the authorization is not provably this
           shipment's money; a person's release does not override either
  retry    the outcome is not final yet (the case is still executing, the capture is still
           pending, or PayPal could not be reached); the receiver asks Pub/Sub to deliver
           the decision again
  failed   PayPal refused, PayPal is not configured, or the case is not released

The authorization id on a shipment is typed by whoever sent the event, so before anything
is captured it must match the record the booking wrote when PayPal authorized it
(payment_binding.py) for this shipment, and PayPal itself must still report it capturable
and large enough. Money is paid out only after PayPal reports the capture COMPLETED.

A redelivery runs the same steps again. Each PayPal call carries a request id derived
from the decision's idempotency key, so PayPal returns the first result instead of
moving money a second time. settle() never raises.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import (
    authorization_lifecycle,
    carrier_registry,
    credit_profile,
    mandate,
    payment_binding,
    payment_checks,
    payment_controls,
    payment_freeze,
    paypal_client,
)
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

# An authorization PayPal reports in one of these states can be captured. CAPTURED is
# there for a redelivery after the capture succeeded and the payout did not.
_CAPTURABLE = frozenset({"CREATED", "CAPTURED"})


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


def _amount(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _body(receipt: dict[str, Any]) -> dict[str, Any]:
    raw = receipt.get("body")
    return raw if isinstance(raw, dict) else {}


async def _unproven_authorization(
    shipment: dict[str, Any], key: str, tenant_id: str | None,
) -> dict[str, Any] | None:
    """A refusal when this shipment's authorization cannot be proven, else None."""
    authorization = str(shipment.get("paypal_authorization_id") or "")
    payout = _amount(shipment.get("payout_amount"))
    currency = str(shipment.get("currency") or "USD")
    bound = await payment_binding.find_authorization(authorization, tenant_id=tenant_id)
    if bound is None:
        return _result(HELD, "no booking made through Floorline holds this authorization; nothing is paid")
    if bound.get("reference_id") != shipment.get("shipment_id"):
        return _result(HELD, "this authorization was made for a different shipment; nothing is paid")
    held = _amount(bound.get("amount"))
    if payout is None or held is None or str(bound.get("currency")) != currency or held < payout:
        return _result(HELD, "the booking holds less than the payout, or in another currency; nothing is paid")
    mandate_id = await payment_binding.find_mandate(authorization, tenant_id)
    if mandate_id:
        outside = await mandate.refusal_for(
            mandate_id, payout, carrier_registry.carrier_key(shipment.get("carrier_name")),
            str(shipment.get("shipment_id") or ""), tenant_id,
        )
        if outside is not None:
            return _result(HELD, outside)
    live = await paypal_client.get_authorization(authorization, f"{key}-verify")
    if live["status"] != paypal_client.OK:
        status = RETRY if _retryable(live) else FAILED
        return _result(status, "PayPal could not confirm the authorization; nothing is paid",
                       verify=_step(live))
    facts = _body(live)
    state = facts.get("status")
    if state not in _CAPTURABLE:
        return _result(FAILED, f"PayPal reports the authorization as {state or 'unknown'}; nothing is paid",
                       verify=_step(live))
    amount = facts.get("amount")
    now_held = _amount(amount.get("value")) if isinstance(amount, dict) else None
    now_currency = amount.get("currency_code") if isinstance(amount, dict) else None
    if now_held is None or now_held < payout or now_currency != currency:
        return _result(HELD, "PayPal reports less held than the payout; nothing is paid", verify=_step(live))
    stage = authorization_lifecycle.stage(facts)
    if stage == authorization_lifecycle.EXPIRED:
        return _result(FAILED, "the authorization has expired at PayPal; the shipper must authorize again; nothing is paid",
                       verify=_step(live))
    if stage == authorization_lifecycle.UNKNOWN:
        return _result(HELD, "PayPal does not say when the authorization was made; nothing is paid", verify=_step(live))
    if stage == authorization_lifecycle.REAUTHORIZE:
        renewed = await paypal_client.reauthorize_authorization(authorization, f"{key}-reauth")
        if renewed["status"] != paypal_client.OK or _body(renewed).get("status") != "CREATED":
            status = RETRY if _retryable(renewed) else FAILED
            return _result(status, "PayPal could not renew the authorization after its honor period; nothing is paid",
                           reauthorize=_step(renewed))
    return None


def _lacks_second_approval(case: dict[str, Any], approval_id: str) -> bool:
    """True when the decision's approval needs a second approver and has none.

    Only an approval found in the case can say so: a case with no verification record
    (a release made before dual approval existed) is judged by the checks that follow.
    """
    verification = case.get("verification")
    approvals = verification.get("approvals") if isinstance(verification, dict) else None
    for approval in approvals or []:
        if isinstance(approval, dict) and approval.get("approval_id") == approval_id:
            return bool(approval.get("requires_second")) and not approval.get("second_approval")
    return False


async def _release(
    case: dict[str, Any], shipment: dict[str, Any], key: str, tenant_id: str | None, approval_id: str,
) -> dict[str, Any]:
    state = case.get("state")
    if state in _NOT_FINAL:
        return _result(RETRY, f"the case is still {state}; deliver the decision again")
    if state != "RELEASED_BY_HUMAN":
        return _result(FAILED, f"the case is {state}, not RELEASED_BY_HUMAN; nothing is paid")
    if _lacks_second_approval(case, approval_id):
        return _result(HELD, "this release needs a second approver; nothing is paid until a different person approves it too")
    paused = await payment_controls.pause_state(tenant_id)
    if paused["paused"]:
        return _result(RETRY, f"payments are paused ({paused['reason']}); the decision is retried when they resume")
    for kind, name in (("carrier", shipment.get("carrier_name")), ("shipper", shipment.get("shipper_name"))):
        frozen = await payment_freeze.frozen(kind, name, tenant_id)
        if frozen is not None:
            return _result(
                HELD,
                f"payments to this {kind} are frozen ({frozen['reason']}); a governance admin must lift the "
                "freeze; nothing is paid",
            )
    held = payment_checks.check_payment(shipment)
    if held:
        codes = ", ".join(f["code"] for f in held)
        return _result(HELD, f"the payment checks hold this payout ({codes}); nothing is paid")
    carrier = carrier_registry.carrier_key(shipment.get("carrier_name"))
    profile = await credit_profile.profile(shipment.get("carrier_name"), case.get("payment_ai"), tenant_id)
    limited = await payment_controls.check_payout(
        str(shipment.get("currency") or "USD"), _amount(shipment.get("payout_amount")), carrier, tenant_id,
        tier_cap=profile["cap_usd"], tier=profile["tier"],
    )
    if limited is not None:
        return _result(limited[0], limited[1])
    refusal = await _unproven_authorization(shipment, key, tenant_id)
    if refusal is not None:
        return refusal
    authorization = str(shipment.get("paypal_authorization_id") or "")
    payee = str(shipment.get("carrier_registered_payee_email") or "")
    currency = str(shipment.get("currency") or "USD")
    captured = await paypal_client.capture_authorization(authorization, f"{key}-capture")
    if captured["status"] != paypal_client.OK:
        status = RETRY if _retryable(captured) else FAILED
        return _result(status, "PayPal did not capture the authorization; nothing is paid",
                       capture=_step(captured))
    capture_state = _body(captured).get("status")
    if capture_state != "COMPLETED":
        status = RETRY if capture_state == "PENDING" else FAILED
        return _result(status, f"PayPal reports the capture as {capture_state or 'unknown'}, not COMPLETED; nothing is paid",
                       capture=_step(captured))
    paid = await paypal_client.create_payout(
        payee, shipment.get("payout_amount"), currency, f"{key}-payout",
        f"Floorline freight payment for {shipment.get('shipment_id') or case.get('case_id')}",
    )
    if paid["status"] != paypal_client.OK:
        status = RETRY if _retryable(paid) else FAILED
        return _result(status, "PayPal captured the funds but did not pay the carrier",
                       capture=_step(captured), payout=_step(paid))
    return _result(
        SETTLED, "captured and paid", capture=_step(captured), payout=_step(paid),
        amount=str(_amount(shipment.get("payout_amount"))), carrier_key=carrier,
    )


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
        shipment = await carrier_registry.enrich(dict(shipment), tenant_id=tenant_id)
        if outcome == "BLOCKED_BY_HUMAN":
            result = await _block(shipment, key)
        else:
            result = await _release(case, shipment, key, tenant_id, str(decision.get("approval_id") or ""))
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
