"""
The mandate in the booking routes (payment_routes.py) and in the settler (settler.py).

Pinned here: an order made for approval can carry a mandate the signed-in person gives, its id goes to PayPal as custom_id and
the signed mandate is kept; a mandate without the approval flow, a malformed one, or one that cannot be signed is refused with
its reason and nothing reaches PayPal; an order without a mandate reaches PayPal exactly as before; when PayPal's reply to the
authorization names a mandate the booking is linked to it; and the settler pays a booking whose mandate allows the payout and
holds one whose mandate is outside its limits, expired, forged, missing, or cannot be verified, while a booking with no
mandate is judged by the other layers as before.

The modules are looked up inside each test so that, before they exist, every test fails instead of the file failing to collect.
"""

from __future__ import annotations

import copy
import importlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from test_settler import SHIPMENT, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
OPERATOR = "operator@forwarder.example"
AS_OPERATOR = {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(OPERATOR)}
SITE = "https://floorline-pay.example"
SECRET = "s" * 32
APPROVAL = "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T"
CREATED = {"status": "ok", "http_status": 201, "error": None, "body": {
    "id": "5O190127TN364715T", "status": "PAYER_ACTION_REQUIRED",
    "links": [{"rel": "payer-action", "href": APPROVAL}],
}}
TERMS = {"max_payout_usd": 1300, "carriers": ["Mekong Haulage Co"], "valid_days": 7}


def mod(name="mandate"):
    return importlib.import_module(f"vf_logistics.{name}")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("VF_MANDATE_SECRET", SECRET)


class TestTheBookingRoutes(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()
        self.origins = patch.dict(os.environ, {"PAYMENT_RETURN_ORIGINS": SITE, "OPERATOR_EMAILS": OPERATOR})
        self.origins.start()
        self.routes = importlib.import_module("vf_logistics.payment_routes")
        # These tests book many orders; the 30-a-minute limit is shared with every other test in the process.
        from vf_logistics import app as app_module

        self.limiter_was = app_module.limiter.enabled
        app_module.limiter.enabled = False

    def teardown_method(self):
        from vf_logistics import app as app_module

        app_module.limiter.enabled = self.limiter_was
        self.origins.stop()
        super().teardown_method()

    def _create(self, headers=AS_OPERATOR, **extra):
        body = {"amount": 1300, "currency": "USD", "reference_id": "SHP-PAY-1",
                "return_url": f"{SITE}/book/return", "cancel_url": f"{SITE}/book", **extra}
        with patch.object(self.routes.paypal_client, "create_order_for_approval", AsyncMock(return_value=CREATED)) as make:
            return self.client.post("/api/v1/payments/orders", json=body, headers=headers), make

    def test_a_mandate_goes_to_paypal_as_custom_id_and_is_kept(self):
        r, make = self._create(mandate=TERMS)
        assert r.status_code == 201
        mandate_id = r.get_json()["mandate_id"]
        assert make.await_args.kwargs == {"custom_id": mandate_id}
        signed = run(mod().load(mandate_id))
        assert signed["mandate"]["reference_id"] == "SHP-PAY-1"
        assert signed["mandate"]["carriers"] == ["mekonghaulageco"]
        assert signed["mandate"]["shipper"] == OPERATOR
        assert mod().refusal(signed, Decimal("1300"), "mekonghaulageco", "SHP-PAY-1") is None

    def test_an_order_without_a_mandate_reaches_paypal_exactly_as_before(self):
        r, make = self._create()
        assert r.status_code == 201
        assert "mandate_id" not in r.get_json()
        assert make.await_args.kwargs == {}

    @pytest.mark.parametrize("terms, status, phrase", [
        ({**TERMS, "max_payout_usd": 0}, 400, "above zero"),
        ({**TERMS, "carriers": []}, 400, "names the carriers"),
        ({**TERMS, "valid_days": 99}, 400, "1 to 30"),
        ("not an object", 400, "above zero"),
    ])
    def test_a_mandate_that_is_not_well_formed_is_refused_and_nothing_reaches_paypal(self, terms, status, phrase):
        r, make = self._create(mandate=terms)
        assert r.status_code == status
        assert phrase in r.get_json()["error"]
        make.assert_not_called()

    def test_a_mandate_is_given_by_a_person_not_by_a_key(self):
        r, make = self._create(headers=KEYED, mandate=TERMS)
        assert r.status_code == 403
        assert "given by a person" in r.get_json()["error"]
        make.assert_not_called()

    def test_a_mandate_cannot_be_signed_without_the_secret(self, monkeypatch):
        monkeypatch.delenv("VF_MANDATE_SECRET")
        r, make = self._create(mandate=TERMS)
        assert r.status_code == 503
        assert "VF_MANDATE_SECRET" in r.get_json()["error"]
        make.assert_not_called()

    def test_a_mandate_needs_the_booking_that_sends_the_shipper_to_paypal(self):
        with patch.object(self.routes.paypal_client, "create_order", AsyncMock(return_value=CREATED)) as make:
            r = self.client.post("/api/v1/payments/orders", headers=AS_OPERATOR, json={
                "amount": 1300, "currency": "USD", "reference_id": "SHP-PAY-1", "mandate": TERMS,
            })
        assert r.status_code == 400
        assert "sends the shipper to PayPal" in r.get_json()["error"]
        make.assert_not_called()

    def _authorize(self, mandate_id):
        auth = f"AUTH{uuid.uuid4().hex[:12].upper()}"
        unit = {
            "reference_id": "SHP-PAY-1",
            "payments": {"authorizations": [{"id": auth, "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}]},
        }
        if mandate_id:
            unit["custom_id"] = mandate_id
        reply = {"status": "ok", "http_status": 201, "error": None, "body": {"id": "5O190127TN364715T", "purchase_units": [unit]}}
        with patch.object(self.routes.paypal_client, "authorize_order", AsyncMock(return_value=reply)):
            r = self.client.post("/api/v1/payments/orders/5O190127TN364715T/authorize", headers=KEYED)
        assert r.status_code == 200
        return auth

    def test_the_booking_is_linked_to_the_mandate_paypal_names(self):
        auth = self._authorize("MND-ABC")
        assert run(mod("payment_binding").find_mandate(auth)) == "MND-ABC"

    def test_a_booking_paypal_names_no_mandate_for_is_not_linked_to_one(self):
        auth = self._authorize(None)
        assert run(mod("payment_binding").find_mandate(auth)) is None


def booked(mandate_id, authorization, carrier="Mekong Haulage Co"):
    """A released case with its own authorization, linked to the mandate (or to none)."""
    if mandate_id:
        run(mod("payment_binding").record_mandate(authorization, mandate_id))
    shipment = {**copy.deepcopy(SHIPMENT), "carrier_name": carrier, "paypal_authorization_id": authorization}
    return decision(new_case(shipment=shipment))


def mandate_for(**over):
    args = {"shipper": "shipper@forwarder.example", "max_payout_usd": 1300, "carriers": ["Mekong Haulage Co"], "valid_days": 7,
            "reference_id": "SHP-PAY-1", "now": datetime.now(timezone.utc)}
    args.update(over)
    signed = mod().issue(args["shipper"], args["max_payout_usd"], args["carriers"], args["valid_days"], args["reference_id"], args["now"])
    run(mod().record(signed))
    return signed


def fresh():
    return f"AUTH{uuid.uuid4().hex[:12].upper()}"


def test_a_booking_whose_mandate_allows_the_payout_is_paid(pp):  # noqa: F811
    settler, capture, payout, void = pp
    signed = mandate_for()
    assert run(settler.settle(booked(signed["mandate"]["mandate_id"], fresh())))["status"] == "settled"
    payout.assert_awaited_once()


@pytest.mark.parametrize("terms, phrase", [
    ({"carriers": ["Aster Freight Co"]}, "not one the shipper's mandate allows"),
    ({"max_payout_usd": 1000}, "above the 1000.00 USD"),
    ({"reference_id": "SHP-OTHER"}, "another shipment"),
    ({"now": datetime.now(timezone.utc) - timedelta(days=30), "valid_days": 1}, "expired"),
])
def test_a_booking_outside_its_mandate_is_held_and_nothing_moves(pp, terms, phrase):  # noqa: F811
    settler, capture, payout, void = pp
    result = run(settler.settle(booked(mandate_for(**terms)["mandate"]["mandate_id"], fresh())))
    assert result["status"] == "held"
    assert phrase in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_forged_mandate_is_held(pp):  # noqa: F811
    settler, capture, payout, void = pp
    signed = mandate_for(max_payout_usd=100)
    forged = copy.deepcopy(signed)
    forged["mandate"]["max_payout_usd"] = "9999.00"
    forged["mandate"]["mandate_id"] = "MND-FORGED" + uuid.uuid4().hex[:6].upper()
    run(mod().record(forged))
    result = run(settler.settle(booked(forged["mandate"]["mandate_id"], fresh())))
    assert result["status"] == "held"
    assert "valid signature" in result["reason"]
    capture.assert_not_called()


def test_a_mandate_that_cannot_be_found_is_held(pp):  # noqa: F811
    settler, capture, payout, void = pp
    result = run(settler.settle(booked("MND-NOBODY", fresh())))
    assert result["status"] == "held"
    assert "cannot be found" in result["reason"]
    capture.assert_not_called()


def test_a_mandate_that_cannot_be_verified_is_held(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    signed = mandate_for()
    monkeypatch.delenv("VF_MANDATE_SECRET")
    result = run(settler.settle(booked(signed["mandate"]["mandate_id"], fresh())))
    assert result["status"] == "held"
    assert "cannot be verified" in result["reason"]
    capture.assert_not_called()


def test_a_booking_with_no_mandate_is_judged_by_the_other_layers_as_before(pp):  # noqa: F811
    settler, capture, payout, void = pp
    assert run(settler.settle(booked(None, fresh())))["status"] == "settled"


def test_a_block_voids_whatever_the_mandate_says(pp):  # noqa: F811
    settler, capture, payout, void = pp
    signed = mandate_for(carriers=["Aster Freight Co"])
    shipment = {**copy.deepcopy(SHIPMENT), "paypal_authorization_id": fresh()}
    run(mod("payment_binding").record_mandate(shipment["paypal_authorization_id"], signed["mandate"]["mandate_id"]))
    d = decision(new_case(state="BLOCKED_BY_HUMAN", shipment=shipment), outcome="BLOCKED_BY_HUMAN")
    assert run(settler.settle(d))["status"] == "voided"
    void.assert_awaited_once()
