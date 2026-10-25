"""
Settler gaps found by the API review of 25 Oct 2026 (settler.py), one class per finding.

1. PayPal captures everything a booking holds. A booking holding more than the freight price on the case took the
   difference from the shipper while the carrier was paid the payout; it is now held before PayPal is asked.
2. The second-approver check read only the flag stored on the approval, and a missing or false flag meant "not needed".
   The limit is checked again at settlement, so a large payout never leaves with one approver.
3. PayPal refusing the token, or answering 429, was final: the decision was acknowledged and, for a block, the void never
   ran again. Both are now retried, and the receiver answers 503 so Pub/Sub delivers the decision again.
4. A redelivery after the capture went through renewed or refused a CAPTURED authorization once it was older than three
   days. A CAPTURED authorization is captured again (PayPal answers with the first capture) whatever its age.
5. A block voided the hold whatever state the case was in. Only a case that is BLOCKED_BY_HUMAN is voided now.
6. A store failure while writing the settlement row escaped settle(), which promises never to raise. It is now a retry.
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_decision_receiver import envelope  # noqa: E402
from test_settler import SHIPMENT, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics import decision_receiver  # noqa: E402
from vf_logistics.store import get_store  # noqa: E402

RATE_LIMITED = {"status": "http_error", "http_status": 429, "body": {"name": "RATE_LIMIT_REACHED"}, "error": "RATE_LIMIT_REACHED"}
TOKEN_REFUSED = {"status": "auth_failed", "http_status": 401, "body": None, "error": "token request refused"}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}


def booking(amount):
    return {"reference_id": "SHP-PAY-1", "amount": amount, "currency": "USD"}


def live(amount, state="CREATED"):
    return {"status": "ok", "http_status": 200, "error": None,
            "body": {"status": state, "amount": {"currency_code": "USD", "value": amount}}}


def large(approval):
    """A released case paying 3000 USD, above the default 2500 second-approval limit, with one stored approval."""
    shipment = {**SHIPMENT, "shipping_cost": 3000.0, "payout_amount": 3000.0}
    case = {"case_id": f"CASE-{uuid.uuid4().hex[:10]}", "state": "RELEASED_BY_HUMAN", "shipment": shipment,
            "verification": {"approvals": [approval]}}
    run(get_store().put_case(case))
    return decision(case["case_id"])


def blocked(state):
    return decision(new_case(state=state), outcome="BLOCKED_BY_HUMAN")


class TestTheCaptureNeverTakesMoreThanTheFreight:
    def test_a_booking_holding_more_than_the_freight_price_is_held(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        settler.payment_binding.find_authorization.return_value = booking("10000.00")
        settler.paypal_client.get_authorization.return_value = live("10000.00")
        shipment = {**SHIPMENT, "shipping_cost": 100.0, "payout_amount": 100.0}
        result = run(settler.settle(decision(new_case(shipment=shipment))))
        assert result["status"] == "held"
        assert "more than the freight price" in result["reason"]
        capture.assert_not_called()
        payout.assert_not_called()

    def test_a_booking_holding_the_freight_price_is_still_captured_and_paid(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(decision(new_case())))
        assert result["status"] == "settled"
        capture.assert_awaited_once()
        payout.assert_awaited_once()


class TestTheSecondApproverIsCheckedAgainstTheLimitNow:
    @pytest.fixture(autouse=True)
    def _covered(self, pp, monkeypatch):  # noqa: F811
        settler, *_ = pp
        monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
        settler.payment_binding.find_authorization.return_value = booking("3000.00")
        settler.paypal_client.get_authorization.return_value = live("3000.00")

    @pytest.mark.parametrize("approval", [
        {"approval_id": "apr-1"},
        {"approval_id": "apr-1", "requires_second": False},
    ])
    def test_a_large_release_whose_flag_is_missing_or_false_is_held(self, pp, approval):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large(approval)))
        assert result["status"] == "held"
        assert "needs a second approver" in result["reason"]
        capture.assert_not_called()
        payout.assert_not_called()

    def test_a_large_release_with_its_second_approver_is_paid(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large({"approval_id": "apr-1", "second_approval": {"by": "second@example.com"}})))
        assert result["status"] == "settled"


class TestATransientPayPalRefusalIsRetried:
    def test_a_rate_limited_void_is_retried(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        void.return_value = RATE_LIMITED
        assert run(settler.settle(blocked("BLOCKED_BY_HUMAN")))["status"] == "retry"

    def test_a_refused_token_at_capture_is_retried_and_nothing_is_paid(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        capture.return_value = TOKEN_REFUSED
        assert run(settler.settle(decision(new_case())))["status"] == "retry"
        payout.assert_not_called()

    def test_the_receiver_asks_for_a_rate_limited_block_again(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        void.return_value = RATE_LIMITED
        data = {"case_id": new_case(state="BLOCKED_BY_HUMAN"), "outcome": "BLOCKED_BY_HUMAN", "approval_id": "apr-1"}
        status, reply = run(decision_receiver.receive(envelope(key=f"exec-{uuid.uuid4().hex[:8]}", data=data)))
        assert status == 503
        assert reply["settlement"]["status"] == "retry"

    def test_a_final_refusal_still_fails(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        void.return_value = REFUSED
        assert run(settler.settle(blocked("BLOCKED_BY_HUMAN")))["status"] == "failed"


class TestACapturedAuthorizationIsNotRenewed:
    @pytest.mark.parametrize("stage", ["reauthorize", "expired"])
    def test_a_redelivery_after_the_capture_pays_whatever_its_age(self, pp, monkeypatch, stage):  # noqa: F811
        settler, capture, payout, void = pp
        monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: stage)
        settler.paypal_client.get_authorization.return_value = live("1300.00", state="CAPTURED")
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=REFUSED)) as renew:
            result = run(settler.settle(decision(new_case())))
        assert result["status"] == "settled"
        renew.assert_not_called()
        payout.assert_awaited_once()

    def test_an_authorization_not_yet_captured_is_still_renewed_after_the_honor_period(self, pp, monkeypatch):  # noqa: F811
        settler, capture, payout, void = pp
        monkeypatch.setattr(settler.authorization_lifecycle, "stage", lambda facts, now=None: "reauthorize")
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=REFUSED)) as renew:
            result = run(settler.settle(decision(new_case())))
        assert result["status"] == "failed"
        renew.assert_awaited_once()
        capture.assert_not_called()


class TestABlockVoidsOnlyABlockedCase:
    def test_a_block_on_a_released_case_voids_nothing(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(blocked("RELEASED_BY_HUMAN")))
        assert result["status"] == "failed"
        assert "not BLOCKED_BY_HUMAN" in result["reason"]
        assert "nothing is voided" in result["reason"]
        void.assert_not_called()

    def test_a_block_on_a_case_still_executing_is_retried(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(blocked("EXECUTING")))
        assert result["status"] == "retry"
        void.assert_not_called()

    def test_a_blocked_case_is_still_voided(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        assert run(settler.settle(blocked("BLOCKED_BY_HUMAN")))["status"] == "voided"
        void.assert_awaited_once()


class TestASettlementThatCannotBeRecordedIsRetried:
    def test_a_store_failure_on_the_settlement_row_is_a_retry(self, pp, monkeypatch):  # noqa: F811
        settler, capture, payout, void = pp
        store = get_store()
        real = store.add_audit_once

        async def failing(entry, tenant_id=None):
            if entry.get("action") == "settle_payment":
                raise RuntimeError("store unavailable")
            return await real(entry, tenant_id=tenant_id)

        monkeypatch.setattr(store, "add_audit_once", failing)
        result = run(settler.settle(decision(new_case())))
        assert result["status"] == "retry"
        assert "could not be recorded" in result["reason"]
