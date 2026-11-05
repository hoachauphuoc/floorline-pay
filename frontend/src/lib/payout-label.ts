import type { PaymentState } from "@/lib/api";

export type PaidLabel = { text: string; tone: string };

/**
 * How a case the settler recorded as paid is shown. Only a payout PayPal confirmed reads as paid to the carrier; one PayPal has
 * accepted but not confirmed, or whose state this console does not know, is a warning, and one PayPal reports as failed is a danger.
 */
export function paidLabel(status: PaymentState["payout_status"]): PaidLabel {
  if (status?.state === "confirmed") {
    return { text: "Paid to the carrier, confirmed by PayPal", tone: "text-risk-clear" };
  }
  if (status?.state === "failed") {
    return { text: "Payout failed at PayPal", tone: "text-risk-critical" };
  }
  return { text: "Payout accepted by PayPal, not confirmed yet", tone: "text-risk-warn" };
}
