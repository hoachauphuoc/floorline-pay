"""
The settler and what PayPal says about the money it holds (settler.py with
authorization_lifecycle.py and payment_freeze.py).

Pinned here: an authorization inside its honor period is captured as it is; one past it is
renewed first and captured only if PayPal renews it; one past 29 days, or whose age PayPal does
not say, is not captured; and a release for a carrier or a shipper that is frozen is held,
while a block still voids the authorization so the shipper's money goes back.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import SHIPMENT, decision, new_case, pp, run  # noqa: E402, F401

CREATED = {"status": "ok", "http_status": 200, "body": {"id": "0VF52814937998046", "status": "CREATED"}, "error": None}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}
DOWN = {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}


def freezes():
    return importlib.import_module("vf_logistics.payment_freeze")


def names():
    tag = uuid.uuid4().hex[:6]
    return f"Mekong Haulage {tag} Co", f"Saigon Plywood {tag} Co"


def case_for(carrier, shipper, state="RELEASED_BY_HUMAN"):
    return new_case(state=state, shipment={**SHIPMENT, "carrier_name": carrier, "shipper_name": shipper})


@pytest.fixture
def stage(pp, monkeypatch):  # noqa: F811
    settler = pp[0]
    renew = AsyncMock(return_value=CREATED)
    monkeypatch.setattr(settler.paypal_client, "reauthorize_authorization", renew)

    def set_stage(value):
        monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: value)

    return set_stage, renew


def test_an_authorization_inside_its_honor_period_is_captured_without_renewing(pp, stage):  # noqa: F811
    settler, capture, payout, void = pp
    set_stage, renew = stage
    set_stage("honored")
    assert run(settler.settle(decision(new_case())))["status"] == "settled"
    renew.assert_not_called()


def test_an_authorization_past_its_honor_period_is_renewed_then_captured(pp, stage):  # noqa: F811
    settler, capture, payout, void = pp
    set_stage, renew = stage
    set_stage("reauthorize")
    d = decision(new_case())
    assert run(settler.settle(d))["status"] == "settled"
    renew.assert_awaited_once_with("0VF52814937998046", f"{d['idempotency_key']}-reauth")
    capture.assert_awaited_once()


@pytest.mark.parametrize("receipt, status, phrase", [
    (REFUSED, "failed", "could not renew"),
    (DOWN, "retry", "could not renew"),
    ({**CREATED, "body": {"status": "DENIED"}}, "failed", "could not renew"),
])
def test_an_authorization_that_cannot_be_renewed_is_not_captured(pp, stage, receipt, status, phrase):  # noqa: F811
    settler, capture, payout, void = pp
    set_stage, renew = stage
    set_stage("reauthorize")
    renew.return_value = receipt
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == status
    assert phrase in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_an_expired_authorization_is_not_captured(pp, stage):  # noqa: F811
    settler, capture, payout, void = pp
    set_stage, renew = stage
    set_stage("expired")
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "failed"
    assert "expired" in result["reason"]
    renew.assert_not_called()
    capture.assert_not_called()


def test_an_authorization_whose_age_paypal_does_not_say_is_held(pp, stage):  # noqa: F811
    settler, capture, payout, void = pp
    set_stage, renew = stage
    set_stage("unknown")
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "held"
    assert "does not say when" in result["reason"]
    capture.assert_not_called()


@pytest.mark.parametrize("kind", ["carrier", "shipper"])
def test_a_release_for_a_frozen_party_is_held(pp, kind):  # noqa: F811
    settler, capture, payout, void = pp
    carrier, shipper = names()
    run(freezes().freeze(kind, carrier if kind == "carrier" else shipper, "a dispute was opened", "event-1"))
    result = run(settler.settle(decision(case_for(carrier, shipper))))
    assert result["status"] == "held"
    assert f"payments to this {kind} are frozen" in result["reason"]
    assert "a dispute was opened" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_release_goes_through_once_the_freeze_is_lifted(pp, stage):  # noqa: F811
    settler, capture, payout, void = pp
    carrier, shipper = names()
    run(freezes().freeze("carrier", carrier, "a dispute was opened", "event-1"))
    run(freezes().lift("carrier", carrier, "settled with the carrier", "admin@forwarder.example", "password"))
    stage[0]("honored")
    assert run(settler.settle(decision(case_for(carrier, shipper))))["status"] == "settled"


def test_a_block_still_voids_the_authorization_of_a_frozen_party(pp):  # noqa: F811
    settler, capture, payout, void = pp
    carrier, shipper = names()
    run(freezes().freeze("carrier", carrier, "a dispute was opened", "event-1"))
    d = decision(case_for(carrier, shipper, state="BLOCKED_BY_HUMAN"), outcome="BLOCKED_BY_HUMAN")
    assert run(settler.settle(d))["status"] == "voided"
    void.assert_awaited_once()
