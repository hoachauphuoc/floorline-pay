"""
The attack theatre (defense_scenarios.py) and its route (defense_routes.py).

Pinned here: ten attacks are replayed, eight live and two named as proved by a test; every live attack is
stopped by the layer it names and with the finding that proves it; the model that says "normal" does not lower
the floor and does not raise a new carrier's standing; every row says no money moved; the replay reads no
store and calls neither PayPal nor a model; and the route answers anyone signed in with the same rows.

The modules are imported inside each test so that, before they exist, every test fails instead of the file
failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402

IDS = [
    "payee-swap", "bec-request", "recent-change", "inflated-payout", "shell-carrier",
    "model-says-fine", "lone-release", "expired-authorization", "unbound-authorization", "disputed-carrier",
]
FINDINGS = {
    "payee-swap": "PAYEE_MISMATCH",
    "bec-request": "PAYEE_CHANGE_REQUEST_FLAGGED",
    "recent-change": "PAYEE_RECENTLY_CHANGED",
    "inflated-payout": "PAYOUT_ABOVE_FREIGHT",
    "shell-carrier": "CARRIER_NOT_FOUND_ONLINE",
    "lone-release": "requires_second",
    "expired-authorization": "expired",
}


def replay():
    module = importlib.import_module("vf_logistics.defense_scenarios")
    return module, asyncio.run(module.run())


def test_ten_attacks_are_replayed_in_order():
    _, rows = replay()
    assert [r["id"] for r in rows] == IDS
    for row in rows:
        assert {"title", "attack", "layer", "live", "outcome", "findings", "money_moved_usd", "proof", "detail"} <= set(row)


def test_every_live_attack_is_stopped_and_named_by_the_finding_that_proves_it():
    module, rows = replay()
    for row in rows:
        assert row["outcome"] != module.NOT_STOPPED, row["id"]
        if row["id"] in FINDINGS:
            assert FINDINGS[row["id"]] in row["findings"], row["id"]
    live = [r["id"] for r in rows if r["live"]]
    assert live == IDS[:8]


def test_the_two_attacks_that_need_the_store_say_which_test_proves_them():
    _, rows = replay()
    static = {r["id"]: r for r in rows if not r["live"]}
    assert set(static) == {"unbound-authorization", "disputed-carrier"}
    assert static["unbound-authorization"]["proof"] == "tests/test_settler_gate.py"
    assert static["disputed-carrier"]["proof"] == "tests/test_settler_settlement.py"


def test_the_attacks_the_floor_catches_are_held_at_or_above_the_flag_line():
    module, rows = replay()
    payment_eval = importlib.import_module("vf_logistics.payment_eval")
    for row in rows:
        if row["id"] in ("payee-swap", "bec-request", "recent-change", "inflated-payout", "shell-carrier", "model-says-fine"):
            assert row["floor"] >= payment_eval.FLAG_FLOOR, row["id"]
            assert row["outcome"] != module.NOT_STOPPED


def test_a_model_that_says_normal_does_not_lower_the_floor_or_raise_a_new_carriers_standing():
    _, rows = replay()
    row = next(r for r in rows if r["id"] == "model-says-fine")
    assert row["floor"] >= 80
    assert "standing stays C" in row["outcome"]
    assert "stays at standing C" in row["detail"]


def test_the_bec_row_says_why_the_message_was_flagged():
    _, rows = replay()
    row = next(r for r in rows if r["id"] == "bec-request")
    assert "asks not to be called back" in row["detail"]
    assert "pressure to act at once" in row["detail"]


def test_no_money_moves_in_any_row():
    _, rows = replay()
    assert {r["money_moved_usd"] for r in rows} == {"0.00"}


def test_the_replay_reads_no_store_and_calls_neither_paypal_nor_a_model(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the replay must not touch this")

    async def aboom(*args, **kwargs):
        raise AssertionError("the replay must not touch this")

    for target in (
        "vf_logistics.credit_profile.get_store", "vf_logistics.payment_freeze.get_store",
        "vf_logistics.payout_tracking.get_store", "vf_logistics.payment_ai.nebius_client.complete_json",
        "vf_logistics.paypal_client._call", "vf_logistics.model_armor.screen",
    ):
        monkeypatch.setattr(target, aboom if "complete_json" in target or target.endswith(("_call", "screen")) else boom)
    _, rows = replay()
    assert len(rows) == 10


class TestTheRoute(Base):
    def test_anyone_signed_in_reads_the_replay(self):
        from vf_logistics.app import app

        r = app.test_client().get("/api/v1/defense/scenarios", headers={"X-VF-API-Key": TEST_KEY})
        assert r.status_code == 200
        body = r.get_json()
        assert [s["id"] for s in body["scenarios"]] == IDS
        assert (body["stopped"], body["of"]) == (10, 10)
        assert "invented data" in body["note"]

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    def test_it_only_reads(self, method):
        from vf_logistics.app import app

        r = getattr(app.test_client(), method)("/api/v1/defense/scenarios", headers={"X-VF-API-Key": TEST_KEY}, json={})
        assert r.status_code == 405
