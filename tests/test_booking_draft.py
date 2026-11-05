"""
The booking form kept on the server under its PayPal order (review F04, 2026-11-05).

The form lived only in the browser tab, so a tab closed after PayPal left a hold with no case. Pinned here: an order created with
a booking keeps it for the tenant that created the order; the first booking stands; a booking for another shipment, with an
unknown field or with a value that is not text is refused before PayPal is asked; an order with no kept booking answers 404.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_routes import KEYED, ORDER  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ORDER_ID = "5O190127TN364715T"
BOOKING = {"shipment_id": "SHP-PAY-1", "shipper_company": "Saigon Plywood Co", "carrier_name": "Mekong Haulage Co", "shipping_cost": "1300"}


def routes():
    return importlib.import_module("vf_logistics.payment_routes")


def binding():
    return importlib.import_module("vf_logistics.payment_binding")


class TestTheKeptBooking(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def _create(self, booking):
        body = {"amount": 1300, "currency": "USD", "reference_id": "SHP-PAY-1", "booking": booking}
        with patch.object(routes().paypal_client, "create_order", AsyncMock(return_value=ORDER)) as create:
            return self.client.post("/api/v1/payments/orders", json=body, headers=KEYED), create

    def _read(self, order_id=ORDER_ID):
        return self.client.get(f"/api/v1/payments/orders/{order_id}/booking", headers=KEYED)

    def test_an_order_keeps_its_booking_and_gives_it_back(self):
        made, _ = self._create(BOOKING)
        assert made.status_code == 201
        r = self._read()
        assert r.status_code == 200
        assert r.get_json() == {"order_id": ORDER_ID, "booking": BOOKING}

    def test_the_first_booking_of_an_order_stands(self):
        self._create(BOOKING)
        self._create({**BOOKING, "carrier_name": "Another Carrier"})
        assert self._read().get_json()["booking"]["carrier_name"] == "Mekong Haulage Co"

    @pytest.mark.parametrize("bad, phrase", [
        ("not an object", "must be a JSON object"),
        ({**BOOKING, "shipment_id": "SHP-OTHER"}, "shipment_id"),
        ({**BOOKING, "password": "x"}, "unknown field"),
        ({**BOOKING, "origin": 7}, "text"),
        ({**BOOKING, "origin": "x" * 501}, "text"),
    ])
    def test_a_booking_that_cannot_be_kept_is_refused_before_paypal_is_asked(self, bad, phrase):
        r, create = self._create(bad)
        assert r.status_code == 400
        assert phrase in r.get_json()["error"]
        assert "nothing is booked" in r.get_json()["error"]
        create.assert_not_called()

    def test_an_order_with_no_kept_booking_answers_404(self):
        r = self._read()
        assert r.status_code == 404
        assert "start again from Book and pay" in r.get_json()["error"]

    def test_an_order_id_with_unexpected_characters_is_refused(self):
        r = self._read("BAD$ID")
        assert r.status_code == 400
        assert "not a PayPal order id" in r.get_json()["error"]

    def test_another_tenants_booking_is_not_given_back(self):
        asyncio.run(binding().record_booking(ORDER_ID, BOOKING, tenant_id="tenant-a"))
        assert self._read().status_code == 404
