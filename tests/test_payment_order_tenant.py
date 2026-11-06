"""
Which tenant may authorize a PayPal order (payment_routes.py, payment_binding.py).

An order id is not a secret, and PayPal answers a repeated authorize with the result it gave the first time. The authorize
route accepted any order id, so a tenant that authorized another tenant's order first had the booking bound in its own tenant,
and its release captured the other tenant's shipper. Pinned here: creating an order claims it for the tenant that created it;
a tenant authorizes only an order it claimed, and PayPal is not asked otherwise; an order another tenant holds is refused at
creation too.

payment_binding and payment_routes are looked up inside each test so that, before the new functions exist, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402

KEYED = {"X-VF-API-Key": TEST_KEY}


def run(coro):
    return asyncio.run(coro)


def binding():
    return importlib.import_module("vf_logistics.payment_binding")


def routes():
    return importlib.import_module("vf_logistics.payment_routes")


def order_id():
    return f"ORD{uuid.uuid4().hex[:14].upper()}"


def created(oid):
    return {"status": "ok", "http_status": 201, "error": None, "body": {"id": oid, "status": "CREATED"}}


def authorized(oid):
    return {"status": "ok", "http_status": 201, "error": None, "body": {"id": oid, "purchase_units": [{
        "reference_id": "SHP-TEN-1",
        "payments": {"authorizations": [{
            "id": f"AUTH{uuid.uuid4().hex[:12].upper()}", "status": "CREATED",
            "amount": {"currency_code": "USD", "value": "1300.00"},
        }]},
    }]}}


class TestTheOrderRecord:
    def test_an_order_is_found_in_the_tenant_that_recorded_it(self):
        oid = order_id()
        assert run(binding().record_order(oid, "SHP-TEN-1", tenant_id="tenant-a")) is True
        assert run(binding().find_order(oid, tenant_id="tenant-a"))["reference_id"] == "SHP-TEN-1"

    def test_another_tenant_finds_nothing_and_cannot_claim_it(self):
        oid = order_id()
        run(binding().record_order(oid, "SHP-TEN-1", tenant_id="tenant-a"))
        assert run(binding().find_order(oid, tenant_id="tenant-b")) is None
        assert run(binding().record_order(oid, "SHP-TEN-1", tenant_id="tenant-b")) is False
        assert run(binding().find_order(oid, tenant_id="tenant-b")) is None

    def test_the_tenant_that_holds_it_may_record_it_again(self):
        oid = order_id()
        assert run(binding().record_order(oid, "SHP-TEN-1")) is True
        assert run(binding().record_order(oid, "SHP-TEN-1")) is True

    def test_an_empty_order_id_has_no_record(self):
        assert run(binding().find_order("")) is None


class TestTheAuthorizeRoute(Base):
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

    def _authorize(self, oid):
        with patch.object(routes().paypal_client, "authorize_order", AsyncMock(return_value=authorized(oid))) as authorize:
            r = self.client.post(f"/api/v1/payments/orders/{oid}/authorize", headers=KEYED)
        return r, authorize

    def _create(self, oid):
        with patch.object(routes().paypal_client, "create_order", AsyncMock(return_value=created(oid))):
            return self.client.post("/api/v1/payments/orders", headers=KEYED,
                                    json={"amount": 1300, "currency": "USD", "reference_id": "SHP-TEN-1"})

    def test_an_order_this_tenant_never_created_is_refused_and_paypal_is_not_asked(self):
        r, authorize = self._authorize(order_id())
        assert r.status_code == 404
        assert "not this tenant's order" in r.get_json()["error"]
        authorize.assert_not_called()

    def test_an_order_another_tenant_created_is_refused_and_paypal_is_not_asked(self):
        oid = order_id()
        run(binding().record_order(oid, "SHP-TEN-1", tenant_id="tenant-a"))
        r, authorize = self._authorize(oid)
        assert r.status_code == 404
        assert "not this tenant's order" in r.get_json()["error"]
        authorize.assert_not_called()

    def test_an_order_this_tenant_created_is_claimed_and_authorized(self):
        oid = order_id()
        made = self._create(oid)
        assert made.status_code == 201
        assert run(binding().find_order(oid))["reference_id"] == "SHP-TEN-1"
        r, authorize = self._authorize(oid)
        assert r.status_code == 200
        authorize.assert_awaited_once()

    def test_creating_an_order_another_tenant_holds_is_refused(self):
        oid = order_id()
        run(binding().record_order(oid, "SHP-TEN-1", tenant_id="tenant-a"))
        r = self._create(oid)
        assert r.status_code == 409
        assert "belongs to another tenant" in r.get_json()["error"]
        assert "order_id" not in r.get_json()
