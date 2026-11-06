"""
The two stores must answer alike. Every scenario here runs on MemoryStore and on FirestoreStore (against
the emulator, skipped without it) and asserts the same thing, so a query that Firestore filters, orders or
counts differently from the in-memory version fails here instead of in production. The older tests cover
each store on its own; this file is the line between them.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vf_logistics import payment_controls  # noqa: E402
from vf_logistics import store as store_mod  # noqa: E402

EMULATOR = os.getenv("FIRESTORE_EMULATOR_HOST", "")
PROJECT = "vf-store-parity"


def run(coro):
    return asyncio.run(coro)


def wipe():
    request = urllib.request.Request(
        f"http://{EMULATOR}/emulator/v1/projects/{PROJECT}/databases/(default)/documents", method="DELETE",
    )
    urllib.request.urlopen(request, timeout=10).close()  # noqa: S310 - local emulator


@pytest.fixture(params=["memory", "firestore"])
def store(request, monkeypatch):
    monkeypatch.setenv("MULTI_TENANT", "false")
    if request.param == "memory":
        return store_mod.MemoryStore()
    if not EMULATOR:
        pytest.skip("FIRESTORE_EMULATOR_HOST is not set")
    wipe()
    return store_mod.FirestoreStore(PROJECT)


def at(n: int) -> str:
    return (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=n)).isoformat()


def case(case_id, n, state="HELD_FOR_REVIEW", **extra):
    return {"case_id": case_id, "shipment_id": case_id, "state": state, "created_at": at(n), "updated_at": at(n), **extra}


def put(store, *cases):
    for c in cases:
        run(store.put_case(c))


class TestQueriesOverCases:
    def test_a_page_comes_newest_first_and_the_cursor_continues_it(self, store):
        put(store, *(case(f"C{i}", i) for i in range(5)))
        first, cursor = run(store.query_cases(limit=2))
        assert [c["case_id"] for c in first] == ["C4", "C3"]
        assert cursor == at(3)
        second, cursor = run(store.query_cases(cursor=cursor, limit=2))
        assert [c["case_id"] for c in second] == ["C2", "C1"]
        last, cursor = run(store.query_cases(cursor=cursor, limit=2))
        assert [c["case_id"] for c in last] == ["C0"]
        assert cursor is None

    def test_a_state_filter_keeps_only_those_states(self, store):
        put(store, case("A", 1, "AUTO_CLEARED"), case("B", 2, "ESCALATED"), case("C", 3, "HELD_FOR_REVIEW"))
        page, _ = run(store.query_cases(states=("ESCALATED", "HELD_FOR_REVIEW")))
        assert sorted(c["case_id"] for c in page) == ["B", "C"]

    def test_list_cases_is_newest_first_and_bounded(self, store):
        put(store, *(case(f"C{i}", i) for i in range(4)))
        assert [c["case_id"] for c in run(store.list_cases(limit=3))] == ["C3", "C2", "C1"]

    def test_the_newest_case_with_a_client_reference_is_found(self, store):
        put(store, case("OLD", 1, client_reference="ref-1"), case("NEW", 2, client_reference="ref-1"), case("OTHER", 3, client_reference="ref-2"))
        assert run(store.find_case_by_client_reference("ref-1"))["case_id"] == "NEW"
        assert run(store.find_case_by_client_reference("nope")) is None
        assert run(store.find_case_by_client_reference("")) is None

    def test_counts_by_state_include_states_with_no_cases(self, store):
        put(store, case("A", 1, "ESCALATED"), case("B", 2, "ESCALATED"), case("C", 3, "AUTO_CLEARED"))
        assert run(store.count_by_state(("ESCALATED", "AUTO_CLEARED", "DEAD_LETTER"))) == {
            "ESCALATED": 2, "AUTO_CLEARED": 1, "DEAD_LETTER": 0,
        }

    def test_auto_cleared_cases_are_counted_by_who_cleared_them(self, store):
        put(store, case("A", 1, "AUTO_CLEARED", cleared_by="rules"), case("B", 2, "AUTO_CLEARED", cleared_by="rules"),
            case("C", 3, "AUTO_CLEARED", cleared_by="ai"), case("D", 4, "AUTO_CLEARED"), case("E", 5, "ESCALATED", cleared_by="ai"))
        assert run(store.count_by_cleared_by()) == {"rules": 2, "ai": 1, "unknown": 1, "total_auto_cleared": 4}


class TestRollups:
    ROLL = {"_agent_calls": 2, "_input_tokens": 100, "_output_tokens": 50, "_estimated_cost_usd": 0.5,
            "_sum_latency_ms": 400, "_tavily_searches": 3, "_tavily_cached": 1, "_tavily_billable": 2}

    def test_totals_add_up_over_every_case(self, store):
        put(store, case("A", 1, **self.ROLL), case("B", 2, **self.ROLL))
        total = run(store.sum_rollups())
        assert total["agent_calls"] == 4
        assert (total["total_input_tokens"], total["total_output_tokens"]) == (200, 100)
        assert total["estimated_cost_usd"] == 1.0
        assert total["avg_latency_ms"] == 200
        assert (total["tavily_searches"], total["tavily_cached"], total["tavily_billable"]) == (6, 2, 4)

    def test_a_window_is_half_open_on_creation_time(self, store):
        put(store, case("A", 1, **self.ROLL), case("B", 2, **self.ROLL), case("C", 3, **self.ROLL))
        assert run(store.sum_rollups(since=at(2), until=at(3)))["agent_calls"] == 2
        assert run(store.sum_rollups(since=at(1)))["agent_calls"] == 6
        assert run(store.sum_rollups(until=at(1)))["agent_calls"] == 0

    def test_a_store_with_no_cases_totals_zero_without_dividing_by_it(self, store):
        total = run(store.sum_rollups())
        assert (total["agent_calls"], total["avg_latency_ms"], total["estimated_cost_usd"]) == (0, 0, 0)


class TestClaimingWork:
    def test_the_oldest_ready_case_is_claimed_once(self, store):
        put(store, case("NEW", 2, "INGESTED"), case("OLD", 1, "INGESTED"), case("DONE", 0, "AUTO_CLEARED"))
        first = run(store.claim_next_pending(("INGESTED",)))
        assert first["case_id"] == "OLD" and first["claimed"] is True
        second = run(store.claim_next_pending(("INGESTED",)))
        assert second["case_id"] == "NEW"
        assert run(store.claim_next_pending(("INGESTED",))) is None

    def test_a_released_case_can_be_claimed_again(self, store):
        put(store, case("A", 1, "INGESTED"))
        assert run(store.claim_next_pending(("INGESTED",)))["case_id"] == "A"
        run(store.release_case("A"))
        assert run(store.claim_next_pending(("INGESTED",)))["case_id"] == "A"

    def test_releasing_a_case_that_is_gone_is_not_an_error(self, store):
        # A board reset can delete a case a worker still holds; letting go of it must not crash the worker.
        assert run(store.release_case("NOPE")) is None

    def test_a_case_still_backing_off_is_not_ready(self, store):
        put(store, case("A", 1, "INGESTED", not_before=at(10**6)))
        assert run(store.claim_next_pending(("INGESTED",))) is None

    def test_a_claim_whose_lease_ran_out_is_taken_over(self, store):
        stale = (datetime.now(timezone.utc) - timedelta(seconds=store_mod.CLAIM_LEASE_SECONDS + 60)).isoformat()
        put(store, case("A", 1, "INGESTED", claimed=True, claimed_at=stale))
        assert run(store.claim_next_pending(("INGESTED",)))["case_id"] == "A"

    def test_a_claim_with_no_readable_time_counts_as_expired(self, store):
        put(store, case("A", 1, "INGESTED", claimed=True, claimed_at="not a time"))
        assert run(store.claim_next_pending(("INGESTED",)))["case_id"] == "A"


class TestReleasingAcrossTenants:
    """Letting go of a claim is a write: under multi-tenancy it needs a tenant and cannot reach another's case."""

    def claimed(self, store, monkeypatch):
        monkeypatch.setenv("MULTI_TENANT", "true")
        run(store.put_case(case("A", 1, "INGESTED"), tenant_id="acme"))
        assert run(store.claim_next_pending(("INGESTED",), tenant_id="acme"))["claimed"] is True

    def test_a_release_with_no_tenant_is_refused_and_the_case_stays_claimed(self, store, monkeypatch):
        from vf_logistics import tenant

        self.claimed(store, monkeypatch)
        with pytest.raises(tenant.TenantError):
            run(store.release_case("A"))
        assert run(store.get_case("A", tenant_id="acme"))["claimed"] is True

    def test_another_tenant_cannot_release_it(self, store, monkeypatch):
        self.claimed(store, monkeypatch)
        run(store.release_case("A", tenant_id="other"))
        assert run(store.get_case("A", tenant_id="acme"))["claimed"] is True

    def test_its_own_tenant_can(self, store, monkeypatch):
        self.claimed(store, monkeypatch)
        run(store.release_case("A", tenant_id="acme"))
        assert run(store.get_case("A", tenant_id="acme"))["claimed"] is False


class TestEventsAndAudit:
    def test_events_page_newest_first(self, store):
        for n in range(5):
            run(store.add_event({"event_id": f"E{n}", "at": at(n)}))
        assert [e["event_id"] for e in run(store.list_events(limit=3))] == ["E4", "E3", "E2"]
        page, cursor = run(store.query_events(limit=2))
        assert [e["event_id"] for e in page] == ["E4", "E3"] and cursor == at(3)
        rest, cursor = run(store.query_events(cursor=cursor, limit=10))
        assert [e["event_id"] for e in rest] == ["E2", "E1", "E0"] and cursor is None

    def test_audit_filters_are_exact_and_the_cursor_continues(self, store):
        rows = [("C1", "release_shipment", "done"), ("C1", "publish_decision", "done"), ("C2", "publish_decision", "failed"),
                ("C1", "publish_decision", "unknown")]
        for n, (case_id, action, status) in enumerate(rows):
            run(store.add_audit({"case_id": case_id, "action": action, "status": status, "at": at(n)}))
        by_case, _ = run(store.query_audit(case_id="C1"))
        assert [a["status"] for a in by_case] == ["unknown", "done", "done"]
        by_action, _ = run(store.query_audit(action="publish_decision"))
        assert len(by_action) == 3
        by_status, _ = run(store.query_audit(status="failed"))
        assert [a["case_id"] for a in by_status] == ["C2"]
        page, cursor = run(store.query_audit(limit=2))
        assert len(page) == 2 and cursor == page[-1]["at"]
        assert len(run(store.query_audit(cursor=cursor, limit=10))[0]) == 2

    def test_audit_filters_combine_and_read_past_the_rows_that_do_not_match(self, store, monkeypatch):
        monkeypatch.setattr(store_mod, "_AUDIT_SCAN_PAGE", 2)
        for n in range(3):
            run(store.add_audit({"case_id": "C1", "action": "settle_payment", "status": "settled", "at": at(n)}))
        for n in range(3, 8):
            run(store.add_audit({"case_id": "C1", "action": "settle_payment", "status": "failed", "at": at(n)}))
        run(store.add_audit({"case_id": "C2", "action": "settle_payment", "status": "settled", "at": at(8)}))
        run(store.add_audit({"case_id": "C1", "action": "retry_settlement", "status": "requested", "at": at(9)}))
        page, cursor = run(store.query_audit(action="settle_payment", status="settled", limit=2))
        assert [a["at"] for a in page] == [at(8), at(2)]
        rest, cursor = run(store.query_audit(action="settle_payment", status="settled", cursor=cursor, limit=5))
        assert [a["at"] for a in rest] == [at(1), at(0)] and cursor is None
        both, _ = run(store.query_audit(case_id="C1", action="settle_payment", limit=100))
        assert len(both) == 8
        all_three, _ = run(store.query_audit(case_id="C1", action="settle_payment", status="failed", limit=100))
        assert len(all_three) == 5

    def test_a_reservation_whose_payment_failed_stays_open_and_a_paid_one_closes(self, store, monkeypatch):
        monkeypatch.setattr(payment_controls, "get_store", lambda: store)
        now = store_mod.utcnow()
        for key in ("k1", "k2"):
            run(store.add_audit({
                "audit_id": f"res-{key}", "case_id": "C1", "action": payment_controls.RESERVED, "status": "reserved",
                "detail": {"idempotency_key": key}, "at": now,
            }))
        run(store.add_audit({"case_id": "C1", "action": "settle_payment", "status": "failed", "detail": {"idempotency_key": "k1"}, "at": now}))
        run(store.add_audit({"case_id": "C1", "action": "settle_payment", "status": "settled", "detail": {"idempotency_key": "k2"}, "at": now}))
        still_open = run(payment_controls._open_reservations(None))
        assert [row["detail"]["idempotency_key"] for row in still_open] == ["k1"]

    def test_an_audit_row_with_a_fixed_id_is_written_once(self, store):
        row = {"audit_id": "audit-publish-1", "case_id": "C1", "action": "publish_decision", "status": "done", "at": at(1)}
        assert run(store.add_audit_once(dict(row))) is True
        assert run(store.add_audit_once({**row, "detail": {"second": True}})) is False
        rows, _ = run(store.query_audit(case_id="C1"))
        assert len(rows) == 1 and "detail" not in rows[0]

    def test_a_fixed_id_audit_row_needs_its_id(self, store):
        with pytest.raises(ValueError):
            run(store.add_audit_once({"case_id": "C1", "action": "publish_decision"}))

    def test_the_audit_trail_cannot_be_edited_or_deleted(self, store):
        run(store.add_audit({"audit_id": "A1", "case_id": "C", "action": "x", "at": at(1)}))
        with pytest.raises(store_mod.AuditImmutabilityError):
            run(store.update_audit("A1", {"action": "y"}))
        with pytest.raises(store_mod.AuditImmutabilityError):
            run(store.delete_audit("A1"))
        assert [a["audit_id"] for a in run(store.list_audit(limit=5))] == ["A1"]


class TestResetAndSmallRecords:
    def test_a_reset_clears_cases_and_events_and_keeps_the_audit_trail(self, store):
        put(store, case("A", 1), case("B", 2))
        run(store.add_event({"event_id": "E", "at": at(1)}))
        run(store.add_audit({"audit_id": "A1", "case_id": "A", "action": "x", "at": at(1)}))
        assert run(store.reset()) >= 2
        assert run(store.list_cases()) == []
        assert run(store.list_events()) == []
        assert len(run(store.list_audit(limit=5))) == 1

    def test_prefilter_rules_are_one_document_per_tenant(self, store):
        assert run(store.get_prefilter_rules()) is None
        run(store.put_prefilter_rules({"whitelist": ["acme"]}))
        run(store.put_prefilter_rules({"whitelist": ["acme", "beta"]}))
        assert run(store.get_prefilter_rules())["whitelist"] == ["acme", "beta"]

    def test_operator_time_adds_up_per_person_and_case(self, store):
        run(store.add_activity("C1", "a@x.test", 1000))
        run(store.add_activity("C1", "a@x.test", 500))
        run(store.add_activity("C1", "b@x.test", 200))
        rows = {(r["case_id"], r["actor"]): r["active_ms"] for r in run(store.list_activity())}
        assert rows == {("C1", "a@x.test"): 1500, ("C1", "b@x.test"): 200}

    def test_rollups_of_current_cases_need_no_backfill(self, store):
        put(store, case("A", 1, _agent_calls=1))
        result = run(store.backfill_rollups())
        assert result["updated"] == 0
