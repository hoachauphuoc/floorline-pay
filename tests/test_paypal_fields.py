"""
Floorline Pay's payment fields on the B2B audit request (schemas.ComplianceAuditRequest).

The request stays loose: every payment field is optional, because a payout with no
payee on it is what the payment checks exist to flag. A malformed value is still a
data error and is refused with the field name, before it can reach a payment.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vf_logistics import b2b
from vf_logistics.openapi import build_spec
from vf_logistics.schemas import ComplianceAuditRequest

VALID = {
    "shipment_id": "SHP-PAY-1",
    "carrier_name": "Mekong Haulage Co",
    "carrier_payee_email": "Payments@Mekong-Haulage.example",
    "carrier_registered_payee_email": "payments@mekong-haulage.example",
    "carrier_payee_changed_at": "2026-11-01T08:00:00+07:00",
    "payout_amount": 1250.5,
    "paypal_order_id": "5O190127TN364715T",
    "paypal_authorization_id": "0VF52814937998046",
}


def _error_for(body: dict, field: str) -> dict:
    with pytest.raises(ValidationError) as raised:
        ComplianceAuditRequest.model_validate(body)
    errors = [e for e in raised.value.errors() if e["loc"] == (field,)]
    assert errors, f"no error reported on {field}: {raised.value.errors()}"
    return errors[0]


def test_valid_payment_fields_are_kept_and_emails_lower_cased():
    req = ComplianceAuditRequest.model_validate(VALID)
    assert req.carrier_name == "Mekong Haulage Co"
    assert req.carrier_payee_email == "payments@mekong-haulage.example"
    assert req.carrier_registered_payee_email == "payments@mekong-haulage.example"
    assert req.carrier_payee_changed_at == "2026-11-01T08:00:00+07:00"
    assert req.payout_amount == 1250.5
    assert req.paypal_order_id == "5O190127TN364715T"
    assert req.paypal_authorization_id == "0VF52814937998046"


def test_payment_fields_are_optional():
    req = ComplianceAuditRequest.model_validate({"shipment_id": "SHP-PAY-2"})
    dumped = req.model_dump(exclude_none=True)
    for field in ("carrier_name", "carrier_payee_email", "payout_amount", "paypal_authorization_id"):
        assert field in ComplianceAuditRequest.model_fields
        assert field not in dumped


def test_the_published_contract_still_requires_only_shipment_id():
    schema = build_spec()["components"]["schemas"]["ComplianceAuditRequest"]
    assert schema["required"] == ["shipment_id"]
    assert "carrier_payee_email" in schema["properties"]
    assert "paypal_authorization_id" in schema["properties"]


@pytest.mark.parametrize("value", [
    "not-an-email", "a@b@c.example", "pay ments@x.example", "payments@localhost",
])
def test_a_malformed_payee_email_is_refused_with_its_field(value):
    error = _error_for({**VALID, "carrier_payee_email": value}, "carrier_payee_email")
    assert "email address" in error["msg"]


def test_a_malformed_registered_payee_email_is_refused_with_its_field():
    body = {**VALID, "carrier_registered_payee_email": "nobody"}
    error = _error_for(body, "carrier_registered_payee_email")
    assert "email address" in error["msg"]


def test_a_payee_change_time_without_a_timezone_is_refused():
    body = {**VALID, "carrier_payee_changed_at": "2026-11-01T08:00:00"}
    error = _error_for(body, "carrier_payee_changed_at")
    assert "timezone" in error["msg"]


def test_a_payee_change_time_that_is_not_a_date_is_refused():
    body = {**VALID, "carrier_payee_changed_at": "last tuesday"}
    error = _error_for(body, "carrier_payee_changed_at")
    assert "ISO 8601" in error["msg"]


@pytest.mark.parametrize("value", [0, -10])
def test_a_non_positive_payout_amount_is_refused(value):
    error = _error_for({**VALID, "payout_amount": value}, "payout_amount")
    assert "greater than 0" in error["msg"]


@pytest.mark.parametrize("field", ["paypal_order_id", "paypal_authorization_id"])
def test_a_paypal_id_with_unexpected_characters_is_refused(field):
    error = _error_for({**VALID, field: "abc;DROP"}, field)
    assert "pattern" in error["msg"]


def test_payment_fields_reach_the_shipment_the_pipeline_sees():
    payload = ComplianceAuditRequest.model_validate(VALID).model_dump(exclude_none=True)
    shipment = b2b.shipment_from_request(payload)
    assert shipment["carrier_payee_email"] == "payments@mekong-haulage.example"
    assert shipment["carrier_payee_changed_at"] == "2026-11-01T08:00:00+07:00"
    assert shipment["paypal_authorization_id"] == "0VF52814937998046"
