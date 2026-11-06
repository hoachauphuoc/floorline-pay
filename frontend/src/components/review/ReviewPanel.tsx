"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, FileText, Loader2, Lock, LogIn, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

import { HelpDot } from "@/components/help/HelpDot";
import { ErrorState } from "@/components/layout/States";
import { DebatePanel } from "@/components/review/DebatePanel";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toaster";
import { ApiError, approveCase, approveException, cancelEvidenceRequest, caseDossierUrl, decideReview, executeApproval, requestDeepReview, reevaluateCase, requestEvidence, reviewDocumentUrl, secondApprove } from "@/lib/api";
import { caseHref } from "@/lib/case-links";
import {
  actionLabel,
  caseStateLabel,
  complianceStatusLabel,
  humaniseCode,
  lowerFirst,
} from "@/lib/format";
import { helpFor } from "@/lib/help-content";
import { type Identity, lockReason, roleLabel, useIdentity } from "@/lib/identity";
import { CONTRADICTION_KIND_LABEL, executionClaim, isContradicted, latestEvaluation, evidenceToWaive } from "@/lib/operations";
import {
  AWAITING_HUMAN,
  HUMAN_ACTION_RESULT,
  REEVALUABLE_STATES,
  type Case,
  type CaseState,
  type HumanAction,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The decision form for one case, with its findings, the debate verdict and the
 * archived paperwork.
 *
 * Moved out of the Review Queue page unchanged so the case page (/cases/<id>)
 * records a decision through the same form -- the same role lock, the same
 * required-note rule, the same confirmation -- rather than a second copy that
 * could drift from it. A Next page module may export nothing but its page, which
 * is why it could not simply be imported from there.
 */

// A dropped or timed-out connection says nothing about what the server did: the decision
// may have been approved or executed before the answer was lost (N08). A refusal the
// server sent (kind "http") is an answer, so there is nothing to reload.
// A 2xx whose body could not be parsed ("parse") is the same: the server said yes and the
// answer was lost on the way.
function outcomeUnknown(error: unknown): boolean {
  return error instanceof ApiError && (error.kind === "unreachable" || error.kind === "parse");
}

const ACTION_LABEL: Record<HumanAction, string> = {
  release: "Release",
  block: "Block",
  request_info: "Request info",
};

/**
 * Back to the page the form is on after signing in. This panel is mounted by the
 * Review Queue and by a case's own page, so a fixed `next=/review` sent someone
 * who signed in from /cases/<id> to a different screen.
 */
function signInHref(): string {
  const here =
    typeof window === "undefined" ? "/review" : window.location.pathname + window.location.search;
  return `/login?next=${encodeURIComponent(here)}`;
}

/**
 * The address a shipper opens, from the path the API returns (`/respond/<token>`).
 * The path alone is not something a reviewer can paste into an email. Without a
 * window (server rendering) the path is the best there is.
 */
function absoluteLink(path: string): string {
  return typeof window === "undefined" ? path : new URL(path, window.location.origin).toString();
}

/**
 * The archived paperwork, or a specific reason there is none.
 *
 * Decided from `provenance` on the case rather than by probing the endpoint: the
 * case already carries `uri`, `generated` and, when archiving failed, `reason`.
 * Fetching the PDF just to discover it is a 404 would download it twice on the
 * happy path.
 *
 * The distinction matters. "No document" and "the document was rendered but
 * DOCUMENT_BUCKET is not configured" are different facts with different owners --
 * the second is an operator's deployment gap, and an empty frame invites a
 * reviewer to conclude the shipment arrived with no paperwork.
 */
function Paperwork({ case: c, identity }: { case: Case; identity: Identity | null | undefined }) {
  const provenance = (c.provenance ?? {}) as Record<string, unknown>;
  const uri = typeof provenance.uri === "string" ? provenance.uri : null;
  const generated = provenance.generated === true;
  const reason =
    typeof provenance.reason === "string" ? provenance.reason : null;
  const filename =
    typeof provenance.filename === "string" ? provenance.filename : null;
  // The archived original is reviewer-only upstream: it is a customer's bill of
  // lading, not a derived record. Without the role the <object> below would load a
  // 403 body into the frame -- so say what is there and who may open it instead.
  const locked = lockReason(identity, "reviewer");

  return (
    <div className="bento-card overflow-hidden p-0">
      <div className="flex items-center gap-1.5 px-4 pb-2 pt-4">
        <h3 className="text-[12px] font-medium text-white">Paperwork</h3>
        <HelpDot id="review.paperwork" />
        {/* Open to anyone who can read the case: it is the case, arranged for a
            compliance file, with no original document inside it. */}
        <a
          href={caseDossierUrl(c.case_id)}
          target="_blank"
          rel="noreferrer noopener"
          className="ml-auto inline-flex items-center gap-1 text-[11.5px] text-brand hover:underline"
        >
          <FileText className="size-3.5" aria-hidden />
          Due-diligence dossier (PDF)
        </a>
      </div>

      {uri && locked ? (
        <div className="flex items-start gap-2 border-t border-white/[0.06] px-4 py-5">
          <Lock className="mt-[2px] size-3.5 shrink-0 text-dim" aria-hidden />
          <div className="min-w-0">
            <p className="text-[12px] leading-relaxed text-dim">
              {generated
                ? "A rendered reconstruction of this case's bill of lading is archived"
                : `The original document${filename ? ` (${filename})` : ""} is archived`}
              , and opening it needs the Reviewer role. {locked}
            </p>
            {identity?.authenticated_by === "anonymous" && (
              <a
                href={signInHref()}
                className="mt-2.5 inline-flex h-8 items-center gap-1.5 rounded-md border border-white/15 px-3 text-[12px] text-white/90 transition-colors hover:border-white/30"
              >
                <LogIn className="size-3.5" aria-hidden />
                Sign in
              </a>
            )}
          </div>
        </div>
      ) : uri ? (
        <>
          <p className="px-4 pb-3 text-[11.5px] leading-relaxed text-dim">
            {generated ? (
              <>
                This case arrived as a structured event, so the document below is
                a{" "}
                <span className="text-risk-warn">rendered reconstruction</span>,
                not an original. It is flagged as generated so it is never read as
                scanned paperwork.
              </>
            ) : (
              <>The document this case was extracted from, as submitted.</>
            )}
          </p>
          {/*
            An <object> rather than an <iframe> with a PDF src: the proxy passes
            the upstream content type through, and an iframe pointed at a PDF is
            at the mercy of the browser's viewer settings. `key` forces a reload
            when the selection changes, which an iframe with a changed src does
            not reliably do.
          */}
          <object
            key={c.case_id}
            data={reviewDocumentUrl(c.case_id)}
            type="application/pdf"
            className="h-[28rem] w-full border-t border-white/[0.06] bg-black/40"
          >
            <p className="p-4 text-[12px] text-dim">
              Your browser would not render it inline.{" "}
              <a
                href={reviewDocumentUrl(c.case_id)}
                className="text-brand hover:underline"
                target="_blank"
                rel="noreferrer noopener"
              >
                Open it in a new tab
              </a>
              .
            </p>
          </object>
        </>
      ) : (
        <div className="border-t border-white/[0.06] px-4 py-5">
          <p className="text-[12px] leading-relaxed text-dim">
            {reason === "DOCUMENT_BUCKET not configured" ? (
              <>
                A bill of lading{filename ? ` (${filename})` : ""} was rendered
                for this case but could not be stored:{" "}
                <span className="font-mono text-risk-warn">
                  DOCUMENT_BUCKET is not configured
                </span>{" "}
                on this deployment. That is a deployment gap rather than a missing
                document — the shipment data itself is complete, and the findings
                above were computed from it.
              </>
            ) : reason ? (
              <>
                No archived document:{" "}
                <span className="font-mono text-white/80">{reason}</span>
              </>
            ) : (
              <>
                No archived document for this case. Every case is supposed to be
                reviewable against paperwork, so this is worth reporting rather
                than working around.
              </>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

export function ReviewPanel({
  summary,
  detail,
  detailLoading,
  detailError,
  onDecided,
}: {
  summary: Case;
  detail: Case | undefined;
  detailLoading: boolean;
  detailError: unknown;
  onDecided: () => void;
}) {
  const c = detail ?? summary;
  const [note, setNote] = useState("");
  const [pending, setPending] = useState<HumanAction | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDeep, setConfirmDeep] = useState(false);
  const [evidenceLinks, setEvidenceLinks] = useState<string[]>([]);

  // The clock the execution claim is read against. A claim's lease runs out while
  // this panel is open, and nothing else re-renders it, so while the case is
  // EXECUTING the clock is moved on every 15 seconds. Not ticking otherwise.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (c.state !== "EXECUTING") return;
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, [c.state]);

  // Who this decision will be recorded as, and whether they may record one.
  //
  // This used to be a text input the reviewer typed, and the typed value was what
  // went onto the audit trail. That is worse than recording nothing: it reads as
  // accountability while being unverifiable, and nothing stopped someone entering a
  // colleague's name. The server takes the address from the verified session and
  // ignores anything sent in the body, so this is display only.
  //
  // Read from the API's whoami rather than the console session, because the role
  // is the API's to assign: signing in does not make someone a reviewer.
  const identityQuery = useIdentity();
  const identity = identityQuery.data;
  const decideLock = lockReason(identity, "reviewer");
  const exceptionLock = lockReason(identity, "operator");
  const toast = useToast();

  const decide = useMutation({
    mutationFn: (action: HumanAction) =>
      decideReview({ caseId: c.case_id, action, note }),
    onSuccess: (result, action) => {
      // The backend answers {ok: false, error} with a 400 for a refused
      // decision, which is a normal outcome rather than a transport failure. So
      // it lands in onSuccess and has to be checked, not assumed.
      if (!result.ok) {
        setFormError(result.error ?? "The decision was refused.");
        return;
      }
      setFormError(null);
      setNote("");
      onDecided();
      // The case leaves the queue on success, which on its own looks the same as
      // the click doing nothing. Say what was recorded, and where to see it.
      toast({
        title: `${ACTION_LABEL[action]} recorded for ${c.shipment_id || c.case_id}`,
        description: identity?.acts_for_a_person
          ? `Written to the audit trail under ${identity.email}.`
          : "Written to the audit trail.",
        action: { label: "See it on the audit trail", href: `/audit?case=${encodeURIComponent(c.case_id)}` },
      });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const deep = useMutation({
    mutationFn: () => requestDeepReview(c.case_id),
    onSuccess: (result) => {
      if (!result.ok) {
        setFormError(result.error ?? "Deep review was refused.");
        return;
      }
      onDecided();
      toast({
        title: "Deep review finished",
        description: "The Senior Auditor's verdict is shown below the decision form.",
        action: { label: "Open the full trace", href: caseHref(c.case_id) },
      });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const rec = c.reconciliation;
  // Read from the server, not re-derived. This computed
  //   Math.abs(effective_risk - model_risk) >= 15
  // which cannot express the rule: `effective_risk` is max(model, floor), so on any
  // case where the model scored ABOVE the floor the difference is zero and a genuine
  // dispute is invisible, while `Math.abs` would also fire on the impossible reverse
  // direction. verifier.reconcile() sets `score_disputed` as (floor - model) >= 15 --
  // deliberately one-directional, because only the model under-scoring against the
  // floor is a dispute worth a debate.
  const disputed = rec?.score_disputed === true;

  // `auto_debate` first, then `debate`. The orchestrator writes two different keys for
  // two different paths -- `auto_debate` when the floor and the model disagree by 15
  // points or more and the debate fires with nobody asking, `debate` when a reviewer
  // clicks Deep Review. The card read only the second, so it mounted on no seeded case
  // at all: seeding never clicks the button, which means the ONE capability the
  // submission leads with was the one the console could not display. Found by opening
  // all fourteen queue cases and seeing the card on none, while the payload for
  // FULL-07-BLACKTAX carried a DISAGREE verdict the whole time.
  const debate = detail?.auto_debate ?? detail?.debate ?? null;
  const debateWasAutomatic = detail?.auto_debate != null;

  const findings = c.validation?.findings ?? [];
  const observations = c.validation?.observations ?? [];

  const hasVerification = c.verification != null;
  // A case enters the verification loop on its first request for documents. That is
  // possible once a finding names the papers that would answer it; before this the
  // first "Request info" went to the legacy decide and the loop could never start.
  const asksForDocuments = (c.validation?.findings ?? []).some(
    (f) => (f.measured?.documents_needed ?? []).length > 0,
  );
  const usesVerificationLoop = hasVerification || asksForDocuments;
  // Release / Block / Request info only while the case is actually waiting on a
  // decision; in the loop's other states the backend would refuse them.
  const awaitingDecision = AWAITING_HUMAN.includes(c.state as CaseState);

  // What the papers on file say. Re-evaluate writes this to the evaluation record, not
  // to the case's findings, so without reading it here a reviewer saw a count in the
  // timeline and an unchanged findings list, and the same Release button as on a clean case.
  const evaluation = latestEvaluation(c);
  const contradicted = isContradicted(evaluation);

  // Execute answers 409 when the case is no longer in the state this page shows: another
  // execution of the approval is still running, or it has finished (N08). The refusal is
  // still shown; the case is read again so the buttons match what the server holds.
  const executeReloadingOnConflict = async (approvalId: string) => {
    try {
      return await executeApproval(c.case_id, approvalId);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "http" && error.status === 409) onDecided();
      throw error;
    }
  };

  // N08: For cases in the verification loop, route decisions through the new API.
  const verificationApprove = useMutation({
    onSettled: (_data, error) => {
      if (outcomeUnknown(error)) onDecided();
    },
    mutationFn: async (action: "release" | "block") => {
      // A release over unresolved or contradicting evidence is an exception (N04): its own
      // operation, carrying the reason and every waived item. Anything else is a plain approval.
      const waived = action === "release" ? evidenceToWaive(evaluation) : [];
      const res = waived.length > 0
        ? await approveException(c.case_id, note, waived)
        : await approveCase(c.case_id, action, note);
      if ((res as Record<string, unknown>).approval) {
        // A release that needs a second approver is not executed here: it waits for one.
        if ((res.approval as Record<string, unknown>).requires_second === true) return res;
        const approval = res.approval as Record<string, string>;
        return executeReloadingOnConflict(approval.approval_id);
      }
      return res;
    },
    onSuccess: (result) => {
      const state = (result as Record<string, unknown>)?.state;
      if (state === "EXECUTION_FAILED") {
        setFormError("The decision was approved but execution failed. You can retry.");
        setPending(null);
        // Reload the case: it is now EXECUTION_FAILED, and the Retry button only
        // appears once the page has that state. The case page does not poll.
        onDecided();
        return;
      }
      setFormError(null);
      setNote("");
      onDecided();
      const approved = (result as Record<string, unknown>)?.approval as Record<string, unknown> | undefined;
      toast({
        title: approved?.requires_second === true
          ? "Approved. A second approver must approve it before it is executed"
          : "Decision executed via the verification loop",
      });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const verificationRequest = useMutation({
    mutationFn: () => requestEvidence(c.case_id),
    onSuccess: (data) => {
      setFormError(null);
      setNote("");
      // The link is returned once and kept nowhere a viewer can read, so show it
      // here for the reviewer to copy.
      const links = Array.isArray(data?.links)
        ? (data.links as Array<{ doc_type?: string; path?: string }>)
            .map((l) => (l.path ? `${l.doc_type ? l.doc_type + ": " : ""}${absoluteLink(l.path)}` : ""))
            .filter(Boolean)
        : typeof data?.path === "string"
          ? [absoluteLink(data.path)]
          : [];
      setEvidenceLinks(links);
      onDecided();
      toast({ title: "Evidence requested" });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const verificationReevaluate = useMutation({
    mutationFn: () => reevaluateCase(c.case_id),
    onSuccess: () => {
      setFormError(null);
      setEvidenceLinks([]);
      onDecided();
      toast({ title: "Evidence re-evaluated" });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const openRequest = (c.verification?.requests ?? []).find((r) =>
    ["OPEN", "SENT"].includes(String(r.status).toUpperCase()),
  );
  const validApproval = (c.verification?.approvals ?? []).find(
    (a) => a.status === "VALID",
  );
  // EXECUTING with a claim whose lease has run out is an interrupted execution; with
  // the lease still running (or no claim to read) it is somebody's execution in flight.
  const claim = executionClaim(c, now);
  const interrupted = claim?.expired === true;
  // The approval Resume continues: the CLAIMING one of an interrupted execution. Only a
  // hint that the lease is over -- evidence.execute_approval decides, and answers 409
  // while the claim is still live (the message is shown in the form's alert).
  const resumable = interrupted ? claim?.approval : undefined;
  // A large release waits for a second person; Execute is withheld until one has approved.
  const awaitingSecond = validApproval?.requires_second === true && !validApproval.second_approval;
  const isFirstApprover = awaitingSecond
    && (identity?.email ?? "").toLowerCase() === (validApproval?.approver ?? "").toLowerCase();
  const [secondNote, setSecondNote] = useState("");
  const verificationSecondApprove = useMutation({
    onSettled: (_data, error) => {
      if (outcomeUnknown(error)) onDecided();
    },
    mutationFn: () => {
      if (!validApproval) throw new Error("No valid approval to second");
      return secondApprove(c.case_id, validApproval.approval_id, secondNote);
    },
    onSuccess: () => {
      setFormError(null);
      setSecondNote("");
      onDecided();
      toast({ title: "Second approval recorded. The release can now be executed" });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const verificationCancel = useMutation({
    mutationFn: () => {
      if (!openRequest) throw new Error("No open request to cancel");
      return cancelEvidenceRequest(c.case_id, openRequest.request_id, note.trim() || "Cancelled by reviewer");
    },
    onSuccess: () => {
      setFormError(null);
      setEvidenceLinks([]);
      onDecided();
      toast({ title: "Request cancelled" });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  const verificationExecute = useMutation({
    onSettled: (_data, error) => {
      if (outcomeUnknown(error)) onDecided();
    },
    mutationFn: () => {
      const approval = resumable ?? validApproval;
      if (!approval) throw new Error("No valid approval to execute");
      return executeReloadingOnConflict(approval.approval_id);
    },
    onSuccess: (result) => {
      if ((result as Record<string, unknown>)?.state === "EXECUTION_FAILED") {
        setFormError("Execution failed. You can retry.");
        onDecided();
        return;
      }
      setFormError(null);
      onDecided();
      toast({ title: "Decision executed" });
    },
    onError: (error) => {
      setFormError(error instanceof Error ? error.message : String(error));
    },
  });

  function submit(action: HumanAction) {
    // Validated here as well as server-side, so the reviewer is told what is
    // missing before a round trip rather than after one.
    //
    // The reviewer's identity is no longer validated here because it is no longer
    // this screen's to supply: an unauthenticated caller never reaches the console
    // at all, and the server reads the name from the verified session.
    if (action !== "release" && !note.trim()) {
      setFormError(
        `A note is required to ${action.replace("_", " ")}. A refusal nobody has to justify is not a control.`,
      );
      return;
    }
    // A release is still possible against contradicting papers (the reader is a model,
    // and a reviewer who has seen the original must be able to dismiss its false alarm),
    // but it is an exception, and an exception carries its reason. The API enforces the same.
    if (action === "release" && contradicted && !note.trim()) {
      setFormError(
        "A reason is required to release a case whose papers contradict the declared origin. Say what you checked that the model did not.",
      );
      return;
    }
    // The same for papers that are still unresolved (N04): the release is an exception, so
    // it needs a reason and the operator role. The API enforces both; this says it first.
    if (action === "release" && evidenceToWaive(evaluation).length > 0) {
      if (!note.trim()) {
        setFormError("A reason is required to release over unresolved or contradicting evidence. It is recorded as an exception.");
        return;
      }
      if (exceptionLock) {
        setFormError(`A release over open evidence is an exception. ${exceptionLock}`);
        return;
      }
    }
    setFormError(null);
    // Only opens the confirmation. Nothing is sent until Confirm, which routes it.
    setPending(action);
  }

  return (
    <div className="space-y-3">
      {disputed && (
        <div className="flex items-start gap-2 rounded-lg border border-risk-warn/30 bg-risk-warn/[0.07] px-3 py-2.5">
          <AlertTriangle className="mt-[1px] size-4 shrink-0 text-risk-warn" aria-hidden />
          <p className="text-[12px] leading-relaxed text-risk-warn">
            The model scored {rec?.model_risk} and the deterministic floor put
            this at {rec?.risk_floor}. The floor stands, so this case carries{" "}
            {rec?.effective_risk} — read the findings before deciding.
          </p>
        </div>
      )}

      <div className="bento-card p-4">
        <h2 className="font-mono text-[13px] text-white">
          {c.shipment_id || c.case_id}
        </h2>
        <p className="mt-0.5 text-[11.5px] text-dim">
          {caseStateLabel(c.state)}
          {c.requires_human && " · flagged for a person"}
        </p>

        {detailError ? (
          <div className="mt-3">
            <ErrorState error={detailError} />
          </div>
        ) : detailLoading ? (
          <Skeleton className="mt-3 h-24 rounded-lg bg-white/[0.04]" />
        ) : (
          <>
            {c.gate_denials && c.gate_denials.length > 0 && (
              <p className="mt-3 rounded-md border border-risk-critical/30 bg-risk-critical/[0.07] px-2.5 py-2 text-[11.5px] leading-relaxed text-risk-critical">
                The delegation boundary refused{" "}
                {c.gate_denials.map((d) => actionLabel(d.action)).join(", ")}, so
                the agent&rsquo;s proposed{" "}
                {c.proposed_outcome
                  ? complianceStatusLabel(c.proposed_outcome)
                  : "outcome"}{" "}
                was never executed.
              </p>
            )}

            {findings.length > 0 && (
              <div className="mt-3 flex items-center gap-1.5">
                <p className="text-[10.5px] uppercase tracking-wide text-dim">
                  Findings &middot; each sets a minimum score
                </p>
                <HelpDot id="review.findings" />
              </div>
            )}
            {findings.length > 0 && (
              <ul className="mt-1.5 space-y-1.5">
                {findings.map((f, i) => (
                  <li
                    key={`${f.code}-${i}`}
                    className="rounded-md border border-white/[0.07] bg-black/25 px-2.5 py-2"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="text-[11.5px] font-medium text-white/90">
                          {humaniseCode(f.code)}
                        </span>
                        <HelpDot id={`finding:${f.code}`} />
                      </span>
                      <span className="shrink-0 font-mono text-[10.5px] tabular-nums text-faint">
                        floor {f.floor}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-dim">
                      {f.detail}
                    </p>
                  </li>
                ))}
              </ul>
            )}

            {evaluation && (
              <div
                data-testid="evidence-check"
                className={cn(
                  "mt-3 rounded-md border px-2.5 py-2",
                  contradicted
                    ? "border-risk-critical/30 bg-risk-critical/[0.07]"
                    : "border-white/[0.07] bg-black/25",
                )}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11.5px] font-medium text-white/90">
                    Evidence check &middot; v{evaluation.evidence_version} &middot;{" "}
                    {evaluation.status}
                  </span>
                  <span className="shrink-0 font-mono text-[10.5px] tabular-nums text-faint">
                    floor {evaluation.risk_floor}
                  </span>
                </div>
                {contradicted && (
                  <ul className="mt-1.5 space-y-1">
                    {(evaluation.contradictions ?? []).map((x, i) => (
                      <li key={`${x.kind}-${i}`} className="text-[11.5px] leading-relaxed">
                        <span className="text-risk-critical">
                          {CONTRADICTION_KIND_LABEL[x.kind] ?? humaniseCode(x.kind)}:
                        </span>{" "}
                        <span className="text-dim">{x.detail}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {!contradicted && evaluation.status === "provided" && (
                  <p className="mt-1 text-[11.5px] leading-relaxed text-dim">
                    The papers the shipper sent are consistent with the declared origin. The
                    floor stays at {evaluation.risk_floor}: documents never lower it. A
                    reviewer&rsquo;s decision does.
                  </p>
                )}
                {(evaluation.unresolved ?? []).length > 0 && (
                  <p className="mt-1 text-[11.5px] leading-relaxed text-dim">
                    Still missing or unreadable:{" "}
                    {(evaluation.unresolved ?? []).map(humaniseCode).join(", ")}.
                  </p>
                )}
              </div>
            )}

            {observations.length > 0 && (
              <div className="mt-3">
                <p className="text-[10.5px] uppercase tracking-wide text-faint">
                  Context &middot; recorded, not scored
                </p>
                <ul className="mt-1 space-y-1">
                  {observations.map((o, i) => (
                    <li
                      key={`${o.code}-${i}`}
                      className="rounded-md border border-dashed border-white/[0.08] px-2.5 py-1.5"
                    >
                      <span className="flex items-center gap-1.5 text-[11px] text-white/75">
                        {helpFor(`finding:${o.code}`)?.title ?? humaniseCode(o.code)}
                        <HelpDot id={`finding:${o.code}`} />
                      </span>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-faint">{o.detail}</p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      <div className="bento-card p-4">
        <div className="flex items-center gap-1.5">
          <h3 className="text-[12px] font-medium text-white">Decision</h3>
          <HelpDot id="review.decision" />
        </div>

        {/* Not permitted: explain, do not offer.
            Reads are public on this deployment, so somebody can reach this panel
            with no session, or with a session that does not hold the reviewer role.
            Rendering the buttons anyway would mean their first information about
            the role is a 401 or 403 on a release they thought they had recorded --
            and for a shipment release, believing you acted when you did not is the
            worst possible failure. `isLoading` is excluded so the panel does not
            flicker through this state on every load. */}
        {!identityQuery.isLoading && decideLock ? (
          <div className="mt-3 rounded-md border border-white/10 bg-black/20 p-3">
            <p className="text-[12px] leading-relaxed text-dim">
              {identity?.authenticated_by === "anonymous" ? (
                <>
                  You are viewing this queue read-only. Recording a decision needs a
                  signed-in account with the Reviewer role, because the audit trail
                  names the person who decided rather than the service that called
                  the API.
                </>
              ) : (
                <>
                  Signed in as <span className="font-mono text-white/85">{identity?.email}</span>{" "}
                  ({roleLabel(identity?.role)}). Recording a decision needs the Reviewer
                  role, which the API assigns from its own list of reviewers — ask a
                  governance admin to add this account.
                </>
              )}
            </p>
            {identity?.authenticated_by === "anonymous" && (
              <a
                href={signInHref()}
                className="mt-2.5 inline-flex h-8 items-center rounded-md border border-white/15 px-3 text-[12px] text-white/90 transition-colors hover:border-white/30"
              >
                Sign in to record a decision
              </a>
            )}
          </div>
        ) : (
        <div className="mt-3 space-y-3">
          <div>
            <Label className="text-[11.5px] text-dim">Recorded as</Label>
            <p className="mt-1 truncate rounded-md border border-white/10 bg-black/30 px-2.5 py-[7px] font-mono text-[12px] text-white/90">
              {identity?.acts_for_a_person ? identity.email : "your signed-in account"}
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-faint">
              Taken from your session, not typed. The audit trail records this
              address and it cannot be overridden from this screen.
            </p>
          </div>

          <div>
            <Label htmlFor="note" className="text-[11.5px] text-dim">
              Reason{" "}
              <span className="text-faint">
                (required for block and request info
                {contradicted ? ", and to release against contradicting papers" : ""})
              </span>
            </Label>
            <Textarea
              id="note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="What you checked and what you concluded"
              className="mt-1 border-white/10 bg-black/30 text-[12.5px]"
            />
          </div>

          {formError && (
            <p
              role="alert"
              className="rounded-md border border-risk-critical/30 bg-risk-critical/[0.07] px-2.5 py-2 text-[11.5px] leading-relaxed text-risk-critical"
            >
              {formError}
            </p>
          )}

          {evidenceLinks.length > 0 && (
            <div
              role="status"
              data-testid="evidence-links"
              className="rounded-md border border-white/10 bg-black/30 px-2.5 py-2 text-[11.5px] leading-relaxed text-white/85"
            >
              <p className="text-dim">Send these links to the shipper (shown once):</p>
              {evidenceLinks.map((l) => (
                <p key={l} className="mt-1 break-all font-mono text-[11px]">{l}</p>
              ))}
            </div>
          )}

          {hasVerification && c.state === "AWAITING_EVIDENCE" && (
            <p className="text-[11.5px] text-dim" data-testid="state-hint">
              Waiting for the shipper to upload documents.
            </p>
          )}
          {hasVerification && c.state === "APPROVED" && !awaitingSecond && (
            <p className="text-[11.5px] text-dim" data-testid="state-hint">
              Approved, ready to execute.
            </p>
          )}
          {hasVerification && c.state === "APPROVED" && awaitingSecond && (
            <div className="space-y-2" data-testid="second-approval">
              <p className="text-[11.5px] leading-relaxed text-dim">
                This release is large enough to need a second approver before it can be executed.
                {isFirstApprover
                  ? " You approved it first, so a different reviewer must approve it."
                  : " Say what you checked, then approve it as the second person."}
              </p>
              {!isFirstApprover && (
                <>
                  <Label htmlFor="second-note" className="text-[11.5px] text-dim">
                    What you checked
                  </Label>
                  <Textarea
                    id="second-note"
                    value={secondNote}
                    onChange={(e) => setSecondNote(e.target.value)}
                    rows={2}
                    className="border-white/10 bg-black/30 text-[12.5px]"
                  />
                  <Button
                    size="sm"
                    disabled={verificationSecondApprove.isPending || !secondNote.trim()}
                    onClick={() => verificationSecondApprove.mutate()}
                    className="h-8 text-[12px]"
                  >
                    {verificationSecondApprove.isPending && (
                      <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />
                    )}
                    Approve as second approver
                  </Button>
                </>
              )}
            </div>
          )}
          {hasVerification && c.state === "EXECUTING" && (
            <p
              className={cn("text-[11.5px]", interrupted ? "text-risk-warn" : "text-dim")}
              data-testid="state-hint"
            >
              {interrupted
                ? "Execution interrupted - claim expired. The earlier attempt never confirmed that the decision was sent. Resume continues it."
                : "Execution in progress. The decision is being sent; this page shows the result when it finishes."}
            </p>
          )}
          {hasVerification && c.state === "EXECUTION_FAILED" && (
            <p className="text-[11.5px] text-risk-critical" data-testid="state-hint">
              Execution failed. Retry to send the decision again.
            </p>
          )}

          {hasVerification && openRequest && c.state !== "AWAITING_EVIDENCE" && (
            // Re-evaluating with only part of the set leaves the case waiting on a
            // person while its request is still open, and the API refuses to approve
            // until the request is closed. Say so here instead of on the 409.
            <p className="text-[11.5px] leading-relaxed text-dim" data-testid="open-request-hint">
              A request for documents is still open (
              {Object.keys(openRequest.received_docs ?? {}).length} of{" "}
              {(openRequest.documents ?? []).length} received). Release and Block are refused
              until it is closed: wait for the rest and re-evaluate, or cancel the request
              with a reason.
            </p>
          )}
          {hasVerification && REEVALUABLE_STATES.includes(String(c.state)) && (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={verificationReevaluate.isPending}
                  onClick={() => verificationReevaluate.mutate()}
                  className="h-8 border-white/10 text-[12px]"
                >
                  {verificationReevaluate.isPending && (
                    <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />
                  )}
                  Re-evaluate
                </Button>
                {openRequest && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={verificationCancel.isPending}
                    onClick={() => verificationCancel.mutate()}
                    className="h-8 border-white/10 text-[12px]"
                  >
                    Cancel request
                  </Button>
                )}
              </div>
            )}
          {hasVerification &&
            (resumable != null ||
              ((c.state === "APPROVED" || c.state === "EXECUTION_FAILED") && validApproval != null && !awaitingSecond)) && (
              <Button
                size="sm"
                disabled={verificationExecute.isPending}
                onClick={() => verificationExecute.mutate()}
                className="h-8 text-[12px]"
              >
                {verificationExecute.isPending && (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />
                )}
                {resumable ? "Resume" : c.state === "EXECUTION_FAILED" ? "Retry" : "Execute"}
              </Button>
            )}

          {awaitingDecision && contradicted && (
            <div
              role="note"
              data-testid="contradiction-warning"
              className="flex items-start gap-2 rounded-md border border-risk-critical/30 bg-risk-critical/[0.07] px-2.5 py-2"
            >
              <AlertTriangle className="mt-[1px] size-3.5 shrink-0 text-risk-critical" aria-hidden />
              <p className="text-[11.5px] leading-relaxed text-risk-critical">
                The papers contradict the declared origin. Blocking is the expected action;
                releasing is recorded as an exception and needs a reason.
              </p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {awaitingDecision && (["release", "block", "request_info"] as HumanAction[]).map((action) => (
              // The dot sits beside the button, not inside it: nesting it would
              // put a second click target inside a control whose whole job is to
              // be pressed deliberately.
              <span key={action} className="inline-flex items-center gap-1">
                <Button
                  size="sm"
                  variant={action === "release" ? "default" : "outline"}
                  disabled={decide.isPending || verificationApprove.isPending || verificationRequest.isPending}
                  onClick={() => submit(action)}
                  className={cn(
                    "h-8 text-[12px]",
                    action === "block" &&
                      "border-risk-critical/40 text-risk-critical hover:bg-risk-critical/10",
                    action === "request_info" && "border-white/10",
                  )}
                >
                  {decide.isPending && pending === action && (
                    <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />
                  )}
                  {ACTION_LABEL[action]}
                </Button>
                <HelpDot id={`action:${action}`} side="top" />
              </span>
            ))}

            <Button
              size="sm"
              variant="outline"
              // Guarded against a double submit as well as confirmed: this spends
              // debate-model tokens, and a second click while the first is in
              // flight bills the tenant twice for the same answer.
              disabled={deep.isPending}
              onClick={() => setConfirmDeep(true)}
              className="ml-auto h-8 border-brand/40 text-[12px] text-brand hover:bg-brand/10"
            >
              {deep.isPending ? (
                <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />
              ) : (
                <Sparkles className="mr-1.5 size-3.5" />
              )}
              {deep.isPending ? "Deep review running" : "Deep review"}
            </Button>
          </div>
          {deep.isPending && (
            <p className="text-[11px] text-dim" role="status">
              The Senior Auditor is debating the case. This usually takes a minute or two.
            </p>
          )}
        </div>
        )}
      </div>

      {debate != null && (
        <DebatePanel
          debate={debate}
          effect={debateWasAutomatic ? detail?.debate_effect : null}
          automatic={debateWasAutomatic}
        />
      )}

      <Paperwork case={c} identity={identity} />

      {/* Confirm on the decision, because it is not reversible from here. */}
      <AlertDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
      >
        <AlertDialogContent className="border-white/10 bg-slate-950">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-[15px]">
              {pending ? ACTION_LABEL[pending] : ""} {c.shipment_id || c.case_id}?
            </AlertDialogTitle>
            <AlertDialogDescription className="text-[12.5px] leading-relaxed text-dim">
              This records{" "}
              <span className="text-white/85">
                {/* Lower-cased on purpose: this sits mid-sentence after "This
                    records", where the map's sentence-case label would read as a
                    stray capital. */}
                {pending ? lowerFirst(caseStateLabel(HUMAN_ACTION_RESULT[pending])) : ""}
              </span>{" "}
              against the case under your name, writes two audit records, and
              cannot be undone from this console.
              {pending === "release" && !contradicted && evidenceToWaive(evaluation).length > 0 && (
                <>
                  {" "}
                  <span className="text-risk-warn" data-testid="exception-confirm">
                    Documents are still unresolved, so this release is recorded as an exception
                    with your reason.
                  </span>
                </>
              )}
              {pending === "release" && contradicted && (
                <>
                  {" "}
                  <span className="text-risk-critical" data-testid="contradiction-confirm">
                    The papers on file contradict the declared origin, so this release is
                    recorded as an exception with your reason.
                  </span>
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 text-[12.5px]">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="text-[12.5px]"
              onClick={() => {
                if (pending) {
                  if (pending === "request_info" && usesVerificationLoop) {
                    verificationRequest.mutate();
                  } else if (pending !== "request_info" && hasVerification) {
                    verificationApprove.mutate(pending as "release" | "block");
                  } else {
                    decide.mutate(pending);
                  }
                }
                setPending(null);
              }}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm on deep review, because it spends money. */}
      <AlertDialog open={confirmDeep} onOpenChange={setConfirmDeep}>
        <AlertDialogContent className="border-white/10 bg-slate-950">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-[15px]">
              Run a deep review?
            </AlertDialogTitle>
            <AlertDialogDescription className="text-[12.5px] leading-relaxed text-dim">
              The Senior Auditor re-reads the junior
              analyst&rsquo;s assessment and may run further searches. It is the most
              expensive operation in the system, takes a minute or two, and is
              billed to this tenant.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 text-[12.5px]">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="text-[12.5px]"
              onClick={() => {
                setConfirmDeep(false);
                deep.mutate();
              }}
            >
              Run it
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
