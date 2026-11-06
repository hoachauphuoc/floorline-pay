"""
PayPal REST client for Floorline Pay, sandbox only.

Every call returns a receipt and never raises: {"status", "http_status", "body", "error"}.
A payment that did not happen must not look like one that did, so only OK means PayPal
accepted the request; every other status names why the money did not move.

Credentials are read per call, as tavily_client reads its key, so a process that sets
them after import is not unconfigured for its whole life. The base URL is fixed to the
sandbox because this project must never move real money.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
REQUEST_TIMEOUT_SECONDS = 15.0

OK = "ok"
NOT_CONFIGURED = "not_configured"
BAD_INPUT = "bad_input"
AUTH_FAILED = "auth_failed"
HTTP_ERROR = "http_error"
TRANSPORT_ERROR = "transport_error"
BAD_RESPONSE = "bad_response"

# Every status that means PayPal did not accept the request. A caller must not release,
# record a payout or mark a hold voided on any of these.
FAILED_STATUSES = frozenset({
    NOT_CONFIGURED, BAD_INPUT, AUTH_FAILED, HTTP_ERROR, TRANSPORT_ERROR, BAD_RESPONSE,
})


def _credentials() -> tuple[str, str] | None:
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        return None
    return client_id, secret


def configured() -> bool:
    return _credentials() is not None


def _receipt(
    status: str, http_status: int | None = None, body: Any = None, error: str | None = None,
) -> dict[str, Any]:
    return {"status": status, "http_status": http_status, "body": body, "error": error}


# PayPal accepts far less; formatting an amount with an enormous exponent builds a string as long as the exponent.
MAX_AMOUNT = Decimal(10) ** 9


def _money(amount: Any, currency: str) -> dict[str, str] | None:
    # PayPal takes a decimal string. A non-positive, non-numeric or non-finite amount is
    # refused here rather than sent, so a malformed figure never reaches a payment API.
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or not 0 < value <= MAX_AMOUNT or len(currency or "") != 3:
        return None
    return {"currency_code": currency.upper(), "value": f"{value:.2f}"}


async def _call(
    method: str, path: str, body: dict[str, Any] | None, request_id: str,
) -> dict[str, Any]:
    creds = _credentials()
    if creds is None:
        return _receipt(NOT_CONFIGURED, error="PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are not set")
    if not request_id:
        # The request id is PayPal's idempotency key: without it a retry could pay twice.
        return _receipt(BAD_INPUT, error="request_id is required")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            token_resp = await client.post(
                f"{SANDBOX_BASE}/v1/oauth2/token",
                auth=creds, data={"grant_type": "client_credentials"},
            )
            if token_resp.status_code != 200:
                return _receipt(AUTH_FAILED, token_resp.status_code, error="token request refused")
            token = token_resp.json().get("access_token")
            if not token:
                return _receipt(AUTH_FAILED, token_resp.status_code, error="no access_token in reply")
            resp = await client.request(
                method, f"{SANDBOX_BASE}{path}", json=body,
                headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": request_id},
            )
            payload = resp.json() if resp.content else {}
    except httpx.HTTPError as exc:
        return _receipt(TRANSPORT_ERROR, error=type(exc).__name__)
    except ValueError:
        return _receipt(BAD_RESPONSE, error="reply is not JSON")
    if resp.status_code >= 300:
        name = payload.get("name") if isinstance(payload, dict) else None
        return _receipt(HTTP_ERROR, resp.status_code, payload, error=str(name or resp.status_code))
    return _receipt(OK, resp.status_code, payload)


async def create_order(
    amount: Any, currency: str, request_id: str, reference_id: str,
) -> dict[str, Any]:
    money = _money(amount, currency)
    if money is None or not reference_id:
        return _receipt(BAD_INPUT, error="amount, currency and reference_id are required")
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [{"reference_id": reference_id, "amount": money}],
    }
    return await _call("POST", "/v2/checkout/orders", body, request_id)


async def authorize_order(order_id: str, request_id: str) -> dict[str, Any]:
    if not order_id:
        return _receipt(BAD_INPUT, error="order_id is required")
    return await _call("POST", f"/v2/checkout/orders/{order_id}/authorize", {}, request_id)


async def capture_authorization(authorization_id: str, request_id: str) -> dict[str, Any]:
    if not authorization_id:
        return _receipt(BAD_INPUT, error="authorization_id is required")
    path = f"/v2/payments/authorizations/{authorization_id}/capture"
    return await _call("POST", path, {"final_capture": True}, request_id)


async def void_authorization(authorization_id: str, request_id: str) -> dict[str, Any]:
    if not authorization_id:
        return _receipt(BAD_INPUT, error="authorization_id is required")
    path = f"/v2/payments/authorizations/{authorization_id}/void"
    return await _call("POST", path, None, request_id)


async def get_authorization(authorization_id: str, request_id: str) -> dict[str, Any]:
    """What PayPal itself says about an authorization: its state, amount and expiry."""
    if not authorization_id:
        return _receipt(BAD_INPUT, error="authorization_id is required")
    return await _call("GET", f"/v2/payments/authorizations/{authorization_id}", None, request_id)


async def create_payout(
    receiver_email: str, amount: Any, currency: str, request_id: str, note: str,
) -> dict[str, Any]:
    money = _money(amount, currency)
    if money is None or "@" not in (receiver_email or ""):
        return _receipt(BAD_INPUT, error="a receiver email, amount and currency are required")
    body = {
        "sender_batch_header": {
            "sender_batch_id": request_id,
            "email_subject": "Floorline freight payment",
        },
        "items": [{
            "recipient_type": "EMAIL",
            "receiver": receiver_email,
            "amount": {"value": money["value"], "currency": money["currency_code"]},
            "note": note,
            "sender_item_id": request_id,
        }],
    }
    return await _call("POST", "/v1/payments/payouts", body, request_id)


def duplicate_batch_id(receipt: dict[str, Any]) -> str | None:
    """The PayPal batch id named by a refusal of a payout whose sender_batch_id was already used, or None.

    Only a 4xx refusal that says "duplicate" or "already" and links to /v1/payments/payouts/<id> qualifies; the shape of that
    reply is taken from PayPal's documentation and has not been seen on a real reply yet.
    """
    http = receipt.get("http_status")
    body = receipt.get("body")
    if receipt.get("status") != HTTP_ERROR or not isinstance(http, int) or not 400 <= http < 500 or not isinstance(body, dict):
        return None
    said = " ".join(str(body.get(field) or "") for field in ("name", "message")).lower() + str(body.get("details") or "").lower()
    if "duplicate" not in said and "already" not in said:
        return None
    links = body.get("links")
    for link in links if isinstance(links, list) else []:
        href = str(link.get("href") or "") if isinstance(link, dict) else ""
        if "/v1/payments/payouts/" in href:
            batch_id = href.rstrip("/").rsplit("/", 1)[-1]
            return batch_id or None
    return None


async def get_payout_batch(batch_id: str, request_id: str) -> dict[str, Any]:
    """What PayPal itself says about a payout batch and each of its items."""
    if not batch_id:
        return _receipt(BAD_INPUT, error="batch_id is required")
    return await _call("GET", f"/v1/payments/payouts/{batch_id}", None, request_id)


async def add_tracking(
    order_id: str, capture_id: str, tracking_number: str, carrier_name: str, request_id: str,
) -> dict[str, Any]:
    """Attach a bill of lading or container number to a captured order, so PayPal holds proof of shipment."""
    if not (order_id and capture_id and tracking_number and carrier_name):
        return _receipt(BAD_INPUT, error="an order, a capture, a tracking number and a carrier name are required")
    body = {
        "capture_id": capture_id,
        "tracking_number": tracking_number,
        "carrier": "OTHER",
        "carrier_name_other": carrier_name,
        "notify_payer": False,
    }
    return await _call("POST", f"/v2/checkout/orders/{order_id}/track", body, request_id)


async def get_dispute(dispute_id: str, request_id: str) -> dict[str, Any]:
    """What PayPal itself says about a dispute, including the transactions it is about."""
    if not dispute_id:
        return _receipt(BAD_INPUT, error="dispute_id is required")
    return await _call("GET", f"/v1/customer/disputes/{dispute_id}", None, request_id)


async def reauthorize_authorization(authorization_id: str, request_id: str) -> dict[str, Any]:
    """Renew an authorization whose three-day honor period has passed; it expires after 29 days."""
    if not authorization_id:
        return _receipt(BAD_INPUT, error="authorization_id is required")
    path = f"/v2/payments/authorizations/{authorization_id}/reauthorize"
    return await _call("POST", path, {}, request_id)


async def verify_webhook_signature(
    headers: dict[str, str], event: dict[str, Any], webhook_id: str, request_id: str,
) -> dict[str, Any]:
    """Ask PayPal whether a webhook delivery is its own. `headers` are the five PayPal
    transmission headers, keyed in lower case."""
    fields = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
    }
    if not all(fields.values()):
        return _receipt(BAD_INPUT, error="the five transmission headers and a webhook id are required")
    body = {**fields, "webhook_event": event}
    return await _call("POST", "/v1/notifications/verify-webhook-signature", body, request_id)


async def create_order_for_approval(
    amount: Any, currency: str, request_id: str, reference_id: str,
    return_url: str, cancel_url: str, custom_id: str | None = None,
) -> dict[str, Any]:
    """An AUTHORIZE order the shipper approves on PayPal, then returns to return_url.

    custom_id, when given, is the id of the shipper's mandate: PayPal keeps it on the order and echoes it.
    """
    money = _money(amount, currency)
    if money is None or not reference_id or not return_url or not cancel_url:
        return _receipt(
            BAD_INPUT,
            error="amount, currency, reference_id, return_url and cancel_url are required",
        )
    unit: dict[str, Any] = {"reference_id": reference_id, "amount": money}
    if custom_id:
        unit["custom_id"] = custom_id
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [unit],
        "payment_source": {"paypal": {"experience_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
        }}},
    }
    return await _call("POST", "/v2/checkout/orders", body, request_id)
