"""
The analysis side of the agent paying a clean case (orchestrator.py), and both ends of it through the real receiver and settler.

Pinned here: a clean payment the agent may pay is auto-cleared and one keyed decision goes out, naming the person's limit, before the case
is stored; a clean payment it may not pay goes to a person, also with the agent off, because nobody can release an auto-cleared case; a
model's answer can only take a payment away from the agent; a keyed decision that cannot be sent goes to a person; a shipment that moves
no money clears as it always did; the rules never clear a payment and may still reject one; and what the analysis sends really pays the
carrier, and a person really can release what the agent may not pay.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("STORE_BACKEND", "memory")

sys.path.insert(0, os.path.dirname(__file__))
from test_release_fails_closed import INVESTIGATION, TERMINAL, _compliance, _fraud, _release_attempted, _run  # noqa: E402
from test_release_fails_closed import SHIPMENT as PLAIN  # noqa: E402
from vf_logistics import credit_profile, decision_receiver, governance, orchestrator, payment_controls, settler, verifier  # noqa: E402
from vf_logistics import store as store_mod  # noqa: E402

ADMIN = "admin@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
AUTHORIZATION = "0VF52814937998046"
LIMIT = {"limit_usd": Decimal("1000"), "published_by": ADMIN, "reason": "first month", "at": "2026-11-06T00:00:00+00:00"}
STANDING = {"tier": "A", "baseline": "A", "suggested": "A", "cap_usd": Decimal("5000"), "paid_count": 3, "frozen": False}
NORMAL = {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": "A"}
ANSWER = {
    "analyst": NORMAL,
    "identity": {"status": "found", "reason": "1 result(s) name the carrier", "urls": [], "payee_site": "on_site"},
}
SHIPMENT = {
    **PLAIN,
    "carrier_name": "Mekong Haulage Co",
    "payout_amount": 900.0,
    "paypal_authorization_id": AUTHORIZATION,
    "carrier_payee_email": PAYEE,
}
ORIGIN, DESTINATION = sorted(verifier.DOMESTIC_SAFE_ROUTES)[0]
PARCEL = {
    **SHIPMENT,
    "origin": ORIGIN,
    "destination": DESTINATION,
    "receiver_country": "Vietnam",
    "declared_value": verifier.LOW_VALUE_THRESHOLD_USD - 1,
    "freight_cost": 10,
    "shipping_cost": 10,
    "weight_kg": 10,
    "receiver_company": "Local Shop",
    "receiver_name": "Local Shop",
    "consignee_name": "Local Shop",
    "payout_amount": 9.0,
}
OK = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
BOOKING = {"reference_id": SHIPMENT["shipment_id"], "amount": "1500.00", "currency": "USD"}
LIVE = {"status": "ok", "http_status": 200, "error": None, "body": {
    "status": "CREATED", "amount": {"currency_code": "USD", "value": "1500.00"}}}


async def published(case_id, tenant_id=None, **kwargs):
    return {"action": "publish_decision", "case_id": case_id, "status": "done", "detail": {"message_id": f"m-{case_id}"}}


async def no_topic(case_id, tenant_id=None, **kwargs):
    if kwargs["decision"].get("idempotency_key"):
        return {"action": "publish_decision", "case_id": case_id, "status": "failed", "detail": {"error": "no topic"}}
    return await published(case_id, tenant_id=tenant_id, **kwargs)


async def topic_down(case_id, tenant_id=None, **kwargs):
    if kwargs["decision"].get("idempotency_key"):
        raise RuntimeError("topic down")
    return await published(case_id, tenant_id=tenant_id, **kwargs)


async def registered(shipment, tenant_id=None):
    # The registered payee comes from the carrier's record and never from the event, so it is added here and not on SHIPMENT.
    if shipment.get("carrier_payee_email"):
        shipment["carrier_registered_payee_email"] = PAYEE
    return shipment


def drive(shipment=None, *, limit=LIMIT, standing=STANDING, answer=ANSWER, publish=None, boundary=None):
    """Ingest the shipment and advance it until it stops, as test_release_fails_closed.drive does, with the payment layer's inputs set."""
    store_mod._store = None
    orchestrator._invalidate_shipper_feedback()
    _run(governance.publish_boundary(
        boundary or governance.proposed_boundary("agent payment test"),
        "test-harness", "enable auto-release so a clean case can clear",
    ))
    publish = publish or AsyncMock(side_effect=published)
    with patch.object(orchestrator, "analyze_shipment", AsyncMock(return_value=_fraud(12))), \
            patch.object(orchestrator, "screen_shipment", AsyncMock(return_value=_compliance("CLEARED"))), \
            patch.object(orchestrator, "conduct_debate", AsyncMock(return_value={"result": {"verdict": {}}})), \
            patch.object(orchestrator, "investigate_case", AsyncMock(return_value=INVESTIGATION)), \
            patch.object(orchestrator.tavily_client, "search", AsyncMock(return_value=[])), \
            patch.object(orchestrator.payment_ai, "assess", AsyncMock(return_value=answer)) as assess, \
            patch.object(orchestrator.carrier_registry, "enrich", AsyncMock(side_effect=registered)), \
            patch.object(payment_controls, "agent_limit", AsyncMock(return_value=limit)), \
            patch.object(credit_profile, "profile", AsyncMock(return_value=standing)), \
            patch.dict(governance._ACTION_IMPLS, {"publish_decision": publish}):
        case = _run(orchestrator.ingest_shipment(copy.deepcopy(SHIPMENT if shipment is None else shipment)))
        for _ in range(8):
            if case.get("state") in TERMINAL:
                break
            # A copy, as the store gives the worker: the memory store keeps the object it was handed, so advancing that same object
            # would show a state nobody has stored.
            case = _run(orchestrator.advance(dict(case))) or case
    return case, publish, assess


def keyed(publish):
    return [c.kwargs["decision"] for c in publish.await_args_list if c.kwargs["decision"].get("idempotency_key")]


def stored(case):
    return _run(store_mod.get_store().get_case(case["case_id"]))


def envelope(body):
    message = {"data": base64.b64encode(json.dumps(body).encode()).decode(),
               "attributes": {"idempotency_key": body["idempotency_key"]}, "messageId": "m-1"}
    return {"message": message}


def test_a_clean_payment_inside_the_limit_is_cleared_and_one_keyed_decision_goes_out():
    case, publish, _ = drive()
    assert case["state"] == "AUTO_CLEARED"
    assert stored(case)["state"] == "AUTO_CLEARED"
    sent = keyed(publish)
    assert len(sent) == 1
    assert sent[0]["outcome"] == "AUTO_CLEARED"
    assert sent[0]["idempotency_key"] == f"agent-{case['case_id']}"
    assert sent[0]["approver"] == ADMIN
    assert sent[0]["executor"] == "agent"
    assert sent[0]["agent_limit_usd"] == "1000"
    assert sent[0]["effective_risk"] == case["risk_score"]


def test_the_keyed_decision_goes_out_before_the_case_is_stored():
    seen = {}

    async def looking(case_id, tenant_id=None, **kwargs):
        if kwargs["decision"].get("idempotency_key"):
            seen["state"] = (await store_mod.get_store().get_case(case_id))["state"]
        return await published(case_id, tenant_id=tenant_id, **kwargs)

    drive(publish=AsyncMock(side_effect=looking))
    assert seen["state"] == "SPECIALISTS_DONE"


def test_a_release_the_boundary_refuses_sends_no_keyed_decision():
    boundary = governance.proposed_boundary("agent payment test")
    boundary["auto_release"]["permitted"] = False
    case, publish, _ = drive(boundary=boundary)
    assert case["state"] == "PENDING_HUMAN"
    assert case["proposed_outcome"] == "AUTO_CLEARED"
    assert case["gate_denials"][0]["action"] == "release_shipment"
    assert keyed(publish) == []


def test_with_the_agent_off_a_clean_payment_goes_to_a_person():
    case, publish, _ = drive(limit=None)
    assert case["state"] == "HELD_FOR_REVIEW"
    assert "no person has published an agent limit" in case["decision"]["rationale"]
    assert not _release_attempted(case)
    assert keyed(publish) == []


def test_a_clean_payment_from_a_carrier_below_standing_a_goes_to_a_person():
    case, publish, _ = drive(standing={**STANDING, "tier": "B"})
    assert case["state"] == "HELD_FOR_REVIEW"
    assert "standing is B" in case["decision"]["rationale"]
    assert not _release_attempted(case)
    assert keyed(publish) == []


def test_a_clean_payment_at_the_limit_goes_to_a_person():
    case, publish, _ = drive(limit={**LIMIT, "limit_usd": Decimal("900")})
    assert case["state"] == "HELD_FOR_REVIEW"
    assert "not below the agent limit" in case["decision"]["rationale"]
    assert keyed(publish) == []


@pytest.mark.parametrize("analyst", [
    {**NORMAL, "verdict": "elevated", "reasons": ["a new lane"]},
    {**NORMAL, "verdict": "unknown", "confidence": 0.0},
    {**NORMAL, "confidence": 0.3},
])
def test_a_model_answer_can_only_take_the_payment_away_from_the_agent(analyst):
    case, publish, _ = drive(answer={**ANSWER, "analyst": analyst})
    assert case["state"] != "AUTO_CLEARED"
    assert not _release_attempted(case)
    assert keyed(publish) == []


@pytest.mark.parametrize("sender,phrase", [(no_topic, "could not be sent"), (topic_down, "could not be sent (RuntimeError)")])
def test_a_keyed_decision_that_cannot_be_sent_goes_to_a_person(sender, phrase):
    case, _, _ = drive(publish=AsyncMock(side_effect=sender))
    kept = stored(case)
    assert kept["state"] == "PENDING_HUMAN"
    assert kept["proposed_outcome"] == "AUTO_CLEARED"
    assert kept["decision"] is None
    assert kept["gate_denials"][0]["action"] == "publish_decision"
    assert phrase in kept["gate_denials"][0]["reason"]


def test_a_shipment_that_moves_no_money_clears_as_it_did():
    case, publish, assess = drive(shipment=PLAIN)
    assert case["state"] == "AUTO_CLEARED"
    assert _release_attempted(case)
    assert keyed(publish) == []
    assess.assert_not_called()


def test_the_parcel_below_is_one_the_rules_would_clear():
    assert verifier.validate({**PARCEL, "carrier_registered_payee_email": PAYEE})["skip_ai"] is True


def test_the_rules_never_clear_a_payment():
    case, publish, assess = drive(shipment=PARCEL)
    assess.assert_awaited_once()
    assert case["state"] == "HELD_FOR_REVIEW"
    assert case.get("cleared_by") != "rules"
    assert keyed(publish) == []


def test_the_rules_may_still_reject_a_payment():
    # A name also on the sanctions list takes the full analysis instead, so this one is on the internal blacklist only.
    blacklisted = {**SHIPMENT, "shipper_company": "Golden Star Trading", "shipper_name": "Golden Star Trading"}
    case, publish, assess = drive(shipment=blacklisted)
    # The rejection sets compliance BLOCKED, and the boundary then gives the case to a person instead of letting the agent hold it.
    assert case["state"] == "PENDING_HUMAN"
    assert case["proposed_outcome"] == "ESCALATED"
    assess.assert_not_called()
    assert keyed(publish) == []


@pytest.fixture
def paypal(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
    monkeypatch.setattr(settler.payment_binding, "find_authorization", AsyncMock(return_value=BOOKING))
    monkeypatch.setattr(settler.paypal_client, "get_authorization", AsyncMock(return_value=LIVE))
    monkeypatch.setattr(settler.carrier_registry, "enrich", AsyncMock(side_effect=registered))
    monkeypatch.setattr(settler.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT))
    monkeypatch.setattr(settler.credit_profile, "profile", AsyncMock(return_value=STANDING))
    monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: "honored")
    with patch.object(settler.paypal_client, "capture_authorization", AsyncMock(return_value=OK)) as capture, \
            patch.object(settler.paypal_client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout:
        yield capture, payout


def test_the_decision_the_analysis_sends_pays_the_carrier(paypal):
    capture, payout = paypal
    case, publish, _ = drive()
    body = {"case_id": case["case_id"], **keyed(publish)[0]}
    status, reply = _run(decision_receiver.receive(envelope(body)))
    assert status == 200
    assert reply["settlement"]["status"] == "settled"
    assert reply["settlement"]["agent"]["published_by"] == ADMIN
    capture.assert_awaited_once_with(AUTHORIZATION, f"{body['idempotency_key']}-capture")
    assert payout.await_args.args[0] == PAYEE
    assert payout.await_args.args[1] == 900.0


def test_a_person_can_release_what_the_agent_may_not_pay(paypal):
    capture, payout = paypal
    case, _, _ = drive(limit=None)
    assert case["state"] == "HELD_FOR_REVIEW"
    capture.assert_not_called()
    receipt = {"action": "publish_decision", "case_id": case["case_id"], "status": "done", "detail": {"message_id": "m-2"}}
    with patch.object(orchestrator.tools, "publish_decision", AsyncMock(return_value=receipt)) as sent:
        decided = _run(orchestrator.human_decide(
            case["case_id"], "release", "ana@forwarder.example", "called the carrier", actor_auth="password",
        ))
    assert decided["ok"] is True
    body = {"case_id": case["case_id"], **sent.await_args.args[1]}
    status, reply = _run(decision_receiver.receive(envelope(body)))
    assert status == 200
    assert reply["settlement"]["status"] == "settled"
    assert "agent" not in reply["settlement"]
    assert payout.await_args.args[0] == PAYEE
