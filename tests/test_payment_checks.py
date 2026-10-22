"""
Floorline Pay's deterministic payment checks (payment_checks.py) and their place in
verifier.validate().

Pinned here: a shipment that moves no money gets no payment finding; inside a payment
case every gap fails closed; a changed or mismatched payee and an inflated payout raise
the floor; and no payee address reaches the findings or the fraud model's prompt.

payment_checks is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics import verifier

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
PAYEE = "payments@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"

PAID = {
    "shipment_id": "SHP-PAY-1",
    "shipping_cost": 1300.0,
    "payout_amount": 1250.0,
    "carrier_name": "Mekong Haulage Co",
    "carrier_payee_email": PAYEE,
    "carrier_registered_payee_email": PAYEE,
    "carrier_payee_changed_at": "2026-08-01T08:00:00+07:00",
    "paypal_authorization_id": "0VF52814937998046",
}


def checks():
    return importlib.import_module("vf_logistics.payment_checks")


def codes(findings):
    return [f["code"] for f in findings]


def by_code(findings, code):
    found = [f for f in findings if f["code"] == code]
    assert found, f"{code} not in {codes(findings)}"
    return found[0]


def test_a_shipment_that_moves_no_money_gets_no_payment_finding():
    pc = checks()
    shipment = {"shipment_id": "SHP-1", "shipping_cost": 100.0}
    assert pc.is_payment_case(shipment) is False
    assert pc.check_payment(shipment, now=NOW) == []


def test_a_clean_payment_case_gets_no_finding():
    assert checks().check_payment(dict(PAID), now=NOW) == []


def test_a_payment_case_without_a_payee_is_held():
    shipment = {**PAID, "carrier_payee_email": None}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_MISSING")
    assert finding["severity"] == "HIGH"
    assert finding["floor"] == 75
    assert finding["blocks_auto_clear"] is True


def test_a_payee_with_no_registered_payee_on_file_is_unverified():
    shipment = {**PAID, "carrier_registered_payee_email": ""}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_UNVERIFIED")
    assert finding["floor"] == 45
    assert finding["blocks_auto_clear"] is True


def test_a_payee_that_differs_from_the_registered_one_is_a_mismatch():
    shipment = {**PAID, "carrier_payee_email": ATTACKER}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_MISMATCH")
    assert finding["severity"] == "HIGH"
    assert finding["floor"] == 80
    assert "not the payee your records hold" in finding["detail"]


def test_letter_case_alone_is_not_a_mismatch():
    shipment = {**PAID, "carrier_payee_email": PAYEE.upper()}
    assert "PAYEE_MISMATCH" not in codes(checks().check_payment(shipment, now=NOW))


@pytest.mark.parametrize("changed_at,days", [
    ("2026-11-07T12:00:00+00:00", 3),
    ("2026-10-27T12:00:00+00:00", 14),
])
def test_a_payee_changed_within_fourteen_days_is_flagged(changed_at, days):
    shipment = {**PAID, "carrier_payee_changed_at": changed_at}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_RECENTLY_CHANGED")
    assert finding["floor"] == 70
    assert finding["measured"] == {"days_since_payee_change": days}


def test_a_payee_changed_fifteen_days_ago_is_not_flagged():
    shipment = {**PAID, "carrier_payee_changed_at": "2026-10-26T12:00:00+00:00"}
    assert "PAYEE_RECENTLY_CHANGED" not in codes(checks().check_payment(shipment, now=NOW))


def test_a_payee_change_dated_in_the_future_is_flagged():
    shipment = {**PAID, "carrier_payee_changed_at": "2026-12-01T00:00:00+00:00"}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_RECENTLY_CHANGED")
    assert "future" in finding["detail"]


@pytest.mark.parametrize("value", ["last tuesday", "2026-11-01T08:00:00"])
def test_an_unreadable_or_naive_change_time_fails_closed(value):
    shipment = {**PAID, "carrier_payee_changed_at": value}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYEE_CHANGE_TIME_UNREADABLE")
    assert finding["floor"] == 45
    assert finding["blocks_auto_clear"] is True


def test_a_payout_above_the_booked_freight_is_flagged():
    shipment = {**PAID, "payout_amount": 1900.0}
    finding = by_code(checks().check_payment(shipment, now=NOW), "PAYOUT_ABOVE_FREIGHT")
    assert finding["severity"] == "HIGH"
    assert finding["floor"] == 75
    assert finding["measured"] == {"payout_amount": 1900.0, "booked_freight": 1300.0}


def test_a_payout_equal_to_the_booked_freight_is_not_flagged():
    shipment = {**PAID, "payout_amount": 1300.0}
    assert "PAYOUT_ABOVE_FREIGHT" not in codes(checks().check_payment(shipment, now=NOW))


def test_freight_cost_is_used_when_shipping_cost_is_absent():
    shipment = {**PAID, "shipping_cost": None, "freight_cost": 1000.0}
    assert "PAYOUT_ABOVE_FREIGHT" in codes(checks().check_payment(shipment, now=NOW))


def test_a_payout_with_no_booked_freight_is_unverified():
    shipment = {**PAID, "shipping_cost": None}
    assert "PAYOUT_FREIGHT_UNVERIFIED" in codes(checks().check_payment(shipment, now=NOW))


@pytest.mark.parametrize("value,code", [
    (None, "PAYOUT_AMOUNT_MISSING"),
    ("abc", "PAYOUT_AMOUNT_UNREADABLE"),
    (-5, "PAYOUT_AMOUNT_UNREADABLE"),
    ("nan", "PAYOUT_AMOUNT_UNREADABLE"),
])
def test_a_missing_or_unreadable_payout_amount_fails_closed(value, code):
    shipment = {**PAID, "payout_amount": value}
    finding = by_code(checks().check_payment(shipment, now=NOW), code)
    assert finding["floor"] == 45


def test_validate_runs_the_payment_checks_and_raises_the_floor():
    checks()
    shipment = {
        **PAID,
        "carrier_payee_email": ATTACKER,
        "carrier_payee_changed_at": datetime.now(timezone.utc).isoformat(),
    }
    result = verifier.validate(shipment, screen_sanctions=False)
    assert "payment" in result["checks_run"]
    assert {"PAYEE_MISMATCH", "PAYEE_RECENTLY_CHANGED"} <= set(codes(result["findings"]))
    assert result["risk_floor"] >= 80


def test_validate_lists_the_payment_check_for_a_shipment_that_moves_no_money():
    checks()
    result = verifier.validate({"shipment_id": "SHP-2"}, screen_sanctions=False)
    assert "payment" in result["checks_run"]
    assert not any(f["code"].startswith(("PAYEE_", "PAYOUT_")) for f in result["findings"])


def test_no_payee_address_reaches_the_validation_record():
    checks()
    shipment = {**PAID, "carrier_payee_email": ATTACKER}
    result = verifier.validate(shipment, screen_sanctions=False)
    assert "PAYEE_MISMATCH" in codes(result["findings"])
    text = json.dumps(result, default=str)
    assert ATTACKER not in text
    assert PAYEE not in text


def test_no_payee_address_reaches_the_fraud_model_prompt():
    checks()
    from vf_logistics.agents.fraud_detection_agent import analyze_shipment

    reply = ('{"risk_score": 25, "findings": [], "recommendations": []}', 100, 50)
    target = "vf_logistics.agents.fraud_detection_agent.nebius_client.complete_json"
    with patch(target, new_callable=AsyncMock, return_value=reply) as complete:
        asyncio.run(analyze_shipment({**PAID, "carrier_payee_email": ATTACKER}))
    sent = str(complete.call_args)
    assert "SHP-PAY-1" in sent
    assert ATTACKER not in sent
    assert PAYEE not in sent
