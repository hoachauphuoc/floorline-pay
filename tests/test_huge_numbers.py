"""
Numbers too large to format or to convert (paypal_client._money, mandate.issue, payment_controls.validate_limits).

Decimal("1E+99999999") is finite and above zero, and formatting it with two decimals builds a string of a hundred million
characters (a larger exponent runs the worker out of memory); math.isfinite of an integer with 400 digits raises
OverflowError. Both came out as a 500 or a stalled worker instead of a 400. An amount and a mandate ceiling above 1000000000
USD are refused, and a limit too large for a float is not a number above zero.
"""

from __future__ import annotations

import importlib

import pytest


def mod(name):
    return importlib.import_module(f"vf_logistics.{name}")


class TestAPaymentAmount:
    def test_an_enormous_exponent_is_refused(self):
        assert mod("paypal_client")._money("1E+99999999", "USD") is None

    def test_an_amount_above_the_most_is_refused(self):
        assert mod("paypal_client")._money("1000000000.01", "USD") is None

    def test_an_amount_at_the_most_is_still_formatted(self):
        assert mod("paypal_client")._money("1000000000", "USD") == {"currency_code": "USD", "value": "1000000000.00"}


class TestAMandateCeiling:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("VF_MANDATE_SECRET", "m" * 32)

    def test_a_ceiling_above_the_most_is_a_400(self):
        mandate = mod("mandate")
        with pytest.raises(mandate.MandateError) as refused:
            mandate.issue("shipper@example.com", "1E+99999999", ["Mekong Haulage"], 3, "SHP-1")
        assert refused.value.status == 400
        assert "most a mandate may name" in str(refused.value)

    def test_a_ceiling_at_the_most_is_signed(self):
        signed = mod("mandate").issue("shipper@example.com", "1000000000", ["Mekong Haulage"], 3, "SHP-1")
        assert signed["mandate"]["max_payout_usd"] == "1000000000.00"


class TestPaymentLimits:
    def test_a_limit_with_hundreds_of_digits_is_a_400(self):
        controls = mod("payment_controls")
        with pytest.raises(controls.ControlsError) as refused:
            controls.validate_limits({**controls.DEFAULT_LIMITS, "max_payout_usd": 10**400})
        assert refused.value.status == 400
        assert "must be a number above zero" in str(refused.value)

    def test_the_default_limits_are_still_accepted(self):
        controls = mod("payment_controls")
        assert controls.validate_limits(dict(controls.DEFAULT_LIMITS))["carrier_daily_count"] == controls.DEFAULT_LIMITS["carrier_daily_count"]
