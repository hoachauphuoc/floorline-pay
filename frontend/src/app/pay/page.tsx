"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { HelpDot } from "@/components/help/HelpDot";
import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchPaymentControls, fetchPaymentLedger } from "@/lib/api";
import { paidKind, type PaidKind } from "@/lib/payout-label";
import { BOARD_AUTO_REFRESH } from "@/lib/pipeline";
import { useTenant } from "@/lib/tenant-context";

const STEPS = [
  {
    href: "/book",
    title: "Book and hold",
    text: "The shipper approves the freight on PayPal. The money is authorized, not taken.",
  },
  {
    href: "/defense",
    title: "Screen the payout",
    text: "Deterministic checks set a floor. A model can raise the risk and never lower it.",
  },
  {
    href: "/review",
    title: "A person releases",
    text: "A person decides every disputed payout, and a large one needs a second person. The agent may pay a clean case only below a limit a person published.",
  },
  {
    href: "/payments",
    title: "Paid or returned",
    text: "The settler captures and pays the carrier, or voids the hold. It is the only code that moves money.",
  },
] as const;

/**
 * Floorline Pay: the life of a payment in four steps, with the live figures behind it.
 *
 * Read-only. Each step links to the screen where it happens; nothing here moves money.
 */
export default function PayPage() {
  const { tenant } = useTenant();
  const ledger = useQuery({
    queryKey: ["payment-ledger", tenant.id],
    queryFn: fetchPaymentLedger,
    refetchInterval: BOARD_AUTO_REFRESH ? 15_000 : false,
    retry: false,
  });
  const controls = useQuery({
    queryKey: ["payment-controls", tenant.id],
    queryFn: fetchPaymentControls,
    refetchInterval: BOARD_AUTO_REFRESH ? 15_000 : false,
    retry: false,
  });

  const figures = (() => {
    if (ledger.isError || controls.isError) {
      return <ErrorState error={ledger.error ?? controls.error} />;
    }
    if (!ledger.data || !controls.data) {
      return <Skeleton className="h-20 rounded-xl bg-white/[0.04]" />;
    }
    const counts = ledger.data.counts;
    const paid = ledger.data.items.filter((item) => item.state === "paid");
    const kinds = paid.map((item) => paidKind(item.payout_status));
    const count = (kind: PaidKind) => String(kinds.filter((found) => found === kind).length);
    const rows = [
      ["Paid to carriers, confirmed by PayPal", count("confirmed"), "pay.paid-confirmed"],
      ["Payouts accepted, not confirmed yet", count("accepted"), "pay.paid-accepted"],
      ["Payouts sent, not received yet", count("waiting"), "pay.paid-waiting"],
      ["Payouts failed at PayPal", count("failed"), "pay.paid-failed"],
      ["Payouts returned by PayPal", count("returned"), "pay.paid-returned"],
      ["Returned to shippers", String(counts.voided ?? 0), "pay.voided"],
      ["Held by the checks", String(counts.held ?? 0), "pay.held"],
      ["Decisions not sent", String(counts.decision_not_sent ?? 0), "pay.decision-not-sent"],
      ["Sent for payout today in USD", controls.data.usage_today.tenant_usd, "pay.sent-today"],
    ];
    return (
      <>
        <ul className="flex flex-wrap gap-2" aria-label="Live payment figures">
          {rows.map(([label, value, help]) => (
            <li
              key={label}
              aria-label={`${label}: ${value}`}
              className="rounded-lg border border-white/[0.08] bg-black/25 px-3 py-1.5 text-[12px]"
            >
              <span className="text-dim">{label}</span>
              <span className="ml-2 font-mono tabular-nums text-white">{value}</span>
              <HelpDot id={help} className="ml-1 align-middle" />
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[12px] text-dim">
          {controls.data.pause.paused ? "Payments are paused" : "Payments are running"}
        </p>
      </>
    );
  })();

  return (
    <>
      <PageHeading title="Floorline Pay">
        Shipper funds are held at booking and reach the carrier only on a published decision (a person&apos;s, or the agent&apos;s
        inside a limit a person set). A block returns them. Every step below is a screen you can open.
      </PageHeading>

      <ol className="mb-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="How a payment moves">
        {STEPS.map((step, index) => (
          <li key={step.title}>
            <Link
              href={step.href}
              className="bento-card block h-full p-4 transition-colors hover:bg-white/[0.04]"
            >
              <span className="font-mono text-[11px] text-faint">Step {index + 1}</span>
              <span className="mt-1 block text-[14px] font-medium text-white">{step.title}</span>
              <span className="mt-1 block text-[12px] leading-relaxed text-dim">{step.text}</span>
            </Link>
          </li>
        ))}
      </ol>

      <section className="bento-card p-4" aria-label="Live figures">
        {figures}
      </section>
    </>
  );
}
