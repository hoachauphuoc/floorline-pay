"""
The settler applies the PayPal events that were waiting for its index rows (review F05).

An event that arrived before the capture and payout index rows existed is kept as waiting. Right after the settler writes those
rows it asks paypal_webhook.reconcile to apply them. Pinned here: reconcile is asked once per paid release, after the capture
index row is stored; and a reconcile that fails changes nothing about the settlement.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import decision, new_case, pp, run  # noqa: E402, F401


def test_the_settler_applies_waiting_events_after_the_index_rows_are_stored(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    seen = {}

    async def reconcile(tenant_id=None, apply=True):
        rows, _ = await settler.get_store().query_audit(case_id="CAPTURE-CAP-1", limit=10, tenant_id=tenant_id)
        seen["index_rows"] = len(rows)
        return {"applied": 0, "pending": 0}

    asked = AsyncMock(side_effect=reconcile)
    monkeypatch.setattr(settler.paypal_webhook, "reconcile", asked)
    assert run(settler.settle(decision(new_case())))["status"] == "settled"
    asked.assert_awaited_once()
    assert seen["index_rows"] == 1


def test_a_reconcile_that_fails_does_not_change_the_settlement(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, void = pp
    asked = AsyncMock(side_effect=RuntimeError("PayPal lookup down"))
    monkeypatch.setattr(settler.paypal_webhook, "reconcile", asked)
    result = run(settler.settle(decision(new_case())))
    assert result["status"] == "settled"
    asked.assert_awaited_once()
