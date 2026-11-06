"""
State store for the autonomous orchestrator.

Two interchangeable backends behind one interface:

  * firestore (default) - durable, survives Cloud Run instance restarts, so a
    long-running case can be picked up by a different instance than the one
    that ingested it.
  * memory - process-local dict. Selected with STORE_BACKEND=memory. Exists so
    the demo is always runnable even if Firestore is not provisioned.

Data integrity guarantees:
  * Optimistic locking - every case has a _version field; updates fail if the
    version changed since read (prevents lost updates from concurrent workers).
  * Immutable audit - audit_log entries can only be appended, never modified
    or deleted. The reset() function deliberately skips the audit collection.
  * Transactions - read-modify-write patterns use Firestore transactions to
    prevent race conditions.

Collections / keys:
  cases                 - one document per shipment moving through the pipeline
  events                - append-only feed of what the orchestrator did, for the
                          dashboard
  audit_log             - append-only record of actions taken on behalf of the
                          operator
  delegation_boundaries - the versioned policies that grant the agent authority
  boundary_counters     - the highest boundary version ever allotted, per tenant
  prefilter_rules       - deterministic rules applied before any model runs

Six collections, not three. The ones that were missing from this list are the
ones that carry governance state, which is the opposite of the right thing to
leave out of a map of where state lives.

Track: Best Apps and Agents
Hackathon: Nebius x NVIDIA Global AI Hackathon
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    """ISO-8601 UTC timestamp. Avoids the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc).isoformat()


# A claim is a lease, not a permanent lock. If the instance holding a case dies
# mid-step - a crash, a scale-down, or a revision rollout - the lease expires
# and another worker picks the case up. Without this a redeploy strands
# in-flight cases forever.
from vf_logistics import tenant
from vf_logistics.config import gcp_project

CLAIM_LEASE_SECONDS = int(os.getenv("CLAIM_LEASE_SECONDS", "180"))


def _lease_expired(case: dict[str, Any], now_dt: datetime) -> bool:
    claimed_at = case.get("claimed_at")
    if not claimed_at:
        return True
    try:
        held = datetime.fromisoformat(claimed_at)
    except (TypeError, ValueError):
        return True
    if held.tzinfo is None:
        held = held.replace(tzinfo=timezone.utc)
    return (now_dt - held).total_seconds() > CLAIM_LEASE_SECONDS


def is_claimable(case: dict[str, Any], states: tuple[str, ...], now: str, now_dt: datetime) -> bool:
    """Shared predicate so both backends agree on what is ready for work."""
    if case.get("state") not in states:
        return False
    if case.get("not_before") and case["not_before"] > now:
        return False  # still backing off after a failure
    if case.get("claimed") and not _lease_expired(case, now_dt):
        return False  # someone else is actively working it
    return True


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class OptimisticLockError(Exception):
    """Raised when a case was modified by another process since it was read."""

    def __init__(self, case_id: str, expected_version: int, actual_version: int):
        self.case_id = case_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Case {case_id} version conflict: expected {expected_version}, "
            f"found {actual_version}. Reload and retry."
        )


class AuditImmutabilityError(Exception):
    """Raised when attempting to modify or delete an audit entry."""

    def __init__(self, operation: str, audit_id: str):
        self.operation = operation
        self.audit_id = audit_id
        super().__init__(
            f"Audit entries are immutable: cannot {operation} entry {audit_id}"
        )


class BoundaryConflict(Exception):
    """A publish would have overwritten a boundary that already exists."""


# How many times a Firestore transaction is attempted before giving up. The
# client's default is 5, and the emulator showed that is too few for six
# concurrent publishes, or a dozen concurrent sign-in attempts, contending on one
# counter document: they failed with "Transaction lock timeout" after 5 attempts.
_TXN_ATTEMPTS = 25

# How many times the WHOLE transaction is run again after losing on contention.
# The client's own retry covers a failed commit only; an Aborted raised by a
# read inside the transaction ("Transaction lock timeout", seen against the
# emulator with client 2.34) escapes it, so that case is retried here.
_TXN_ROUNDS = 8

# FirestoreStore.query_audit asks Firestore for one filter only. When it must check others it reads this many rows a
# request and at most this many requests a call; a call that stops there returns the cursor to go on from.
_AUDIT_SCAN_PAGE = 200
_AUDIT_SCAN_PAGES = 25


def _is_contention(exc: BaseException, aborted: type[BaseException]) -> bool:
    if isinstance(exc, aborted):
        return True
    # transactional() reports a commit retried out as this ValueError. Any other
    # ValueError is a real error and must not be retried as if it were a race.
    return isinstance(exc, ValueError) and "Failed to commit transaction" in str(exc)


# --------------------------------------------------------------------------
# In-memory backend
# --------------------------------------------------------------------------

# Tenant ownership, stamped on every document this module writes and checked on
# every document it returns. Underscore-prefixed to match the other
# store-managed fields (_version, _updated_at) -- these are ours, not the
# caller's.
TENANT_FIELD = "_tenant_id"


def _stamp_tenant(doc: dict[str, Any], tenant_id: str | None) -> str:
    """
    Record which tenant owns a document. Returns the resolved tenant.

    Every write in this module goes through here, so there is one place to read
    to know that nothing is stored unowned. A document without an owner is one
    that a tenant-filtered query can never return, which would look like data
    loss rather than like the bug it is.
    """
    resolved = tenant.resolve(tenant_id)
    doc[TENANT_FIELD] = resolved
    return resolved


def _owned_by(doc: dict[str, Any] | None, tenant_id: str) -> bool:
    """
    Whether a document belongs to this tenant.

    Documents written before tenant stamping existed have no owner field. They
    are treated as belonging to the single implicit tenant rather than to nobody,
    because the alternative is that an upgrade makes every existing case
    invisible. That is only sound while those records predate multi-tenancy --
    which they do by definition, since multi-tenancy is what introduced the
    field.
    """
    if doc is None:
        return False
    owner = doc.get(TENANT_FIELD) or tenant.SINGLE_TENANT_ID
    return owner == tenant_id


def _keep_case_owner(
    case: dict[str, Any], existing: dict[str, Any] | None, requested: str | None,
) -> None:
    """
    Settle who owns `case` before it is written over `existing` (None if new).

    MemoryStore.put_case's rule, used by both FirestoreStore.put_case paths so the
    two backends cannot drift apart again: a stored case with an owner keeps it,
    and a write requested as any other tenant raises rather than re-homing -- or,
    without a version check, replacing -- the case. A new case, or one written
    before tenant stamping, is stamped with the requested tenant.
    """
    owner = (existing or {}).get(TENANT_FIELD)
    if owner:
        if requested and requested != owner:
            raise tenant.TenantError(
                f"case {case['case_id']} belongs to tenant {owner!r}; "
                f"refusing to write it as {requested!r}"
            )
        case[TENANT_FIELD] = owner
    else:
        _stamp_tenant(case, requested)


def _throttle_id(tenant_id: str, key: str) -> str:
    """
    The document id for a rate-limit counter.

    Hashed so a stored counter does not carry the email address or IP it counts:
    the collection exists to say "too many attempts", not to keep a log of who
    tried to sign in.
    """
    return hashlib.sha256(f"{tenant_id}\x00{key}".encode("utf-8")).hexdigest()


def _activity_id(tenant_id: str, case_id: str, actor: str) -> str:
    """The document id for one person's running time on one case. Hashed: no email in the id."""
    return hashlib.sha256(
        f"{tenant_id}\x00{case_id}\x00{actor}".encode("utf-8")
    ).hexdigest()


# The one ledger scope every console sign-in attempt is counted in. Not a tenant:
# a sign-in comes before anyone has an organisation (re-review finding R04), and a
# per-tenant scope would give a caller who could name one a fresh allowance.
LOGIN_THROTTLE_SCOPE = "platform-login"


def _within_window(
    created_at: Any, since: str | None, until: str | None
) -> bool:
    """
    Whether a timestamp falls in the half-open window [since, until).

    Half-open so that consecutive periods neither double-count nor drop a case:
    a case created at exactly the month boundary belongs to the later month and
    to only one of them. Closed-closed bounds would bill the boundary twice.

    Compared lexicographically, which is valid because every timestamp in this
    store comes from utcnow() -- a fixed-width ISO-8601 string at UTC, where
    string order is chronological order. It would NOT be valid across mixed
    offsets, so nothing here should ever store a local-time timestamp.

    A document with no `created_at` is EXCLUDED from a bounded query and included
    in an unbounded one. Excluding it is the conservative answer: a case whose age
    is unknown cannot be asserted to fall inside a billing period, and silently
    billing it to whichever period happens to be queried would be worse than
    leaving it out of both.
    """
    if since is None and until is None:
        return True
    if not isinstance(created_at, str) or not created_at:
        return False
    if since is not None and created_at < since:
        return False
    if until is not None and created_at >= until:
        return False
    return True


def _boundary_key(tenant_id: str, boundary_id: str) -> str:
    """
    Where a boundary is stored: a key that includes its tenant.

    Review finding T01: the key used to be the bare boundary_id, so every tenant's
    first publish wrote BOUNDARY-v1 to the same slot and the later one erased the
    earlier one's policy and history. The implicit single tenant keeps the bare id,
    because the three documents the live service already holds are keyed that way
    and a rename would orphan them; any other tenant is prefixed. `__` cannot occur
    in a boundary id, so the two forms never collide.
    """
    if tenant_id == tenant.SINGLE_TENANT_ID:
        return boundary_id
    return f"{tenant_id}__{boundary_id}"


def _as_version(value: Any) -> int:
    """A stored version as an int, treating anything unreadable as 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _next_boundary(
    draft: dict[str, Any], version: int, current: dict[str, Any] | None,
) -> dict[str, Any]:
    """The record a publish writes, given the version it was allotted."""
    boundary = dict(draft)
    boundary.update({
        "boundary_id": f"BOUNDARY-v{version}",
        "version": version,
        "status": "ACTIVE",
        "supersedes": current.get("boundary_id") if current else None,
    })
    return boundary


class MemoryStore:
    """Process-local store. Fine for a single Cloud Run instance."""

    backend = "memory"

    def __init__(self) -> None:
        self._cases: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._audit: list[dict[str, Any]] = []
        self._boundaries: dict[str, dict[str, Any]] = {}
        # The highest version ever allotted per tenant. Kept apart from the
        # boundaries themselves so a version is never handed out twice even if
        # the record that carried it were ever removed.
        self._boundary_counters: dict[str, int] = {}
        self._prefilter: dict[str, dict[str, Any]] = {}
        self._throttle: dict[str, list[float]] = {}
        self._activity: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # The receiver's applied decisions, by (tenant, idempotency key) (N01).
        self._applied: dict[tuple[str, str], dict[str, Any]] = {}

    # -- applied decisions (the receiving side of case-decisions, N01) ---------

    async def apply_decision_once(
        self, key: str, record: dict[str, Any], tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Apply a delivered decision unless its key was applied before in this tenant.

        The stored record is the application, so claiming the key and applying the
        decision are one step: a repeat only counts a delivery. Returns the record
        with `applied` (whether this delivery applied it) and `deliveries`.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            existing = self._applied.get((scope, key))
            if existing is not None:
                existing["deliveries"] = int(existing.get("deliveries") or 0) + 1
                existing["last_delivery_at"] = utcnow()
                return {**existing, "applied": False}
            doc = {**record, "idempotency_key": key, "deliveries": 1, "applied_at": utcnow()}
            _stamp_tenant(doc, scope)
            self._applied[(scope, key)] = doc
            return {**doc, "applied": True}

    async def get_applied_decision(
        self, key: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """The applied record for this key in this tenant, or None."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            doc = self._applied.get((scope, key))
            return dict(doc) if doc is not None else None

    # -- pre-filter rules --------------------------------------------------
    #
    # Keyed by tenant with no document id of its own: there is exactly one live
    # rule set per tenant, and giving it an id would invite a second.
    #
    # These rules decide which shipments skip AI screening altogether, so they
    # are not display configuration -- they are a control. They used to live in
    # verifier.py module globals, which meant one tenant's edit silently
    # rewrote every tenant's screening until the next container start.

    async def get_prefilter_rules(
        self, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """The tenant's stored rules, or None if they have never saved any."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            stored = self._prefilter.get(scope)
            return dict(stored) if stored else None

    async def put_prefilter_rules(
        self, rules: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            self._prefilter[scope] = dict(rules)

    # -- shared rate limits -----------------------------------------------

    async def hit_rate_limit(
        self, key: str, window_seconds: int, limit: int, tenant_id: str | None = None,
    ) -> bool:
        """
        True when `key` has already used `limit` attempts in the last
        `window_seconds`; otherwise record this attempt and return False.

        Review finding A04: the console's sign-in limiter was a Map in each Node
        process, so every Cloud Run instance had its own allowance. Counting here
        puts every instance on one ledger. A refused attempt is not recorded, so
        waiting out the window is always enough to get back in.
        """
        scope = tenant.resolve(tenant_id)
        doc = _throttle_id(scope, key)
        now = time.time()
        with self._lock:
            stamps = [s for s in self._throttle.get(doc, []) if s > now - window_seconds]
            limited = len(stamps) >= limit
            if not limited:
                stamps.append(now)
            self._throttle[doc] = stamps
            return limited

    # -- operator activity ------------------------------------------------

    async def add_activity(
        self, case_id: str, actor: str, active_ms: int, tenant_id: str | None = None,
    ) -> None:
        """
        Add `active_ms` to the time `actor` has spent with `case_id` in front of them.

        One running total per (tenant, case, person), not one row per report, so the
        console can report every 30 seconds without the collection growing with it,
        and so it is not the audit trail: this is a measurement for the Outcomes
        screen, and an immutable regulator's record is the wrong place for heartbeats.
        """
        scope = tenant.resolve(tenant_id)
        doc = _activity_id(scope, case_id, actor)
        now = utcnow()
        with self._lock:
            row = self._activity.setdefault(doc, {
                "case_id": case_id, "actor": actor, "active_ms": 0,
                "first_at": now, TENANT_FIELD: scope,
            })
            row["active_ms"] += int(active_ms)
            row["last_at"] = now

    async def list_activity(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Every (case, person) running total for the tenant."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            return [dict(r) for r in self._activity.values() if r.get(TENANT_FIELD) == scope]

    # -- delegation boundaries --------------------------------------------

    async def put_boundary(
        self, boundary: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        with self._lock:
            scope = _stamp_tenant(boundary, tenant_id)
            self._boundaries[_boundary_key(scope, boundary["boundary_id"])] = boundary

    async def publish_boundary(
        self, draft: dict[str, Any], tenant_id: str | None = None
    ) -> dict[str, Any]:
        """
        Allot the next version, supersede the active boundary, and store the new
        one -- as one step.

        Review finding A02: the version used to be "active version + 1", so after
        a revoke (no active boundary) the next publish was v1 again and replaced
        the original v1 -- its permissions, its author and its revocation. The
        version now comes from the highest ever allotted for the tenant, and a key
        that already exists is refused rather than overwritten.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            owned = {
                key: b for key, b in self._boundaries.items() if _owned_by(b, scope)
            }
            version = max(
                [self._boundary_counters.get(scope, 0)]
                + [_as_version(b.get("version")) for b in owned.values()]
            ) + 1
            active = {k: b for k, b in owned.items() if b.get("status") == "ACTIVE"}
            current = max(
                active.values(), key=lambda b: _as_version(b.get("version")), default=None,
            )

            boundary = _next_boundary(draft, version, current)
            _stamp_tenant(boundary, scope)
            key = _boundary_key(scope, boundary["boundary_id"])
            if key in self._boundaries:
                raise BoundaryConflict(f"{key} already exists; refusing to overwrite it")

            # Staged, then committed in one go, so a failure above leaves the
            # store exactly as it was. Every ACTIVE record is superseded, not
            # only the newest: two ACTIVE ones would be a defect to repair.
            now = utcnow()
            staged = {
                k: {**b, "status": "SUPERSEDED", "superseded_at": now,
                    "superseded_by": boundary["boundary_id"]}
                for k, b in active.items()
            }
            self._boundaries.update(staged)
            self._boundaries[key] = boundary
            self._boundary_counters[scope] = version
            return dict(boundary)

    async def revoke_active_boundary(
        self, revoked_by: str, note: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Mark the active boundary REVOKED, keeping its content. None if none."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            active = {
                k: b for k, b in self._boundaries.items()
                if b.get("status") == "ACTIVE" and _owned_by(b, scope)
            }
            if not active:
                return None
            now = utcnow()
            staged = {
                k: {**b, "status": "REVOKED", "revoked_at": now,
                    "revoked_by": revoked_by, "revocation_note": note}
                for k, b in active.items()
            }
            self._boundaries.update(staged)
            newest = max(staged.values(), key=lambda b: _as_version(b.get("version")))
            return dict(newest)

    async def active_boundary(
        self, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        # Boundaries are tenant-scoped because "Compliance Admin configures risk
        # rules" is a per-customer role: one customer's delegated auto-release
        # ceiling has no business governing another's shipments.
        scope = tenant.resolve(tenant_id)
        with self._lock:
            active = [
                b for b in self._boundaries.values()
                if b.get("status") == "ACTIVE" and _owned_by(b, scope)
            ]
            if not active:
                return None
            newest = max(active, key=lambda b: b.get("version", 0))
            return dict(newest)

    async def list_boundaries(
        self, limit: int = 20, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            ordered = sorted(
                (b for b in self._boundaries.values() if _owned_by(b, scope)),
                key=lambda b: b.get("version", 0),
                reverse=True,
            )
            return [dict(b) for b in ordered[:limit]]

    async def put_case(
        self,
        case: dict[str, Any],
        expected_version: int | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """
        Store a case with optimistic locking.

        Args:
            case: The case document to store.
            expected_version: If provided, the update only succeeds if the
                current version matches. Pass None for new cases.
            tenant_id: Owner. Taken from the case document when already stamped,
                so a read-modify-write cycle cannot silently re-home a case into
                whichever tenant happens to be making the request.

        Raises:
            OptimisticLockError: If expected_version does not match current.
            TenantError: On an attempt to write into another tenant's case.
        """
        with self._lock:
            case_id = case["case_id"]
            existing = self._cases.get(case_id)

            if expected_version is not None:
                current_version = (existing or {}).get("_version", 0)
                if current_version != expected_version:
                    raise OptimisticLockError(
                        case_id, expected_version, current_version
                    )

            # An existing case keeps its owner. Without this an update that
            # happened to be made in another tenant's request context would move
            # the case across the boundary -- a write that silently steals a
            # record rather than failing.
            if existing is not None and existing.get(TENANT_FIELD):
                owner = existing[TENANT_FIELD]
                requested = tenant.normalise(tenant_id) or case.get(TENANT_FIELD)
                if requested and requested != owner:
                    raise tenant.TenantError(
                        f"case {case_id} belongs to tenant {owner!r}; refusing to "
                        f"write it as {requested!r}"
                    )
                case[TENANT_FIELD] = owner
            else:
                _stamp_tenant(case, tenant_id or case.get(TENANT_FIELD))

            # Increment version on every write
            new_version = (existing or {}).get("_version", 0) + 1 if existing else 1
            case["_version"] = new_version
            case["_updated_at"] = utcnow()
            self._cases[case_id] = case

    async def get_case(
        self, case_id: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            case = self._cases.get(case_id)
            # None rather than a 403 on a foreign case. A distinct "exists but is
            # not yours" response confirms the id is real, which lets a caller
            # enumerate another tenant's case ids.
            if not _owned_by(case, scope):
                return None
            return dict(case) if case else None

    async def list_cases(
        self, limit: int = 100, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            cases = sorted(
                (c for c in self._cases.values() if _owned_by(c, scope)),
                key=lambda c: c.get("created_at", ""),
                reverse=True,
            )
            return [dict(c) for c in cases[:limit]]

    async def find_case_by_client_reference(
        self, client_reference: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        The case an integrator's own reference already produced, if any.

        Backs idempotency on POST /api/v1/compliance/audit. An ERP whose request
        timed out will retry it, and without this the retry runs the whole
        pipeline again: two audits, two sets of tokens billed, and two possibly
        different verdicts for one shipment.

        Newest-first so that if a reference was somehow reused, the latest audit
        wins rather than an arbitrary one.
        """
        if not client_reference:
            return None
        scope = tenant.resolve(tenant_id)
        with self._lock:
            matches = [
                c for c in self._cases.values()
                if c.get("client_reference") == client_reference
                and _owned_by(c, scope)
            ]
            if not matches:
                return None
            matches.sort(key=lambda c: c.get("created_at", ""), reverse=True)
            return dict(matches[0])

    async def query_cases(
        self,
        states: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Cases newest-first, optionally filtered by state, with cursor
        pagination so a caller can walk the whole collection rather than
        only ever seeing the newest `limit` rows (the bug this replaces:
        filtering *after* truncating to a fixed window silently drops older
        matching cases once enough newer ones exist).

        The cursor is the `created_at` value of the last row returned.
        Timestamps come from `utcnow()` (microsecond ISO-8601), so a
        same-instant collision that would require a tie-break secondary key
        is not a realistic risk for this workload.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            cases = [
                c for c in self._cases.values()
                if not c.get("is_marker") and _owned_by(c, scope)
            ]
            if states:
                cases = [c for c in cases if c.get("state") in states]
            cases.sort(key=lambda c: c.get("created_at", ""), reverse=True)
            if cursor:
                cases = [c for c in cases if c.get("created_at", "") < cursor]
            page = [dict(c) for c in cases[:limit]]
            next_cursor = (
                page[-1].get("created_at")
                if len(page) == limit and page[-1].get("created_at")
                else None
            )
            return page, next_cursor

    async def count_by_state(
        self, states: tuple[str, ...], tenant_id: str | None = None
    ) -> dict[str, int]:
        """Exact counts across the whole collection, not just a fetched window."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            counts = {s: 0 for s in states}
            for c in self._cases.values():
                if c.get("is_marker") or not _owned_by(c, scope):
                    continue
                state = c.get("state")
                if state in counts:
                    counts[state] += 1
            return counts

    async def sum_rollups(
        self,
        tenant_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """
        Global token/latency/cost totals from the per-case rollup fields
        (`_agent_calls`, `_input_tokens`, ...) written by the orchestrator on
        every step, not from summing a fetched page - so the number is
        correct at any collection size.

        Tenant-scoped, which makes this the basis for per-customer billing: an
        unscoped total would bill every customer for the whole platform's spend.

        `since` and `until` bound the window by `created_at`, half-open: since <=
        created_at < until. Both are ISO-8601 UTC strings as produced by utcnow(),
        compared lexicographically -- which is correct for that format and is what
        the Firestore implementation does too, so the two agree.

        WITHOUT A WINDOW THERE IS NO INVOICE. This function previously had no time
        filter at all, so it could only ever report lifetime-to-date totals, and
        nothing snapshotted them. A month's usage was therefore not computable even
        in principle: not from this number, and not from a difference of two, because
        the earlier one was never stored.

        The window is on the case's CREATION, not on when each step ran. A case that
        starts on the last day of a month and is decided on the first of the next
        bills entirely to the month it arrived in. That is a deliberate choice --
        per-step attribution would need a timestamp on every step and a different
        aggregation shape -- and it is the convention an invoice has to state.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            calls = 0
            input_tokens = 0
            output_tokens = 0
            cost = 0.0
            latency_sum = 0
            tavily = 0
            tavily_cached = 0
            tavily_billable = 0
            for c in self._cases.values():
                if c.get("is_marker") or not _owned_by(c, scope):
                    continue
                if not _within_window(c.get("created_at"), since, until):
                    continue
                calls += c.get("_agent_calls", 0)
                input_tokens += c.get("_input_tokens", 0)
                output_tokens += c.get("_output_tokens", 0)
                cost += c.get("_estimated_cost_usd", 0.0)
                latency_sum += c.get("_sum_latency_ms", 0)
                tavily += c.get("_tavily_searches", 0)
                tavily_cached += c.get("_tavily_cached", 0)
                tavily_billable += c.get("_tavily_billable", 0)
            return {
                "agent_calls": calls,
                "total_input_tokens": input_tokens,
                "total_output_tokens": output_tokens,
                "estimated_cost_usd": round(cost, 6),
                "avg_latency_ms": int(latency_sum / calls) if calls else 0,
                # Tavily is metered separately from the model spend because it is
                # billed in credits, not dollars, and it is the tighter of the two
                # ceilings: measured at 4.5-5.3 searches per case, the free tier's
                # 1,000 credits a month runs out after roughly 200 cases, while the
                # same traffic costs under a dollar of Nemotron.
                #
                # `billable` is summed rather than derived. A cache hit costs nothing
                # and so does a request that never left the process, so
                # attempts-minus-hits over-reports -- a deployment with no API key would
                # show a full bill for zero requests.
                "tavily_searches": tavily,
                "tavily_cached": tavily_cached,
                "tavily_billable": tavily_billable,
            }

    async def count_by_cleared_by(
        self, tenant_id: str | None = None
    ) -> dict[str, int]:
        """
        Count cases by cleared_by field (rules vs ai).
        Returns counts for Cost-Aware Hybrid Architecture KPI tiles.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            counts = {"rules": 0, "ai": 0, "unknown": 0, "total_auto_cleared": 0}
            for c in self._cases.values():
                if c.get("is_marker") or not _owned_by(c, scope):
                    continue
                if c.get("state") == "AUTO_CLEARED":
                    counts["total_auto_cleared"] += 1
                    cleared_by = c.get("cleared_by")
                    if cleared_by == "rules":
                        counts["rules"] += 1
                    elif cleared_by == "ai":
                        counts["ai"] += 1
                    else:
                        counts["unknown"] += 1
            return counts

    async def claim_next_pending(
        self, states: tuple[str, ...], tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        Atomically hand out one case that is ready for work and mark it claimed,
        so two concurrent workers can never pick up the same case.

        `tenant_id=None` here means "any tenant", not "the default tenant", and
        this is the only method where that is the intended contract: the
        background worker serves every customer, and scoping it to one would
        leave the others' cases unprocessed forever. Callers that want a specific
        tenant pass one, and every request handler does.

        The comment here used to claim this was "the one place" normalise() was
        correct, which stopped being true of the codebase rather than of this
        method: release_case() also used normalise() and was silently accepting
        "any tenant" on a write. It now uses resolve(). If a second method ever
        needs all-tenant reach, it should say so here as explicitly as this one
        does, because normalise() in a scoping position is indistinguishable at a
        glance from a missing check.
        """
        scope = tenant.normalise(tenant_id)
        now = utcnow()
        now_dt = datetime.now(timezone.utc)
        with self._lock:
            for case in sorted(
                self._cases.values(), key=lambda c: c.get("created_at", "")
            ):
                if scope and not _owned_by(case, scope):
                    continue
                if not is_claimable(case, states, now, now_dt):
                    continue
                case["claimed"] = True
                case["claimed_at"] = now
                return dict(case)
        return None

    async def release_case(
        self, case_id: str, tenant_id: str | None = None
    ) -> None:
        # resolve(), not normalise(). normalise() returns None for an absent
        # tenant and never raises, which made `scope` falsy and skipped the
        # ownership check below entirely -- so under multi-tenancy a caller with
        # no tenant could un-claim any case in any tenant and stall that
        # customer's pipeline. resolve() raises instead, which is the same
        # fail-closed shape every other method here uses.
        scope = tenant.resolve(tenant_id)
        with self._lock:
            case = self._cases.get(case_id)
            if case is None:
                return
            # A foreign case is left claimed rather than released. Releasing it
            # would let one tenant interfere with another's pipeline.
            if not _owned_by(case, scope):
                return
            case["claimed"] = False

    async def add_event(
        self, event: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        with self._lock:
            _stamp_tenant(event, tenant_id or event.get(TENANT_FIELD))
            self._events.append(event)
            del self._events[:-500]  # bound memory growth

    async def list_events(
        self, limit: int = 80, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            owned = [e for e in self._events if _owned_by(e, scope)]
            return [dict(e) for e in reversed(owned[-limit:])]

    async def query_events(
        self, cursor: str | None = None, limit: int = 80,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            events = [e for e in reversed(self._events) if _owned_by(e, scope)]
            if cursor:
                events = [e for e in events if e.get("at", "") < cursor]
            page = [dict(e) for e in events[:limit]]
            next_cursor = (
                page[-1].get("at")
                if len(page) == limit and page[-1].get("at")
                else None
            )
            return page, next_cursor

    async def add_audit(
        self, entry: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        """Append an audit entry. Audit entries are immutable once written."""
        with self._lock:
            # Ensure audit_id exists and is unique
            if "audit_id" not in entry:
                entry["audit_id"] = new_id("AUD")
            _stamp_tenant(entry, tenant_id or entry.get(TENANT_FIELD))
            entry["_immutable"] = True
            entry["_created_at"] = utcnow()
            self._audit.append(entry)
            # Bounded so a long-running demo process does not grow memory
            # without limit. This is a memory-backend-only limit: it does
            # NOT apply to FirestoreStore, where the audit log is genuinely
            # unbounded and immutable. Do not rely on MemoryStore for a
            # production audit trail past this cap.
            del self._audit[:-5000]

    async def add_audit_once(
        self, entry: dict[str, Any], tenant_id: str | None = None
    ) -> bool:
        """
        Append the entry unless one with its audit_id is already there. True when this
        call wrote it.

        For a row two writers may both decide to create: the check and the write are one
        step under the lock here, and one atomic create on Firestore, so the loser is told
        "already there" instead of adding a second row. The caller picks an audit_id that
        the same fact always maps to; without one there is nothing to be unique by.
        """
        if "audit_id" not in entry:
            raise ValueError("add_audit_once needs an audit_id derived from what makes the row unique")
        with self._lock:
            if any(a.get("audit_id") == entry["audit_id"] for a in self._audit):
                return False
            _stamp_tenant(entry, tenant_id or entry.get(TENANT_FIELD))
            entry["_immutable"] = True
            entry["_created_at"] = utcnow()
            self._audit.append(entry)
            del self._audit[:-5000]
            return True

    async def update_audit(
        self, audit_id: str, updates: dict[str, Any],
        tenant_id: str | None = None,
    ) -> None:
        """Blocked: audit entries are immutable."""
        raise AuditImmutabilityError("update", audit_id)

    async def delete_audit(
        self, audit_id: str, tenant_id: str | None = None
    ) -> None:
        """Blocked: audit entries are immutable."""
        raise AuditImmutabilityError("delete", audit_id)

    async def list_audit(
        self, limit: int = 80, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)
        with self._lock:
            owned = [a for a in self._audit if _owned_by(a, scope)]
            return [dict(a) for a in reversed(owned[-limit:])]

    async def query_audit(
        self,
        case_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Audit entries newest-first with exact field filters and a cursor,
        so a search for an older case is not silently limited to whatever a
        fixed-size window happened to include."""
        scope = tenant.resolve(tenant_id)
        with self._lock:
            entries = [a for a in reversed(self._audit) if _owned_by(a, scope)]
            if case_id:
                entries = [a for a in entries if a.get("case_id") == case_id]
            if action:
                entries = [a for a in entries if a.get("action") == action]
            if status:
                entries = [a for a in entries if a.get("status") == status]
            if cursor:
                entries = [a for a in entries if a.get("at", "") < cursor]
            page = [dict(a) for a in entries[:limit]]
            next_cursor = (
                page[-1].get("at")
                if len(page) == limit and page[-1].get("at")
                else None
            )
            return page, next_cursor

    async def reset(self, tenant_id: str | None = None) -> int:
        """
        Clear cases and events but preserve audit log (immutable).

        Tenant-scoped: an unscoped reset would let one customer clear another's
        board. `tenant_id=None` with multi-tenancy off clears the single implicit
        tenant, which is the existing behaviour.
        """
        scope = tenant.resolve(tenant_id)
        with self._lock:
            doomed = [
                cid for cid, c in self._cases.items() if _owned_by(c, scope)
            ]
            for cid in doomed:
                del self._cases[cid]
            self._events[:] = [
                e for e in self._events if not _owned_by(e, scope)
            ]
            # AUDIT LOG IS DELIBERATELY NOT CLEARED - immutability guarantee
            # Boundaries deliberately survive a reset: clearing the board is a
            # demo convenience, revoking published authority is not.
            return len(doomed)

    async def backfill_rollups(self, tenant_id: str | None = None) -> dict[str, int]:
        """No-op: MemoryStore cases are process-local and always written by
        the current code, so they never lack the rollup fields the way a
        case created before those fields existed would.

        `tenant_id` is accepted and unused here only because there is nothing to
        migrate; the Firestore implementation honours it."""
        return {"updated": 0, "skipped": len(self._cases)}


# --------------------------------------------------------------------------
# Firestore backend
# --------------------------------------------------------------------------

class FirestoreStore:
    """
    Durable backend. All google-cloud-firestore calls are blocking, so they are
    pushed onto a thread with asyncio.to_thread to keep the event loop free for
    the concurrent model calls.
    """

    backend = "firestore"

    def __init__(self, project: str | None) -> None:
        from google.cloud import firestore  # imported lazily so memory mode
                                            # never needs the dependency

        self._fs = firestore
        # What a transaction that lost on contention raises (see _is_contention).
        # Resolved here, beside the client import, so memory mode never imports
        # google.api_core.
        from google.api_core.exceptions import Aborted

        self._aborted: type[BaseException] = Aborted
        self._db = firestore.Client(project=project)
        self._cases = self._db.collection("cases")
        self._events = self._db.collection("events")
        self._audit = self._db.collection("audit_log")
        self._boundaries = self._db.collection("delegation_boundaries")
        self._boundary_counters = self._db.collection("boundary_counters")
        self._prefilter = self._db.collection("prefilter_rules")
        self._throttle = self._db.collection("login_throttle")
        self._activity = self._db.collection("operator_activity")
        self._applied = self._db.collection("applied_decisions")

    # -- delegation boundaries --------------------------------------------

    # Every query below adds a tenant filter. The helper keeps the pattern in one
    # place so a new query cannot be written without one -- a missing filter here
    # is not a slow query, it is another customer's data in the response.
    def _run_transaction(self, body: Any) -> Any:
        """
        Run a @transactional `body`, retrying the whole of it on contention.

        Each round is a fresh transaction, so every read is repeated and nothing
        decided on stale data is committed. The jittered pause keeps contending
        callers from retrying in lockstep. Anything that is not contention is
        raised at once.
        """
        for round_ in range(_TXN_ROUNDS):
            try:
                return body(self._db.transaction(max_attempts=_TXN_ATTEMPTS))
            except Exception as exc:  # noqa: BLE001 - narrowed by _is_contention
                if round_ == _TXN_ROUNDS - 1 or not _is_contention(exc, self._aborted):
                    raise
                time.sleep(random.uniform(0.05, 0.25) * (round_ + 1))
        raise AssertionError("unreachable")

    # -- applied decisions (the receiving side of case-decisions, N01) ---------

    async def apply_decision_once(
        self, key: str, record: dict[str, Any], tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """
        MemoryStore.apply_decision_once, inside one Firestore transaction.

        The document id is the tenant and the key, so the CREATE that applies the
        decision fails for a second delivery of the same key; concurrent deliveries
        contend on that document and the losers are retried from the read, where
        they see it and only count themselves.
        """
        scope = tenant.resolve(tenant_id)
        ref = self._applied.document(f"{scope}__{key}")

        def _txn() -> dict[str, Any]:
            @self._fs.transactional
            def _do(t: Any) -> dict[str, Any]:
                snap = ref.get(transaction=t)
                now = utcnow()
                if snap.exists:
                    doc = snap.to_dict() or {}
                    deliveries = int(doc.get("deliveries") or 0) + 1
                    t.update(ref, {"deliveries": deliveries, "last_delivery_at": now})
                    return {**doc, "deliveries": deliveries, "last_delivery_at": now, "applied": False}
                doc = {**record, "idempotency_key": key, "deliveries": 1, "applied_at": now}
                _stamp_tenant(doc, scope)
                t.create(ref, doc)
                return {**doc, "applied": True}

            result: dict[str, Any] = self._run_transaction(_do)
            return result

        return await asyncio.to_thread(_txn)

    async def get_applied_decision(
        self, key: str, tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """The applied record for this key in this tenant, or None."""
        scope = tenant.resolve(tenant_id)
        snap = await asyncio.to_thread(self._applied.document(f"{scope}__{key}").get)
        if not snap.exists:
            return None
        doc: dict[str, Any] = snap.to_dict() or {}
        return doc if doc.get(TENANT_FIELD) == scope else None

    def _scoped(self, collection: Any, scope: str) -> Any:
        return collection.where(
            filter=self._fs.FieldFilter(TENANT_FIELD, "==", scope)
        )

    # -- pre-filter rules --------------------------------------------------

    async def get_prefilter_rules(
        self, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        The tenant's stored rules, or None if they have never saved any.

        Addressed by document id rather than by a scoped query, and the id *is*
        the tenant. That makes the read a single get with no index, and makes it
        impossible to end up with two live rule sets for one tenant -- which a
        query-based version would allow and then resolve arbitrarily.
        """
        scope = tenant.resolve(tenant_id)
        snap = await asyncio.to_thread(self._prefilter.document(scope).get)
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        # Defence in depth: the id already scopes this, but a document whose
        # stamp disagrees with its own id means something wrote across tenants
        # and the rules are not safe to screen against.
        if not _owned_by(data, scope):
            return None
        return data

    async def put_prefilter_rules(
        self, rules: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        scope = tenant.resolve(tenant_id)
        payload = dict(rules)
        _stamp_tenant(payload, scope)
        await asyncio.to_thread(self._prefilter.document(scope).set, payload)

    async def hit_rate_limit(
        self, key: str, window_seconds: int, limit: int, tenant_id: str | None = None,
    ) -> bool:
        """
        The same contract as MemoryStore.hit_rate_limit, on Firestore.

        A fixed window, one document per (key, window), counted with an atomic
        server-side Increment and read back. NOT a transaction, deliberately: the
        first version used one, and against the emulator a dozen concurrent
        attempts on one counter queued on its lock for minutes. The console waits
        three seconds for this answer and then gives up -- it used to fall back to
        its per-instance limiter, and now refuses the sign-in (finding R01) -- so
        under exactly the burst this exists to stop, the transactional ledger
        would have turned the limit into an outage. An increment takes no lock and
        does not retry.

        Correctness without a lock: attempt k's increment makes the count at least
        k before it reads, so any attempt that reads a count within the limit was
        one of the first `limit` -- at most `limit` are ever admitted per window.
        Under a race an admitted attempt can read a higher count and be refused;
        that errs toward refusing. Two differences from the sliding window in
        memory: a refused attempt is counted too, and a burst straddling a window
        boundary can see up to twice the limit. Both are bounded by one window.

        Each document carries `expire_at` for the TTL policy on
        login_throttle.expire_at, so finished windows are deleted.
        """
        scope = tenant.resolve(tenant_id)
        now = time.time()
        window = int(now // window_seconds)
        ref = self._throttle.document(_throttle_id(scope, f"{key}\x00{window}"))
        expire_at = datetime.fromtimestamp((window + 2) * window_seconds, timezone.utc)

        def _count() -> bool:
            ref.set({
                "count": self._fs.Increment(1),
                "expire_at": expire_at,
                TENANT_FIELD: scope,
            }, merge=True)
            seen = (ref.get().to_dict() or {}).get("count")
            return not isinstance(seen, int) or seen > limit

        return await asyncio.to_thread(_count)

    async def add_activity(
        self, case_id: str, actor: str, active_ms: int, tenant_id: str | None = None,
    ) -> None:
        """
        The same contract as MemoryStore.add_activity, on Firestore.

        An atomic Increment on one document per (tenant, case, person), like the
        sign-in ledger: two tabs reporting at once both count, with no read and no
        lock. `first_at` is not maintained here -- a merge cannot set a field only
        when it is absent without a read -- and nothing reads it.
        """
        scope = tenant.resolve(tenant_id)
        ref = self._activity.document(_activity_id(scope, case_id, actor))
        payload = {
            "case_id": case_id,
            "actor": actor,
            "active_ms": self._fs.Increment(int(active_ms)),
            "last_at": utcnow(),
            TENANT_FIELD: scope,
        }
        await asyncio.to_thread(ref.set, payload, merge=True)

    async def list_activity(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """
        Every (case, person) running total for the tenant.

        An equality filter on the tenant alone, which Firestore serves from its
        automatic single-field index -- no composite index to deploy. Bounded,
        because this feeds one screen's aggregate.
        """
        scope = tenant.resolve(tenant_id)
        query = self._scoped(self._activity, scope).limit(20_000)
        docs = await asyncio.to_thread(lambda: list(query.stream()))
        return [d.to_dict() or {} for d in docs]

    async def put_boundary(
        self, boundary: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        scope = _stamp_tenant(boundary, tenant_id or boundary.get(TENANT_FIELD))
        await asyncio.to_thread(
            self._boundaries.document(_boundary_key(scope, boundary["boundary_id"])).set,
            boundary,
        )

    async def publish_boundary(
        self, draft: dict[str, Any], tenant_id: str | None = None
    ) -> dict[str, Any]:
        """
        MemoryStore.publish_boundary, inside one Firestore transaction.

        Every read precedes every write, as Firestore requires: the tenant's
        counter and its boundaries are read, then the new boundary is CREATED
        (which fails if the document exists, so nothing is ever overwritten), the
        active ones are superseded, and the counter is advanced. Two concurrent
        publishes contend on the counter document, and the loser's transaction is
        retried from the reads, so it is allotted the next version rather than
        the same one.
        """
        scope = tenant.resolve(tenant_id)
        counter_ref = self._boundary_counters.document(scope)
        query = self._scoped(self._boundaries, scope)

        def _txn() -> dict[str, Any]:
            @self._fs.transactional
            def _do(t: Any) -> dict[str, Any]:
                counter = counter_ref.get(transaction=t)
                counted = _as_version((counter.to_dict() or {}).get("last_version")) if counter.exists else 0
                snaps = list(t.get(query))
                docs = [(s.reference, s.to_dict() or {}) for s in snaps]
                version = max([counted] + [_as_version(d.get("version")) for _, d in docs]) + 1
                active = [(ref, d) for ref, d in docs if d.get("status") == "ACTIVE"]
                current = max(
                    (d for _, d in active), key=lambda d: _as_version(d.get("version")),
                    default=None,
                )

                boundary = _next_boundary(draft, version, current)
                _stamp_tenant(boundary, scope)
                t.create(
                    self._boundaries.document(_boundary_key(scope, boundary["boundary_id"])),
                    boundary,
                )
                now = utcnow()
                for ref, _ in active:
                    t.update(ref, {
                        "status": "SUPERSEDED",
                        "superseded_at": now,
                        "superseded_by": boundary["boundary_id"],
                    })
                t.set(counter_ref, {
                    "last_version": version,
                    "active_boundary_id": boundary["boundary_id"],
                    "updated_at": now,
                    TENANT_FIELD: scope,
                })
                return boundary

            result: dict[str, Any] = self._run_transaction(_do)
            return result

        return await asyncio.to_thread(_txn)

    async def revoke_active_boundary(
        self, revoked_by: str, note: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        scope = tenant.resolve(tenant_id)
        counter_ref = self._boundary_counters.document(scope)
        query = self._scoped(self._boundaries, scope).where(
            filter=self._fs.FieldFilter("status", "==", "ACTIVE")
        )

        def _txn() -> dict[str, Any] | None:
            @self._fs.transactional
            def _do(t: Any) -> dict[str, Any] | None:
                counter = counter_ref.get(transaction=t)
                active = [(s.reference, s.to_dict() or {}) for s in t.get(query)]
                if not active:
                    return None
                now = utcnow()
                change = {
                    "status": "REVOKED",
                    "revoked_at": now,
                    "revoked_by": revoked_by,
                    "revocation_note": note,
                }
                for ref, _ in active:
                    t.update(ref, change)
                if counter.exists:
                    t.update(counter_ref, {"active_boundary_id": None, "updated_at": now})
                newest = max((d for _, d in active), key=lambda d: _as_version(d.get("version")))
                return {**newest, **change}

            result: dict[str, Any] | None = self._run_transaction(_do)
            return result

        return await asyncio.to_thread(_txn)

    async def active_boundary(
        self, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        scope = tenant.resolve(tenant_id)

        def _q() -> dict[str, Any] | None:
            docs = list(
                self._scoped(self._boundaries, scope)
                .where(filter=self._fs.FieldFilter("status", "==", "ACTIVE"))
                .limit(10)
                .stream()
            )
            if not docs:
                return None
            # Highest version wins if a partial publish ever left two ACTIVE.
            newest: dict[str, Any] = max(
                (d.to_dict() for d in docs), key=lambda b: b.get("version", 0)
            )
            return newest

        return await asyncio.to_thread(_q)

    async def list_boundaries(
        self, limit: int = 20, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)

        def _q() -> list[dict[str, Any]]:
            docs = (
                self._scoped(self._boundaries, scope)
                .order_by("version", direction=self._fs.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [d.to_dict() for d in docs]

        return await asyncio.to_thread(_q)

    # -- cases -------------------------------------------------------------

    async def put_case(
        self,
        case: dict[str, Any],
        expected_version: int | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """
        Store a case with optimistic locking using Firestore transactions.

        Args:
            case: The case document to store.
            expected_version: If provided, the update only succeeds if the
                current version matches. Pass None for new cases.
            tenant_id: Owner. An existing case keeps the owner already on the
                stored document, so an update made in another tenant's request
                context raises instead of silently re-homing the case. That
                holds with or without expected_version.

        Raises:
            OptimisticLockError: If expected_version does not match current.
            TenantError: On an attempt to write into another tenant's case.
        """
        case_id = case["case_id"]
        requested = tenant.normalise(tenant_id) or case.get(TENANT_FIELD)

        def _put_with_version() -> None:
            ref = self._cases.document(case_id)

            if expected_version is None:
                # New case or unconditional write -- but never over another
                # tenant's case. This was a blind ref.set(), and case ids derive
                # from the shipment id a document states (CASE-<shipment_id>), so
                # tenant B ingesting a shipment that reused tenant A's id replaced
                # A's case; MemoryStore refused the same write. The owner is read
                # in the same transaction as the write, so a case another writer
                # creates between the check and the write is not replaced unseen:
                # Firestore makes one of the two transactions retry, and the
                # retry reads that case.
                @self._fs.transactional
                def _do(t: Any) -> None:
                    snap = ref.get(transaction=t)
                    _keep_case_owner(case, snap.to_dict() if snap.exists else None, requested)
                    # As before: an unversioned write restarts the count at 1,
                    # where MemoryStore continues it from the stored version.
                    case["_version"] = 1
                    case["_updated_at"] = utcnow()
                    t.set(ref, case)

                self._run_transaction(_do)
                return

            # Optimistic locking with transaction
            txn = self._db.transaction()

            @self._fs.transactional
            def _update_if_version_matches(t, doc_ref):  # type: ignore[no-untyped-def]
                snap = doc_ref.get(transaction=t)
                if not snap.exists:
                    raise OptimisticLockError(case_id, expected_version, 0)

                current = snap.to_dict()
                current_version = current.get("_version", 0)

                if current_version != expected_version:
                    raise OptimisticLockError(
                        case_id, expected_version, current_version
                    )

                # Checked inside the transaction so it cannot race a concurrent
                # write that changes the owner.
                _keep_case_owner(case, current, requested)

                case["_version"] = current_version + 1
                case["_updated_at"] = utcnow()
                t.set(doc_ref, case)

            _update_if_version_matches(txn, ref)

        await asyncio.to_thread(_put_with_version)

    async def get_case(
        self, case_id: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        scope = tenant.resolve(tenant_id)
        snap = await asyncio.to_thread(self._cases.document(case_id).get)
        if not snap.exists:
            return None
        doc = snap.to_dict()
        # None rather than a distinct "exists but is not yours": a different
        # response for a foreign id confirms the id is real and lets a caller
        # enumerate another tenant's case ids.
        return doc if _owned_by(doc, scope) else None

    async def list_cases(
        self, limit: int = 100, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)

        def _q() -> list[dict[str, Any]]:
            docs = (
                self._scoped(self._cases, scope)
                .order_by("created_at", direction=self._fs.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [d.to_dict() for d in docs]

        return await asyncio.to_thread(_q)

    async def find_case_by_client_reference(
        self, client_reference: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        The case an integrator's own reference already produced, if any.

        Backs idempotency on POST /api/v1/compliance/audit; see the MemoryStore
        docstring for why that matters.

        Needs a composite index on (_tenant_id, client_reference, created_at desc)
        -- see infra/firestore.indexes.json. Without it Firestore rejects the
        query rather than running it slowly, which is the better failure: a
        silently slow idempotency check on the hot path would be worse than a loud
        one.

        Scoped by tenant so one customer's reference cannot collide with another's
        and return their audit.
        """
        if not client_reference:
            return None
        scope = tenant.resolve(tenant_id)

        def _q() -> dict[str, Any] | None:
            docs = (
                self._scoped(self._cases, scope)
                .where(
                    filter=self._fs.FieldFilter(
                        "client_reference", "==", client_reference
                    )
                )
                .order_by("created_at", direction=self._fs.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for d in docs:
                first: dict[str, Any] = d.to_dict()
                return first
            return None

        return await asyncio.to_thread(_q)

    async def query_cases(
        self,
        states: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Cases newest-first, optionally filtered by state, queried and
        ordered on the server via a composite index (see
        firestore.indexes.json) rather than fetching a fixed window and
        filtering in Python. That earlier approach is what let cases
        silently fall out of the review queue once enough newer cases
        existed - this replaces it.

        The cursor is the `created_at` of the last row returned; see the
        MemoryStore docstring for why no tie-break key is needed here.
        """
        scope = tenant.resolve(tenant_id)

        def _q() -> tuple[list[dict[str, Any]], str | None]:
            q = self._scoped(self._cases, scope)
            if states:
                q = q.where(
                    filter=self._fs.FieldFilter("state", "in", list(states))
                )
            q = q.order_by("created_at", direction=self._fs.Query.DESCENDING)
            if cursor:
                q = q.start_after({"created_at": cursor})
            docs = list(q.limit(limit).stream())
            items = [d.to_dict() for d in docs]
            next_cursor = (
                items[-1].get("created_at") if len(items) == limit else None
            )
            return items, next_cursor

        return await asyncio.to_thread(_q)

    async def count_by_state(
        self, states: tuple[str, ...], tenant_id: str | None = None
    ) -> dict[str, int]:
        """
        Exact counts across the whole collection using count() aggregation
        (one per state - Firestore has no GROUP BY). Aggregation queries are
        billed as a single read regardless of how many documents match, so
        this is cheap even at 100k+ cases; callers should still cache it
        briefly rather than call it on every poll.
        """
        scope = tenant.resolve(tenant_id)

        def _q() -> dict[str, int]:
            counts: dict[str, int] = {}
            for state in states:
                agg = (
                    self._scoped(self._cases, scope)
                    .where(filter=self._fs.FieldFilter("state", "==", state))
                    .count()
                    .get()
                )
                counts[state] = agg[0][0].value
            return counts

        return await asyncio.to_thread(_q)

    async def sum_rollups(
        self,
        tenant_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """
        Global token/latency/cost totals via sum() aggregation over the
        per-case rollup fields written by the orchestrator on every step.
        sum() cannot reach into the `steps[]` array, which is exactly why
        those totals are denormalized onto the case document at write time.

        Tenant-scoped, which makes this the basis for per-customer billing: an
        unscoped total would bill every customer for the whole platform's spend.

        `since` and `until` bound the window by `created_at`, half-open:
        since <= created_at < until. Same semantics as the memory backend, which is
        the point -- the two are compared for parity by the test suite, and a
        billing figure that depended on which backend answered would be worse than
        no figure.

        REQUIRES TWO COMPOSITE INDEXES PER AGGREGATED FIELD, not one:

            unbounded   (_tenant_id ASC, <field> ASC)
            windowed    (_tenant_id ASC, created_at ASC, <field> ASC)

        Firestore needs the aggregated field in the index alongside the filtered ones,
        and an index prefix has to match the query's filters -- so the three-field index
        does NOT satisfy the unbounded query, which never constrains `created_at`. With
        eight aggregations that is sixteen indexes. Create them with
        infra/monitoring/create_billing_indexes.py.

        THIS DOCSTRING PREVIOUSLY SAID THE OPPOSITE, and the correction is the useful
        part. It read: "None of them are needed for the unbounded call, so an existing
        deployment keeps working until the first windowed request." The first clause is
        false. The unbounded call needs the two-field indexes; they already existed from
        when unbounded billing was built, so nothing broke and the false statement went
        unchallenged.

        Adding the Tavily sums proved it. The windowed indexes were created, every
        windowed test passed, and the *unbounded* GET /billing/usage returned 500 in
        production with "The query requires an index" naming
        (_tenant_id, _tavily_searches). The windowed path was the one covered by tests
        and the unbounded path was the one every console page calls.

        tests/test_billing_period.py extracts every summed field name from this method
        by pattern and asserts each has BOTH index shapes defined, so a new aggregation
        cannot repeat this.
        """
        scope = tenant.resolve(tenant_id)

        def _q() -> dict[str, Any]:
            base = self._scoped(self._cases, scope)
            # Applied to the shared `base` before any aggregation is built, so all
            # five sums see the same window. Adding it per-aggregation would be five
            # chances for one of them to report lifetime totals inside a period
            # report, which reads as a plausible number and is wrong.
            if since is not None:
                base = base.where(
                    filter=self._fs.FieldFilter("created_at", ">=", since)
                )
            if until is not None:
                base = base.where(
                    filter=self._fs.FieldFilter("created_at", "<", until)
                )

            agg_input = base.sum("_input_tokens").get()
            agg_output = base.sum("_output_tokens").get()
            agg_calls = base.sum("_agent_calls").get()
            agg_cost = base.sum("_estimated_cost_usd").get()
            agg_latency = base.sum("_sum_latency_ms").get()
            agg_tavily = base.sum("_tavily_searches").get()
            agg_tavily_cached = base.sum("_tavily_cached").get()
            agg_tavily_billable = base.sum("_tavily_billable").get()

            calls = agg_calls[0][0].value or 0
            latency_sum = agg_latency[0][0].value or 0
            tavily = int(agg_tavily[0][0].value or 0)
            tavily_cached = int(agg_tavily_cached[0][0].value or 0)
            tavily_billable = int(agg_tavily_billable[0][0].value or 0)
            return {
                "agent_calls": calls,
                "total_input_tokens": agg_input[0][0].value or 0,
                "total_output_tokens": agg_output[0][0].value or 0,
                "estimated_cost_usd": round(agg_cost[0][0].value or 0.0, 6),
                "avg_latency_ms": int(latency_sum / calls) if calls else 0,
                "tavily_searches": tavily,
                "tavily_cached": tavily_cached,
                "tavily_billable": tavily_billable,
            }

        return await asyncio.to_thread(_q)

    async def count_by_cleared_by(
        self, tenant_id: str | None = None
    ) -> dict[str, int]:
        """
        Count cases by cleared_by field (rules vs ai).
        Returns counts for Cost-Aware Hybrid Architecture KPI tiles.
        
        Firestore aggregation cannot GROUP BY multiple fields, so we run
        separate count() queries for each cleared_by value.
        """
        scope = tenant.resolve(tenant_id)

        def _q() -> dict[str, int]:
            counts = {"rules": 0, "ai": 0, "unknown": 0, "total_auto_cleared": 0}
            cleared = self._scoped(self._cases, scope).where(
                filter=self._fs.FieldFilter("state", "==", "AUTO_CLEARED")
            )

            counts["total_auto_cleared"] = cleared.count().get()[0][0].value
            counts["rules"] = (
                cleared.where(
                    filter=self._fs.FieldFilter("cleared_by", "==", "rules")
                ).count().get()[0][0].value
            )
            counts["ai"] = (
                cleared.where(
                    filter=self._fs.FieldFilter("cleared_by", "==", "ai")
                ).count().get()[0][0].value
            )
            # Unknown = total - rules - ai
            counts["unknown"] = (
                counts["total_auto_cleared"] - counts["rules"] - counts["ai"]
            )
            return counts

        return await asyncio.to_thread(_q)

    async def claim_next_pending(
        self, states: tuple[str, ...], tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """
        Claim inside a Firestore transaction. This is what makes the worker
        safe against duplicate Pub/Sub delivery and against more than one
        Cloud Run instance being alive during a rollout.

        Readiness is decided in Python rather than in the query. Expressing
        "unclaimed OR lease expired" plus a state filter plus ordering would
        need a hand-built composite index, and the whole point of this service
        is that it runs against a bare Firestore database with no setup step.

        `tenant_id=None` means "any tenant" here, not "the default tenant", and
        this is the only method where that is the intended contract -- see the
        MemoryStore implementation for why, and for what changed about the claim
        that it was "the one place".

        Note one asymmetry with MemoryStore that matters for the all-tenant case:
        _scoped() is skipped entirely when scope is None, so this reaches
        documents that have no `_tenant_id` at all. A scoped call does not, since
        a Firestore equality filter does not match a missing field. That is the
        right way round -- the worker must be able to drain legacy unstamped
        cases -- but it means a per-tenant caller will not see them.
        """
        scope = tenant.normalise(tenant_id)
        now = utcnow()
        now_dt = datetime.now(timezone.utc)

        def _claim() -> dict[str, Any] | None:
            source = self._cases if scope is None else self._scoped(self._cases, scope)
            candidates = list(source.limit(60).stream())
            candidates.sort(key=lambda d: (d.to_dict() or {}).get("created_at", ""))

            for doc in candidates:
                data = doc.to_dict() or {}
                if not is_claimable(data, states, now, now_dt):
                    continue

                txn = self._db.transaction()

                @self._fs.transactional
                def _take(t, ref):  # type: ignore[no-untyped-def]
                    snap = ref.get(transaction=t)
                    cur = snap.to_dict()
                    if not cur:
                        return None
                    # Re-check under the transaction: another worker may have
                    # taken it between the query and here.
                    if cur.get("claimed") and not _lease_expired(cur, now_dt):
                        return None
                    t.update(ref, {"claimed": True, "claimed_at": now})
                    cur["claimed"] = True
                    cur["claimed_at"] = now
                    return cur

                taken: dict[str, Any] | None = _take(txn, doc.reference)
                if taken:
                    return taken
            return None

        return await asyncio.to_thread(_claim)

    async def release_case(
        self, case_id: str, tenant_id: str | None = None
    ) -> None:
        # resolve(), not normalise(): an absent tenant must raise under multi-tenancy, not skip the
        # ownership check below (the hole MemoryStore.release_case closed).
        scope = tenant.resolve(tenant_id)
        # Read first so one tenant cannot un-claim another's case, and so a case that is gone (a
        # board reset can delete one a worker still holds) is let go of quietly, as MemoryStore does.
        snap = await asyncio.to_thread(self._cases.document(case_id).get)
        if not snap.exists or not _owned_by(snap.to_dict(), scope):
            return
        await asyncio.to_thread(
            self._cases.document(case_id).update, {"claimed": False}
        )

    # -- append-only feeds -------------------------------------------------

    async def add_event(
        self, event: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        _stamp_tenant(event, tenant_id or event.get(TENANT_FIELD))
        await asyncio.to_thread(self._events.document(event["event_id"]).set, event)

    async def list_events(
        self, limit: int = 80, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)

        def _q() -> list[dict[str, Any]]:
            docs = (
                self._scoped(self._events, scope)
                .order_by("at", direction=self._fs.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [d.to_dict() for d in docs]

        return await asyncio.to_thread(_q)

    async def query_events(
        self, cursor: str | None = None, limit: int = 80,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        scope = tenant.resolve(tenant_id)

        def _q() -> tuple[list[dict[str, Any]], str | None]:
            q = self._scoped(self._events, scope).order_by(
                "at", direction=self._fs.Query.DESCENDING
            )
            if cursor:
                q = q.start_after({"at": cursor})
            docs = list(q.limit(limit).stream())
            items = [d.to_dict() for d in docs]
            next_cursor = items[-1].get("at") if len(items) == limit else None
            return items, next_cursor

        return await asyncio.to_thread(_q)

    async def add_audit(
        self, entry: dict[str, Any], tenant_id: str | None = None
    ) -> None:
        """Append an audit entry. Audit entries are immutable once written."""
        if "audit_id" not in entry:
            entry["audit_id"] = new_id("AUD")
        _stamp_tenant(entry, tenant_id or entry.get(TENANT_FIELD))
        entry["_immutable"] = True
        entry["_created_at"] = utcnow()
        await asyncio.to_thread(self._audit.document(entry["audit_id"]).set, entry)

    async def add_audit_once(
        self, entry: dict[str, Any], tenant_id: str | None = None
    ) -> bool:
        """
        The same contract as MemoryStore.add_audit_once: one atomic create, so a second
        writer of the same audit_id is refused by the database rather than adding a row.
        """
        if "audit_id" not in entry:
            raise ValueError("add_audit_once needs an audit_id derived from what makes the row unique")
        from google.api_core.exceptions import AlreadyExists

        _stamp_tenant(entry, tenant_id or entry.get(TENANT_FIELD))
        entry["_immutable"] = True
        entry["_created_at"] = utcnow()
        try:
            await asyncio.to_thread(self._audit.document(entry["audit_id"]).create, entry)
        except AlreadyExists:
            return False
        return True

    async def update_audit(
        self, audit_id: str, updates: dict[str, Any],
        tenant_id: str | None = None,
    ) -> None:
        """Blocked: audit entries are immutable."""
        raise AuditImmutabilityError("update", audit_id)

    async def delete_audit(
        self, audit_id: str, tenant_id: str | None = None
    ) -> None:
        """Blocked: audit entries are immutable."""
        raise AuditImmutabilityError("delete", audit_id)

    async def list_audit(
        self, limit: int = 80, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        scope = tenant.resolve(tenant_id)

        def _q() -> list[dict[str, Any]]:
            docs = (
                self._scoped(self._audit, scope)
                .order_by("at", direction=self._fs.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [d.to_dict() for d in docs]

        return await asyncio.to_thread(_q)

    async def query_audit(
        self,
        case_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        Audit entries newest-first with exact field filters, queried on the
        server via composite indexes (see firestore.indexes.json) instead of
        filtering a fixed-size fetched window - the earlier approach made a
        search for an older case return an empty result that looked like
        missing data rather than an under-sized window.

        Firestore is asked for one equality filter, the first of case_id, action and status that is given, which the
        declared composite indexes cover; any others are checked here, as MemoryStore checks all three.
        Each declared index now carries _tenant_id as its leading field.
        """
        scope = tenant.resolve(tenant_id)

        def _q() -> tuple[list[dict[str, Any]], str | None]:
            q = self._scoped(self._audit, scope)
            if case_id:
                q = q.where(filter=self._fs.FieldFilter("case_id", "==", case_id))
            elif action:
                q = q.where(filter=self._fs.FieldFilter("action", "==", action))
            elif status:
                q = q.where(filter=self._fs.FieldFilter("status", "==", status))
            q = q.order_by("at", direction=self._fs.Query.DESCENDING)
            filters = {n: v for n, v in (("case_id", case_id), ("action", action), ("status", status)) if v}
            # The query above carries the first of them; the others are checked here, reading on until `limit` rows match,
            # so a combined filter answers as it does on MemoryStore. A single filter asks for exactly `limit` rows.
            size = limit if len(filters) <= 1 else max(limit, _AUDIT_SCAN_PAGE)
            items: list[dict[str, Any]] = []
            after = cursor
            for _ in range(_AUDIT_SCAN_PAGES):
                page = q.start_after({"at": after}) if after else q
                batch = [d.to_dict() for d in page.limit(size).stream()]
                for row in batch:
                    if all(row.get(n) == v for n, v in filters.items()):
                        items.append(row)
                        if len(items) == limit:
                            return items, row.get("at")
                if len(batch) < size:
                    return items, None
                after = batch[-1].get("at")
            # Stopped at the most one call reads: the cursor says where to go on, so a paging caller is not told this is the end.
            return items, after

        return await asyncio.to_thread(_q)

    async def reset(self, tenant_id: str | None = None) -> int:
        """
        Clear cases and events but preserve audit log (immutable).

        Exists so a demo run starts from a clean board; without it, terminal
        cases from earlier runs stay on the dashboard forever.

        Tenant-scoped, because an unscoped wipe here would let one customer
        clear another customer's board -- which is the same class of mistake as
        reading their data, with worse consequences.
        """
        scope = tenant.resolve(tenant_id)

        def _wipe() -> int:
            removed = 0
            # AUDIT LOG IS DELIBERATELY NOT CLEARED - immutability guarantee
            for coll in (self._cases, self._events):
                while True:
                    batch = list(
                        self._scoped(coll, scope).limit(300).stream()
                    )
                    if not batch:
                        break
                    writer = self._db.batch()
                    for doc in batch:
                        writer.delete(doc.reference)
                    writer.commit()
                    removed += len(batch)
            return removed

        return await asyncio.to_thread(_wipe)

    async def backfill_rollups(self, tenant_id: str | None = None) -> dict[str, int]:
        """
        One-off migration: compute `_agent_calls`/`_input_tokens`/
        `_output_tokens`/`_estimated_cost_usd`/`_sum_latency_ms` from
        `steps[]` for every case written before those rollup fields
        existed, so `sum_rollups()`/`global_metrics()` reflect full
        history rather than silently reading 0 for pre-migration cases.
        Idempotent: a case that already has `_agent_calls` is left alone.

        Scoped to one tenant. The signature accepted `tenant_id` and ignored it,
        which made this the one write path that reached every customer's
        documents: an operator running the migration for their own tenant
        rewrote fields on every tenant in the collection.

        Filtered in Python via _owned_by rather than with _scoped(). The two do
        not agree about a document that has no `_tenant_id`: _scoped() builds an
        equality filter, and a Firestore equality filter does not match documents
        missing the field, while _owned_by adopts them into the single implicit
        tenant. Scoping the query would therefore have skipped exactly the
        documents this migration exists to fix -- a case old enough to lack
        `_agent_calls` is old enough to lack `_tenant_id` too -- and reported
        "updated: 0" as a success.

        The cost is that this reads the whole collection. For a migration an
        operator runs by hand that is the right trade, and it is why this is not
        a request-path method. Note the scoping still holds where it matters: a
        run under a real tenant id leaves unstamped legacy records alone, because
        _owned_by reports their owner as the implicit tenant, not as the caller's.
        """
        # Local import, kept local: lineage's own tenant_usage() reads the store back
        # through get_store(), so a module-level import here would be a cycle waiting
        # for someone to move that call out of its function.
        from vf_logistics import lineage

        scope = tenant.resolve(tenant_id)

        def _run() -> dict[str, int]:
            updated = 0
            skipped = 0
            for doc in self._cases.stream():
                case = doc.to_dict() or {}
                if not _owned_by(case, scope):
                    skipped += 1
                    continue
                if case.get("is_marker") or "_agent_calls" in case:
                    skipped += 1
                    continue

                calls = 0
                input_tokens = 0
                output_tokens = 0
                cost = 0.0
                latency_sum = 0
                for step in case.get("steps", []) or []:
                    calls += 1
                    it = step.get("input_tokens", 0) or 0
                    ot = step.get("output_tokens", 0) or 0
                    input_tokens += it
                    output_tokens += ot
                    latency = step.get("latency_ms")
                    if isinstance(latency, int):
                        latency_sum += latency
                    # One pricing implementation. See lineage.cost_usd() -- this
                    # was the fourth inline copy of the same arithmetic.
                    cost += lineage.cost_usd(step.get("model"), it, ot)

                doc.reference.update(
                    {
                        "_agent_calls": calls,
                        "_input_tokens": input_tokens,
                        "_output_tokens": output_tokens,
                        "_estimated_cost_usd": round(cost, 6),
                        "_sum_latency_ms": latency_sum,
                    }
                )
                updated += 1
            return {"updated": updated, "skipped": skipped}

        return await asyncio.to_thread(_run)


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

_store: MemoryStore | FirestoreStore | None = None
_init_note = ""


def store_backend() -> str:
    """
    The configured backend name, defaulting to firestore.

    Named so "is this real data or a fixture" has one answer. app.py used to read
    STORE_BACKEND with a default of "" while this module defaulted to firestore,
    so with the variable unset the store wrote to Firestore while app.py believed
    it was not in production -- and _safe_error returned raw exception text to
    clients as a result.

    tenant.py and auth.py still read the variable directly rather than calling
    this, because importing store from either would be circular. They use the
    same "firestore" default, which is the part that has to agree.
    """
    return os.getenv("STORE_BACKEND", "firestore").strip().lower()


def get_store() -> Any:
    """
    Return the process-wide store, building it on first use.

    Firestore is preferred, but a failure here must not take the demo down, so
    we fall back to the in-memory backend and record why on the health
    endpoint rather than crashing the container at boot.
    """
    global _store, _init_note
    if _store is not None:
        return _store

    requested = store_backend()

    if requested == "memory":
        _store = MemoryStore()
        _init_note = "memory backend requested via STORE_BACKEND"
        return _store

    try:
        # None when no project can be resolved: the client then raises, and the
        # fallback below records that as the reason rather than writing anywhere.
        _store = FirestoreStore(gcp_project())
        _init_note = "firestore connected"
    except Exception as exc:  # noqa: BLE001 - degrade, never fail to boot
        _store = MemoryStore()
        _init_note = f"firestore unavailable ({type(exc).__name__}: {exc}); using memory"

    return _store


def store_status() -> dict[str, str]:
    store = get_store()
    return {"backend": store.backend, "detail": _init_note}
