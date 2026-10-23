"""
Requests for documents, and the signed link a shipper answers them through.

Plan 1 of the 12/10 restructure proposal, step 2. A reviewer who cannot decide a case
from what is in front of them used to press "Request info" and write a sentence; the
case then sat in PENDING_HUMAN with nothing recording who was asked for what, or
whether it came. Here the request is a record of its own, inside the case:

    case["verification"] = {
        "evidence_version": 0,   # rises when new evidence arrives (step 3)
        "resume_state": None,    # where the case goes back to when no request is open
        "requests": [ {request_id, finding_codes, documents, owner, status, ...} ],
        "documents": [], "evaluations": [], "approvals": [], "executions": [],
    }

Embedded rather than stored apart so it shares the case's optimistic lock and its
tenant: a request and the state change it causes are one write, and cannot land
half-way. The cost is the 1 MiB Firestore document limit, so what is stored here is
metadata and extracted facts, never document bytes (those go to document_store).

THE LINK IS NOT IN THE CASE

The case JSON is readable by a viewer. A link stored in it would hand every viewer
the ability to submit "evidence" for that case, so the case keeps only a
fingerprint of each link issued and the link itself leaves exactly once, in the
response to the reviewer who asked. Lose it and the reviewer issues a new one,
which retires the old (the request's `link_seq` is in the token and compared on use).

THE CHANNEL IS A SANDBOX

Nothing is emailed. "sent" means a link was issued to a reviewer, who passes it on;
the request records that and no more. The console says so wherever it shows the
status, because a status that read "sent" with no sending would be the AI-written
claim the proposal asked this loop not to make.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import time
from typing import Any

from vf_logistics import auth, dossier, dual_approval, tenant
from vf_logistics.store import OptimisticLockError, get_store, new_id, utcnow

AWAITING_EVIDENCE = "AWAITING_EVIDENCE"

# States a reviewer can ask for documents from: the ones that wait on a person, and
# AWAITING_EVIDENCE itself so a second request can be added to an open one.
REQUESTABLE_STATES = ("PENDING_HUMAN", "HELD_FOR_REVIEW", "ESCALATED", AWAITING_EVIDENCE, "APPROVED")

# N05: the states a case can be re-evaluated from. The console mirrors this list
# (frontend/src/lib/types.ts REEVALUABLE_STATES); a test reads both so they cannot drift.
REEVALUABLE = ("AWAITING_EVIDENCE", "HELD_FOR_REVIEW", "ESCALATED", "PENDING_HUMAN")

OPEN_STATUSES = ("OPEN", "SENT")
LINK_TTL_S = 7 * 24 * 3600
# How long an execution claim is exclusive to the executor who made it. After this a
# different person may resume a claim whose process died between claim and confirm.
CLAIM_LEASE_S = 300
MAX_TOKEN_CHARS = 1024
MAX_FIELD_CHARS = 200
MAX_NOTE_CHARS = 1000
MAX_FINDING_CODES = 20

# Domain separation: the key that signs these links is derived from the console
# session secret but is not it, so a console session token can never be replayed as
# an evidence link, nor a link as a session.
_KEY_CONTEXT = b"evidence-response\x00"


class EvidenceError(Exception):
    """A refusal with the HTTP status the route should answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------

def empty_verification() -> dict[str, Any]:
    return {
        "evidence_version": 0,
        "resume_state": None,
        "requests": [],
        "documents": [],
        "evaluations": [],
        "approvals": [],
        "executions": [],
    }


def verification_of(case: dict[str, Any]) -> dict[str, Any]:
    """The case's verification record, created in place if the case predates it."""
    record = case.get("verification")
    if not isinstance(record, dict):
        record = empty_verification()
        case["verification"] = record
    for key, value in empty_verification().items():
        record.setdefault(key, value)
    return record


def open_requests(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        r for r in verification_of(case)["requests"]
        if isinstance(r, dict) and r.get("status") in OPEN_STATUSES
    ]


def find_request(case: dict[str, Any], request_id: str) -> dict[str, Any] | None:
    for r in verification_of(case)["requests"]:
        if isinstance(r, dict) and r.get("request_id") == request_id:
            return r
    return None


# --------------------------------------------------------------------------
# The link
# --------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    secret = auth.console_session_secret()
    if secret is None:
        raise EvidenceError(
            "This service has no VF_SESSION_SECRET (or one under 32 characters), so it "
            "cannot sign a link for a shipper to answer through.",
            503,
        )
    return hmac.new(secret.encode("utf-8"), _KEY_CONTEXT, hashlib.sha256).digest()


def mint_token(scope: str, case_id: str, request_id: str, seq: int, now: float | None = None, doc_type: str | None = None) -> tuple[str, int]:
    """A signed link token and its expiry (epoch seconds)."""
    expires = int((time.time() if now is None else now) + LINK_TTL_S)
    payload_dict: dict[str, Any] = {"v": 1, "t": scope, "c": case_id, "r": request_id, "s": seq, "e": expires}
    if doc_type:
        payload_dict["d"] = doc_type
    payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = _b64(payload)
    signature = _b64(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}", expires


def read_token(token: Any, now: float | None = None) -> dict[str, Any] | None:
    """
    The claims of a valid, unexpired token, else None.

    One answer for every failure -- malformed, forged, expired, wrong version -- so
    the route cannot be used to learn which of them it was.
    """
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
        return None
    body, dot, signature = token.partition(".")
    if not dot or not body or not signature or "." in signature:
        return None
    try:
        expected = hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        claims = json.loads(_unb64(body))
    except EvidenceError:
        return None
    except Exception:
        return None
    if not isinstance(claims, dict) or claims.get("v") != 1:
        return None
    expires = claims.get("e")
    if not isinstance(expires, int) or isinstance(expires, bool):
        return None
    if (time.time() if now is None else now) >= expires:
        return None
    for field in ("t", "c", "r"):
        if not isinstance(claims.get(field), str) or not claims[field]:
            return None
    if not isinstance(claims.get("s"), int) or isinstance(claims.get("s"), bool):
        return None
    return claims


def link_path(token: str) -> str:
    """Where the shipper opens it. The console turns this into an absolute address."""
    return f"/respond/{token}"


def _issue(
    request: dict[str, Any], scope: str, case_id: str, issued_by: str, now: str,
) -> tuple[str, int]:
    """Mint a new link for `request`, retiring the previous one. Mutates `request`."""
    request["link_seq"] = int(request.get("link_seq") or 0) + 1
    token, expires = mint_token(scope, case_id, request["request_id"], request["link_seq"])
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))
    request["status"] = "SENT"
    request.setdefault("sent_at", now)
    request["link_expires_at"] = expires_at
    request.setdefault("outbox", []).append({
        "channel": "sandbox_link",
        "seq": request["link_seq"],
        "issued_at": now,
        "issued_by": issued_by,
        "expires_at": expires_at,
        # Enough to say which link was which, not enough to use one.
        "token_fingerprint": hashlib.sha256(token.encode("ascii")).hexdigest()[:12],
    })
    return token, expires


def _document_links(
    request: dict[str, Any], scope: str, case_id: str, doc_types: list[str],
) -> list[dict[str, str]]:
    """
    N03: one link per document type, each token naming the type it is for. They carry
    the request's CURRENT link_seq, so the next `_issue` retires them along with the
    request-level token. Call it after `_issue`, never before.
    """
    links = []
    for dt in doc_types:
        dt_token, _ = mint_token(
            scope, case_id, str(request["request_id"]), int(request["link_seq"]), doc_type=dt,
        )
        links.append({"doc_type": dt, "token": dt_token, "path": link_path(dt_token)})
    return links


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

def _clean_text(value: Any, limit: int, what: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise EvidenceError(f"{what} must be text")
    text = value.strip()
    if required and not text:
        raise EvidenceError(f"{what} is required")
    if len(text) > limit:
        raise EvidenceError(f"{what} must be {limit} characters or fewer")
    return text


def _string_list(value: Any, what: str, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise EvidenceError(f"{what} must be a list of names")
    if len(value) > limit:
        raise EvidenceError(f"{what} may name at most {limit}")
    seen: list[str] = []
    for item in value:
        if item not in seen:
            seen.append(item)
    return seen


def _documents_each_finding_asks_for(case: dict[str, Any]) -> dict[str, list[str]]:
    """finding code -> the document types the verifier says would answer it."""
    out: dict[str, list[str]] = {}
    for finding in (case.get("validation") or {}).get("findings") or []:
        needed = (finding.get("measured") or {}).get("documents_needed")
        if isinstance(needed, list) and needed:
            out[str(finding.get("code"))] = [str(d) for d in needed]
    return out


def usable_document(doc: Any) -> bool:
    """A received paper that can count as evidence: not blocked by the screen, and read
    by the model into at least one field. Anything else arrived but proves nothing."""
    return (
        isinstance(doc, dict)
        and not doc.get("blocked")
        and isinstance(doc.get("extracted"), dict)
        and bool(doc["extracted"])
    )


def _received_types(case: dict[str, Any]) -> set[str]:
    # N04: only papers that can count. A blocked or unreadable copy must not stop a
    # later request from asking for that document again.
    return {
        str(d.get("doc_type")) for d in verification_of(case)["documents"] if usable_document(d)
    }


async def _write(
    case: dict[str, Any], expected_version: Any, tenant_id: str | None,
) -> None:
    try:
        await get_store().put_case(case, expected_version=expected_version, tenant_id=tenant_id)
    except OptimisticLockError:
        raise EvidenceError(
            "The case changed while this was being done. Reload it and try again.", 409,
        ) from None


async def _audit(
    case_id: str, action: str, actor: str, actor_auth: str | None,
    detail: dict[str, Any], tenant_id: str | None,
) -> None:
    await get_store().add_audit({
        "audit_id": new_id("audit"),
        "case_id": case_id,
        "action": action,
        "status": "done",
        "actor": actor,
        "actor_auth": actor_auth,
        "detail": detail,
        "at": utcnow(),
    }, tenant_id=tenant_id)


def public_request(request: dict[str, Any]) -> dict[str, Any]:
    """A request as the console shows it. No token is ever in a stored request."""
    return {k: v for k, v in request.items() if k != "token"}


async def request_evidence(
    case_id: str,
    requested_by: str,
    *,
    finding_codes: Any = None,
    documents: Any = None,
    owner: Any = None,
    note: Any = None,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Ask the shipper for documents on one or more findings of a case.

    Returns {"request", "token", "path", "state"}. The token is in the return value
    and nowhere else: the caller passes it to the reviewer once.
    """
    if not requested_by:
        raise EvidenceError("A request for documents is made by a named person")
    codes = _string_list(finding_codes, "finding_codes", MAX_FINDING_CODES)
    wanted = _string_list(documents, "documents", len(dossier.EVIDENCE_CATALOGUE))
    owner_name = _clean_text(owner, MAX_FIELD_CHARS, "owner") or requested_by
    note_text = _clean_text(note, MAX_NOTE_CHARS, "note")
    for doc in wanted:
        if doc not in dossier.EVIDENCE_CATALOGUE:
            raise EvidenceError(f"'{doc}' is not a document this service can ask for")

    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)

    state = case.get("state")
    if state not in REQUESTABLE_STATES:
        raise EvidenceError(f"A case in state {state} is not waiting on a person, so documents cannot be requested.", 409)

    asked = _documents_each_finding_asks_for(case)
    if not codes:
        codes = list(asked)
    if not codes:
        raise EvidenceError("No finding on this case asks for documents. Name the finding the request is for.")
    unknown = [c for c in codes if c not in asked]
    if unknown:
        raise EvidenceError(
            f"{', '.join(unknown)} is not a finding on this case that asks for documents."
        )

    record = verification_of(case)
    received = _received_types(case)
    if not wanted:
        # The minimum: what the chosen findings need, less what is already in hand.
        for code in codes:
            for doc in asked[code]:
                if doc not in wanted and doc not in received:
                    wanted.append(doc)
    if not wanted:
        raise EvidenceError("Everything those findings need has already been received.", 409)

    for existing in open_requests(case):
        if sorted(existing["documents"]) == sorted(wanted) and set(existing["finding_codes"]) == set(codes):
            raise EvidenceError(
                f"Those documents are already requested ({existing['request_id']}). "
                "Issue a new link on that request instead.",
                409,
            )

    now = utcnow()
    scope = tenant.resolve(tenant_id)
    request = {
        "request_id": new_id("evreq"),
        "finding_codes": codes,
        "documents": wanted,
        "owner": owner_name,
        "note": note_text,
        "status": "OPEN",
        "channel": "sandbox_link",
        "created_by": requested_by,
        "created_at": now,
        "sent_at": None,
        "received_at": None,
        "closed_at": None,
    }
    # N03: mint one link per doc_type so the token carries which document to expect.
    token, _ = _issue(request, scope, case_id, requested_by, now)
    links = _document_links(request, scope, case_id, wanted)
    record["requests"].append(request)

    if state != AWAITING_EVIDENCE:
        record["resume_state"] = state
    case["state"] = AWAITING_EVIDENCE
    case["updated_at"] = now
    case["claimed"] = False
    # Counted with the other requests for information, so Outcomes' "rounds" and the
    # worklist's "waiting since" read one history rather than two.
    labels = ", ".join(dossier.evidence_label(d) for d in wanted)
    case.setdefault("reviews", []).append({
        "action": "request_info",
        "reviewer": requested_by,
        "note": note_text or f"Requested: {labels}",
        "at": now,
        "state_before": state,
        "state_after": AWAITING_EVIDENCE,
        "request_id": request["request_id"],
    })

    await _write(case, expected_version, tenant_id)
    await _audit(case_id, "evidence_requested", requested_by, actor_auth, {
        "request_id": request["request_id"], "finding_codes": codes, "documents": wanted,
        "owner": owner_name, "channel": "sandbox_link",
    }, tenant_id)
    return {
        "request": public_request(request),
        "token": token,
        "path": link_path(token),
        # One link per requested document; the token says which one it is for.
        "links": links,
        "state": AWAITING_EVIDENCE,
    }


async def reissue_link(
    case_id: str,
    request_id: str,
    issued_by: str,
    *,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    A new link for an open request. The old one stops working.

    Returns {"request", "token", "path", "links"}. `links` has one typed link per
    document the request still waits for (N03), the same shape request_evidence
    returns. Send those: the top-level `token` has no document type, so an upload
    through it is filed as the first type still missing, whatever it really is.
    """
    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)
    request = find_request(case, request_id)
    if request is None:
        raise EvidenceError("request not found", 404)
    if request.get("status") not in OPEN_STATUSES:
        raise EvidenceError(f"That request is {str(request.get('status')).lower()}; no link can be issued for it.", 409)

    now = utcnow()
    scope = tenant.resolve(tenant_id)
    token, _ = _issue(request, scope, case_id, issued_by, now)
    received_docs = request.get("received_docs") or {}
    still_missing = [d for d in request.get("documents") or [] if d not in received_docs]
    links = _document_links(request, scope, case_id, still_missing)
    case["updated_at"] = now
    await _write(case, expected_version, tenant_id)
    await _audit(case_id, "evidence_link_issued", issued_by, actor_auth, {
        "request_id": request_id, "seq": request["link_seq"],
    }, tenant_id)
    return {
        "request": public_request(request),
        "token": token,
        "path": link_path(token),
        "links": links,
    }


async def cancel_request(
    case_id: str,
    request_id: str,
    cancelled_by: str,
    reason: Any,
    *,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Withdraw an open request, with a reason.

    Without this a shipper who never answers leaves the case in AWAITING_EVIDENCE for
    good: the decision routes refuse a case in that state. When the last open request
    closes, the case goes back to the state it was in before the first was made.
    """
    why = _clean_text(reason, MAX_NOTE_CHARS, "reason", required=True)
    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)
    request = find_request(case, request_id)
    if request is None:
        raise EvidenceError("request not found", 404)
    if request.get("status") not in OPEN_STATUSES:
        raise EvidenceError(f"That request is already {str(request.get('status')).lower()}.", 409)

    now = utcnow()
    request["status"] = "CANCELLED"
    request["closed_at"] = now
    request["cancel_reason"] = why
    request["cancelled_by"] = cancelled_by
    record = verification_of(case)
    if case.get("state") == AWAITING_EVIDENCE and not open_requests(case):
        case["state"] = record.get("resume_state") or "HELD_FOR_REVIEW"
        record["resume_state"] = None
    case["updated_at"] = now

    await _write(case, expected_version, tenant_id)
    await _audit(case_id, "evidence_request_cancelled", cancelled_by, actor_auth, {
        "request_id": request_id, "reason": why, "state_after": case["state"],
    }, tenant_id)
    return {"request": public_request(request), "state": case["state"]}


# --------------------------------------------------------------------------
# Receiving evidence (step 3)
# --------------------------------------------------------------------------

MAX_EVIDENCE_MB = 20
SUPPORTED_EVIDENCE_MIME = frozenset({
    "application/pdf", "image/png", "image/jpeg", "image/webp",
})


def _supersede_approvals(record: dict[str, Any], new_version: int, now: str) -> list[str]:
    """
    Any VALID approval with an evidence_version lower than `new_version` is stale:
    the person approved on facts that are no longer current. Returns the ids that
    were superseded.
    """
    hit: list[str] = []
    for approval in record.get("approvals") or []:
        if isinstance(approval, dict) and approval.get("status") == "VALID":
            if (approval.get("evidence_version") or 0) < new_version:
                approval["status"] = "SUPERSEDED"
                approval["superseded_at"] = now
                approval["superseded_reason"] = f"evidence v{new_version} received"
                hit.append(str(approval.get("approval_id") or ""))
    return hit


async def receive_evidence(
    token: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    *,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """
    Accept a document submitted through a signed link.

    This is the one public route in the loop. It has no session and no API key:
    the token IS the credential, and it is single-purpose (one case, one request,
    one sequence). Everything else is checked before any work happens.
    """
    if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
        raise EvidenceError("No file was submitted")
    if len(file_bytes) > MAX_EVIDENCE_MB * 1024 * 1024:
        raise EvidenceError(f"The file must be {MAX_EVIDENCE_MB} MB or smaller", 413)
    if mime_type not in SUPPORTED_EVIDENCE_MIME:
        raise EvidenceError(
            f"Only {', '.join(sorted(SUPPORTED_EVIDENCE_MIME))} are accepted", 415,
        )

    claims = read_token(token)
    if claims is None:
        raise EvidenceError("The link is invalid, expired, or has been replaced", 403)

    scope = claims["t"]
    case_id = claims["c"]
    request_id = claims["r"]
    token_seq = claims["s"]

    store = get_store()
    stored = await store.get_case(case_id, tenant_id=scope)
    if stored is None:
        raise EvidenceError("The link is invalid, expired, or has been replaced", 403)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)

    request = find_request(case, request_id)
    if request is None or request.get("status") not in OPEN_STATUSES:
        raise EvidenceError("The link is invalid, expired, or has been replaced", 403)
    if request.get("link_seq") != token_seq:
        raise EvidenceError("The link is invalid, expired, or has been replaced", 403)

    record = verification_of(case)
    now = utcnow()

    # N03: settle which document this upload is, and refuse a wrong or repeated one,
    # before spending anything on screening, archiving or extraction.
    received_docs = request.get("received_docs") or {}
    requested_docs = list(request.get("documents") or [])
    still_missing = [d for d in requested_docs if d not in received_docs]
    token_doc_type = claims.get("d")
    if token_doc_type:
        if token_doc_type not in requested_docs:
            raise EvidenceError("This link is for a document that was not requested", 400)
        if token_doc_type in received_docs:
            raise EvidenceError(
                f"The {token_doc_type.replace('_', ' ')} was already received. "
                + (f"Still needed: {', '.join(still_missing)}." if still_missing else ""),
                409,
            )
        doc_type = token_doc_type
    else:
        # A request-level link (no document in the token) cannot say which paper an upload is.
        # With one document requested there is nothing to confuse; with several, the upload
        # would be filed under whichever is still missing, whatever it actually is (N03).
        if len(requested_docs) > 1:
            raise EvidenceError(
                "This link does not say which document it is for. Ask for a link for each document.",
                400,
            )
        doc_type = still_missing[0] if still_missing else (
            requested_docs[0] if requested_docs else "unknown"
        )

    # -- Model Armor screen (deterministic pre-screen + Google Model Armor) --
    from vf_logistics import model_armor

    text_layer = ""
    if mime_type == "application/pdf":
        text_layer, _ = model_armor.extract_pdf_text(file_bytes)
    armor_result = await model_armor.screen(text_layer or filename, stage="evidence_response")
    blocked = armor_result.get("blocked", False)

    # -- Archive the file --
    from vf_logistics import document_store

    archive = await document_store.archive(
        file_bytes, filename, mime_type, case_id,
        prefix=f"{document_store.EVIDENCE_PREFIX}{request_id}", tenant_id=scope,
    )

    # -- Extract facts --
    extraction: dict[str, Any] | None = None
    extraction_cost = 0.0
    if not blocked:
        from vf_logistics.agents import document_agent
        from vf_logistics import lineage

        extraction = await document_agent.extract_evidence(doc_type, file_bytes, filename, mime_type)
        extraction_cost = lineage.cost_usd(
            extraction.get("model"),
            extraction.get("input_tokens", 0),
            extraction.get("output_tokens", 0),
        )

    # -- Write the document record --
    new_version = (record.get("evidence_version") or 0) + 1
    record["evidence_version"] = new_version
    doc_record: dict[str, Any] = {
        "document_id": new_id("evdoc"),
        "request_id": request_id,
        "evidence_version": new_version,
        "doc_type": doc_type,
        "filename": filename,
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "uri": archive.get("uri"),
        "content_type": mime_type,
        "received_at": now,
        "blocked": blocked,
        # How much of the file the reader saw, named as in ingest_document's
        # provenance: `pages` is the PDF's page count, `pages_read` how many of them
        # were transcribed. None when no extraction ran (blocked) or it did not say.
        "pages": extraction.get("source_pages") if extraction else None,
        "pages_read": extraction.get("source_pages_read") if extraction else None,
        "extracted": extraction.get("result") if extraction and not extraction.get("parse_error") else None,
        "extraction": {
            "model": extraction.get("model") if extraction else None,
            "cost_usd": extraction_cost,
            "parse_error": extraction.get("parse_error") if extraction else None,
        },
    }
    record["documents"].append(doc_record)

    # -- Update the request --
    # N03: track per-doc-type receipt; only close when all are in.
    # N04: only a usable copy fills its slot. A blocked or unreadable one is kept on the
    # record (and in the audit trail) but leaves the type open, so the same link takes a
    # clearer copy; it used to close the request and make that type impossible to send.
    usable = usable_document(doc_record)
    if usable:
        received_docs[doc_type] = doc_record["document_id"]
    else:
        request.setdefault("unusable_uploads", []).append(doc_record["document_id"])
    request["received_docs"] = received_docs
    if set(received_docs.keys()) >= set(requested_docs) and requested_docs:
        request["status"] = "RECEIVED"
        request["received_at"] = now
    # else: keep SENT so the link stays valid for the next document

    # -- Cost rollup --
    case["_estimated_cost_usd"] = (case.get("_estimated_cost_usd") or 0.0) + extraction_cost
    case["_sum_latency_ms"] = (case.get("_sum_latency_ms") or 0) + (extraction.get("latency_ms") or 0 if extraction else 0)

    # -- Supersede approvals --
    superseded = _supersede_approvals(record, new_version, now)

    case["updated_at"] = now

    await _write(case, expected_version, scope)
    await _audit(case_id, "evidence_received", "shipper (via link)", None, {
        "document_id": doc_record["document_id"],
        "request_id": request_id,
        "evidence_version": new_version,
        "sha256": doc_record["sha256"],
        "blocked": blocked,
        "usable": usable,
        "superseded_approvals": superseded,
        "source_ip": client_ip,
    }, scope)

    return {
        "received": True,
        "document_id": doc_record["document_id"],
        "evidence_version": new_version,
        "blocked": blocked,
        "extracted": doc_record["extracted"] is not None,
        "usable": usable,
        "superseded_approvals": len(superseded),
        "doc_type": doc_type,
        "still_missing": [d for d in requested_docs if d not in received_docs],
    }


# --------------------------------------------------------------------------
# Re-evaluation (step 4) and approval (step 5)
# --------------------------------------------------------------------------

async def reevaluate(
    case_id: str, *, tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Run the origin checks again on the case's shipment and the evidence received.

    Creates an evaluation record on the case: which findings were clarified, which
    remain, which contradict, what to ask next, and what evidence version all of that
    was computed at. Changes the floor if the evidence contradicts, but NEVER lowers
    it. The case moves to HELD_FOR_REVIEW (or ESCALATED if floor >= 70, or if it
    was escalated before).
    """
    from vf_logistics import verifier

    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)

    # N05: only reevaluate cases that are still in the verification loop.
    if case.get("state") not in REEVALUABLE:
        raise EvidenceError(
            f"Cannot re-evaluate a case in state {case.get('state')}.",
            409,
        )
    # N05: ... and only a case that actually asked for documents. A review case that
    # never entered the loop used to pass the state gate above, get an empty
    # verification record written onto it, and lose the legacy /decide path.
    existing = case.get("verification")
    if not isinstance(existing, dict) or not existing.get("requests"):
        raise EvidenceError("No documents have been requested for this case.", 409)

    record = verification_of(case)
    shipment = case.get("shipment") or {}

    # Gather the extracted facts from all non-blocked documents.
    documents = [
        d for d in record["documents"]
        if isinstance(d, dict) and not d.get("blocked") and isinstance(d.get("extracted"), dict)
    ]
    evidence_result = verifier.check_origin_evidence(shipment, documents)

    # Re-run the deterministic checks on the shipment. This picks up the origin
    # watch finding plus every other check, so the evaluation's findings match what
    # the verifier would say today (rules may have changed since the case arrived).
    validation = verifier.validate(shipment)
    base_findings = list(validation["findings"])
    # Add the evidence findings (contradictions, mismatches). They can only raise.
    for f in evidence_result["findings"]:
        base_findings.append(f)
    from vf_logistics.verifier import _floor_for
    floor, high_count, _, _ = _floor_for(base_findings)

    now = utcnow()
    ev_version = record.get("evidence_version") or 0
    evaluation: dict[str, Any] = {
        "evaluation_id": new_id("eveval"),
        "evidence_version": ev_version,
        "at": now,
        "risk_floor": floor,
        "findings": [f["code"] for f in base_findings],
        "clarified": evidence_result.get("present") or [],
        "unresolved": evidence_result.get("missing") or [],
        "contradictions": evidence_result.get("contradictions") or [],
        "unread": evidence_result.get("unread") or [],
        "status": evidence_result["status"],
        "ruleset_version": case.get("validation", {}).get("checks_run"),
    }
    record["evaluations"].append(evaluation)

    # Update the case's floor if the evidence raised it.
    old_floor = (case.get("reconciliation") or {}).get("risk_floor") or (case.get("validation") or {}).get("risk_floor") or 0
    if floor > old_floor:
        rec = case.get("reconciliation") or {}
        rec["risk_floor"] = floor
        rec["effective_risk"] = max(rec.get("effective_risk") or 0, floor)
        rec["score_disputed"] = (floor - (rec.get("model_risk") or 0)) >= 15
        rec["veto_reasons"] = [f["detail"] for f in base_findings]
        case["reconciliation"] = rec
        case["risk_score"] = rec["effective_risk"]

    # State transition. An escalated case stays escalated: it waits for a signature,
    # and a floor under 70 does not withdraw that. Before a request the case's own
    # state says where it was; during one, resume_state does.
    before = record.get("resume_state") if case.get("state") == AWAITING_EVIDENCE else case.get("state")
    new_state = "ESCALATED" if floor >= 70 or before == "ESCALATED" else "HELD_FOR_REVIEW"
    case["state"] = new_state
    case["updated_at"] = now
    # N04: a request is closed by receive_evidence, the moment every document it asked
    # for is in (status RECEIVED), and by nothing here. A partial set leaves it SENT
    # so the shipper's remaining per-document links keep working. This used to loop
    # over the open requests promoting full ones to RECONCILED, but a full request is
    # never open by the time it gets here, so the loop could not fire.

    await _write(case, expected_version, tenant_id)
    await _audit(case_id, "evidence_reconciled", "system", None, {
        "evaluation_id": evaluation["evaluation_id"],
        "evidence_version": ev_version,
        "floor": floor,
        "status": evidence_result["status"],
        "state_after": new_state,
    }, tenant_id)
    return {
        "evaluation": evaluation,
        "state": new_state,
        "floor": floor,
    }


APPROVED = "APPROVED"

# States from which a reviewer may approve. HELD_FOR_REVIEW is the expected one;
# ESCALATED too (a senior can sign off on it); PENDING_HUMAN for legacy paths.
APPROVABLE_STATES = ("HELD_FOR_REVIEW", "ESCALATED", "PENDING_HUMAN")


async def approve_exception(
    case_id: str,
    approver: str,
    reason: str,
    waived: list[str],
    *,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Release over unresolved or contradicting evidence, as an operation of its own.

    The caller names every item it waives (the unresolved documents, and
    "contradiction:<kind>" for each contradiction) and gives a reason; the approval
    records both. Who may call it is decided by the route (operator role and a
    password sign-in); approve() enforces the password rule again.
    """
    if not (reason or "").strip():
        raise EvidenceError("An exception needs a reason.", 400)
    if not waived:
        raise EvidenceError("An exception must name what it waives.", 400)
    return await approve(
        case_id, "release", approver, note=reason.strip(), actor_auth=actor_auth,
        waived=[str(w) for w in waived], tenant_id=tenant_id,
    )


async def approve(
    case_id: str,
    action: str,
    approver: str,
    *,
    note: str = "",
    actor_auth: str | None = None,
    execute: bool = True,
    waived: list[str] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Approve a release or block, binding the decision to the evidence version.

    The approval is a record; whether it also executes depends on `execute`. The
    demo uses two steps (approve then execute) so the judge sees the chain; the
    seed script uses one (execute=True) so seeding is not a 2N round trip.

    Preconditions:
    - state in APPROVABLE_STATES
    - no request OPEN or SENT (all evidence must have been accounted for)
    - the evaluation at the current evidence_version exists (for a block at evidence
      version 0 none is needed; a release always needs one)
    - actor_auth == "password" for a release (policy A01 for one-click)
    """
    if action not in ("release", "block"):
        raise EvidenceError(f"'{action}' is not a decision that can be approved")
    if not approver:
        raise EvidenceError("An approval is made by a named person")

    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)
    record = verification_of(case)
    state = case.get("state")

    if state not in APPROVABLE_STATES:
        raise EvidenceError(f"The case is {state}, which is not awaiting approval.", 409)
    if open_requests(case):
        raise EvidenceError(
            "There is still an open request for documents. Wait for the remaining documents "
            "and re-evaluate, or cancel the request (with a reason), before approving.", 409,
        )

    ev_version = record.get("evidence_version") or 0
    latest_eval = None
    for ev in reversed(record.get("evaluations") or []):
        if isinstance(ev, dict) and ev.get("evidence_version") == ev_version:
            latest_eval = ev
            break
    if latest_eval is None and ev_version > 0:
        raise EvidenceError(
            "The evidence has been updated but not yet re-evaluated. Re-evaluate first.", 409,
        )

    # N04: a release also needs an evaluation at version 0. A request cancelled before
    # anything arrived leaves no evaluation at all, and a release then went through
    # with nothing checked. A block does not need one: it only holds the shipment.
    if latest_eval is None and action == "release":
        raise EvidenceError(
            "This release has not been evaluated: no evaluation of the evidence on file "
            "exists yet. Re-evaluate first, then approve the release.", 409,
        )

    if action == "release" and actor_auth != "password":
        raise EvidenceError(
            "A release approval needs a password sign-in, not a one-click session.", 403,
        )

    if waived is not None and action != "release":
        raise EvidenceError("Only a release can be an exception.", 400)

    exception: dict[str, Any] | None = None
    if action == "release" and latest_eval:
        unresolved = [str(u) for u in (latest_eval.get("unresolved") or [])]
        contradictions = [
            {"kind": c.get("kind"), "detail": c.get("detail")}
            for c in (latest_eval.get("contradictions") or []) if isinstance(c, dict)
        ]
        contradicted = latest_eval.get("status") == "contradicted" or bool(contradictions)
        if unresolved or contradicted:
            # The decision stays possible: the extractor is a model, and a reviewer who
            # has seen the original must be able to dismiss its false alarm. It is not
            # silent, though: it needs a reason, and the approval carries what was
            # waived so the audit trail shows it, by whom, and why.
            what = []
            if unresolved:
                what.append(f"unresolved evidence ({', '.join(unresolved)})")
            if contradicted:
                what.append("papers that contradict the declared origin")
            needed = unresolved + [f"contradiction:{c['kind']}" for c in contradictions]
            if waived is None:
                raise EvidenceError(
                    f"Release with {' and '.join(what)} is an exception: use the exception "
                    "operation (approve-exception), name every waived item and give a reason.",
                    403,
                )
            if waived is not None and sorted(set(waived)) != sorted(set(needed)):
                raise EvidenceError(
                    "An exception must name every waived item: " + ", ".join(needed) + ".", 400,
                )
            if not (note or "").strip():
                raise EvidenceError(
                    f"Release with {' and '.join(what)} requires a note explaining the exception.",
                    400,
                )
            exception = {
                "unresolved": unresolved,
                "reason": note.strip(),
                "approved_by": approver,
            }
            if contradicted:
                exception["contradictions"] = contradictions
            if waived is not None:
                exception["operation"] = "exception"
                exception["waived"] = sorted(set(waived))

    now = utcnow()
    if waived is not None and exception is None:
        raise EvidenceError("There is nothing to waive: approve the release normally.", 409)

    approval: dict[str, Any] = {
        "approval_id": new_id("evapr"),
        "action": action,
        "approver": approver,
        "approver_auth": actor_auth,
        "evidence_version": ev_version,
        "evaluation_id": latest_eval["evaluation_id"] if latest_eval else None,
        "at": now,
        "note": note,
        "status": "VALID",
    }
    if exception is not None:
        approval["exception"] = exception
    if action == "release" and dual_approval.needed(case.get("shipment") or {}):
        approval["requires_second"] = True
    record["approvals"].append(approval)
    case["state"] = APPROVED
    case["updated_at"] = now

    case.setdefault("reviews", []).append({
        "action": action,
        "reviewer": approver,
        "note": note,
        "at": now,
        "state_before": state,
        "state_after": APPROVED,
        "approval_id": approval["approval_id"],
    })

    await _write(case, expected_version, tenant_id)
    await _audit(case_id, f"evidence_approval_{action}", approver, actor_auth, {
        "approval_id": approval["approval_id"],
        "evidence_version": ev_version,
        "evaluation_id": approval["evaluation_id"],
    }, tenant_id)
    return {
        "approval": approval,
        "state": APPROVED,
    }


# --------------------------------------------------------------------------
# Execution (step 6)
# --------------------------------------------------------------------------

async def second_approve(
    case_id: str,
    approval_id: str,
    approver: str,
    note: str,
    *,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    The second person's approval of a release that needs two (dual_approval.needed).

    Password sign-in only, a note saying what was checked, and a person other than the
    first approver (compared case-insensitively). The approval keeps who and when.
    """
    if not approver:
        raise EvidenceError("A second approval is made by a named person")
    if actor_auth != "password":
        raise EvidenceError("A second approval needs a password sign-in, not a one-click session.", 403)
    if not (note or "").strip():
        raise EvidenceError("A second approval needs a note saying what was checked.", 400)

    stored = await get_store().get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)
    approval = next(
        (a for a in verification_of(case).get("approvals") or []
         if isinstance(a, dict) and a.get("approval_id") == approval_id),
        None,
    )
    if approval is None:
        raise EvidenceError("approval not found", 404)
    if approval.get("status") != "VALID" or case.get("state") != APPROVED:
        raise EvidenceError(
            f"The approval is {approval.get('status')} and the case is {case.get('state')}: "
            "a second approval is only possible while a valid release waits to be executed.", 409,
        )
    if not approval.get("requires_second"):
        raise EvidenceError("This release does not need a second approver.", 409)
    if approval.get("second_approval"):
        raise EvidenceError("This release already has its second approval.", 409)
    if approver.strip().lower() == str(approval.get("approver") or "").strip().lower():
        raise EvidenceError(
            "The second approver must be a different person than the one who approved the release first.", 403,
        )

    now = utcnow()
    approval["second_approval"] = {
        "approver": approver, "approver_auth": actor_auth, "at": now, "note": note.strip(),
    }
    case["updated_at"] = now
    await _write(case, expected_version, tenant_id)
    await _audit(case_id, "evidence_second_approval", approver, actor_auth, {
        "approval_id": approval_id, "first_approver": approval.get("approver"),
    }, tenant_id)
    return {"approval": approval, "state": case["state"]}


def approval_is_executable(
    case: dict[str, Any], approval_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    (approval, reason) — the approval if it can be executed right now, else None
    with the reason. Pure: no IO, so the route and the unit test call the same code.
    """
    record = verification_of(case)
    approval = None
    for a in record.get("approvals") or []:
        if isinstance(a, dict) and a.get("approval_id") == approval_id:
            approval = a
            break
    if approval is None:
        return None, "approval not found"
    status = approval.get("status")
    state = case.get("state")
    if status not in ("VALID", "CLAIMING"):
        return None, f"approval is {status}, not VALID"
    # A CLAIMING approval on an EXECUTING case is an interrupted execution
    # (the process died between claim and confirm): it may be resumed.
    if status == "CLAIMING" and state != "EXECUTING":
        return None, f"approval is CLAIMING but the case is {state}, not EXECUTING"
    if approval.get("requires_second") and not approval.get("second_approval"):
        return None, (
            "this release needs a second approver: a different person must second-approve it "
            "before it is executed"
        )
    if status == "VALID" and state not in (APPROVED, "EXECUTION_FAILED"):
        return None, f"case is {state}, not APPROVED or EXECUTION_FAILED"
    ev_version = record.get("evidence_version") or 0
    if (approval.get("evidence_version") or 0) != ev_version:
        return None, (
            f"approval was for evidence v{approval.get('evidence_version')}; "
            f"the case is now v{ev_version}"
        )
    if open_requests(case):
        return None, "a request for documents is still open"
    return approval, None


def published_message_id(receipt: Any) -> str | None:
    """The Pub/Sub message id a publish receipt proves, or None if nothing went out.

    Two shapes reach here. A direct publish carries it at `detail.message_id`. With
    EXECUTOR_URL set the publish is delegated, and the executor's answer is nested one
    level down at `detail.executor.message_id`; reading only the top level recorded a
    message that WAS published as a failed send (N02). A receipt marked failed proves
    nothing whatever else it carries.
    """
    if not isinstance(receipt, dict) or receipt.get("status") == "failed":
        return None
    detail = receipt.get("detail") or {}
    delegated = detail.get("executor")
    message_id = detail.get("message_id") or (
        delegated.get("message_id") if isinstance(delegated, dict) else None
    )
    return str(message_id) if message_id else None


EXECUTED_STATES = ("RELEASED_BY_HUMAN", "BLOCKED_BY_HUMAN")


async def unreconciled_publish_audits(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """
    Executions whose decision the broker accepted but whose publish_decision audit row was
    never saved (N01): the case says what was sent, the append-only trail does not yet.
    """
    store = get_store()
    gaps: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page, cursor = await store.query_cases(
            states=EXECUTED_STATES, cursor=cursor, limit=200, tenant_id=tenant_id,
        )
        for case in page:
            if not case.get("audit_pending"):
                continue
            for exe in verification_of(case).get("executions") or []:
                if isinstance(exe, dict) and exe.get("audit_pending"):
                    gaps.append({
                        "case_id": case.get("case_id"),
                        "approval_id": exe.get("approval_id"),
                        "execution_id": exe.get("execution_id"),
                        "message_id": exe.get("message_id"),
                        "since": exe.get("at"),
                    })
        if not cursor:
            return gaps


async def reconcile_publish_audits(actor: str, tenant_id: str | None = None) -> dict[str, int]:
    """
    Write the publish_decision audit row each gap is missing, once, and clear the gap.

    A row the trail already holds for the approval's key (the sent ledger may have saved
    it) clears the gap without a second row. A row that still cannot be saved leaves the
    gap in place: it is cleared only when the trail holds the row.
    """
    from vf_logistics import tools

    if not actor:
        raise EvidenceError(
            "A reconciliation is recorded under a named person: sign in, or send reviewer.", 400,
        )
    store = get_store()
    counts = {"reconciled": 0, "already_recorded": 0, "failed": 0}
    for gap in await unreconciled_publish_audits(tenant_id):
        stored = await store.get_case(gap["case_id"], tenant_id=tenant_id)
        if not stored:
            counts["failed"] += 1
            continue
        case = copy.deepcopy(stored)
        exe = next(
            (e for e in verification_of(case).get("executions") or []
             if isinstance(e, dict) and e.get("execution_id") == gap["execution_id"]),
            None,
        )
        if exe is None or not exe.get("audit_pending"):
            continue
        receipt = next(
            (a for a in reversed(case.get("actions") or [])
             if isinstance(a, dict) and a.get("action") == "publish_decision"
             and (a.get("detail") or {}).get("message_id") == exe.get("message_id")),
            None,
        )
        detail = dict((receipt or {}).get("detail") or {})
        detail.setdefault("case_id", gap["case_id"])
        detail.setdefault("approval_id", exe.get("approval_id"))
        detail.setdefault("idempotency_key", f"exec-{exe.get('approval_id')}")
        detail["message_id"] = exe.get("message_id")
        try:
            wrote = await tools.record_publish_audit(gap["case_id"], detail, actor, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001 - the gap stays until the trail holds the row
            counts["failed"] += 1
            continue
        counts["reconciled" if wrote else "already_recorded"] += 1
        exe.pop("audit_pending", None)
        exe["audit_reconciled_at"] = utcnow()
        exe["audit_reconciled_by"] = actor
        if not any(
            isinstance(e, dict) and e.get("audit_pending")
            for e in verification_of(case).get("executions") or []
        ):
            case.pop("audit_pending", None)
        await _write(case, stored.get("_version"), tenant_id)
    return {**counts, "pending": len(await unreconciled_publish_audits(tenant_id))}


async def execute_approval(

    case_id: str,
    approval_id: str,
    executor: str,
    *,
    actor_auth: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Carry out an approved decision: claim, send, confirm.

    Three phases so that concurrent callers and publish failures are handled:
    1. CLAIM -- atomically mark the approval CLAIMING via optimistic lock.
       Only one caller wins; the loser gets a 409 from the store.
    2. SEND -- call the tool + Pub/Sub.
    3. CONFIRM -- write the outcome. If Pub/Sub failed, the execution is
       recorded as send_failed and the case moves to EXECUTION_FAILED;
       the approval reverts to VALID so a retry is possible.
    """
    from vf_logistics import tools

    if not executor:
        raise EvidenceError("An execution is done by a named person")
    if actor_auth != "password":
        raise EvidenceError("Executing a decision needs a password sign-in", 403)

    store = get_store()
    stored = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored:
        raise EvidenceError("case not found", 404)
    expected_version = stored.get("_version")
    case = copy.deepcopy(stored)
    # Where this attempt starts: APPROVED the first time, EXECUTION_FAILED on a retry,
    # EXECUTING when resuming an expired claim. The review entry records it.
    state_before = case.get("state")

    approval, reason = approval_is_executable(case, approval_id)
    if approval is None:
        record = verification_of(case)
        record.setdefault("executions", []).append({
            "execution_id": new_id("evexe"),
            "approval_id": approval_id,
            "executor": executor,
            "at": utcnow(),
            "status": "denied",
            "reason": reason,
        })
        case["updated_at"] = utcnow()
        await _write(case, expected_version, tenant_id)
        await _audit(case_id, "execution_denied", executor, actor_auth, {
            "approval_id": approval_id, "reason": reason,
        }, tenant_id)
        raise EvidenceError(reason or "execution refused", 409)

    # -- Phase 1: CLAIM (atomic via optimistic lock) --
    action = approval["action"]
    now = utcnow()
    record = verification_of(case)
    existing_exe = next(
        (e for e in reversed(record.get("executions") or [])
         if isinstance(e, dict)
         and e.get("approval_id") == approval_id
         and e.get("status") == "claiming"),
        None,
    )
    if existing_exe is not None and case.get("state") == "EXECUTING":
        # Recovery: reuse the interrupted claim rather than opening a second one, but
        # only once the lease has run out. Inside it NOBODY may resume, the claimant
        # included (N01): what is stored is a name and a deadline, which cannot tell
        # a crashed process from a slow one, and a same-person second call while the
        # first is still mid-send was being treated as a resume and sent a second
        # message. The price is a wait of up to CLAIM_LEASE_S after a real crash.
        lease_ends = float(existing_exe.get("claim_expires_epoch") or 0)
        if time.time() < lease_ends:
            raise EvidenceError(
                "Another execution of this approval is in progress. "
                "Try again after it finishes or its claim expires.", 409,
            )
        execution_id = existing_exe["execution_id"]
        execution = existing_exe
        execution["claim_expires_epoch"] = time.time() + CLAIM_LEASE_S
        execution.setdefault("resumed", []).append({"executor": executor, "at": now})
    else:
        execution_id = new_id("evexe")
        execution = {
            "execution_id": execution_id,
            "approval_id": approval_id,
            "executor": executor,
            "at": now,
            "status": "claiming",
            "claim_expires_epoch": time.time() + CLAIM_LEASE_S,
        }
        record.setdefault("executions", []).append(execution)
        approval["status"] = "CLAIMING"
        case["state"] = "EXECUTING"
    case["updated_at"] = now
    await _write(case, expected_version, tenant_id)
    # If another caller claimed first, _write raises a version conflict (409).

    # -- Phase 2: SEND --
    shipment_id = case.get("shipment_id") or case_id
    # Set before the try so that a publish that raises does not discard the receipt of
    # the release/hold that already happened: it is a row in the audit trail, and the
    # case's own actions should say so too.
    tool_receipt = None
    pub_receipt = None
    try:
        if action == "release":
            tool_receipt = await tools.release_shipment(
                case_id, shipment_id,
                f"Approved by {approval['approver']} at evidence v{approval['evidence_version']}",
                tenant_id=tenant_id,
            )
            new_state = "RELEASED_BY_HUMAN"
        else:
            tool_receipt = await tools.hold_shipment(
                case_id, shipment_id,
                f"Blocked by {approval['approver']} at evidence v{approval['evidence_version']}",
                tenant_id=tenant_id,
            )
            new_state = "BLOCKED_BY_HUMAN"

        pub_receipt = await tools.publish_decision(
            case_id,
            {
                "outcome": new_state,
                "action": action,
                "approval_id": approval_id,
                "evidence_version": approval.get("evidence_version"),
                "evaluation_id": approval.get("evaluation_id"),
                "approver": approval["approver"],
                "executor": executor,
                # Fixed per approval so a retry after EXECUTION_FAILED or an
                # interrupted claim is deduplicated downstream.
                "idempotency_key": f"exec-{approval_id}",
            },
            after_attempt=state_before != APPROVED,
            tenant_id=tenant_id,
        )
        message_id = published_message_id(pub_receipt)
        pub_failed = message_id is None
    except Exception:
        message_id = None
        pub_failed = True
        new_state = action  # placeholder, overridden below

    # -- Phase 3: CONFIRM --
    stored2 = await store.get_case(case_id, tenant_id=tenant_id)
    if not stored2:
        raise EvidenceError("case disappeared during execution", 500)
    ev2 = stored2.get("_version")
    case2 = copy.deepcopy(stored2)
    record2 = verification_of(case2)
    exe2 = next(
        (e for e in record2.get("executions") or [] if e.get("execution_id") == execution_id),
        None,
    )
    apr2 = next(
        (a for a in record2.get("approvals") or [] if a.get("approval_id") == approval_id),
        None,
    )
    if exe2 is None or apr2 is None:
        raise EvidenceError("execution record lost during write", 500)

    if pub_failed:
        # A publish that timed out may have been accepted. Say so, so the retry is read as
        # "result unknown, same key" and not as "nothing was sent" (N01).
        unknown = isinstance(pub_receipt, dict) and pub_receipt.get("status") == "unknown"
        exe2["status"] = "send_unknown" if unknown else "send_failed"
        if unknown:
            exe2["reason"] = (
                "The publish timed out and may have been accepted; "
                "a retry reuses the same idempotency key."
            )
        exe2["message_id"] = None
        apr2["status"] = "VALID"  # revert so retry is possible
        case2["state"] = "EXECUTION_FAILED"
        final_state = "EXECUTION_FAILED"
    else:
        final_state = new_state
        exe2["status"] = "sent"
        exe2["message_id"] = message_id
        if isinstance(pub_receipt, dict) and pub_receipt.get("audit_saved") is False:
            # The broker accepted the message but its audit row could not be saved, twice
            # (N01). The decision stands and is not sent again; the gap stays on the case
            # until reconcile_publish_audits() writes the row.
            exe2["audit_pending"] = True
            case2["audit_pending"] = True
        apr2["status"] = "EXECUTED"
        case2["state"] = final_state
        case2["decision"] = {
            "outcome": final_state,
            "rationale": f"{approval['approver']} approved {action}; executed by {executor}.",
            "decided_by": "human",
        }

    case2["updated_at"] = utcnow()
    case2.setdefault("reviews", []).append({
        "action": action,
        "reviewer": executor,
        "note": f"Executed approval {approval_id}" if not pub_failed else f"Execution failed for {approval_id}",
        "at": now,
        "state_before": state_before,
        "state_after": final_state,
    })
    if tool_receipt:
        case2.setdefault("actions", []).append(tool_receipt)
    if pub_receipt:
        case2.setdefault("actions", []).append(pub_receipt)

    await _write(case2, ev2, tenant_id)
    if final_state in ("RELEASED_BY_HUMAN", "BLOCKED_BY_HUMAN"):
        # The learning loop reads human release/block history through a TTL
        # cache, and this is the only way a case with a verification record
        # reaches either state. Left cached, a second block recorded here would
        # not add its +15 to this shipper's next shipment until the TTL ran out,
        # and that shipment could auto-clear. Imported here, like tools above,
        # so that importing evidence does not pull in every agent module.
        from vf_logistics import orchestrator
        orchestrator._invalidate_shipper_feedback()
    await _audit(case_id, f"evidence_executed_{action}", executor, actor_auth, {
        "execution_id": execution_id,
        "approval_id": approval_id,
        "evidence_version": approval.get("evidence_version"),
        "message_id": message_id,
        "state_after": final_state,
        "pub_failed": pub_failed,
    }, tenant_id)
    return {
        "execution": exe2,
        "state": final_state,
        "message_id": message_id,
    }
