"""
The settler and dual approval (settler.py with dual_approval.py).

Defence in depth: execution already refuses a large release without its second approver, but
the settler is the code that moves money, so it checks the approval the decision names. A
release whose approval needs a second approver and has none is held and PayPal is not asked
to capture anything; one that has it is paid; and a case with no such approval on record
(a release made before dual approval existed) is judged by the checks that follow.
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import SHIPMENT, decision, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store  # noqa: E402


def case_with(approvals):
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    case = {"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": dict(SHIPMENT)}
    if approvals is not None:
        case["verification"] = {"approvals": approvals}
    run(get_store().put_case(case))
    return case_id


def test_a_release_still_waiting_for_its_second_approver_is_held(pp):  # noqa: F811
    settler, capture, payout, void = pp
    case_id = case_with([{"approval_id": "apr-1", "requires_second": True}])
    result = run(settler.settle(decision(case_id)))
    assert result["status"] == "held"
    assert "second approver" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_release_with_its_second_approval_is_paid(pp):  # noqa: F811
    settler, capture, payout, void = pp
    case_id = case_with([{
        "approval_id": "apr-1", "requires_second": True,
        "second_approval": {"approver": "second@forwarder.example"},
    }])
    result = run(settler.settle(decision(case_id)))
    assert result["status"] == "settled"
    capture.assert_awaited_once()
    payout.assert_awaited_once()


def test_a_case_with_no_such_approval_on_record_is_judged_by_the_other_checks(pp):  # noqa: F811
    settler, capture, payout, void = pp
    assert run(settler.settle(decision(case_with(None))))["status"] == "settled"
    assert run(settler.settle(decision(case_with([{"approval_id": "other", "requires_second": True}]))))["status"] == "settled"
