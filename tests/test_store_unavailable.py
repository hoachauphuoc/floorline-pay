"""
A store outage or an exhausted read quota is a 503 that says to retry, not a bare 500 (7 Nov 2026).

The live service answered every read with "Internal server error" while /health said healthy; the likely cause was the Firestore free read quota.
The booking and authorize routes have no handler of their own, so the error must be mapped for the whole app. A refusal is still a refusal:
PayPal is not asked when the store cannot be read, and any other failure stays a 500.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

from google.api_core.exceptions import DeadlineExceeded, ResourceExhausted, ServiceUnavailable

sys.path.insert(0, os.path.dirname(__file__))
from test_payment_routes import KEYED  # noqa: E402
from test_routes_crash import KEY, CrashBase  # noqa: E402

ORDER_ID = "5O190127TN364715T"
PHRASE = "temporarily unavailable"


class TestAStoreOutageIsAnOutage(CrashBase):
    def read(self, url, error, production=True):
        with patch.object(self.app_mod, "_IS_PRODUCTION", production), \
                patch.object(self.app_mod, "_on_worker", side_effect=error):
            return self.client.get(url, headers=KEY)

    def test_an_exhausted_quota_on_a_case_read_is_a_503_that_says_to_retry(self):
        response = self.read("/api/v1/orchestrator/case/CASE-1", ResourceExhausted("429 Quota exceeded."))
        assert response.status_code == 503
        assert PHRASE in response.get_json()["error"]
        assert response.headers["Retry-After"] == "60"

    def test_the_503_does_not_leak_the_storage_error(self):
        response = self.read("/api/v1/orchestrator/case/CASE-1", ResourceExhausted("429 Quota exceeded for project secret-project."))
        assert "secret-project" not in response.get_data(as_text=True)

    def test_an_unavailable_store_on_the_audit_read_is_a_503(self):
        response = self.read("/api/v1/audit", ServiceUnavailable("backend down"))
        assert response.status_code == 503
        assert PHRASE in response.get_json()["error"]

    def test_a_deadline_on_the_case_list_is_a_503(self):
        response = self.read("/api/v1/cases", DeadlineExceeded("deadline"))
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "60"

    def test_any_other_failure_is_still_a_500_that_does_not_leak_the_reason(self):
        response = self.read("/api/v1/orchestrator/case/CASE-1", RuntimeError("store down"))
        assert response.status_code == 500
        assert response.get_json() == {"error": "Internal server error"}

    def test_the_authorize_route_is_a_503_and_paypal_is_not_asked(self):
        routes = importlib.import_module("vf_logistics.payment_routes")
        with patch.object(routes.payment_binding, "find_order", AsyncMock(side_effect=ServiceUnavailable("down"))), \
                patch.object(routes.paypal_client, "authorize_order", AsyncMock()) as authorize:
            response = self.client.post(f"/api/v1/payments/orders/{ORDER_ID}/authorize", headers=KEYED)
        assert response.status_code == 503
        assert PHRASE in response.get_json()["error"]
        assert response.headers["Retry-After"] == "60"
        authorize.assert_not_awaited()

    def test_the_booking_route_is_a_503_when_the_kept_booking_cannot_be_read(self):
        routes = importlib.import_module("vf_logistics.payment_routes")
        with patch.object(routes.payment_binding, "find_booking", AsyncMock(side_effect=ResourceExhausted("429 Quota exceeded."))):
            response = self.client.get(f"/api/v1/payments/orders/{ORDER_ID}/booking", headers=KEYED)
        assert response.status_code == 503
        assert PHRASE in response.get_json()["error"]
