"""
Routes for the payment controls (payment_controls.py).

Anyone with the operator role may pause payments; resuming them and publishing limits need
a governance admin. All three need a password sign-in, so a one-click guest session cannot
touch them, and each is recorded against the signed-in person with the reason given. The
reading route is for anyone signed in.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from vf_logistics import payment_controls


def _plain(value: Any) -> Any:
    """Decimals as strings, so a total never loses a cent to a float."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "is_finite"):
        return str(value)
    return value


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

    @app.route("/api/v1/payments/controls", methods=["GET"])
    @require_viewer
    def payments_controls() -> Any:
        async def read() -> dict[str, Any]:
            live = await payment_controls.limits(tenant_id=tenant())
            return {
                "pause": await payment_controls.pause_state(tenant_id=tenant()),
                "limits": live,
                "limits_are_defaults": live == payment_controls.DEFAULT_LIMITS,
                "usage_today": await payment_controls.usage_today(tenant_id=tenant()),
            }

        try:
            return jsonify(_plain(run_async(read()))), 200
        except payment_controls.ControlsError as exc:
            return jsonify({"error": exc.message}), exc.status

    @app.route("/api/v1/payments/pause", methods=["POST"])
    @require_operator
    @require_password_session
    @limiter.limit("10 per minute")
    def payments_pause() -> Any:
        actor, actor_auth = person()
        try:
            state = run_async(payment_controls.pause(
                body().get("reason"), actor, actor_auth, tenant_id=tenant(),
            ))
        except payment_controls.ControlsError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify(state), 200

    @app.route("/api/v1/payments/resume", methods=["POST"])
    @require_governance_admin
    @require_password_session
    @limiter.limit("10 per minute")
    def payments_resume() -> Any:
        actor, actor_auth = person()
        try:
            state = run_async(payment_controls.resume(
                body().get("reason"), actor, actor_auth, tenant_id=tenant(),
            ))
        except payment_controls.ControlsError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify(state), 200

    @app.route("/api/v1/payments/limits", methods=["POST"])
    @require_governance_admin
    @require_password_session
    @limiter.limit("10 per minute")
    def payments_limits() -> Any:
        actor, actor_auth = person()
        data = body()
        try:
            published = run_async(payment_controls.publish_limits(
                data.get("limits"), data.get("reason"), actor, actor_auth, tenant_id=tenant(),
            ))
        except payment_controls.ControlsError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify({"limits": published}), 200
