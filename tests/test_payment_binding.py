"""
The record that ties an authorization to its shipment (payment_binding.py).

Pinned here: a recorded authorization is found with what PayPal said about it; an unknown or
blank one is not found; the first record for an authorization stands.

payment_binding is imported inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def binding():
    return importlib.import_module("vf_logistics.payment_binding")


def fresh():
    return f"AUTH{uuid.uuid4().hex[:12].upper()}"


class TestTheBinding(Base):
    def test_a_recorded_authorization_is_found_with_what_paypal_said(self, binding):
        auth = fresh()
        assert run(binding.record_authorization(auth, "SHP-PAY-1", "1300.00", "USD", "ORDER-1")) is True
        found = run(binding.find_authorization(auth))
        assert found == {
            "authorization_id": auth, "reference_id": "SHP-PAY-1", "amount": "1300.00",
            "currency": "USD", "order_id": "ORDER-1",
        }

    def test_an_authorization_nobody_recorded_is_not_found(self, binding):
        assert run(binding.find_authorization(fresh())) is None

    def test_a_blank_authorization_is_not_found(self, binding):
        assert run(binding.find_authorization("")) is None

    def test_the_first_record_for_an_authorization_stands(self, binding):
        auth = fresh()
        assert run(binding.record_authorization(auth, "SHP-FIRST", "1300.00", "USD", "ORDER-1")) is True
        assert run(binding.record_authorization(auth, "SHP-SECOND", "9999.00", "USD", "ORDER-2")) is False
        found = run(binding.find_authorization(auth))
        assert found["reference_id"] == "SHP-FIRST"
        assert found["amount"] == "1300.00"

    def test_one_authorization_never_finds_another(self, binding):
        first, second = fresh(), fresh()
        run(binding.record_authorization(first, "SHP-1", "10.00", "USD", "ORDER-1"))
        assert run(binding.find_authorization(second)) is None
