"""
PayPal shipment tracking and the dispute look-up (paypal_client.add_tracking / get_dispute, settler.py, paypal_webhook.py,
schemas.ComplianceAuditRequest.tracking_number).

Pinned here: the tracking call attaches a bill of lading or container number to the order and its capture, as PayPal's order
tracking API wants it, and sends nothing when something it needs is missing; the settler tells PayPal after it has paid, never before
and never so that a failure changes the outcome of the payment; a shipment with no tracking number gets no call and no trace in the
result; an opened dispute is looked up at PayPal when the event does not carry its transactions, a dispute that cannot be looked up is a
503 so PayPal sends it again (never an event recorded as matching nothing), and one that carries its transactions costs no call.

The new functions are looked up inside each test so that, before they exist, every test fails instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(__file__))
from test_paypal_webhook import HEADERS, event, frozen, hook, settled  # noqa: E402, F401
from test_settler import BOOKING, SHIPMENT, decision, new_case, pp  # noqa: E402, F401
from vf_logistics.schemas import ComplianceAuditRequest  # noqa: E402

_RealAsyncClient = httpx.AsyncClient


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def paypal(monkeypatch):
    pp_mod = importlib.import_module("vf_logistics.paypal_client")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok-1"})
        return httpx.Response(200, json={"id": "X1"})

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setattr(pp_mod.httpx, "AsyncClient", lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler), **kw))
    return pp_mod, calls


class TestTheClientCalls:
    def test_tracking_is_attached_to_the_order_and_its_capture(self, paypal):
        client, calls = paypal
        receipt = run(client.add_tracking("5O190127TN364715T", "CAP-1", "MSKU1234567", "Mekong Haulage Co", "req-1"))
        assert receipt["status"] == client.OK
        sent = calls[1]
        assert sent.method == "POST"
        assert sent.url.path == "/v2/checkout/orders/5O190127TN364715T/track"
        assert sent.headers["PayPal-Request-Id"] == "req-1"
        assert json.loads(sent.content) == {
            "capture_id": "CAP-1", "tracking_number": "MSKU1234567", "carrier": "OTHER",
            "carrier_name_other": "Mekong Haulage Co", "notify_payer": False,
        }

    @pytest.mark.parametrize("args", [
        ("", "CAP-1", "MSKU1", "Carrier"), ("ORDER-1", "", "MSKU1", "Carrier"), ("ORDER-1", "CAP-1", "", "Carrier"),
        ("ORDER-1", "CAP-1", "MSKU1", ""),
    ])
    def test_tracking_with_something_missing_sends_nothing(self, paypal, args):
        client, calls = paypal
        assert run(client.add_tracking(*args, "req-1"))["status"] == client.BAD_INPUT
        assert calls == []

    def test_a_dispute_is_read_by_id(self, paypal):
        client, calls = paypal
        assert run(client.get_dispute("PP-D-27803", "req-2"))["status"] == client.OK
        assert calls[1].method == "GET"
        assert calls[1].url.path == "/v1/customer/disputes/PP-D-27803"

    def test_reading_a_dispute_without_an_id_sends_nothing(self, paypal):
        client, calls = paypal
        assert run(client.get_dispute("", "req-2"))["status"] == client.BAD_INPUT
        assert calls == []


class TestTheTrackingNumberOnTheRequest:
    def test_a_bill_of_lading_or_container_number_is_kept(self):
        req = ComplianceAuditRequest.model_validate({"shipment_id": "SHP-1", "tracking_number": "MSKU1234567"})
        assert req.tracking_number == "MSKU1234567"

    def test_it_is_optional(self):
        assert ComplianceAuditRequest.model_validate({"shipment_id": "SHP-1"}).tracking_number is None

    @pytest.mark.parametrize("value", ["has space", "semi;colon", "x" * 65, ""])
    def test_a_malformed_one_is_refused_with_the_field_name(self, value):
        with pytest.raises(ValidationError) as raised:
            ComplianceAuditRequest.model_validate({"shipment_id": "SHP-1", "tracking_number": value})
        assert any(e["loc"] == ("tracking_number",) for e in raised.value.errors())


def booked(tracking_number=None, order_id="5O190127TN364715T"):
    shipment = {**SHIPMENT, "carrier_name": "Mekong Haulage Co"}
    if tracking_number:
        shipment["tracking_number"] = tracking_number
    return decision(new_case(shipment=shipment)), {**BOOKING, "order_id": order_id}


class TestTheSettlerTellsPayPal:
    def test_after_paying_it_attaches_the_tracking_number_to_the_order_and_capture(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        d, booking = booked("MSKU1234567")
        settler.payment_binding.find_authorization.return_value = booking
        with patch.object(settler.paypal_client, "add_tracking", AsyncMock(return_value={"status": "ok", "http_status": 200, "error": None})) as track:
            result = run(settler.settle(d))
        assert result["status"] == "settled"
        track.assert_awaited_once_with("5O190127TN364715T", "CAP-1", "MSKU1234567", "Mekong Haulage Co", f"{d['idempotency_key']}-tracking")
        assert result["tracking"] == {"status": "ok", "http_status": 200, "error": None}

    def test_a_tracking_call_that_fails_or_raises_never_changes_the_payment(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        for outcome in ({"status": "http_error", "http_status": 422, "body": {}, "error": "NO"}, RuntimeError("down")):
            d, booking = booked("MSKU1234567")
            settler.payment_binding.find_authorization.return_value = booking
            mock = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
            with patch.object(settler.paypal_client, "add_tracking", mock):
                result = run(settler.settle(d))
            assert result["status"] == "settled"
            assert result["tracking"]["status"] in ("http_error", "error")

    def test_a_shipment_without_a_tracking_number_gets_no_call_and_no_trace(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        d, booking = booked(None)
        settler.payment_binding.find_authorization.return_value = booking
        with patch.object(settler.paypal_client, "add_tracking", AsyncMock()) as track:
            result = run(settler.settle(d))
        assert result["status"] == "settled"
        track.assert_not_called()
        assert "tracking" not in result

    def test_a_booking_without_an_order_is_skipped_not_guessed(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        d, booking = booked("MSKU1234567", order_id="")
        settler.payment_binding.find_authorization.return_value = booking
        with patch.object(settler.paypal_client, "add_tracking", AsyncMock()) as track:
            result = run(settler.settle(d))
        assert result["status"] == "settled"
        assert result["tracking"]["status"] == "skipped"
        track.assert_not_called()

    def test_nothing_is_told_to_paypal_when_nothing_was_paid(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        d, booking = booked("MSKU1234567")
        settler.payment_binding.find_authorization.return_value = booking
        payout.return_value = {"status": "http_error", "http_status": 422, "body": {}, "error": "NO"}
        with patch.object(settler.paypal_client, "add_tracking", AsyncMock()) as track:
            result = run(settler.settle(d))
        assert result["status"] == "failed"
        track.assert_not_called()


def dispute_event(case, **resource):
    return event("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "PP-D-1", **resource})


class TestTheDisputeLookUp:
    def test_a_dispute_event_without_its_transactions_is_looked_up_and_freezes_both_parties(self, hook):  # noqa: F811
        webhook, _ = hook
        case = settled()
        found = {"status": "ok", "http_status": 200, "error": None,
                 "body": {"disputed_transactions": [{"seller_transaction_id": f"CAP-{case['tag']}"}]}}
        with patch.object(webhook.paypal_client, "get_dispute", AsyncMock(return_value=found)) as look:
            status, reply = run(webhook.receive(HEADERS, dispute_event(case)))
        assert status == 200
        assert reply["frozen"] == ["carrier", "shipper"]
        look.assert_awaited_once()
        assert look.await_args.args[0] == "PP-D-1"
        assert frozen("carrier", case["carrier"]) is not None
        assert frozen("shipper", case["shipper"]) is not None

    def test_a_dispute_event_that_carries_its_transactions_costs_no_call(self, hook):  # noqa: F811
        webhook, _ = hook
        case = settled()
        with patch.object(webhook.paypal_client, "get_dispute", AsyncMock()) as look:
            status, reply = run(webhook.receive(HEADERS, dispute_event(
                case, disputed_transactions=[{"seller_transaction_id": f"CAP-{case['tag']}"}],
            )))
        assert status == 200
        assert reply["outcome"] == "applied"
        look.assert_not_called()

    @pytest.mark.parametrize("receipt", [
        {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"},
        {"status": "http_error", "http_status": 404, "body": {}, "error": "RESOURCE_NOT_FOUND"},
        {"status": "ok", "http_status": 200, "body": "not an object", "error": None},
    ])
    def test_a_dispute_that_cannot_be_looked_up_is_a_503_so_paypal_sends_it_again(self, hook, receipt):  # noqa: F811
        webhook, _ = hook
        case = settled()
        with patch.object(webhook.paypal_client, "get_dispute", AsyncMock(return_value=receipt)):
            status, reply = run(webhook.receive(HEADERS, dispute_event(case)))
        assert status == 503
        assert reply["outcome"] == "retry"
        assert frozen("carrier", case["carrier"]) is None

    def test_an_event_with_no_dispute_id_and_no_transactions_is_unmatched_not_an_error(self, hook):  # noqa: F811
        webhook, _ = hook
        status, reply = run(webhook.receive(HEADERS, event("CUSTOMER.DISPUTE.CREATED", {})))
        assert status == 200
        assert reply["outcome"] == "unmatched"
