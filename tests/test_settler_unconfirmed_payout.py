"""
A payout's reservation stays until PayPal's answer proves nothing went out (follow-up review F01, 2026-11-07).

PayPal accepted a payout, the record of its batch failed, the run ended in retry and the reservation was given up, so a second payout
passed the daily limit while the first was uncounted. Pinned here: while the result is unknown the room stays reserved and the next
payout is refused; the same key is reconciled and counted once; and only a refused first request or a batch PayPal shows as dead, or
a run that never asked PayPal, gives the room back.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_payout_batch_kept import days_ago, kept, started  # noqa: E402
from test_payout_recovery import DUPLICATE, batch  # noqa: E402
from test_settler_controls import ADMIN, LIMITS, carrier, decision, mod, pp, run  # noqa: E402, F401
from vf_logistics.store import get_store, utcnow  # noqa: E402

ROOM_FOR_ONE = {**LIMITS, "carrier_daily_usd": 2000, "tenant_daily_usd": 5000, "carrier_daily_count": 5}
TIGHTER = {**ROOM_FOR_ONE, "max_payout_usd": 1000, "carrier_daily_usd": 1000}
DOWN = {"status": "transport_error", "http_status": None, "body": None, "error": "ConnectError"}
UNAVAILABLE = {"status": "http_error", "http_status": 503, "body": {}, "error": "503"}
SLOW_DOWN = {"status": "http_error", "http_status": 429, "body": {}, "error": "429"}
UNREADABLE = {"status": "bad_response", "http_status": 200, "body": None, "error": "not json"}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "INSUFFICIENT_FUNDS"}, "error": "refused"}


def publish(limits=ROOM_FOR_ONE):
    run(mod("payment_controls").publish_limits(limits, "room for one payout", ADMIN, "password"))


def still_reserved():
    return [row["detail"]["idempotency_key"] for row in run(mod("payment_controls")._open_reservations(None))]


def lost_batch_record(settler):
    return patch.object(settler, "_keep_batch", AsyncMock(side_effect=RuntimeError("the store is down")))


def settled_rows(d):
    rows, _ = run(get_store().query_audit(case_id=d["case_id"], action="settle_payment", limit=10))
    return [row for row in rows if row.get("status") == "settled"]


def test_a_payout_paypal_took_while_its_record_failed_holds_its_room_and_is_counted_once(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    name = carrier()
    first = decision(name)
    key = first["idempotency_key"]
    with lost_batch_record(settler):
        assert run(settler.settle(first))["status"] == "retry"
    payout.assert_awaited_once()
    assert still_reserved() == [key]

    capture.reset_mock()
    payout.reset_mock()
    second = decision(name)
    refused = run(settler.settle(second))
    assert refused["status"] == "retry"
    assert "daily limit of 2000.0 USD" in refused["reason"]
    capture.assert_not_called()
    payout.assert_not_called()

    payout.return_value = DUPLICATE
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(key)))
    done = run(settler.settle(first))
    assert done["status"] == "settled"
    assert done["recovered_from"] == "duplicate_refusal"
    assert len(settled_rows(first)) == 1
    assert still_reserved() == []
    used = run(mod("payment_controls").usage_today())
    assert used["tenant_usd"] == Decimal("1250")

    payout.reset_mock()
    assert run(settler.settle(first))["status"] == "settled"
    payout.assert_not_called()
    assert len(settled_rows(first)) == 1
    assert run(settler.settle(second))["status"] == "retry"
    payout.assert_not_called()


@pytest.mark.parametrize("answer", [DOWN, UNAVAILABLE, SLOW_DOWN, UNREADABLE], ids=["timeout", "server-error", "rate-limit", "unreadable"])
def test_an_answer_that_proves_nothing_holds_the_room_and_refuses_the_next_payout(pp, answer):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    name = carrier()
    first = decision(name)
    payout.return_value = answer
    assert run(settler.settle(first))["status"] == "retry"
    assert still_reserved() == [first["idempotency_key"]]
    payout.reset_mock()
    refused = run(settler.settle(decision(name)))
    assert refused["status"] == "retry"
    assert "daily limit of 2000.0 USD" in refused["reason"]
    payout.assert_not_called()


@pytest.mark.parametrize("read", [lambda key: DOWN, lambda key: batch(key, status="SOMETHING_NEW")], ids=["unreadable", "unknown-state"])
def test_a_duplicate_refusal_that_cannot_be_checked_holds_the_room(pp, monkeypatch, read):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    payout.return_value = DUPLICATE
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=read(d["idempotency_key"])))
    assert run(settler.settle(d))["status"] == "failed"
    assert still_reserved() == [d["idempotency_key"]]


def test_a_refusal_that_speaks_of_a_repeated_key_without_a_link_holds_the_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    payout.return_value = {**DUPLICATE, "body": {"name": "USER_BUSINESS_ERROR", "message": "already exists"}}
    assert run(settler.settle(d))["status"] == "failed"
    assert still_reserved() == [d["idempotency_key"]]


def test_a_refusal_of_a_repeat_proves_nothing_because_the_first_request_may_stand(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    payout.return_value = DOWN
    assert run(settler.settle(d))["status"] == "retry"
    payout.return_value = REFUSED
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "proved_unpaid" not in result
    assert still_reserved() == [d["idempotency_key"]]


def test_a_later_delivery_stopped_by_a_freeze_holds_the_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    name = carrier()
    d = decision(name)
    payout.return_value = DOWN
    assert run(settler.settle(d))["status"] == "retry"
    run(mod("payment_freeze").freeze("carrier", name, "a dispute was opened", "event-1"))
    payout.reset_mock()
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert "the money is captured" in result["reason"]
    payout.assert_not_called()
    assert still_reserved() == [d["idempotency_key"]]


def test_a_later_delivery_stopped_by_the_limits_holds_the_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    payout.return_value = DOWN
    assert run(settler.settle(d))["status"] == "retry"
    publish(TIGHTER)
    result = run(settler.settle(d))
    assert result["status"] == "held"
    assert "per-payout limit" in result["reason"]
    assert still_reserved() == [d["idempotency_key"]]


def test_an_attempt_too_old_to_repeat_holds_the_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    started(d["idempotency_key"], days_ago(40))
    assert run(settler.settle(d))["status"] == "held"
    assert still_reserved() == [d["idempotency_key"]]


def test_a_kept_batch_that_cannot_be_read_holds_the_room(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    kept(d["idempotency_key"])
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=DOWN))
    assert run(settler.settle(d))["status"] == "retry"
    assert still_reserved() == [d["idempotency_key"]]


def test_an_attempt_on_the_replacement_batch_holds_the_room(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    key = d["idempotency_key"]
    run(get_store().add_audit({
        "audit_id": f"payatt-{key}-payout-2", "case_id": f"PAYBATCH-{key}-payout-2", "action": "settle_payout_started",
        "status": "done", "detail": {}, "at": utcnow(),
    }))
    capture.side_effect = RuntimeError("the client crashed")
    assert run(settler.settle(d))["status"] == "retry"
    assert still_reserved() == [key]


def test_a_reservation_stays_when_the_attempt_rows_cannot_be_read(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    store = get_store()
    real = store.query_audit

    async def broken(*args, **kwargs):
        if str(kwargs.get("case_id") or "").startswith("PAYBATCH-"):
            raise RuntimeError("the store is down")
        return await real(*args, **kwargs)

    monkeypatch.setattr(store, "query_audit", broken)
    assert run(settler.settle(d))["status"] == "retry"
    payout.assert_not_called()
    assert still_reserved() == [d["idempotency_key"]]


def test_a_first_request_paypal_refused_outright_gives_the_room_back(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    payout.return_value = REFUSED
    result = run(settler.settle(decision(carrier())))
    assert result["status"] == "failed"
    assert result["proved_unpaid"] is True
    assert still_reserved() == []


def test_a_batch_paypal_shows_as_denied_gives_the_room_back(pp, monkeypatch):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    d = decision(carrier())
    key = d["idempotency_key"]
    with lost_batch_record(settler):
        assert run(settler.settle(d))["status"] == "retry"
    assert still_reserved() == [key]
    payout.return_value = DUPLICATE
    monkeypatch.setattr(settler.paypal_client, "get_payout_batch", AsyncMock(return_value=batch(key, status="DENIED")))
    result = run(settler.settle(d))
    assert result["status"] == "failed"
    assert result["dead_batch"] == "BATCH-DUP"
    assert still_reserved() == []


def test_a_run_that_never_asked_paypal_for_a_payout_gives_the_room_back(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    capture.return_value = REFUSED
    assert run(settler.settle(decision(carrier())))["status"] == "failed"
    payout.assert_not_called()
    assert still_reserved() == []


def test_an_error_before_any_payout_request_gives_the_room_back(pp):  # noqa: F811
    settler, capture, payout, _ = pp
    publish()
    capture.side_effect = RuntimeError("the client crashed")
    assert run(settler.settle(decision(carrier())))["status"] == "retry"
    assert still_reserved() == []
