"""
The body of POST /api/v1/payments/orders (payment_routes.py), two gaps found by the API review of 25 Oct 2026.

1. A JSON body that is not an object (a list, a string, a number) raised and answered 500. It is a 400 that says so.
2. A request_id or reference_id of any length went to PayPal and came back as a PayPal error (502). Past the limits it is a
   400 and PayPal is not asked.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}
ORDER = {"status": "ok", "http_status": 201, "body": {"id": "5O190127TN364715T", "status": "CREATED"}, "error": None}


class Booking(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()
        from vf_logistics import app as app_module

        self.limiter_was = app_module.limiter.enabled
        app_module.limiter.enabled = False

    def teardown_method(self):
        from vf_logistics import app as app_module

        app_module.limiter.enabled = self.limiter_was
        super().teardown_method()

    def _create(self, payload):
        routes = importlib.import_module("vf_logistics.payment_routes")
        with patch.object(routes.paypal_client, "create_order", AsyncMock(return_value=ORDER)) as create:
            r = self.client.post("/api/v1/payments/orders", json=payload, headers=KEYED)
        return r, create


class TestABodyThatIsNotAnObject(Booking):
    @pytest.mark.parametrize("payload", [[1], "x", 5])
    def test_it_is_refused_with_400_and_paypal_is_not_asked(self, payload):
        r, create = self._create(payload)
        assert r.status_code == 400
        assert "JSON object" in r.get_json()["error"]
        create.assert_not_called()


class TestOverlongIds(Booking):
    def _body(self, **extra):
        return {"amount": 1250.5, "currency": "USD", "reference_id": "SHP-PAY-1", **extra}

    def test_a_request_id_over_100_characters_is_refused(self):
        r, create = self._create(self._body(request_id="r" * 101))
        assert r.status_code == 400
        assert "request_id may be at most 100" in r.get_json()["error"]
        create.assert_not_called()

    def test_a_reference_over_256_characters_is_refused(self):
        r, create = self._create(self._body(reference_id="S" * 257))
        assert r.status_code == 400
        assert "reference_id at most 256" in r.get_json()["error"]
        create.assert_not_called()

    def test_ids_at_the_limits_still_reach_paypal(self):
        r, create = self._create(self._body(request_id="r" * 100, reference_id="S" * 256))
        assert r.status_code == 201
        create.assert_awaited_once_with(1250.5, "USD", "r" * 100, "S" * 256)
