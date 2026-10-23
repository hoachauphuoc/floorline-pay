/**
 * What the console's BFF (`app/api/proxy/[...path]/route.ts`) forwards to Flask.
 *
 * The single source of truth for the allow-lists. The proxy enforces them, and
 * the Access Control screen reports them (`app/api/auth/posture/route.ts`); both
 * import from here, so the screen cannot show a list the proxy no longer uses.
 *
 * An allow-list per method, not a pass-through. A catch-all forwarding any path
 * would hand the browser every route on the Flask app with the console's key
 * attached for a signed-in user, and several of those are destructive or
 * expensive. Each entry below is a route a console screen actually needs; a route
 * absent from these sets is a 404 at the proxy even though it exists upstream.
 * (The public API at /api/v1/* is a different path: it carries the caller's own
 * credential and never the console's -- see lib/upstream.ts.)
 *
 * What is deliberately NOT proxied, and why:
 *
 * - `orchestrator/drain` and `orchestrator/tick` advance the pipeline. The
 *   Pipeline screen already advances it as a side effect of polling
 *   `orchestrator/state?drain=1`, and a second explicit advancer racing the
 *   first is how the optimistic-lock conflicts in the last round were produced.
 * - `admin/backfill-rollups` is a one-off migration that rewrites every case in
 *   the tenant. An operator runs it deliberately, once.
 * - `internal/execute` is the cross-identity executor hop, not a browser route.
 * - `config/model` (POST) changes which model every subsequent audit runs on, for
 *   the whole service rather than per tenant.
 * - `demo` spends Nebius tokens and Tavily credits on a GET.
 *
 * `orchestrator/reset` used to head that list. It is proxied now, for the
 * DevOps screen's Clear board, and only ever for a PASSWORD sign-in: the BFF
 * refuses it from any other session (PASSWORD_ONLY_POST below) and Flask refuses
 * it again (auth.require_password_session). It clears the tenant's cases and
 * events; the audit trail is append-only and survives, and the reset is itself
 * written to it.
 */

/** Reads. Cheap, idempotent, and safe to poll. */
export const ALLOWED_GET = new Set([
  // Board and pipeline
  "orchestrator/state",
  "cases",
  "events",
  "metrics/summary",
  // Review
  "review/queue",
  // Operator screens: Today and Outcomes (worklist.py)
  "worklist",
  "outcomes",
  // Audit trail
  "audit",
  // Governance
  "governance/agent",
  "governance/drift",
  "governance/boundaries",
  "governance/prefilter-rules",
  // Agent console
  "config/model",
  "security/attacks",
  // B2B contract surface the original console was built on
  "compliance/reports",
  "billing/usage",
  // Identity and access: who the backend thinks the caller is, and the policy it
  // enforces. Every role-dependent control on every screen is drawn from these.
  "auth/whoami",
  "auth/policy",
  // The committed benchmark and HS-classifier reports (evaluation.py), for the
  // Evaluation panel. Aggregates only.
  "evaluation",
  // What the settler did with the money on each case (payment_status.py).
  "payments/ledger",
  // The carrier payee registry (carrier_registry.py).
  "carriers",
  // Payment controls: the pause switch, the limits in force, what was paid today.
  "payments/controls",
]);

/**
 * Reads that carry an id in the path.
 *
 * Kept separate from the exact set so the id segment can be shape-checked. The
 * value is the trailing segment, or "" when the id is last.
 */
export const ALLOWED_GET_BY_ID: ReadonlyArray<readonly [string, string]> = [
  ["review", "document"], // the scanned bill of lading, for the review iframe
  ["orchestrator/case", ""], // full case trace, for the Case screen
  ["orchestrator/case", "dossier"], // the case as a due-diligence PDF (dossier.py)
  ["compliance/audit", ""], // one B2B audit record
  ["payments/cases", ""], // the payment state of one case (payment_status.py)
];

/**
 * Writes. Each one is an action a named human takes.
 *
 * The two review routes carry a case id between segments, as in
 * `review/CASE-123/decide`, so they are matched by pattern rather than by exact
 * string -- see matchWrite.
 */
export const ALLOWED_POST = new Set([
  // Review decisions
  "review/decide",
  "review/deep-review",
  // Verification loop: request, evaluate, approve, execute, cancel, re-link.
  "review/second-approve",
  "review/approve-exception",
  "review/request-evidence",
  "review/reevaluate",
  "review/approve",
  "review/execute",
  // Verification loop: request evidence, sub-actions on a request.
  "review/request-evidence",
  "review/reevaluate",
  "review/approve",
  "review/execute",
  "review/evidence/link",
  "review/evidence/cancel",
  // `cases/<id>/activity`; reviewer upstream.
  "cases/activity",
  // Governance
  "governance/publish",
  "governance/revoke",
  "governance/simulate",
  "governance/verify-entity",
  // Tavily sweep for sanctions and enforcement news, from the Governance screen.
  // Governance admin upstream and rate-limited to 5/min there.
  "governance/tavily-scan",
  // DevOps
  "simulate",
  "simulate/bulk",
  "events/shipment",
  "events/document",
  // Agent console: the manual Model Armor / injection probe. Named `screen`
  // upstream; there is no `security/redteam` route despite the old dashboard's
  // label for the panel.
  "security/screen",
  // DevOps: Clear board. Password sign-ins only -- see PASSWORD_ONLY_POST.
  "orchestrator/reset",
  // Evidence respond: token-authenticated by Flask, no console session needed.
  "evidence/respond",
  // Booking: create the PayPal order a shipper approves, and authorize it on return.
  // Capturing and paying out are never browser routes: only the settler does those.
  "payments/orders",
  "payments/orders/authorize",
  // Payment controls: any operator may pause payments; a governance admin resumes them and
  // publishes the limits.
  "payments/pause",
  "payments/resume",
  "payments/limits",
  // Carrier payee registry: register a payee, and verify one as a different person.
  "carriers/payee",
  "carriers/payee/verify",
]);

/**
 * Writes a session must have signed in with a PASSWORD to use.
 *
 * The one-click judge button is public and its account is an admin, so for these
 * the role is not enough. Checked in the BFF so a refusal costs no upstream call
 * and reads well in the UI; Flask enforces the same rule independently.
 *
 * Each one changes the demo for every visitor after the person who pressed it:
 * Clear board empties it, revoking the boundary suspends the agent, publishing
 * replaces the shared boundary (and `{"allowed_actions": []}` suspends the agent
 * as surely as a revoke), and the prefilter rules decide what clears with no
 * model at all. Simulate writes nothing and stays open.
 */
export const PASSWORD_ONLY_POST = new Set([
  "orchestrator/reset",
  "governance/revoke",
  "governance/publish",
  // Payment controls: stopping and starting money, and its limits, need a password sign-in.
  "payments/pause",
  "payments/resume",
  "payments/limits",
  // Carrier payees: a payee is moved by a person signed in with a password, never by a
  // one-click session.
  "carriers/payee",
  "carriers/payee/verify",
  // Verification loop: approve and execute change downstream state.
  "review/second-approve",
  "review/approve-exception",
  "review/approve",
  "review/execute",
]);

export const ALLOWED_PUT = new Set(["governance/prefilter-rules"]);

/** PUTs with the same password rule. Today that is every PUT the proxy allows. */
export const PASSWORD_ONLY_PUT = new Set(["governance/prefilter-rules"]);

/**
 * The id segment is checked against the case-id shape the backend issues, so a
 * path traversal cannot hide in it.
 */
export const CASE_ID = /^[A-Za-z0-9_-]{1,80}$/;

/** PayPal order ids: upper-case letters, digits and hyphens. */
export const PAYPAL_ID = /^[A-Z0-9-]{1,64}$/;

/** Carrier keys: lower-case letters and digits. */
export const CARRIER_KEY = /^[a-z0-9]{1,80}$/;

/**
 * Whether a path is an allowed write, resolving the two review routes that carry
 * a case id.
 *
 * Matched by shape rather than by substring: a `.includes("decide")` test would
 * also pass `review/../../orchestrator/reset/decide`.
 */
export function matchWrite(joined: string, allowed: Set<string>): boolean {
  const parts = joined.split("/");
  if (parts.length === 3 && parts[0] === "review" && CASE_ID.test(parts[1])) {
    const action = parts[2];
    if (["decide", "deep-review", "request-evidence", "reevaluate", "approve", "approve-exception", "second-approve", "execute"].includes(action)) {
      return allowed.has(`review/${action}`);
    }
    return false;
  }
  // carriers/<carrier key>/payee/<version>/verify
  if (parts.length === 5 && parts[0] === "carriers" && parts[2] === "payee" && parts[4] === "verify") {
    return CARRIER_KEY.test(parts[1]) && /^[0-9]{1,9}$/.test(parts[3]) && allowed.has("carriers/payee/verify");
  }
  // payments/orders/<order id>/authorize
  if (parts.length === 4 && parts[0] === "payments" && parts[1] === "orders" && parts[3] === "authorize") {
    return PAYPAL_ID.test(parts[2]) && allowed.has("payments/orders/authorize");
  }
  // review/<id>/evidence/<rid>/link and review/<id>/evidence/<rid>/cancel
  if (parts.length === 5 && parts[0] === "review" && CASE_ID.test(parts[1]) && parts[2] === "evidence" && CASE_ID.test(parts[3])) {
    const sub = parts[4];
    if (sub === "link" || sub === "cancel") return allowed.has(`review/evidence/${sub}`);
    return false;
  }
  if (parts.length === 3 && parts[0] === "cases" && CASE_ID.test(parts[1])) {
    return parts[2] === "activity" && allowed.has("cases/activity");
  }
  if (joined === "payments/orders/authorize") return false;
  if (joined === "carriers/payee/verify") return false;
  // These are templates for the id-carrying forms above, not routes of their own;
  // Flask has none of them without an id.
  if (joined === "review/decide" || joined === "review/deep-review" || joined === "cases/activity"
      || joined === "review/request-evidence" || joined === "review/reevaluate"
      || joined === "review/approve" || joined === "review/approve-exception" || joined === "review/execute"
      || joined === "review/second-approve"
      || joined === "review/evidence/link" || joined === "review/evidence/cancel") {
    return false;
  }
  return allowed.has(joined);
}

/**
 * The policy key for a path that matchWrite already accepted: the id-carrying forms
 * mapped to the template the policy sets are written in, everything else unchanged.
 *
 * ALLOWED_POST and PASSWORD_ONLY_POST name `review/approve`, but a request arrives as
 * `review/CASE-1/approve`. Looking the raw path up in PASSWORD_ONLY_POST never matched,
 * so the password gate for approve and execute was dead code and only Flask's own check
 * stood between a one-click session and a release (N07). Anything that asks "which
 * rule applies to this write" must go through here.
 */
export function policyKey(joined: string): string {
  const parts = joined.split("/");
  if (parts.length === 3 && parts[0] === "review" && CASE_ID.test(parts[1])) {
    return `review/${parts[2]}`;
  }
  if (parts.length === 5 && parts[0] === "review" && CASE_ID.test(parts[1])
      && parts[2] === "evidence" && CASE_ID.test(parts[3])) {
    return `review/evidence/${parts[4]}`;
  }
  if (parts.length === 3 && parts[0] === "cases" && CASE_ID.test(parts[1])) {
    return `cases/${parts[2]}`;
  }
  if (parts.length === 5 && parts[0] === "carriers" && parts[2] === "payee" && parts[4] === "verify"
      && CARRIER_KEY.test(parts[1])) {
    return "carriers/payee/verify";
  }
  return joined;
}

/**
 * Whether a path is an allowed read, including the id-carrying ones.
 *
 * A passing shape check is not an authorisation check and is not pretending to
 * be one: the backend scopes every lookup to the caller's tenant and role, and
 * answers a foreign id with the same 404 as a missing one.
 */
export function matchRead(joined: string): boolean {
  if (ALLOWED_GET.has(joined)) return true;

  for (const [prefix, suffix] of ALLOWED_GET_BY_ID) {
    const head = `${prefix}/`;
    if (!joined.startsWith(head)) continue;
    const rest = joined.slice(head.length).split("/");

    if (suffix === "") {
      if (rest.length === 1 && CASE_ID.test(rest[0])) return true;
    } else if (rest.length === 2 && CASE_ID.test(rest[0]) && rest[1] === suffix) {
      return true;
    }
  }
  return false;
}

/**
 * Query parameters forwarded on reads. Anything else is dropped.
 *
 * `state` is singular because that is what `/cases` reads; `states` is here only
 * so a future rename does not silently return every case. `status` is the third
 * mutually-exclusive audit filter alongside `case_id` and `action` -- omitting it
 * made the Audit Trail's status filter appear to work and return unfiltered rows.
 *
 * `since` and `until` bound a billing period on `billing/usage`. Safe to forward
 * even though the tenant is not: they narrow the caller's own window and cannot
 * widen its scope.
 */
export const FORWARDED_PARAMS = [
  "limit",
  "cursor",
  "state",
  "states",
  "status",
  "case_id",
  "action",
  "min_risk",
  "outcome",
  "drain",
  "prefix",
  "since",
  "until",
];

/** Human-readable read list for the Access Control screen, id routes included. */
export function describedReads(): string[] {
  return [
    ...ALLOWED_GET,
    ...ALLOWED_GET_BY_ID.map(([prefix, suffix]) =>
      suffix ? `${prefix}/<id>/${suffix}` : `${prefix}/<id>`,
    ),
  ].sort();
}

/** Human-readable write list for the Access Control screen. */
export function describedWrites(): Array<{ method: "POST" | "PUT"; path: string }> {
  const posts = [...ALLOWED_POST].map((path) => ({
    method: "POST" as const,
    path: path.startsWith("review/evidence/")
      ? path.replace("review/evidence/", "review/<id>/evidence/<id>/")
      : path.startsWith("review/")
        ? path.replace("review/", "review/<id>/")
        : path.startsWith("cases/")
          ? path.replace("cases/", "cases/<id>/")
          : path,
  }));
  const puts = [...ALLOWED_PUT].map((path) => ({ method: "PUT" as const, path }));
  return [...posts, ...puts].sort((a, b) => a.path.localeCompare(b.path));
}
