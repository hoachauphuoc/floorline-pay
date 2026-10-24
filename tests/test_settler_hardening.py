"""
Three gaps in the settler that an independent review (SOLIFAN, 24 Oct 2026) found by reading settler.py.

1. A renewed authorization has a new id. PayPal issues one when an authorization is reauthorized after its three-day honor
   period, and every later capture or void must use it. The settler checked the renewal's status and then captured the old id.
   Pinned here: the new id is recorded against the old one (payment_binding.record_renewal / find_renewal), PayPal is asked about
   the new id on a redelivery, the capture and the void go to the new id, a renewal that names no new id pays nothing, and the
   renewal is asked for once.
2. A block voided whatever authorization the shipment event named. A release is checked against the booking Floorline wrote; a
   block must be too, or a shipment event carrying someone else's authorization id could void their hold.
3. A release at or above the second-approval limit passed when the case held no record of the approval, because only a record
   that was found could say a second approver was missing. A missing record on a large release is now a refusal.

The modules are looked up inside each test so that, before the new functions exist, every test fails instead of the file
failing to collect.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import BOOKING, LIVE, OK, SHIPMENT, VOIDED, decision, new_case, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402


def binding():
    return importlib.import_module("vf_logistics.payment_binding")


def fresh():
    return f"AUTH{uuid.uuid4().hex[:12].upper()}"


def reply(body, status=201):
    return {"status": "ok", "http_status": status, "error": None, "body": body}


def live_for(auth_id, request_id):
    """What PayPal says about the authorization asked for: it carries its own id, so a test can tell old from new."""
    return reply({"id": auth_id, "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}, 200)


def booked(authorization):
    return decision(new_case(shipment={**SHIPMENT, "paypal_authorization_id": authorization}))


class TestTheRenewalRecord:
    def test_a_renewal_is_found_by_the_authorization_it_replaced(self):
        old, new = fresh(), fresh()
        assert run(binding().record_renewal(old, new)) is True
        assert run(binding().find_renewal(old)) == new

    def test_an_authorization_that_was_never_renewed_has_none(self):
        assert run(binding().find_renewal(fresh())) is None
        assert run(binding().find_renewal("")) is None

    def test_a_second_renewal_of_the_same_authorization_is_not_written_twice(self):
        old = fresh()
        assert run(binding().record_renewal(old, fresh())) is True
        assert run(binding().record_renewal(old, fresh())) is False


class TestTheRenewedAuthorization:
    @pytest.fixture(autouse=True)
    def _after_the_honor_period(self, monkeypatch, pp):  # noqa: F811
        settler, *_ = pp
        monkeypatch.setattr(settler.paypal_client, "get_authorization", AsyncMock(side_effect=live_for))
        monkeypatch.setattr(
            settler.authorization_lifecycle, "stage",
            lambda facts, now=None: "reauthorize" if str(facts.get("id")).startswith("AUTHOLD") else "honored",
        )

    def test_the_capture_uses_the_new_id_and_the_renewal_is_recorded(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        old, new = "AUTHOLD" + fresh(), fresh()
        d = booked(old)
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=reply({"id": new, "status": "CREATED"}))) as renew:
            result = run(settler.settle(d))
        assert result["status"] == "settled"
        renew.assert_awaited_once()
        capture.assert_awaited_once_with(new, f"{d['idempotency_key']}-capture")
        assert run(binding().find_renewal(old)) == new

    def test_a_redelivery_asks_paypal_about_the_new_id_and_does_not_renew_again(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        old, new = "AUTHOLD" + fresh(), fresh()
        d = booked(old)
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=reply({"id": new, "status": "CREATED"}))) as renew:
            run(settler.settle(d))
            run(settler.settle(d))
        renew.assert_awaited_once()
        asked = [call.args[0] for call in settler.paypal_client.get_authorization.await_args_list]
        assert asked[-1] == new
        assert capture.await_args_list[-1].args[0] == new

    def test_a_renewal_that_names_no_new_id_pays_nothing(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        old = "AUTHOLD" + fresh()
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=reply({"status": "CREATED"}))):
            result = run(settler.settle(booked(old)))
        assert result["status"] == "held"
        assert "did not name the new one" in result["reason"]
        capture.assert_not_called()
        payout.assert_not_called()
        assert run(binding().find_renewal(old)) is None

    def test_a_renewal_that_paypal_refuses_still_pays_nothing(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        refused = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}
        with patch.object(settler.paypal_client, "reauthorize_authorization", AsyncMock(return_value=refused)):
            result = run(settler.settle(booked("AUTHOLD" + fresh())))
        assert result["status"] == "failed"
        assert "could not renew" in result["reason"]
        capture.assert_not_called()


class TestTheBlockIsBoundToTheBooking:
    def _block(self, settler, authorization):
        d = decision(new_case(state="BLOCKED_BY_HUMAN", shipment={**SHIPMENT, "paypal_authorization_id": authorization}),
                     outcome="BLOCKED_BY_HUMAN")
        return d, run(settler.settle(d))

    def test_an_authorization_no_booking_holds_is_not_voided(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        settler.payment_binding.find_authorization.return_value = None
        _, result = self._block(settler, fresh())
        assert result["status"] == "held"
        assert "no booking made through Floorline holds this authorization" in result["reason"]
        void.assert_not_called()

    def test_an_authorization_booked_for_another_shipment_is_not_voided(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        settler.payment_binding.find_authorization.return_value = {**BOOKING, "reference_id": "SHP-SOMEONE-ELSE"}
        _, result = self._block(settler, fresh())
        assert result["status"] == "held"
        assert "different shipment" in result["reason"]
        void.assert_not_called()

    def test_this_shipments_own_authorization_is_voided(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        authorization = fresh()
        d, result = self._block(settler, authorization)
        assert result["status"] == "voided"
        void.assert_awaited_once_with(authorization, f"{d['idempotency_key']}-void")

    def test_a_renewed_authorization_is_voided_by_its_new_id(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        old, new = fresh(), fresh()
        run(binding().record_renewal(old, new))
        d, result = self._block(settler, old)
        assert result["status"] == "voided"
        void.assert_awaited_once_with(new, f"{d['idempotency_key']}-void")

    def test_a_case_with_no_authorization_still_skips(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        shipment = {k: v for k, v in SHIPMENT.items() if k != "paypal_authorization_id"}
        shipment["carrier_payee_email"] = SHIPMENT["carrier_payee_email"]
        d = decision(new_case(state="BLOCKED_BY_HUMAN", shipment=shipment), outcome="BLOCKED_BY_HUMAN")
        assert run(settler.settle(d))["status"] == "skipped"
        void.assert_not_called()


def large_case(approvals):
    """A released case whose payout is above the second-approval limit; `approvals` is the case's verification record or None."""
    shipment = {**SHIPMENT, "payout_amount": 3000.0, "shipping_cost": 3000.0, "paypal_authorization_id": fresh()}
    case = {"case_id": f"CASE-{uuid.uuid4().hex[:10]}", "state": "RELEASED_BY_HUMAN", "shipment": shipment}
    if approvals is not None:
        case["verification"] = {"approvals": approvals}
    run(get_store().put_case(case))
    return decision(case["case_id"])


class TestALargeReleaseNeedsItsApprovalRecord:
    def test_no_verification_record_at_all_is_a_refusal(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large_case(None)))
        assert result["status"] == "held"
        assert "no record of its approval" in result["reason"]
        capture.assert_not_called()

    def test_a_record_that_does_not_hold_this_approval_is_a_refusal(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large_case([{"approval_id": "apr-other", "requires_second": True, "second_approval": {"by": "b"}}])))
        assert result["status"] == "held"
        assert "no record of its approval" in result["reason"]
        capture.assert_not_called()

    def test_an_approval_still_missing_its_second_approver_is_held_for_it(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large_case([{"approval_id": "apr-1", "requires_second": True}])))
        assert result["status"] == "held"
        assert "needs a second approver" in result["reason"]
        capture.assert_not_called()

    def test_an_approval_with_its_second_approver_passes_this_check(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        result = run(settler.settle(large_case([{"approval_id": "apr-1", "requires_second": True, "second_approval": {"by": "b"}}])))
        assert "approval" not in result["reason"]

    def test_a_small_release_with_no_record_is_judged_by_the_other_checks_as_before(self, pp):  # noqa: F811
        settler, capture, payout, void = pp
        assert run(settler.settle(decision(new_case())))["status"] == "settled"
