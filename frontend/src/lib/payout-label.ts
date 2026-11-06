import type { PaymentState } from "@/lib/api";

export type PaidLabel = { text: string; tone: string };

export type PaidKind = "confirmed" | "accepted" | "waiting" | "failed" | "returned";

const RETURNED = new Set(["RETURNED", "REFUNDED", "REVERSED"]);

const LABELS: Record<PaidKind, PaidLabel> = {
  confirmed: { text: "Paid to the carrier, confirmed by PayPal", tone: "text-risk-clear" },
  accepted: { text: "Payout accepted by PayPal, not confirmed yet", tone: "text-risk-warn" },
  waiting: { text: "Payout sent, the carrier has not received it yet", tone: "text-risk-warn" },
  failed: { text: "Payout failed at PayPal", tone: "text-risk-critical" },
  returned: { text: "Payout returned by PayPal", tone: "text-risk-critical" },
};

const NEXT: Record<PaidKind, string | null> = {
  confirmed: null,
  accepted: "Check the payout from this case to ask PayPal for its state.",
  waiting: "PayPal is still processing it. Check it again later.",
  failed: "The carrier is frozen. Check the payout at PayPal before a governance admin lifts the freeze.",
  returned: "The money came back and the carrier is frozen. Check the payee before a governance admin lifts the freeze.",
};

/**
 * What became of a case the settler recorded as paid. Only a payout PayPal confirmed reads as paid to the carrier; a state this
 * console does not know reads as accepted, a warning.
 */
export function paidKind(status: PaymentState["payout_status"]): PaidKind {
  if (status?.state === "confirmed") return "confirmed";
  if (status?.state === "failed") {
    return RETURNED.has(String(status.status ?? "").toUpperCase()) ? "returned" : "failed";
  }
  return status?.state === "pending" ? "waiting" : "accepted";
}

export function paidLabel(status: PaymentState["payout_status"]): PaidLabel {
  return LABELS[paidKind(status)];
}

export function paidNext(status: PaymentState["payout_status"]): string | null {
  return NEXT[paidKind(status)];
}
