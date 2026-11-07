"""
Autonomous orchestrator.

This is the Taskmaster layer. It monitors an event stream, drives each shipment
through a conditional multi-agent workflow without human involvement, and
executes real actions on the outcome.

State machine. Three actionable states, not five:

    ACTIONABLE = ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED")

  INGESTED
     |  fraud_detection, compliance and route validation run CONCURRENTLY
     |  (asyncio.gather), then hs_classifier, then zero_day if the gate opens,
     |  then auto_debate if the floor and the model disagree by >= 15 points
     v
  SPECIALISTS_DONE
     |-- effective risk < FRAUD_CLEAR_BELOW, compliance CLEARED
     |   and reconcile() permits auto-clear ------> AUTO_CLEARED   release_shipment
     |
     |-- otherwise, if not blocked / review
     |   required and risk < INVESTIGATE_AT ------> HELD_FOR_REVIEW assign_analyst
     |
     |-- blocked / review required, or risk >= INVESTIGATE_AT
     v  investigation agent
  INVESTIGATED
     v
  ESCALATED   hold_shipment + draft_sar + notify_webhook

  Any step failing 3 times ends in DEAD_LETTER. A proposed outcome the delegation
  boundary refuses becomes PENDING_HUMAN instead, and the refusal is recorded.

THERE IS NO `FRAUD_SCORED` AND NO `COMPLIANCE_SCREENED`. An earlier version of this
docstring drew them as sequential states, and they exist nowhere else in the codebase --
fraud and compliance are gathered into one step and meet in `SPECIALISTS_DONE`. That
mattered beyond tidiness: a hand-drawn architecture diagram was built from this comment
and inherited the invented states, so the picture shipped wrong twice for the same
reason. Draw the pipeline from `advance()` and `ACTIONABLE`, not from here.

Every transition is persisted before the next one starts, so a case survives an
instance restart and is picked up wherever it left off.

Track: Best Apps and Agents
Hackathon: Nebius x NVIDIA Global AI Hackathon
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from vf_logistics import budget
from vf_logistics import carrier_registry
from vf_logistics import config
from vf_logistics import document_render
from vf_logistics import document_store
from vf_logistics import governance
from vf_logistics import grounding
from vf_logistics import lineage
from vf_logistics import model_armor
from vf_logistics import shipper_registry
from vf_logistics import tavily_client
from vf_logistics import tools
from vf_logistics import untrusted
from vf_logistics import agent_payment, payment_ai, payment_checks
from vf_logistics import verifier
from vf_logistics.agents import (
    analyze_shipment,
    conduct_debate,
    extract_shipment,
    investigate_case,
    screen_shipment,
)
# Imported directly rather than via agents/__init__ because these two are not
# re-exported there -- __init__ exports the thirteen names the dashboard uses, and
# adding to it would put them in an API surface they are not part of.
from vf_logistics.agents.hs_classifier_agent import classify_hs
from vf_logistics.agents.hs_classifier_agent import interpret as interpret_hs
# The module rather than a name, so the event strings can read MODEL_ID at call time.
# Reading it through the module is also what lets a test monkeypatch the debate model
# and assert the emitted line follows it, which is the guard that stops this from
# drifting back to a hard-coded "Super".
from vf_logistics.agents import debate_agent
from vf_logistics.agents.zero_day_agent import interpret as interpret_zero_day
from vf_logistics.agents.zero_day_agent import screen_zero_day
from vf_logistics.agents.zero_day_agent import should_screen as should_screen_zero_day
from vf_logistics.schemas import ComplianceStatus
from vf_logistics.store import (
    TENANT_FIELD,
    OptimisticLockError,
    get_store,
    new_id,
    utcnow,
)

log = logging.getLogger(__name__)

# --- Decision thresholds. Env-overridable so the policy is not buried in code.
FRAUD_CLEAR_BELOW = int(os.getenv("FRAUD_CLEAR_BELOW", "40"))
INVESTIGATE_AT = int(os.getenv("INVESTIGATE_AT", "70"))
# A person turns this on (Render environment). Off, the agent gate reads the case risk as always. On, a payment case whose model score no flag of
# its own supports (grounding.grounded_risk) is read at the deterministic floor plus any penalty; the case risk and the model score stay as they were.
AGENT_GROUNDED_GATE = os.getenv("AGENT_GROUNDED_GATE", "").strip().lower() in ("1", "true", "yes", "on")

# How sure the Senior Auditor must be before a DISAGREE changes a case's route.
# Read per call (debate_escalate_confidence) so it can be tuned without a deploy.
DEBATE_ESCALATE_CONFIDENCE_DEFAULT = 0.7
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "1.5"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))

# How the pipeline gets driven.
#
#   ondemand (default) - no background loop. Cases advance inside request
#     handlers: the Pub/Sub push handler drives its own case to completion, and
#     the dashboard's state poll drains a step at a time. This is the only mode
#     that works with --min-instances=0 and CPU throttling, because Cloud Run
#     freezes the container once a response is sent. It is also the only mode
#     that costs nothing when nobody is using the system.
#
#   poll - the original always-on worker loop. Requires
#     --no-cpu-throttling --min-instances=1, which bills around the clock.
#     Kept for recording a demo where a visibly self-running worker matters.
WORKER_MODE = os.getenv("WORKER_MODE", "ondemand").lower()

# Guards on synchronous draining, so a request can never hang indefinitely.
MAX_CHAIN_STEPS = int(os.getenv("MAX_CHAIN_STEPS", "6"))
CHAIN_BUDGET_SECONDS = float(os.getenv("CHAIN_BUDGET_SECONDS", "120"))

# States the worker will pick up. Terminal states are absent by design.
ACTIONABLE = ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED")

# States where the agent has finished its own work. Several of these still need
# a person to act, which is what AWAITING_HUMAN tracks: "the agent is done" and
# "the case is closed" are different facts and conflating them is how work gets
# silently dropped.
TERMINAL = (
    "AUTO_CLEARED",
    "HELD_FOR_REVIEW",
    "ESCALATED",
    "PENDING_HUMAN",
    "RELEASED_BY_HUMAN",
    "BLOCKED_BY_HUMAN",
    "DEAD_LETTER",
    # A reviewer asked the shipper for documents (evidence.py). Terminal for the
    # agent -- it has nothing more to do -- but NOT in AWAITING_HUMAN: a decision
    # cannot be recorded on a case while a request is open. Answer it or cancel it.
    "AWAITING_EVIDENCE",
    "APPROVED",
    "EXECUTING",
    "EXECUTION_FAILED",
)

# Cases sitting in a human's queue. Nothing leaves these states without a named
# reviewer making a decision.
AWAITING_HUMAN = ("PENDING_HUMAN", "HELD_FOR_REVIEW", "ESCALATED")

# TTL cache for whole-collection aggregation (count()/sum()). Firestore bills
# an aggregation query as one read regardless of how many documents match,
# but the dashboard polls every 250ms while work is in flight - without a
# cache that turns into ~40 aggregation round trips/second for a number that
# does not need sub-second freshness. A short cache keeps the totals exact
# at any collection size while bounding both cost and per-poll latency.
#
# Keyed by tenant. A single shared entry would be a cross-tenant leak through
# the cache rather than through a query: the first caller's totals -- case
# counts, token spend, cost -- would be served to the next tenant to poll
# within the TTL, and no amount of scoping on the queries underneath would
# show it, because the queries would not run.
_METRICS_CACHE_TTL_SECONDS = float(os.getenv("METRICS_CACHE_TTL_SECONDS", "5"))
_metrics_cache: dict[str, dict[str, Any]] = {}


def _case_tenant(case: dict[str, Any]) -> str | None:
    """
    The tenant a stored case belongs to, read off the document itself.

    Preferred over a `tenant_id` parameter everywhere the case is already in
    hand. A parameter can disagree with the document, and on a read that
    disagreement is silent -- it would simply return nothing, or worse, scope a
    follow-up query to the caller's tenant while acting on a case owned by
    another. put_case already refuses a write whose requested owner differs from
    the stored one (store.py), so deriving here keeps reads and writes answering
    to the same source of truth.

    Returns None for a case that predates tenant stamping, which the store
    treats as the single implicit tenant.
    """
    return case.get(TENANT_FIELD)


async def global_metrics(tenant_id: str | None = None) -> dict[str, Any]:
    """Exact counts/token/cost/latency totals across the whole collection,
    cached briefly so frequent polling does not multiply aggregation reads."""
    now = time.monotonic()
    cache_key = tenant_id or ""
    cached = _metrics_cache.get(cache_key)
    if cached is not None and (now - cached["at"] < _METRICS_CACHE_TTL_SECONDS):
        value: dict[str, Any] = cached["value"]
        return value

    store = get_store()
    all_states = ACTIONABLE + TERMINAL
    counts, rollups, cleared_by = await asyncio.gather(
        store.count_by_state(all_states, tenant_id=tenant_id),
        store.sum_rollups(tenant_id=tenant_id),
        store.count_by_cleared_by(tenant_id=tenant_id),
    )
    value = {
        "counts": counts,
        "in_flight": sum(counts.get(s, 0) for s in ACTIONABLE),
        "awaiting_human": sum(counts.get(s, 0) for s in AWAITING_HUMAN),
        "agent_calls": rollups["agent_calls"],
        "avg_latency_ms": rollups["avg_latency_ms"],
        "total_input_tokens": rollups["total_input_tokens"],
        "total_output_tokens": rollups["total_output_tokens"],
        "estimated_cost_usd": rollups["estimated_cost_usd"],
        # Cost-Aware Hybrid Architecture: Split KPI tiles
        "cleared_by_rules": cleared_by["rules"],
        "cleared_by_ai": cleared_by["ai"],
        "cleared_by_unknown": cleared_by["unknown"],
        "total_auto_cleared": cleared_by["total_auto_cleared"],
        "at": utcnow(),
    }
    _metrics_cache[cache_key] = {"at": now, "value": value}
    return value


# --------------------------------------------------------------------------
# Event feed
# --------------------------------------------------------------------------

async def emit(
    case_id: str, kind: str, message: str, *, tenant_id: str | None = None,
    **extra: Any,
) -> None:
    await get_store().add_event(
        {
            "event_id": new_id("evt"),
            "case_id": case_id,
            "kind": kind,
            "message": message,
            "at": utcnow(),
            **extra,
        },
        tenant_id=tenant_id,
    )


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

async def ingest_shipment(
    shipment: dict[str, Any],
    source: str = "event",
    provenance: dict[str, Any] | None = None,
    intake_step: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Accept a shipment event and create the case the worker will pick up.

    Idempotent on shipment_id: re-delivery of the same Pub/Sub message returns
    the existing case instead of starting a second workflow for it.

    `source` records how the shipment arrived (event, simulator, document).
    `provenance` carries the Cloud Storage receipt when it came from a document.
    `intake_step` is the document agent's hop, prepended so the case trace opens
    with the extraction rather than starting at fraud scoring with no record of
    where the data came from.
    """
    store = get_store()
    shipment_id = str(shipment.get("shipment_id") or new_id("SHIP"))
    case_id = f"CASE-{shipment_id}"

    # Scoped: case_id is derived from the caller-supplied shipment_id, so an
    # unscoped lookup lets a submitter probe for another tenant's case by naming
    # its shipment -- and worse, the early return would hand that case back while
    # silently discarding the shipment actually submitted.
    existing = await store.get_case(case_id, tenant_id=tenant_id)
    if existing:
        found: dict[str, Any] = existing
        return found

    # Supply the one fact the shipment is not allowed to assert about itself.
    # ingest_document already does this after sanitisation; event-sourced
    # shipments went straight to validate() with no counterparty lookup at
    # all, which meant check_counterparty()'s identity_mismatch detection -
    # and the VIP whitelist's own identity check - had nothing to cross-check
    # an event-sourced claim against. An unknown or mismatched counterparty
    # writes nothing and leaves the unverified-history floor standing.
    shipper_registry.enrich(shipment)
    await carrier_registry.enrich(shipment, tenant_id=tenant_id)

    case: dict[str, Any] = {
        "case_id": case_id,
        "shipment_id": shipment_id,
        "shipment": shipment,
        "state": "INGESTED",
        "claimed": False,
        "attempts": 0,
        "not_before": None,
        "risk_score": None,
        "compliance_status": None,
        "source": source,
        "provenance": provenance or {},
        "steps": [intake_step] if intake_step else [],
        "actions": [],        # receipts from tools.py
        "decision": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    # The document-intake step's tokens must reach the rollups, not only `steps`.
    #
    # This was a real metering defect. `intake_step` is built by ingest_document with
    # genuine token counts and inserted straight into `steps` above, bypassing
    # _record_step -- which is the only other place `_agent_calls`, `_input_tokens`,
    # `_output_tokens` and `_estimated_cost_usd` are incremented. The consequences
    # were split and self-inconsistent:
    #
    #   - the audit record reads `steps[]` via lineage.step_lineage(), so it PRICED
    #     the vision call correctly;
    #   - /api/v1/billing/usage reads the rollups via sum_rollups(), so it did NOT
    #     see it at all.
    #
    # So for every document-sourced case the audit row and the case's own
    # `_estimated_cost_usd` disagreed, and the billing figure was the lower of the
    # two. It was also the worst call to lose: the vision model's input rate is the
    # highest in config.PRICING, roughly eleven times Nano's.
    #
    # backfill_rollups() cannot repair historical cases here, because it skips any
    # case that already has `_agent_calls` -- which a document-sourced case acquires
    # the moment its first fraud step runs.
    if intake_step:
        intake_in = int(intake_step.get("input_tokens") or 0)
        intake_out = int(intake_step.get("output_tokens") or 0)
        intake_latency = intake_step.get("latency_ms")

        case["_agent_calls"] = 1
        case["_input_tokens"] = intake_in
        case["_output_tokens"] = intake_out
        if isinstance(intake_latency, int):
            case["_sum_latency_ms"] = intake_latency

        intake_cost = lineage.cost_usd(
            intake_step.get("model"), intake_in, intake_out
        )
        # Written onto the step too, so the trace shows what this hop cost -- the
        # same field _record_step sets for every other step.
        intake_step["cost_usd"] = round(intake_cost, 8)
        case["_estimated_cost_usd"] = intake_cost
    # Every case must be reviewable against paperwork. A case that arrived as a
    # structured event has none, so the event is rendered into a bill of lading
    # and archived alongside document-sourced cases. It is flagged `generated`
    # so the reviewer is never shown a reconstruction as if it were an original.
    # Document-sourced cases are skipped: ingest_document archives the real file
    # a moment later, and rendering one here would only be overwritten.
    if source != "document" and not case["provenance"].get("uri"):
        rendered = document_render.render_bill_of_lading(shipment, case_id, source)
        if rendered:
            filename = f"{shipment_id}-bill-of-lading.pdf"
            receipt = await document_store.archive(
                rendered, filename, "application/pdf", case_id, tenant_id=tenant_id,
            )
            case["provenance"] = {
                **case["provenance"],
                "filename": filename,
                "generated": True,
                "rendered_from": source,
                **receipt,
            }

    await store.put_case(case, tenant_id=tenant_id)
    await emit(
        case_id,
        "ingested",
        f"Shipment {shipment_id} received via {source}, case opened",
        source=source,
        tenant_id=tenant_id,
    )
    return case


# --------------------------------------------------------------------------
# Document intake
# --------------------------------------------------------------------------

async def ingest_document(
    document_bytes: bytes, filename: str, mime_type: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Turn an uploaded shipping document into a running case.

    Model Armor screens the document before the vision model sees it, whenever that is
    possible. For a PDF with a text layer, pypdf extracts the text with no model
    involved, Model Armor screens that, and a blocked document is never sent for
    transcription at all - no tokens spent, no model exposed. For a scan there is
    no text layer to pre-screen, so transcription happens first and the result is
    screened before it reaches any downstream agent. The case records which of
    the two it got, because they are not the same assurance.
    """
    # Set before the vision transcription, which is the single most expensive model
    # call in the service -- MiniCPM-V's input rate is 11x Nano's. A tenant over its
    # ceiling must be refused here, not after the page has been read.
    budget.set_current_tenant(tenant_id)

    pre_screen: dict[str, Any] | None = None
    raw_text = ""

    if (mime_type or "").endswith("pdf") or filename.lower().endswith(".pdf"):
        raw_text, extract_error = model_armor.extract_pdf_text(document_bytes)
        if raw_text:
            pre_screen = await model_armor.screen(raw_text, stage="before_model")
        elif extract_error:
            pre_screen = {
                "provider": "google-cloud-model-armor",
                "stage": "before_model",
                "blocked": False,
                "available": False,
                "detail": f"could not read PDF text layer: {extract_error}",
                "requires_human": True,
            }

    # A document that tried to manipulate the model never reaches the model.
    if pre_screen and pre_screen.get("blocked"):
        shipment = {
            "shipment_id": new_id("BLOCKED"),
            "status": "pending",
            "cargo_description": "not transcribed: document blocked by Model Armor",
        }
        case = await ingest_shipment(
            shipment,
            source="document",
            provenance={"filename": filename},
            intake_step={
                "agent": "model_armor",
                "latency_ms": None,
                "model": "google-cloud-model-armor",
                "parse_error": False,
                "at": utcnow(),
                "result": {
                    "summary": (
                        "Document blocked before any model processing. "
                        f"{pre_screen.get('detail')}"
                    ),
                    "model_armor": pre_screen,
                },
            },
            tenant_id=tenant_id,
        )
        receipt = await document_store.archive(
            document_bytes, filename, mime_type or "application/pdf", case["case_id"],
            tenant_id=tenant_id,
        )
        case["provenance"] = {"filename": filename, **receipt}
        case["input_security"] = {"model_armor": pre_screen, "model_invoked": False}
        case["requires_human"] = True
        case["state"] = "PENDING_HUMAN"
        case["proposed_outcome"] = None
        case["gate_denials"] = [{
            "action": "document_intake",
            "reason": "DENIED: Model Armor flagged prompt injection; no model was invoked",
        }]
        await get_store().put_case(case, tenant_id=tenant_id)
        await emit(
            case["case_id"],
            "security",
            f"Model Armor blocked {filename} before any model processing "
            f"({pre_screen.get('confidence') or 'match found'}). No tokens spent.",
            agent="model_armor",
            tenant_id=tenant_id,
        )
        return {
            "accepted": True,
            "blocked": True,
            "case_id": case["case_id"],
            "state": case["state"],
            "security": case["input_security"],
            "error": None,
        }

    response = await extract_shipment(document_bytes, filename, mime_type)
    raw = response.get("result") or {}

    if response.get("parse_error") or not raw:
        return {
            "accepted": False,
            "error": response.get("error") or "document could not be transcribed",
            "latency_ms": response.get("latency_ms"),
        }

    # The document is attacker-controlled. Enforce the schema before any of this
    # reaches a field the orchestrator reads.
    sanitised = untrusted.sanitise_shipment(raw)
    shipment = sanitised["shipment"]
    notes = sanitised["extraction_notes"]

    screening = untrusted.screen_text(
        untrusted.searchable_text(shipment, notes)
    )

    # No pre-screen was possible (a scan): screen the transcription instead,
    # before it reaches the fraud or compliance agents.
    post_screen = None
    if pre_screen is None:
        post_screen = await model_armor.screen(
            untrusted.searchable_text(shipment, notes), stage="after_transcription"
        )

    shipment.setdefault("status", "pending")
    if str(shipment.get("shipment_id") or "").strip() in ("", "not stated", "N/A"):
        # A document with no readable booking number is still a shipment that
        # needs screening, so give it a synthetic id rather than dropping it.
        shipment["shipment_id"] = new_id("DOC")

    # Supply the one fact the document is not allowed to assert about itself.
    # Deliberately after sanitisation, so the file cannot present its own
    # history, and before the agents run, so they score the enriched record.
    # An unknown or mismatched counterparty writes nothing and leaves the
    # unverified-history floor in verifier.py standing.
    counterparty = shipper_registry.enrich(shipment)

    case_id = f"CASE-{shipment['shipment_id']}"
    receipt = await document_store.archive(
        document_bytes,
        filename,
        response.get("source_mime", "application/pdf"),
        case_id,
        tenant_id=tenant_id,
    )

    intake_step = {
        "agent": "document_intake",
        "latency_ms": response.get("latency_ms"),
        "model": response.get("model"),
        "input_tokens": response.get("input_tokens", 0),
        "output_tokens": response.get("output_tokens", 0),
        "parse_error": False,
        "at": response.get("at"),
        "result": {
            "summary": (
                f"Transcribed {filename} into a shipment record "
                f"(confidence {sanitised['extraction_confidence']})"
            ),
            "currency": shipment.get("currency"),
            "extraction_notes": notes,
            "extraction_confidence": sanitised["extraction_confidence"],
            "dropped_fields": sanitised["dropped_fields"],
            "forbidden_fields_attempted": sanitised["forbidden_fields_attempted"],
            "injection_screening": screening,
            "model_armor": pre_screen or post_screen,
            "counterparty": counterparty,
        },
    }

    case = await ingest_shipment(
        shipment,
        source="document",
        # The page count rides on provenance rather than on the intake step:
        # ingest_shipment persists a fixed subset of the agent envelope
        # (agent, at, model, tokens, latency, parse_error, result), so
        # source_pages would be dropped the way source_mime already is.
        # provenance is both persisted and the right home for a fact about the
        # file rather than about the transcription -- and it gives the count a
        # numeric field to assert on, next to the prose note in
        # extraction_notes.
        provenance={
            "filename": filename,
            "pages": response.get("source_pages"),
            "pages_read": response.get("source_pages_read"),
            **receipt,
        },
        intake_step=intake_step,
        tenant_id=tenant_id,
    )

    armor = pre_screen or post_screen or {}
    case["input_security"] = {
        "injection_screening": screening,
        "model_armor": armor,
        "model_invoked": True,
        "screened_before_model": bool(pre_screen and pre_screen.get("available")),
        "dropped_fields": sanitised["dropped_fields"],
        "forbidden_fields_attempted": sanitised["forbidden_fields_attempted"],
    }
    if (
        screening["blocked"]
        or sanitised["forbidden_fields_attempted"]
        or armor.get("blocked")
        or armor.get("requires_human")
    ):
        case["requires_human"] = True
        await get_store().put_case(case, tenant_id=tenant_id)
        reasons = sorted({f["type"] for f in screening["findings"]})
        if armor.get("blocked"):
            reasons.append("Model Armor match")
        if armor.get("requires_human"):
            reasons.append(f"Model Armor unavailable ({armor.get('detail')})")
        if sanitised["forbidden_fields_attempted"]:
            reasons.append("forbidden field override")
        await emit(
            case["case_id"],
            "security",
            "Document flagged for mandatory human review: " + "; ".join(reasons),
            agent="input_security",
            tenant_id=tenant_id,
        )
    else:
        await get_store().put_case(case, tenant_id=tenant_id)

    await emit(
        case["case_id"],
        "document_extracted",
        f"Read {filename} in {response.get('latency_ms')}ms"
        + (f"; {len(notes)} transcription issue(s) noted" if notes else ""),
        agent="document_intake",
        tenant_id=tenant_id,
    )

    return {
        "accepted": True,
        "case_id": case["case_id"],
        "shipment_id": case["shipment_id"],
        "state": case["state"],
        "extracted": shipment,
        "archive": receipt,
        "security": case["input_security"],
        "latency_ms": response.get("latency_ms"),
    }


# --------------------------------------------------------------------------
# One step of the workflow
# --------------------------------------------------------------------------

async def _record_step(
    case: dict[str, Any], agent: str, response: dict[str, Any]
) -> dict[str, Any]:
    result = response.get("result") or {}
    input_tokens = response.get("input_tokens", 0) or 0
    output_tokens = response.get("output_tokens", 0) or 0
    latency_ms = response.get("latency_ms")
    model = response.get("model")

    step: dict[str, Any] = {
        "agent": agent,
        "latency_ms": latency_ms,
        "model": model,
        "parse_error": response.get("parse_error", False),
        "at": response.get("at"),
        "result": result,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    # The exact text sent to the model and the exact text it returned. Carried
    # onto the step so a reviewer can audit a decision rather than take the
    # parsed summary on trust; both are already truncated by envelope().
    if response.get("prompt"):
        step["prompt"] = response["prompt"]
    if response.get("prompt_sha256"):
        # Carried onto the step because this is what makes a decision auditable:
        # it identifies the exact prompt version that produced the reply, and it
        # is a hash of the full text rather than the truncated copy stored above.
        step["prompt_sha256"] = response["prompt_sha256"]
    if response.get("raw"):
        step["raw_response"] = response["raw"]
    # Kept apart from parse_error so the two stay countable. JSON that would not
    # parse is a formatting problem; JSON that parsed into the wrong shape is a
    # model behaviour problem, and they need different fixes.
    if response.get("schema_error"):
        step["schema_error"] = True

    # A tally of model calls that produced nothing usable, so the auto-clear
    # decision can refuse to run on a case where an agent failed. Without this,
    # a compliance reply that failed validation left compliance_status at
    # "UNKNOWN", which is not in ("BLOCKED", "REVIEW_REQUIRED"), so a shipment
    # could auto-clear on the strength of a screening that never happened.
    if response.get("parse_error"):
        case["_model_failures"] = case.get("_model_failures", 0) + 1
        failed = case.setdefault("_model_failure_agents", [])
        if agent not in failed:
            failed.append(agent)
    # Only the compliance step carries these (set in compliance_agent.py);
    # surfaced so the case trace can show the live Tavily lookup was real,
    # not just a claim in the docs.
    if "external_search_used" in response:
        step["external_search_used"] = response["external_search_used"]
        step["external_search_results"] = response.get("external_search_results", [])
    for field in (
        "external_search_status",
        "external_search_count",
        "external_search_cache_hits",
        "external_search_live",
    ):
        if field in response:
            step[field] = response[field]
    case["steps"].append(step)

    # Denormalized rollups, kept in sync with every step so a global
    # summary can be computed with Firestore sum()/count() aggregation
    # instead of fetching and re-scanning `steps[]` for every case in the
    # collection. sum() cannot reach into an array field, which is exactly
    # why these live on the case document itself.
    case["_agent_calls"] = case.get("_agent_calls", 0) + 1
    case["_input_tokens"] = case.get("_input_tokens", 0) + input_tokens
    case["_output_tokens"] = case.get("_output_tokens", 0) + output_tokens

    # Tavily searches, rolled up the same way and for the same reason the tokens are.
    #
    # Measured: 90-106 searches across 20 cases, 4.5-5.3 each, against $0.068 of model
    # spend. At the free tier's 1,000 credits a month that is 188-222 cases -- so the
    # external search, not the model, is what limits throughput, and it appeared in no
    # figure anywhere. `estimated_cost_usd` is Nebius only.
    #
    # Three counters rather than two derived from each other. Attempts, cache hits and
    # billable requests are genuinely independent: a cache hit costs nothing, and so
    # does a request that never left the process because no API key was configured.
    # Deriving billable as attempts-minus-hits made a deployment with no key report a
    # full bill for zero requests.
    case["_tavily_searches"] = (
        case.get("_tavily_searches", 0) + int(response.get("external_search_count") or 0)
    )
    case["_tavily_cached"] = (
        case.get("_tavily_cached", 0)
        + int(response.get("external_search_cache_hits") or 0)
    )
    case["_tavily_billable"] = (
        case.get("_tavily_billable", 0)
        + int(response.get("external_search_live") or 0)
    )
    if isinstance(latency_ms, int):
        case["_sum_latency_ms"] = case.get("_sum_latency_ms", 0) + latency_ms
    # One pricing implementation, called from here rather than repeated.
    #
    # This arithmetic previously existed in four places -- lineage.cost_usd(),
    # which nothing called, plus inline copies here, in the debate rollup below,
    # and in store.backfill_rollups(). A rate-card change had to be made
    # correctly in four files, and three of them were invisible from the one that
    # looked canonical.
    step_cost = lineage.cost_usd(model, input_tokens, output_tokens)
    # Per-step cost, so the trace can attribute spend to the individual model
    # call instead of only showing a case total.
    step["cost_usd"] = round(step_cost, 8)
    case["_estimated_cost_usd"] = case.get("_estimated_cost_usd", 0.0) + step_cost

    # Invalidate the cached spend total now that it is stale.
    #
    # Without this, the budget check's few-second cache would be the only thing
    # deciding when a breach is noticed, and a case running six agent hops back to
    # back could complete entirely inside one cache window. Dropping the entry here
    # means the next hop re-reads and refuses.
    budget.forget(_case_tenant(case))

    return result


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


async def _decide(
    case: dict[str, Any],
    outcome: str,
    rationale: str,
    actions: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """
    Record a Decision Pack and attempt to execute it through the gateway.

    This is the seam between analysis and authority. The workflow decides what
    *should* happen and says why; whether any of it actually happens is decided
    by `governance.check` against the published delegation boundary, not here.

    If every protected action is permitted, the outcome becomes official. If any
    is denied, the outcome remains a proposal and the case goes to PENDING_HUMAN
    with the denial reasons attached, so the reviewer sees what the agent wanted
    to do and why it was not allowed to.
    """
    case_id = case["case_id"]
    tenant = _case_tenant(case)

    case["decision_pack"] = {
        "proposed_outcome": outcome,
        "rationale": rationale,
        "proposed_actions": [name for name, _ in actions],
        "packs": synthesise_packs(case),
        "evidence": {
            "effective_risk": case.get("risk_score"),
            "model_risk": case.get("model_risk_score"),
            "risk_floor": (case.get("reconciliation") or {}).get("risk_floor"),
            "compliance_status": case.get("compliance_status"),
            "deterministic_findings": (case.get("validation") or {}).get("findings", []),
        },
        "proposed_at": utcnow(),
    }

    receipts = []
    denials = []
    for index, (name, kwargs) in enumerate(actions):
        receipt = await governance.execute(name, case, tenant_id=tenant, **kwargs)
        receipts.append(receipt)
        if receipt.get("status") == "denied":
            denials.append({
                "action": name,
                "reason": receipt.get("detail", {}).get("gate_reason"),
                "human_triggers": receipt.get("detail", {}).get("human_triggers", []),
            })
            # Stop the pack here instead of running the rest of it. Every action
            # after the substantive one is conditional on the outcome becoming
            # official, and publish_decision is always last - so letting the loop
            # continue announced `outcome: AUTO_CLEARED` on the case-decisions
            # topic for a shipment the boundary had just refused to release and
            # which was on its way to a human. A subscriber had no way to tell
            # that apart from a real release. The remaining actions are recorded
            # as skipped rather than dropped, so the trace says what was not
            # attempted and why.
            for skipped, _ in actions[index + 1:]:
                receipts.append({
                    "action": skipped,
                    "case_id": case_id,
                    "at": utcnow(),
                    "status": "skipped",
                    "detail": {"reason": f"not attempted: {name} was denied"},
                })
            break

    case["actions"].extend(receipts)

    if denials:
        case["state"] = "PENDING_HUMAN"
        case["proposed_outcome"] = outcome
        case["gate_denials"] = denials
        case["decision"] = None  # nothing is official until a human acts
        await emit(
            case_id,
            "gate_denied",
            f"Proposed {outcome} not executed: " + denials[0]["reason"],
            outcome="PENDING_HUMAN",
            tenant_id=tenant,
        )
        # Recorded even though nothing was executed. A gate denial is a decision
        # the system made and a customs authority may ask about it -- "the agent
        # wanted to release this and was refused" is exactly the kind of thing
        # that must not exist only in a log line.
        await lineage.record_decision(
            case, "gate_denied", outcome="PENDING_HUMAN", actor="governance",
            tenant_id=tenant,
        )
        return case

    case["decision"] = {
        "outcome": outcome,
        "rationale": rationale,
        "executed_under_boundary": next(
            (
                r.get("detail", {}).get("boundary_version")
                for r in receipts
                if r.get("detail", {}).get("boundary_version") is not None
            ),
            None,
        ),
    }
    case["state"] = outcome
    await emit(case_id, "decision", rationale[:180], outcome=outcome, tenant_id=tenant)
    # The lineage record for an automated decision. Written here rather than at
    # each call site so every outcome that becomes official carries one -- a
    # branch that forgot to call it would produce a released shipment with no
    # defensible trail, which is the failure this module exists to prevent.
    record = await lineage.record_decision(
        case, "agent_decision", outcome=outcome, tenant_id=tenant,
    )
    case["lineage_audit_id"] = record["audit_id"]
    return case


async def _send_agent_decision(case: dict[str, Any], limit: dict[str, Any]) -> dict[str, Any]:
    """
    Publish the keyed decision the settler pays from; if it cannot go out, the case goes to a person.

    It goes out before the case is stored, so a crash in between leaves a case the worker analyses again and never an auto-cleared
    case nobody can act on: the settler answers retry while the state is SPECIALISTS_DONE, and the second pass finds the key already
    published (tools.publish_decision) and sends nothing new.
    """
    from vf_logistics.evidence import published_message_id

    case_id = case["case_id"]
    tenant = _case_tenant(case)
    reason = "the agent's decision could not be sent"
    try:
        receipt = await governance.execute(
            "publish_decision",
            case,
            tenant_id=tenant,
            decision={
                "outcome": agent_payment.OUTCOME,
                "effective_risk": case.get("risk_score"),
                "executor": "agent",
                "approver": limit["published_by"],
                "agent_limit_usd": str(limit["limit_usd"]),
                "idempotency_key": agent_payment.key_for(case_id),
            },
        )
        case["actions"].append(receipt)
        if published_message_id(receipt) is not None:
            return case
    except Exception as exc:  # noqa: BLE001 - a decision that may not have gone out is a person's to take
        reason = f"the agent's decision could not be sent ({type(exc).__name__})"
    case["state"] = "PENDING_HUMAN"
    case["proposed_outcome"] = agent_payment.OUTCOME
    case["gate_denials"] = [{"action": "publish_decision", "reason": reason, "human_triggers": []}]
    case["decision"] = None
    await emit(
        case_id,
        "gate_denied",
        f"Proposed {agent_payment.OUTCOME} not executed: {reason}",
        outcome="PENDING_HUMAN",
        tenant_id=tenant,
    )
    await lineage.record_decision(
        case, "gate_denied", outcome="PENDING_HUMAN", actor="governance",
        tenant_id=tenant,
    )
    return case


async def _prefilter_rules(tenant_id: str | None) -> verifier.PrefilterRules:
    """
    The pre-AI screening rules for one tenant, or the bundled defaults.

    Read per advance() rather than cached in the module, which is the point of
    the change that introduced this: the five lists used to be module globals
    rebound by the governance route, so a tenant's edit applied to every tenant
    and a container restart silently reverted it. A keyed document read is cheap
    enough to do on the one transition that needs it.
    """
    stored = await get_store().get_prefilter_rules(tenant_id=tenant_id)
    return verifier.PrefilterRules.from_dict(stored)


async def advance(case: dict[str, Any]) -> dict[str, Any]:
    """
    Run exactly one transition for a case and persist the outcome.

    Uses optimistic locking to prevent lost updates when multiple workers
    attempt to advance the same case concurrently.
    """
    store = get_store()
    state = case["state"]
    case_id = case["case_id"]
    # Read off the case rather than taken as an argument: advance() is called by
    # the worker for every tenant in turn and by request handlers for one, and a
    # parameter would let those two disagree with the stored owner.
    tenant = _case_tenant(case)
    expected_version = case.get("_version")  # Capture version for optimistic lock

    # Declare whose budget this transition spends against, before any agent runs.
    # nebius_client._with_retry reads it to enforce the tenant spend ceiling, and it
    # is set here rather than passed down because it would otherwise have to thread
    # through every agent signature -- nine chances to forget one.
    budget.set_current_tenant(tenant)

    if state == "INGESTED":
        # ---------------------------------------------------------------
        # SQL Pre-processing Pipeline: Cost-Aware Hybrid Architecture
        #
        # Run the full deterministic battery up front, not just whitelist/
        # blacklist/low-value in isolation. verifier.validate()'s skip_ai
        # only fires when a clearance signal (whitelist, low-value
        # domestic) is present AND no other HIGH/CRITICAL finding
        # contradicts it - a whitelist claim riding alongside a freight
        # anomaly, a dual-use HS code, a high-risk destination, or an
        # identity mismatch does NOT get a free pass. This closes the
        # "type a known company name and skip every check" gap a
        # whitelist-only short-circuit would otherwise leave open.
        # ---------------------------------------------------------------
        # The rule set is read from this tenant's stored rules rather than from
        # module state. verifier.py used to hold the live lists as globals that
        # the governance screen rebound in place, so one customer's blacklist
        # edit changed what every customer's shipments were screened against.
        rules = await _prefilter_rules(tenant)
        validation = verifier.validate(case["shipment"], rules=rules)

        clearance_findings = [
            f for f in validation["findings"] if f.get("auto_clear_by_rules")
        ]
        if clearance_findings and not validation.get("skip_ai"):
            # A whitelist/low-value signal fired, but something else in the
            # record contradicts it - this is a more useful signal than an
            # ordinary grey-area case: someone (or something) claimed a
            # fast-track identity on a shipment that doesn't otherwise look
            # clean. Surface it distinctly rather than letting it blend into
            # routine AI-graded traffic.
            competing = [
                f for f in validation["findings"]
                if (f["severity"] in ("HIGH", "CRITICAL") or f.get("blocks_auto_clear"))
                and not f.get("skip_ai")
            ]
            await emit(
                case_id,
                "prefilter_downgraded",
                "Whitelist/low-value signal ("
                + ", ".join(f["code"] for f in clearance_findings)
                + ") present but overridden by "
                + ", ".join(f["code"] for f in competing)
                + " - routed to full AI/human review instead of auto-clear.",
                agent="sql_prefilter",
                tenant_id=tenant,
            )

        # The rules would clear this case with no analysis, and a payment needs one: the agent pays only on what the analysis stored,
        # and a person can only act on a case that was not cleared. The rules may still reject a payment.
        needs_analysis = payment_checks.is_payment_case(case["shipment"]) and not validation.get("auto_reject_by_rules")
        if validation.get("skip_ai") and not needs_analysis:
            auto_reject = bool(validation.get("auto_reject_by_rules"))
            action = "auto_reject" if auto_reject else "auto_clear"
            flag = "auto_reject_by_rules" if auto_reject else "auto_clear_by_rules"
            primary: dict[str, Any] = next((f for f in validation["findings"] if f.get(flag)), {})
            other_findings = [f for f in validation["findings"] if f is not primary]
            reason = primary.get("detail", "Cleared by SQL pre-filter rules")

            # Record the prefilter step (no AI, zero tokens)
            case.setdefault("steps", []).append({
                "agent": "sql_prefilter",
                "model": None,
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "at": utcnow(),
                "result": {
                    "action": action,
                    "finding": primary,
                    "other_findings": other_findings,
                },
            })

            # finding_count feeds governance's require_zero_deterministic_findings
            # gate on auto-release. We only reach this branch when
            # validation["skip_ai"] is True, which by construction already means
            # no other HIGH/CRITICAL finding is present - only the clearance
            # signal itself (CLEAR severity) and possibly harmless MEDIUM/LOW
            # informational findings (e.g. SHIPPER_HISTORY_UNVERIFIED, which
            # fires for any counterparty not in the internal book and carries no
            # risk signal on its own). Neither should count as a blocking finding,
            # or the gate would deny release_shipment for every genuinely clean
            # rule-cleared case. The auto_reject branch doesn't go through the
            # release gate at all (hold_shipment/assign_analyst, not
            # release_shipment), so finding_count there is for the record only.
            case["validation"] = {
                **validation,
                "finding_count": 0 if action == "auto_clear" else validation["finding_count"],
                "skip_ai": True,
                "cleared_by": "rules",
                "checks_run": validation.get("checks_run", []) + ["prefilter"],
            }
            # The release gate also checks reconciliation.auto_clear_permitted (the
            # same field verifier.reconcile() sets for the AI path). The prefilter
            # branch builds its own reconciliation here so the gate can auto-release
            # a rule-cleared case even though verifier.reconcile() itself never runs.
            case["reconciliation"] = {
                "effective_risk": 0 if action == "auto_clear" else 100,
                "model_risk": None,
                "risk_floor": validation["risk_floor"],
                "source": "sql prefilter",
                "score_disputed": False,
                "auto_clear_permitted": action == "auto_clear",
                "veto_reasons": [] if action == "auto_clear" else [reason],
            }
            
            if action == "auto_clear":
                case["risk_score"] = 0
                case["risk_level"] = "LOW"
                case["compliance_status"] = "COMPLIANT"
                case["compliance_score"] = 100
                case["cleared_by"] = "rules"  # Track for metrics
                
                await emit(
                    case_id,
                    "prefilter_clear",
                    f"Auto-cleared by SQL rules: {reason}",
                    agent="sql_prefilter",
                    risk_score=0,
                    tenant_id=tenant,
                )
                
                # Skip AI, go straight to AUTO_CLEARED
                case = await _decide(
                    case,
                    "AUTO_CLEARED",
                    f"Cleared by SQL pre-filter rules (zero AI cost): {reason}",
                    [
                        ("release_shipment", {
                            "shipment_id": case["shipment_id"],
                            "reason": f"Pre-filter rule: {primary.get('code', 'RULE_MATCH')}",
                        }),
                    ],
                )
                
            elif action == "auto_reject":
                case["risk_score"] = 100
                case["risk_level"] = "CRITICAL"
                case["compliance_status"] = "BLOCKED"
                case["compliance_score"] = 0
                case["cleared_by"] = "rules"  # Track for metrics
                
                await emit(
                    case_id,
                    "prefilter_reject",
                    f"Auto-rejected by SQL rules: {reason}",
                    agent="sql_prefilter",
                    risk_score=100,
                    tenant_id=tenant,
                )
                
                # Skip AI, escalate immediately
                case = await _decide(
                    case,
                    "ESCALATED",
                    f"Blocked by SQL pre-filter rules: {reason}",
                    [
                        ("hold_shipment", {
                            "shipment_id": case["shipment_id"],
                            "reason": f"Pre-filter blacklist: {primary.get('code', 'BLACKLIST_MATCH')}",
                        }),
                        ("assign_analyst", {"queue": "financial-crime", "priority": "HIGH"}),
                    ],
                )
            
            # Persist and return - no AI needed
            await store.put_case(
                case, expected_version=expected_version, tenant_id=tenant,
            )
            return case
        
        # ---------------------------------------------------------------
        # Grey Area: Needs AI analysis (Nemotron Nano + Tavily)
        # ---------------------------------------------------------------
        # The two screening disciplines are independent, so they run concurrently.
        # Sequentially this was two round trips of roughly seven seconds each;
        # in parallel a case reaches a decision in about half the wall time for
        # the same number of model calls.
        await emit(
            case_id,
            "agent_start",
            "Fraud detection and compliance screening running in parallel",
            agent="specialists",
            tenant_id=tenant,
        )

        # Route validation: search for disruptions on this shipping lane
        async def _route_search(shipment: dict[str, Any]) -> tuple[Any, Any, bool]:
            origin = shipment.get("origin_country") or shipment.get("origin", "")
            dest = shipment.get("destination_country") or shipment.get("destination", "")
            if not origin or not dest:
                return [], tavily_client.NOT_ATTEMPTED, False
            q = f"{origin} {dest} shipping route disruption OR port congestion OR sanctions"
            # Cached, and this is the one search where that needs no argument. The query
            # carries a country pair and nothing else -- no entity, no shipment, no value
            # -- so two shipments on the same lane are asking a question with one answer.
            # Measured on a 20-case run: 20 searches resolved to 6 distinct lanes, one of
            # them repeated 15 times.
            results, status, hit = await tavily_client.search_cached(
                q,
                max_results=3,
                ttl_seconds=tavily_client.ROUTE_CACHE_TTL_SECONDS,
            )
            return results, status, hit

        fraud_resp, compliance_resp, route_outcome = await asyncio.gather(
            analyze_shipment(case["shipment"]),
            # The floor is passed but no longer acts on anything: it fed a cache
            # bypass that measurement removed, because it fired on 91% of traffic and
            # so disabled the cache it was protecting. Kept at the call site so the
            # decision is cheap to revisit. It never influenced scoring.
            screen_shipment(
                case["shipment"], risk_floor=validation.get("risk_floor"),
            ),
            _route_search(case["shipment"]),
        )
        route_results, route_status, route_cached = route_outcome

        # The two checks the deterministic battery cannot make, run after the
        # first pair because both depend on `validation`: the HS check needs to
        # know what was declared, and the zero-day gate must not fire on a
        # shipment the official list has already matched.
        hs_verdict: dict[str, Any] | None = None
        zero_day_verdict: dict[str, Any] | None = None

        shipment = case["shipment"]
        if shipment.get("cargo_description") and shipment.get("hs_code"):
            try:
                hs_resp = await classify_hs(
                    str(shipment["cargo_description"]),
                    str(shipment["hs_code"]),
                    mode="cot_strict",
                )
                await _record_step(case, "hs_classifier", hs_resp)
                hs_verdict = interpret_hs(hs_resp)
                case["hs_classification"] = hs_verdict
            except Exception as exc:
                log.warning("HS classification failed for %s: %s", case_id, exc)
                # Synthesised rather than left as None. A None verdict makes
                # validate() skip the check entirely, so a failed call would be
                # indistinguishable from a description that matched its heading --
                # silence reading as a clearance, which is the failure mode this
                # whole layer exists to prevent. "unknown" produces
                # HS_DESCRIPTION_CHECK_UNAVAILABLE instead, which says on the case
                # that the comparison did not happen.
                hs_verdict = {
                    "verdict": "unknown",
                    "suggested_hs": None,
                    "confidence": 0.0,
                    "reasoning": f"{type(exc).__name__}: {exc}",
                    "obfuscation": None,
                }
                case["hs_classification"] = hs_verdict

        screen_now, gate_reason = should_screen_zero_day(shipment, validation)
        # Recorded either way. "We did not search, and here is why" belongs in an
        # audit trail as much as a finding does.
        case["zero_day_gate"] = {"screened": screen_now, "reason": gate_reason}
        if screen_now:
            try:
                zd_resp = await screen_zero_day(shipment)
                await _record_step(case, "zero_day", zd_resp)
                zero_day_verdict = interpret_zero_day(zd_resp)
                case["zero_day"] = zero_day_verdict
                for search in zd_resp.get("searches") or []:
                    case.setdefault("tavily_searches", []).append({
                        "type": "zero_day",
                        "query": search.get("query"),
                        "status": search.get("status"),
                        "results": search.get("result_count", 0),
                        "urls": search.get("urls") or [],
                        "at": utcnow(),
                    })
                    # Counted into the same rollup the compliance searches feed, so
                    # the per-case total is the whole bill rather than one agent's
                    # share. Not cached: this agent exists to catch what a stale list
                    # missed, so reusing a stale search would defeat it. Billable only
                    # when the request actually reached the API.
                    case["_tavily_searches"] = case.get("_tavily_searches", 0) + 1
                    if search.get("status") in tavily_client.BILLABLE_STATUSES:
                        case["_tavily_billable"] = case.get("_tavily_billable", 0) + 1
            except Exception as exc:
                log.warning("Zero-day screening failed for %s: %s", case_id, exc)
                # Same reasoning as the HS path above: the gate said this shipment
                # needed an adverse-media check, so a failed check must appear as
                # a check that did not happen rather than as nothing at all.
                zero_day_verdict = {
                    "verdict": "unknown",
                    "searched": False,
                    "confidence": 0.0,
                    "reasoning": f"{type(exc).__name__}: {exc}",
                    "evidence_urls": [],
                    "entities_checked": [],
                }
                case["zero_day"] = zero_day_verdict

        # The payment layer's checks run only on a payment case and may only raise the floor:
        # an answer that cannot be used adds no finding, and assess() never raises.
        payment_ai_verdict: dict[str, Any] | None = None
        if payment_checks.is_payment_case(shipment):
            payment_ai_verdict = await payment_ai.assess(shipment, tenant)
            case["payment_ai"] = payment_ai_verdict
            # The analyst is a model call like any other: its tokens go into the case cost, and the tenant budget sees them.
            usage = payment_ai_verdict.get("usage") or {}
            ai_in, ai_out = int(usage.get("tokens_in") or 0), int(usage.get("tokens_out") or 0)
            if ai_in or ai_out:
                case["_agent_calls"] = case.get("_agent_calls", 0) + 1
                case["_input_tokens"] = case.get("_input_tokens", 0) + ai_in
                case["_output_tokens"] = case.get("_output_tokens", 0) + ai_out
                case["_estimated_cost_usd"] = case.get("_estimated_cost_usd", 0.0) + lineage.cost_usd(
                    str(usage.get("model") or ""), ai_in, ai_out,
                )
                budget.forget(_case_tenant(case))

        # Re-derived so the two model findings take part in the ordinary floor and
        # corroboration arithmetic rather than being merged in afterwards. One code
        # path computes the floor; a second would drift out of agreement with it.
        validation = verifier.validate(
            shipment, hs_verdict=hs_verdict, zero_day=zero_day_verdict, payment_ai=payment_ai_verdict,
            rules=await _prefilter_rules(tenant),
        )

        # Attach route intelligence to case for downstream agents
        if route_results:
            case["route_intelligence"] = tavily_client.format_findings(
                route_results, route_status,
            )
            case.setdefault("tavily_searches", []).append({
                "type": "route_validation",
                "status": route_status,
                "results": len(route_results),
                "cached": route_cached,
                "at": utcnow(),
            })

        # Counted from the status rather than from re-checking the shipment fields.
        #
        # The earlier version re-tested origin/destination presence here, duplicating
        # `_route_search`'s own guard, and counted a credit whenever both were present --
        # including when the request never left the process because no API key was
        # configured, or when it timed out. The status is the only thing that knows
        # which of those happened, and it was being discarded.
        if route_status != tavily_client.NOT_ATTEMPTED:
            case["_tavily_searches"] = case.get("_tavily_searches", 0) + 1
            if route_cached:
                case["_tavily_cached"] = case.get("_tavily_cached", 0) + 1
            elif route_status in tavily_client.BILLABLE_STATUSES:
                case["_tavily_billable"] = case.get("_tavily_billable", 0) + 1

        fraud_result = await _record_step(case, "fraud_detection", fraud_resp)
        compliance_result = await _record_step(case, "compliance", compliance_resp)

        # Deterministic grounding validation. The agents' scores are claims; this
        # is the part of the system that checks them against arithmetic and
        # code-resident lists before anything acts on them. `validation` was
        # already computed above (before the AI calls) to decide the
        # fast-path branch; reused here so both branches see the same facts.
        reconciled = verifier.reconcile(fraud_result.get("risk_score"), validation)

        # Learning loop: adjust risk by how humans have ruled on this shipper
        # before. Deliberately applied after reconcile() so it can move the
        # effective risk but never lowers the deterministic floor itself -- the
        # clamp below keeps a trusted shipper from being discounted past 0, and
        # a rules-only auto-clear never reaches this branch at all. The shipment
        # is passed so a discount can rest on its verified identity rather than
        # on the name it claims; see shipper_risk_adjustment().
        shipper_name = shipment.get("shipper_name", "")
        adj = await shipper_risk_adjustment(
            shipper_name, tenant_id=tenant, shipment=shipment,
        )
        if adj != 0:
            feedback = await get_shipper_feedback(shipper_name, tenant_id=tenant) or {}
            verified = await get_verified_shipper_feedback(shipment, tenant_id=tenant)
            old_risk = reconciled["effective_risk"]
            # Clamped at the deterministic floor, not at 0. The comment above always
            # promised this and the code did not keep it: a shipper with five human
            # releases took -10 from a floor of 75, landing at 65 -- below
            # INVESTIGATE_AT, so a case the rules said to investigate was queued
            # instead. History may discount the model's opinion; it may not
            # discount a finding.
            floor = int(reconciled.get("risk_floor") or 0)
            reconciled["effective_risk"] = max(floor, min(100, old_risk + adj))
            reconciled["learning_adjustment"] = reconciled["effective_risk"] - old_risk
            reconciled["learning_note"] = (
                f"Risk {old_risk} -> {reconciled['effective_risk']} ({adj:+d} proposed) from "
                f"{feedback.get('released', 0)} human release(s) and "
                f"{feedback.get('blocked', 0)} human block(s) under this shipper name"
                + (
                    f"; {verified['released']} release(s) and {verified['blocked']} "
                    f"block(s) of its registry-verified identity"
                    if verified else ""
                )
                + (
                    f"; held at the deterministic floor of {floor}"
                    if old_risk + adj < floor else ""
                )
            )

        case["validation"] = validation
        case["reconciliation"] = reconciled
        case["model_risk_score"] = reconciled["model_risk"]
        case["risk_score"] = reconciled["effective_risk"]
        case["risk_level"] = fraud_result.get("risk_level") or "UNKNOWN"
        case["compliance_status"] = compliance_result.get("compliance_status") or "UNKNOWN"
        case["compliance_score"] = _as_int(compliance_result.get("compliance_score"), 50)
        case["grounding"] = grounding.classify(fraud_result.get("flags"), shipment, validation)
        case["state"] = "SPECIALISTS_DONE"

        if reconciled["source"] == "deterministic floor":
            await emit(
                case_id,
                "veto",
                f"Model scored {reconciled['model_risk']}; deterministic floor is "
                f"{reconciled['risk_floor']} from {validation['finding_count']} "
                f"hard finding(s). Effective risk {reconciled['effective_risk']}.",
                agent="verifier",
                risk_score=reconciled["effective_risk"],
                tenant_id=tenant,
            )

            # Auto-debate: when the model and the deterministic floor disagree by
            # a wide margin, put the Senior Auditor on it immediately rather than
            # waiting for a human to click Deep Review. conduct_debate reads the
            # fraud and compliance steps off the case, both of which _record_step
            # has already appended above, along with model_risk_score/risk_score.
            #
            # The model name in the event string is read from the result the debate
            # actually returned, so it reports the model that ran rather than the
            # model someone last wrote down. An earlier version hard-coded "Super"
            # while the hop ran Ultra, and the wrong name was copied into the
            # README, the architecture diagram and the Devpost submission.
            if reconciled.get("score_disputed"):
                try:
                    debate_result = await conduct_debate(case)
                    # Routed through _record_step rather than appended by hand so
                    # the debate's tokens land in the cost rollups. Appending
                    # directly left auto-debate spend out of _estimated_cost_usd
                    # entirely, understating the cost of the disputed-score path.
                    debate_out = await _record_step(case, "auto_debate", debate_result)
                    verdict = (debate_out or {}).get("verdict") or {}
                    case["auto_debate"] = debate_out
                    await emit(
                        case_id, "debate",
                        f"Auto-debate (score disputed by "
                        f"{reconciled['risk_floor'] - (reconciled['model_risk'] or 0)} "
                        f"points): "
                        f"{config.model_label(debate_result.get('model'))} returned "
                        f"{verdict.get('verdict') or 'no verdict'} in "
                        f"{debate_result.get('latency_ms', '?')}ms",
                        agent="debate",
                        tenant_id=tenant,
                    )
                    await _apply_debate_verdict(case, verdict, tenant)
                except Exception as exc:
                    log.warning(
                        "Auto-debate failed for %s: %s", case_id, exc, exc_info=True
                    )
        else:
            await emit(
                case_id,
                "agent_done",
                f"Fraud {case['risk_score']}/100, compliance "
                f"{case['compliance_status']} "
                f"({fraud_resp.get('latency_ms')}ms / "
                f"{compliance_resp.get('latency_ms')}ms in parallel); "
                f"deterministic checks agree",
                agent="specialists",
                risk_score=case["risk_score"],
                tenant_id=tenant,
            )

    elif state == "SPECIALISTS_DONE":
        risk = case.get("risk_score") or 0
        gate_risk = risk
        gate_note = ""
        case.pop("gate_risk", None)
        if AGENT_GROUNDED_GATE and payment_checks.is_payment_case(case["shipment"]):
            gate_risk, note = grounding.grounded_risk(risk, case.get("reconciliation"), case.get("grounding"))
            if note:
                case["gate_risk"] = gate_risk
                gate_note = " " + note
        reconciled = case.get("reconciliation") or {}
        # Read the way governance._check_release reads it: only an explicit True
        # from reconcile() permits. A case with no reconciliation has no record
        # that the deterministic checks agreed, and absence must not read as
        # consent.
        may_auto_clear = reconciled.get("auto_clear_permitted") is True
        status = (case.get("compliance_status") or "").upper()
        # An allowlist, not a denylist of the two known refusals. "UNKNOWN", an
        # off-schema value that slipped past validation, or a status from an
        # older record is a screening nobody can read as clean.
        compliance_cleared = status == ComplianceStatus.CLEARED.value

        # Any agent that returned nothing usable -- unparseable JSON, or JSON that
        # failed its schema after the retries in nebius_client were exhausted.
        # reconcile() already blocks auto-clear when the FRAUD score is missing,
        # but nothing blocked it when COMPLIANCE failed, and a shipment released
        # on a screening that did not happen is the worst outcome this system can
        # produce. A human gets it instead.
        failed_agents = case.get("_model_failure_agents") or []
        model_failed = bool(case.get("_model_failures"))

        clean = (
            gate_risk < FRAUD_CLEAR_BELOW
            and may_auto_clear
            and compliance_cleared
            and not model_failed
        )
        # Nobody can release an auto-cleared case, so a payment case is auto-cleared only when the agent may pay it; any other clean
        # payment case goes to a person, who can, instead of leaving the money held with no one able to act.
        agent_limit: dict[str, Any] | None = None
        agent_refusals: list[str] = []
        if clean and payment_checks.is_payment_case(case["shipment"]):
            agent_limit, agent_refusals = await agent_payment.review(case, case["shipment"], tenant)
            clean = not agent_refusals
        needs_investigation = (
            status in ("BLOCKED", "REVIEW_REQUIRED")
            or risk >= INVESTIGATE_AT
            # A confident, genuine DISAGREE from the Senior Auditor: see
            # _apply_debate_verdict. Upward only -- it can send a case to the
            # investigation it would otherwise have skipped, never away from one.
            or bool(case.get("debate_escalated"))
        )

        if model_failed:
            # Named before the routing so the trace says which agent failed and
            # why the case is with a human, rather than leaving a reviewer to
            # infer it from a risk score that no model produced.
            await emit(
                case_id,
                "veto",
                f"Model output unusable from {', '.join(failed_agents)} after "
                f"retries; auto-clear withheld and the case routed to a human. "
                f"The raw reply is on the step for inspection.",
                agent="schema_guard",
                tenant_id=tenant,
            )

        if clean:
            case["cleared_by"] = "ai"  # Track for metrics: cleared by Nemotron
            case = await _decide(
                case,
                "AUTO_CLEARED",
                f"Fraud risk {gate_risk}/100 is below the {FRAUD_CLEAR_BELOW} "
                f"threshold, compliance returned {case.get('compliance_status')}, "
                f"and deterministic validation raised nothing." + gate_note,
                [
                    ("release_shipment", {
                        "shipment_id": case["shipment_id"],
                        "reason": f"Fraud risk {gate_risk} below threshold "
                                  f"{FRAUD_CLEAR_BELOW}, no deterministic findings",
                    }),
                    ("publish_decision", {
                        "decision": {"outcome": "AUTO_CLEARED", "effective_risk": risk}
                    }),
                ],
            )
            # After _decide and never before it: sent first, this would announce a clearance the boundary may then refuse.
            if agent_limit is not None and case["state"] == "AUTO_CLEARED":
                case = await _send_agent_decision(case, agent_limit)
        elif agent_refusals:
            case = await _decide(
                case,
                "HELD_FOR_REVIEW",
                "Every check on this payment is clean, but the agent may not pay it: "
                + "; ".join(agent_refusals) + ". A person decides.",
                [
                    ("assign_analyst", {"queue": "trade-review", "priority": "NORMAL"}),
                    ("publish_decision", {
                        "decision": {"outcome": "HELD_FOR_REVIEW", "effective_risk": risk}
                    }),
                ],
            )
        elif not needs_investigation:
            # The rationale names what actually withheld the release. "Above the
            # threshold" would be false on the decision record of a low-risk case
            # held by a deterministic veto or by a compliance status that is not a
            # clearance.
            if model_failed:
                held_reason = (
                    f"Model output from {', '.join(failed_agents)} could not be "
                    f"validated after retries, so no automated decision is safe "
                    f"on this case."
                )
            elif not compliance_cleared:
                held_reason = (
                    f"Compliance returned "
                    f"{case.get('compliance_status') or 'no status'}, which is not a "
                    f"clearance, so the shipment is not released without a human."
                )
            elif risk < FRAUD_CLEAR_BELOW:
                held_reason = (
                    f"Effective risk {risk}/100 is under the auto-clear threshold, but "
                    f"deterministic validation did not permit an automated release, "
                    f"so a human reviewer is assigned."
                )
            else:
                held_reason = (
                    f"Compliance cleared the shipment but effective risk {risk}/100 is "
                    f"above the auto-clear threshold, so a human reviewer is assigned."
                )
            case = await _decide(
                case,
                "HELD_FOR_REVIEW",
                held_reason,
                [
                    ("assign_analyst", {
                        "queue": "trade-review",
                        "priority": (
                            "HIGH" if model_failed or not compliance_cleared else "NORMAL"
                        ),
                    }),
                    ("publish_decision", {
                        "decision": {"outcome": "HELD_FOR_REVIEW", "effective_risk": risk}
                    }),
                ],
            )
        else:
            escalated_by_debate = bool(case.get("debate_escalated")) and not (
                status in ("BLOCKED", "REVIEW_REQUIRED") or risk >= INVESTIGATE_AT
            )
            await emit(
                case_id,
                "agent_start",
                (
                    f"Senior Auditor disagreed with the junior analyst at risk {risk}; "
                    "opening deep investigation on Nemotron 3 Super"
                    if escalated_by_debate else
                    f"Compliance {status} at risk {risk}, opening deep investigation "
                    "on Nemotron 3 Super"
                ),
                agent="investigation",
                tenant_id=tenant,
            )
            response = await investigate_case(_investigation_payload(case))
            result = await _record_step(case, "investigation", response)

            case["investigation"] = result
            case["state"] = "INVESTIGATED"
            await emit(
                case_id,
                "agent_done",
                f"Investigation complete in {response.get('latency_ms')}ms: "
                f"{result.get('fraud_pattern') or 'pattern inconclusive'}",
                agent="investigation",
                tenant_id=tenant,
            )

    elif state == "INVESTIGATED":
        inv = case.get("investigation") or {}
        narrative = str(inv.get("summary") or "No narrative returned.")
        exposure = str(inv.get("exposure_estimate") or "unquantified")

        # Check the figure the agent put on the case before it reaches a filing.
        exposure_flag = verifier.check_exposure_claim(case["shipment"], inv)
        if exposure_flag:
            case.setdefault("validation", {}).setdefault("findings", []).append(
                exposure_flag
            )
            await emit(
                case_id,
                "veto",
                exposure_flag["detail"],
                agent="verifier",
                tenant_id=tenant,
            )

        case = await _decide(
            case,
            "ESCALATED",
            f"Investigation identified "
            f"{inv.get('fraud_pattern') or 'suspicious activity'} with estimated "
            f"exposure {exposure}. Shipment held, SAR drafted for human signoff.",
            [
                ("hold_shipment", {
                    "shipment_id": case["shipment_id"],
                    "reason": "Escalated after deep investigation",
                }),
                ("draft_sar", {
                    "shipment_id": case["shipment_id"],
                    "narrative": narrative,
                    "exposure": exposure,
                }),
                ("notify_webhook", {
                    "title": f"Escalation: {case['shipment_id']}",
                    "body": narrative,
                    "severity": "CRITICAL",
                }),
                ("assign_analyst", {"queue": "financial-crime", "priority": "HIGH"}),
                ("publish_decision", {
                    "decision": {
                        "outcome": "ESCALATED",
                        "fraud_pattern": inv.get("fraud_pattern"),
                        "exposure_estimate": exposure,
                    }
                }),
            ],
        )

    case["updated_at"] = utcnow()
    case["claimed"] = False
    await store.put_case(
        case, expected_version=expected_version, tenant_id=tenant,
    )
    return case


def debate_escalate_confidence() -> float:
    """
    The confidence a DISAGREE needs before it changes routing.

    Per call, from DEBATE_ESCALATE_CONFIDENCE. An unreadable or out-of-range value
    falls back to the default rather than to 0: a typo must not turn every
    low-confidence disagreement into an escalation, nor -- at 1.1 -- silently
    switch the whole mechanism off.
    """
    raw = os.getenv("DEBATE_ESCALATE_CONFIDENCE", "")
    try:
        value = float(raw)
    except ValueError:
        return DEBATE_ESCALATE_CONFIDENCE_DEFAULT
    return value if 0.0 <= value <= 1.0 else DEBATE_ESCALATE_CONFIDENCE_DEFAULT


def debate_escalation(verdict: dict[str, Any] | None) -> tuple[bool, str]:
    """
    Whether an auto-debate verdict escalates the case, and why or why not.

    The auto-debate only runs when the deterministic floor outscored the junior
    analyst by 15 points or more, and it asks the Senior Auditor one question:
    did the junior analyst miss something? DISAGREE means yes. Until this
    function existed the answer was recorded on the case and read by nothing --
    routing used the floor alone, so the most expensive model call in the
    pipeline could not change a single outcome.

    It now can, in ONE direction. A DISAGREE the model genuinely rendered, at or
    above debate_escalate_confidence(), sends the case to the investigation it
    would otherwise have skipped. Nothing else here can move a case:

      * CONFIRM does nothing. It agrees with a junior analyst the floor had
        already outvoted, and the floor stands either way.
      * A forced verdict does nothing. debate_agent fills one in when the model
        produced no usable answer, and a default is not a judgement.
      * A DISAGREE below the threshold does nothing, and says so on the case.

    So a manipulated or mistaken Senior Auditor can at worst cost an
    investigation on a case a human was already going to see. It cannot release
    one. That asymmetry is the same argument verifier.check_hs_description_
    consistency() makes for the HS classifier.
    """
    if not isinstance(verdict, dict) or not verdict:
        return False, "no verdict was returned"
    if verdict.get("forced"):
        return False, (
            "the verdict was a default filled in by the system "
            f"({verdict.get('forced_reason') or 'unspecified'}), not a judgement"
        )
    if verdict.get("verdict") != "DISAGREE":
        return False, "the Senior Auditor confirmed the junior analyst's assessment"
    raw_confidence = verdict.get("confidence")
    if raw_confidence is None:
        return False, "DISAGREE with no readable confidence"
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        return False, "DISAGREE with no readable confidence"
    if not 0.0 <= confidence <= 1.0:
        return False, f"DISAGREE with an out-of-range confidence ({confidence})"
    threshold = debate_escalate_confidence()
    if confidence < threshold:
        return False, (
            f"DISAGREE at {confidence:.2f}, below the {threshold:.2f} needed to act on it"
        )
    return True, f"DISAGREE at {confidence:.2f} (threshold {threshold:.2f})"


async def _apply_debate_verdict(
    case: dict[str, Any], verdict: dict[str, Any] | None, tenant: str | None,
) -> None:
    """
    Record what the auto-debate changed, and change it.

    `case["debate_effect"]` is written whether or not anything changed, so a case
    trace always answers "did the debate matter here, and why". When the verdict
    escalates, the case is marked for investigation and its risk may rise to the
    auditor's adjusted_risk_score -- never fall to it. The auditor's number is an
    opinion about the junior analyst's score; the effective risk is already at
    least the deterministic floor, and an opinion does not lower a floor.
    """
    escalates, reason = debate_escalation(verdict)
    before = int(case.get("risk_score") or 0)
    after = before

    if escalates:
        adjusted = (verdict or {}).get("adjusted_risk_score")
        try:
            proposed = int(round(float(adjusted))) if adjusted is not None else None
        except (TypeError, ValueError):
            proposed = None
        if proposed is not None:
            after = max(before, min(100, proposed))

        # The invariant, stated where it is established rather than hoped for:
        # nothing in this function may lower the risk a case already carried.
        if after < before:
            raise AssertionError(f"debate lowered risk {before} -> {after}")

        case["debate_escalated"] = True
        case["risk_score"] = after
        reconciled = case.get("reconciliation") or {}
        reconciled["effective_risk"] = after
        if after > before:
            reconciled["raised_by"] = "debate"
        await emit(
            case["case_id"],
            "debate_escalation",
            f"Senior Auditor disagreed ({reason}); routed to deep investigation"
            + (f", effective risk {before} -> {after}" if after > before else ""),
            agent="debate",
            risk_score=after,
            tenant_id=tenant,
        )

    case["debate_effect"] = {
        "escalated": escalates,
        "reason": reason,
        "threshold": debate_escalate_confidence(),
        "risk_before": before,
        "risk_after": after,
    }


def _investigation_payload(case: dict[str, Any]) -> dict[str, Any]:
    """Build the nested case envelope the investigation agent expects."""
    shipment = case.get("shipment", {})
    fraud_step: dict[str, Any] = next(
        (s for s in case.get("steps", []) if s["agent"] == "fraud_detection"), {}
    )
    compliance_step: dict[str, Any] = next(
        (s for s in case.get("steps", []) if s["agent"] == "compliance"), {}
    )
    fraud_result = fraud_step.get("result", {})
    compliance_result = compliance_step.get("result", {})

    triggers = []
    # First, when it is the reason this investigation exists at all: the case's
    # scores alone would have queued it for review instead.
    if case.get("debate_escalated"):
        verdict = ((case.get("auto_debate") or {}).get("verdict")) or {}
        rationale = str(verdict.get("rationale") or "no rationale given")[:400]
        triggers.append(f"Senior Auditor disagreed with the junior analyst: {rationale}")
    for flag in fraud_result.get("flags", []) or []:
        triggers.append(flag if isinstance(flag, str) else str(flag.get("description") or flag))
    for factor in compliance_result.get("risk_factors", []) or []:
        triggers.append(str(factor))

    return {
        "case_id": case["case_id"],
        "trigger_reason": "; ".join(triggers[:6]) or "Elevated fraud and compliance risk",
        "risk_score": case.get("risk_score"),
        "primary_shipment": shipment,
        "primary_entity": {
            "name": shipment.get("shipper_name"),
            "company": shipment.get("shipper_company"),
            "country": shipment.get("shipper_country"),
            "tax_id": shipment.get("shipper_tax_id"),
            "transaction_count": shipment.get("shipper_tx_count"),
        },
        "historical_alerts": compliance_result.get("regulatory_issues", []),
        "total_volume": shipment.get("declared_value"),
        "avg_transaction": shipment.get("avg_route_cost"),
        "anomaly_count_30d": len(fraud_result.get("flags", []) or []),
    }


async def sweep_bucket(
    prefix: str, limit: int, tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Process documents already on the stage.

    Objects whose case already exists are skipped rather than reprocessed, which
    is what makes repeated sweeps safe to run.

    Under MULTI_TENANT the prefix is read inside the caller's own space
    (tenants/<tenant_id>/), so "inbox/" sweeps that tenant's inbox. The listing
    is narrowed that way for efficiency only; each object is still checked by
    ingest_refusal(), which is what keeps other tenants' objects and the
    service's own output out of this board whatever prefix was asked for.
    """
    root = document_store.tenant_root(tenant_id)
    if not prefix.startswith(root):
        prefix = root + prefix
    names = await document_store.list_objects(prefix, limit * 3)
    store = get_store()

    processed: list[dict[str, Any]] = []
    skipped: list[str] = []

    for name in names:
        if len(processed) >= limit:
            break
        if document_store.ingest_refusal(document_store.BUCKET, name, tenant_id):
            continue

        marker = await store.get_case(
            f"CASE-DOCOBJ-{_object_key(name)}", tenant_id=tenant_id,
        )
        if marker:
            skipped.append(name)
            continue

        result = await ingest_from_storage(
            document_store.BUCKET, name, tenant_id=tenant_id,
        )
        processed.append({
            "object": name,
            "case_id": result.get("case_id"),
            "blocked": result.get("blocked", False),
            "accepted": result.get("accepted", False),
        })

    return {
        "prefix": prefix,
        "processed": processed,
        "processed_count": len(processed),
        "skipped_already_seen": skipped,
    }


def _object_key(name: str) -> str:
    """A stable, id-safe key for an object path."""
    import hashlib

    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


async def ingest_from_storage(
    bucket: str, name: str, tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Read an object off the stage and run it through document intake.

    A marker case is written under the object's hash so a repeated notification
    or sweep does not process the same file twice. Pub/Sub delivers at least
    once, so this is not optional.

    The marker is tenant-scoped along with everything else, which means two
    tenants staging the same object path each get their own marker rather than
    the second silently seeing the first's as a duplicate and processing nothing.

    Refuses, before reading anything, an object that does not belong in this
    tenant's board (see document_store.ingest_refusal). Both callers check
    first; this is the check that cannot be skipped by a third.
    """
    refusal = document_store.ingest_refusal(bucket, name, tenant_id)
    if refusal:
        return {"accepted": False, "ignored": True, "reason": refusal}

    store = get_store()
    marker_id = f"CASE-DOCOBJ-{_object_key(name)}"

    existing = await store.get_case(marker_id, tenant_id=tenant_id)
    if existing:
        return {
            "accepted": False,
            "duplicate": True,
            "case_id": existing.get("linked_case_id") or marker_id,
            "reason": f"gs://{bucket}/{name} has already been processed",
        }

    data, content_type = await document_store.fetch(f"gs://{bucket}/{name}")
    if data is None:
        return {"accepted": False, "error": f"could not read gs://{bucket}/{name}"}

    filename = name.rsplit("/", 1)[-1]
    result = await ingest_document(data, filename, content_type, tenant_id=tenant_id)

    # Write the marker only after intake, so a failed read can be retried.
    await store.put_case({
        "case_id": marker_id,
        "shipment_id": marker_id,
        "state": "OBJECT_PROCESSED",
        "claimed": False,
        "attempts": 0,
        "not_before": None,
        "is_marker": True,
        "object": f"gs://{bucket}/{name}",
        "linked_case_id": result.get("case_id"),
        "steps": [],
        "actions": [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }, tenant_id=tenant_id)

    result["source_object"] = f"gs://{bucket}/{name}"
    return result


# --------------------------------------------------------------------------
# Human review
# --------------------------------------------------------------------------

HUMAN_ACTIONS = {
    "release": ("RELEASED_BY_HUMAN", "release_shipment"),
    "block": ("BLOCKED_BY_HUMAN", "hold_shipment"),
    "request_info": ("PENDING_HUMAN", None),
}


async def _publish_human_decision(
    case_id: str, action: str, reviewer: str, outcome: str, tenant_id: str | None,
) -> dict[str, Any]:
    """Send a person's release or block of a booked payment to the case-decisions topic.

    The settler captures or voids only from a published decision, never from the case state, so a decision that
    stayed in the store left the PayPal hold open. The key is fixed per case and action, so a repeat is
    deduplicated downstream. A failure never undoes the person's decision: it is returned, so the console can say
    the money has not moved.
    """
    from vf_logistics.evidence import published_message_id

    try:
        receipt = await tools.publish_decision(
            case_id,
            {
                "outcome": outcome,
                "action": action,
                "approver": reviewer,
                "executor": "human_decide",
                "idempotency_key": f"human-{case_id}-{action}",
            },
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        return {"published": False, "publish_error": f"decision was not published: {type(exc).__name__}: {exc}"}
    if published_message_id(receipt) is None:
        detail = (receipt or {}).get("detail") or {}
        return {"published": False, "publish_error": f"decision was not published: {detail.get('error') or 'no message id'}"}
    return {"published": True}


async def human_decide(
    case_id: str, action: str, reviewer: str, note: str,
    tenant_id: str | None = None,
    actor_auth: str | None = None,
) -> dict[str, Any]:
    """
    Apply a named human's decision to a case.

    Deliberately not routed through the delegation boundary: a boundary
    constrains what the *agent* may do on its own, and a human reviewer is the
    authority the boundary derives from. What the human cannot do is act
    anonymously - `reviewer` is required, and a note is required for anything
    other than a plain release, because a refusal that nobody has to justify is
    not a control.

    `actor_auth` is how the reviewer signed in (password, one_click, ...), written
    beside their name so a decision taken from a public one-click session can be
    told apart from one taken with a password.
    """
    store = get_store()

    if action not in HUMAN_ACTIONS:
        return {"ok": False, "error": f"unknown action '{action}'"}
    if not reviewer.strip():
        return {"ok": False, "error": "reviewer is required"}
    if action != "release" and not note.strip():
        return {"ok": False, "error": f"a note is required when action is '{action}'"}

    # Scoped: without the tenant, a guessed case_id lets a reviewer in one tenant
    # release or block another tenant's shipment, and put_case below preserves the
    # stored owner -- so the decision would succeed and be recorded against the
    # victim's case. get_case returns None rather than raising on a foreign id,
    # deliberately, so id enumeration gets the same answer as a genuine 404.
    case = await store.get_case(case_id, tenant_id=tenant_id)
    if not case:
        return {"ok": False, "error": "case not found"}
    expected_version = case.get("_version")  # Capture for optimistic lock

    if case.get("state") not in AWAITING_HUMAN:
        return {
            "ok": False,
            "error": f"case is in state {case.get('state')}, which is not awaiting review",
        }

    # N07: a case with a verification record must go through
    # evidence.approve + evidence.execute_approval. Checking existence (not
    # just version > 0) closes the loophole where a case at version 0 could
    # still be released through the legacy API.
    if case.get("verification") is not None:
        return {
            "ok": False,
            "error": (
                "This case has a verification record. "
                "Use /approve and /execute instead."
            ),
        }

    # N07: password required for ALL releases, not just verification cases.
    if action == "release" and actor_auth != "password":
        return {
            "ok": False,
            "error": "Releasing a shipment needs a password sign-in.",
        }

    new_state, tool_action = HUMAN_ACTIONS[action]

    receipts = []
    if tool_action == "release_shipment":
        receipts.append(await tools.release_shipment(
            case_id, case["shipment_id"],
            f"Released by {reviewer}: {note or 'no note'}",
            tenant_id=tenant_id,
        ))
    elif tool_action == "hold_shipment":
        receipts.append(await tools.hold_shipment(
            case_id, case["shipment_id"], f"Blocked by {reviewer}: {note}",
            tenant_id=tenant_id,
        ))

    # Any SAR draft on this case now carries a signature.
    signed = None
    for existing in case.get("actions", []):
        if existing.get("action") == "draft_sar" and existing.get("status") == "done":
            existing.setdefault("detail", {})["requires_human_signoff"] = False
            existing["detail"]["signed_by"] = reviewer
            existing["detail"]["signed_at"] = utcnow()
            signed = existing["detail"].get("reference")

    review = {
        "action": action,
        "reviewer": reviewer,
        "note": note,
        "at": utcnow(),
        "state_before": case.get("state"),
        "state_after": new_state,
        "agent_proposed": case.get("proposed_outcome") or (
            (case.get("decision") or {}).get("outcome")
        ),
        "sar_signed": signed,
    }

    case.setdefault("reviews", []).append(review)
    case["actions"].extend(receipts)
    case["state"] = new_state
    case["updated_at"] = utcnow()
    case["claimed"] = False

    if action != "request_info":
        case["decision"] = {
            "outcome": new_state,
            "rationale": f"{reviewer} chose to {action}. {note}".strip(),
            "decided_by": "human",
        }

    await store.put_case(
        case, expected_version=expected_version, tenant_id=tenant_id,
    )
    # Two records, because they answer different questions. The thin one below
    # says a named human chose an action; the lineage record says what evidence
    # was in front of them -- which list version, which model, which prompt --
    # and is the one a customs authority reads three years later.
    await store.add_audit({
        "audit_id": new_id("audit"),
        "case_id": case_id,
        "action": f"human_{action}",
        "status": "done",
        # Named at the top level as well as inside `detail`, because the audit
        # list reads `actor` and this row was rendering with a blank one -- on the
        # single row type whose whole purpose is to say who decided. The name was
        # present in detail.reviewer all along, one level too deep for the column
        # that a customs officer actually looks at.
        "actor": reviewer,
        "actor_auth": actor_auth,
        "detail": review,
        "at": utcnow(),
    }, tenant_id=tenant_id)
    await lineage.record_decision(
        case, f"human_{action}", outcome=new_state, actor=reviewer,
        tenant_id=tenant_id,
    )
    await emit(
        case_id,
        "human_decision",
        f"{reviewer} chose {action}"
        + (f" (agent had proposed {review['agent_proposed']})"
           if review["agent_proposed"] else ""),
        outcome=new_state,
        tenant_id=tenant_id,
    )

    # Learning loop: this decision is now part of the shipper's history, so the
    # derived tallies must be re-read rather than served from the stale cache.
    _invalidate_shipper_feedback()

    result: dict[str, Any] = {"ok": True, "case_id": case_id, "state": new_state, "review": review}
    if action in ("release", "block") and (case.get("shipment") or {}).get("paypal_authorization_id"):
        result.update(await _publish_human_decision(case_id, action, reviewer, new_state, tenant_id))
    return result


# --------------------------------------------------------------------------
# Learning loop: human review history feeds back into risk scoring
# --------------------------------------------------------------------------
#
# Derived from the cases themselves rather than kept in a counter, because a
# counter in process memory is wrong in both directions on Cloud Run: it resets
# on every cold start, and each instance accumulates its own totals, so the same
# shipper gets a different adjustment depending on which instance answers. The
# terminal state of a reviewed case is already durable and already the source of
# truth for "what did a human decide", so the history is read from there.
#
# The cache is a read-through TTL cache only. Losing it costs one query, not
# correctness. Every writer of a human decision (human_decide,
# evidence.execute_approval) drops it, but only in its own process, so on its
# own it would let another instance miss a decision for up to the TTL.
# shipper_risk_adjustment() therefore refreshes it before every decision; the
# cache serves only the reads that follow in the same scoring step.

_FEEDBACK_OUTCOMES = {"RELEASED_BY_HUMAN": "released", "BLOCKED_BY_HUMAN": "blocked"}
_FEEDBACK_STATES = tuple(_FEEDBACK_OUTCOMES)
_FEEDBACK_TTL_SECONDS = 30.0
_FEEDBACK_SCAN_LIMIT = 500

# Keyed by tenant, not by shipper alone. A single shared dict was a verdict-
# changing leak rather than a display one: the tallies feed
# shipper_risk_adjustment(), which moves the effective risk score by up to 15
# points, so one customer's reviewers blocking "Acme Trading" twice would have
# raised the risk of a different customer's unrelated "Acme Trading".
#
# Each tenant's entry holds two tallies over the same cases: "by_name", keyed on
# the free-text shipper_name, and "by_identity", keyed on the counterparty-book
# tax ID of shipments shipper_registry verified. shipper_risk_adjustment() says
# why a discount may only come from the second.
_feedback_cache: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
_feedback_cache_at: dict[str, float] = {}


def _shipper_key(shipper_name: str | None) -> str:
    return (shipper_name or "").strip().lower()


def _verified_identity(shipment: dict[str, Any] | None) -> str:
    """
    The counterparty-book key of a shipper the registry verified, else "".

    shipper_registry.enrich() writes shipper_identity_status at intake, from the
    book and after sanitisation, so a document cannot assert it. "verified" means
    the tax ID and the company name both matched one entry, and the normalised
    tax ID is that entry's key.
    """
    if not isinstance(shipment, dict):
        return ""
    if shipment.get("shipper_identity_status") != "verified":
        return ""
    return shipper_registry._norm_tax_id(shipment.get("shipper_tax_id"))


async def _load_shipper_feedback(
    force: bool = False, tenant_id: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Rebuild the release/block tallies, by shipper name and by verified identity."""
    key = tenant_id or ""
    at = _feedback_cache_at.get(key, 0.0)
    fresh = (time.monotonic() - at) < _FEEDBACK_TTL_SECONDS
    if at and fresh and not force and key in _feedback_cache:
        return _feedback_cache[key]

    store = get_store()
    tallies: dict[str, dict[str, dict[str, Any]]] = {"by_name": {}, "by_identity": {}}
    cursor = None
    scanned = 0

    while scanned < _FEEDBACK_SCAN_LIMIT:
        page, cursor = await store.query_cases(
            states=_FEEDBACK_STATES, cursor=cursor, limit=100,
            tenant_id=tenant_id,
        )
        if not page:
            break
        for case in page:
            outcome = _FEEDBACK_OUTCOMES.get(str(case.get("state")))
            if outcome is None:
                continue
            shipment: dict[str, Any] = case.get("shipment") or {}
            # `who`, not `key`: `key` is the tenant this result is cached under,
            # and rebinding it here would file the tallies under whichever
            # shipper came last, where another tenant's lookup could find them.
            for tally, who in (
                (tallies["by_name"], _shipper_key(shipment.get("shipper_name"))),
                (tallies["by_identity"], _verified_identity(shipment)),
            ):
                if who:
                    row = tally.setdefault(who, {"released": 0, "blocked": 0})
                    row[outcome] += 1
        scanned += len(page)
        if not cursor:
            break

    for tally in tallies.values():
        for row in tally.values():
            total = row["released"] + row["blocked"]
            row["clearance_rate"] = (row["released"] / total) if total else 0.0

    _feedback_cache[key] = tallies
    _feedback_cache_at[key] = time.monotonic()
    return tallies


def _invalidate_shipper_feedback() -> None:
    """Drop the cache so the next read reflects a decision just recorded."""
    _feedback_cache_at.clear()


async def get_shipper_feedback(
    shipper_name: str, tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Release/block history under a shipper name, or None if a human never ruled."""
    key = _shipper_key(shipper_name)
    if not key:
        return None
    return (await _load_shipper_feedback(tenant_id=tenant_id))["by_name"].get(key)


async def get_verified_shipper_feedback(
    shipment: dict[str, Any] | None, tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Release/block history of the registry-verified shipper behind `shipment`.

    None when the shipment's identity is not verified, or when no human has ruled
    on a shipment verified as that same counterparty.
    """
    identity = _verified_identity(shipment)
    if not identity:
        return None
    return (await _load_shipper_feedback(tenant_id=tenant_id))["by_identity"].get(identity)


async def shipper_risk_adjustment(
    shipper_name: str,
    tenant_id: str | None = None,
    shipment: dict[str, Any] | None = None,
) -> int:
    """
    Risk adjustment implied by how humans have historically ruled on a shipper.

    Positive raises effective risk, negative lowers it:
    - blocked 2+ times by a human -> +15
    - released 5+ times by a human -> -10, for a registry-verified shipper only

    Blocks are checked first and with a lower threshold on purpose: a shipper a
    human has stopped twice should not be discounted because it also has a long
    tail of releases.

    The two directions rest on different evidence because they fail differently.
    The penalty may key on the free-text shipper_name: raising risk is always
    safe, and a wrong name match costs a review. The discount can carry a
    borderline score under FRAUD_CLEAR_BELOW and release cargo, so it requires
    `shipment` to carry an identity shipper_registry verified (tax ID and company
    name together) and counts only human releases of that same verified
    counterparty. Keyed on the name, a document could borrow any shipper's
    history by typing its name, which is the impersonation the registry exists to
    refuse; shipper_name is not even a field the registry checks. A caller that
    passes no shipment gets the penalty and never the discount.
    """
    # Read fresh. This is the one reader whose answer moves a release decision,
    # and a cached tally can only be missing decisions recorded since it was
    # built -- on another Cloud Run instance, whose invalidation never reaches
    # this one. A missed second block is a missed +15, which can let a
    # borderline shipment clear. The rescan is one bounded query per scored
    # case; the reads below, and the learning note, then hit what it cached.
    await _load_shipper_feedback(force=True, tenant_id=tenant_id)
    named = await get_shipper_feedback(shipper_name, tenant_id=tenant_id) or {}
    verified = await get_verified_shipper_feedback(shipment, tenant_id=tenant_id) or {}
    if named.get("blocked", 0) >= 2 or verified.get("blocked", 0) >= 2:
        return 15
    if verified.get("released", 0) >= 5:
        return -10
    return 0


async def deep_review(case_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Conduct a Multi-Agent Debate on a case.

    A Senior Auditor (debate_agent.MODEL_ID, Nemotron 3 Super by default) reviews
    Nemotron Nano's (Junior Analyst) fraud assessment. It can use function calling to:
    - Request Nano to re-evaluate with specific focus areas
    - Run additional Tavily searches for context
    - Render a final verdict: CONFIRM or DISAGREE

    This is an expensive, opt-in operation triggered by an analyst clicking
    "Deep Review" on a specific case in the Review Queue.
    """
    store = get_store()
    # Scoped for the same reason as human_decide, plus one of its own: this spends
    # debate-model tokens and returns a debate payload containing the case
    # content, so an unscoped lookup bills one tenant to read another's shipment.
    case = await store.get_case(case_id, tenant_id=tenant_id)

    if not case:
        return {"ok": False, "error": "case not found"}

    if case.get("state") not in AWAITING_HUMAN:
        return {
            "ok": False,
            "error": f"case must be awaiting human review, current state: {case['state']}",
        }

    expected_version = case.get("_version")

    # Read off the stored case rather than the argument, for the same reason advance()
    # does: the case's own owner is the authority on whose budget this spends. Deep
    # review runs the debate agent, whose model is debate_agent.MODEL_ID (Nemotron 3 Super
    # by default; DEBATE_MODEL can put Ultra there). The name in the event string below
    # is read from MODEL_ID so it cannot drift from what ran.
    budget.set_current_tenant(_case_tenant(case))

    await emit(
        case_id,
        "debate_start",
        f"Senior Auditor ({config.model_label(debate_agent.MODEL_ID)}) reviewing "
        f"Junior Analyst ({config.model_label(config.get_model())}) assessment",
        agent="debate",
        tenant_id=tenant_id,
    )

    try:
        debate_result = await conduct_debate(case)
    except Exception as e:
        await emit(case_id, "debate_error", f"Debate failed: {e}", agent="debate", tenant_id=tenant_id)
        return {"ok": False, "error": str(e)}

    # Record as a step in the case trace
    step = {
        "agent": "debate",
        "model": debate_result.get("model"),
        "latency_ms": debate_result.get("latency_ms"),
        "input_tokens": debate_result.get("input_tokens"),
        "output_tokens": debate_result.get("output_tokens"),
        "at": debate_result.get("at") or utcnow(),
        "result": debate_result.get("result"),
    }
    case.setdefault("steps", []).append(step)
    case["debate"] = debate_result.get("result")
    case["updated_at"] = utcnow()

    # Update rollups for cost tracking
    case["_agent_calls"] = case.get("_agent_calls", 0) + 1
    case["_input_tokens"] = case.get("_input_tokens", 0) + debate_result.get("input_tokens", 0)
    case["_output_tokens"] = case.get("_output_tokens", 0) + debate_result.get("output_tokens", 0)
    case["_estimated_cost_usd"] = case.get("_estimated_cost_usd", 0.0) + lineage.cost_usd(
        debate_result.get("model", ""),
        debate_result.get("input_tokens", 0),
        debate_result.get("output_tokens", 0),
    )
    # Deep review does not go through _record_step, so the cached spend total is
    # invalidated here for the same reason it is there: the next model call must
    # see this spend rather than a pre-debate figure.
    budget.forget(_case_tenant(case))

    verdict = (debate_result.get("result") or {}).get("verdict")
    if verdict:
        verdict_str = verdict.get("verdict", "UNKNOWN")
        confidence = verdict.get("confidence", 0)
        rationale = verdict.get("rationale", "")[:150]
        await emit(
            case_id,
            "debate_verdict",
            f"Senior Auditor verdict: {verdict_str} (confidence {confidence:.0%}) - {rationale}",
            agent="debate",
            verdict=verdict_str,
            confidence=confidence,
            recommended_action=verdict.get("recommended_action"),
            tenant_id=tenant_id,
        )

    try:
        await store.put_case(
            case, expected_version=expected_version, tenant_id=tenant_id,
        )
    except OptimisticLockError:
        return {"ok": False, "error": "case was modified concurrently, please retry"}

    return {
        "ok": True,
        "case_id": case_id,
        "debate": debate_result.get("result"),
        "verdict": verdict,
        "latency_ms": debate_result.get("latency_ms"),
    }


async def review_queue(
    cursor: str | None = None, limit: int = 40, tenant_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Cases waiting on a person, newest first, with cursor pagination.

    Queries `state IN AWAITING_HUMAN` directly on an indexed field instead of
    fetching the newest N cases and filtering in Python. The old approach
    silently dropped a case from the queue the moment 120 newer cases (of
    any state) existed - a case genuinely held for review would disappear
    from the one place a human looks for it. See firestore.indexes.json for
    the composite index this relies on.
    """
    page: tuple[list[dict[str, Any]], str | None] = await get_store().query_cases(
        states=AWAITING_HUMAN, cursor=cursor, limit=limit, tenant_id=tenant_id,
    )
    return page


# Fields a board card actually renders. A full case document runs ~13KB,
# mostly `steps[].result` payloads a list view never shows; a slim
# projection is roughly 100x smaller and is what GET /api/v1/cases returns.
# Anything more (the trace, decision packs) is fetched per-case on demand
# via GET /api/v1/orchestrator/case/<id>.
SLIM_CASE_FIELDS = (
    "case_id",
    "shipment_id",
    "state",
    "source",
    "risk_score",
    "compliance_status",
    "claimed",
    "created_at",
)


def slim_case(case: dict[str, Any]) -> dict[str, Any]:
    return {k: case.get(k) for k in SLIM_CASE_FIELDS}


async def list_cases_page(
    states: tuple[str, ...] | None = None,
    cursor: str | None = None,
    limit: int = 50,
    tenant_id: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Slim, cursor-paginated case listing for GET /api/v1/cases."""
    cases, next_cursor = await get_store().query_cases(
        states=states, cursor=cursor, limit=limit, tenant_id=tenant_id,
    )
    return [slim_case(c) for c in cases], next_cursor


def synthesise_packs(case: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Collapse everything known about a case into four Decision Packs.

    A reviewer does not want six raw agent payloads and a validation blob; they
    want to know what each discipline concluded and where the disagreements are.
    The synthesiser is deliberately deterministic - it arranges evidence the
    agents and the verifier already produced rather than asking a model to
    summarise its own colleagues, which would add a fresh opportunity to
    confabulate at exactly the point a human is about to rely on it.
    """
    steps = {s["agent"]: s.get("result", {}) for s in case.get("steps", [])}
    fraud = steps.get("fraud_detection", {})
    compliance = steps.get("compliance", {})
    investigation = steps.get("investigation", {})
    intake = steps.get("document_intake", {})

    validation = case.get("validation") or {}
    reconciliation = case.get("reconciliation") or {}
    findings = validation.get("findings", [])

    def codes(*prefixes: str) -> list[dict[str, Any]]:
        return [
            f for f in findings
            if any(f.get("code", "").startswith(p) for p in prefixes)
        ]

    return [
        {
            "pack": "Integrity and pricing",
            "headline": (
                f"Effective risk {case.get('risk_score')}/100"
                + (f", agent said {reconciliation.get('model_risk')}"
                   if reconciliation.get("score_disputed") else "")
            ),
            "agent_view": {
                "risk_score": fraud.get("risk_score"),
                "risk_level": fraud.get("risk_level"),
                "flags": fraud.get("flags", []),
            },
            "deterministic_view": codes("FREIGHT", "VALUE_DENSITY"),
            "disagreement": reconciliation.get("score_disputed", False),
        },
        {
            "pack": "Sanctions and trade compliance",
            "headline": (
                f"{case.get('compliance_status') or 'not screened'}"
                + (f", score {case.get('compliance_score')}/100"
                   if case.get("compliance_score") is not None else "")
            ),
            "agent_view": {
                "compliance_status": compliance.get("compliance_status"),
                "sanctions_hits": compliance.get("sanctions_hits", []),
                "regulatory_issues": compliance.get("regulatory_issues", []),
            },
            "deterministic_view": codes("DUAL_USE", "HS_CODE", "HIGH_RISK", "MULTIPLE_DIVERSION", "ROUTE_"),
            "disagreement": False,
        },
        {
            "pack": "Exposure and counterparty",
            "headline": (
                investigation.get("exposure_estimate")
                or f"declared value {case.get('shipment', {}).get('declared_value')} USD"
            ),
            "agent_view": {
                "fraud_pattern": investigation.get("fraud_pattern"),
                "exposure_estimate": investigation.get("exposure_estimate"),
                "summary": investigation.get("summary"),
            },
            "deterministic_view": codes("SHIPPER", "RECENTLY_REGISTERED", "EXPOSURE_CLAIM"),
            "disagreement": any(
                f.get("code") == "EXPOSURE_CLAIM_UNSUPPORTED" for f in findings
            ),
        },
        {
            "pack": "Provenance and input safety",
            "headline": (
                "document blocked before model processing"
                if not (case.get("input_security") or {}).get("model_invoked", True)
                else f"source: {case.get('source', 'event')}"
            ),
            "agent_view": {
                "extraction_confidence": intake.get("extraction_confidence"),
                "extraction_notes": intake.get("extraction_notes", []),
            },
            "deterministic_view": [
                {
                    "code": "INPUT_SECURITY",
                    "severity": (
                        "CRITICAL"
                        if ((case.get("input_security") or {}).get("model_armor") or {}).get("blocked")
                        else "MEDIUM"
                    ),
                    "detail": (
                        (((case.get("input_security") or {}).get("model_armor") or {}).get("detail"))
                        or "no input security findings"
                    ),
                }
            ] + codes("MISSING", "SHIPPER_TAX_ID", "CARGO_DESCRIPTION"),
            "disagreement": False,
        },
    ]


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------

async def _superseded(case: dict[str, Any]) -> None:
    """
    Abandon a transition whose case was committed by somebody else mid-flight.

    Deliberately writes NOTHING. The store already holds the authoritative case,
    and the in-memory copy here is a mutated stale snapshot -- persisting it is
    precisely the bug this exists to prevent. A reviewer released
    CASE-FULL-20-THINHISTORY at 07:27:18 and the case read ESCALATED again twenty
    seconds later because a losing transition wrote its stale copy back.

    Also does not touch `attempts`. A lost race is not a fault of the case, and
    counting it as one means three reviewer decisions in quick succession could
    push a perfectly healthy case to DEAD_LETTER.
    """
    _stats["superseded"] = _stats.get("superseded", 0) + 1
    logging.info(
        "case %s superseded by a concurrent write; discarding this transition",
        case.get("case_id"),
    )
    await emit(
        case["case_id"],
        "superseded",
        "A concurrent write (usually a reviewer decision) committed while this "
        "transition was running. The computed result was discarded rather than "
        "overwriting the newer state.",
        tenant_id=_case_tenant(case),
    )


async def _fail(case: dict[str, Any], exc: Exception) -> None:
    """
    Back off and retry, or dead-letter after MAX_ATTEMPTS.

    Both writes below carry `expected_version`. They used to be the only two case
    writes in this module without it, which made this recovery path the one place
    that could silently overwrite committed state: a transition that lost a race
    landed here, and here it wrote its stale copy back unconditionally. Step 3
    stops the known route in (a lock conflict is now handled as superseded before
    it can reach this function), but any *other* failure racing a reviewer's
    decision had exactly the same power, so the guard belongs here too.

    A recovery path that can destroy a committed decision is not a recovery path.
    """
    store = get_store()
    tenant = _case_tenant(case)
    expected_version = case.get("_version")
    case["attempts"] = case.get("attempts", 0) + 1
    case["last_error"] = f"{type(exc).__name__}: {exc}"
    case["claimed"] = False
    case["updated_at"] = utcnow()

    if case["attempts"] >= MAX_ATTEMPTS:
        case["state"] = "DEAD_LETTER"
        try:
            await store.put_case(
                case, expected_version=expected_version, tenant_id=tenant,
            )
        except OptimisticLockError:
            # Somebody committed while we were failing. Their state wins; marking
            # DEAD_LETTER over a reviewer's decision would be the worst possible
            # outcome of a failed retry.
            await _superseded(case)
            return
        await emit(
            case["case_id"],
            "dead_letter",
            f"Gave up after {case['attempts']} attempts: {case['last_error']}",
            tenant_id=tenant,
        )
        return

    # 5s, 10s, 20s. The earlier 2s base burned all three attempts inside a few
    # seconds, which dead-lettered cases faster than any transient fault could
    # clear.
    backoff = 5 * (2 ** (case["attempts"] - 1))
    from datetime import datetime, timedelta, timezone

    case["not_before"] = (
        datetime.now(timezone.utc) + timedelta(seconds=backoff)
    ).isoformat()
    try:
        await store.put_case(
            case, expected_version=expected_version, tenant_id=tenant,
        )
    except OptimisticLockError:
        await _superseded(case)
        return
    await emit(
        case["case_id"],
        "retry",
        f"Attempt {case['attempts']} failed, retrying in {backoff}s: {case['last_error']}",
        tenant_id=tenant,
    )


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------

_worker_task = None
_stats: dict[str, Any] = {
    "started_at": None,
    "ticks": 0,
    "advanced": 0,
    "failed": 0,
    "last_tick_error": None,
    "tick_errors": 0,
}


async def tick(tenant_id: str | None = None) -> int:
    """
    Advance up to MAX_CONCURRENT cases by one step each.

    Also usable as an HTTP-driven fallback if the always-on background loop is
    ever unavailable, which is why it is a plain awaitable returning a count.

    `tenant_id=None` reaches every tenant, which is what claim_next_pending's
    own contract means by None and is correct for the background worker: scoping
    the worker to one tenant would leave every other tenant's cases unprocessed
    forever. A request handler passes its own tenant so one customer cannot
    spend another's work budget.
    """
    _stats["ticks"] += 1
    claimed = []
    store = get_store()

    for _ in range(MAX_CONCURRENT):
        case = await store.claim_next_pending(ACTIONABLE, tenant_id=tenant_id)
        if not case:
            break
        claimed.append(case)

    if not claimed:
        return 0

    async def _run(case: dict[str, Any]) -> None:
        try:
            await advance(case)
            _stats["advanced"] += 1
        except OptimisticLockError:
            # Not a failure. Another writer -- most often a reviewer's decision --
            # committed while this transition was running, so everything computed
            # here is based on a snapshot that no longer exists. Retrying would
            # recompute from the same stale copy, and _fail() would persist it.
            await _superseded(case)
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the worker
            _stats["failed"] += 1
            logging.exception("case %s failed to advance", case.get("case_id"))
            await _fail(case, exc)

    await asyncio.gather(*(_run(c) for c in claimed))
    return len(claimed)


async def advance_until_terminal(case: dict[str, Any]) -> dict[str, Any]:
    """
    Drive one case from wherever it is to a terminal state, in this request.

    This is what makes scale-to-zero viable: a single Pub/Sub delivery wakes the
    container once and the whole workflow completes before the response is sent,
    instead of needing one wake-up per agent hop.

    Bounded twice over - by step count and by wall clock - so a pathological
    case cannot hold a request open forever. A case that runs out of budget is
    simply left where it is, and the next trigger picks it up.

    The stored case is re-read before every step after the first. This loop used
    to judge `state in ACTIONABLE` against its own in-memory copy, which made a
    decision committed by a reviewer completely invisible to it: the chain carried
    on from the stale snapshot and executed the next transition's TOOLS --
    hold_shipment, draft_sar -- against a shipment a human had already released.
    The optimistic lock stops the resulting *write*, but a lock only guards
    writes, and by the time it fires the side effects have happened. That is why
    the audit for CASE-FULL-20-THINHISTORY shows a hold twenty seconds after a
    release.

    One extra store read per step, against several model calls per step, is not a
    cost worth optimising away.
    """
    started = time.monotonic()
    steps = 0

    while steps < MAX_CHAIN_STEPS and time.monotonic() - started < CHAIN_BUDGET_SECONDS:
        if steps:
            fresh = await get_store().get_case(
                case["case_id"], tenant_id=_case_tenant(case)
            )
            # A missing case means it was deleted mid-chain (a reset, most
            # likely). Keep the in-memory copy for the return value but stop
            # working it -- there is nothing left to advance.
            if fresh is None:
                break
            case = fresh

        if case.get("state") not in ACTIONABLE:
            break

        try:
            case = await advance(case)
            _stats["advanced"] += 1
        except OptimisticLockError:
            # Someone else committed this case while the transition ran. Stop the
            # chain: the stored state is authoritative, and if it is still
            # actionable the next trigger will pick it up from the real state
            # rather than from this stale copy.
            await _superseded(case)
            break
        except Exception as exc:  # noqa: BLE001
            _stats["failed"] += 1
            logging.exception("case %s failed to advance", case.get("case_id"))
            await _fail(case, exc)
            break
        steps += 1

    return case


async def drain(
    max_cases: int = 1, tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Claim up to `max_cases` pending cases and run each to completion.

    Used by the request-driven mode. Kept deliberately small by default: the
    dashboard calls this on every poll, and a large batch would make a single
    poll take a minute.
    """
    store = get_store()
    handled: list[str] = []

    for _ in range(max_cases):
        case = await store.claim_next_pending(ACTIONABLE, tenant_id=tenant_id)
        if not case:
            break
        done = await advance_until_terminal(case)
        handled.append(done.get("case_id", "?"))

    return {"drained": len(handled), "case_ids": handled}


async def worker_loop() -> None:
    """
    The background loop. Runs for the lifetime of the container.

    Errors are recorded on _stats rather than only suppressed: a loop that
    silently swallows failures looks identical to a loop with nothing to do,
    which makes a stalled pipeline impossible to diagnose from outside.
    """
    _stats["started_at"] = utcnow()
    while True:
        try:
            moved = await tick()
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            moved = 0
            _stats["tick_errors"] += 1
            _stats["last_tick_error"] = f"{type(exc).__name__}: {exc}"
            logging.exception("orchestrator tick failed")
        await asyncio.sleep(0.1 if moved else POLL_SECONDS)


def start_worker(loop: asyncio.AbstractEventLoop) -> None:
    """
    Schedule the background loop, but only in poll mode.

    run_coroutine_threadsafe is used rather than loop.create_task because this
    is called from the Flask/gunicorn thread, and create_task is not
    thread-safe. It also queues correctly if the target loop has not finished
    starting yet.
    """
    global _worker_task
    if WORKER_MODE != "poll":
        return
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.run_coroutine_threadsafe(worker_loop(), loop)


def worker_status() -> dict[str, Any]:
    running = bool(_worker_task and not _worker_task.done())
    return {
        "mode": WORKER_MODE,
        "running": running,
        "drives_pipeline": (
            "background loop" if WORKER_MODE == "poll" else "request handlers"
        ),
        **_stats,
        "thresholds": {
            "fraud_clear_below": FRAUD_CLEAR_BELOW,
            "investigate_at": INVESTIGATE_AT,
            "max_attempts": MAX_ATTEMPTS,
            "max_concurrent": MAX_CONCURRENT,
            "max_chain_steps": MAX_CHAIN_STEPS,
        },
    }


# --------------------------------------------------------------------------
# Dashboard projection
# --------------------------------------------------------------------------

async def snapshot(limit: int = 60, tenant_id: str | None = None) -> dict[str, Any]:
    """
    Dashboard header + a bounded board window.

    KPIs (`counts`, `in_flight`, `awaiting_human`, token/cost/latency
    totals) come from `global_metrics()` - a TTL-cached whole-collection
    aggregation - not from the fetched window below, so the numbers
    describe the real system at any case volume. `cases[]` itself stays
    windowed because a live board only ever needs to render one page; walk
    further pages via `GET /api/v1/cases`.
    """
    store = get_store()
    all_cases, events, audit, metrics = await asyncio.gather(
        store.list_cases(limit + 40, tenant_id=tenant_id),
        store.list_events(80, tenant_id=tenant_id),
        store.list_audit(80, tenant_id=tenant_id),
        global_metrics(tenant_id=tenant_id),
    )

    # Storage dedupe markers are bookkeeping, not cases. They must not appear on
    # the board or be counted in the metrics.
    cases = [c for c in all_cases if not c.get("is_marker")][:limit]

    # Per-agent breakdown is a Cost Monitor detail (a secondary, DEMO_MODE-
    # hidden feature), not a headline KPI, so it stays windowed rather than
    # needing a denormalized field per agent per case.
    #
    # `cost_usd` is summed here rather than derived in the console, for the reason
    # config.pricing_for() spells out: the rate depends on WHICH model ran the step,
    # and only the step knows that. Investigation and the debate run Super (by default)
    # while fraud and compliance run Nano, so a client multiplying total tokens by the
    # currently selected model's rate would misreport every mixed window. The per-step
    # figure already exists -- _record_step writes it -- so this only adds it up.
    #
    # The card this feeds was titled "Cost by agent" and displayed calls and tokens
    # only, with no money on it at all, while the demo narration said spend was metered
    # "per agent, in tokens and in dollars". The claim came first and the field second.
    tokens_by_agent: dict[str, dict[str, float]] = {}
    for case in cases:
        for step in case.get("steps", []) or []:
            agent = step.get("agent", "unknown")
            bucket = tokens_by_agent.setdefault(
                agent, {"calls": 0, "input": 0, "output": 0, "cost_usd": 0.0}
            )
            bucket["calls"] += 1
            bucket["input"] += step.get("input_tokens", 0) or 0
            bucket["output"] += step.get("output_tokens", 0) or 0
            bucket["cost_usd"] += step.get("cost_usd", 0.0) or 0.0

    # Rounded once at the end rather than per step: summing eight-decimal figures and
    # rounding the total keeps the per-agent column adding up to the board total, which
    # is what an operator checks first when two numbers on one screen disagree.
    for bucket in tokens_by_agent.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)

    readiness = await governance.agent_readiness(tenant_id=tenant_id)

    return {
        "cases": cases,
        "events": events,
        "audit": audit,
        "counts": metrics["counts"],
        "in_flight": metrics["in_flight"],
        "awaiting_human": metrics["awaiting_human"],
        "agent": readiness,
        "agent_calls": metrics["agent_calls"],
        "avg_latency_ms": metrics["avg_latency_ms"],
        "total_input_tokens": metrics["total_input_tokens"],
        "total_output_tokens": metrics["total_output_tokens"],
        "tokens_by_agent": tokens_by_agent,
        "estimated_cost_usd": metrics["estimated_cost_usd"],
        "worker": worker_status(),
        "at": utcnow(),
    }
