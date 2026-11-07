"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { PageHeading } from "@/components/layout/PageHeading";
import { EmptyState, ErrorState } from "@/components/layout/States";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchPaymentLedger, type PaymentState } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { paidLabel } from "@/lib/payout-label";
import { BOARD_AUTO_REFRESH } from "@/lib/pipeline";
import { useTenant } from "@/lib/tenant-context";
import { cn } from "@/lib/utils";

const LABEL: Record<PaymentState["state"], string> = {
  none: "No payment",
  awaiting_decision: "Awaiting a decision",
  no_authorization: "No funds held",
  paid: "Paid to the carrier",
  voided: "Returned to the shipper",
  held: "Payout held",
  pending: "Waiting to retry",
  failed: "Payment failed",
  decision_not_sent: "Decision not sent",
  unknown: "Status not recognised",
};

function shownFor(item: { state: PaymentState["state"]; payout_status?: PaymentState["payout_status"] }) {
  if (item.state === "paid") return paidLabel(item.payout_status);
  return { text: LABEL[item.state] ?? LABEL.unknown, tone: TONE[item.state] ?? TONE.unknown };
}

const TONE: Record<PaymentState["state"], string> = {
  none: "text-faint",
  awaiting_decision: "text-dim",
  no_authorization: "text-risk-warn",
  paid: "text-risk-clear",
  voided: "text-risk-clear",
  held: "text-risk-critical",
  pending: "text-risk-warn",
  failed: "text-risk-critical",
  decision_not_sent: "text-risk-critical",
  unknown: "text-risk-warn",
};

/**
 * Payments: what the settler did with the money on each case, newest outcome per case.
 *
 * Read-only. Money moves only in the settler, after a person's decision; this screen shows
 * the result and offers no way to move any.
 */
export default function PaymentsPage() {
  const { tenant } = useTenant();
  const ledger = useQuery({
    queryKey: ["payment-ledger", tenant.id],
    queryFn: fetchPaymentLedger,
    refetchInterval: BOARD_AUTO_REFRESH ? 15_000 : false,
    retry: false,
  });

  const heading = (
    <PageHeading title="Payments">
      The newest outcome of every payment: paid to the carrier after a person released the
      shipment, returned to the shipper after a block, or held back by the payment checks.
    </PageHeading>
  );

  if (ledger.isError) {
    return (
      <>
        {heading}
        <ErrorState error={ledger.error} />
      </>
    );
  }

  if (ledger.isLoading || !ledger.data) {
    return (
      <>
        {heading}
        <Skeleton className="h-64 rounded-xl bg-white/[0.04]" />
      </>
    );
  }

  const { items } = ledger.data;
  const tally = new Map<string, { text: string; tone: string; n: number }>();
  for (const item of items) {
    const shown = shownFor(item);
    tally.set(shown.text, { ...shown, n: (tally.get(shown.text)?.n ?? 0) + 1 });
  }

  return (
    <>
      {heading}

      {items.length === 0 ? (
        <EmptyState title="No payments yet">
          A payment appears here once a person has released or blocked a booked shipment.
        </EmptyState>
      ) : (
        <>
          <ul className="mb-4 flex flex-wrap gap-2" aria-label="Payments by state">
            {[...tally.values()].map((entry) => (
              <li
                key={entry.text}
                className="rounded-lg border border-white/[0.08] bg-black/25 px-3 py-1.5 text-[12px]"
              >
                <span className={cn("font-medium", entry.tone)}>{entry.text}</span>
                <span className="ml-2 font-mono tabular-nums text-dim">{entry.n}</span>
              </li>
            ))}
          </ul>

          <section className="bento-card overflow-x-auto p-2" aria-label="Payment ledger">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Case</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Why</TableHead>
                  <TableHead>Captured</TableHead>
                  <TableHead>Paid out</TableHead>
                  <TableHead>Voided</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.case_id}>
                    <TableCell className="font-mono text-[12px]">
                      <Link
                        href={`/cases/${encodeURIComponent(item.case_id)}`}
                        className="text-brand hover:underline"
                      >
                        {item.case_id}
                      </Link>
                    </TableCell>
                    <TableCell className={cn("text-[12px]", shownFor(item).tone)}>
                      {shownFor(item).text}
                    </TableCell>
                    <TableCell className="max-w-[22rem] text-[12px] text-dim">
                      {item.reason ?? ""}
                    </TableCell>
                    <TableCell className="font-mono text-[11px]">{item.capture?.paypal_id ?? ""}</TableCell>
                    <TableCell className="font-mono text-[11px]">{item.payout?.paypal_id ?? ""}</TableCell>
                    <TableCell className="font-mono text-[11px]">{item.void?.paypal_id ?? ""}</TableCell>
                    <TableCell className="text-[11px] text-faint">
                      {item.at ? formatRelative(item.at) : ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        </>
      )}
    </>
  );
}
