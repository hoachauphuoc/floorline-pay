"""
Booking routes for Floorline Pay: create the PayPal order a shipper pays, then authorize
it once the shipper has approved it in PayPal.

An authorization holds the shipper's money without taking it. Nothing here pays anyone:
the carrier is paid only by settler.py, after a person has released the shipment.

The routes sit behind the operator role and a rate limit, and answer in the same shapes
as the rest of the API. A PayPal id from the caller is checked against a strict pattern
before it is placed in a PayPal URL. A PayPal reply that lacks the id the caller needs is
a 502, never an empty success: a booking with no authorization id must not look booked.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from vf_logistics import paypal_client

# PayPal order and authorization ids are upper-case letters, digits and hyphens.
PAYPAL_ID = re.compile(r"^[A-Z0-9-]{1,64}$")


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


def register(
    app: Flask, limiter: Any, require_operator: Callable[..., Any], run_async: Callable[[Any], Any],
) -> None:
    @app.route("/api/v1/payments/orders", methods=["POST"])
    @require_operator
    @limiter.limit("30 per minute")
    def payments_create_order() -> Any:
        body = request.get_json(silent=True) or {}
        request_id = str(body.get("request_id") or f"order-{uuid.uuid4().hex}")
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
        return jsonify({"order_id": order_id, "paypal_status": data.get("status")}), 201

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
        return jsonify({"order_id": order_id, "authorization_id": authorization_id}), 200
