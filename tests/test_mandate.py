"""
The shipper's mandate (mandate.py) and the three places it touches: the PayPal client's custom_id, the store, and the
booking's link to it (payment_binding.record_mandate / find_mandate).

Pinned here: a mandate is issued only with a signing secret, a person, a positive ceiling, named carriers, a lifetime of 1 to 30
days and the shipment it is for; it verifies only under the secret that signed it and only unchanged; it is refused when it has
expired, was given for another shipment, does not allow the carrier, or is below the payout; a secret that is not set means
nothing verifies; the order PayPal is asked for carries the mandate id as custom_id and an order without a mandate does not; and
the booking's mandate is found by its authorization.

The modules are looked up inside each test so that, before they exist, every test fails instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

SECRET = "s" * 32
REF = "SHP-PAY-1"
CARRIER = "Mekong Haulage Co"
KEY = "mekonghaulageco"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
_RealAsyncClient = httpx.AsyncClient


def run(coro):
    return asyncio.run(coro)


def mod(name="mandate"):
    return importlib.import_module(f"vf_logistics.{name}")


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("VF_MANDATE_SECRET", SECRET)


def issued(**over):
    args = {"shipper": "shipper@forwarder.example", "max_payout_usd": 1300, "carriers": [CARRIER], "valid_days": 7,
            "reference_id": REF, "now": NOW}
    args.update(over)
    return mod().issue(args["shipper"], args["max_payout_usd"], args["carriers"], args["valid_days"], args["reference_id"], args["now"])


class TestIssuing:
    def test_a_mandate_states_the_limits_the_shipper_set(self):
        signed = issued()
        payload = signed["mandate"]
        assert payload["type"] == "intent_mandate"
        assert payload["mandate_id"].startswith("MND-")
        assert payload["shipper"] == "shipper@forwarder.example"
        assert payload["reference_id"] == REF
        assert payload["max_payout_usd"] == "1300.00"
        assert payload["currency"] == "USD"
        assert payload["carriers"] == [KEY]
        assert payload["expires_at"] == (NOW + timedelta(days=7)).isoformat()
        assert len(signed["signature"]) == 64

    def test_carriers_are_kept_by_key_once_and_in_order(self):
        assert issued(carriers=["Mekong Haulage Co", "mekong haulage co", "Aster Freight 1 Co"])["mandate"]["carriers"] == [
            "asterfreight1co", KEY,
        ]

    @pytest.mark.parametrize("over, status, phrase", [
        ({"shipper": ""}, 403, "given by a person"),
        ({"max_payout_usd": 0}, 400, "above zero"),
        ({"max_payout_usd": -5}, 400, "above zero"),
        ({"max_payout_usd": "abc"}, 400, "above zero"),
        ({"max_payout_usd": "nan"}, 400, "above zero"),
        ({"carriers": []}, 400, "names the carriers"),
        ({"carriers": "Mekong"}, 400, "names the carriers"),
        ({"carriers": ["   "]}, 400, "names the carriers"),
        ({"valid_days": 0}, 400, "1 to 30"),
        ({"valid_days": 31}, 400, "1 to 30"),
        ({"valid_days": True}, 400, "1 to 30"),
        ({"valid_days": "7"}, 400, "1 to 30"),
        ({"reference_id": ""}, 400, "one shipment"),
    ])
    def test_a_mandate_that_is_not_well_formed_is_refused(self, over, status, phrase):
        with pytest.raises(mod().MandateError) as raised:
            issued(**over)
        assert raised.value.status == status
        assert phrase in raised.value.message

    @pytest.mark.parametrize("secret", ["", "short"])
    def test_without_a_signing_secret_nothing_is_issued(self, monkeypatch, secret):
        monkeypatch.setenv("VF_MANDATE_SECRET", secret)
        with pytest.raises(mod().MandateError) as raised:
            issued()
        assert raised.value.status == 503
        assert "VF_MANDATE_SECRET" in raised.value.message


class TestVerifying:
    def test_a_mandate_inside_its_limits_has_no_refusal(self):
        assert mod().refusal(issued(), Decimal("1250"), KEY, REF, NOW + timedelta(days=1)) is None
        assert mod().refusal(issued(), Decimal("1300"), KEY, REF, NOW) is None

    def test_a_changed_mandate_does_not_verify(self):
        signed = issued()
        signed["mandate"]["max_payout_usd"] = "9999.00"
        assert "valid signature" in mod().refusal(signed, Decimal("5000"), KEY, REF, NOW)
        other = issued()
        other["mandate"]["carriers"].append("attackerfreight")
        assert "valid signature" in mod().refusal(other, Decimal("100"), "attackerfreight", REF, NOW)

    def test_a_mandate_signed_under_another_secret_does_not_verify(self, monkeypatch):
        signed = issued()
        monkeypatch.setenv("VF_MANDATE_SECRET", "t" * 32)
        assert "valid signature" in mod().refusal(signed, Decimal("100"), KEY, REF, NOW)

    def test_without_the_secret_nothing_verifies(self, monkeypatch):
        signed = issued()
        monkeypatch.delenv("VF_MANDATE_SECRET")
        assert "cannot be verified" in mod().refusal(signed, Decimal("100"), KEY, REF, NOW)

    @pytest.mark.parametrize("payout, carrier, reference, now, phrase", [
        (Decimal("100"), KEY, REF, NOW + timedelta(days=7), "expired"),
        (Decimal("100"), KEY, "SHP-OTHER", NOW, "another shipment"),
        (Decimal("100"), "attackerfreight", REF, NOW, "not one the shipper's mandate allows"),
        (Decimal("1300.01"), KEY, REF, NOW, "above the 1300.00 USD"),
        (None, KEY, REF, NOW, "above the 1300.00 USD"),
    ])
    def test_a_payout_outside_the_mandate_is_refused_with_the_reason(self, payout, carrier, reference, now, phrase):
        assert phrase in mod().refusal(issued(), payout, carrier, reference, now)

    @pytest.mark.parametrize("signed", [None, {}, {"mandate": "x", "signature": "y"}, {"mandate": {}, "signature": 5}, "text"])
    def test_something_that_is_not_a_mandate_is_refused(self, signed):
        assert "unreadable" in mod().refusal(signed, Decimal("100"), KEY, REF, NOW)

    def test_a_mandate_with_an_expiry_that_cannot_be_read_is_refused(self):
        signed = issued()
        signed["mandate"]["expires_at"] = "soon"
        signed["signature"] = mod()._sign(signed["mandate"], SECRET.encode())
        assert "unreadable" in mod().refusal(signed, Decimal("100"), KEY, REF, NOW)
        signed["mandate"]["expires_at"] = "2030-01-01T00:00:00"
        signed["signature"] = mod()._sign(signed["mandate"], SECRET.encode())
        assert "expired" in mod().refusal(signed, Decimal("100"), KEY, REF, NOW)


class TestKeeping:
    def test_a_recorded_mandate_is_found_by_its_id_and_the_first_record_stands(self):
        signed = issued(reference_id=f"SHP-{uuid.uuid4().hex[:6]}")
        assert run(mod().record(signed)) is True
        assert run(mod().load(signed["mandate"]["mandate_id"])) == signed
        assert run(mod().record(signed)) is False

    def test_an_unknown_mandate_is_not_found_and_is_a_refusal(self):
        assert run(mod().load("MND-NOBODY")) is None
        assert run(mod().load("")) is None
        assert "cannot be found" in run(mod().refusal_for("MND-NOBODY", Decimal("100"), KEY, REF))

    def test_refusal_for_judges_the_stored_mandate(self):
        signed = issued(reference_id=f"SHP-{uuid.uuid4().hex[:6]}", now=datetime.now(timezone.utc))
        run(mod().record(signed))
        mid, ref = signed["mandate"]["mandate_id"], signed["mandate"]["reference_id"]
        assert run(mod().refusal_for(mid, Decimal("100"), KEY, ref)) is None
        assert "another shipment" in run(mod().refusal_for(mid, Decimal("100"), KEY, "SHP-OTHER"))


class TestTheBookingsMandate:
    def test_a_booking_names_its_mandate_by_its_authorization(self):
        binding = mod("payment_binding")
        auth = f"AUTH{uuid.uuid4().hex[:12].upper()}"
        assert run(binding.find_mandate(auth)) is None
        assert run(binding.record_mandate(auth, "MND-ABC")) is True
        assert run(binding.find_mandate(auth)) == "MND-ABC"
        assert run(binding.record_mandate(auth, "MND-OTHER")) is False
        assert run(binding.find_mandate(auth)) == "MND-ABC"
        assert run(binding.find_mandate("")) is None

    def test_the_booking_record_itself_is_unchanged(self):
        binding = mod("payment_binding")
        auth = f"AUTH{uuid.uuid4().hex[:12].upper()}"
        run(binding.record_authorization(auth, REF, "1300.00", "USD", "ORDER-1"))
        run(binding.record_mandate(auth, "MND-ABC"))
        assert run(binding.find_authorization(auth)) == {
            "authorization_id": auth, "reference_id": REF, "amount": "1300.00", "currency": "USD", "order_id": "ORDER-1",
        }


class TestThePayPalOrder:
    @pytest.fixture
    def paypal(self, monkeypatch):
        pp = importlib.import_module("vf_logistics.paypal_client")
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "tok-1"})
            return httpx.Response(201, json={"id": "ORDER-1", "status": "PAYER_ACTION_REQUIRED"})

        monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
        monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
        monkeypatch.setattr(pp.httpx, "AsyncClient", lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler), **kw))
        return pp, calls

    def test_the_mandate_id_travels_with_the_order_as_custom_id(self, paypal):
        pp, calls = paypal
        receipt = run(pp.create_order_for_approval(1300, "USD", "req-1", REF, "https://x.example/r", "https://x.example/c", custom_id="MND-ABC"))
        assert receipt["status"] == pp.OK
        unit = json.loads(calls[1].content)["purchase_units"][0]
        assert unit["custom_id"] == "MND-ABC"
        assert unit["reference_id"] == REF

    def test_an_order_without_a_mandate_carries_no_custom_id(self, paypal):
        pp, calls = paypal
        run(pp.create_order_for_approval(1300, "USD", "req-1", REF, "https://x.example/r", "https://x.example/c"))
        assert "custom_id" not in json.loads(calls[1].content)["purchase_units"][0]
