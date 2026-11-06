"""
Routes for the carrier payee registry (carrier_registry.py).

A person with the operator role registers a payee; a different person, reviewer or above,
verifies it after calling the carrier back and recording what they were told. Both steps
need a password sign-in (the one-click guest session cannot move a payee) and are recorded
against the signed-in person, never against a name in the request. A refusal answers with
the registry's own status and message.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from vf_logistics import carrier_registry

_KEY = re.compile(r"^[a-z0-9]{1,80}$")


def register(
    app: Flask,
    limiter: Any,
    require_reviewer: Callable[..., Any],
    require_operator: Callable[..., Any],
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

    @app.route("/api/v1/carriers", methods=["GET"])
    @require_reviewer
    def carriers_list() -> Any:
        return jsonify({"items": run_async(carrier_registry.carriers(tenant_id=tenant()))}), 200

    @app.route("/api/v1/carriers/payee", methods=["POST"])
    @require_operator
    @require_password_session
    @limiter.limit("20 per minute")
    def carriers_register_payee() -> Any:
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        actor, actor_auth = person()
        try:
            result = run_async(carrier_registry.register_payee(
                body.get("carrier_name"), body.get("payee_email"), actor, actor_auth,
                request_text=body.get("request_text"),
                tenant_id=tenant(),
            ))
        except carrier_registry.RegistryError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify(result), 201

    @app.route("/api/v1/carriers/<carrier_key>/payee/<version>/verify", methods=["POST"])
    @require_reviewer
    @require_password_session
    @limiter.limit("20 per minute")
    def carriers_verify_payee(carrier_key: str, version: str) -> Any:
        if not _KEY.match(carrier_key):
            return jsonify({"error": "carrier_key is not a carrier key"}), 400
        if not (version.isascii() and version.isdigit() and len(version) <= 9):
            return jsonify({"error": "version is not a version number"}), 400
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        actor, actor_auth = person()
        try:
            result = run_async(carrier_registry.verify_payee(
                carrier_key, int(version), actor, actor_auth, body.get("callback_note"),
                tenant_id=tenant(),
            ))
        except carrier_registry.RegistryError as exc:
            return jsonify({"error": exc.message}), exc.status
        return jsonify(result), 200
