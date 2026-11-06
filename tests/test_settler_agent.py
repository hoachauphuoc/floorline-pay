"""
The agent paying a clean case by itself (settler.py with agent_payment.py).

Pinned here: a keyed AUTO_CLEARED on an auto-cleared case pays the carrier only if the agent's conditions still hold when the money would
move, and the record names the limit and the person who published it; any other key or state moves no money; a case the agent may not
pay goes to a person with the reason, because nobody can act on an auto-cleared case; a pause or a daily limit waits instead; and after
a capture the case stays on the agent's key, because a person's release would capture under a new key and PayPal refuses that.

settler is imported inside the fixture, as in test_settler.py.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics.store import get_store

PAYEE = "payments@mekong-haulage.example"
ADMIN = "admin@forwarder.example"
AUTHORIZATION = "0VF52814937998046"
OK = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
VOIDED = {"status": "ok", "http_status": 204, "body": {}, "error": None}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}
BOOKING = {"reference_id": "SHP-AGENT-1", "amount": "1300.00", "currency": "USD"}
LIVE = {"status": "ok", "http_status": 200, "error": None, "body": {
    "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}}
LIMIT = {"limit_usd": Decimal("1000"), "published_by": ADMIN, "reason": "first month", "at": "2026-11-06T00:00:00+00:00"}
STANDING = {"tier": "A", "baseline": "A", "suggested": "A", "cap_usd": Decimal("5000"), "paid_count": 3, "frozen": False}

SHIPMENT = {
    "shipment_id": "SHP-AGENT-1",
    "shipper_company": "Saigon Plywood Co",
    "carrier_name": "Mekong Haulage Co",
    "currency": "USD",
    "shipping_cost": 1300.0,
    "payout_amount": 900.0,
    "carrier_payee_email": PAYEE,
    "carrier_registered_payee_email": PAYEE,
    "paypal_authorization_id": AUTHORIZATION,
}


def run(coro):
    return asyncio.run(coro)


def cleared(state="AUTO_CLEARED", shipment=None, **extra):
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(get_store().put_case({
        "case_id": case_id,
        "state": state,
        "shipment": dict(SHIPMENT if shipment is None else shipment),
        "validation": {"findings": []},
        "reconciliation": {"auto_clear_permitted": True},
        "payment_ai": {
            "analyst": {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": "A"},
            "identity": {"status": "found", "reason": "1 result(s) name the carrier", "urls": [], "payee_site": "on_site"},
        },
        **extra,
    }))
    return case_id


def agent_decision(case_id):
    return {"case_id": case_id, "outcome": "AUTO_CLEARED", "approval_id": None, "idempotency_key": f"agent-{case_id}"}


def state_of(case_id):
    return run(get_store().get_case(case_id))["state"]


def with_a_person(case_id, phrase):
    case = run(get_store().get_case(case_id))
    assert case["state"] == "PENDING_HUMAN"
    assert case["proposed_outcome"] == "AUTO_CLEARED"
    assert case["decision"] is None
    assert case["gate_denials"][0]["action"] == "settle_payment"
    assert phrase in case["gate_denials"][0]["reason"]


def money_did_not_move(*calls):
    for call in calls:
        call.assert_not_called()


@pytest.fixture
def pp(monkeypatch):
    settler = importlib.import_module("vf_logistics.settler")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
    # A freeze outlives the test in the shared store: names of their own keep one test's dispute from holding another's payment.
    token = uuid.uuid4().hex[:8]
    monkeypatch.setitem(SHIPMENT, "shipper_company", f"Saigon Plywood {token} Co")
    monkeypatch.setitem(SHIPMENT, "carrier_name", f"Mekong Haulage {token} Co")
    monkeypatch.setattr(settler.payment_binding, "find_authorization", AsyncMock(return_value=BOOKING))
    monkeypatch.setattr(settler.paypal_client, "get_authorization", AsyncMock(return_value=LIVE))
    monkeypatch.setattr(settler.carrier_registry, "enrich", AsyncMock(side_effect=lambda s, tenant_id=None: s))
    monkeypatch.setattr(settler.payment_controls, "pause_state", AsyncMock(return_value={"paused": False}))
    monkeypatch.setattr(settler.payment_controls, "check_payout", AsyncMock(return_value=None))
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT))
    monkeypatch.setattr(settler.credit_profile, "profile", AsyncMock(return_value=STANDING))
    monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: "honored")
    with patch.object(settler.paypal_client, "capture_authorization", AsyncMock(return_value=OK)) as capture, \
            patch.object(settler.paypal_client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout, \
            patch.object(settler.paypal_client, "void_authorization", AsyncMock(return_value=VOIDED)) as void:
        yield settler, capture, payout, void


def test_the_agent_pays_a_clean_case_inside_the_limit(pp):
    settler, capture, payout, void = pp
    d = agent_decision(cleared())
    result = run(settler.settle(d))
    assert result["status"] == "settled"
    capture.assert_awaited_once_with(AUTHORIZATION, f"{d['idempotency_key']}-capture")
    payout.assert_awaited_once()
    args = payout.await_args.args
    assert args[0] == PAYEE
    assert args[1] == 900.0
    assert args[3] == f"{d['idempotency_key']}-payout"
    assert state_of(d["case_id"]) == "AUTO_CLEARED"
    void.assert_not_called()


def test_the_record_names_the_limit_and_the_person_who_published_it(pp):
    settler, *_ = pp
    d = agent_decision(cleared())
    run(settler.settle(d))
    rows, _ = run(get_store().query_audit(case_id=d["case_id"], action="settle_payment", limit=10))
    assert len(rows) == 1
    assert rows[0]["detail"]["agent"] == {"limit_usd": "1000", "published_by": ADMIN}
    assert rows[0]["detail"]["outcome"] == "AUTO_CLEARED"
    assert PAYEE not in json.dumps(rows[0], default=str)


def test_a_repeat_of_the_agents_decision_pays_once(pp):
    settler, capture, payout, _ = pp
    d = agent_decision(cleared())
    run(settler.settle(d))
    again = run(settler.settle(d))
    assert again["status"] == "settled"
    assert capture.await_count == 1
    assert payout.await_count == 1


@pytest.mark.parametrize("key", ["exec-1", "agent-CASE-SOMEONE-ELSE", "agent-"])
def test_an_auto_cleared_decision_under_any_other_key_moves_no_money(pp, key):
    settler, capture, payout, void = pp
    d = {**agent_decision(cleared()), "idempotency_key": key}
    result = run(settler.settle(d))
    assert result["status"] == "skipped"
    assert "not a keyed human release or block" in result["reason"]
    money_did_not_move(capture, payout, void)


@pytest.mark.parametrize("state", ["PENDING_HUMAN", "HELD_FOR_REVIEW", "ESCALATED", "RELEASED_BY_HUMAN", "BLOCKED_BY_HUMAN"])
def test_a_case_with_a_person_is_not_paid_by_the_agent(pp, state):
    settler, capture, payout, void = pp
    case_id = cleared(state=state)
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "skipped"
    assert "a person decides it" in result["reason"]
    money_did_not_move(capture, payout, void)
    assert state_of(case_id) == state


def test_a_decision_that_arrives_before_the_case_is_stored_is_delivered_again(pp):
    settler, capture, payout, void = pp
    case_id = cleared(state="SPECIALISTS_DONE")
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "retry"
    assert "still storing this case" in result["reason"]
    money_did_not_move(capture, payout, void)
    assert state_of(case_id) == "SPECIALISTS_DONE"


def test_a_limit_withdrawn_after_the_analysis_stops_the_payment(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=None))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    assert result["handed_to_person"] is True
    assert "no person has published an agent limit" in result["reason"]
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "no person has published an agent limit")


def test_a_standing_that_fell_after_the_analysis_stops_the_payment(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.credit_profile, "profile", AsyncMock(return_value={**STANDING, "tier": "B"}))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    assert result["handed_to_person"] is True
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "standing is B")


def test_a_case_with_no_analysis_goes_to_a_person(pp):
    settler, capture, payout, void = pp
    case_id = cleared(payment_ai=None)
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "no payment analysis")


def test_a_payout_that_needs_two_people_goes_to_a_person_whatever_the_limit(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value={**LIMIT, "limit_usd": Decimal("9000")}))
    case_id = cleared(shipment={**SHIPMENT, "payout_amount": 2500.0, "shipping_cost": 2600.0})
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "second approver")


def test_a_frozen_carrier_goes_to_a_person(pp):
    settler, capture, payout, void = pp
    run(settler.payment_freeze.freeze("carrier", SHIPMENT["carrier_name"], "a dispute", "EVT-1"))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    assert result["handed_to_person"] is True
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "frozen")


def test_a_payee_that_no_longer_matches_goes_to_a_person(pp):
    settler, capture, payout, void = pp
    case_id = cleared(shipment={**SHIPMENT, "carrier_registered_payee_email": "someone-else@haulage.example"})
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "PAYEE_MISMATCH")


def test_a_payout_the_limits_hold_goes_to_a_person(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "check_payout", AsyncMock(return_value=("held", "above the per-payout limit")))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "above the per-payout limit")


def test_a_pause_waits_and_the_case_stays_with_the_agent(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "pause_state", AsyncMock(return_value={"paused": True, "reason": "an incident"}))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "retry"
    assert "handed_to_person" not in result
    money_did_not_move(capture, payout, void)
    assert state_of(case_id) == "AUTO_CLEARED"


def test_a_daily_limit_waits_and_the_case_stays_with_the_agent(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "check_payout", AsyncMock(return_value=("retry", "the day's limit is used")))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "retry"
    assert "handed_to_person" not in result
    money_did_not_move(capture, payout, void)
    assert state_of(case_id) == "AUTO_CLEARED"


def test_a_capture_paypal_refuses_goes_to_a_person(pp):
    settler, capture, payout, _ = pp
    capture.return_value = REFUSED
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "failed"
    assert result["handed_to_person"] is True
    payout.assert_not_called()
    with_a_person(case_id, "did not capture")


def test_a_failed_payout_after_the_capture_stays_on_the_agents_key(pp):
    settler, _, payout, _ = pp
    payout.return_value = REFUSED
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "failed"
    assert "handed_to_person" not in result
    assert state_of(case_id) == "AUTO_CLEARED"


def test_a_held_payout_after_the_capture_stays_on_the_agents_key(pp, monkeypatch):
    settler, *_ = pp
    stale = {"status": settler._STALE_ATTEMPT, "http_status": None, "body": None, "error": None}
    monkeypatch.setattr(settler, "_send_payout", AsyncMock(return_value=stale))
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "held"
    assert "handed_to_person" not in result
    assert state_of(case_id) == "AUTO_CLEARED"


def test_without_paypal_credentials_the_case_goes_to_a_person(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.delenv("PAYPAL_CLIENT_ID")
    case_id = cleared()
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "failed"
    assert "not configured" in result["reason"]
    money_did_not_move(capture, payout, void)
    with_a_person(case_id, "not configured")


def test_a_handover_that_cannot_be_stored_is_delivered_again(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=None))
    case_id = cleared()
    with patch.object(get_store(), "put_case", AsyncMock(side_effect=RuntimeError("store down"))):
        result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "retry"
    assert "RuntimeError" in result["reason"]
    money_did_not_move(capture, payout, void)
    assert state_of(case_id) == "AUTO_CLEARED"


def test_a_repeat_after_the_handover_moves_no_money(pp, monkeypatch):
    settler, capture, payout, void = pp
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=None))
    d = agent_decision(cleared())
    run(settler.settle(d))
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT))
    again = run(settler.settle(d))
    assert again["status"] == "skipped"
    assert "a person decides it" in again["reason"]
    money_did_not_move(capture, payout, void)


def test_a_payment_that_did_not_go_out_gives_its_reservation_back(pp, monkeypatch):
    settler, *_ = pp
    cancel = AsyncMock()
    monkeypatch.setattr(settler.payment_controls, "cancel_reservations", cancel)
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=None))
    d = agent_decision(cleared())
    run(settler.settle(d))
    cancel.assert_awaited_once_with(d["idempotency_key"], None)


def test_a_limit_withdrawn_after_the_capture_stays_a_failed_settlement_a_person_can_retry(pp, monkeypatch):
    settler, _, payout, _ = pp
    payout.return_value = REFUSED
    d = agent_decision(cleared())
    assert run(settler.settle(d))["status"] == "failed"
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=None))
    payout.reset_mock()
    again = run(settler.settle(d))
    assert again["status"] == "failed"
    assert "the money is captured" in again["reason"]
    assert "no person has published an agent limit" in again["reason"]
    assert "handed_to_person" not in again
    payout.assert_not_called()
    assert state_of(d["case_id"]) == "AUTO_CLEARED"
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT))
    payout.return_value = PAID_OUT
    assert run(settler.settle(d))["status"] == "settled"
