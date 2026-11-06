"""
Floorline Pay's settler (settler.py) and its place in decision_receiver.receive().

Pinned here: money moves only after a published human RELEASE on a released case whose
payment checks are clean, and a published BLOCK voids the hold; every other path is a
named status and PayPal is not asked to move money; an unknown outcome asks Pub/Sub to
deliver the decision again (503) instead of acknowledging it; and no payee address is
written to the audit trail.

settler is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics import decision_receiver
from vf_logistics.store import get_store

PAYEE = "payments@mekong-haulage.example"
OK = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
VOIDED = {"status": "ok", "http_status": 204, "body": {}, "error": None}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}
DOWN = {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}
UNAVAILABLE = {"status": "http_error", "http_status": 503, "body": {}, "error": "503"}
BOOKING = {"reference_id": "SHP-PAY-1", "amount": "1300.00", "currency": "USD"}
LIVE = {"status": "ok", "http_status": 200, "error": None, "body": {
    "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}}

SHIPMENT = {
    "shipment_id": "SHP-PAY-1",
    "shipper_company": "Saigon Plywood Co",
    "currency": "USD",
    "shipping_cost": 1300.0,
    "payout_amount": 1250.0,
    "carrier_payee_email": PAYEE,
    "carrier_registered_payee_email": PAYEE,
    "carrier_payee_changed_at": "2026-08-01T08:00:00+07:00",
    "paypal_authorization_id": "0VF52814937998046",
}


def run(coro):
    return asyncio.run(coro)


def new_case(state="RELEASED_BY_HUMAN", shipment=None):
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(get_store().put_case({"case_id": case_id, "state": state,
                              "shipment": dict(SHIPMENT if shipment is None else shipment)}))
    return case_id


def decision(case_id, outcome="RELEASED_BY_HUMAN"):
    return {"case_id": case_id, "outcome": outcome, "approval_id": "apr-1",
            "idempotency_key": f"exec-{uuid.uuid4().hex[:8]}"}


@pytest.fixture
def pp(monkeypatch):
    settler = importlib.import_module("vf_logistics.settler")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    # A freeze on a shipper outlives the test in the shared store: a name of its own keeps one test's dispute from holding another's release.
    monkeypatch.setitem(SHIPMENT, "shipper_company", f"Saigon Plywood {uuid.uuid4().hex[:8]} Co")
    monkeypatch.setattr(settler.payment_binding, "find_authorization", AsyncMock(return_value=BOOKING))
    monkeypatch.setattr(settler.paypal_client, "get_authorization", AsyncMock(return_value=LIVE))
    monkeypatch.setattr(settler.carrier_registry, "enrich", AsyncMock(side_effect=lambda s, tenant_id=None: s))
    monkeypatch.setattr(settler.payment_controls, "pause_state", AsyncMock(return_value={"paused": False}))
    monkeypatch.setattr(settler.payment_controls, "check_payout", AsyncMock(return_value=None))
    monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: "honored")
    with patch.object(settler.paypal_client, "capture_authorization", AsyncMock(return_value=OK)) as capture, \
            patch.object(settler.paypal_client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout, \
            patch.object(settler.paypal_client, "void_authorization", AsyncMock(return_value=VOIDED)) as void:
        yield settler, capture, payout, void


def money_did_not_move(*calls):
    for call in calls:
        call.assert_not_called()


def test_a_released_payment_case_is_captured_then_paid(pp):
    settler, capture, payout, void = pp
    d = decision(new_case())
    result = run(settler.settle(d))
    assert result["status"] == "settled"
    capture.assert_awaited_once_with("0VF52814937998046", f"{d['idempotency_key']}-capture")
    payout.assert_awaited_once()
    args = payout.await_args.args
    assert args[0] == PAYEE
    assert args[1] == 1250.0
    assert args[2] == "USD"
    assert args[3] == f"{d['idempotency_key']}-payout"
    assert result["capture"]["paypal_id"] == "CAP-1"
    assert result["payout"]["paypal_id"] == "PAYOUT-1"
    void.assert_not_called()


def test_the_audit_row_names_the_outcome_and_never_the_payee_address(pp):
    settler, *_ = pp
    d = decision(new_case())
    run(settler.settle(d))
    rows = [r for r in run(get_store().list_audit(limit=500)) if r["action"] == "settle_payment"
            and r["detail"]["idempotency_key"] == d["idempotency_key"]]
    assert len(rows) == 1
    assert rows[0]["status"] == "settled"
    assert PAYEE not in json.dumps(rows[0], default=str)


def test_a_redelivered_decision_writes_no_second_audit_row(pp):
    settler, *_ = pp
    d = decision(new_case())
    run(settler.settle(d))
    run(settler.settle(d))
    rows = [r for r in run(get_store().list_audit(limit=500)) if r["action"] == "settle_payment"
            and r["detail"]["idempotency_key"] == d["idempotency_key"]]
    assert len(rows) == 1


def test_a_blocked_case_voids_the_authorization(pp):
    settler, capture, payout, void = pp
    d = decision(new_case(state="BLOCKED_BY_HUMAN"), outcome="BLOCKED_BY_HUMAN")
    result = run(settler.settle(d))
    assert result["status"] == "voided"
    void.assert_awaited_once_with("0VF52814937998046", f"{d['idempotency_key']}-void")
    money_did_not_move(capture, payout)


def test_a_blocked_case_with_no_authorization_is_skipped(pp):
    settler, capture, payout, void = pp
    shipment = {**SHIPMENT, "paypal_authorization_id": None}
    d = decision(new_case(state="BLOCKED_BY_HUMAN", shipment=shipment), outcome="BLOCKED_BY_HUMAN")
    assert run(settler.settle(d))["status"] == "skipped"
    money_did_not_move(capture, payout, void)


def test_a_case_that_moves_no_money_is_skipped(pp):
    settler, capture, payout, void = pp
    d = decision(new_case(shipment={"shipment_id": "SHP-2", "shipping_cost": 100.0}))
    result = run(settler.settle(d))
    assert result["status"] == "skipped"
    assert "moves no money" in result["reason"]
    money_did_not_move(capture, payout, void)


def test_an_unknown_case_is_skipped(pp):
    settler, capture, payout, void = pp
    assert run(settler.settle(decision("CASE-DOES-NOT-EXIST")))["status"] == "skipped"
    money_did_not_move(capture, payout, void)


@pytest.mark.parametrize("outcome", ["AUTO_CLEARED", "ESCALATED", None])
def test_only_a_human_release_or_block_can_move_money(pp, outcome):
    settler, capture, payout, void = pp
    d = decision(new_case(), outcome=outcome)
    assert run(settler.settle(d))["status"] == "skipped"
    money_did_not_move(capture, payout, void)


def test_a_decision_without_a_key_is_skipped(pp):
    settler, capture, payout, void = pp
    d = {**decision(new_case()), "idempotency_key": ""}
    assert run(settler.settle(d))["status"] == "skipped"
    money_did_not_move(capture, payout, void)


def test_without_paypal_credentials_nothing_is_paid(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET")
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "failed"
    assert "not configured" in result["reason"]
    money_did_not_move(capture, payout, void)


def test_a_case_still_executing_asks_for_redelivery(pp):
    settler, capture, payout, void = pp
    result = run(settler.settle(decision(new_case(state="EXECUTING"))))
    assert result["status"] == "retry"
    assert "still EXECUTING" in result["reason"]
    money_did_not_move(capture, payout, void)


@pytest.mark.parametrize("state", ["EXECUTION_FAILED", "HELD_FOR_REVIEW", "BLOCKED_BY_HUMAN"])
def test_a_case_that_was_not_released_is_never_paid(pp, state):
    settler, capture, payout, void = pp
    result = run(settler.settle(decision(new_case(state=state))))
    assert result["status"] == "failed"
    assert "not RELEASED_BY_HUMAN" in result["reason"]
    money_did_not_move(capture, payout, void)


def test_a_person_releasing_does_not_override_the_payment_checks(pp):
    settler, capture, payout, void = pp
    shipment = {**SHIPMENT, "carrier_payee_email": "billing-update@mekong-haulage-pay.example"}
    result = run(settler.settle(decision(new_case(shipment=shipment))))
    assert result["status"] == "held"
    assert "PAYEE_MISMATCH" in result["reason"]
    assert "nothing is paid" in result["reason"]
    money_did_not_move(capture, payout, void)


def test_an_inflated_payout_is_held_even_when_the_case_is_released(pp):
    settler, capture, payout, void = pp
    case_id = new_case(shipment={**SHIPMENT, "payout_amount": 4000.0})
    case = run(get_store().get_case(case_id))
    case["verification"] = {"approvals": [{"approval_id": "apr-1", "requires_second": True, "second_approval": {"by": "second@example.com"}}]}
    run(get_store().put_case(case))
    result = run(settler.settle(decision(case_id)))
    assert result["status"] == "held"
    assert "PAYOUT_ABOVE_FREIGHT" in result["reason"]
    money_did_not_move(capture, payout, void)


def test_a_refused_capture_pays_nobody(pp):
    settler, capture, payout, _ = pp
    capture.return_value = REFUSED
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "failed"
    assert "did not capture" in result["reason"]
    payout.assert_not_called()


def test_an_unreachable_paypal_on_capture_asks_for_redelivery(pp):
    settler, capture, payout, _ = pp
    capture.return_value = DOWN
    assert run(settler.settle(decision(new_case())))["status"] == "retry"
    payout.assert_not_called()


def test_a_captured_but_unpaid_case_says_so(pp):
    settler, _, payout, _ = pp
    payout.return_value = REFUSED
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "failed"
    assert "captured the funds but did not pay" in result["reason"]
    assert result["capture"]["status"] == "ok"
    assert result["payout"]["status"] == "http_error"


def test_a_paypal_server_error_on_payout_asks_for_redelivery(pp):
    settler, _, payout, _ = pp
    payout.return_value = UNAVAILABLE
    assert run(settler.settle(decision(new_case())))["status"] == "retry"


def test_settle_never_raises(pp):
    settler, capture, *_ = pp
    capture.side_effect = RuntimeError("boom")
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "retry"
    assert "RuntimeError" in result["reason"]


def envelope(case_id, key):
    body = {"case_id": case_id, "outcome": "RELEASED_BY_HUMAN", "approval_id": "apr-1",
            "idempotency_key": key}
    message = {"data": base64.b64encode(json.dumps(body).encode()).decode(),
               "attributes": {"idempotency_key": key}, "messageId": "m-1"}
    return {"message": message}


def test_the_receiver_reports_the_settlement_and_acknowledges(pp):
    settler, *_ = pp
    key = f"exec-{uuid.uuid4().hex[:8]}"
    settled = {"status": "settled", "reason": "captured and paid"}
    with patch.object(decision_receiver.settler, "settle", AsyncMock(return_value=settled)) as settle:
        status, body = run(decision_receiver.receive(envelope("CASE-R1", key)))
    assert status == 200
    assert body["outcome"] == "applied"
    assert body["settlement"] == settled
    sent = settle.await_args.args[0]
    assert sent["idempotency_key"] == key
    assert sent["case_id"] == "CASE-R1"
    assert sent["outcome"] == "RELEASED_BY_HUMAN"


def test_an_unknown_settlement_is_not_acknowledged_so_pubsub_redelivers(pp):
    settler, *_ = pp
    key = f"exec-{uuid.uuid4().hex[:8]}"
    retry = {"status": "retry", "reason": "the case is still EXECUTING; deliver the decision again"}
    with patch.object(decision_receiver.settler, "settle", AsyncMock(return_value=retry)) as settle:
        first = run(decision_receiver.receive(envelope("CASE-R2", key)))
        second = run(decision_receiver.receive(envelope("CASE-R2", key)))
    assert first[0] == 503
    assert first[1]["outcome"] == "applied"
    assert second[0] == 503
    assert second[1]["outcome"] == "duplicate"
    assert second[1]["deliveries"] == 2
    assert settle.await_count == 2
