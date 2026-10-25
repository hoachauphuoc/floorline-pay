"""
The booking route that writes the binding (payment_routes.py, POST .../authorize).

Pinned here: the route records the booking from PayPal's own reply (the reference the order
was made for and the amount held); a reply that lacks the reference or the amount is a 502
and records nothing; a replayed authorize keeps the first booking.

payment_binding is imported inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}


def run(coro):
    return asyncio.run(coro)


def authorized(authorization_id, reference="SHP-PAY-1", value="1300.00", currency="USD"):
    unit = {"payments": {"authorizations": [{
        "id": authorization_id, "status": "CREATED",
        "amount": {"currency_code": currency, "value": value},
    }]}}
    if reference is not None:
        unit["reference_id"] = reference
    return {"status": "ok", "http_status": 201, "error": None,
            "body": {"id": "5O190127TN364715T", "purchase_units": [unit]}}


@pytest.fixture
def binding():
    return importlib.import_module("vf_logistics.payment_binding")


def fresh():
    return f"AUTH{uuid.uuid4().hex[:12].upper()}"


class TestTheAuthorizeRoute(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def _authorize(self, reply):
        routes = importlib.import_module("vf_logistics.payment_routes")
        with patch.object(routes.paypal_client, "authorize_order", AsyncMock(return_value=reply)):
            run(importlib.import_module("vf_logistics.payment_binding").record_order("5O190127TN364715T", "SHP-PAY-1"))
            return self.client.post("/api/v1/payments/orders/5O190127TN364715T/authorize", headers=KEYED)

    def test_the_route_records_the_booking_from_paypals_own_reply(self, binding):
        auth = fresh()
        r = self._authorize(authorized(auth, reference="SHP-PAY-7", value="820.50"))
        assert r.status_code == 200
        assert r.get_json() == {"order_id": "5O190127TN364715T", "authorization_id": auth}
        found = run(binding.find_authorization(auth))
        assert found["reference_id"] == "SHP-PAY-7"
        assert found["amount"] == "820.50"
        assert found["currency"] == "USD"
        assert found["order_id"] == "5O190127TN364715T"

    @pytest.mark.parametrize("reply", [
        authorized("AUTHX1", reference=None),
        authorized("AUTHX2", value=""),
        authorized("AUTHX3", currency=""),
    ])
    def test_a_reply_without_the_reference_or_amount_is_not_a_booking(self, binding, reply):
        auth = reply["body"]["purchase_units"][0]["payments"]["authorizations"][0]["id"]
        r = self._authorize(reply)
        assert r.status_code == 502
        assert "no booking reference and amount" in r.get_json()["error"]
        assert "nothing is booked" in r.get_json()["error"]
        assert run(binding.find_authorization(auth)) is None

    def test_a_replayed_authorize_keeps_the_first_booking(self, binding):
        auth = fresh()
        self._authorize(authorized(auth, reference="SHP-FIRST", value="100.00"))
        r = self._authorize(authorized(auth, reference="SHP-SECOND", value="9000.00"))
        assert r.status_code == 200
        found = run(binding.find_authorization(auth))
        assert found["reference_id"] == "SHP-FIRST"
        assert found["amount"] == "100.00"
