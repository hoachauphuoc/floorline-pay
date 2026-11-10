import type { Case, CaseState } from "./types";

/**
 * Board arithmetic, kept out of React.
 *
 * Every function here is pure and takes what it needs as an argument, so the
 * column folding and the SLA maths can be reasoned about (and tested) without
 * rendering anything. The old dashboard had the same logic inline in a template
 * string, which is how two of its bugs survived: nothing could look at
 * `casePriority` without also looking at 90 lines of HTML.
 */

export interface BoardColumn {
  /** The column's own state, which is also its key. */
  state: CaseState;
  label: string;
}

/** Six columns, in workflow order. */
export const BOARD_COLUMNS: BoardColumn[] = [
  { state: "INGESTED", label: "Queued" },
  { state: "SPECIALISTS_DONE", label: "Fraud + compliance" },
  { state: "INVESTIGATED", label: "Investigated" },
  { state: "AUTO_CLEARED", label: "Cleared" },
  { state: "PENDING_HUMAN", label: "Awaiting a person" },
  { state: "BLOCKED_BY_HUMAN", label: "Blocked" },
];

/**
 * Which column a raw backend state is drawn in.
 *
 * The folding mirrors the backend's own AWAITING_HUMAN tuple, deliberately, so
 * that the "Awaiting a person" column and the "Awaiting human" KPI tile count the
 * same thing. They did not in the ported version: the old dashboard folded
 * BLOCKED_BY_HUMAN into the awaiting-human column, so the column header read 4
 * while the tile above it read 3, and the extra card was a shipment a reviewer had
 * already blocked. A case that has been refused is disposed of, not queued -- and
 * two numbers with the same name disagreeing on one screen is how an operator
 * learns to trust neither.
 *
 * BLOCKED_BY_HUMAN gets the sixth column outright, taking it from ESCALATED.
 * That trade is on purpose: ESCALATED is genuinely awaiting a person, so it reads
 * correctly folded in alongside the rest (with its own red card accent), whereas a
 * blocked shipment is the one outcome a compliance console must never bury in a
 * column labelled something else.
 *
 * RELEASED_BY_HUMAN folds into Cleared because that column means "cleared to
 * ship", not "cleared without a human" -- which is why it is no longer labelled
 * "Auto-cleared".
 *
 * DEAD_LETTER folds into the awaiting column: a case the workflow gave up on is
 * work for a person. It is therefore the one state that can make the column
 * exceed the tile, and that is the honest direction for the discrepancy to run.
 */
const COLUMN_OF: Partial<Record<CaseState, CaseState>> = {
  HELD_FOR_REVIEW: "PENDING_HUMAN",
  ESCALATED: "PENDING_HUMAN",
  DEAD_LETTER: "PENDING_HUMAN",
  RELEASED_BY_HUMAN: "AUTO_CLEARED",
};

export function columnFor(state: CaseState): CaseState {
  return COLUMN_OF[state] ?? state;
}

/**
 * The states a reviewer can decide: MUST MATCH orchestrator.AWAITING_HUMAN, which
 * is what the decision route checks. DEAD_LETTER sits in the awaiting column but
 * is not here -- the route refuses to decide it, so offering to would be a lie.
 */
export const AWAITING_STATES: ReadonlySet<string> = new Set([
  "PENDING_HUMAN",
  "HELD_FOR_REVIEW",
  "ESCALATED",
]);

/**
 * The raw states that fold into each column.
 *
 * Derived from COLUMN_OF rather than written out, so the two cannot disagree --
 * a column header counting only its own literal state name would under-report
 * the awaiting-human column by every held, blocked and dead-lettered case.
 */
export const STATES_IN_COLUMN: Record<string, CaseState[]> = (() => {
  const map: Record<string, CaseState[]> = {};
  for (const { state } of BOARD_COLUMNS) map[state] = [state];
  for (const [raw, col] of Object.entries(COLUMN_OF)) {
    (map[col] ??= []).push(raw as CaseState);
  }
  return map;
})();

/**
 * The column's total across the whole collection, not just the fetched window.
 *
 * `counts` comes from the aggregation in global_metrics(), so this is exact at
 * any case volume. The board itself only ever holds one page, which is why the
 * header reads "8/23" rather than claiming the page is the total.
 */
export function columnTotal(
  counts: Partial<Record<CaseState, number>>,
  column: CaseState,
): number {
  return (STATES_IN_COLUMN[column] ?? [column]).reduce(
    (sum, s) => sum + (counts[s] ?? 0),
    0,
  );
}

export const TERMINAL_STATES: readonly CaseState[] = [
  "AUTO_CLEARED",
  "HELD_FOR_REVIEW",
  "ESCALATED",
  "PENDING_HUMAN",
  "RELEASED_BY_HUMAN",
  "BLOCKED_BY_HUMAN",
  "DEAD_LETTER",
];

export function isTerminal(state: CaseState): boolean {
  return TERMINAL_STATES.includes(state);
}

// --------------------------------------------------------------------------
// Priority and SLA
// --------------------------------------------------------------------------

export type Priority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export const PRIORITY_RANK: Record<Priority, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
};

/** Hours a case of each priority may sit before it is late. */
export const SLA_HOURS: Record<Priority, number> = {
  CRITICAL: 2,
  HIGH: 8,
  MEDIUM: 24,
  LOW: 72,
};

/**
 * Operational priority, derived rather than stored.
 *
 * Note that an ungraded case (`risk_score == null`) is MEDIUM, not LOW. A case
 * the agents have not scored yet is unknown, and treating unknown as low is the
 * same error as rendering an unverifiable check as clear -- it sorts the case to
 * the bottom of a queue on the strength of information nobody has.
 */
export function casePriority(c: Case): Priority {
  const risk = c.risk_score;
  const status = (c.compliance_status ?? "").toUpperCase();

  if ((risk != null && risk >= 90) || status === "BLOCKED") return "CRITICAL";
  if (
    (risk != null && risk >= 70) ||
    status === "REVIEW_REQUIRED" ||
    c.state === "ESCALATED"
  ) {
    return "HIGH";
  }
  if (risk == null) return "MEDIUM";
  if (risk >= 40) return "MEDIUM";
  return "LOW";
}

export function slaDeadline(c: Case): Date | null {
  if (!c.created_at) return null;
  const created = new Date(c.created_at);
  if (Number.isNaN(created.getTime())) return null;
  return new Date(created.getTime() + SLA_HOURS[casePriority(c)] * 3_600_000);
}

export type SlaTone = "done" | "overdue" | "urgent" | "ok" | "none";

export interface SlaStatus {
  text: string;
  tone: SlaTone;
}

export function slaStatus(c: Case, now: number = Date.now()): SlaStatus {
  if (isTerminal(c.state)) return { text: "Resolved", tone: "done" };
  const deadline = slaDeadline(c);
  if (!deadline) return { text: "", tone: "none" };

  const diff = deadline.getTime() - now;
  if (diff < 0) return { text: `Overdue ${formatDuration(-diff)}`, tone: "overdue" };
  if (diff < 3_600_000) return { text: `${formatDuration(diff)} left`, tone: "urgent" };
  return { text: `${formatDuration(diff)} left`, tone: "ok" };
}

export function formatDuration(ms: number): string {
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

// --------------------------------------------------------------------------
// Search, filter, sort
// --------------------------------------------------------------------------

/**
 * Free-text match across the fields an operator would type.
 *
 * Reads the nested `shipment` defensively: `/cases` returns the eight
 * `slim_case()` keys and no shipment at all, while `/orchestrator/state` returns
 * whole documents. Searching by company name therefore works on the board and
 * finds nothing on a paged case list -- which is the backend's shape, not
 * something to paper over by pretending the field is there.
 */
export function matchesSearch(c: Case, text: string): boolean {
  const query = text.trim().toLowerCase();
  if (!query) return true;

  const s = c.shipment ?? {};
  const haystack = [
    c.case_id,
    c.shipment_id,
    c.state,
    c.compliance_status,
    s.shipper_company,
    s.shipper_name,
    s.consignee_name,
    s.origin,
    s.destination,
    s.hs_code,
  ];

  return haystack.some(
    (field) => typeof field === "string" && field.toLowerCase().includes(query),
  );
}

export type PriorityFilter = "" | "CRITICAL" | "HIGH" | "MEDIUM";

/**
 * Priority filter, as a floor rather than an exact match.
 *
 * "HIGH" means high *and above*, which is what an operator triaging a backlog
 * means by it. An exact match would hide the CRITICAL cases from someone who
 * asked to see the urgent ones.
 */
export function matchesPriority(c: Case, filter: PriorityFilter): boolean {
  if (!filter) return true;
  const p = casePriority(c);
  if (filter === "CRITICAL") return p === "CRITICAL";
  if (filter === "HIGH") return p === "CRITICAL" || p === "HIGH";
  return p !== "LOW";
}

export type SortKey =
  | "newest"
  | "oldest"
  | "risk-high"
  | "risk-low"
  | "priority"
  | "sla";

export const SORT_LABELS: Record<SortKey, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  "risk-high": "Highest risk",
  "risk-low": "Lowest risk",
  priority: "Priority",
  sla: "SLA deadline",
};

function timestamp(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
}

export function sortCases(cases: Case[], key: SortKey): Case[] {
  const compare: Record<SortKey, (a: Case, b: Case) => number> = {
    newest: (a, b) => timestamp(b.created_at) - timestamp(a.created_at),
    oldest: (a, b) => timestamp(a.created_at) - timestamp(b.created_at),
    // Unscored last in both directions, rather than sorting as 0. `?? 0` put
    // every ungraded case at the top of "lowest risk", which reads as "these are
    // the safest" about cases nothing has looked at yet.
    "risk-high": (a, b) => riskOrder(b.risk_score) - riskOrder(a.risk_score),
    "risk-low": (a, b) => riskOrder(a.risk_score) - riskOrder(b.risk_score),
    priority: (a, b) => PRIORITY_RANK[casePriority(a)] - PRIORITY_RANK[casePriority(b)],
    sla: (a, b) =>
      (slaDeadline(a)?.getTime() ?? Number.MAX_SAFE_INTEGER) -
      (slaDeadline(b)?.getTime() ?? Number.MAX_SAFE_INTEGER),
  };
  return [...cases].sort(compare[key] ?? compare.newest);
}

/** Sorts an unscored case to the end regardless of direction. */
function riskOrder(risk: number | null | undefined): number {
  return risk ?? -1;
}

// --------------------------------------------------------------------------
// Bucketing
// --------------------------------------------------------------------------

export const COLUMN_PAGE_SIZE = 8;

export interface BoardBucket {
  column: BoardColumn;
  /** Cases in this column after search, filter and sort. */
  cases: Case[];
  /** Exact total for the column across the whole collection. */
  total: number;
}

export function bucketCases(
  cases: Case[],
  counts: Partial<Record<CaseState, number>>,
  { search, priority, sort }: {
    search: string;
    priority: PriorityFilter;
    sort: SortKey;
  },
): { buckets: BoardBucket[]; matched: number } {
  const filtered = cases.filter(
    (c) => matchesSearch(c, search) && matchesPriority(c, priority),
  );

  const grouped = new Map<CaseState, Case[]>();
  for (const { state } of BOARD_COLUMNS) grouped.set(state, []);
  for (const c of filtered) {
    const col = columnFor(c.state);
    // A case in a state no column claims is dropped rather than forced into
    // one. That cannot happen with the current state set, and if a new state is
    // added the count in the header will disagree with the cards below it --
    // which is a visible discrepancy rather than a silent miscategorisation.
    grouped.get(col)?.push(c);
  }

  return {
    buckets: BOARD_COLUMNS.map((column) => ({
      column,
      cases: sortCases(grouped.get(column.state) ?? [], sort),
      total: columnTotal(counts, column.state),
    })),
    matched: filtered.length,
  };
}

/**
 * Whether the pipeline has work in flight, which sets the poll interval.
 *
 * Read from the exact aggregate rather than by scanning the fetched window: a
 * case in flight beyond the first page would otherwise make the board poll
 * slowly precisely when it should poll fast.
 */
export function hasWorkInFlight(inFlight: number | undefined): boolean {
  return (inFlight ?? 0) > 0;
}

export const POLL_BUSY_MS = 250;
export const POLL_IDLE_MS = 2500;

// On because the project is billed: each refresh reads the whole board, so without billing one open tab used up the free daily quota.
// Set it to false again if billing is removed; the board then loads on a page load only.
export const BOARD_AUTO_REFRESH = true;

export function boardRefetchMs(inFlight: number | undefined, autoRefresh: boolean = BOARD_AUTO_REFRESH): number | false {
  if (!autoRefresh) return false;
  return hasWorkInFlight(inFlight) ? POLL_BUSY_MS : POLL_IDLE_MS;
}
