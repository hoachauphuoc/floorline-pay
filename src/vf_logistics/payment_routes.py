"""
Booking routes for Floorline Pay: create the PayPal order a shipper pays, then authorize
it once the shipper has approved it in PayPal.

An authorization holds the shipper's money without taking it. Nothing here pays anyone:
the carrier is paid only by settler.py, after a person has released the shipment.

The routes sit behind the operator role and a rate limit, and answer in the same shapes
as the rest of the API. A PayPal id from the caller is checked against a strict pattern
before it is placed in a PayPal URL. A PayPal reply that lacks the id the caller needs is
a 502, never an empty success: a booking with no authorization id must not look booked.

An authorization is bound to its shipment when it is made (payment_binding.py), from what
PayPal's own reply says: the reference the order was created for and the amount held. A
reply that does not say both is not a booking.

A caller that wants the shipper sent to PayPal to approve (return_url and cancel_url)
must name addresses on an origin listed in PAYMENT_RETURN_ORIGINS. With the variable unset
no return address is accepted, so a deployment that has not said where its shippers come
back to cannot be used to send them anywhere else. The approval address handed back is
checked to be a PayPal address before it is returned.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from vf_logistics import mandate, payment_binding, paypal_client
from vf_logistics.auth import get_auth_context

# PayPal order and authorization ids are upper-case letters, digits and hyphens.
PAYPAL_ID = re.compile(r"^[A-Z0-9-]{1,64}$")

# The only hosts a shipper may be sent to in order to approve a payment.
PAYPAL_APPROVAL_HOSTS = frozenset({"www.paypal.com", "www.sandbox.paypal.com"})


def _refusal(receipt: dict[str, Any]) -> tuple[Any, int]:
    status = receipt["status"]
    if status == paypal_client.NOT_CONFIGURED:
        code = 503
    elif status == paypal_client.BAD_INPUT:
        code = 400
    else:
        code = 502
    return jsonify({"error": receipt.get("error") or status, "paypal_status": status}), code


def _no_id(what: str) -> tuple[Any, int]:
    return jsonify({
        "error": f"PayPal returned no {what}; nothing is booked",
        "paypal_status": "bad_response",
    }), 502


def _authorization_id(body: Any) -> str | None:
    try:
        value = body["purchase_units"][0]["payments"]["authorizations"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) and PAYPAL_ID.match(value) else None


def _booking_facts(body: Any) -> dict[str, str] | None:
    """The reference the order was made for and the amount held, as PayPal reports them."""
    try:
        unit = body["purchase_units"][0]
        amount = unit["payments"]["authorizations"][0]["amount"]
        facts = {
            "reference_id": unit["reference_id"],
            "amount": amount["value"],
            "currency": amount["currency_code"],
        }
    except (KeyError, IndexError, TypeError):
        return None
    return facts if all(isinstance(v, str) and v for v in facts.values()) else None


def _mandate_id(body: Any) -> str | None:
    """The mandate id PayPal echoes from the order's custom_id, if the order was made under one."""
    try:
        value = body["purchase_units"][0]["custom_id"]
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) and value else None


def _allowed_return(url: str) -> bool:
    allowed = {
        origin.strip().lower().rstrip("/")
        for origin in os.getenv("PAYMENT_RETURN_ORIGINS", "").split(",") if origin.strip()
    }
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username:
        return False
    return f"{parsed.scheme}://{parsed.netloc}".lower() in allowed


def _approval_url(body: Any) -> str | None:
    links = body.get("links") if isinstance(body, dict) else None
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and link.get("rel") == "payer-action":
            href = str(link.get("href") or "")
            parsed = urlparse(href)
            if parsed.scheme == "https" and parsed.netloc in PAYPAL_APPROVAL_HOSTS:
                return href
    return None


def register(
    app: Flask, limiter: Any, require_operator: Callable[..., Any], run_async: Callable[[Any], Any],
    tenant: Callable[[], str | None],
) -> None:
    @app.route("/api/v1/payments/orders", methods=["POST"])
    @require_operator
    @limiter.limit("30 per minute")
    def payments_create_order() -> Any:
        body = request.get_json(silent=True) or {}
        request_id = str(body.get("request_id") or f"order-{uuid.uuid4().hex}")
        return_url = str(body.get("return_url") or "")
        cancel_url = str(body.get("cancel_url") or "")
        signed: dict[str, Any] | None = None
        if body.get("mandate") is not None:
            if not (return_url and cancel_url):
                return jsonify({"error": "a mandate is given with the booking that sends the shipper to PayPal"}), 400
            terms = body["mandate"] if isinstance(body["mandate"], dict) else {}
            context = get_auth_context()
            try:
                signed = mandate.issue(
                    str(context.email) if context is not None and context.acts_for_a_person else "",
                    terms.get("max_payout_usd"), terms.get("carriers"), terms.get("valid_days"),
                    str(body.get("reference_id") or ""),
                )
            except mandate.MandateError as exc:
                return jsonify({"error": exc.message}), exc.status
        extras = {"custom_id": signed["mandate"]["mandate_id"]} if signed is not None else {}
        if return_url or cancel_url:
            if not (_allowed_return(return_url) and _allowed_return(cancel_url)):
                return jsonify({
                    "error": "return_url and cancel_url must be on an origin listed in "
                             "PAYMENT_RETURN_ORIGINS",
                }), 400
            receipt = run_async(paypal_client.create_order_for_approval(
                body.get("amount"), str(body.get("currency") or ""), request_id,
                str(body.get("reference_id") or ""), return_url, cancel_url, **extras,
            ))
        else:
            receipt = run_async(paypal_client.create_order(
                body.get("amount"), str(body.get("currency") or ""), request_id,
                str(body.get("reference_id") or ""),
            ))
        if receipt["status"] != paypal_client.OK:
            return _refusal(receipt)
        data = receipt["body"] if isinstance(receipt["body"], dict) else {}
        order_id = data.get("id")
        if not isinstance(order_id, str) or not PAYPAL_ID.match(order_id):
            return _no_id("order id")
        reply: dict[str, Any] = {"order_id": order_id, "paypal_status": data.get("status")}
        if signed is not None:
            run_async(mandate.record(signed, tenant_id=tenant()))
            reply["mandate_id"] = signed["mandate"]["mandate_id"]
        if return_url:
            approval = _approval_url(data)
            if approval is None:
                return _no_id("approval link")
            reply["approval_url"] = approval
        return jsonify(reply), 201

    @app.route("/api/v1/payments/orders/<order_id>/authorize", methods=["POST"])
    @require_operator
    @limiter.limit("30 per minute")
    def payments_authorize_order(order_id: str) -> Any:
        if not PAYPAL_ID.match(order_id):
            return jsonify({"error": "order_id is not a PayPal order id"}), 400
        receipt = run_async(paypal_client.authorize_order(order_id, f"authorize-{order_id}"))
        if receipt["status"] != paypal_client.OK:
            return _refusal(receipt)
        authorization_id = _authorization_id(receipt["body"])
        if authorization_id is None:
            return _no_id("authorization id")
        facts = _booking_facts(receipt["body"])
        if facts is None:
            return _no_id("booking reference and amount")
        run_async(payment_binding.record_authorization(
            authorization_id, facts["reference_id"], facts["amount"], facts["currency"],
            order_id, tenant_id=tenant(),
        ))
        mandate_id = _mandate_id(receipt["body"])
        if mandate_id:
            run_async(payment_binding.record_mandate(authorization_id, mandate_id, tenant_id=tenant()))
        return jsonify({"order_id": order_id, "authorization_id": authorization_id}), 200
