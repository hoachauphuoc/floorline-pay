"""
The settler pays the registry's payee and only the registry's payee (settler.py with
carrier_registry.py).

The payee on a shipment event, the payee it says is on file and the date it says the payee
changed all come from whoever sent the event. Pinned here, with the real registry and no
stand-in for it: money goes to the address the registry verified; a sender who states the
registry's role for themselves is held; a carrier the registry does not know is held; a
payee changed inside the cooling-off window is held, however old the sender says the change
is; and nothing is captured or paid on any of those paths.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics.store import get_store

OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
NEW_PAYEE = "accounts@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"
CAPTURED = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
LIVE = {"status": "ok", "http_status": 200, "error": None,
        "body": {"id": "A", "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}}


def run(coro):
    return asyncio.run(coro)


def modules():
    return (
        importlib.import_module("vf_logistics.settler"),
        importlib.import_module("vf_logistics.carrier_registry"),
        importlib.import_module("vf_logistics.payment_binding"),
    )


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def register_and_verify(name, payee):
    _, registry, _ = modules()
    made = run(registry.register_payee(name, payee, OPERATOR, "password"))
    run(registry.verify_payee(made["carrier_key"], made["version"], REVIEWER, "password", NOTE))


def released(name, **shipment):
    """A released payment case for the carrier, with a booking for its authorization."""
    _, _, binding = modules()
    authorization = f"AUTH{uuid.uuid4().hex[:12].upper()}"
    body = {
        "shipment_id": "SHP-SET-1", "currency": "USD", "shipping_cost": 1300.0, "payout_amount": 1250.0,
        "carrier_name": name, "paypal_authorization_id": authorization, **shipment,
    }
    run(binding.record_authorization(authorization, "SHP-SET-1", "1300.00", "USD", "ORDER-1"))
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": body}))
    return {"case_id": case_id, "outcome": "RELEASED_BY_HUMAN", "approval_id": "apr-1",
            "idempotency_key": f"exec-{uuid.uuid4().hex[:8]}"}


@pytest.fixture
def pp(monkeypatch):
    settler = importlib.import_module("vf_logistics.settler")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    client = settler.paypal_client
    with patch.object(client, "get_authorization", AsyncMock(return_value=LIVE)), \
            patch.object(client, "capture_authorization", AsyncMock(return_value=CAPTURED)) as capture, \
            patch.object(client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout:
        yield settler, capture, payout


def test_the_carrier_is_paid_at_the_address_the_registry_verified(pp):
    settler, capture, payout = pp
    name = carrier()
    register_and_verify(name, PAYEE)
    result = run(settler.settle(released(name, carrier_payee_email=PAYEE.upper())))
    assert result["status"] == "settled"
    assert payout.await_args.args[0] == PAYEE


def test_the_registry_payee_is_paid_when_the_event_names_none(pp):
    settler, capture, payout = pp
    name = carrier()
    register_and_verify(name, PAYEE)
    assert run(settler.settle(released(name)))["status"] == "settled"
    assert payout.await_args.args[0] == PAYEE


def test_a_sender_who_states_the_payee_on_file_for_themselves_is_held(pp):
    settler, capture, payout = pp
    name = carrier()
    register_and_verify(name, PAYEE)
    decision = released(
        name, carrier_payee_email=ATTACKER, carrier_registered_payee_email=ATTACKER,
    )
    result = run(settler.settle(decision))
    assert result["status"] == "held"
    assert "PAYEE_MISMATCH" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_carrier_the_registry_does_not_know_is_held_whatever_the_event_says(pp):
    settler, capture, payout = pp
    decision = released(
        carrier(), carrier_payee_email=ATTACKER, carrier_registered_payee_email=ATTACKER,
        carrier_payee_changed_at="2020-01-01T00:00:00+00:00",
    )
    result = run(settler.settle(decision))
    assert result["status"] == "held"
    assert "PAYEE_UNVERIFIED" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_payee_still_waiting_for_verification_is_not_paid(pp):
    settler, capture, payout = pp
    name = carrier()
    _, registry, _ = modules()
    run(registry.register_payee(name, PAYEE, OPERATOR, "password"))
    result = run(settler.settle(released(name, carrier_payee_email=PAYEE)))
    assert result["status"] == "held"
    assert "PAYEE_UNVERIFIED" in result["reason"]
    payout.assert_not_called()


def test_a_payee_changed_a_moment_ago_is_held_however_old_the_sender_says_the_change_is(pp):
    settler, capture, payout = pp
    name = carrier()
    register_and_verify(name, PAYEE)
    register_and_verify(name, NEW_PAYEE)
    decision = released(
        name, carrier_payee_email=NEW_PAYEE, carrier_payee_changed_at="2020-01-01T00:00:00+00:00",
    )
    result = run(settler.settle(decision))
    assert result["status"] == "held"
    assert "PAYEE_RECENTLY_CHANGED" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_the_old_payee_is_still_the_payee_until_the_change_is_verified(pp):
    settler, capture, payout = pp
    name = carrier()
    register_and_verify(name, PAYEE)
    _, registry, _ = modules()
    run(registry.register_payee(name, NEW_PAYEE, OPERATOR, "password"))
    result = run(settler.settle(released(name, carrier_payee_email=NEW_PAYEE)))
    assert result["status"] == "held"
    assert "PAYEE_MISMATCH" in result["reason"]
    payout.assert_not_called()
