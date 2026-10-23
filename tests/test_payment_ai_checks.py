"""
The findings the payment AI layers can add (payment_checks.check_payment_ai) and how they meet
the risk floor (verifier.validate).

Pinned here: an elevated verdict at a confidence of 0.6 or more adds a finding and a weaker one,
"normal", "unknown" or no answer adds none; a carrier look-up that did not run is a finding and
one that ran and found nothing is a stronger one; a found carrier adds none; a shipment that
moves no money gets nothing; and, whatever the AI layers say, the floor of validate() is never
below the floor without them and an answer that adds a finding blocks auto-clear.

The module under test is imported inside each test so that, before the change, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_origin_inputs import plywood  # noqa: E402
from vf_logistics import payment_checks, verifier  # noqa: E402

PAYEE = "payments@mekong-haulage.example"


def paying(**over):
    return plywood(
        payout_amount=2000.0, shipping_cost=3000.0, paypal_authorization_id="0VF52814937998046",
        carrier_payee_email=PAYEE, carrier_registered_payee_email=PAYEE, **over,
    )


def codes(findings):
    return [f["code"] for f in findings]


def analyst(verdict="elevated", confidence=0.8, reasons=("payout above the booked freight",)):
    return {"analyst": {"verdict": verdict, "confidence": confidence, "reasons": list(reasons)},
            "identity": {"status": "found"}}


def test_a_confident_elevated_verdict_adds_a_finding_with_the_reasons():
    found = payment_checks.check_payment_ai(paying(), analyst())
    assert codes(found) == ["PAYMENT_AI_ELEVATED"]
    assert found[0]["severity"] == "MEDIUM"
    assert found[0]["floor"] == 55
    assert found[0]["blocks_auto_clear"] is True
    assert "payout above the booked freight" in found[0]["detail"]
    assert found[0]["measured"] == {"confidence": 0.8}


@pytest.mark.parametrize("ai", [
    analyst(confidence=0.59),
    analyst(confidence="high"),
    analyst(verdict="normal", confidence=0.99),
    analyst(verdict="unknown", confidence=0.99),
    {"analyst": None, "identity": {"status": "found"}},
    {"analyst": "elevated", "identity": {"status": "found"}},
    {"identity": {"status": "found"}},
    {},
    None,
    "elevated",
])
def test_anything_less_than_a_confident_elevated_verdict_adds_nothing(ai):
    assert payment_checks.check_payment_ai(paying(), ai) == []


def test_reasons_that_are_not_a_list_are_ignored_not_an_error():
    ai = analyst()
    ai["analyst"]["reasons"] = "payout above the booked freight"
    assert codes(payment_checks.check_payment_ai(paying(), ai)) == ["PAYMENT_AI_ELEVATED"]


def test_a_look_up_that_did_not_run_is_a_finding_not_a_pass():
    found = payment_checks.check_payment_ai(paying(), {"identity": {"status": "unverified"}})
    assert codes(found) == ["CARRIER_IDENTITY_UNVERIFIED"]
    assert found[0]["severity"] == "MEDIUM"
    assert found[0]["floor"] == 50


def test_a_look_up_that_found_nothing_is_a_stronger_finding():
    found = payment_checks.check_payment_ai(paying(), {"identity": {"status": "not_found"}})
    assert codes(found) == ["CARRIER_NOT_FOUND_ONLINE"]
    assert found[0]["severity"] == "HIGH"
    assert found[0]["floor"] == 65


@pytest.mark.parametrize("identity", [{"status": "found"}, {"status": "something new"}, {}, None, "found"])
def test_a_found_carrier_and_an_unreadable_look_up_add_nothing(identity):
    assert payment_checks.check_payment_ai(paying(), {"identity": identity}) == []


def test_both_layers_can_add_their_findings_together():
    ai = {**analyst(), "identity": {"status": "not_found"}}
    assert codes(payment_checks.check_payment_ai(paying(), ai)) == ["PAYMENT_AI_ELEVATED", "CARRIER_NOT_FOUND_ONLINE"]


def test_a_shipment_that_moves_no_money_gets_no_ai_findings():
    ai = {**analyst(), "identity": {"status": "not_found"}}
    assert payment_checks.check_payment_ai(plywood(), ai) == []


AIS = [
    None, {}, analyst(), analyst(verdict="normal"), analyst(verdict="unknown", confidence=0.0),
    {"identity": {"status": "unverified"}}, {"identity": {"status": "not_found"}},
    {**analyst(), "identity": {"status": "not_found"}}, {"analyst": {"verdict": "elevated", "confidence": 1}},
]


@pytest.mark.parametrize("ai", AIS)
def test_the_floor_with_the_ai_layers_is_never_below_the_floor_without_them(ai):
    shipment = paying()
    without = verifier.validate(shipment)
    with_ai = verifier.validate(shipment, payment_ai=ai)
    assert with_ai["risk_floor"] >= without["risk_floor"]
    assert set(codes(without["findings"])) <= set(codes(with_ai["findings"]))
    assert with_ai["payment_ai_checked"] is (ai is not None)


def test_an_ai_finding_raises_the_floor_of_a_shipment_that_was_below_it():
    shipment = paying()
    base = verifier.validate(shipment)["risk_floor"]
    raised = verifier.validate(shipment, payment_ai={"identity": {"status": "not_found"}})
    assert raised["risk_floor"] == max(base, 65)
    assert "CARRIER_NOT_FOUND_ONLINE" in codes(raised["findings"])
    assert "payment_ai" in raised["checks_run"]
    assert raised["hs_floor_effect"]["findings_added"].count("CARRIER_NOT_FOUND_ONLINE") == 1


def test_an_answer_that_adds_a_finding_blocks_auto_clear():
    shipment = paying()
    result = verifier.validate(shipment, payment_ai=analyst())
    assert result["auto_clear_by_rules"] is False
    assert not result["cleared_by"]


def test_validate_without_the_ai_layers_is_as_before():
    result = verifier.validate(paying())
    assert result["payment_ai_checked"] is False
    assert "payment_ai" not in result["checks_run"]
