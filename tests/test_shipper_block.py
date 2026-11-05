"""
A freeze on a shipper holds the release under whichever name the case gives it (settler.py, paypal_webhook.py, payment_freeze.py).

The booking form states the shipper as a company, other intake paths as a contact name. The settler and the webhook looked the
shipper up by shipper_name only, so a shipper frozen under its company name was paid again, and a case with no name at all was
treated as not frozen. Pinned here: a freeze on either name holds the release; a case that names no shipper is held, because
a freeze on it cannot be checked; the webhook freezes the shipper under every name the case gives; and after a dispute on a
case that names only a company, a release for another carrier of the same shipper is held and no payout is made.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_paypal_webhook import HEADERS, event, hook  # noqa: E402, F401
from test_settler import OK, SHIPMENT, decision, new_case, pp, run  # noqa: E402, F401


def freezes():
    return importlib.import_module("vf_logistics.payment_freeze")


def case_with(**names):
    """A payment case whose shipment gives the shipper only the names passed."""
    tag = uuid.uuid4().hex[:6]
    bare = {k: v for k, v in SHIPMENT.items() if k not in ("shipper_company", "shipper_name")}
    return new_case(shipment={**bare, "carrier_name": f"Mekong Haulage {tag} Co", **names})


def test_shipper_names_lists_every_name_the_case_gives_once_and_without_blanks():
    names = freezes().shipper_names
    assert names({"shipper_company": "Acme Ltd", "shipper_name": "J Smith"}) == ["Acme Ltd", "J Smith"]
    assert names({"shipper_company": " Acme Ltd ", "shipper_name": "Acme Ltd"}) == ["Acme Ltd"]
    assert names({"shipper_company": "  ", "shipper_name": None}) == []
    assert names({}) == []
    assert names(None) == []


def test_a_release_for_a_shipper_frozen_under_its_company_name_is_held(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    company = f"Saigon Plywood {uuid.uuid4().hex[:6]} Co"
    run(freezes().freeze("shipper", company, "a dispute was opened", "event-1"))
    result = run(settler.settle(decision(case_with(shipper_company=company))))
    assert result["status"] == "held"
    assert "shipper" in result["reason"] and "frozen" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_release_for_a_shipper_frozen_under_its_contact_name_is_still_held(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    contact = f"Tran Van {uuid.uuid4().hex[:6]}"
    run(freezes().freeze("shipper", contact, "a dispute was opened", "event-2"))
    result = run(settler.settle(decision(case_with(shipper_name=contact))))
    assert result["status"] == "held"
    assert "shipper" in result["reason"] and "frozen" in result["reason"]
    payout.assert_not_called()


@pytest.mark.parametrize("frozen_field", ["shipper_company", "shipper_name"])
def test_a_freeze_on_either_name_of_the_shipper_holds_the_release(pp, frozen_field):  # noqa: F811
    settler, capture, payout, _ = pp
    tag = uuid.uuid4().hex[:6]
    names = {"shipper_company": f"Saigon Plywood {tag} Co", "shipper_name": f"Tran Van {tag}"}
    run(freezes().freeze("shipper", names[frozen_field], "a dispute was opened", f"event-{frozen_field}"))
    result = run(settler.settle(decision(case_with(**names))))
    assert result["status"] == "held"
    assert "frozen" in result["reason"]
    payout.assert_not_called()


def test_a_case_that_names_no_shipper_is_held_because_no_freeze_can_be_checked(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    result = run(settler.settle(decision(case_with())))
    assert result["status"] == "held"
    assert "names no shipper" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_dispute_on_a_case_that_names_only_a_company_freezes_it_and_holds_its_next_release(pp, hook):  # noqa: F811
    settler, capture, payout, _ = pp
    webhook, _ = hook
    tag = uuid.uuid4().hex[:6]
    company = f"Saigon Plywood {tag} Co"
    capture.return_value = {**OK, "body": {"id": f"CAP-{tag}", "status": "COMPLETED"}}
    first = run(settler.settle(decision(case_with(shipper_company=company))))
    assert first["status"] == "settled"
    status, reply = run(webhook.receive(HEADERS, event("CUSTOMER.DISPUTE.CREATED", {
        "disputed_transactions": [{"seller_transaction_id": f"CAP-{tag}"}],
    })))
    assert status == 200
    assert "shipper" in reply["frozen"]
    second = run(settler.settle(decision(case_with(shipper_company=company))))
    assert second["status"] == "held"
    assert "shipper" in second["reason"] and "frozen" in second["reason"]
    payout.assert_awaited_once()
