"""
An order authorized once answers with that authorization (review F04, 2026-11-05).

A reload of the return page repeats the authorize. Pinned here: PayPal is asked once and both answers carry the same authorization;
a refused authorize records nothing, so the next try asks PayPal again.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_routes import AUTHORIZED, KEYED  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ORDER_ID = "5O190127TN364715T"
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "ORDER_NOT_APPROVED"}, "error": "ORDER_NOT_APPROVED"}


class TestAuthorizingAgain(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()
        self.routes = importlib.import_module("vf_logistics.payment_routes")
        asyncio.run(importlib.import_module("vf_logistics.payment_binding").record_order(ORDER_ID, "SHP-PAY-1"))

    def _authorize(self):
        return self.client.post(f"/api/v1/payments/orders/{ORDER_ID}/authorize", headers=KEYED)

    def test_a_second_authorize_answers_from_the_first_without_asking_paypal(self):
        with patch.object(self.routes.paypal_client, "authorize_order", AsyncMock(return_value=AUTHORIZED)) as authorize:
            first = self._authorize()
            second = self._authorize()
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.get_json() == first.get_json()
        authorize.assert_awaited_once()

    def test_a_refused_authorize_is_asked_again(self):
        with patch.object(self.routes.paypal_client, "authorize_order", AsyncMock(side_effect=[REFUSED, AUTHORIZED])) as authorize:
            first = self._authorize()
            second = self._authorize()
        assert first.status_code != 200
        assert second.status_code == 200
        assert authorize.await_count == 2
