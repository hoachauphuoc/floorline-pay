import { describe, expect, it } from "vitest";

import type { PaymentState } from "@/lib/api";

import { paidLabel } from "./payout-label";

describe("paidLabel", () => {
  it("is a success only when PayPal confirmed the payout", () => {
    expect(paidLabel({ state: "confirmed", status: "SUCCESS", at: null })).toEqual({
      text: "Paid to the carrier, confirmed by PayPal",
      tone: "text-risk-clear",
    });
  });

  it.each([
    undefined,
    { state: "unconfirmed", status: null, at: null },
    { state: "pending", status: "PENDING", at: null },
    { state: "settled", status: null, at: null },
  ])("is a warning, not a success, for %j", (status) => {
    expect(paidLabel(status as PaymentState["payout_status"])).toEqual({
      text: "Payout accepted by PayPal, not confirmed yet",
      tone: "text-risk-warn",
    });
  });

  it("is a danger when PayPal reports the payout failed", () => {
    expect(paidLabel({ state: "failed", status: "RETURNED", at: null })).toEqual({
      text: "Payout failed at PayPal",
      tone: "text-risk-critical",
    });
  });
});
