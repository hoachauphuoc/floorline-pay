"""
The attack theatre route (defense_scenarios.py): GET /api/v1/defense/scenarios.

For anyone signed in. It replays ten attacks against the payment layers in memory and reports which layer
stopped each; it reads and writes nothing and cannot move money.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify

from vf_logistics import defense_scenarios, payment_eval


def register(app: Flask, require_viewer: Callable[..., Any], run_async: Callable[[Any], Any]) -> None:
    @app.route("/api/v1/defense/scenarios", methods=["GET"])
    @require_viewer
    def defense_list() -> Any:
        rows = run_async(defense_scenarios.run())
        return jsonify({
            "scenarios": rows,
            "stopped": sum(1 for r in rows if r["outcome"] != defense_scenarios.NOT_STOPPED),
            "of": len(rows),
            "note": "A replay on invented data: it reads and writes nothing and calls neither PayPal nor a model.",
        }), 200

    @app.route("/api/v1/defense/evaluation", methods=["GET"])
    @require_viewer
    def defense_evaluation() -> Any:
        return jsonify({
            "synthetic": True,
            "flag_floor": payment_eval.FLAG_FLOOR,
            "summary": run_async(payment_eval.offline_summary()),
            "note": "Invented payouts and carriers, built from the patterns the checks were written for: the rates describe this corpus only. The analyst model is not part of this table until it has been run against it.",
        }), 200
