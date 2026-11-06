"""
What became of a payout after PayPal accepted it (payout_tracking.py, payment_status.py).

Pinned here: PayPal's payout statuses are sorted into confirmed, failed and pending, and
anything unknown is pending; a case with no status is unconfirmed, never confirmed; one failed
status makes the case failed whatever else was said, and freezes the carrier; asking PayPal
records what it answers and a PayPal that cannot be reached records nothing; and the payment
state of a settled case carries the confirmation.

payout_tracking is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from unittest.mock import AsyncMock

import pytest

from vf_logistics import payment_status
from vf_logistics.store import get_store, utcnow


def run(coro):
    return asyncio.run(coro)


def freezes():
    return importlib.import_module("vf_logistics.payment_freeze")


def mod():
    return importlib.import_module("vf_logistics.payout_tracking")


def settled_case(batch="BATCH-1"):
    tag = uuid.uuid4().hex[:8]
    case_id, carrier = f"CASE-{tag}", f"Mekong Haulage {tag} Co"
    shipment = {
        "shipment_id": case_id, "currency": "USD", "payout_amount": 1250.0,
        "carrier_name": carrier, "shipper_name": f"Saigon Plywood {tag} Co",
        "paypal_authorization_id": "0VF52814937998046",
    }
    run(get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": shipment}))
    run(get_store().add_audit({
        "audit_id": f"settle-{tag}", "case_id": case_id, "action": "settle_payment", "status": "settled",
        "detail": {"payout": {"paypal_id": batch}, "capture": {"paypal_id": f"CAP-{tag}"}, "idempotency_key": tag},
        "at": utcnow(),
    }))
    return case_id, carrier


def batch(*statuses):
    return {"status": "ok", "http_status": 200, "error": None, "body": {
        "batch_header": {"payout_batch_id": "BATCH-1", "batch_status": "SUCCESS"},
        "items": [{"transaction_status": s} for s in statuses],
    }}


@pytest.mark.parametrize("status, expected", [
    ("SUCCESS", "confirmed"), ("succeeded", "confirmed"),
    ("FAILED", "failed"), ("RETURNED", "failed"), ("BLOCKED", "failed"), ("DENIED", "failed"),
    ("REFUNDED", "failed"), ("reversed", "failed"), ("CANCELED", "failed"),
    ("PENDING", "pending"), ("ONHOLD", "pending"), ("UNCLAIMED", "pending"), ("", "pending"), (None, "pending"),
    ("something new", "pending"),
])
def test_payout_statuses_are_sorted_and_the_unknown_is_pending(status, expected):
    assert mod().classify(status) == expected


def test_a_case_with_no_status_is_unconfirmed():
    state = run(mod().confirmation("CASE-NOTHING"))
    assert state == {"state": "unconfirmed", "status": None, "at": None}


def test_a_failed_status_beats_a_confirmed_one_and_freezes_the_carrier():
    pt = mod()
    case_id, carrier = settled_case()
    assert run(pt.apply(case_id, f"{case_id}-1", "SUCCESS", carrier)) == "confirmed"
    assert run(pt.confirmation(case_id))["state"] == "confirmed"
    assert run(freezes().frozen("carrier", carrier)) is None
    assert run(pt.apply(case_id, f"{case_id}-2", "RETURNED", carrier)) == "failed"
    assert run(pt.confirmation(case_id))["state"] == "failed"
    assert "RETURNED" in run(freezes().frozen("carrier", carrier))["reason"]


def test_a_confirmed_status_beats_a_pending_one():
    pt = mod()
    case_id, carrier = settled_case()
    run(pt.apply(case_id, f"{case_id}-1", "SUCCESS", carrier))
    run(pt.apply(case_id, f"{case_id}-2", "PENDING", carrier))
    assert run(pt.confirmation(case_id))["state"] == "confirmed"


def test_a_pending_status_alone_is_pending():
    pt = mod()
    case_id, carrier = settled_case()
    run(pt.apply(case_id, f"{case_id}-1", "ONHOLD", carrier))
    assert run(pt.confirmation(case_id)) | {"at": None} == {"state": "pending", "status": "ONHOLD", "at": None}


def test_the_same_source_is_recorded_once():
    pt = mod()
    case_id, carrier = settled_case()
    run(pt.apply(case_id, f"{case_id}-1", "PENDING", carrier))
    run(pt.apply(case_id, f"{case_id}-1", "SUCCESS", carrier))
    assert run(pt.confirmation(case_id))["status"] == "PENDING"


def test_asking_paypal_records_a_confirmed_payout(monkeypatch):
    pt = mod()
    case_id, carrier = settled_case()
    ask = AsyncMock(return_value=batch("SUCCESS"))
    monkeypatch.setattr(pt.paypal_client, "get_payout_batch", ask)
    state = run(pt.refresh(case_id))
    assert state["state"] == "confirmed"
    ask.assert_awaited_once_with("BATCH-1", "refresh-BATCH-1")
    assert run(freezes().frozen("carrier", carrier)) is None


def test_asking_paypal_about_a_payout_with_one_failed_item_freezes_the_carrier(monkeypatch):
    pt = mod()
    case_id, carrier = settled_case()
    monkeypatch.setattr(pt.paypal_client, "get_payout_batch", AsyncMock(return_value=batch("SUCCESS", "BLOCKED")))
    state = run(pt.refresh(case_id))
    assert state["state"] == "failed"
    assert state["status"] == "BLOCKED"
    assert run(freezes().frozen("carrier", carrier)) is not None


def test_a_paypal_that_cannot_be_reached_records_nothing(monkeypatch):
    pt = mod()
    case_id, _ = settled_case()
    down = {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}
    monkeypatch.setattr(pt.paypal_client, "get_payout_batch", AsyncMock(return_value=down))
    with pytest.raises(pt.PayoutError) as raised:
        run(pt.refresh(case_id))
    assert raised.value.status == 502
    assert "could not tell" in raised.value.message
    assert run(pt.confirmation(case_id))["state"] == "unconfirmed"


def test_a_case_without_a_settled_payout_has_nothing_to_ask_about():
    pt = mod()
    with pytest.raises(pt.PayoutError) as raised:
        run(pt.refresh("CASE-NEVER-PAID"))
    assert raised.value.status == 409
    assert "no settled payout" in raised.value.message


def test_the_payment_state_of_a_settled_case_carries_the_confirmation():
    pt = mod()
    case_id, carrier = settled_case()
    assert run(payment_status.payment_state(case_id))["payout_status"]["state"] == "unconfirmed"
    run(pt.apply(case_id, f"{case_id}-1", "SUCCESS", carrier))
    assert run(payment_status.payment_state(case_id))["payout_status"]["state"] == "confirmed"
