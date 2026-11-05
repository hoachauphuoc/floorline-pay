"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";

import { HelpDot } from "@/components/help/HelpDot";
import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { CaseTimeline } from "@/components/operations/CaseTimeline";
import { useCaseActivity } from "@/components/operations/useCaseActivity";
import { PaymentCard } from "@/components/payments/PaymentCard";
import { ReviewPanel } from "@/components/review/ReviewPanel";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchAuditTrail, fetchCase, fetchPaymentState, queryKeys, refreshPayout, retrySettlement } from "@/lib/api";
import { caseHref } from "@/lib/case-links";
import { caseStateLabel, formatUsd } from "@/lib/format";
import { holds, useIdentity } from "@/lib/identity";
import { buildCaseTimeline, caseRefetchMs, informationRounds, riskLabel } from "@/lib/operations";
import { useTenant } from "@/lib/tenant-context";
import { AWAITING_HUMAN, type CaseState, VERIFICATION_LOOP_STATES } from "@/lib/types";

/**
 * One case, start to finish: what was found, what the agents did, what was
 * refused, what was asked for, and what was decided -- with the decision form
 * beside it while the case is still waiting on a person.
 *
 * The form is the Review Queue's own (components/review/ReviewPanel), so a
 * decision recorded here is the same decision, under the same role lock and the
 * same confirmation. Time a reviewer spends here is reported for the Outcomes
 * screen's handling-time figure (useCaseActivity); nobody else's is.
 */
export default function CasePage() {
  const params = useParams<{ id: string }>();
  const caseId = typeof params?.id === "string" ? decodeURIComponent(params.id) : "";
  const { tenant } = useTenant();
  const queryClient = useQueryClient();
  const identity = useIdentity().data;

  const detail = useQuery({
    queryKey: queryKeys.case(tenant.id, caseId),
    queryFn: () => fetchCase(caseId),
    // An execution ends on the server without anyone asking this page (N08).
    refetchInterval: (query) => caseRefetchMs(query.state.data?.state),
    enabled: caseId !== "",
    retry: false,
  });

  // The trail carries rows the case document does not, such as a refusal recorded
  // only there. Optional: a failure here leaves the line drawn from the case alone.
  const audit = useQuery({
    queryKey: queryKeys.auditTrail(tenant.id, { caseId }),
    queryFn: () => fetchAuditTrail({ caseId, limit: 100 }),
    enabled: caseId !== "",
    retry: false,
  });

  // What happened to the money, from the settler's own record. Optional: a failure here
  // leaves the page without the card, never without the case. Polled while the settlement
  // is still to come, because it follows the decision through Pub/Sub.
  const payment = useQuery({
    queryKey: ["payment", tenant.id, caseId],
    queryFn: () => fetchPaymentState(caseId),
    enabled: caseId !== "",
    retry: false,
    refetchInterval: (query) =>
      ["awaiting_decision", "pending", "failed"].includes(query.state.data?.state ?? "") ||
      (query.state.data?.state === "paid" && query.state.data.payout_status?.state !== "confirmed")
        ? 5000
        : false,
  });

  const c = detail.data;
  const events = useMemo(
    () => (c ? buildCaseTimeline(c, audit.data?.items ?? []) : []),
    [c, audit.data],
  );

  useCaseActivity(
    c?.case_id ?? null,
    identity?.acts_for_a_person === true && holds(identity, "reviewer") === true,
  );

  // The verification loop's own states still have something for a reviewer to do
  // (copy the link, re-evaluate, cancel, execute, retry), so the panel stays. Hiding
  // it on AWAITING_EVIDENCE threw away the request links, which are returned once.
  const awaiting = c
    ? AWAITING_HUMAN.includes(c.state as CaseState) ||
      (c.verification != null && VERIFICATION_LOOP_STATES.includes(String(c.state)))
    : false;

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["case", tenant.id] });
    queryClient.invalidateQueries({ queryKey: queryKeys.reviewQueue(tenant.id) });
    queryClient.invalidateQueries({ queryKey: queryKeys.worklist(tenant.id) });
    queryClient.invalidateQueries({ queryKey: ["audit-trail", tenant.id] });
    queryClient.invalidateQueries({ queryKey: ["payment", tenant.id] });
  };

  const back = (
    <Link
      href="/today"
      className="mb-3 inline-flex items-center gap-1 text-[12px] text-dim transition-colors hover:text-white"
    >
      <ArrowLeft className="size-3.5" aria-hidden />
      Today
    </Link>
  );

  if (detail.isError) {
    return (
      <>
        {back}
        <PageHeading title={caseId || "Case"}>The case could not be loaded.</PageHeading>
        <ErrorState error={detail.error} />
      </>
    );
  }

  if (detail.isLoading || !c) {
    return (
      <>
        {back}
        <Skeleton className="mb-4 h-10 w-72 bg-white/[0.04]" />
        <div className="grid gap-4 lg:grid-cols-[1fr_26rem]">
          <Skeleton className="h-96 rounded-xl bg-white/[0.04]" />
          <Skeleton className="h-96 rounded-xl bg-white/[0.04]" />
        </div>
      </>
    );
  }

  const rounds = informationRounds(c);

  return (
    <>
      {back}
      <PageHeading title={c.shipment_id || c.case_id}>
        {caseStateLabel(c.state)}
        {riskLabel(c) && ` · ${riskLabel(c)}`}
        {rounds > 0 && ` · ${rounds} ${rounds === 1 ? "request" : "requests"} for information`}
        {c._estimated_cost_usd != null && ` · ${formatUsd(c._estimated_cost_usd)} model spend`}
      </PageHeading>

      <div className="grid gap-4 lg:grid-cols-[1fr_26rem]">
        <section className="bento-card min-w-0 p-4" aria-label="What happened">
          <div className="mb-3 flex items-center gap-1.5">
            <h2 className="text-[12px] font-medium text-white">What happened</h2>
            <HelpDot id="case.timeline" />
            <Link
              href={caseHref(c.case_id)}
              className="ml-auto inline-flex items-center gap-1 text-[11.5px] text-brand hover:underline"
            >
              <ExternalLink className="size-3.5" aria-hidden />
              Full agent trace
            </Link>
          </div>
          {audit.isError && (
            <p className="mb-3 text-[11px] text-faint">
              The audit trail could not be read, so this line is drawn from the case
              record alone.
            </p>
          )}
          {payment.data?.payment_case === true && (
            <div className="mb-3">
              <PaymentCard
                state={payment.data}
                onCheckPayout={async () => {
                  await refreshPayout(caseId);
                  await queryClient.invalidateQueries({ queryKey: ["payment", tenant.id] });
                }}
                onRetry={async (reason) => {
                  await retrySettlement(caseId, reason);
                  await queryClient.invalidateQueries({ queryKey: ["payment", tenant.id] });
                  await queryClient.invalidateQueries({ queryKey: ["audit-trail", tenant.id] });
                }}
              />
            </div>
          )}
          <CaseTimeline events={events} />
        </section>

        <section className="min-w-0" aria-label="Decision">
          {awaiting ? (
            <ReviewPanel
              key={c.case_id}
              summary={c}
              detail={c}
              detailLoading={false}
              detailError={null}
              onDecided={refresh}
            />
          ) : (
            <div className="bento-card p-4">
              <h2 className="text-[12px] font-medium text-white">Nobody owes this case anything</h2>
              <p className="mt-1 text-[12px] leading-relaxed text-dim">
                It is {caseStateLabel(c.state).toLowerCase()}, so there is no decision to
                record here. The line on the left is its full history.
              </p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
