from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_routes import KEYED, ORDER  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ORDER_ID = "5O190127TN364715T"
BOOKING = {
    "shipment_id": "SHP-PAY-1",
    "shipper_company": "Saigon Textile Export JSC",
    "receiver_company": "Hanoi Garment Retail Co",
    "carrier_name": "Mekong Haulage Co",
    "shipping_cost": "20",
}


class TestTheBookingCarriesTheReceiver(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def test_an_order_keeps_the_receiver_company(self):
        routes = importlib.import_module("vf_logistics.payment_routes")
        body = {"amount": 20, "currency": "USD", "reference_id": "SHP-PAY-1", "booking": BOOKING}
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)):
            made = self.client.post("/api/v1/payments/orders", json=body, headers=KEYED)
        assert made.status_code == 201
        read = self.client.get(f"/api/v1/payments/orders/{ORDER_ID}/booking", headers=KEYED)
        assert read.status_code == 200
        assert read.get_json()["booking"] == BOOKING
