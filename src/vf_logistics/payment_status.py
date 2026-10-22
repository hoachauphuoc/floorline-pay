"""
The payment state of a case, for the console: GET /api/v1/payments/cases/<case_id>.

It reads what settler.py recorded, never PayPal. The newest settle_payment audit row of the
case names the outcome (paid, voided, held, pending, failed); a payment case with no such
row is waiting for a person's decision. A status this module does not know is reported as
"unknown" rather than mapped to a friendlier one, so a new settler status cannot be shown
as a success by default.

The reply carries PayPal ids and finding codes only. It never carries a payee address.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify

from vf_logistics import payment_checks
from vf_logistics.store import get_store

_STATE = {
    "settled": "paid",
    "voided": "voided",
    "held": "held",
    "retry": "pending",
    "failed": "failed",
}


async def payment_state(case_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """The payment state of one case, or None when there is no such case."""
    store = get_store()
    case = await store.get_case(case_id, tenant_id=tenant_id)
    if case is None:
        return None
    shipment = case.get("shipment") or {}
    if not payment_checks.is_payment_case(shipment):
        return {
            "case_id": case_id, "payment_case": False, "state": "none",
            "reason": "the case moves no money",
        }
    rows, _ = await store.query_audit(
        case_id=case_id, action="settle_payment", limit=20, tenant_id=tenant_id,
    )
    if rows:
        latest = rows[0]
        detail = latest.get("detail") or {}
        return {
            "case_id": case_id,
            "payment_case": True,
            "state": _STATE.get(str(latest.get("status")), "unknown"),
            "reason": detail.get("reason"),
            "capture": detail.get("capture"),
            "payout": detail.get("payout"),
            "void": detail.get("void"),
            "at": latest.get("at"),
        }
    findings = [f["code"] for f in payment_checks.check_payment(shipment)]
    has_authorization = bool(shipment.get("paypal_authorization_id"))
    return {
        "case_id": case_id,
        "payment_case": True,
        "state": "awaiting_decision" if has_authorization else "no_authorization",
        "reason": (
            "the funds are held; a person has not decided yet"
            if has_authorization else "no PayPal authorization is held for this case"
        ),
        "findings": findings,
    }


def register(
    app: Flask, require_viewer: Callable[..., Any], run_async: Callable[[Any], Any],
    tenant: Callable[[], str | None],
) -> None:
    @app.route("/api/v1/payments/cases/<case_id>", methods=["GET"])
    @require_viewer
    def payments_case_state(case_id: str) -> Any:
        state = run_async(payment_state(case_id, tenant_id=tenant()))
        if state is None:
            return jsonify({"error": "case not found"}), 404
        return jsonify(state), 200
