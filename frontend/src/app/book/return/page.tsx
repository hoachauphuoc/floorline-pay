"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { authorizePaymentOrder, submitShipmentEvent } from "@/lib/api";
import { clearBooking, loadBooking, shipmentFromForm } from "@/lib/booking";

type Outcome =
  | { kind: "working" }
  | { kind: "done"; caseId: string | null }
  | { kind: "stopped"; message: string }
  | { kind: "failed"; error: unknown };

function Finish() {
  const token = useSearchParams()?.get("token") ?? "";
  const [outcome, setOutcome] = useState<Outcome>({ kind: "working" });
  const started = useRef(false);

  useEffect(() => {
    // Once only per mount: a repeat would submit the shipment twice. The stored booking is cleared only after the
    // shipment was accepted, so a failure leaves it for a reload to finish; the server takes the same order and the
    // same shipment id again without a second hold or a second case.
    if (started.current) return;
    started.current = true;

    (async () => {
      const form = token ? loadBooking(token) : null;
      if (!form) {
        setOutcome({
          kind: "stopped",
          message:
            "No booking is waiting for this PayPal approval, so nothing was submitted. " +
            "Start again from Book and pay.",
        });
        return;
      }
      try {
        const authorized = await authorizePaymentOrder(token);
        const result = await submitShipmentEvent({
          ...shipmentFromForm(form),
          paypal_order_id: authorized.order_id,
          paypal_authorization_id: authorized.authorization_id,
        });
        clearBooking();
        setOutcome({ kind: "done", caseId: result.case_id ? String(result.case_id) : null });
      } catch (error) {
        setOutcome({ kind: "failed", error });
      }
    })();
  }, [token]);

  return (
    <>
      <PageHeading title="Finishing your booking">
        PayPal has sent you back. The payment is authorized, not taken, and the shipment is now
        being checked.
      </PageHeading>

      <section className="bento-card max-w-3xl p-4" aria-label="Booking result">
        {outcome.kind === "working" && (
          <p className="text-[13px] text-dim">Authorizing the payment and submitting the shipment...</p>
        )}
        {outcome.kind === "stopped" && (
          <p className="text-[13px] text-risk-warn">{outcome.message}</p>
        )}
        {outcome.kind === "done" && (
          <p className="text-[13px] text-risk-clear">
            Booked.{" "}
            {outcome.caseId ? (
              <Link
                href={`/cases/${encodeURIComponent(outcome.caseId)}`}
                className="text-brand hover:underline"
              >
                Open {outcome.caseId}
              </Link>
            ) : (
              "The shipment is being checked."
            )}
          </p>
        )}
        {outcome.kind === "failed" && (
          <>
            <ErrorState error={outcome.error} />
            <p className="mt-3 text-[13px] text-dim">
              Your booking is still kept in this browser tab. Reload this page to try again with the same order.
            </p>
          </>
        )}
      </section>
    </>
  );
}

export default function BookReturnPage() {
  return (
    <Suspense fallback={null}>
      <Finish />
    </Suspense>
  );
}
