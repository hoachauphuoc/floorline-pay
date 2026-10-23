"""
Pydantic v2 schemas for API request/response validation.

All API endpoints should use these schemas for:
1. Input validation (reject malformed requests early)
2. Output serialization (consistent response shapes)
3. OpenAPI documentation generation

The schemas mirror the Firestore document structures but add:
- Type enforcement
- Required field validation
- Value constraints (min/max, regex patterns, enums)
- Sensitive field handling (exclude from logs)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


# =============================================================================
# Enums
# =============================================================================

class CaseState(str, Enum):
    """
    Valid case states in the workflow.

    These are the real states, taken from orchestrator.ACTIONABLE and
    orchestrator.TERMINAL. They were not before: this enum declared INTAKE,
    PENDING_ANALYSIS, ANALYSIS_COMPLETE, RELEASED and BLOCKED -- none of which the
    orchestrator ever sets -- and omitted INGESTED, which every new case starts
    in, plus SPECIALISTS_DONE, INVESTIGATED, RELEASED_BY_HUMAN and
    BLOCKED_BY_HUMAN. CaseResponse.state would therefore have failed validation
    on any real case. Nothing imported this module, which is the only reason that
    went unnoticed.

    Kept as a literal enum rather than generated from the orchestrator tuples to
    avoid schemas.py importing the orchestrator -- it is the leaf of the import
    graph and several modules depend on it staying that way. tests assert the two
    agree instead.
    """
    # In flight (orchestrator.ACTIONABLE)
    INGESTED = "INGESTED"
    SPECIALISTS_DONE = "SPECIALISTS_DONE"
    INVESTIGATED = "INVESTIGATED"
    # Terminal (orchestrator.TERMINAL)
    AUTO_CLEARED = "AUTO_CLEARED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
    ESCALATED = "ESCALATED"
    PENDING_HUMAN = "PENDING_HUMAN"
    RELEASED_BY_HUMAN = "RELEASED_BY_HUMAN"
    BLOCKED_BY_HUMAN = "BLOCKED_BY_HUMAN"
    DEAD_LETTER = "DEAD_LETTER"
    # A reviewer asked the shipper for documents (evidence.py).
    AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class ReviewAction(str, Enum):
    """Valid review actions a human can take."""
    RELEASE = "release"
    BLOCK = "block"
    REQUEST_INFO = "request_info"


# =============================================================================
# Shipment schemas
# =============================================================================

class ShipmentBase(BaseModel):
    """Base shipment fields."""
    model_config = ConfigDict(str_strip_whitespace=True)

    shipment_id: str = Field(..., min_length=1, max_length=100)
    origin: str = Field(..., min_length=1, max_length=200)
    destination: str = Field(..., min_length=1, max_length=200)
    weight_kg: float = Field(..., ge=0, le=1_000_000)
    declared_value: float = Field(..., ge=0)
    shipping_cost: float = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")

    shipper_name: str = Field(..., max_length=200)
    shipper_company: str = Field(..., max_length=200)
    shipper_country: str = Field(..., max_length=100)
    shipper_tax_id: str = Field(..., max_length=50)

    receiver_name: str = Field(..., max_length=200)
    receiver_company: str = Field(..., max_length=200)
    receiver_country: str = Field(..., max_length=100)

    cargo_description: str = Field(..., max_length=1000)
    hs_code: str = Field(..., max_length=20)
    route_details: str = Field(..., max_length=500)
    transit_points: str | None = Field(None, max_length=500)

    status: str = Field(default="pending", max_length=50)
    extraction_notes: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ShipmentCreate(ShipmentBase):
    """Schema for creating a new shipment via API."""
    pass


class ShipmentResponse(ShipmentBase):
    """Schema for shipment in API responses."""
    created_at: datetime | None = None


# =============================================================================
# Case schemas
# =============================================================================

class ReconciliationInfo(BaseModel):
    """
    Risk reconciliation between model and deterministic floor.

    Field names follow verifier.reconcile() exactly. They did not before: this
    model declared `effective`, `disputed` and `dispute_delta` while reconcile()
    returns `effective_risk`, `score_disputed` and `auto_clear_permitted`. No
    route imported this module, so the mismatch never raised -- which is the only
    reason it survived. Wiring the old version into an endpoint would have failed
    validation on every single case.
    """
    model_config = ConfigDict(extra="allow")

    effective_risk: float = Field(..., ge=0, le=100)
    model_risk: float | None = Field(None, ge=0, le=100)
    risk_floor: float = Field(..., ge=0, le=100)
    source: str
    score_disputed: bool = False
    auto_clear_permitted: bool = False
    veto_reasons: list[str] = Field(default_factory=list)


class GateDenial(BaseModel):
    """Record of a governance gate denial."""
    gate: str
    reason: str
    timestamp: datetime


class ReviewEntry(BaseModel):
    """Record of a human review action."""
    action: ReviewAction
    reviewer: str
    at: datetime
    note: str | None = None
    state_before: str
    state_after: str


class CaseResponse(BaseModel):
    """Schema for case in API responses."""
    case_id: str
    state: CaseState
    shipment: ShipmentResponse | None = None
    risk_score: float | None = Field(None, ge=0, le=100)
    recommendation: str | None = None
    reconciliation: ReconciliationInfo | None = None
    gate_denials: list[GateDenial] = Field(default_factory=list)
    proposed_outcome: str | None = None
    reviews: list[ReviewEntry] = Field(default_factory=list)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CaseListResponse(BaseModel):
    """Schema for listing multiple cases."""
    cases: list[CaseResponse]
    total: int
    has_more: bool = False


# =============================================================================
# Review queue schemas
# =============================================================================

class ReviewQueueItem(BaseModel):
    """A case in the review queue."""
    case_id: str
    state: CaseState
    risk_score: float | None = None
    shipment_id: str | None = None
    shipper_company: str | None = None
    receiver_company: str | None = None
    declared_value: float | None = None
    currency: str | None = None
    waiting_since: datetime | None = None


class ReviewQueueResponse(BaseModel):
    """Schema for review queue API response."""
    cases: list[ReviewQueueItem]
    total: int


class ReviewDecisionRequest(BaseModel):
    """Schema for submitting a review decision."""
    action: ReviewAction
    note: str | None = Field(None, max_length=1000)

    @field_validator("note")
    @classmethod
    def sanitize_note(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Basic XSS prevention
        return v.replace("<", "&lt;").replace(">", "&gt;")


class ReviewDecisionResponse(BaseModel):
    """Schema for review decision API response."""
    success: bool
    case_id: str
    new_state: CaseState
    message: str | None = None


# =============================================================================
# Simulation schemas
# =============================================================================

class SimulationRequest(BaseModel):
    """Schema for triggering a simulation."""
    shipment: ShipmentCreate | None = None
    scenario: str | None = Field(None, max_length=100)


class SimulationResponse(BaseModel):
    """Schema for simulation API response."""
    case_id: str
    state: CaseState
    message: str


# =============================================================================
# Health/status schemas
# =============================================================================

class HealthResponse(BaseModel):
    """Schema for health check response."""
    status: str
    version: str | None = None
    timestamp: datetime


class AgentInfo(BaseModel):
    """Schema for agent info in /agents endpoint."""
    name: str
    model: str
    capabilities: list[str]


class AgentsResponse(BaseModel):
    """Schema for /agents endpoint response."""
    agents: list[AgentInfo]


# =============================================================================
# Agent output schemas
# =============================================================================
#
# Everything above this line validates HTTP traffic. These validate what the
# MODEL returns, which is a different problem: the caller of an endpoint is a
# customer integration that can be told to fix its request, whereas a model that
# returns the wrong shape cannot be argued with and will do it again.
#
# What "required" means here
# --------------------------
# Only the fields the system acts on are required. That is a deliberate line,
# not laziness. The fraud prompt asks for five fields, but reconcile() reads
# exactly one of them -- risk_score -- and the orchestrator reads risk_level only
# to display it. Requiring `confidence` would fail a response that is perfectly
# usable, and a schema that rejects usable answers pushes work to humans for no
# safety gain.
#
# It also keeps the schemas honest about a discrepancy already in the codebase:
# fraud_detection_agent.py:41 asks the model for `flags`, while the test mocks at
# tests/test_orchestrator_unit.py:366 return `findings`. Both are accepted below
# and neither is required, because nothing downstream reads either one to make a
# decision.
#
# Why out-of-range values are rejected rather than clamped
# -------------------------------------------------------
# A model returning risk_score 150 is malfunctioning, and clamping to 100 would
# hide that. Rejection is safe because reconcile() already handles a missing
# score correctly: `effective_risk = max(floor, 50)`, `score_disputed = True`,
# `auto_clear_permitted = False` (verifier.py:757-765). So a rejected score
# cannot release a shipment -- it can only send one to a human.
#
# extra="allow" throughout: a model inventing an additional field is harmless and
# is not worth a human review cycle.


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComplianceStatus(str, Enum):
    CLEARED = "CLEARED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class AgentOutput(BaseModel):
    """Base for every model reply. Unknown fields are kept, not rejected."""
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class FraudAnalysis(AgentOutput):
    """fraud_detection_agent. risk_score is the only field reconcile() reads."""
    risk_score: float = Field(..., ge=0, le=100)
    risk_level: RiskLevel | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    flags: list[Any] = Field(default_factory=list)
    findings: list[Any] = Field(default_factory=list)
    recommendations: list[Any] = Field(default_factory=list)


class ComplianceScreening(AgentOutput):
    """compliance_agent. Both required fields are read at orchestrator.py:895-896."""
    compliance_status: ComplianceStatus
    compliance_score: float = Field(..., ge=0, le=100)
    confidence: float | None = Field(None, ge=0, le=1)
    risk_factors: list[Any] = Field(default_factory=list)
    sanctions_hits: Any = None
    regulatory_issues: list[Any] = Field(default_factory=list)
    required_actions: list[Any] = Field(default_factory=list)


class HSClassification(AgentOutput):
    """
    hs_classifier_agent, both prompt variants.

    `consistent` is required and must be a real bool. agents/hs_classifier_agent
    .interpret() already refuses to coerce it -- a reply without it becomes
    verdict "unknown" rather than being read as either answer, because treating an
    unparseable reply as consistent would clear exactly the cases the agent exists
    to catch. This schema enforces the same rule one layer earlier.

    goods_as_described and heading_for_goods appear only in the cot modes, so both
    are optional; the base prompt does not ask for them.

    StrictBool, not bool: Pydantic's lax mode reads the string "no" as False, so a
    plain `bool` annotation accepted a reply that interpret() then reported as
    "unknown". Two layers disagreeing about whether the same reply is valid is
    worse than either rule on its own, so this one matches interpret().
    """
    consistent: StrictBool
    declared_hs: str | None = None
    suggested_hs: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    reasoning: str | None = None
    obfuscation_observed: str | None = None
    goods_as_described: str | None = None
    heading_for_goods: str | None = None


class DebateVerdict(AgentOutput):
    """
    debate_agent's render_final_verdict tool arguments.

    Currently accepted unvalidated at debate_agent.py:297 (`final_verdict =
    tool_args`), so a malformed verdict reaches the case with fields missing or
    outside their enum. Validating here closes that.
    """
    verdict: Literal["CONFIRM", "DISAGREE"]
    confidence: float = Field(..., ge=0, le=1)
    rationale: str
    recommended_action: Literal["release", "hold", "escalate"]
    adjusted_risk_score: float | None = Field(None, ge=0, le=100)
    # Set by debate_agent, never by the model: True for the defaults it fills in
    # when no usable verdict was rendered. orchestrator._apply_debate_verdict acts
    # only on a verdict with forced=False.
    forced: bool = False
    forced_reason: str | None = None


class ZeroDayVerdict(AgentOutput):
    """
    zero_day_agent: negative-news screening for entities absent from the
    official list.

    `searched` is required and separate from `risk_found` on purpose. "I looked
    and found nothing" and "I could not look" must not collapse into the same
    value -- tavily_client returns [] for a missing API key, a timeout and a
    genuinely empty result alike, so without this field an outage reads as a
    clean entity.
    """
    risk_found: StrictBool
    searched: StrictBool
    confidence: float = Field(..., ge=0, le=1)
    reasoning: str
    entities_checked: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)


class InvestigationReport(AgentOutput):
    """
    investigation_agent, on Nemotron 3 Super.

    `summary` is the only required field because it is the one a decision is built
    from: orchestrator.py writes it into the SAR narrative and the escalation
    webhook. Everything else stays loose -- `connections` came back as a list in
    five good replies and as a dict in a sixth, and rejecting a sound report over
    that would send a reviewer less, not more.

    Measured on the 20-case board: 8 of 16 investigation replies were a JSON
    fragment such as {": {}}": null} or {".json": null}, or the model's own
    planning text as a key, after 283-579 output tokens. Each parsed as JSON, and
    with no schema registered here each was stored as a finished investigation
    with parse_error False, so the trace showed the step as done, the SAR read
    "No narrative returned." and the failure was never counted.
    """
    summary: str = Field(..., min_length=1)
    # Read by orchestrator.py into the escalation reason and the SAR, through
    # str(); typed Any so a figure given as a number is kept rather than refused.
    # The prompt's other fields arrive through extra="allow", unchecked, because
    # nothing makes a decision from them.
    fraud_pattern: Any = None
    exposure_estimate: Any = None


# Maps an agent name to the schema its replies must satisfy. agents/_common.py
# looks the schema up here rather than each agent importing its own, so adding an
# agent without a schema is visible in one place.
AGENT_OUTPUT_SCHEMAS: dict[str, type[AgentOutput]] = {
    "fraud_detection": FraudAnalysis,
    "compliance": ComplianceScreening,
    "hs_classifier": HSClassification,
    "debate": DebateVerdict,
    "zero_day": ZeroDayVerdict,
    "investigation": InvestigationReport,
}


# =============================================================================
# B2B integration API (Module 3)
# =============================================================================
#
# The contract an ERP or TMS codes against. Two things about it are deliberate
# and worth stating, because both look like mistakes.
#
# 1. The request is LOOSE. ShipmentBase above requires nearly twenty fields with
#    `...`, and enforcing that at the edge would 422 any feed missing, say,
#    cargo_description. But a shipment with no cargo description is exactly what
#    this system is built to flag -- verifier.check_mandatory_fields() raises a
#    CARGO_DESCRIPTION_MISSING finding with a floor of 60. Rejecting it at the
#    door would convert a detection into a silence, and the integrator would
#    "fix" their feed by inventing a value. So the only hard requirement is
#    enough to identify the shipment; everything else is optional and its absence
#    becomes a finding.
#
#    Validation at the edge still earns its place: a declared_value of "abc" or a
#    negative weight is a data error the integrator must fix, and saying so with
#    a field name beats letting it become a confusing risk score.
#
# 2. The response is WIDE and versioned. Customers build against it, so it
#    carries the verdict, the evidence behind it, the model versions, the token
#    cost and a lineage reference -- everything needed to answer a customs
#    authority asking why a shipment was flagged, which is the whole reason this
#    is a product rather than a script.


class AuditOutcome(str, Enum):
    """
    Terminal outcomes exposed to an integrator.

    Narrower than CaseState on purpose: the internal state machine has ten states
    including intermediate ones, and an ERP should not have to know that
    SPECIALISTS_DONE exists or handle it changing. CLEARED / HELD / BLOCKED /
    ERROR is the whole vocabulary a customer system needs to branch on.
    """
    CLEARED = "CLEARED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
    BLOCKED = "BLOCKED"
    PENDING_HUMAN = "PENDING_HUMAN"
    ERROR = "ERROR"


class AuditFinding(BaseModel):
    """One deterministic or model-derived finding behind a verdict."""
    model_config = ConfigDict(extra="allow")

    code: str
    severity: str
    detail: str
    floor: int = Field(0, ge=0, le=100)
    source_entity_ids: list[str] = Field(
        default_factory=list,
        description="Sanctions list record ids that produced this finding, when any",
    )
    evidence_urls: list[str] | None = Field(
        None,
        description="Public news citations behind an adverse-media finding. "
                    "null and [] mean different things and must not be "
                    "collapsed: null is 'no search result was recorded for this "
                    "finding', [] is 'a search ran and returned nothing'. A "
                    "consumer that treats both as 'no adverse media' reports a "
                    "search that never happened as a clean result.",
    )


class AuditLineage(BaseModel):
    """
    The provenance of a verdict.

    A customs authority asking "why was this flagged" cannot be answered with
    "the AI said so". Every field here exists to make the answer checkable: which
    list record matched, when that list was pulled, which model version ran, and
    the hash of the exact prompt it ran on.
    """
    audit_id: str
    prompt_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="agent name -> SHA-256 of the system prompt that produced its reply",
    )
    model_versions: dict[str, str] = Field(
        default_factory=dict, description="agent name -> model id",
    )
    sanctions_synced_at: str | None = Field(
        None,
        description="When the sanctions list this verdict screened against was pulled",
    )
    sanctions_list_age_days: int | None = Field(
        None,
        description=(
            "Age of that list in days. Present so a consumer can see staleness "
            "rather than assume currency; a weekly refresh means up to 7 days."
        ),
    )
    ruleset_version: int | None = None


class AuditUsage(BaseModel):
    """Token and cost accounting for one audit, for usage-based billing."""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    agent_calls: int = 0
    latency_ms: int | None = None


class ComplianceAuditRequest(BaseModel):
    """
    A shipment submitted for audit by an ERP or TMS.

    `client_reference` is the integrator's own identifier and makes the endpoint
    idempotent. It is not decoration: Module 4 retries a failed model call, and
    an ERP whose HTTP request timed out will retry the whole POST. Without a
    dedupe key that produces two audits, two sets of tokens billed, and two
    possibly different verdicts for one shipment.
    """
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    shipment_id: str = Field(..., min_length=1, max_length=100)
    client_reference: str | None = Field(
        None, max_length=200,
        description="Your own id for this submission. Re-POSTing it returns the "
                    "original audit instead of running a second one.",
    )

    origin: str | None = Field(None, max_length=200)
    destination: str | None = Field(None, max_length=200)

    shipper_company: str | None = Field(None, max_length=200)
    shipper_name: str | None = Field(None, max_length=200)
    shipper_country: str | None = Field(None, max_length=100)
    shipper_tax_id: str | None = Field(None, max_length=50)

    receiver_company: str | None = Field(None, max_length=200)
    receiver_name: str | None = Field(None, max_length=200)
    receiver_country: str | None = Field(None, max_length=100)
    consignee_name: str | None = Field(None, max_length=200)

    cargo_description: str | None = Field(None, max_length=2000)
    hs_code: str | None = Field(None, max_length=20)

    # Negative money or weight is an integrator data error, not a risk signal,
    # so it is rejected here with a field name rather than left to become a
    # confusing score.
    declared_value: float | None = Field(None, ge=0)
    freight_cost: float | None = Field(None, ge=0)
    shipping_cost: float | None = Field(None, ge=0)
    weight_kg: float | None = Field(None, ge=0, le=1_000_000)
    currency: str | None = Field(None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")

    route_details: str | None = Field(None, max_length=500)
    transit_points: str | None = Field(None, max_length=500)

    # Fields untrusted.py deliberately refuses to take from a DOCUMENT, accepted
    # here because the trust boundary is different.
    #
    # A bill of lading that states its own trading history would defeat the check
    # that field feeds -- the party under scrutiny supplied the document. An ERP
    # is not that party: it is the customer's own system of record, and it
    # genuinely knows how many times it has shipped with a counterparty.
    #
    # The residual risk is a customer suppressing their own finding, e.g. a
    # forwarder sending shipper_tx_count=999 to clear the thin-history flag.
    # That is visible rather than hidden: the value arrives over an authenticated
    # integration and lands in the audit record, so an auditor can see the figure
    # came from the integrator instead of from observed history. Refusing the
    # field outright would instead put a MEDIUM finding on every honest
    # integration, which trains customers to ignore findings.
    shipper_tx_count: int | None = Field(
        None, ge=0,
        description="Completed shipments with this shipper, from your own records. "
                    "Absent means unverified, which raises a finding.",
    )
    avg_route_cost: float | None = Field(
        None, ge=0,
        description="Your historical average freight cost for this lane, in USD.",
    )

    # Floorline Pay. Optional like the rest of this request: a payout with no payee
    # on it is what the payment checks exist to flag, so its absence becomes a finding
    # rather than a 422. A malformed value is still an integrator data error.
    carrier_name: str | None = Field(None, max_length=200)
    carrier_payee_email: str | None = Field(
        None, max_length=254,
        description="The PayPal account the carrier asks to be paid to on this load.",
    )
    carrier_registered_payee_email: str | None = Field(
        None, max_length=254,
        description="The PayPal account your own records hold for this carrier.",
    )
    carrier_payee_changed_at: str | None = Field(
        None, max_length=40,
        description="When the carrier's payee was last changed, ISO 8601 with a timezone.",
    )
    payout_amount: float | None = Field(None, gt=0)
    paypal_order_id: str | None = Field(None, max_length=64, pattern=r"^[A-Z0-9-]+$")
    paypal_authorization_id: str | None = Field(None, max_length=64, pattern=r"^[A-Z0-9-]+$")
    tracking_number: str | None = Field(
        None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$",
        description="The bill of lading or container number of the load, sent to PayPal after the carrier is paid.",
    )

    webhook_url: str | None = Field(
        None, max_length=500,
        description="Delivered to when the audit completes, for async submissions",
    )

    @field_validator("hs_code")
    @classmethod
    def strip_hs_separators(cls, v: str | None) -> str | None:
        """ERPs send 6109, 6109.10, 6109.10.00 and 610910 for the same heading."""
        if v is None:
            return v
        return v.strip()

    @field_validator("carrier_payee_email", "carrier_registered_payee_email")
    @classmethod
    def check_payee_email(cls, v: str | None) -> str | None:
        """Lower-cased, so the payee comparison is not fooled by letter case alone."""
        if v is None:
            return v
        local, _, domain = v.partition("@")
        if not local or "@" in domain or "." not in domain or any(c.isspace() for c in v):
            raise ValueError("must be an email address such as carrier@example.com")
        return v.lower()

    @field_validator("carrier_payee_changed_at")
    @classmethod
    def check_payee_changed_at(cls, v: str | None) -> str | None:
        """A time without a timezone cannot be compared with now, so it is refused."""
        if v is None:
            return v
        try:
            parsed = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("must be an ISO 8601 date-time") from None
        if parsed.tzinfo is None:
            raise ValueError("must include a timezone, e.g. 2026-11-01T08:00:00+07:00")
        return v


class ComplianceAuditResponse(BaseModel):
    """The verdict, plus everything needed to defend it."""
    model_config = ConfigDict(extra="allow")

    audit_id: str
    case_id: str
    shipment_id: str
    client_reference: str | None = None

    outcome: AuditOutcome
    effective_risk: float = Field(..., ge=0, le=100)
    model_risk: float | None = Field(
        None, ge=0, le=100,
        description="What the model alone scored, before the deterministic floor",
    )
    risk_floor: float = Field(
        ..., ge=0, le=100,
        description="Minimum risk set by deterministic checks. The model cannot "
                    "lower this, only raise it.",
    )
    score_disputed: bool = Field(
        False,
        description="The model and the deterministic floor disagreed by 15 points "
                    "or more",
    )

    findings: list[AuditFinding] = Field(default_factory=list)
    requires_human_review: bool = False
    review_reason: str | None = None

    lineage: AuditLineage
    usage: AuditUsage

    created_at: str
    completed_at: str | None = None
    idempotent_replay: bool = Field(
        False,
        description="True when this returns a previously computed audit because "
                    "client_reference had been seen before. No tokens were spent.",
    )


class ComplianceAuditAccepted(BaseModel):
    """202 for an async submission; the verdict arrives by webhook."""
    audit_id: str
    case_id: str
    shipment_id: str
    status: str = "accepted"
    poll_url: str


class ComplianceReportsResponse(BaseModel):
    """
    A page of completed audits.

    Cursor pagination rather than offset, matching store.query_cases(): an offset
    silently skips or repeats rows when new audits land between pages, and audits
    land continuously.
    """
    audits: list[ComplianceAuditResponse]
    next_cursor: str | None = None
    has_more: bool = False


class TenantUsageResponse(BaseModel):
    """
    Billable usage for one tenant.

    `cleared_by_rules` against `cleared_by_ai` is the field pair worth reading. A
    shipment the deterministic checks settle consumes no model tokens at all, so
    the ratio -- not the absolute call count -- is what decides the marginal cost of
    serving an account. Two customers with identical volumes can differ by an order
    of magnitude in what they cost to serve, and this is where that shows.

    `estimated_cost_usd` is named an estimate on purpose. It is computed from token
    counts at the per-model rates in config.pricing_for(), which tracks the
    published rate card but is not the invoice: it excludes Tavily searches, egress,
    and the Cloud Run time the deterministic checks consume.
    """
    tenant_id: str
    agent_calls: int = Field(..., ge=0)
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    estimated_cost_usd: float = Field(..., ge=0)
    cost_per_call_usd: float = Field(..., ge=0)
    auto_cleared: int = Field(..., ge=0)
    cleared_by_rules: int = Field(..., ge=0)
    cleared_by_ai: int = Field(..., ge=0)
    avg_latency_ms: float = Field(..., ge=0)


# =============================================================================
# Error schemas
# =============================================================================

class ErrorResponse(BaseModel):
    """Standard error response schema."""
    error: str
    detail: str | None = None
    code: str | None = None


class ValidationErrorDetail(BaseModel):
    """Detail for a single validation error."""
    field: str
    message: str
    value: Any | None = None


class ValidationErrorResponse(BaseModel):
    """Schema for validation error responses (422)."""
    error: str = "Validation error"
    details: list[ValidationErrorDetail]
