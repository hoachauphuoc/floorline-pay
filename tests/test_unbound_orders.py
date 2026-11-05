"""
Holds with no case can be found from the console (review F04, 2026-11-05).

A hold whose browser never came back left nothing to look at. Pinned here: an authorized order no case holds is listed; one a case
holds is not; the list needs an operator.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402
from test_payment_routes import KEYED  # noqa: E402
from vf_logistics.app import app  # noqa: E402

ORDER_ID = "5O190127TN364715T"
URL = "/api/v1/payments/orders/unbound"


def run(coro):
    return asyncio.run(coro)


def binding():
    return importlib.import_module("vf_logistics.payment_binding")


class TestHoldsWithNoCase(Base):
    def setup_method(self):
        super().setup_method()
        self.client = app.test_client()

    def test_an_authorized_order_no_case_holds_is_listed(self):
        run(binding().record_order_authorization(ORDER_ID, "AUTH-1"))
        r = self.client.get(URL, headers=KEYED)
        assert r.status_code == 200
        orders = r.get_json()["orders"]
        assert [(o["order_id"], o["authorization_id"]) for o in orders] == [(ORDER_ID, "AUTH-1")]
        assert orders[0]["at"]

    def test_an_order_whose_authorization_a_case_holds_is_not_listed(self):
        run(binding().record_order_authorization(ORDER_ID, "AUTH-1"))
        run(self.store.put_case({"case_id": "CASE-O", "state": "HELD_FOR_REVIEW", "shipment": {"paypal_authorization_id": "AUTH-1"}}))
        assert self.client.get(URL, headers=KEYED).get_json()["orders"] == []

    def test_no_order_lists_nothing(self):
        assert self.client.get(URL, headers=KEYED).get_json()["orders"] == []

    def test_the_list_needs_a_signed_in_operator(self):
        assert self.client.get(URL).status_code in (401, 403)
