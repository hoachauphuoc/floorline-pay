"""
Audit queries on the Firestore store with more than one filter.

Firestore takes one equality filter in a query ordered by time (the one the declared composite indexes cover), so
FirestoreStore.query_audit applied only the first of case_id, action and status and dropped the rest, where MemoryStore
applies all three. Pinned here on a fake of the query surface query_audit touches (where, order_by, start_after, limit,
stream): a combined filter returns only rows that match every field and reads on to fill the limit, a single filter asks
for no more rows than the limit, and the two payment checks that were wrong on Firestore, the daily limit and the retry of
a failed settlement, give the answer MemoryStore gives.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.api_core.exceptions import AlreadyExists

from vf_logistics import payment_controls, settlement_retry
from vf_logistics import store as store_mod
from vf_logistics.store import utcnow

ADMIN = "admin@forwarder.example"
LIMITS = {"max_payout_usd": 1250, "carrier_daily_usd": 2000, "tenant_daily_usd": 5000, "carrier_daily_count": 5}


def run(coro):
    return asyncio.run(coro)


def at(n: int) -> str:
    return (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=n)).isoformat()


class _Filter:
    def __init__(self, field, op, value):
        self.field, self.op, self.value = field, op, value


class _Doc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return copy.deepcopy(self._data)


class _Query:
    def __init__(self, db, filters=(), after=None, size=None):
        self._db, self._filters, self._after, self._size = db, tuple(filters), after, size

    def where(self, filter):  # noqa: A002 - the keyword the Firestore client uses
        return _Query(self._db, self._filters + (filter,), self._after, self._size)

    def order_by(self, field, direction=None):
        return self

    def start_after(self, values):
        return _Query(self._db, self._filters, values["at"], self._size)

    def limit(self, size):
        return _Query(self._db, self._filters, self._after, size)

    def stream(self):
        rows = [r for r in self._db.rows if all(r.get(f.field) == f.value for f in self._filters)]
        rows.sort(key=lambda r: r["at"], reverse=True)
        if self._after is not None:
            rows = [r for r in rows if r["at"] < self._after]
        self._db.requests.append(self._size)
        return iter([_Doc(r) for r in rows[: self._size]])


class _Ref:
    def __init__(self, db):
        self._db = db

    def set(self, data):
        self._db.rows.append(copy.deepcopy(data))

    def create(self, data):
        if any(r.get("audit_id") == data.get("audit_id") for r in self._db.rows):
            raise AlreadyExists("an audit row with this id exists")
        self._db.rows.append(copy.deepcopy(data))


class _Collection(_Query):
    def document(self, doc_id):
        return _Ref(self._db)


class _FakeFirestore:
    FieldFilter = _Filter
    Query = SimpleNamespace(DESCENDING="DESCENDING")

    def __init__(self):
        self.rows = []
        self.requests = []

    def collection(self, name):
        return _Collection(self)


@pytest.fixture
def fs(monkeypatch):
    monkeypatch.setenv("MULTI_TENANT", "false")
    fake = _FakeFirestore()
    store = store_mod.FirestoreStore.__new__(store_mod.FirestoreStore)  # skips the real client
    store._fs = fake
    store._db = fake
    store._audit = fake.collection("audit_log")
    monkeypatch.setattr(payment_controls, "get_store", lambda: store)
    monkeypatch.setattr(settlement_retry, "get_store", lambda: store)
    return store, fake


def audit(store, audit_id, case_id, action, status, n=None, **detail):
    run(store.add_audit({
        "audit_id": audit_id, "case_id": case_id, "action": action, "status": status,
        "detail": detail, "at": at(n) if n is not None else utcnow(),
    }))


def test_a_case_and_an_action_together_return_only_rows_with_both(fs):
    store, _ = fs
    audit(store, "a1", "C1", "release_shipment", "done", 1)
    audit(store, "a2", "C1", "publish_decision", "done", 2)
    audit(store, "a3", "C2", "publish_decision", "done", 3)
    rows, _ = run(store.query_audit(case_id="C1", action="publish_decision"))
    assert [r["audit_id"] for r in rows] == ["a2"]


def test_an_action_and_a_status_together_skip_rows_of_another_status(fs):
    store, _ = fs
    audit(store, "s1", "C1", "settle_payment", "settled", 1)
    audit(store, "f1", "C2", "settle_payment", "failed", 2)
    rows, _ = run(store.query_audit(action="settle_payment", status="settled"))
    assert [r["audit_id"] for r in rows] == ["s1"]


def test_the_limit_is_filled_from_pages_beyond_the_first_and_the_cursor_continues(fs):
    store, fake = fs
    for n in range(3):
        audit(store, f"s{n}", f"C{n}", "settle_payment", "settled", n)
    for n in range(450):
        audit(store, f"o{n}", "C9", "settle_payment", "failed", 10 + n)
    first, cursor = run(store.query_audit(action="settle_payment", status="settled", limit=2))
    assert [r["audit_id"] for r in first] == ["s2", "s1"]
    assert cursor == at(1)
    rest, cursor = run(store.query_audit(action="settle_payment", status="settled", limit=2, cursor=cursor))
    assert [r["audit_id"] for r in rest] == ["s0"]
    assert cursor is None
    assert len(fake.requests) > 2


def test_a_single_filter_asks_for_no_more_rows_than_the_limit(fs):
    store, fake = fs
    for n in range(5):
        audit(store, f"a{n}", "C1", "publish_decision", "done", n)
    rows, _ = run(store.query_audit(case_id="C1", limit=1))
    assert [r["audit_id"] for r in rows] == ["a4"]
    assert fake.requests == [1]


def test_a_reservation_still_counts_while_its_decision_has_only_a_failed_settlement(fs):
    store, _ = fs
    run(payment_controls.publish_limits(LIMITS, "room for one", ADMIN, "password"))
    assert run(payment_controls.reserve_payout("k1", Decimal("1250"), "mekong")) is None
    audit(store, "settle-k1-failed", "CASE-1", "settle_payment", "failed", idempotency_key="k1", outcome="RELEASED_BY_HUMAN")
    refusal = run(payment_controls.reserve_payout("k2", Decimal("1250"), "mekong"))
    assert refusal is not None and refusal[0] == "retry"
    assert "daily limit" in refusal[1]


def test_a_retry_finds_the_failed_settlement_under_a_newer_row_of_the_case(fs, monkeypatch):
    store, _ = fs
    send = AsyncMock(return_value={"status": "done", "detail": {"message_id": "m-1"}})
    monkeypatch.setattr(settlement_retry.tools, "publish_decision", send)
    audit(store, "settle-k1-failed", "CASE-1", "settle_payment", "failed", 1, idempotency_key="k1", outcome="RELEASED_BY_HUMAN")
    audit(store, "retry-1", "CASE-1", "retry_settlement", "requested", 2, idempotency_key="k1")
    monkeypatch.setattr(settlement_retry.tools, "_earlier_publish", AsyncMock(return_value={"detail": {"approver": "reviewer@forwarder.example"}}))
    result = run(settlement_retry.retry("CASE-1", "checked the PayPal activity", "ops@forwarder.example"))
    assert result == {"status": "redelivered", "case_id": "CASE-1"}
    send.assert_awaited_once()
