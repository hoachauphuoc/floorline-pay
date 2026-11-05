"use client";

import { useState } from "react";

import type { PaymentState } from "@/lib/api";
import { PayPalLogo } from "@/components/ui/PayPalLogo";
import { humaniseCode } from "@/lib/format";
import { cn } from "@/lib/utils";

const LABEL: Record<PaymentState["state"], string> = {
  none: "No payment",
  awaiting_decision: "Funds held, awaiting a decision",
  no_authorization: "No funds held",
  paid: "Paid to the carrier",
  voided: "Hold released to the shipper",
  held: "Payout held",
  pending: "Waiting to retry",
  failed: "Payment failed",
  unknown: "Status not recognised",
};

const TONE: Record<PaymentState["state"], string> = {
  none: "text-faint",
  awaiting_decision: "text-dim",
  no_authorization: "text-risk-warn",
  paid: "text-risk-clear",
  voided: "text-risk-clear",
  held: "text-risk-critical",
  pending: "text-risk-warn",
  failed: "text-risk-critical",
  unknown: "text-risk-warn",
};

/**
 * What happened to the money on one case: held at booking, paid to the carrier after a
 * person released it, or returned to the shipper after a block. Drawn from the settler's
 * own record. A state this card does not know is shown as not recognised, never as a success.
 */
export function PaymentCard({
  state,
  onRetry,
}: {
  state: PaymentState;
  onRetry?: (reason: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  async function retry() {
    setBusy(true);
    setError(null);
    setSent(false);
    try {
      await onRetry?.(reason.trim());
      setReason("");
      setSent(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The retry failed");
    } finally {
      setBusy(false);
    }
  }
  const steps: Array<[string, string | null | undefined]> = [
    ["Captured", state.capture?.paypal_id],
    ["Paid out", state.payout?.paypal_id],
    ["Voided", state.void?.paypal_id],
  ];
  const ids = steps.filter(([, id]) => Boolean(id));
  return (
    <section className="bento-card p-4" aria-label="Payment">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[12px] font-medium text-white">Payment</h2>
        <span className="inline-flex h-5 items-center rounded bg-white px-1.5">
          <PayPalLogo height={12} />
        </span>
      </div>
      <p className={cn("mt-1 text-[13px] font-medium", TONE[state.state] ?? TONE.unknown)}>
        {LABEL[state.state] ?? LABEL.unknown}
      </p>
      {state.reason && (
        <p className="mt-1 text-[12px] leading-relaxed text-dim">{state.reason}</p>
      )}
      {state.findings && state.findings.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11.5px] text-risk-warn">
          {state.findings.map((code) => (
            <li key={code}>{humaniseCode(code)}</li>
          ))}
        </ul>
      )}
      {ids.length > 0 && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px] text-dim">
          {ids.map(([label, id]) => (
            <div key={label} className="contents">
              <dt>{label}</dt>
              <dd className="truncate text-white/85">{id}</dd>
            </div>
          ))}
        </dl>
      )}
      {state.state === "failed" && onRetry && (
        <div className="mt-3 space-y-2">
          <label className="block text-[11.5px] text-dim" htmlFor={`retry-reason-${state.case_id}`}>
            Reason for the retry
          </label>
          <input
            id={`retry-reason-${state.case_id}`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="What you checked and what you concluded"
            className="w-full rounded border border-white/10 bg-transparent px-2 py-1 text-[12px] text-white"
          />
          <button
            type="button"
            disabled={busy || reason.trim() === ""}
            onClick={retry}
            className="rounded bg-white px-2 py-1 text-[12px] font-medium text-black disabled:opacity-40"
          >
            Retry settlement
          </button>
          {sent && (
            <p className="text-[12px] text-dim">Sent to the settler again. This card updates when it answers.</p>
          )}
          {error && (
            <p role="alert" className="text-[12px] text-risk-critical">
              {error}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
