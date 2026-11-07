"""
A refund, reversal, denial or dispute applied during a settlement stops that settlement's payout (follow-up review F05, 2026-11-07).

An event kept as waiting was applied right after the capture index rows were written and froze the carrier and the shipper, but the
freeze check had run before it, so the same run still paid the carrier. Pinned here: the payment is judged again right before the
payout, PayPal's payout call is made 0 times, the reason is stored, a person can retry once the freeze is lifted, and an event that
could not be applied stops the payout too.
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_paypal_webhook import HEADERS, event, hook  # noqa: E402, F401
from test_payout_recovery import batch  # noqa: E402
from test_payout_replacement import replacement  # noqa: E402
from test_settler_agent import OK as AGENT_CAPTURED, agent_decision, cleared, state_of  # noqa: E402
from test_settler_agent import pp as agent_pp  # noqa: E402, F401
from test_settler_controls import ADMIN, LIMITS, carrier, decision, mod, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402

ROOM = {**LIMITS, "carrier_daily_usd": 5000, "tenant_daily_usd": 5000, "carrier_daily_count": 5}
SHIPPER = "Saigon Plywood Co"
CAPTURE = "CAP-1"


def up(capture_id):
    return [{"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"}]


REFUND = ("PAYMENT.CAPTURE.REFUNDED", {"id": "REF-1", "links": up(CAPTURE)})
TROUBLE = [
    REFUND,
    ("PAYMENT.CAPTURE.REVERSED", {"id": "REV-1", "links": up(CAPTURE)}),
    ("PAYMENT.CAPTURE.DENIED", {"id": CAPTURE}),
    ("CUSTOMER.DISPUTE.CREATED", {"dispute_id": "D-1", "disputed_transactions": [{"seller_transaction_id": CAPTURE}]}),
]


def publish_room():
    run(mod("payment_controls").publish_limits(ROOM, "room for several payouts", ADMIN, "password"))


def arrives_before_the_capture(webhook, event_type, resource):
    status, reply = run(webhook.receive(HEADERS, event(event_type, resource)))
    assert (status, reply["outcome"]) == (200, "unmatched")


def settlements(d):
    rows, _ = run(get_store().query_audit(case_id=d["case_id"], action="settle_payment", limit=10))
    return rows


def lift_both(name):
    freezes = mod("payment_freeze")
    run(freezes.lift("carrier", name, "PayPal confirmed the refund was an error", ADMIN, "password"))
    run(freezes.lift("shipper", SHIPPER, "PayPal confirmed the refund was an error", ADMIN, "password"))


@pytest.mark.parametrize("event_type,resource", TROUBLE, ids=["refund", "reversal", "denial", "dispute"])
def test_an_event_kept_before_the_capture_stops_the_payout_it_would_have_paid(pp, hook, event_type, resource):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    name = carrier()
    d = decision(name)
    arrives_before_the_capture(webhook, event_type, resource)
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "the money is captured" in result["reason"]
    assert "frozen" in result["reason"]
    assert result["capture"]["paypal_id"] == CAPTURE
    capture.assert_awaited_once()
    payout.assert_not_called()
    assert run(mod("payment_freeze").frozen("carrier", name)) is not None
    assert run(mod("payment_freeze").frozen("shipper", SHIPPER)) is not None
    rows = settlements(d)
    assert [row["status"] for row in rows] == ["failed"]
    assert "frozen" in rows[0]["detail"]["reason"]


def test_after_the_freeze_is_lifted_the_same_decision_pays_once_on_the_same_capture(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    name = carrier()
    d = decision(name)
    arrives_before_the_capture(webhook, *REFUND)
    assert run(settler.settle(d))["status"] == "failed"
    lift_both(name)
    result = run(settler.settle(d))
    assert result["status"] == "settled"
    assert result["capture"]["paypal_id"] == CAPTURE
    payout.assert_awaited_once()
    assert capture.await_args_list[0] == capture.await_args_list[1]


def test_a_delivery_while_a_party_is_still_frozen_stays_failed_so_a_person_can_retry_later(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    arrives_before_the_capture(webhook, *REFUND)
    run(settler.settle(d))
    again = run(settler.settle(d))
    assert again["status"] == "failed"
    assert "the money is captured" in again["reason"]
    capture.assert_awaited_once()
    payout.assert_not_called()
    assert [row["status"] for row in settlements(d)] == ["failed"]


def test_a_kept_event_that_could_not_be_applied_stops_the_payout_until_it_is(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    arrives_before_the_capture(webhook, *REFUND)
    with patch.object(mod("payment_freeze"), "freeze", AsyncMock(side_effect=RuntimeError("the store is down"))):
        stopped = run(settler.settle(d))
    assert stopped["status"] == "retry"
    assert "has not taken effect" in stopped["reason"]
    payout.assert_not_called()
    judged = run(settler.settle(d))
    assert judged["status"] == "failed"
    assert "frozen" in judged["reason"]
    payout.assert_not_called()


def test_a_reconcile_that_fails_still_stops_the_payout_for_a_kept_event_about_the_capture(pp, hook, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    arrives_before_the_capture(webhook, *REFUND)
    monkeypatch.setattr(settler.paypal_webhook, "reconcile", AsyncMock(side_effect=RuntimeError("the store is down")))
    result = run(settler.settle(d))
    assert result["status"] == "retry"
    assert "has not taken effect" in result["reason"]
    payout.assert_not_called()


def test_a_kept_event_about_another_capture_leaves_this_payment_alone(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    arrives_before_the_capture(webhook, "PAYMENT.CAPTURE.REFUNDED", {"id": "REF-2", "links": up("CAP-OTHER")})
    assert run(settler.settle(d))["status"] == "settled"
    payout.assert_awaited_once()


def test_a_freeze_that_cannot_be_read_after_the_capture_stops_the_payout(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    publish_room()
    d = decision(carrier())
    freezes = mod("payment_freeze")
    real = freezes.frozen

    async def read(kind, name, tenant_id=None):
        if capture.await_count:
            raise RuntimeError("the store is down")
        return await real(kind, name, tenant_id)

    monkeypatch.setattr(freezes, "frozen", read)
    assert run(settler.settle(d))["status"] == "retry"
    payout.assert_not_called()


def test_a_completed_capture_with_no_id_cannot_be_judged_and_pays_nothing(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish_room()
    capture.return_value = {"status": "ok", "http_status": 201, "body": {"status": "COMPLETED"}, "error": None}
    result = run(settler.settle(decision(carrier())))
    assert result["status"] == "retry"
    assert "cannot be judged" in result["reason"]
    payout.assert_not_called()


def test_a_replacement_payout_is_not_sent_for_a_frozen_party(pp, hook, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    key = d["idempotency_key"]
    replacement(key)
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(key, status="DENIED")))
    arrives_before_the_capture(webhook, *REFUND)
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "frozen" in result["reason"]
    payout.assert_not_called()


def test_a_stop_before_any_payout_request_gives_the_reservation_back(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    publish_room()
    d = decision(carrier())
    arrives_before_the_capture(webhook, *REFUND)
    assert run(settler.settle(d))["status"] == "failed"
    assert run(mod("payment_controls")._open_reservations(None)) == []


def test_the_agent_path_stops_the_same_way_and_the_case_stays_on_its_key(agent_pp, hook):  # noqa: F811
    settler, capture, payout, _ = agent_pp
    webhook, _ = hook
    capture_id = f"CAP{uuid.uuid4().hex[:10].upper()}"
    capture.return_value = {**AGENT_CAPTURED, "body": {"id": capture_id, "status": "COMPLETED"}}
    case_id = cleared()
    arrives_before_the_capture(webhook, "PAYMENT.CAPTURE.REFUNDED", {"id": f"REF-{capture_id}", "links": up(capture_id)})
    result = run(settler.settle(agent_decision(case_id)))
    assert result["status"] == "failed"
    assert "the money is captured" in result["reason"]
    assert "handed_to_person" not in result
    assert state_of(case_id) == "AUTO_CLEARED"
    payout.assert_not_called()
