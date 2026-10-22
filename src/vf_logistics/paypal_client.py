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


def _money(amount: Any, currency: str) -> dict[str, str] | None:
    # PayPal takes a decimal string. A non-positive, non-numeric or non-finite amount is
    # refused here rather than sent, so a malformed figure never reaches a payment API.
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0 or len(currency or "") != 3:
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
