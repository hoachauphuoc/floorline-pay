import { DEMO_MODE } from "./config";
import { demoAudits, demoUsage } from "./demo-data";
import type { BookingForm } from "./booking";
import type {
  AgentReadiness,
  AuditRecord,
  Case,
  CaseEvent,
  ComplianceAuditResponse,
  ComplianceReportsResponse,
  DeepReviewResult,
  DelegationBoundary,
  DriftCheck,
  HumanAction,
  MetricsSummary,
  OrchestratorSnapshot,
  Outcomes,
  Page,
  PrefilterRules,
  ReviewDecisionResult,
  TenantUsageResponse,
  Worklist,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: "unreachable" | "http" | "parse",
    /**
     * The role a 403 said it needed (`required_role` in auth.py's refusal), so a
     * screen can say "needs the operator role" instead of a bare "forbidden".
     */
    readonly requiredRole: string | null = null,
    /**
     * The sign-in a 403 said it needed (`required_auth`), today only "password":
     * the refusal a one-click session gets on Clear board.
     */
    readonly requiredAuth: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Thrown when a screen that needs live data is asked for it in demo mode.
 *
 * Demo mode has fixtures for the two B2B reads only. Rather than invent
 * plausible operational data for the other five screens, they say they need the
 * live API. A console that renders a fabricated pipeline is the same liability
 * the demo pill exists to prevent -- and a fabricated *governance boundary*
 * would be worse, because it would show an authority that was never delegated.
 */
export class DemoModeUnavailable extends ApiError {
  constructor(what: string) {
    super(
      `${what} has no demo fixture. Set NEXT_PUBLIC_DEMO_MODE=false and point ` +
        `FLASK_API_BASE at a running API to use this screen.`,
      501,
      "http",
    );
    this.name = "DemoModeUnavailable";
  }
}

/**
 * Client-side ceiling on a single request.
 *
 * The BFF proxy already carries `AbortSignal.timeout(15_000)` for GET, but that governs
 * the proxy's call to the backend, not the browser's call to the proxy. A route handler
 * that hangs -- cold start, an exhausted connection pool, an unhandled await -- leaves
 * the browser fetch pending with nothing to end it, and almost every query in this app
 * sets `retry: false`, so the screen sits on its skeleton indefinitely.
 *
 * Longer than the proxy's own 15s so a genuine upstream timeout surfaces as the proxy's
 * error rather than being masked by this one, which would report "unreachable" for a
 * backend that answered with a 504.
 */
const CLIENT_TIMEOUT_MS = 20_000;

/**
 * The ceiling for the two writes that run models before they answer: a document
 * upload (Model Armor, extraction, then the whole workflow in on-demand mode --
 * 30 to 60 seconds is normal) and a deep review (a multi-round debate on the
 * Senior Auditor model). With the 20s default the browser gave up first and reported "the API
 * is unreachable" while the server finished the job and created the case.
 *
 * A little over the BFF's own 120s write timeout, so a genuine upstream timeout
 * still surfaces as the BFF's error rather than this one.
 */
export const LONG_WRITE_TIMEOUT_MS = 125_000;

async function request(
  path: string,
  init?: RequestInit,
  timeoutMs: number = CLIENT_TIMEOUT_MS,
): Promise<{ text: string; status: number; ok: boolean }> {
  let response: Response;
  try {
    response = await fetch(`/api/proxy/${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      // An explicit signal on `init` wins, so a caller that wants its own cancellation
      // is not overridden.
      signal: init?.signal ?? AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    // A timeout arrives as a TimeoutError DOMException, whose message is "signal timed
    // out" -- accurate but meaningless to an operator reading it on a card.
    const timedOut =
      error instanceof DOMException && error.name === "TimeoutError";
    throw new ApiError(
      timedOut
        ? `The request took longer than ${Math.round(timeoutMs / 1000)}s and was abandoned.`
        : error instanceof Error
          ? error.message
          : String(error),
      0,
      "unreachable",
    );
  }
  // The server answered, then the connection dropped while its body was read: what it did
  // is as unknown as for a request that never got an answer (N08).
  let text: string;
  try {
    text = await response.text();
  } catch (error) {
    throw new ApiError(
      `The connection dropped while the answer was read: ${error instanceof Error ? error.message : String(error)}`,
      response.status,
      "unreachable",
    );
  }
  return { text, status: response.status, ok: response.ok };
}

function parseOrThrow<T>(text: string, status: number, ok: boolean): T {
  if (!ok) {
    // Prefer the upstream's own error text; a bare status code tells an operator
    // nothing about whether the API is down or the request was wrong.
    let detail = `HTTP ${status}`;
    let requiredRole: string | null = null;
    let requiredAuth: string | null = null;
    try {
      const parsed = JSON.parse(text) as {
        error?: string;
        detail?: string;
        details?: string[];
        supported?: string[];
        required_role?: string;
        required_auth?: string;
      };
      detail =
        parsed.details?.join("; ") ?? parsed.detail ?? parsed.error ?? detail;
      requiredRole = parsed.required_role ?? null;
      requiredAuth = parsed.required_auth ?? null;

      // A 415 from the document route answers "what should I have sent?" in a
      // `supported` array. Appended rather than dropped: without it the message
      // reads "Unsupported file type: contract.docx" and leaves the user to
      // guess. Handled here rather than at the upload call site so any route
      // that starts returning a supported list gets the same treatment.
      if (parsed.supported?.length) {
        detail = `${detail} — accepted: ${parsed.supported.join(", ")}`;
      }
    } catch {
      /* keep the status */
    }
    throw new ApiError(
      detail,
      status,
      // 502 and 504 are the gateway's, not the API's: the API may have finished the job (N08).
      status === 502 || status === 504 ? "unreachable" : "http",
      requiredRole,
      requiredAuth,
    );
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError("Response was not JSON", status, "parse");
  }
}

async function getJson<T>(path: string): Promise<T> {
  const { text, status, ok } = await request(path);
  return parseOrThrow<T>(text, status, ok);
}

async function postJson<T>(
  path: string,
  body?: unknown,
  timeoutMs: number = CLIENT_TIMEOUT_MS,
): Promise<T> {
  const { text, status, ok } = await request(
    path,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    },
    timeoutMs,
  );
  return parseOrThrow<T>(text, status, ok);
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const { text, status, ok } = await request(path, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseOrThrow<T>(text, status, ok);
}

function liveOnly(what: string): never {
  throw new DemoModeUnavailable(what);
}

// --------------------------------------------------------------------------
// B2B contract reads (the two the console was originally built on)
// --------------------------------------------------------------------------

/**
 * Audits for a tenant.
 *
 * `tenantId` is used as a fixture key in demo mode and as a cache key in live
 * mode -- it is never sent to the API, because the API decides the tenant from
 * the authenticated identity and would ignore it. Keeping it in the query key
 * anyway is what makes a tenant switch discard the previous tenant's rows
 * instead of showing them under a new name.
 */
export async function fetchAudits(
  tenantId: string,
): Promise<ComplianceAuditResponse[]> {
  if (DEMO_MODE) return demoAudits(tenantId);
  const page = await getJson<ComplianceReportsResponse>(
    "compliance/reports?limit=50",
  );
  return page.audits;
}

export async function fetchUsage(
  tenantId: string,
): Promise<TenantUsageResponse> {
  if (DEMO_MODE) return demoUsage(tenantId);
  return getJson<TenantUsageResponse>("billing/usage");
}

// --------------------------------------------------------------------------
// Pipeline
// --------------------------------------------------------------------------

/**
 * The board snapshot.
 *
 * `drain` defaults to 0 and is opt-in for one reason: GET
 * /api/v1/orchestrator/state *mutates* when drain=1 -- it advances one case per
 * request in ondemand mode. Every screen polls this endpoint, so a default of 1
 * would make six screens six competing pipeline advancers, which is exactly how
 * the optimistic-lock conflicts in the last round were produced. Only the
 * Pipeline screen asks for it.
 */
export async function fetchSnapshot(
  { drain = false, limit = 60 }: { drain?: boolean; limit?: number } = {},
): Promise<OrchestratorSnapshot> {
  if (DEMO_MODE) liveOnly("The pipeline board");
  return getJson<OrchestratorSnapshot>(
    `orchestrator/state?limit=${limit}&drain=${drain ? 1 : 0}`,
  );
}

export async function fetchMetrics(): Promise<MetricsSummary> {
  if (DEMO_MODE) liveOnly("Exact metrics");
  return getJson<MetricsSummary>("metrics/summary");
}

export async function fetchCases(
  { states, cursor, limit = 50 }:
    { states?: string[]; cursor?: string | null; limit?: number } = {},
): Promise<Page<Case>> {
  if (DEMO_MODE) liveOnly("The case list");
  const params = new URLSearchParams({ limit: String(limit) });
  // `state`, singular, even though it accepts a comma-separated list. The route
  // reads request.args.get("state") and splits on commas; sending `states` is
  // silently ignored, which returns every case rather than an error.
  if (states?.length) params.set("state", states.join(","));
  if (cursor) params.set("cursor", cursor);
  return getJson<Page<Case>>(`cases?${params}`);
}

export async function fetchCase(caseId: string): Promise<Case> {
  if (DEMO_MODE) liveOnly("The case trace");
  return getJson<Case>(`orchestrator/case/${encodeURIComponent(caseId)}`);
}

export async function fetchEvents(
  { cursor, limit = 80 }: { cursor?: string | null; limit?: number } = {},
): Promise<Page<CaseEvent>> {
  if (DEMO_MODE) liveOnly("The event feed");
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return getJson<Page<CaseEvent>>(`events?${params}`);
}

// --------------------------------------------------------------------------
// Review queue
// --------------------------------------------------------------------------

/**
 * Cases waiting on a person.
 *
 * Note the response key: this route returns `{cases, next_cursor}` while
 * `/cases`, `/audit` and `/events` all return `{items, next_cursor}`. It is the
 * only one that differs, and typing it as `Page<Case>` made `items` undefined --
 * which rendered as an empty queue rather than as an error, so a reviewer would
 * have been shown "nothing to review" with cases sitting in it. Normalised here
 * so no screen has to remember the exception.
 */
export async function fetchReviewQueue(
  { cursor, limit = 40 }: { cursor?: string | null; limit?: number } = {},
): Promise<Page<Case>> {
  if (DEMO_MODE) liveOnly("The review queue");
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const page = await getJson<{ cases: Case[]; next_cursor: string | null }>(
    `review/queue?${params}`,
  );
  return { items: page.cases ?? [], next_cursor: page.next_cursor };
}

/**
 * Record a named human's decision.
 *
 * `reviewer` is deliberately NOT sent. The server reads the acting person from the
 * verified console session and ignores any name in the body, so sending one would
 * be a field that looks authoritative and is discarded -- which is how a caller
 * ends up believing they set something they did not. A note is still sent: the
 * backend requires one for anything other than a plain release.
 *
 * Returns `{ok: false, error}` with a 400 rather than throwing on a refused
 * decision. Both shapes are handled by the caller, because a refusal here is a
 * normal outcome and not an error.
 */
export async function decideReview(args: {
  caseId: string;
  action: HumanAction;
  note: string;
}): Promise<ReviewDecisionResult> {
  if (DEMO_MODE) liveOnly("Recording a review decision");
  return postJson<ReviewDecisionResult>(
    `review/${encodeURIComponent(args.caseId)}/decide`,
    { action: args.action, note: args.note },
  );
}

/**
 * The Senior Auditor re-reads the case. Expensive, opt-in,
 * and slow -- a multi-round debate -- so it gets the long write timeout.
 */
export async function requestDeepReview(
  caseId: string,
): Promise<DeepReviewResult> {
  if (DEMO_MODE) liveOnly("Deep review");
  return postJson<DeepReviewResult>(
    `review/${encodeURIComponent(caseId)}/deep-review`,
    undefined,
    LONG_WRITE_TIMEOUT_MS,
  );
}

/** The archived paperwork, for the review iframe. Returns a proxy URL. */
export function reviewDocumentUrl(caseId: string): string {
  return `/api/proxy/review/${encodeURIComponent(caseId)}/document`;
}

/**
 * The case as a due-diligence dossier (PDF). A proxy URL, opened in a new tab:
 * the browser renders it, and a reviewer saves it to the compliance file.
 */
export function caseDossierUrl(caseId: string): string {
  return `/api/proxy/orchestrator/case/${encodeURIComponent(caseId)}/dossier`;
}

// --------------------------------------------------------------------------
// Verification loop (evidence.py): the new API for N08
// --------------------------------------------------------------------------

export async function requestEvidence(
  caseId: string,
  documents?: string[],
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Requesting evidence");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/request-evidence`,
    documents ? { documents } : undefined,
  );
}

export async function reevaluateCase(
  caseId: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Re-evaluating");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/reevaluate`,
    undefined,
  );
}

export async function cancelEvidenceRequest(
  caseId: string,
  requestId: string,
  reason: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Cancelling a request");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(requestId)}/cancel`,
    { reason },
  );
}

export async function approveCase(
  caseId: string,
  action: "release" | "block",
  note?: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Approving");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/approve`,
    { action, note: note ?? "" },
  );
}

/**
 * Release over unresolved or contradicting evidence: the exception operation (N04).
 * The API wants the reason and every item the release waives, the operator role and a
 * password sign-in.
 */
export async function approveException(
  caseId: string,
  reason: string,
  waived: string[],
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Approving an exception");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/approve-exception`,
    { reason, waived },
  );
}

/**
 * The second person's approval of a release large enough to need two. The API wants a
 * reviewer other than the first approver, a password sign-in and a note.
 */
export async function secondApprove(
  caseId: string,
  approvalId: string,
  note: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Approving as the second approver");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/second-approve`,
    { approval_id: approvalId, note },
  );
}

export async function executeApproval(
  caseId: string,
  approvalId: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Executing");
  return postJson<Record<string, unknown>>(
    `review/${encodeURIComponent(caseId)}/execute`,
    { approval_id: approvalId },
  );
}

// --------------------------------------------------------------------------
// Audit trail
// --------------------------------------------------------------------------

/**
 * The audit log, filtered server-side.
 *
 * `case_id`, `action` and `status` are mutually exclusive upstream -- the route
 * applies whichever it finds and a wider combination would need a composite
 * index this project does not declare. Enforced here by sending exactly one, in
 * that order of precedence, rather than sending several and hoping.
 */
export async function fetchAuditTrail(
  { caseId, action, status, cursor, limit = 60 }: {
    caseId?: string | null;
    action?: string | null;
    status?: string | null;
    cursor?: string | null;
    limit?: number;
  } = {},
): Promise<Page<AuditRecord>> {
  if (DEMO_MODE) liveOnly("The audit trail");
  const params = new URLSearchParams({ limit: String(limit) });
  if (caseId) params.set("case_id", caseId);
  else if (action) params.set("action", action);
  else if (status) params.set("status", status);
  if (cursor) params.set("cursor", cursor);
  // Normalised for the same reason fetchReviewQueue is (see the comment at :233): a
  // response without `items` left `trail.data?.items ?? []` producing an empty array at
  // the call site, so the screen rendered "No audit records match" over a populated
  // trail. That fix was applied to the sibling and not to this one.
  const page = await getJson<Page<AuditRecord>>(`audit?${params}`);
  return {
    items: page?.items ?? [],
    next_cursor: page?.next_cursor ?? null,
  };
}

// --------------------------------------------------------------------------
// Governance
// --------------------------------------------------------------------------

export async function fetchAgentReadiness(): Promise<AgentReadiness> {
  if (DEMO_MODE) liveOnly("Agent readiness");
  return getJson<AgentReadiness>("governance/agent");
}

/**
 * Drift metrics alone.
 *
 * This route returns the drift object at the top level -- `{material, reason,
 * reasons, metrics}` -- not `{agent, drift}`. Typing it the other way made
 * `data.agent` undefined, which rendered the governance banner as "Operating
 * under undefined" while reporting the agent as ready: the fail-closed state
 * would have been displayed as the healthy one, on the screen whose whole job is
 * to say whether the agent may act.
 *
 * Note `metrics` is absent entirely when no boundary has been published -- the
 * route falls back to `{material: false, reasons: []}` -- so it is optional here
 * rather than defaulted, because a zeroed metrics block would read as "we
 * measured and found nothing" instead of "we did not measure".
 */
export async function fetchDrift(): Promise<DriftCheck> {
  if (DEMO_MODE) liveOnly("Drift detection");
  return getJson<DriftCheck>("governance/drift");
}

export async function fetchBoundaries(): Promise<{
  boundaries: DelegationBoundary[];
  proposed_template: Record<string, unknown>;
}> {
  if (DEMO_MODE) liveOnly("Delegation boundaries");
  // Note the response has no `active` key. The active boundary is whichever one
  // has status ACTIVE, and `proposed_template` is a starting point for the form,
  // not something that has been granted -- rendering it as the current authority
  // would show a delegation nobody published.
  return getJson<{
    boundaries: DelegationBoundary[];
    proposed_template: Record<string, unknown>;
  }>("governance/boundaries");
}

export async function publishBoundary(args: {
  permissions: Record<string, unknown>;
  note: string;
}): Promise<{ published: boolean; boundary: DelegationBoundary }> {
  if (DEMO_MODE) liveOnly("Publishing a delegation boundary");
  return postJson("governance/publish", args);
}

/**
 * The kill switch.
 *
 * No `author` is sent. The backend records the signed-in identity (app.py,
 * governance_revoke), and a typed name it would then ignore -- or, with no
 * verified identity, record unverified -- is worse than no field.
 */
export async function revokeBoundary(args: {
  note: string;
}): Promise<{
  revoked: boolean;
  reason: string;
  agent_state: string | null;
  boundary: DelegationBoundary | null;
}> {
  if (DEMO_MODE) liveOnly("Revoking a delegation boundary");
  return postJson("governance/revoke", args);
}

/**
 * Replay recent cases against a candidate boundary. Writes nothing.
 *
 * `check()` is pure, so this is safe to call freely -- no boundary is published
 * and no action is executed. The candidate is wrapped in a throwaway envelope
 * carrying version null, which is why the per-case rows report a decision rather
 * than a version.
 */
export async function simulateBoundary(
  permissions: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Boundary simulation");
  return postJson("governance/simulate", { permissions });
}

export async function fetchPrefilterRules(): Promise<PrefilterRules> {
  if (DEMO_MODE) liveOnly("Pre-filter rules");
  return getJson<PrefilterRules>("governance/prefilter-rules");
}

/**
 * Save a partial rule update.
 *
 * Partial on purpose: the backend leaves any key absent from the body as it was,
 * so a screen editing the blacklist does not have to resubmit the VIP registry
 * and risk clobbering a concurrent edit to it.
 *
 * No `author` is sent. The backend derives it from the authenticated identity
 * and ignores a body field, because a self-declared author on a control that
 * decides which shipments skip screening is worth nothing.
 */
export async function savePrefilterRules(
  patch: Partial<PrefilterRules>,
): Promise<{ ok: boolean; rules: PrefilterRules }> {
  if (DEMO_MODE) liveOnly("Editing pre-filter rules");
  return putJson("governance/prefilter-rules", patch);
}

export async function verifyEntity(args: {
  type: "company" | "tax_id";
  value: string;
  country?: string;
}): Promise<{
  verified: boolean;
  confidence: string;
  results: Array<{ title: string; url: string; snippet: string }>;
  summary: string;
}> {
  if (DEMO_MODE) liveOnly("Entity verification");
  return postJson("governance/verify-entity", args);
}

/** One result from the sanctions-news sweep. */
export interface TavilyScanAlert {
  title: string;
  snippet: string;
  url: string;
}

/**
 * Search recent news for sanctions and enforcement updates (governance admin).
 *
 * Up to five Tavily searches per call, metered against the monthly quota, and
 * rate-limited to 5/min upstream. With no queries the backend runs its own three
 * defaults. Returns what was found; it changes nothing on the service.
 */
export async function runTavilyScan(queries?: string[]): Promise<{
  scan_count: number;
  alerts: TavilyScanAlert[];
  alert_count: number;
  summary: string;
}> {
  if (DEMO_MODE) liveOnly("The sanctions-news scan");
  return postJson("governance/tavily-scan", queries?.length ? { queries } : {});
}

// --------------------------------------------------------------------------
// DevOps
// --------------------------------------------------------------------------

/**
 * Inject the scripted demo batch.
 *
 * Takes no shipment. The route builds `simulator.scripted_shipments()` and
 * ignores the request body entirely, so a signature accepting a custom shipment
 * would be a lie that silently discarded it. Use submitShipmentEvent for a
 * hand-written one.
 *
 * Returns 202 with the ids it queued, not a finished case: the workflow runs
 * afterwards and the Pipeline board is where the outcome appears.
 */
export async function injectScriptedBatch(): Promise<{
  injected: number;
  case_ids: string[];
  note?: string;
}> {
  if (DEMO_MODE) liveOnly("Injecting the demo batch");
  return postJson("simulate");
}

export async function injectBulk(
  count: number,
): Promise<{ queued: number; case_ids: string[]; note?: string }> {
  if (DEMO_MODE) liveOnly("Bulk injection");
  return postJson("simulate/bulk", { count });
}

/** What creating a PayPal order for the shipper to approve answers with. */
export interface PaymentOrderRequest {
  amount: number;
  currency: string;
  reference_id: string;
  return_url: string;
  cancel_url: string;
  booking?: BookingForm;
}

export async function createPaymentOrder(
  request: PaymentOrderRequest,
): Promise<{ order_id: string; paypal_status: string | null; approval_url: string }> {
  if (DEMO_MODE) liveOnly("Creating a PayPal order");
  return postJson("payments/orders", request);
}

/** Authorize the order the shipper approved; the answer carries the authorization id. */
export async function authorizePaymentOrder(
  orderId: string,
): Promise<{ order_id: string; authorization_id: string }> {
  if (DEMO_MODE) liveOnly("Authorizing a PayPal payment");
  return postJson(`payments/orders/${encodeURIComponent(orderId)}/authorize`);
}

/** The form the server kept with this order; a 404 means it kept none. */
export async function fetchBookingDraft(orderId: string): Promise<{ order_id: string; booking: Partial<BookingForm> }> {
  if (DEMO_MODE) liveOnly("Reading a kept booking");
  return getJson<{ order_id: string; booking: Partial<BookingForm> }>(`payments/orders/${encodeURIComponent(orderId)}/booking`);
}

/** A payee as the carrier registry reports it: registered by one person, verified by another. */
export type CarrierPayee = {
  version: number;
  payee_email: string;
  registered_by: string;
  registered_at?: string | null;
  verified_by?: string | null;
  verified_at?: string | null;
  changed_at?: string | null;
  /** True when the message that asked for this payee read like payment-diversion fraud (the screen's reasons follow). */
  change_request_flagged?: boolean;
  change_request_reasons?: string[];
};

/** One carrier: its verified payee, if any, and the registrations still waiting. */
export type CarrierRow = {
  carrier_key: string;
  carrier_name?: string | null;
  current: CarrierPayee | null;
  pending: CarrierPayee[];
};

export async function fetchCarriers(): Promise<{ items: CarrierRow[] }> {
  if (DEMO_MODE) liveOnly("The carrier registry");
  return getJson<{ items: CarrierRow[] }>("carriers");
}

export async function registerCarrierPayee(request: {
  carrier_name: string;
  /** The message that asked for the change, if any. It is screened; only the reasons are kept, never the text. */
  request_text?: string;
  payee_email: string;
}): Promise<{ carrier_key: string; version: number; status: string }> {
  if (DEMO_MODE) liveOnly("Registering a carrier payee");
  return postJson("carriers/payee", request);
}

export async function verifyCarrierPayee(
  carrierKey: string,
  version: number,
  callbackNote: string,
): Promise<{ carrier_key: string; version: number; status: string }> {
  if (DEMO_MODE) liveOnly("Verifying a carrier payee");
  return postJson(
    `carriers/${encodeURIComponent(carrierKey)}/payee/${version}/verify`,
    { callback_note: callbackNote },
  );
}

/** The limits that cap what leaves in a day, in US dollars. Set by a governance admin. */
export type PaymentLimits = {
  max_payout_usd: number;
  carrier_daily_usd: number;
  tenant_daily_usd: number;
  carrier_daily_count: number;
};

/** The most the agent may pay by itself, as a person published it. */
export type AgentLimit = {
  limit_usd: string;
  published_by: string;
  reason?: string | null;
  at?: string | null;
};

/** GET payments/controls: the pause switch, the limits in force and what was paid today. */
export type PaymentControls = {
  pause: { paused: boolean; reason?: string | null; by?: string | null; at?: string | null };
  limits: PaymentLimits | null;
  limits_are_defaults: boolean;
  agent_limit?: AgentLimit | null;
  usage_today: {
    tenant_usd: string;
    carriers: Record<string, { usd: string; count: number }>;
  };
};

export async function fetchPaymentControls(): Promise<PaymentControls> {
  if (DEMO_MODE) liveOnly("The payment controls");
  return getJson<PaymentControls>("payments/controls");
}

export async function pausePayments(reason: string): Promise<PaymentControls["pause"]> {
  if (DEMO_MODE) liveOnly("Pausing payments");
  return postJson("payments/pause", { reason });
}

export async function resumePayments(reason: string): Promise<PaymentControls["pause"]> {
  if (DEMO_MODE) liveOnly("Resuming payments");
  return postJson("payments/resume", { reason });
}

export async function publishPaymentLimits(
  limits: PaymentLimits,
  reason: string,
): Promise<{ limits: PaymentLimits }> {
  if (DEMO_MODE) liveOnly("Publishing payment limits");
  return postJson("payments/limits", { limits, reason });
}

/** Zero turns the agent off. */
export async function publishAgentLimit(
  limitUsd: number,
  reason: string,
): Promise<{ agent_limit: AgentLimit | null }> {
  if (DEMO_MODE) liveOnly("Publishing the agent limit");
  return postJson("payments/agent/limit", { limit_usd: limitUsd, reason });
}

/** Submit a shipment event, as Pub/Sub would. */
export async function submitShipmentEvent(
  shipment: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Submitting a shipment event");
  // The shipment runs the whole workflow before it answers, 30 to 60 seconds with the models. With the 20 second default the
  // booking page reported the API unreachable while the case was being created.
  return postJson("events/shipment", shipment, LONG_WRITE_TIMEOUT_MS);
}

/**
 * What a document upload answers with.
 *
 * Three outcomes, and only one of them is an error:
 *  - accepted, not blocked: a case was created and has run (202).
 *  - accepted and blocked: Model Armor or the injection screen stopped it before
 *    any model read it. Still a case -- a refusal is recorded, not discarded (202).
 *  - not accepted: the document could not be transcribed, and nothing was
 *    created (422). An answer about the file, not a failure of the service.
 */
export interface UploadResult {
  accepted: boolean;
  blocked?: boolean;
  case_id?: string;
  state?: string;
  error?: string | null;
  reason?: string;
  [key: string]: unknown;
}

/**
 * Upload a shipping document.
 *
 * multipart/form-data, streamed through the proxy rather than buffered, and with
 * the long write timeout: in on-demand mode the route runs Model Armor,
 * extraction and the whole workflow before it answers.
 *
 * A 422 is returned, not thrown. It means "this file could not be read", and
 * throwing it rendered the generic red error card over what is an ordinary
 * answer about the document.
 */
export async function uploadDocument(file: File): Promise<UploadResult> {
  if (DEMO_MODE) liveOnly("Document upload");
  const form = new FormData();
  form.append("file", file);
  // No content-type header: the browser must set it, because it has to append
  // the multipart boundary. Setting it by hand produces a body the server
  // cannot parse, and the failure looks like a corrupt file.
  const { text, status, ok } = await request(
    "events/document",
    { method: "POST", body: form },
    LONG_WRITE_TIMEOUT_MS,
  );
  if (status === 422) {
    try {
      const refused = JSON.parse(text) as UploadResult;
      return { ...refused, accepted: false };
    } catch {
      /* fall through to the ordinary error */
    }
  }
  return parseOrThrow<UploadResult>(text, status, ok);
}

/**
 * Clear the board: every case and event on this tenant.
 *
 * The audit trail is kept -- it is append-only -- and the reset is written to it
 * under the caller's name. Needs the operator role AND a password sign-in; a
 * one-click judge session is refused with `requiredAuth === "password"`.
 */
export async function resetBoard(): Promise<{ cleared: number }> {
  if (DEMO_MODE) liveOnly("Clearing the board");
  return postJson<{ cleared: number }>("orchestrator/reset");
}

// --------------------------------------------------------------------------
// Agent console
// --------------------------------------------------------------------------

/**
 * One entry in `/config/model`'s `available_models`.
 *
 * `input` and `output` are USD per MILLION tokens, which is how Nebius publishes them.
 * Typed as optional because this is an internal dashboard route -- see the note at the
 * top of the types file: "those routes return whatever the store holds".
 */
export interface ModelOption {
  id: string;
  name?: string;
  description?: string;
  input?: number;
  output?: number;
}

export interface ModelConfig {
  available_models?: ModelOption[];
  current_model?: string;
  pricing?: Omit<ModelOption, "id">;
}

export async function fetchModelConfig(): Promise<ModelConfig> {
  if (DEMO_MODE) liveOnly("Model configuration");
  return getJson<ModelConfig>("config/model");
}

export async function fetchAttackLog(): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("The attack log");
  return getJson<Record<string, unknown>>("security/attacks");
}

/** Run one text through Model Armor by hand. */
export async function screenText(
  text: string,
): Promise<Record<string, unknown>> {
  if (DEMO_MODE) liveOnly("Manual injection screening");
  return postJson("security/screen", { text });
}

// --------------------------------------------------------------------------
// Evaluation
// --------------------------------------------------------------------------

export interface Detection {
  precision: number;
  recall: number;
  f1: number;
  false_positive_rate: number;
  tp: number;
  fp: number;
  fn: number;
  tn: number;
}

/** A 95% Wilson score interval and the counts it was built from (evaluation.intervals). */
export interface Interval {
  low: number;
  high: number;
  successes: number;
  n: number;
}

/** Null where a report has no counts for that rate, or nothing in its denominator. */
export interface Intervals {
  precision: Interval | null;
  recall: Interval | null;
  false_positive_rate: Interval | null;
}

/** One committed report from data/benchmark_results/ (evaluation.py). */
export interface PipelineResult {
  id: string;
  arm: "rules" | "hs" | "full";
  split: "dev" | "holdout" | "all";
  split_method?: string;
  generated_at?: string;
  cases_run: number;
  verifier: string;
  detection: Detection;
  /** For `detection`'s precision, recall and false-positive rate. */
  intervals?: Intervals;
  rules_only_baseline?: Detection;
  by_attack_type?: Record<string, { cases: number; detected: number; detection_rate: number }>;
  false_positives?: {
    all_clean_cases: number;
    flagged: number;
    rate: number;
    hard_negatives?: { cases: number; flagged: number; rate: number };
    easy_negatives?: { cases: number; flagged: number; rate: number };
  };
  cost?: { total_usd: number; projected_usd_per_1000_cases: number };
  zero_day?: { real_searches?: number; stubbed_searches?: number };
  notes?: string[];
  /** Reviewer load per 1,000 shipments at real-world fraud rates (evaluation.workload). */
  workload?: Workload[];
  /** Attack against its honest counterpart, on the paired public-case corpus only. */
  pairs?: CasePair[] | null;
}

export interface Workload {
  prevalence: number;
  held_per_1000: number;
  true_per_1000: number;
  missed_per_1000: number;
  precision_at_prevalence: number;
  held_per_true_case: number | null;
  /** Recall at the top of its interval and false alarms at the bottom; null without counts. */
  best_case?: WorkloadEnd | null;
  /** Recall at the bottom of its interval and false alarms at the top. */
  worst_case?: WorkloadEnd | null;
}

/**
 * The workload redone at one end of the recall and false-alarm intervals. No
 * held_per_1000: it rises with both rates, so these ends do not bound it.
 */
export interface WorkloadEnd {
  recall: number;
  false_positive_rate: number;
  precision_at_prevalence: number;
  held_per_true_case: number | null;
}

export interface CasePair {
  ref: string;
  typology: string;
  title?: string | null;
  source_url?: string | null;
  identical_inputs: boolean;
  attack_flagged: boolean;
  honest_flagged: boolean;
  separated: boolean;
  model_variance: boolean;
  attack_codes: string[];
  honest_codes: string[];
  attack_floor: number;
  honest_floor: number;
}

/** One HS classifier arm from data/eval_results/. */
export interface HsResult {
  arm: string;
  model: string | null;
  n: number;
  precision: number;
  recall: number;
  f1: number;
  false_positive_rate: number;
  redirect_accuracy: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  measured_at?: string | null;
  intervals?: Intervals;
}

/** One review threshold on the rules arm's curve (scripts/operating_curve.py). */
export interface CurvePoint {
  /** Held when the risk floor is at or above this. */
  threshold: number;
  /** Every threshold that holds the same shipments, inclusive. */
  thresholds: [number, number];
  /** The review threshold in use falls in this point's range. */
  current: boolean;
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  precision: number | null;
  recall: number | null;
  false_positive_rate: number | null;
  /** At the curve's prevalence. */
  held_per_1000: number;
  held_per_true_case: number | null;
  intervals?: Intervals;
  best_case?: WorkloadEnd | null;
  worst_case?: WorkloadEnd | null;
}

export interface OperatingCurve {
  arm: string;
  generated_at?: string;
  review_threshold: number;
  prevalence: number;
  splits: Record<string, { cases_run: number; attacks: number; clean: number; points: CurvePoint[] }>;
}

/** A count with its rate and 95% Wilson interval, as the comparison scripts write them. */
export interface CountedRate {
  count: number;
  of: number;
  rate: number | null;
  ci95: [number, number] | null;
}

export interface FraudFloorArm {
  cases: number;
  unusable_replies: number;
  recall: CountedRate;
  false_alarm_rate: CountedRate;
  /** A reply that failed to parse counts as a hold, as the orchestrator treats it. */
  recall_as_held: CountedRate;
  false_alarm_as_held: CountedRate;
  failed_reply_rate: CountedRate;
  cost_per_case_usd: number | null;
  median_seconds: number | null;
}

export interface FraudFloorZero {
  kind: "fraud_floor_zero";
  question: string;
  partial?: boolean;
  total_cost_usd: number;
  notes: string[];
  results: Record<string, { model: string; holdout: FraudFloorArm; dev: FraudFloorArm; all: FraudFloorArm }>;
}

export interface DebateArm {
  model: string;
  debates: number;
  reached_a_verdict: CountedRate;
  disagree_on_fraud: CountedRate;
  disagree_on_honest: CountedRate;
  cost_per_debate_usd: number | null;
  median_seconds: number | null;
  mean_input_tokens: number | null;
}

export interface DebateCorpus {
  kind: "debate_corpus";
  question: string;
  partial?: boolean;
  debated: { fraud: number; honest: number };
  total_cost_usd: number;
  notes: string[];
  results: Record<string, DebateArm>;
}

export interface EvaluationSummary {
  pipeline: PipelineResult[];
  hs_classifier: { pairs: HsResult[]; holdout: HsResult[] };
  /** Threshold sweeps by arm, from data/operating_curve/. Absent from an older backend. */
  operating_curve?: Record<string, OperatingCurve>;
  /** Model choices tested head to head, by report name. Absent from an older backend. */
  model_comparison?: { fraud_floor0?: FraudFloorZero; debate_corpus?: DebateCorpus };
  caveats: string[];
}

/** The committed measurements, served by the API that produced them. */
export async function fetchEvaluation(): Promise<EvaluationSummary> {
  if (DEMO_MODE) liveOnly("The evaluation results");
  return getJson<EvaluationSummary>("evaluation");
}

// --------------------------------------------------------------------------
// Operator screens
// --------------------------------------------------------------------------

/** Every case waiting on someone, grouped on the server (worklist.py). */
export async function fetchWorklist(): Promise<Worklist> {
  if (DEMO_MODE) liveOnly("Today's worklist");
  return getJson<Worklist>("worklist");
}

/** Resolved cases and what they took, over cases created in [since, until). */
export async function fetchOutcomes(
  window: { since?: string | null; until?: string | null } = {},
): Promise<Outcomes> {
  if (DEMO_MODE) liveOnly("Outcomes");
  const params = new URLSearchParams();
  if (window.since) params.set("since", window.since);
  if (window.until) params.set("until", window.until);
  const query = params.toString();
  return getJson<Outcomes>(query ? `outcomes?${query}` : "outcomes");
}

/**
 * Report time the signed-in reviewer spent with a case in view.
 *
 * Fire-and-forget by design: a lost report under-counts handling time, which is
 * the safe direction for a productivity figure, and a failure here must never get
 * in the way of the review itself.
 */
export async function reportCaseActivity(caseId: string, activeMs: number): Promise<void> {
  if (DEMO_MODE) return;
  await postJson<unknown>(`cases/${encodeURIComponent(caseId)}/activity`, {
    active_ms: Math.round(activeMs),
  });
}

// --------------------------------------------------------------------------
// Query keys
//
// Every key carries the tenant id, even though the tenant is never sent. That
// is what makes a tenant switch discard the previous tenant's cache rather than
// relabel it, which is the one cross-tenant leak a client-side console can
// create without the server's help.
// --------------------------------------------------------------------------

/** One PayPal call the settler made. Ids and statuses only, never a payee address. */
export type PaymentStep = {
  status: string | null;
  http_status: number | null;
  paypal_id: string | null;
  error: string | null;
};

/** The payment state of a case, read from what the settler recorded (payment_status.py). */
export type PaymentState = {
  case_id: string;
  payment_case: boolean;
  state:
    | "none"
    | "awaiting_decision"
    | "no_authorization"
    | "paid"
    | "voided"
    | "held"
    | "pending"
    | "failed"
    | "decision_not_sent"
    | "unknown";
  reason?: string | null;
  findings?: string[];
  decision_not_sent?: boolean;
  paid_by_agent?: { limit_usd: string; published_by: string } | null;
  handed_to_person?: boolean;
  payout_status?: { state: "confirmed" | "failed" | "pending" | "unconfirmed"; status: string | null; at: string | null };
  capture?: PaymentStep | null;
  payout?: PaymentStep | null;
  void?: PaymentStep | null;
  at?: string | null;
};

/** The newest settlement outcome of every case that has one, and a count per state. */
export type PaymentLedger = {
  items: Array<{
    case_id: string;
    state: PaymentState["state"];
    reason?: string | null;
    capture?: PaymentStep | null;
    payout?: PaymentStep | null;
    void?: PaymentStep | null;
    payout_status?: { state: "confirmed" | "failed" | "pending" | "unconfirmed"; status: string | null; at: string | null };
    at?: string | null;
  }>;
  counts: Record<string, number>;
};

export async function fetchPaymentLedger(): Promise<PaymentLedger> {
  if (DEMO_MODE) liveOnly("The payment ledger");
  return getJson<PaymentLedger>("payments/ledger");
}

/** What the payment checks and the AI layers say about one case (payment_status.payment_risk). */
export type PaymentRisk = {
  case_id: string;
  payment_case: boolean;
  findings?: Array<{ code: string; severity: string; floor: number; detail: string }>;
  analyst?: { verdict: string; confidence?: number; reasons?: string[]; suggested_tier?: string | null } | null;
  identity?: { status: string; reason?: string; urls?: string[]; payee_site?: string } | null;
  credit?: {
    tier: string;
    baseline: string;
    suggested: string | null;
    cap_usd: string | null;
    paid_count: number;
    frozen: boolean;
  };
};

export async function fetchPaymentRisk(caseId: string): Promise<PaymentRisk> {
  if (DEMO_MODE) liveOnly("The payment risk");
  return getJson<PaymentRisk>(`payments/cases/${encodeURIComponent(caseId)}/risk`);
}

export async function fetchPaymentState(caseId: string): Promise<PaymentState> {
  if (DEMO_MODE) liveOnly("The payment state");
  return getJson<PaymentState>(`payments/cases/${encodeURIComponent(caseId)}`);
}

/** One of the attacks the Defense page replays against the payment layers (defense_scenarios.py). */
export type DefenseScenario = {
  id: string;
  title: string;
  attack: string;
  layer: string;
  live: boolean;
  outcome: string;
  findings: string[];
  floor: number | null;
  money_moved_usd: string;
  proof: string;
  detail: string;
};

export async function fetchDefenseScenarios(): Promise<{
  scenarios: DefenseScenario[];
  stopped: number;
  of: number;
  note: string;
}> {
  if (DEMO_MODE) liveOnly("The attack replay");
  return getJson("defense/scenarios");
}

type Rate = { count: number; of: number; rate: number | null; low: number | null; high: number | null };
type Arm = { recall: Rate; false_positive_rate: Rate };

/** What the payment checks catch on the synthetic corpus (payment_eval.py); the analyst arm is null until it is measured. */
export type DefenseEvaluation = {
  synthetic: boolean;
  flag_floor: number;
  note: string;
  summary: {
    cases: number;
    fraud: number;
    legit: number;
    arms: { floor: Arm | null; identity: Arm | null; analyst: Arm | null; site?: Arm | null };
  };
};

export async function fetchDefenseEvaluation(): Promise<DefenseEvaluation> {
  if (DEMO_MODE) liveOnly("The evaluation table");
  return getJson<DefenseEvaluation>("defense/evaluation");
}

/** A carrier or shipper that nothing is paid to until a governance admin lifts the freeze (payment_freeze.py). */
export type FrozenParty = {
  kind: string;
  party: string;
  name?: string | null;
  reason: string;
  source?: string | null;
  at?: string | null;
};

export async function fetchFrozenParties(): Promise<{ frozen: FrozenParty[] }> {
  if (DEMO_MODE) liveOnly("The frozen parties");
  return getJson<{ frozen: FrozenParty[] }>("payments/freezes");
}

export type UnboundOrder = { order_id: string; authorization_id: string; at: string | null };

export async function fetchUnboundOrders(): Promise<{ orders: UnboundOrder[] }> {
  if (DEMO_MODE) liveOnly("The holds with no case");
  return getJson<{ orders: UnboundOrder[] }>("payments/orders/unbound");
}

export async function fetchPendingPayPalEvents(): Promise<{ pending: number }> {
  if (DEMO_MODE) liveOnly("The waiting PayPal events");
  return getJson<{ pending: number }>("payments/events/pending");
}

export async function reconcilePayPalEvents(): Promise<{ applied: number; pending: number }> {
  if (DEMO_MODE) liveOnly("Applying the waiting PayPal events");
  return postJson("payments/events/reconcile", {});
}

export async function retrySettlement(caseId: string, reason: string): Promise<{ status: string; case_id: string }> {
  if (DEMO_MODE) liveOnly("Retrying a settlement");
  return postJson(`payments/cases/${encodeURIComponent(caseId)}/decision/redeliver`, { reason });
}

export async function refreshPayout(caseId: string): Promise<{ state: string; status: string | null; at: string | null }> {
  if (DEMO_MODE) liveOnly("Checking a payout with PayPal");
  return postJson(`payments/cases/${encodeURIComponent(caseId)}/payout/refresh`, {});
}

export async function liftFreeze(request: {
  kind: string;
  name: string;
  reason: string;
}): Promise<{ frozen: FrozenParty[] }> {
  if (DEMO_MODE) liveOnly("Lifting a freeze");
  return postJson("payments/freezes/lift", request);
}

export const queryKeys = {
  audits: (tenantId: string) => ["audits", tenantId] as const,
  usage: (tenantId: string) => ["usage", tenantId] as const,
  snapshot: (tenantId: string, drain: boolean) =>
    ["snapshot", tenantId, drain] as const,
  metrics: (tenantId: string) => ["metrics", tenantId] as const,
  cases: (tenantId: string, states?: string[]) =>
    ["cases", tenantId, states ?? []] as const,
  case: (tenantId: string, caseId: string) =>
    ["case", tenantId, caseId] as const,
  events: (tenantId: string) => ["events", tenantId] as const,
  reviewQueue: (tenantId: string) => ["review-queue", tenantId] as const,
  auditTrail: (
    tenantId: string,
    filter: { caseId?: string | null; action?: string | null },
  ) => ["audit-trail", tenantId, filter.caseId ?? "", filter.action ?? ""] as const,
  agent: (tenantId: string) => ["agent", tenantId] as const,
  drift: (tenantId: string) => ["drift", tenantId] as const,
  boundaries: (tenantId: string) => ["boundaries", tenantId] as const,
  prefilter: (tenantId: string) => ["prefilter", tenantId] as const,
  modelConfig: (tenantId: string) => ["model-config", tenantId] as const,
  attacks: (tenantId: string) => ["attacks", tenantId] as const,
  worklist: (tenantId: string) => ["worklist", tenantId] as const,
  outcomes: (tenantId: string, since: string | null) =>
    ["outcomes", tenantId, since ?? ""] as const,
};
