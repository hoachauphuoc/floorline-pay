/**
 * TypeScript mirrors of the published audit contract.
 *
 * These follow src/vf_logistics/schemas.py field for field. Where the Python
 * model allows a null the type here is `| null` rather than optional, because
 * the API sends the key with a null value and `undefined` would let a missing
 * key pass unnoticed through an `if (x)` check that was meant to test for null.
 *
 * Regenerate against docs/swagger.json if the backend contract moves.
 */

/** Terminal outcomes an integrator branches on. Five, not two. */
export type AuditOutcome =
  | "CLEARED"
  | "HELD_FOR_REVIEW"
  | "BLOCKED"
  | "PENDING_HUMAN"
  | "ERROR";

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO" | "CLEAR";

export interface AuditFinding {
  code: string;
  severity: Severity;
  detail: string;
  /** Minimum risk this finding alone forces. The model cannot lower it. */
  floor: number;
  /**
   * Sanctions list entity ids behind a match. Empty for findings derived from
   * arithmetic, which have no list record to point at.
   */
  source_entity_ids: string[];
  /**
   * Public news citations behind an adverse-media finding.
   *
   * `null` and `[]` are different facts and must not be collapsed: null means
   * no search result was recorded for this finding, [] means a search ran and
   * returned nothing. Optional as well as nullable because an older backend
   * omits the key entirely, which carries the same meaning as null.
   */
  evidence_urls?: string[] | null;
  /** What the verifier measured. `documents_needed` names the papers that would answer it. */
  measured?: { documents_needed?: string[] | null } & Record<string, unknown>;
}

export interface AuditLineage {
  audit_id: string;
  /** Keyed by agent name. */
  prompt_hashes: Record<string, string>;
  /** Keyed by agent name, so each part of a verdict traces to its own model. */
  model_versions: Record<string, string>;
  sanctions_synced_at: string | null;
  sanctions_list_age_days: number | null;
  ruleset_version: number | null;
}

export interface AuditUsage {
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  agent_calls: number;
  latency_ms: number | null;
}

export interface ComplianceAuditResponse {
  audit_id: string;
  case_id: string;
  shipment_id: string;
  client_reference: string | null;

  outcome: AuditOutcome;
  effective_risk: number;
  /** What the model alone scored, before the deterministic floor. */
  model_risk: number | null;
  /** Deterministic minimum. The model may raise this, never lower it. */
  risk_floor: number;
  /** Model and floor disagreed by 15 points or more. */
  score_disputed: boolean;

  findings: AuditFinding[];
  requires_human_review: boolean;
  review_reason: string | null;

  lineage: AuditLineage;
  usage: AuditUsage;

  created_at: string;
  completed_at: string | null;
  idempotent_replay: boolean;
}

export interface ComplianceReportsResponse {
  audits: ComplianceAuditResponse[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface TenantUsageResponse {
  tenant_id: string;
  agent_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  cost_per_call_usd: number;
  auto_cleared: number;
  cleared_by_rules: number;
  cleared_by_ai: number;
  avg_latency_ms: number;
}

/** A tenant the console can display. */
export interface Tenant {
  id: string;
  name: string;
  /** Shown under the name in the switcher. */
  descriptor: string;
}

// ==========================================================================
// Operational surface
//
// The types above mirror the published B2B audit contract in schemas.py, which
// is versioned and validated. Everything below describes the *internal*
// dashboard API, which is not: those routes return whatever the store holds.
//
// So these are written defensively -- almost every field is nullable, and none
// of them is trusted to exist. A case written before a field was introduced
// simply lacks it, and `slim_case()` projects only eight keys, so the same
// `Case` shape arrives thin from `/cases` and fat from `/orchestrator/case/<id>`.
// ==========================================================================

/**
 * Case lifecycle states, exactly as orchestrator.py defines them.
 *
 * Kept as a union rather than a string so a screen cannot silently handle a
 * state that does not exist. Note that BLOCKED is absent on purpose: it is an
 * *audit outcome*, not a case state -- a case reaches BLOCKED_BY_HUMAN and the
 * audit projection reports BLOCKED. There is no autonomous block.
 */
export type CaseState =
  // actionable
  | "INGESTED"
  | "SPECIALISTS_DONE"
  | "INVESTIGATED"
  // terminal
  | "AUTO_CLEARED"
  | "HELD_FOR_REVIEW"
  | "ESCALATED"
  | "PENDING_HUMAN"
  | "RELEASED_BY_HUMAN"
  | "BLOCKED_BY_HUMAN"
  | "DEAD_LETTER"
  | "AWAITING_EVIDENCE"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTION_FAILED";

/** Cases sitting in a person's queue. Mirrors AWAITING_HUMAN. */
/**
 * States of the verification loop in which a reviewer still has an action to take,
 * or has to be told what an execution is doing (EXECUTING: running, or interrupted
 * with its claim expired).
 */
export const VERIFICATION_LOOP_STATES: readonly string[] = [
  "AWAITING_EVIDENCE",
  "APPROVED",
  "EXECUTING",
  "EXECUTION_FAILED",
];

/**
 * States a reviewer can re-evaluate a verification case from. Mirrors
 * evidence.REEVALUABLE on the backend, which is the authority (it answers 409 for
 * anything else); tests/test_evidence_reevaluable_mirror.py compares the two.
 */
export const REEVALUABLE_STATES: readonly string[] = [
  "AWAITING_EVIDENCE",
  "HELD_FOR_REVIEW",
  "ESCALATED",
  "PENDING_HUMAN",
];

export const AWAITING_HUMAN: readonly CaseState[] = [
  "PENDING_HUMAN",
  "HELD_FOR_REVIEW",
  "ESCALATED",
];

/** States the worker will pick up. Mirrors ACTIONABLE. */
export const ACTIONABLE: readonly CaseState[] = [
  "INGESTED",
  "SPECIALISTS_DONE",
  "INVESTIGATED",
];

/** The self-reported shipment record. Every field is caller-supplied. */
export interface Shipment {
  shipment_id?: string | null;
  shipper_company?: string | null;
  shipper_name?: string | null;
  shipper_tax_id?: string | null;
  consignee_name?: string | null;
  origin?: string | null;
  destination?: string | null;
  transit_country?: string | null;
  goods_description?: string | null;
  hs_code?: string | null;
  declared_value?: number | null;
  shipping_cost?: number | null;
  weight_kg?: number | null;
  quantity?: number | null;
  currency?: string | null;
  incoterms?: string | null;
  [key: string]: unknown;
}

/** One agent hop, with its own economics. */
export interface CaseStep {
  agent: string;
  model?: string | null;
  latency_ms?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  at?: string | null;
  parse_error?: boolean | null;
  /** The agent's raw payload. Shape varies per agent, so it stays unknown. */
  result?: Record<string, unknown> | null;
  /** Exact text sent to and returned by the model, when recorded. */
  prompt?: string | null;
  raw_response?: string | null;
  /**
   * Citations the agent actually read, as {title, url}.
   *
   * This lives on the STEP, not on the case: it is evidence a particular agent
   * fetched, and attributing it to the case would lose which reasoning it fed.
   * compliance and investigation are the two that populate it.
   */
  external_search_results?: ExternalCitation[] | null;
}

/** One web source an agent read at decision time. */
export interface ExternalCitation {
  title?: string | null;
  url?: string | null;
}

/** A receipt from tools.py, or a governance denial. */
export interface ActionReceipt {
  audit_id?: string | null;
  case_id?: string | null;
  action: string;
  status: "done" | "denied" | "skipped" | "failed" | string;
  at?: string | null;
  detail?: Record<string, unknown> | null;
  gate_reason?: string | null;
}

export interface GateDenial {
  action: string;
  reason?: string | null;
  human_triggers?: string[] | null;
}

/**
 * How the deterministic floor and the model score were combined.
 *
 * `source` says which one won. "deterministic floor" means the model was
 * overruled, which is the case a reviewer most needs to see.
 */
export interface Reconciliation {
  model_risk?: number | null;
  effective_risk?: number | null;
  /**
   * `risk_floor`, NOT `floor`. This interface declared `floor`, which
   * verifier.reconcile() has never emitted -- checked against the live API, where a
   * case's reconciliation carries exactly: auto_clear_permitted, effective_risk,
   * model_risk, risk_floor, score_disputed, source, veto_reasons.
   *
   * The consequence was silent and sat on the most important row in the console:
   * CaseTraceSheet guarded the "Rules floor" row on `rec.floor != null`, so the one
   * number that shows an agent cannot lower risk simply never rendered, on any case.
   * Same shape of bug as the dropped citations, which read `s.urls` off a step that
   * only ever had `external_search_results`.
   */
  risk_floor?: number | null;
  source?: string | null;
  /** Model and floor disagreed by 15 points or more. Computed server-side; do not
   * re-derive it from effective_risk, which equals max(model, floor) and so cannot
   * distinguish "the floor won" from "the model scored higher". */
  score_disputed?: boolean | null;
  /** Not sent by the backend today. `source === "deterministic floor"` is the live
   * signal; this stays only because reading it is harmless if it ever appears. */
  vetoed?: boolean | null;
  veto_reasons?: string[] | null;
  auto_clear_permitted?: boolean | null;
}

export interface Validation {
  findings?: AuditFinding[] | null;
  /**
   * Context a reviewer should see that carries no weight: no floor, not counted
   * as a finding (verifier.validate). A short trading record, freight above a
   * per-consignment typical. Rendered apart from the findings so it is never read
   * as a reason the shipment was held.
   */
  observations?: AuditFinding[] | null;
  risk_floor?: number | null;
  skip_ai?: boolean | null;
  auto_clear_by_rules?: boolean | null;
  auto_reject_by_rules?: boolean | null;
  hs_floor_effect?: number | null;
  [key: string]: unknown;
}

export interface DebateEffect {
  escalated: boolean;
  reason: string;
  threshold: number;
  risk_before: number;
  risk_after: number;
}

export interface HumanReview {
  action?: string | null;
  reviewer?: string | null;
  note?: string | null;
  at?: string | null;
  decided_by?: string | null;
  /** The state the case was in before, and the one this review put it in. */
  state_before?: string | null;
  state_after?: string | null;
  /** The outcome the agent had proposed, which the reviewer confirmed or overturned. */
  agent_proposed?: string | null;
  /** The SAR draft reference this review signed, if any. */
  sar_signed?: string | null;
}

/**
 * A case, thin or fat.
 *
 * `/cases` returns the eight `slim_case()` keys; `/orchestrator/case/<id>`
 * returns the whole document. One type covers both because the fat fields are
 * optional, which means a component reading `case.steps` has to cope with it
 * being absent -- and that is the honest situation, not a modelling compromise.
 */
export interface Case {
  case_id: string;
  shipment_id?: string | null;
  state: CaseState;
  source?: string | null;
  risk_score?: number | null;
  model_risk_score?: number | null;
  risk_level?: string | null;
  compliance_status?: string | null;
  compliance_score?: number | null;
  claimed?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;

  // Fat fields, present only on the per-case fetch.
  shipment?: Shipment | null;
  steps?: CaseStep[] | null;
  actions?: ActionReceipt[] | null;
  validation?: Validation | null;
  reconciliation?: Reconciliation | null;
  decision?: { outcome?: string; rationale?: string } | null;
  proposed_outcome?: string | null;
  gate_denials?: GateDenial[] | null;
  /**
   * Every human decision on the case, oldest first (orchestrator.human_decide appends
   * one per decision). PLURAL: the stored case has never carried a singular `review`
   * -- that key exists only on the decide response -- so a screen reading `review`
   * off a fetched case found nothing, on every case.
   */
  reviews?: HumanReview[] | null;
  /** @deprecated Present on the decide response only, never on a stored case. Read `reviews`. */
  review?: HumanReview | null;
  /** How an auto-cleared case was cleared: by the rules alone, or after the agents. */
  cleared_by?: "rules" | "ai" | string | null;
  requires_human?: boolean | null;
  provenance?: Record<string, unknown> | null;
  input_security?: Record<string, unknown> | null;
  route_intelligence?: string | null;
  /**
   * Per-search METADATA: {type, results, cached, status, at}. Counts, not citations.
   *
   * Deliberately not `urls`. An earlier version of the trace sheet read `s.urls` from
   * these entries, which never existed on them, so the citation list silently rendered
   * empty for every case while the section claimed to reproduce sources. The URLs are on
   * CaseStep.external_search_results.
   */
  tavily_searches?: Array<Record<string, unknown>> | null;
  /**
   * TWO fields, because there are two debate paths and the orchestrator names them
   * differently:
   *
   *   `auto_debate` -- fired with no human involved when the floor and the model
   *                    disagree by 15 points or more (orchestrator.py sets it on the
   *                    automatic path). This is the one the submission advertises.
   *   `debate`      -- a reviewer clicked Deep Review (set on the manual path).
   *
   * The console read only `debate`, so the verdict card mounted only for manually
   * triggered reviews -- and no seeded case has one, because seeding never clicks the
   * button. The headline capability was the one that could not be shown. Read both.
   */
  auto_debate?: Record<string, unknown> | null;
  debate?: Record<string, unknown> | null;
  /**
   * What the automatic debate changed (orchestrator._apply_debate_verdict). Always
   * written when a debate ran, so "did it matter here, and why" has an answer
   * whichever way it went.
   */
  debate_effect?: DebateEffect | null;
  debate_escalated?: boolean | null;
  attempts?: number | null;
  last_error?: string | null;
  lineage_audit_id?: string | null;

  /** Verification loop state (evidence.py). */
  verification?: Verification | null;

  // Rollups, denormalised for aggregation.
  _agent_calls?: number | null;
  _input_tokens?: number | null;
  _output_tokens?: number | null;
  _estimated_cost_usd?: number | null;
  _sum_latency_ms?: number | null;
}

export interface CaseEvent {
  event_id: string;
  case_id: string;
  kind: string;
  message: string;
  at: string;
  [key: string]: unknown;
}

export interface AuditRecord {
  audit_id: string;
  case_id: string;
  action: string;
  status: string;
  at: string;
  detail?: Record<string, unknown> | null;
  /** Who acted, on the rows that name one (human decisions, board resets). */
  actor?: string | null;
  /** How they signed in: password, one_click, api_key... (auth.AuthContext.actor_auth). */
  actor_auth?: string | null;
}

export interface WorkerStatus {
  mode?: string | null;
  running?: boolean | null;
  started_at?: string | null;
  ticks?: number | null;
  advanced?: number | null;
  failed?: number | null;
  last_tick_error?: string | null;
  thresholds?: Record<string, number> | null;
}

export interface DelegationBoundary {
  boundary_id: string;
  version: number | null;
  status: "ACTIVE" | "SUPERSEDED" | "REVOKED" | "SIMULATED" | string;
  permissions: Record<string, unknown>;
  published_by?: string | null;
  note?: string | null;
  published_at?: string | null;
  supersedes?: string | null;
  revoked_by?: string | null;
  revoked_at?: string | null;
  revocation_note?: string | null;
}

export interface DriftMetrics {
  sample: number;
  auto_release_rate: number;
  veto_rate: number;
  injection_rate: number;
  boundary_version?: number | null;
}

export interface DriftCheck {
  material: boolean;
  reason?: string | null;
  reasons: string[];
  /**
   * Absent when no boundary has been published, because there was nothing to
   * measure drift against. Not defaulted to zeros: "we did not measure" and "we
   * measured and found nothing" are different facts, and a zeroed sample would
   * read as the second.
   */
  metrics?: DriftMetrics | null;
}

/**
 * Whether the agent may act at all.
 *
 * SUSPENDED with no boundary is the fail-closed default, not an error: with no
 * published delegation boundary every outcome stays a proposal and the case
 * goes to a human. A console that renders this as a warning badge and nothing
 * else leaves the operator wondering why nothing auto-clears.
 */
export interface AgentReadiness {
  state: "READY" | "SUSPENDED" | string;
  reason: string;
  boundary: DelegationBoundary | null;
  drift?: DriftCheck | null;
}

/** The pre-AI screening rule set, per tenant. */
export interface PrefilterRules {
  vip_registry: Array<{ company: string; tax_id: string }>;
  blacklist_companies: string[];
  blacklist_tax_ids: string[];
  safe_routes: Array<{ origin: string; destination: string }>;
  low_value_threshold_usd: number;
}

/** GET /api/v1/orchestrator/state */
export interface OrchestratorSnapshot {
  cases: Case[];
  events: CaseEvent[];
  audit: AuditRecord[];
  counts: Partial<Record<CaseState, number>>;
  in_flight: number;
  awaiting_human: number;
  agent: AgentReadiness;
  agent_calls: number;
  avg_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  tokens_by_agent: Record<
    string,
    // `cost_usd` is summed server-side from each step's own rate, not derived here.
    // The rate depends on which model ran the step -- Nano for fraud and compliance,
    // Super for investigation and the debate -- so multiplying these tokens by
    // any single model's price would misreport every mixed window.
    { calls: number; input: number; output: number; cost_usd: number }
  >;
  estimated_cost_usd: number;
  worker: WorkerStatus;
  at: string;
  /** Present only when the request drained a case. */
  drained_this_request?: { drained: number; case_ids: string[] } | null;
}

/** GET /api/v1/metrics/summary -- the exact whole-collection totals. */
export interface MetricsSummary {
  counts: Partial<Record<CaseState, number>>;
  in_flight: number;
  awaiting_human: number;
  agent_calls: number;
  avg_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  estimated_cost_usd: number;
  cleared_by_rules: number;
  cleared_by_ai: number;
  cleared_by_unknown: number;
  total_auto_cleared: number;
  at: string;
}

// ==========================================================================
// Verification loop (evidence.py)
// ==========================================================================

export interface EvidenceRequest {
  request_id: string;
  documents: string[];
  status: string;
  received_docs?: Record<string, string> | null;
  owner: string;
  created_at: string;
  received_at?: string | null;
}

export interface EvidenceDocument {
  document_id: string;
  request_id: string;
  evidence_version: number;
  doc_type: string;
  filename: string;
  sha256: string;
  received_at: string;
  blocked: boolean;
  extracted?: Record<string, unknown> | null;
  /** Which model read the paper, and what it cost. Recorded per document. */
  extraction?: {
    model?: string | null;
    cost_usd?: number | null;
    parse_error?: unknown;
  } | null;
}

/** What the papers say that disagrees with the declaration. `detail` names the paper. */
export interface EvidenceContradiction {
  /** certificate_country, input_origin or producer. */
  kind: string;
  detail: string;
}

export interface EvidenceEvaluation {
  evaluation_id: string;
  evidence_version: number;
  at: string;
  risk_floor: number;
  status: string;
  clarified: string[];
  unresolved: string[];
  contradictions: EvidenceContradiction[];
  /** Finding codes the evaluation computed (not merged into the case's own findings). */
  findings?: string[];
}

export interface EvidenceApproval {
  approval_id: string;
  action: string;
  approver: string;
  evidence_version: number;
  status: string;
  at: string;
  note?: string | null;
  /** True when the payout is large enough that a second person must approve before it is executed. */
  requires_second?: boolean;
  /** The second person's approval: who, when and what they checked. */
  second_approval?: { approver: string; at: string; note: string } | null;
  /** Set when a release waived missing or contradicting evidence: what, why, and by whom. */
  exception?: {
    unresolved: string[];
    reason: string;
    approved_by: string;
    contradictions?: EvidenceContradiction[];
  } | null;
}

export interface EvidenceExecution {
  execution_id: string;
  approval_id: string;
  executor: string;
  status: string;
  message_id?: string | null;
  at: string;
  reason?: string | null;
  /**
   * Unix SECONDS (not milliseconds) at which this claim's lease ends. Set while the
   * status is "claiming". evidence.execute_approval refuses a second execute until
   * then and resumes the same execution after it; a missing value counts as expired.
   */
  claim_expires_epoch?: number | null;
  /** Who picked an interrupted claim up again, and when. */
  resumed?: Array<{ executor: string; at: string }>;
}

export interface Verification {
  evidence_version: number;
  requests: EvidenceRequest[];
  documents: EvidenceDocument[];
  evaluations: EvidenceEvaluation[];
  approvals: EvidenceApproval[];
  executions?: EvidenceExecution[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/** POST /api/v1/review/<id>/decide */
export interface ReviewDecisionResult {
  ok: boolean;
  error?: string | null;
  case_id?: string | null;
  state?: CaseState | null;
  review?: HumanReview | null;
  receipts?: ActionReceipt[] | null;
}

/** POST /api/v1/review/<id>/deep-review */
export interface DeepReviewResult {
  ok: boolean;
  error?: string | null;
  case_id?: string | null;
  debate?: Record<string, unknown> | null;
  verdict?: {
    verdict?: string;
    confidence?: number;
    rationale?: string;
  } | null;
  latency_ms?: number | null;
}

/**
 * The human actions the review form offers. Mirrors HUMAN_ACTIONS exactly.
 *
 * Three, not four. There is no "hold" or "escalate" action a reviewer can take:
 * HELD_FOR_REVIEW and ESCALATED are states the *workflow* puts a case into, and
 * the only way out of them is one of these three. `request_info` keeps the case
 * in PENDING_HUMAN and records why, which is how a reviewer parks something
 * without pretending to have decided it.
 */
export type HumanAction = "release" | "block" | "request_info";

/** Where each action leaves the case, for the confirmation copy. */
export const HUMAN_ACTION_RESULT: Record<HumanAction, CaseState> = {
  release: "RELEASED_BY_HUMAN",
  block: "BLOCKED_BY_HUMAN",
  request_info: "PENDING_HUMAN",
};

// ==========================================================================
// Operator screens (worklist.py): Today and Outcomes
// ==========================================================================

/** What a case is waiting for. Mirrors worklist.BUCKETS, in display order. */
export type WorkBucket =
  | "awaiting_documents"
  | "awaiting_review"
  | "awaiting_sign_off"
  | "awaiting_execution";

export const WORK_BUCKETS: readonly WorkBucket[] = [
  "awaiting_documents",
  "awaiting_review",
  "awaiting_sign_off",
  "awaiting_execution",
];

export interface WorkOwner {
  kind: "person" | "queue" | "unassigned";
  name: string | null;
}

/** One row of the Today screen: a projection of a case, never the case. */
export interface WorkItem {
  case_id: string;
  shipment_id?: string | null;
  /** A case state, or a state of the verification loop the union does not list yet. */
  state: string;
  bucket: WorkBucket;
  risk_score?: number | null;
  owner: WorkOwner;
  next_action: { code: string; text: string } | null;
  waiting_since?: string | null;
  sla_hours?: number | null;
  due_at?: string | null;
  information_rounds: number;
  finding_count: number;
  top_finding?: string | null;
  created_at?: string | null;
}

/** GET /api/v1/worklist */
export interface Worklist {
  buckets: Record<WorkBucket, { count: number; items: WorkItem[] }>;
  total: number;
  scanned: number;
  /** True when the tenant has more waiting cases than one call reads. */
  truncated: boolean;
  at: string;
}

/** A distribution. Every figure is null when nothing was measured. */
export interface MeasuredSummary {
  n: number;
  median: number | null;
  p95: number | null;
  total: number | null;
}

/** GET /api/v1/outcomes */
export interface Outcomes {
  window: { since: string | null; until: string | null };
  resolved: {
    total: number;
    by: {
      cleared_by_rules: number;
      cleared_by_ai: number;
      released_by_reviewer: number;
      blocked_by_reviewer: number;
    };
  };
  time_to_resolution_ms: MeasuredSummary;
  human_handling_ms: MeasuredSummary & { of_human_resolved: number };
  information_rounds: {
    total: number;
    cases_with_rounds: number;
    mean_per_resolved: number | null;
  };
  cost_per_resolved_usd: MeasuredSummary & { mean: number | null };
  model_latency_ms: MeasuredSummary;
  held_now: { total: number; by_state: Record<string, number> };
  refused_actions: { total: number };
  external_wait_ms: { value: number | null; reason: string };
  scanned: number;
  truncated: boolean;
  at: string;
}

