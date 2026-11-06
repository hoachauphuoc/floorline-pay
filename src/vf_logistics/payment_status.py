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

from vf_logistics import carrier_registry, credit_profile, dual_approval, payment_checks, payout_tracking, settlement_retry
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
        case_id=case_id, limit=100, tenant_id=tenant_id,
    )
    rows = [r for r in rows if r.get("action") == "settle_payment"]
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
            "paid_by_agent": detail.get("agent"),
            "handed_to_person": detail.get("handed_to_person") is True,
            "payout_status": await payout_tracking.confirmation(case_id, tenant_id=tenant_id),
            "at": latest.get("at"),
        }
    shipment = await carrier_registry.enrich(dict(shipment), tenant_id=tenant_id)
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
        "decision_not_sent": await settlement_retry.unsent_decision(case, tenant_id) is not None,
    }


async def payment_ledger(tenant_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """
    The newest settlement outcome of every case that has one, and a count per state.

    Read from the settle_payment audit rows, newest first, so a case that was retried and
    then settled appears once, as paid. A status this module does not know is "unknown".
    """
    rows, _ = await get_store().query_audit(
        action="settle_payment", limit=limit, tenant_id=tenant_id,
    )
    newest: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if case_id and case_id not in newest:
            newest[case_id] = row
    payouts = await payout_tracking.confirmations(list(newest), tenant_id)
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for case_id, row in newest.items():
        detail = row.get("detail") or {}
        state = _STATE.get(str(row.get("status")), "unknown")
        counts[state] = counts.get(state, 0) + 1
        items.append({
            "case_id": case_id,
            "state": state,
            "reason": detail.get("reason"),
            "capture": detail.get("capture"),
            "payout": detail.get("payout"),
            "void": detail.get("void"),
            "at": row.get("at"),
            "payout_status": payouts[case_id],
        })
    decided, _ = await get_store().query_cases(states=tuple(settlement_retry.HUMAN_ACTION), limit=limit, tenant_id=tenant_id)
    for case in decided:
        case_id = str(case.get("case_id") or "")
        if case_id in newest or await settlement_retry.unsent_decision(case, tenant_id) is None:
            continue
        counts["decision_not_sent"] = counts.get("decision_not_sent", 0) + 1
        items.append({
            "case_id": case_id,
            "state": "decision_not_sent",
            "reason": "a person's decision was saved but never reached the settler; send it again from the case",
            "at": case.get("updated_at"),
        })
    return {"items": items, "counts": counts}


async def payment_risk(case_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """
    What the payment layers say about one case: the findings of the checks and of the AI answers stored on
    it, its carrier's credit standing and cap, and who has approved a release that needs two.

    No finding quotes a payee address. None when there is no such case.
    """
    case = await get_store().get_case(case_id, tenant_id=tenant_id)
    if case is None:
        return None
    shipment = case.get("shipment") or {}
    if not payment_checks.is_payment_case(shipment):
        return {"case_id": case_id, "payment_case": False}
    shipment = await carrier_registry.enrich(dict(shipment), tenant_id=tenant_id)
    ai = case.get("payment_ai")
    found = payment_checks.check_payment(shipment) + payment_checks.check_payment_ai(shipment, ai)
    profile = await credit_profile.profile(shipment.get("carrier_name"), ai, tenant_id)
    approvals = (case.get("verification") or {}).get("approvals") or []
    release = next(
        (a for a in reversed(approvals) if isinstance(a, dict) and a.get("action") == "release" and a.get("status") == "VALID"),
        None,
    )
    second = release.get("second_approval") if release else None
    cap = profile["cap_usd"]
    return {
        "case_id": case_id,
        "payment_case": True,
        "findings": [
            {"code": f["code"], "severity": f["severity"], "floor": f["floor"], "detail": f["detail"]} for f in found
        ],
        "analyst": ai.get("analyst") if isinstance(ai, dict) else None,
        "identity": ai.get("identity") if isinstance(ai, dict) else None,
        "credit": {
            "tier": profile["tier"], "baseline": profile["baseline"], "suggested": profile["suggested"],
            "cap_usd": None if cap is None else str(cap), "paid_count": profile["paid_count"], "frozen": profile["frozen"],
        },
        "dual_approval": {
            "needed": dual_approval.needed(shipment),
            "threshold_usd": str(dual_approval.threshold()),
            "first_approver": release.get("approver") if release else None,
            "second_approver": second.get("approver") if isinstance(second, dict) else None,
        },
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


    @app.route("/api/v1/payments/ledger", methods=["GET"])
    @require_viewer
    def payments_ledger() -> Any:
        return jsonify(run_async(payment_ledger(tenant_id=tenant()))), 200

    @app.route("/api/v1/payments/cases/<case_id>/risk", methods=["GET"])
    @require_viewer
    def payments_case_risk(case_id: str) -> Any:
        risk = run_async(payment_risk(case_id, tenant_id=tenant()))
        if risk is None:
            return jsonify({"error": "case not found"}), 404
        return jsonify(risk), 200
