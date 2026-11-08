"""
An order PayPal no longer holds is a 409 that says to book again (reviewer recheck, 8 Nov 2026).

PayPal answers 404 RESOURCE_NOT_FOUND to the authorize of an order that expired. The route used to turn that into a 502, which the
console shows as "the API is unreachable" and advises to reload. Pinned here: the 409 names the cause, nothing is recorded, and
every other PayPal refusal is still a 502.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_routes import KEYED  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ORDER_ID = "5O190127TN364715T"
GONE = {"status": "http_error", "http_status": 404, "body": {"name": "RESOURCE_NOT_FOUND"}, "error": "RESOURCE_NOT_FOUND"}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "ORDER_NOT_APPROVED"}, "error": "ORDER_NOT_APPROVED"}


class TestAuthorizingAnOrderThatIsGone(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()
        self.routes = importlib.import_module("vf_logistics.payment_routes")
        asyncio.run(importlib.import_module("vf_logistics.payment_binding").record_order(ORDER_ID, "SHP-PAY-1"))

    def _authorize(self):
        return self.client.post(f"/api/v1/payments/orders/{ORDER_ID}/authorize", headers=KEYED)

    def test_an_order_paypal_no_longer_holds_is_a_409_that_says_book_again(self):
        with patch.object(self.routes.paypal_client, "authorize_order", AsyncMock(return_value=GONE)):
            response = self._authorize()
        assert response.status_code == 409
        body = response.get_json()
        assert "no longer holds" in body["error"]
        assert "nothing was held or charged" in body["error"]
        assert "Book again" in body["error"]
        assert body["paypal_error"] == "RESOURCE_NOT_FOUND"
        binding = importlib.import_module("vf_logistics.payment_binding")
        assert asyncio.run(binding.find_order_authorization(ORDER_ID)) is None

    def test_another_paypal_refusal_is_still_a_502(self):
        with patch.object(self.routes.paypal_client, "authorize_order", AsyncMock(return_value=REFUSED)):
            response = self._authorize()
        assert response.status_code == 502
        assert response.get_json()["paypal_status"] == "http_error"
