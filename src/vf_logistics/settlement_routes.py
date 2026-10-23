"""
Routes for what happens after the money moves: PayPal's webhook, the freezes it can cause,
and the state of a payout.

The webhook (POST /api/v1/paypal/webhook) has no session and no API key: PayPal can present
neither, so paypal_webhook.receive believes nothing until PayPal itself has verified the
signature. Everything else needs a signed-in role. Anyone signed in reads the frozen parties;
lifting a freeze needs a governance admin and a password sign-in, so a one-click guest
session cannot loosen anything; asking PayPal for a payout's state needs an operator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from vf_logistics import payment_freeze, paypal_webhook, payout_tracking


def register(
    app: Flask,
    limiter: Any,
    require_viewer: Callable[..., Any],
    require_operator: Callable[..., Any],
    require_governance_admin: Callable[..., Any],
    require_password_session: Callable[..., Any],
    run_async: Callable[[Any], Any],
    tenant: Callable[[], str | None],
    get_auth_context: Callable[[], Any],
) -> None:
    def person() -> tuple[str, str | None]:
        context = get_auth_context()
        if context is None or not context.acts_for_a_person:
            return "", None
        return str(context.email), context.actor_auth

    def body() -> dict[str, Any]:
        raw = request.get_json(silent=True)
        return raw if isinstance(raw, dict) else {}

    @app.route("/api/v1/paypal/webhook", methods=["POST"])
    @limiter.limit("120 per minute")
    def paypal_webhook_receive() -> Any:
        status, reply = run_async(paypal_webhook.receive(dict(request.headers.items()), request.get_json(silent=True)))
        return jsonify(reply), status

    # Not behind a role decorator: PayPal presents only its signature, which receive()
    # verifies. Declared operator, the role this write carries, so route_policy() and the
    # Access Control screen do not call it public (the same arrangement as receiver_decision).
    paypal_webhook_receive._required_role = "operator"

    @app.route("/api/v1/payments/freezes", methods=["GET"])
    @require_viewer
    def payments_freezes() -> Any:
        return jsonify({"frozen": run_async(payment_freeze.frozen_parties(tenant_id=tenant()))}), 200

    @app.route("/api/v1/payments/freezes/lift", methods=["POST"])
    @require_governance_admin
    @require_password_session
    @limiter.limit("10 per minute")
    def payments_freeze_lift() -> Any:
        actor, actor_auth = person()
        data = body()
        try:
            run_async(payment_freeze.lift(
                str(data.get("kind") or ""), data.get("name"), data.get("reason"),
                actor, actor_auth, tenant_id=tenant(),
            ))
        except payment_freeze.FreezeError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify({"frozen": run_async(payment_freeze.frozen_parties(tenant_id=tenant()))}), 200

    @app.route("/api/v1/payments/cases/<case_id>/payout/refresh", methods=["POST"])
    @require_operator
    @limiter.limit("20 per minute")
    def payments_payout_refresh(case_id: str) -> Any:
        try:
            state = run_async(payout_tracking.refresh(case_id, tenant_id=tenant()))
        except payout_tracking.PayoutError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify(state), 200
