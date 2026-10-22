"""
What the settler proves before it captures, and what it requires before it pays (settler.py).

The authorization id on a shipment is typed by whoever sent the event, so a release is not
enough on its own. Pinned here: the authorization must have been recorded by a booking for
this very shipment, in an amount and currency that cover the payout; PayPal must still
report it capturable and large enough; and the carrier is paid only after PayPal reports the
capture COMPLETED. Every other path is a named status and nothing is captured or paid.

settler is imported inside each test so that, before the change, the tests fail on the
missing behaviour and not on collection.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics.store import get_store

PAYEE = "payments@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"
CAPTURED_OK = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}
DOWN = {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}


def live(state="CREATED", value="1300.00", currency="USD"):
    return {"status": "ok", "http_status": 200, "error": None,
            "body": {"id": "A", "status": state, "amount": {"currency_code": currency, "value": value}}}


def capture_reply(state):
    body = {"id": "CAP-1"} if state is None else {"id": "CAP-1", "status": state}
    return {"status": "ok", "http_status": 201, "body": body, "error": None}


def run(coro):
    return asyncio.run(coro)


class Booked:
    """A released payment case whose authorization a booking recorded."""

    def __init__(self, reference="SHP-GATE-1", amount="1300.00", currency="USD", **shipment):
        self.authorization = f"AUTH{uuid.uuid4().hex[:12].upper()}"
        self.shipment = {
            "shipment_id": "SHP-GATE-1", "currency": "USD", "shipping_cost": 1300.0,
            "payout_amount": 1250.0, "carrier_payee_email": PAYEE,
            "carrier_registered_payee_email": PAYEE,
            "carrier_payee_changed_at": "2026-08-01T08:00:00+07:00",
            "paypal_authorization_id": self.authorization, **shipment,
        }
        if reference is not None:
            run(importlib.import_module("vf_logistics.payment_binding").record_authorization(
                self.authorization, reference, amount, currency, "ORDER-1"))
        self.case_id = f"CASE-{uuid.uuid4().hex[:10]}"
        run(get_store().put_case({"case_id": self.case_id, "state": "RELEASED_BY_HUMAN",
                                  "shipment": dict(self.shipment)}))
        self.decision = {"case_id": self.case_id, "outcome": "RELEASED_BY_HUMAN",
                         "approval_id": "apr-1", "idempotency_key": f"exec-{uuid.uuid4().hex[:8]}"}


@pytest.fixture
def pp(monkeypatch):
    settler = importlib.import_module("vf_logistics.settler")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    client = settler.paypal_client
    with patch.object(client, "get_authorization", AsyncMock(return_value=live())) as verify, \
            patch.object(client, "capture_authorization", AsyncMock(return_value=CAPTURED_OK)) as capture, \
            patch.object(client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout:
        yield settler, verify, capture, payout


def nothing_moved(capture, payout):
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_booked_authorization_that_paypal_confirms_is_captured_and_paid(pp):
    settler, verify, capture, payout = pp
    booked = Booked()
    result = run(settler.settle(booked.decision))
    assert result["status"] == "settled"
    key = booked.decision["idempotency_key"]
    verify.assert_awaited_once_with(booked.authorization, f"{key}-verify")
    capture.assert_awaited_once_with(booked.authorization, f"{key}-capture")
    payout.assert_awaited_once()


def test_an_authorization_no_booking_recorded_is_held(pp):
    settler, verify, capture, payout = pp
    booked = Booked(reference=None)
    result = run(settler.settle(booked.decision))
    assert result["status"] == "held"
    assert "no booking made through Floorline" in result["reason"]
    verify.assert_not_called()
    nothing_moved(capture, payout)


def test_an_authorization_booked_for_another_shipment_is_held(pp):
    settler, verify, capture, payout = pp
    booked = Booked(reference="SHP-SOMEONE-ELSE")
    result = run(settler.settle(booked.decision))
    assert result["status"] == "held"
    assert "different shipment" in result["reason"]
    verify.assert_not_called()
    nothing_moved(capture, payout)


@pytest.mark.parametrize("amount,currency", [("1000.00", "USD"), ("1300.00", "EUR"), ("not a number", "USD")])
def test_a_booking_that_does_not_cover_the_payout_is_held(pp, amount, currency):
    settler, verify, capture, payout = pp
    booked = Booked(amount=amount, currency=currency)
    result = run(settler.settle(booked.decision))
    assert result["status"] == "held"
    assert "holds less than the payout" in result["reason"]
    nothing_moved(capture, payout)


def test_the_payment_checks_are_settled_before_paypal_is_asked_anything(pp):
    settler, verify, capture, payout = pp
    booked = Booked(carrier_payee_email=ATTACKER)
    result = run(settler.settle(booked.decision))
    assert result["status"] == "held"
    assert "PAYEE_MISMATCH" in result["reason"]
    verify.assert_not_called()
    nothing_moved(capture, payout)


def test_a_paypal_refusal_to_confirm_the_authorization_pays_nobody(pp):
    settler, verify, capture, payout = pp
    verify.return_value = REFUSED
    result = run(settler.settle(Booked().decision))
    assert result["status"] == "failed"
    assert "could not confirm the authorization" in result["reason"]
    nothing_moved(capture, payout)


def test_an_unreachable_paypal_asks_for_redelivery(pp):
    settler, verify, capture, payout = pp
    verify.return_value = DOWN
    assert run(settler.settle(Booked().decision))["status"] == "retry"
    nothing_moved(capture, payout)


@pytest.mark.parametrize("state", ["VOIDED", "EXPIRED", "PENDING", "DENIED", "CREATEDX"])
def test_an_authorization_paypal_reports_in_another_state_is_never_captured(pp, state):
    settler, verify, capture, payout = pp
    verify.return_value = live(state=state)
    result = run(settler.settle(Booked().decision))
    assert result["status"] == "failed"
    assert f"authorization as {state}" in result["reason"]
    nothing_moved(capture, payout)


def test_an_authorization_with_no_state_is_never_captured(pp):
    settler, verify, capture, payout = pp
    verify.return_value = {"status": "ok", "http_status": 200, "body": {}, "error": None}
    result = run(settler.settle(Booked().decision))
    assert result["status"] == "failed"
    assert "authorization as unknown" in result["reason"]
    nothing_moved(capture, payout)


def test_an_authorization_paypal_says_holds_less_than_the_payout_is_held(pp):
    settler, verify, capture, payout = pp
    verify.return_value = live(value="1000.00")
    result = run(settler.settle(Booked().decision))
    assert result["status"] == "held"
    assert "less held than the payout" in result["reason"]
    nothing_moved(capture, payout)


def test_a_redelivery_after_a_capture_goes_on_to_pay(pp):
    settler, verify, capture, payout = pp
    booked = Booked()
    payout.return_value = REFUSED
    first = run(settler.settle(booked.decision))
    assert first["status"] == "failed"
    verify.return_value = live(state="CAPTURED")
    payout.return_value = PAID_OUT
    second = run(settler.settle(booked.decision))
    assert second["status"] == "settled"


@pytest.mark.parametrize("state,status", [("PENDING", "retry"), ("DECLINED", "failed"), ("FAILED", "failed"), (None, "failed")])
def test_the_carrier_is_paid_only_after_paypal_says_the_capture_is_completed(pp, state, status):
    settler, verify, capture, payout = pp
    capture.return_value = capture_reply(state)
    result = run(settler.settle(Booked().decision))
    assert result["status"] == status
    assert "not COMPLETED" in result["reason"]
    assert f"capture as {state or 'unknown'}" in result["reason"]
    payout.assert_not_called()


def test_a_completed_capture_is_the_only_one_that_goes_on_to_pay(pp):
    settler, verify, capture, payout = pp
    capture.return_value = capture_reply("COMPLETED")
    assert run(settler.settle(Booked().decision))["status"] == "settled"
    payout.assert_awaited_once()
