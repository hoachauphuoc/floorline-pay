"""
Module 2: tenant isolation.

What these tests are for
------------------------
Every other test in this suite can pass while tenant isolation is broken, because
isolation is a negative property: it is about what a caller CANNOT see. So these
tests are written from the attacker's side. Tenant A writes, tenant B reads, and
the assertion is that B gets nothing -- on every read path, not a representative
sample, because the one path nobody checked is the one that leaks.

Why get_case returns None rather than 403
-----------------------------------------
A distinct "exists, but is not yours" response confirms the id is real, which
turns the API into an oracle for enumerating another tenant's case ids. So a
foreign case is indistinguishable from a missing one. test_foreign_case_is_
indistinguishable_from_missing is the assertion that keeps it that way.

Why the signature parity test lives here
----------------------------------------
MemoryStore and FirestoreStore are duck-typed with no ABC, so nothing forces them
to agree. During this work MemoryStore gained tenant_id on sixteen methods while
FirestoreStore had none, and every call passing tenant_id would have worked in the
tests and crashed in production. The parity test makes that state unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import inspect
import json
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("STORE_BACKEND", "memory")

sys.path.insert(0, os.path.dirname(__file__))
from test_document_store import FakeGcs  # noqa: E402
from vf_logistics import store as store_mod  # noqa: E402
from vf_logistics import tenant  # noqa: E402
from vf_logistics import app as _app  # noqa: E402,F401
from vf_logistics.store import (  # noqa: E402
    TENANT_FIELD,
    FirestoreStore,
    MemoryStore,
    new_id,
    utcnow,
)

TENANT_A = "acme-freight"
TENANT_B = "globex-logistics"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _case(case_id: str, state: str = "AUTO_CLEARED", **extra) -> dict:
    return {
        "case_id": case_id,
        "shipment_id": f"SHIP-{case_id}",
        "state": state,
        "created_at": utcnow(),
        "steps": [],
        "actions": [],
        "cleared_by": "ai",
        "_agent_calls": 2,
        "_input_tokens": 1000,
        "_output_tokens": 500,
        "_estimated_cost_usd": 0.001,
        "_sum_latency_ms": 200,
        **extra,
    }


class StoreBackendParityTests(unittest.TestCase):
    def _public_signatures(self, cls) -> dict[str, list[str]]:
        return {
            name: list(inspect.signature(fn).parameters)
            for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

    def test_backends_expose_identical_signatures(self):
        """
        Duck-typed backends with no ABC. A method that gained tenant_id on one
        and not the other would pass every test and crash in production, because
        the tests run on memory and production runs on Firestore.
        """
        memory = self._public_signatures(MemoryStore)
        firestore = self._public_signatures(FirestoreStore)

        self.assertEqual(
            set(memory), set(firestore), "the two backends expose different methods",
        )
        for name in sorted(memory):
            self.assertEqual(
                memory[name], firestore[name],
                f"{name}() has different parameters on the two backends",
            )

    def test_every_store_method_accepts_a_tenant(self):
        for name, params in self._public_signatures(MemoryStore).items():
            self.assertIn(
                "tenant_id", params,
                f"{name}() cannot be scoped to a tenant, so it is a leak waiting "
                f"to happen",
            )


class TenantResolutionTests(unittest.TestCase):
    def test_absent_tenant_resolves_to_the_implicit_one_when_single_tenant(self):
        with patch.dict(os.environ, {"MULTI_TENANT": "false"}):
            self.assertEqual(tenant.resolve(None), tenant.SINGLE_TENANT_ID)

    def test_absent_tenant_raises_when_multi_tenant(self):
        """
        There is no default tenant. Resolving one would pool a customer's records
        into a bucket another customer can read.
        """
        with patch.dict(os.environ, {"MULTI_TENANT": "true"}):
            with self.assertRaises(tenant.TenantError):
                tenant.resolve(None)
            with self.assertRaises(tenant.TenantError):
                tenant.resolve("")
            with self.assertRaises(tenant.TenantError):
                tenant.resolve("   ")

    def test_require_tenant_never_falls_back(self):
        """Billing attribution must not silently invoice "default"."""
        with patch.dict(os.environ, {"MULTI_TENANT": "false"}):
            with self.assertRaises(tenant.TenantError):
                tenant.require_tenant(None)

    def test_path_dangerous_ids_are_rejected(self):
        """
        A tenant id lands in Firestore query keys. Anything needing escaping is
        rejected rather than escaped, because an id that needs escaping will be
        used unescaped somewhere eventually.
        """
        for bad in (
            "../other", "a/b", "tenant.with.dots", "has space", "UPPER/slash",
            "x", "", "-starts-with-hyphen", "a" * 64, "tenant\x00null",
        ):
            with self.assertRaises(tenant.TenantError, msg=f"{bad!r} was accepted"):
                tenant.validate(bad)

    def test_ids_are_case_insensitive(self):
        self.assertEqual(tenant.validate("Acme-Freight"), "acme-freight")

    def test_no_tenant_is_derived_from_an_email_address(self):
        """
        Finding T02: tenant_from_email() made acme.com and acme.net one tenant and
        yahoo.co.jp the tenant "yahoo". Membership is declared in the registry
        (test_membership.py); the helper is gone so nothing can call it again.
        """
        self.assertFalse(hasattr(tenant, "tenant_from_email"))

    def test_isolation_guard_refuses_the_dangerous_combination(self):
        """
        Multi-tenant on real data with no identity source -- no IAP and no console
        session secret -- cannot tell one organisation's caller from another's.
        Failing to boot is the only safe response.
        """
        with patch.dict(os.environ, {
            "MULTI_TENANT": "true", "STORE_BACKEND": "firestore",
            "IAP_ENABLED": "false", "VF_SESSION_SECRET": "",
        }):
            with self.assertRaises(tenant.TenantIsolationError):
                tenant.assert_isolation_is_enforceable()

    def test_isolation_guard_allows_the_safe_combinations(self):
        signed = "s" * 32
        safe = [
            {"MULTI_TENANT": "false", "STORE_BACKEND": "firestore", "IAP_ENABLED": "false",
             "VF_SESSION_SECRET": ""},
            {"MULTI_TENANT": "true", "STORE_BACKEND": "firestore", "IAP_ENABLED": "true",
             "VF_SESSION_SECRET": ""},
            {"MULTI_TENANT": "true", "STORE_BACKEND": "firestore", "IAP_ENABLED": "false",
             "VF_SESSION_SECRET": signed},
            {"MULTI_TENANT": "true", "STORE_BACKEND": "memory", "IAP_ENABLED": "false",
             "VF_SESSION_SECRET": ""},
        ]
        for env in safe:
            with patch.dict(os.environ, env):
                tenant.assert_isolation_is_enforceable()  # must not raise


class IsolationOnEveryReadPathTests(unittest.TestCase):
    """Tenant A writes. Tenant B must see nothing, on every path."""

    def setUp(self):
        self.store = MemoryStore()
        _run(self.store.put_case(_case("CASE-A1"), tenant_id=TENANT_A))
        _run(self.store.put_case(
            _case("CASE-A2", state="HELD_FOR_REVIEW", client_reference="REF-A"),
            tenant_id=TENANT_A,
        ))
        _run(self.store.add_event(
            {"event_id": new_id("ev"), "at": utcnow(), "kind": "a"},
            tenant_id=TENANT_A,
        ))
        _run(self.store.add_audit(
            {"audit_id": "AUD-A", "case_id": "CASE-A1", "action": "release",
             "status": "ok", "at": utcnow()},
            tenant_id=TENANT_A,
        ))
        _run(self.store.put_boundary(
            {"boundary_id": "B-A", "version": 1, "status": "ACTIVE"},
            tenant_id=TENANT_A,
        ))

    def test_get_case(self):
        self.assertIsNotNone(_run(self.store.get_case("CASE-A1", tenant_id=TENANT_A)))
        self.assertIsNone(_run(self.store.get_case("CASE-A1", tenant_id=TENANT_B)))

    def test_foreign_case_is_indistinguishable_from_missing(self):
        """
        Both return None. A different response for a real-but-foreign id would
        let a caller enumerate another tenant's case ids.
        """
        foreign = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_B))
        absent = _run(self.store.get_case("CASE-NEVER-EXISTED", tenant_id=TENANT_B))
        self.assertEqual(foreign, absent)

    def test_list_cases(self):
        self.assertEqual(len(_run(self.store.list_cases(tenant_id=TENANT_A))), 2)
        self.assertEqual(_run(self.store.list_cases(tenant_id=TENANT_B)), [])

    def test_query_cases(self):
        rows, _ = _run(self.store.query_cases(tenant_id=TENANT_A))
        self.assertEqual(len(rows), 2)
        rows, _ = _run(self.store.query_cases(tenant_id=TENANT_B))
        self.assertEqual(rows, [])

    def test_query_cases_with_a_state_filter(self):
        rows, _ = _run(self.store.query_cases(
            states=("HELD_FOR_REVIEW",), tenant_id=TENANT_B,
        ))
        self.assertEqual(rows, [], "the review queue must not cross tenants")

    def test_find_case_by_client_reference(self):
        """
        Idempotency keys are the integrator's own strings and will collide across
        customers. A collision must not return someone else's audit.
        """
        self.assertIsNotNone(_run(
            self.store.find_case_by_client_reference("REF-A", tenant_id=TENANT_A)
        ))
        self.assertIsNone(_run(
            self.store.find_case_by_client_reference("REF-A", tenant_id=TENANT_B)
        ))

    def test_count_by_state(self):
        counts = _run(self.store.count_by_state(
            ("AUTO_CLEARED", "HELD_FOR_REVIEW"), tenant_id=TENANT_B,
        ))
        self.assertEqual(sum(counts.values()), 0)

    def test_count_by_cleared_by(self):
        counts = _run(self.store.count_by_cleared_by(tenant_id=TENANT_B))
        self.assertEqual(counts["total_auto_cleared"], 0)

    def test_list_and_query_events(self):
        self.assertEqual(len(_run(self.store.list_events(tenant_id=TENANT_A))), 1)
        self.assertEqual(_run(self.store.list_events(tenant_id=TENANT_B)), [])
        rows, _ = _run(self.store.query_events(tenant_id=TENANT_B))
        self.assertEqual(rows, [])

    def test_list_and_query_audit(self):
        self.assertEqual(len(_run(self.store.list_audit(tenant_id=TENANT_A))), 1)
        self.assertEqual(_run(self.store.list_audit(tenant_id=TENANT_B)), [])
        rows, _ = _run(self.store.query_audit(
            case_id="CASE-A1", tenant_id=TENANT_B,
        ))
        self.assertEqual(rows, [], "an audit trail must not cross tenants")

    def test_boundaries(self):
        """
        Tenant-scoped because "Compliance Admin configures risk rules" is a
        per-customer role: one customer's delegated auto-release ceiling has no
        business governing another's shipments.
        """
        self.assertIsNotNone(_run(self.store.active_boundary(tenant_id=TENANT_A)))
        self.assertIsNone(
            _run(self.store.active_boundary(tenant_id=TENANT_B)),
            "tenant B inherits no authority from tenant A's boundary",
        )
        self.assertEqual(_run(self.store.list_boundaries(tenant_id=TENANT_B)), [])

    def test_no_active_boundary_means_fail_closed_for_the_new_tenant(self):
        """A new tenant starts with no delegated authority, not with someone
        else's."""
        self.assertIsNone(_run(self.store.active_boundary(tenant_id="brand-new")))


class CrossTenantWriteTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        _run(self.store.put_case(_case("CASE-A1"), tenant_id=TENANT_A))

    def test_put_case_refuses_to_rehome_an_existing_case(self):
        """
        A read-modify-write made in another tenant's request context would
        otherwise move the case across the boundary -- a write that silently
        steals a record instead of failing.
        """
        stolen = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        with self.assertRaises(tenant.TenantError):
            _run(self.store.put_case(
                stolen, expected_version=stolen["_version"], tenant_id=TENANT_B,
            ))
        still_a = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        self.assertIsNotNone(still_a, "the case must still belong to tenant A")

    def test_reset_clears_only_the_calling_tenant(self):
        """An unscoped wipe would let one customer clear another's board."""
        _run(self.store.put_case(_case("CASE-B1"), tenant_id=TENANT_B))
        removed = _run(self.store.reset(tenant_id=TENANT_B))
        self.assertEqual(removed, 1)
        self.assertIsNotNone(_run(self.store.get_case("CASE-A1", tenant_id=TENANT_A)))
        self.assertIsNone(_run(self.store.get_case("CASE-B1", tenant_id=TENANT_B)))

    def test_release_case_cannot_touch_another_tenants_case(self):
        _run(self.store.claim_next_pending(("AUTO_CLEARED",), tenant_id=TENANT_A))
        _run(self.store.release_case("CASE-A1", tenant_id=TENANT_B))
        case = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        self.assertTrue(
            case["claimed"], "tenant B must not interfere with tenant A's pipeline",
        )

    def test_every_write_is_stamped_with_an_owner(self):
        """An unowned document can never be returned by a scoped query, which
        would look like data loss rather than like the bug it is."""
        case = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        self.assertEqual(case[TENANT_FIELD], TENANT_A)


# --------------------------------------------------------------------------
# The same refusal on Firestore
# --------------------------------------------------------------------------
#
# Every store test above runs on MemoryStore, and FirestoreStore.put_case did
# not keep the contract they check: without an expected_version it was a blind
# ref.set(). MemoryStore refused tenant B's write over tenant A's case; Firestore
# replaced it. Case ids come from the shipment id a document states
# (CASE-<shipment_id>), so tenant B ingesting a shipment that reused A's id
# overwrote A's case in production while every test here passed.
#
# The emulator cannot run everywhere, so FirestoreStore is also run here against
# a fake of exactly the surface put_case, get_case and add_event touch:
# collection().document(), DocumentReference.get/set, Client.transaction(),
# Transaction.set and the @transactional decorator. Like Firestore, the fake
# commits a transaction's writes only when the wrapped function returns. It does
# not model locking, contention or retries -- that is what
# TestCaseOwnershipOnFirestore in test_firestore_emulator.py is for.


class _FakeSnapshot:
    def __init__(self, data: dict | None):
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return copy.deepcopy(self._data)


class _FakeDocument:
    def __init__(self, db: _FakeFirestore, path: tuple[str, str]):
        self._db = db
        self.path = path

    def get(self, transaction=None) -> _FakeSnapshot:
        self._db.log.append(("get", self.path, transaction is not None))
        return _FakeSnapshot(copy.deepcopy(self._db.docs.get(self.path)))

    def set(self, data: dict) -> None:
        # Outside any transaction: nothing is read first, so nothing is checked.
        self._db.log.append(("set", self.path, False))
        self._db.docs[self.path] = copy.deepcopy(data)


class _FakeCollection:
    def __init__(self, db: _FakeFirestore, name: str):
        self._db = db
        self._name = name

    def document(self, doc_id: str) -> _FakeDocument:
        return _FakeDocument(self._db, (self._name, doc_id))


class _FakeTransaction:
    def __init__(self) -> None:
        self.pending: list[tuple[tuple[str, str], dict]] = []

    def set(self, ref: _FakeDocument, data: dict) -> None:
        self.pending.append((ref.path, copy.deepcopy(data)))


class _FakeAborted(Exception):
    """What FirestoreStore treats as contention. Never raised by this fake."""


class _FakeFirestore:
    """Stands in for both the client (store._db) and the module (store._fs)."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.log: list[tuple[str, tuple[str, str], bool]] = []

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)

    def transaction(self, **_options) -> _FakeTransaction:
        return _FakeTransaction()

    def transactional(self, fn):
        def run(transaction: _FakeTransaction, *args, **kwargs):
            result = fn(transaction, *args, **kwargs)  # raising discards the writes
            for path, data in transaction.pending:
                self.log.append(("commit", path, True))
                self.docs[path] = data
            return result

        return run

    def writes_to(self, case_id: str) -> list[tuple[str, tuple[str, str], bool]]:
        return [
            entry for entry in self.log
            if entry[0] in ("set", "commit") and entry[1] == ("cases", case_id)
        ]


def _firestore_on_fake() -> tuple[FirestoreStore, _FakeFirestore]:
    fake = _FakeFirestore()
    store = FirestoreStore.__new__(FirestoreStore)  # skips the real client
    store._fs = fake
    store._db = fake
    store._aborted = _FakeAborted
    store._cases = fake.collection("cases")
    store._events = fake.collection("events")
    return store, fake


def _backends():
    """MemoryStore and FirestoreStore-on-the-fake, each with a way to plant a raw
    document the way an older release would have left it."""
    memory = MemoryStore()

    def plant_in_memory(doc: dict) -> None:
        memory._cases[doc["case_id"]] = doc

    firestore, fake = _firestore_on_fake()

    def plant_in_firestore(doc: dict) -> None:
        fake.docs[("cases", doc["case_id"])] = copy.deepcopy(doc)

    return [("memory", memory, plant_in_memory), ("firestore", firestore, plant_in_firestore)]


class CrossTenantOverwriteParityTests(unittest.TestCase):
    """put_case's ownership rule, held to one contract on both backends."""

    def test_neither_backend_lets_another_tenant_replace_a_case(self):
        for label, version in (("unversioned", None), ("versioned", 1)):
            for name, store, _ in _backends():
                _run(store.put_case(_case("CASE-SHP-1", shipper="Acme"), tenant_id=TENANT_A))
                with self.subTest(backend=name, write=label):
                    with self.assertRaises(tenant.TenantError):
                        _run(store.put_case(
                            _case("CASE-SHP-1", shipper="Globex"),
                            expected_version=version, tenant_id=TENANT_B,
                        ))
                    kept = _run(store.get_case("CASE-SHP-1", tenant_id=TENANT_A))
                    self.assertIsNotNone(kept, "tenant A's case must still be A's")
                    self.assertEqual(kept["shipper"], "Acme")
                    self.assertEqual(kept[TENANT_FIELD], TENANT_A)
                    self.assertIsNone(_run(store.get_case("CASE-SHP-1", tenant_id=TENANT_B)))

    def test_the_owner_can_still_rewrite_its_own_case_unversioned(self):
        """
        Not every unversioned write is a create: ingest_document writes the case
        ingest_shipment has just opened (or returned as already existing) again,
        with no version. Refusing those would break document intake.
        """
        for name, store, _ in _backends():
            with self.subTest(backend=name):
                _run(store.put_case(_case("CASE-SHP-1", shipper="Acme"), tenant_id=TENANT_A))
                _run(store.put_case(
                    _case("CASE-SHP-1", shipper="Acme Ltd"), tenant_id=TENANT_A,
                ))
                case = _run(store.get_case("CASE-SHP-1", tenant_id=TENANT_A))
                self.assertEqual(case["shipper"], "Acme Ltd")
                self.assertEqual(case[TENANT_FIELD], TENANT_A)

    def test_a_new_case_is_stamped_with_the_requested_tenant(self):
        for name, store, _ in _backends():
            with self.subTest(backend=name):
                _run(store.put_case(_case("CASE-B1"), tenant_id=TENANT_B))
                case = _run(store.get_case("CASE-B1", tenant_id=TENANT_B))
                self.assertEqual(case[TENANT_FIELD], TENANT_B)

    def test_a_write_naming_no_tenant_keeps_the_stored_owner(self):
        """The case keeps the owner on the stored document rather than being
        re-homed into whichever tenant the write resolves to."""
        with patch.dict(os.environ, {"MULTI_TENANT": "false"}):
            for name, store, _ in _backends():
                with self.subTest(backend=name):
                    _run(store.put_case(_case("CASE-A1"), tenant_id=TENANT_A))
                    _run(store.put_case(_case("CASE-A1", note="rewritten")))
                    case = _run(store.get_case("CASE-A1", tenant_id=TENANT_A))
                    self.assertIsNotNone(case, "the case was moved out of tenant A")
                    self.assertEqual(case["note"], "rewritten")

    def test_a_case_written_before_stamping_is_stamped_on_rewrite(self):
        """An unowned document has no owner to protect; refusing to rewrite it
        would strand every pre-upgrade case."""
        with patch.dict(os.environ, {"MULTI_TENANT": "false"}):
            for name, store, plant in _backends():
                with self.subTest(backend=name):
                    plant(_case("CASE-LEGACY"))
                    _run(store.put_case(_case("CASE-LEGACY", note="rewritten")))
                    case = _run(store.get_case(
                        "CASE-LEGACY", tenant_id=tenant.SINGLE_TENANT_ID,
                    ))
                    self.assertEqual(case["note"], "rewritten")
                    self.assertEqual(case[TENANT_FIELD], tenant.SINGLE_TENANT_ID)

    def test_firestore_reads_the_owner_inside_the_transaction_and_writes_nothing_on_refusal(self):
        """
        The check must be made on a read inside the same transaction as the
        write: a read before it would let a case created between the two be
        replaced. And a refused write must not reach the document at all.
        """
        store, fake = _firestore_on_fake()
        _run(store.put_case(_case("CASE-SHP-1"), tenant_id=TENANT_A))
        fake.log.clear()

        with self.assertRaises(tenant.TenantError):
            _run(store.put_case(_case("CASE-SHP-1"), tenant_id=TENANT_B))

        self.assertEqual(fake.writes_to("CASE-SHP-1"), [])
        self.assertIn(("get", ("cases", "CASE-SHP-1"), True), fake.log)

    def test_ingesting_another_tenants_shipment_id_leaves_their_case(self):
        """
        The reported path, end to end: B's scoped get_case cannot see A's case,
        so ingest_shipment goes on to write CASE-<shipment_id> for B.
        """
        from vf_logistics import orchestrator

        with patch.dict(os.environ, {"MULTI_TENANT": "true"}), patch.object(
            orchestrator.document_render, "render_bill_of_lading", lambda *a: None,
        ):
            for name, store, _ in _backends():
                with self.subTest(backend=name), patch.object(store_mod, "_store", store):
                    _run(orchestrator.ingest_shipment(
                        {"shipment_id": "SHP-1", "shipper_company": "Acme"},
                        tenant_id=TENANT_A,
                    ))
                    with self.assertRaises(tenant.TenantError):
                        _run(orchestrator.ingest_shipment(
                            {"shipment_id": "SHP-1", "shipper_company": "Globex"},
                            tenant_id=TENANT_B,
                        ))
                    case = _run(store.get_case("CASE-SHP-1", tenant_id=TENANT_A))
                    self.assertIsNotNone(case)
                    self.assertEqual(case["shipment"]["shipper_company"], "Acme")


class BillingAttributionTests(unittest.TestCase):
    def test_rollups_are_per_tenant(self):
        """
        An unscoped total would bill every customer for the whole platform's
        spend, which is a wrong invoice rather than a wrong dashboard.
        """
        store = MemoryStore()
        _run(store.put_case(_case("CASE-A1"), tenant_id=TENANT_A))
        _run(store.put_case(_case("CASE-A2"), tenant_id=TENANT_A))
        _run(store.put_case(_case("CASE-B1"), tenant_id=TENANT_B))

        a = _run(store.sum_rollups(tenant_id=TENANT_A))
        b = _run(store.sum_rollups(tenant_id=TENANT_B))

        self.assertEqual(a["total_input_tokens"], 2000)
        self.assertEqual(b["total_input_tokens"], 1000)
        self.assertAlmostEqual(a["estimated_cost_usd"], 0.002, places=6)
        self.assertAlmostEqual(b["estimated_cost_usd"], 0.001, places=6)


class WorkerScopeTests(unittest.TestCase):
    def test_worker_claims_across_every_tenant(self):
        """
        tenant_id=None on claim_next_pending means "any tenant", not "the default
        tenant". Scoping the background worker to one would leave every other
        customer's cases unprocessed forever.
        """
        store = MemoryStore()
        _run(store.put_case(_case("CASE-B1", state="INGESTED"), tenant_id=TENANT_B))

        claimed = _run(store.claim_next_pending(("INGESTED",)))
        self.assertIsNotNone(
            claimed, "the worker must pick up work from tenants other than default",
        )
        self.assertEqual(claimed["case_id"], "CASE-B1")


class LegacyDocumentTests(unittest.TestCase):
    def test_documents_written_before_stamping_belong_to_the_default_tenant(self):
        """
        Otherwise the upgrade that introduced the field would make every existing
        case invisible. Sound only because those records predate multi-tenancy by
        definition -- multi-tenancy is what introduced the field.
        """
        store = MemoryStore()
        legacy = _case("CASE-LEGACY")
        # Written directly, bypassing put_case, to simulate a pre-upgrade row.
        store._cases["CASE-LEGACY"] = legacy
        self.assertNotIn(TENANT_FIELD, legacy)

        found = _run(store.get_case(
            "CASE-LEGACY", tenant_id=tenant.SINGLE_TENANT_ID,
        ))
        self.assertIsNotNone(found)
        self.assertIsNone(_run(store.get_case("CASE-LEGACY", tenant_id=TENANT_B)))


class SingleTenantBackwardCompatibilityTests(unittest.TestCase):
    def test_omitting_tenant_id_keeps_working(self):
        """
        Every existing call site omits tenant_id. With multi-tenancy off they all
        resolve to the one implicit tenant, so behaviour is unchanged -- which is
        what lets this land without rewriting the orchestrator.
        """
        store = MemoryStore()
        _run(store.put_case(_case("CASE-X")))
        self.assertIsNotNone(_run(store.get_case("CASE-X")))
        self.assertEqual(len(_run(store.list_cases())), 1)
        rows, _ = _run(store.query_cases())
        self.assertEqual(len(rows), 1)

    def test_the_implicit_tenant_is_named_not_blank(self):
        """Turning multi-tenancy on later must not find a population of records
        whose owner is an empty string."""
        store = MemoryStore()
        _run(store.put_case(_case("CASE-X")))
        case = _run(store.get_case("CASE-X"))
        self.assertEqual(case[TENANT_FIELD], tenant.SINGLE_TENANT_ID)
        self.assertTrue(tenant.SINGLE_TENANT_ID)


# --------------------------------------------------------------------------
# Route-level isolation
# --------------------------------------------------------------------------
#
# Everything above tests the store. That is necessary and not sufficient: the
# store was already fully scoped while the orchestrator, governance and tools
# layers dropped the tenant on the way to it, so every store test passed and
# four of fifty-two routes were actually isolated.
#
# These tests run the real Flask handlers with MULTI_TENANT=true. Under that
# setting tenant.resolve(None) raises, so a handler that still drops the tenant
# cannot return a plausible-looking answer -- it 500s, and the walk below fails.


def _auth_as(tenant_id: str):
    """An authenticated context for one tenant, as IAP would produce."""
    from vf_logistics import auth

    return auth.AuthContext(
        email=f"operator@{tenant_id}.example",
        roles={auth.Role.GOVERNANCE_ADMIN},
        iap_subject=f"subject-{tenant_id}",
        tenant_id=tenant_id,
    )


class RouteTenantCompletenessTests(unittest.TestCase):
    """
    The completeness gate for threading the tenant through every layer.

    Written as a walk over routes rather than as one test per route on purpose:
    a per-route list is a list somebody forgets to add to, and the failure that
    causes is silent. This asks the app for its own URL map, so a route added
    later is covered on arrival.
    """

    # Routes excluded, each for a stated reason rather than because it failed.
    #
    # The machine-to-machine sinks take no tenant by design -- _tenant() returns
    # None for them and the store resolves the implicit tenant, which is sound
    # while they are IAM-gated, and tenant.assert_isolation_is_enforceable()
    # refuses to boot the configuration where it would not be.
    #
    # All of these happen to be POST-only, so the GET walk below would not reach
    # them anyway. They are listed so that adding a GET to one of them is a
    # deliberate act rather than something this test quietly starts covering, and
    # the paths are the real ones: an earlier version of this list had
    # "/api/v1/bucket/", which matches nothing -- the route is
    # /api/v1/ingest/bucket-sweep.
    SKIP_PREFIXES = (
        "/api/v1/events/",        # Pub/Sub shipment sink and the GCS storage sink
        "/internal/",             # cross-identity executor hop, IAM-gated
        "/api/v1/ingest/",        # operator sweep of the staging bucket
        "/static/",
    )
    SKIP_EXACT = {
        "/",                      # serves HTML, not the API
        "/health",
        "/healthz",
        "/readyz",
        "/api/v1/openapi.json",
        "/api/v1/config",
        # Spends model tokens on a real inference call, so it needs credentials
        # and would bill a run of the test suite. Its own tests cover it.
        "/demo",
    }

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MULTI_TENANT": "true",
            "STORE_BACKEND": "memory",
            "IAP_ENABLED": "false",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        from vf_logistics import app as app_mod

        self.app_mod = app_mod
        app_mod.app.config["TESTING"] = True
        self.client = app_mod.app.test_client()

        ctx = _auth_as(TENANT_A)
        self.auth_patch = patch.object(app_mod, "get_auth_context", lambda: ctx)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

        # The role decorators authenticate the request themselves. Before the
        # membership registry (findings T02/T03) an unauthenticated request fell
        # through to the anonymous viewer, even with MULTI_TENANT on, so they
        # passed here; an anonymous caller now has no tenant and is refused. The
        # walk is about handlers dropping the tenant, not about authentication,
        # so the request is authenticated as the same tenant-A identity.
        from flask import g

        from vf_logistics import auth

        def _authenticated() -> None:
            g.auth_context = ctx
            return None

        self.authn_patch = patch.object(auth, "authenticate_request", _authenticated)
        self.authn_patch.start()
        self.addCleanup(self.authn_patch.stop)

    def _gettable_paths(self) -> list[str]:
        paths = []
        for rule in self.app_mod.app.url_map.iter_rules():
            if "GET" not in (rule.methods or set()):
                continue
            if rule.arguments:
                continue  # needs a real id; covered by the per-route tests below
            path = str(rule.rule)
            if path in self.SKIP_EXACT:
                continue
            if any(path.startswith(p) for p in self.SKIP_PREFIXES):
                continue
            paths.append(path)
        return sorted(paths)

    def test_no_get_route_drops_the_tenant(self):
        """
        Under MULTI_TENANT=true a handler that reaches the store without a tenant
        raises TenantError, which surfaces as a 500. Any 5xx here means a layer
        between the route and the store is still dropping the value.
        """
        broken = []
        for path in self._gettable_paths():
            resp = self.client.get(path)
            if resp.status_code >= 500:
                broken.append((path, resp.status_code))

        self.assertEqual(
            [], broken,
            "these routes reached the store without a tenant: "
            + ", ".join(f"{p} -> {s}" for p, s in broken),
        )

    def test_the_walk_actually_covered_something(self):
        """
        Guards the guard. A typo in SKIP_PREFIXES that excluded everything would
        make the test above pass by testing nothing, which is the failure mode of
        every test that iterates over a filtered collection.
        """
        self.assertGreaterEqual(len(self._gettable_paths()), 8)


class ReviewActionIsolationTests(unittest.TestCase):
    """
    The two write paths that take a case_id from the caller.

    Both looked up the case unscoped, and put_case preserves the stored owner --
    so a reviewer in one tenant naming another tenant's case id would have had
    their decision recorded against the victim's shipment, successfully.
    """

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MULTI_TENANT": "true", "STORE_BACKEND": "memory",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.store = MemoryStore()
        self.store_patch = patch.object(store_mod, "_store", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

        _run(self.store.put_case(
            _case("CASE-A1", state="PENDING_HUMAN"), tenant_id=TENANT_A,
        ))

    def test_human_decide_cannot_reach_another_tenants_case(self):
        from vf_logistics import orchestrator

        result = _run(orchestrator.human_decide(
            "CASE-A1", "block", "reviewer-b", "not mine to block",
            tenant_id=TENANT_B,
        ))

        self.assertFalse(result["ok"])
        # Same answer as a genuinely missing case, so the id cannot be probed.
        self.assertEqual("case not found", result["error"])

        # And the real case is untouched.
        case = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        self.assertEqual("PENDING_HUMAN", case["state"])

    def test_the_owning_tenant_can_still_decide(self):
        """
        The negative test above passes if human_decide is simply broken, so the
        positive case is asserted alongside it.
        """
        from vf_logistics import orchestrator

        result = _run(orchestrator.human_decide(
            "CASE-A1", "block", "reviewer-a", "sanctioned consignee",
            tenant_id=TENANT_A,
        ))
        self.assertTrue(result["ok"], result.get("error"))
        case = _run(self.store.get_case("CASE-A1", tenant_id=TENANT_A))
        self.assertEqual("BLOCKED_BY_HUMAN", case["state"])

    def test_deep_review_cannot_spend_tokens_on_another_tenants_case(self):
        from vf_logistics import orchestrator

        result = _run(orchestrator.deep_review("CASE-A1", tenant_id=TENANT_B))
        self.assertFalse(result["ok"])
        self.assertEqual("case not found", result["error"])


class SharedBucketIsolationTests(unittest.TestCase):
    """
    The document bucket is one bucket for every tenant, so isolation there is a
    matter of object names, not of the store's tenant filter.

    Written from the attacker's side like the rest of this file: tenant A stages,
    sweeps or archives, and the assertion is that nothing of tenant B's reaches A
    -- not B's paperwork through a colliding object name, and not B's staged files
    or evidence through a sweep of a prefix A chose.
    """

    BUCKET = "shared-documents"

    def setUp(self):
        from vf_logistics import document_store, orchestrator

        self.env = patch.dict(os.environ, {
            "MULTI_TENANT": "true",
            "STORE_BACKEND": "memory",
            "IAP_ENABLED": "false",
            # Only so app.py's import-time registry guard passes if this class is
            # the first to import it; requests here authenticate via _auth_as.
            "VF_TENANT_MEMBERS": json.dumps({"service": {TENANT_A: "operator"}}),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.store = MemoryStore()
        for p in (
            patch.object(store_mod, "_store", self.store),
            patch.object(document_store, "BUCKET", self.BUCKET),
            patch.object(document_store, "_client", FakeGcs()),
            patch.object(document_store, "_client_error", None),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.gcs = document_store._client

        # Intake is the model call; what matters here is which objects reach it,
        # and for which tenant.
        self.ingested: list[tuple[bytes, str | None]] = []

        async def fake_ingest_document(data, filename, mime_type=None, tenant_id=None):
            self.ingested.append((data, tenant_id))
            return {"accepted": True, "blocked": False, "case_id": f"CASE-DOC-{len(self.ingested)}"}

        ingest = patch.object(orchestrator, "ingest_document", fake_ingest_document)
        ingest.start()
        self.addCleanup(ingest.stop)

    def stage(self, *names: str) -> None:
        for name in names:
            self.gcs.objects[(self.BUCKET, name)] = (name.encode(), "application/pdf")

    def test_the_same_shipment_in_two_tenants_cannot_replace_the_first_ones_document(self):
        """
        An object filed by case id and date alone is one object for every tenant
        with that shipment id. The archive is written before the case, so B's
        bill of lading replaced A's whether or not the store then accepted B's
        case, and A's reviewer was served B's shipment.
        """
        from vf_logistics import document_store, orchestrator

        def render(shipment, case_id, source):
            return f"bill of lading for {shipment['shipper_company']}".encode()

        with patch.object(orchestrator.document_render, "render_bill_of_lading", render):
            _run(orchestrator.ingest_shipment(
                {"shipment_id": "SHP-1", "shipper_company": "Acme"}, tenant_id=TENANT_A,
            ))
            # Case ids are not tenant-scoped, so this backend refuses B's case;
            # what is asserted is what B's attempt left behind in the bucket.
            with contextlib.suppress(tenant.TenantError):
                _run(orchestrator.ingest_shipment(
                    {"shipment_id": "SHP-1", "shipper_company": "Globex"}, tenant_id=TENANT_B,
                ))

        case = _run(self.store.get_case("CASE-SHP-1", tenant_id=TENANT_A))
        data, _ = _run(document_store.fetch(case["provenance"]["uri"]))
        self.assertEqual(data, b"bill of lading for Acme")
        self.assertEqual(
            {data for data, _ in self.gcs.objects.values()},
            {b"bill of lading for Acme", b"bill of lading for Globex"},
        )

    def test_a_sweep_ingests_only_the_callers_own_staged_objects(self):
        """
        Finding (c): the sweep skipped only shipping-documents/, so a prefix of
        the caller's choosing reached every tenant's staging and evidence.
        """
        from vf_logistics import orchestrator

        own = f"tenants/{TENANT_A}/inbox/a.pdf"
        self.stage(
            own,
            f"tenants/{TENANT_B}/inbox/b.pdf",
            f"tenants/{TENANT_B}/evidence/evreq-1/2026/10/14/CASE-1/cert.pdf",
            f"tenants/{TENANT_A}/evidence/evreq-2/2026/10/14/CASE-2/cert.pdf",
            f"tenants/{TENANT_A}/shipping-documents/2026/10/14/CASE-3/BL.pdf",
            "evidence/evreq-3/2026/10/14/CASE-4/cert.pdf",
            "shipping-documents/2026/10/14/CASE-5/BL.pdf",
            "inbox/legacy.pdf",
        )

        for prefix in (
            "evidence/", "", "tenants/", f"tenants/{TENANT_B}/",
            f"../{TENANT_B}/", f"tenants/{TENANT_A}/../{TENANT_B}/", "inbox/",
        ):
            _run(orchestrator.sweep_bucket(prefix, 25, tenant_id=TENANT_A))

        self.assertEqual(self.ingested, [(own.encode(), TENANT_A)])

    def test_the_storage_event_route_ingests_only_the_callers_own_objects(self):
        from flask import g

        from vf_logistics import app as app_mod
        from vf_logistics import auth

        ctx = _auth_as(TENANT_A)

        def _authenticated() -> None:
            g.auth_context = ctx
            return None

        authn = patch.object(auth, "authenticate_request", _authenticated)
        authn.start()
        self.addCleanup(authn.stop)
        limiter_was = app_mod.limiter.enabled
        app_mod.limiter.enabled = False
        self.addCleanup(setattr, app_mod.limiter, "enabled", limiter_was)
        client = app_mod.app.test_client()

        def notify(name: str, bucket: str = self.BUCKET):
            envelope = {"message": {"attributes": {"bucketId": bucket, "objectId": name}}}
            response = client.post("/api/v1/events/storage", json=envelope)
            self.assertEqual(response.status_code, 200)
            return response.get_json()

        own = f"tenants/{TENANT_A}/inbox/a.pdf"
        foreign = (
            (f"tenants/{TENANT_B}/inbox/b.pdf", self.BUCKET),
            (f"tenants/{TENANT_B}/evidence/evreq-1/2026/10/14/CASE-1/cert.pdf", self.BUCKET),
            ("evidence/evreq-3/2026/10/14/CASE-4/cert.pdf", self.BUCKET),
            ("inbox/legacy.pdf", self.BUCKET),
            (f"tenants/{TENANT_A}/../{TENANT_B}/inbox/b.pdf", self.BUCKET),
            (f"tenants/{TENANT_A}/evidence/evreq-2/2026/10/14/CASE-2/cert.pdf", self.BUCKET),
            (own, "someone-elses-bucket"),
        )
        self.stage(own, *(name for name, _ in foreign))
        self.gcs.objects[("someone-elses-bucket", own)] = (b"not ours", "application/pdf")

        for name, bucket in foreign:
            with self.subTest(name=name, bucket=bucket):
                self.assertTrue(notify(name, bucket).get("ignored"))
        self.assertEqual(self.ingested, [])

        notify(own)
        self.assertEqual(self.ingested, [(own.encode(), TENANT_A)])


class PrefilterRuleIsolationTests(unittest.TestCase):
    """
    Pre-filter rules used to be verifier.py module globals rebound in place.

    That made them the one control in the system with no tenant at all: a
    governance admin editing their blacklist changed what every other customer's
    shipments were screened against, with no audit record on the affected
    tenants. The assertion that matters is not that the rules are stored
    separately but that they produce different verdicts.
    """

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MULTI_TENANT": "true", "STORE_BACKEND": "memory",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = MemoryStore()

    def test_rules_are_stored_per_tenant(self):
        _run(self.store.put_prefilter_rules(
            {"blacklist_companies": ["acme trading"]}, tenant_id=TENANT_A,
        ))
        self.assertIsNotNone(
            _run(self.store.get_prefilter_rules(tenant_id=TENANT_A))
        )
        self.assertIsNone(
            _run(self.store.get_prefilter_rules(tenant_id=TENANT_B)),
            "tenant B read tenant A's screening rules",
        )

    def test_a_tenants_blacklist_edit_does_not_change_another_tenants_verdict(self):
        """
        The end-to-end version of the same fact, through verifier.validate().

        Asserted on the verdict rather than on storage because storage being
        separate is worth nothing if the check function still reads a global.
        """
        from vf_logistics import verifier

        shipment = {
            "shipper_company": "Acme Trading",
            "shipper_tax_id": "0301234567",
            "origin": "ho chi minh city",
            "destination": "hanoi",
            "declared_value": 50_000,
            "shipping_cost": 1_500,
        }

        rules_a, errors = verifier.apply_prefilter_update(
            verifier.PrefilterRules.defaults(),
            {"blacklist_companies": ["acme trading"]},
        )
        self.assertEqual([], errors)
        rules_b = verifier.PrefilterRules.defaults()

        blocked = verifier.validate(
            shipment, screen_sanctions=False, rules=rules_a,
        )
        clean = verifier.validate(
            shipment, screen_sanctions=False, rules=rules_b,
        )

        codes_a = {f["code"] for f in blocked["findings"]}
        codes_b = {f["code"] for f in clean["findings"]}

        self.assertIn("BLACKLIST_MATCH", codes_a)
        self.assertNotIn(
            "BLACKLIST_MATCH", codes_b,
            "tenant A's blacklist entry decided tenant B's shipment",
        )

    def test_defaults_are_returned_for_a_tenant_that_never_saved_any(self):
        from vf_logistics import verifier

        rules = verifier.PrefilterRules.from_dict(
            _run(self.store.get_prefilter_rules(tenant_id=TENANT_B))
        )
        self.assertEqual(verifier.PrefilterRules.defaults(), rules)

    def test_a_partial_update_leaves_the_other_lists_alone(self):
        """
        The governance screen edits one list at a time. An update that reset the
        four keys it did not mention would quietly restore a blacklist entry a
        customer had removed.
        """
        from vf_logistics import verifier

        base = verifier.PrefilterRules.defaults()
        updated, errors = verifier.apply_prefilter_update(
            base, {"low_value_threshold_usd": 250},
        )
        self.assertEqual([], errors)
        self.assertEqual(250, updated.low_value_threshold_usd)
        self.assertEqual(base.blacklist_companies, updated.blacklist_companies)
        self.assertEqual(base.vip_registry, updated.vip_registry)
        self.assertEqual(base.safe_routes, updated.safe_routes)

    def test_an_invalid_key_applies_nothing(self):
        """
        All or nothing. The previous implementation applied each valid key as it
        went and reported errors afterwards, so a request with one bad field left
        a half-applied rule set behind an error response.
        """
        from vf_logistics import verifier

        base = verifier.PrefilterRules.defaults()
        updated, errors = verifier.apply_prefilter_update(
            base,
            {
                "blacklist_companies": ["legitimately added"],
                "low_value_threshold_usd": "not a number",
            },
        )
        self.assertIsNone(updated)
        self.assertTrue(errors)

    def test_rules_are_immutable(self):
        """
        A check function handed a mutable rule set could edit it, and then whether
        a shipment cleared would depend on which checks had already run against
        the same object.
        """
        import dataclasses

        from vf_logistics import verifier

        rules = verifier.PrefilterRules.defaults()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            rules.low_value_threshold_usd = 999  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
