"""
The downstream settler of Floorline Pay: the only code that moves money.

It runs on the receiving side of case-decisions (decision_receiver.receive), after a
decision has been applied, never in the analysis or agent runtime. A published
RELEASED_BY_HUMAN captures the shipper's PayPal authorization and pays the carrier; a
published BLOCKED_BY_HUMAN voids the authorization so the money goes back.

The agent's own decision is a published AUTO_CLEARED under the key agent_payment.key_for gives the case. agent_payment.review says yes
again here, because a limit can be withdrawn after the analysis, and then every check of a person's release runs. No model is called:
what the analysis stored is read. A case the agent may not pay goes to a person, because nobody can act on an auto-cleared case.

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

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from vf_logistics import (
    agent_payment,
    authorization_lifecycle,
    carrier_registry,
    credit_profile,
    dual_approval,
    mandate,
    payment_binding,
    payment_checks,
    payment_controls,
    payment_freeze,
    paypal_client,
    paypal_webhook,
)
from vf_logistics.store import get_store, utcnow

log = logging.getLogger(__name__)

SKIPPED = "skipped"
HELD = "held"
RETRY = "retry"
FAILED = "failed"
SETTLED = "settled"
VOIDED = "voided"

# Case states in which a published release may still become final; any other state that
# is not RELEASED_BY_HUMAN means the release did not happen.
_NOT_FINAL = frozenset({"APPROVED", "EXECUTING"})
# PayPal unreachable, PayPal refusing our token, PayPal asking us to slow down (HTTP 429), or an answer that is not JSON is never PayPal's word on this
# payment: the decision is delivered again instead of acknowledged, or a block's void would never run again.
_RETRYABLE = frozenset({paypal_client.TRANSPORT_ERROR, paypal_client.AUTH_FAILED, paypal_client.BAD_RESPONSE})

# An authorization PayPal reports in one of these states can be captured. CAPTURED is
# there for a redelivery after the capture succeeded and the payout did not.
_CAPTURABLE = frozenset({"CREATED", "CAPTURED"})
# The batch states that mean PayPal took a payout. A batch it denied or cancelled paid nobody, and a state it does not name proves nothing.
_TAKEN_BATCH = frozenset({"NEW", "PENDING", "PROCESSING", "SUCCESS"})
# A batch in one of these states paid nobody, so a new payout cannot pay the carrier twice.
_DEAD_BATCH = frozenset({"DENIED", "CANCELED"})
# PayPal refuses a repeated sender_batch_id for 30 days. Past that a repeat could pay twice, so a day is kept in hand.
_KEY_DAYS = 29
_NOT_PAID = "batch_not_paid"
_STALE_ATTEMPT = "stale_attempt"

# A reauthorization can itself be reauthorized inside the 29 days; this many renewals are followed from the booking's own id.
_RENEWALS = 5


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
        receipt.get("status") == paypal_client.HTTP_ERROR and isinstance(http, int) and (http >= 500 or http == 429)
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


async def _current_authorization(authorization: str, tenant_id: str | None) -> str:
    """The id to ask PayPal about and to capture or void: the booking's own, or the one PayPal issued when it renewed it."""
    current = authorization
    for _ in range(_RENEWALS):
        renewed = await payment_binding.find_renewal(current, tenant_id)
        if not renewed or renewed == current:
            break
        current = renewed
    return current


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
    price = _amount(shipment.get("shipping_cost")) or _amount(shipment.get("freight_cost"))
    if price is None or held > price:
        # PayPal captures everything the booking holds: a hold above the freight price on the case would take more from
        # the shipper than the released shipment costs, while the carrier is paid only the payout.
        return _result(
            HELD,
            "the booking holds more than the freight price on the case, or the case states no price; nothing is captured",
        )
    mandate_id = await payment_binding.find_mandate(authorization, tenant_id)
    if mandate_id:
        outside = await mandate.refusal_for(
            mandate_id, payout, carrier_registry.carrier_key(shipment.get("carrier_name")),
            str(shipment.get("shipment_id") or ""), tenant_id,
        )
        if outside is not None:
            return _result(HELD, outside)
    current = await _current_authorization(authorization, tenant_id)
    live = await paypal_client.get_authorization(current, f"{key}-verify")
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
    # A CAPTURED authorization is a redelivery after the capture went through: its age no longer matters, and renewing it
    # or calling it expired would leave the carrier unpaid for money already taken.
    stage = authorization_lifecycle.HONORED if state == "CAPTURED" else authorization_lifecycle.stage(facts)
    if stage == authorization_lifecycle.EXPIRED:
        return _result(FAILED, "the authorization has expired at PayPal; the shipper must authorize again; nothing is paid",
                       verify=_step(live))
    if stage == authorization_lifecycle.UNKNOWN:
        return _result(HELD, "PayPal does not say when the authorization was made; nothing is paid", verify=_step(live))
    if stage == authorization_lifecycle.REAUTHORIZE:
        renewed = await paypal_client.reauthorize_authorization(current, f"{key}-reauth")
        if renewed["status"] != paypal_client.OK or _body(renewed).get("status") != "CREATED":
            status = RETRY if _retryable(renewed) else FAILED
            return _result(status, "PayPal could not renew the authorization after its honor period; nothing is paid",
                           reauthorize=_step(renewed))
        new_id = _body(renewed).get("id")
        if not new_id:
            return _result(HELD, "PayPal renewed the authorization but did not name the new one; nothing is paid",
                           reauthorize=_step(renewed))
        await payment_binding.record_renewal(current, str(new_id), tenant_id)
    return None


def _lacks_second_approval(case: dict[str, Any], shipment: dict[str, Any], approval_id: str) -> bool:
    """True when the decision's approval needs a second approver and has none.

    Only an approval found in the case can say so: a case with no verification record
    (a release made before dual approval existed) is judged by the checks that follow.
    """
    verification = case.get("verification")
    approvals = verification.get("approvals") if isinstance(verification, dict) else None
    for approval in approvals or []:
        if isinstance(approval, dict) and approval.get("approval_id") == approval_id:
            # The flag records the limit when the first person approved; the limit now is checked too, so a flag that is
            # missing, or was set under a higher limit, never lets a large payout leave with one approver.
            needs_second = approval.get("requires_second") is True or dual_approval.needed(shipment)
            return needs_second and not approval.get("second_approval")
    return False


def _approval_record_missing(case: dict[str, Any], shipment: dict[str, Any], approval_id: str) -> bool:
    """True for a release that needs a second approver and whose approval the case does not hold.

    _lacks_second_approval can only judge an approval it finds. Below the limit a case with no record is judged by the checks that
    follow; at or above it the missing record is itself the refusal, because nothing shows that a second person was asked.
    """
    if not dual_approval.needed(shipment):
        return False
    verification = case.get("verification")
    approvals = verification.get("approvals") if isinstance(verification, dict) else None
    return not any(isinstance(a, dict) and a.get("approval_id") == approval_id for a in approvals or [])


async def _release(
    case: dict[str, Any], shipment: dict[str, Any], key: str, tenant_id: str | None, approval_id: str,
    released: str = "RELEASED_BY_HUMAN",
) -> dict[str, Any]:
    state = case.get("state")
    if state in _NOT_FINAL:
        return _result(RETRY, f"the case is still {state}; deliver the decision again")
    if state != released:
        return _result(FAILED, f"the case is {state}, not {released}; nothing is paid")
    if _approval_record_missing(case, shipment, approval_id):
        return _result(
            HELD,
            "this release is at or above the second-approval limit and the case holds no record of its approval; nothing is paid",
        )
    if _lacks_second_approval(case, shipment, approval_id):
        return _result(HELD, "this release needs a second approver; nothing is paid until a different person approves it too")
    paused = await payment_controls.pause_state(tenant_id)
    if paused["paused"]:
        return _result(RETRY, f"payments are paused ({paused['reason']}); the decision is retried when they resume")
    shippers = payment_freeze.shipper_names(shipment)
    if not shippers:
        # A freeze is kept under the shipper's name; a case that gives none cannot be checked, and an unchecked shipper is not a clear one.
        return _result(HELD, "the case names no shipper (shipper_company or shipper_name), so a freeze on the shipper cannot be checked; nothing is paid")
    for kind, name in (("carrier", shipment.get("carrier_name")), *(("shipper", shipper) for shipper in shippers)):
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
        tier_cap=profile["cap_usd"], tier=profile["tier"], key=key,
    )
    if limited is not None:
        return _result(limited[0], limited[1])
    refusal = await _unproven_authorization(shipment, key, tenant_id)
    if refusal is not None:
        return refusal
    authorization = str(shipment.get("paypal_authorization_id") or "")
    payee = str(shipment.get("carrier_registered_payee_email") or "")
    currency = str(shipment.get("currency") or "USD")
    authorization = await _current_authorization(authorization, tenant_id)
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
    # PayPal's refund, reversal and dispute events name the capture, and its payout events name the batch "<key>-payout". Both rows
    # are written as soon as the money is captured, before the payout, so a payout that fails or is lost still leaves the capture
    # findable; each finds the settlement by id, however many settlements came after it.
    capture_id = _step(captured).get("paypal_id")
    if capture_id:
        await get_store().add_audit_once({
            "audit_id": f"capidx-{capture_id}",
            "case_id": f"CAPTURE-{capture_id}",
            "action": "settle_capture_index",
            "status": "done",
            "detail": {"settled_case_id": case.get("case_id"), "idempotency_key": key},
            "at": utcnow(),
        }, tenant_id=tenant_id)
    await get_store().add_audit_once({
        "audit_id": f"payidx-{key}",
        "case_id": f"PAYOUT-{key}",
        "action": "settle_payout_index",
        "status": "done",
        "detail": {"settled_case_id": case.get("case_id")},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    # An event PayPal sent before these two rows existed was kept as waiting; apply it now that it can be matched. It only freezes
    # or records a payout status, so a failure here is logged and never changes what is paid.
    try:
        await paypal_webhook.reconcile(tenant_id)
    except Exception:  # noqa: BLE001
        log.exception("applying the waiting PayPal events failed for %s", case.get("case_id"))
    name = f"{key}-payout"
    dead_batch = await _replacement(key, tenant_id)
    if dead_batch:
        found = await paypal_client.get_payout_batch(dead_batch, f"{key}-dead")
        header = _own_header(found, shipment.get("payout_amount"), currency, name)
        if header is None or header.get("batch_status") not in _DEAD_BATCH:
            return _result(
                RETRY if _retryable(found) else FAILED,
                "PayPal does not show the first payout batch as denied, so no second payout is sent; nothing more is paid",
                capture=_step(captured),
            )
        name = f"{key}-payout-2"
    paid = await _send_payout(
        payee, shipment.get("payout_amount"), currency, name,
        f"Floorline freight payment for {shipment.get('shipment_id') or case.get('case_id')}", tenant_id,
    )
    if paid["status"] != paypal_client.OK:
        paid = await _recovered_payout(paid, shipment.get("payout_amount"), currency, name, tenant_id) or paid
    if paid["status"] != paypal_client.OK:
        if paid.get("dead_batch"):
            return _result(
                FAILED,
                "PayPal captured the funds but did not pay the carrier: it denied the payout batch, so a person can send one new "
                "payout from the case",
                capture=_step(captured), payout=_step(paid), dead_batch=str(paid["dead_batch"]),
            )
        if paid["status"] == _STALE_ATTEMPT:
            return _result(
                HELD,
                "the first payout attempt is older than PayPal keeps its key, so a new request could pay twice; "
                "check the batch at PayPal; nothing more is paid",
                capture=_step(captured),
            )
        status = RETRY if _retryable(paid) else FAILED
        return _result(status, "PayPal captured the funds but did not pay the carrier",
                       capture=_step(captured), payout=_step(paid))
    return _result(
        SETTLED, "captured and paid", capture=_step(captured), payout=_step(paid),
        **({"recovered_from": paid["recovered_from"]} if paid.get("recovered_from") else {}),
        amount=str(_amount(shipment.get("payout_amount"))), carrier_key=carrier,
        **await _tracking(shipment, _step(captured), key, tenant_id),
    )


def _own_header(found: dict[str, Any], amount: Any, currency: str, name: str) -> dict[str, Any] | None:
    """The header of this payout's own batch as PayPal reports it; None for a batch that cannot be read or is another payout."""
    body = found.get("body")
    header = body.get("batch_header") if isinstance(body, dict) else None
    if found.get("status") != paypal_client.OK or not isinstance(header, dict):
        return None
    sender = header.get("sender_batch_header")
    money = header.get("amount")
    if not isinstance(sender, dict) or sender.get("sender_batch_id") != name or not isinstance(money, dict):
        return None
    if _amount(money.get("value")) != _amount(amount) or money.get("currency", money.get("currency_code")) != currency:
        return None
    return header


async def _replacement(key: str, tenant_id: str | None) -> str:
    """The dead batch a person asked to replace, from the row settlement_retry wrote, or an empty string."""
    rows, _ = await get_store().query_audit(case_id=f"PAYNEW-{key}", limit=1, tenant_id=tenant_id)
    detail = rows[0].get("detail") if rows else None
    return str(detail.get("dead_batch_id") or "") if isinstance(detail, dict) else ""


async def _keep_batch(name: str, batch_id: str, tenant_id: str | None) -> None:
    await get_store().add_audit_once({
        "audit_id": f"paybatch-{name}",
        "case_id": f"PAYBATCH-{name}",
        "action": "settle_payout_batch",
        "status": "done",
        "detail": {"batch_id": batch_id},
        "at": utcnow(),
    }, tenant_id=tenant_id)


def _past_key_life(started: str) -> bool:
    """True when PayPal may have forgotten the key of an attempt made then; a date that cannot be read counts as past."""
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(started) > timedelta(days=_KEY_DAYS)
    except (TypeError, ValueError):
        return True


async def _send_payout(payee: str, amount: Any, currency: str, name: str, note: str, tenant_id: str | None) -> dict[str, Any]:
    """PayPal's answer for this batch: from the batch id already kept, or from a request made now.

    The attempt is written first and the batch id right after PayPal answers, so a later delivery knows what happened from this
    record and not only from PayPal's refusal of a repeated key. A kept batch is never sent again.
    """
    rows, _ = await get_store().query_audit(case_id=f"PAYBATCH-{name}", limit=10, tenant_id=tenant_id)
    details = {str(row.get("action")): row for row in rows}
    batch = details.get("settle_payout_batch")
    kept = str((batch.get("detail") or {}).get("batch_id") or "") if batch else ""
    if kept:
        found = await paypal_client.get_payout_batch(kept, f"{name}-read")
        if found.get("status") != paypal_client.OK:
            return found
        header = _own_header(found, amount, currency, name)
        if header is not None and header.get("batch_status") in _TAKEN_BATCH:
            return {**found, "recovered_from": "kept_batch"}
        dead = header is not None and header.get("batch_status") in _DEAD_BATCH
        return {**found, "status": _NOT_PAID, **({"dead_batch": kept} if dead else {})}
    attempt = details.get("settle_payout_started")
    if attempt is not None and _past_key_life(str(attempt.get("at") or "")):
        return {"status": _STALE_ATTEMPT, "http_status": None, "body": None, "error": None}
    await get_store().add_audit_once({
        "audit_id": f"payatt-{name}",
        "case_id": f"PAYBATCH-{name}",
        "action": "settle_payout_started",
        "status": "done",
        "detail": {},
        "at": utcnow(),
    }, tenant_id=tenant_id)
    paid = await paypal_client.create_payout(payee, amount, currency, name, note)
    batch_id = _step(paid).get("paypal_id")
    if paid["status"] == paypal_client.OK and batch_id:
        await _keep_batch(name, str(batch_id), tenant_id)
    return paid


async def _recovered_payout(
    refusal: dict[str, Any], amount: Any, currency: str, name: str, tenant_id: str | None,
) -> dict[str, Any] | None:
    """PayPal's own record of this payout when it refused the request as a duplicate, or None.

    A repeated delivery after a payout whose result was lost gets a duplicate-batch refusal although the carrier was paid. The
    batch is read at PayPal and counts only when it carries this payout's sender_batch_id, amount, currency and a state PayPal took it in; anything else,
    or a batch that cannot be read, leaves the refusal in force. No payout is ever created here.
    """
    batch_id = paypal_client.duplicate_batch_id(refusal)
    if batch_id is None:
        return None
    found = await paypal_client.get_payout_batch(batch_id, f"{name}-recover")
    header = _own_header(found, amount, currency, name)
    if header is None:
        return None
    await _keep_batch(name, batch_id, tenant_id)
    if header.get("batch_status") in _DEAD_BATCH:
        return {**found, "status": _NOT_PAID, "dead_batch": batch_id}
    if header.get("batch_status") not in _TAKEN_BATCH:
        return None
    return {**found, "recovered_from": "duplicate_refusal"}


async def _tracking(
    shipment: dict[str, Any], capture: dict[str, Any], key: str, tenant_id: str | None,
) -> dict[str, Any]:
    """Tell PayPal the tracking number once the carrier is paid; a failure here never changes the payment."""
    number = str(shipment.get("tracking_number") or "")
    if not number:
        return {}
    try:
        bound = await payment_binding.find_authorization(
            str(shipment.get("paypal_authorization_id") or ""), tenant_id=tenant_id,
        )
        order_id = str((bound or {}).get("order_id") or "")
        if not order_id:
            return {"tracking": {"status": "skipped", "http_status": None, "error": "the booking holds no PayPal order"}}
        receipt = await paypal_client.add_tracking(
            order_id, str(capture.get("paypal_id") or ""), number, str(shipment.get("carrier_name") or ""),
            f"{key}-tracking",
        )
    except Exception as exc:  # noqa: BLE001 - the carrier is already paid; PayPal's copy of the proof is best effort
        return {"tracking": {"status": "error", "http_status": None, "error": type(exc).__name__}}
    return {"tracking": {"status": receipt.get("status"), "http_status": receipt.get("http_status"), "error": receipt.get("error")}}


async def _block(case: dict[str, Any], shipment: dict[str, Any], key: str, tenant_id: str | None) -> dict[str, Any]:
    state = case.get("state")
    if state in _NOT_FINAL:
        return _result(RETRY, f"the case is still {state}; deliver the decision again")
    if state != "BLOCKED_BY_HUMAN":
        # A block that arrives after the case was decided another way must not void the hold a release depends on.
        return _result(FAILED, f"the case is {state}, not BLOCKED_BY_HUMAN; nothing is voided")
    authorization = str(shipment.get("paypal_authorization_id") or "")
    if not authorization:
        return _result(SKIPPED, "no PayPal authorization is held for this case")
    bound = await payment_binding.find_authorization(authorization, tenant_id=tenant_id)
    if bound is None:
        return _result(HELD, "no booking made through Floorline holds this authorization; nothing is voided")
    if bound.get("reference_id") != shipment.get("shipment_id"):
        return _result(HELD, "this authorization was made for a different shipment; nothing is voided")
    current = await _current_authorization(authorization, tenant_id)
    voided = await paypal_client.void_authorization(current, f"{key}-void")
    if voided["status"] != paypal_client.OK:
        status = RETRY if _retryable(voided) else FAILED
        return _result(status, "PayPal did not void the authorization", void=_step(voided))
    return _result(VOIDED, "authorization voided; the shipper's funds are released", void=_step(voided))


async def _hand_to_person(case: dict[str, Any], reason: str, tenant_id: str | None) -> None:
    """Put a case the agent could not pay in a person's queue: nobody can act on an auto-cleared case, and the money stays held."""
    case["state"] = "PENDING_HUMAN"
    case["proposed_outcome"] = agent_payment.OUTCOME
    case["gate_denials"] = [{"action": "settle_payment", "reason": reason, "human_triggers": []}]
    case["decision"] = None
    case["updated_at"] = utcnow()
    await get_store().put_case(case, expected_version=case.get("_version"), tenant_id=tenant_id)


async def _captured(key: str, tenant_id: str | None) -> bool:
    """True once this decision's capture went through: _release writes the payout index row right after it."""
    rows, _ = await get_store().query_audit(case_id=f"PAYOUT-{key}", limit=1, tenant_id=tenant_id)
    return bool(rows)


async def _agent_release(case: dict[str, Any], shipment: dict[str, Any], key: str, tenant_id: str | None) -> dict[str, Any]:
    limit, refused = await agent_payment.review(case, shipment, tenant_id)
    if limit is None or refused:
        why = "; ".join(refused)
        if await _captured(key, tenant_id):
            # A person's release cannot take over a captured payment, so this stays a failed settlement a person can retry.
            return _result(FAILED, f"the money is captured, but the agent may not pay the carrier now ({why}); restore what is missing and retry")
        result = _result(HELD, f"the agent may not pay this case ({why}); nothing is paid and a person decides")
    else:
        result = await _release(case, shipment, key, tenant_id, "", released=agent_payment.OUTCOME)
        result = {**result, "agent": {"limit_usd": str(limit["limit_usd"]), "published_by": limit["published_by"]}}
    # After a capture the case stays on the agent's key: a person's release would capture under a new key, which PayPal refuses.
    if result["status"] in (HELD, FAILED) and not await _captured(key, tenant_id):
        await _hand_to_person(case, str(result["reason"]), tenant_id)
        result = {**result, "handed_to_person": True}
    return result


async def _stored_result(case_id: str, key: str, tenant_id: str | None) -> dict[str, Any] | None:
    """What an earlier delivery of this decision already settled or voided, so a redelivery answers the same.

    Run again, the checks would count the payout it already made against the limits and turn a paid payment into held or retry.
    """
    rows, _ = await get_store().query_audit(case_id=case_id, limit=100, tenant_id=tenant_id)
    done = (f"settle-{key}-{SETTLED}", f"settle-{key}-{VOIDED}")
    for row in rows:
        if row.get("audit_id") in done and isinstance(row.get("detail"), dict):
            return dict(row["detail"])
    return None


async def _give_up_reservation(key: str, tenant_id: str | None) -> None:
    """Drop this decision's reservation after a settlement that stopped on an error. If the drop itself fails the
    reservation stays and keeps counting, which only tightens the day's limit until the redelivery settles it."""
    try:
        await payment_controls.cancel_reservations(key, tenant_id)
    except Exception:  # noqa: BLE001
        return


async def settle(decision: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
    """Move the money for one applied decision, or say why it did not move."""
    case_id = str(decision.get("case_id") or "")
    key = str(decision.get("idempotency_key") or "")
    outcome = decision.get("outcome")
    try:
        by_agent = outcome == agent_payment.OUTCOME and key == agent_payment.key_for(case_id)
        if (outcome not in ("RELEASED_BY_HUMAN", "BLOCKED_BY_HUMAN") and not by_agent) or not key or not case_id:
            return _result(SKIPPED, "not a keyed human release or block")
        case = await get_store().get_case(case_id, tenant_id=tenant_id)
        shipment = (case or {}).get("shipment") or {}
        if case is None or not payment_checks.is_payment_case(shipment):
            return _result(SKIPPED, "the case moves no money")
        stored = await _stored_result(case_id, key, tenant_id)
        if stored is not None:
            return stored
        if by_agent and case.get("state") != agent_payment.OUTCOME:
            if case.get("state") == agent_payment.DECIDING:
                return _result(RETRY, "the analysis is still storing this case; deliver the decision again")
            return _result(SKIPPED, f"the case is {case.get('state')}, so a person decides it and the agent pays nothing")
        if not paypal_client.configured():
            if by_agent:
                await _hand_to_person(case, "PayPal is not configured", tenant_id)
            return _result(FAILED, "PayPal is not configured; nothing is paid")
        shipment = await carrier_registry.enrich(dict(shipment), tenant_id=tenant_id)
        if outcome == "BLOCKED_BY_HUMAN":
            result = await _block(case, shipment, key, tenant_id)
        else:
            if by_agent:
                result = await _agent_release(case, shipment, key, tenant_id)
            else:
                result = await _release(case, shipment, key, tenant_id, str(decision.get("approval_id") or ""))
            if result["status"] != SETTLED:
                await payment_controls.cancel_reservations(key, tenant_id)
    except Exception as exc:  # noqa: BLE001 - settle never raises; the status says what happened
        await _give_up_reservation(key, tenant_id)
        return _result(RETRY, f"settlement stopped on {type(exc).__name__}; deliver the decision again")
    try:
        await get_store().add_audit_once({
            "audit_id": f"settle-{key}-{result['status']}",
            "case_id": case_id,
            "action": "settle_payment",
            "status": result["status"],
            "detail": {**result, "idempotency_key": key, "outcome": outcome},
            "at": utcnow(),
        }, tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001 - settle never raises; without its row the payment is not counted, so it is retried
        return _result(RETRY, f"the settlement could not be recorded ({type(exc).__name__}); deliver the decision again")
    return result
