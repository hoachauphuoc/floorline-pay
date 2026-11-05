import { describe, expect, it } from "vitest";

import type { PaymentState } from "@/lib/api";

import { paidKind, paidLabel, paidNext } from "./payout-label";

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
    { state: "settled", status: null, at: null },
  ])("is a warning, not a success, for %j", (status) => {
    expect(paidLabel(status as PaymentState["payout_status"])).toEqual({
      text: "Payout accepted by PayPal, not confirmed yet",
      tone: "text-risk-warn",
    });
  });

  it("says the carrier has not received a payout PayPal is still working on", () => {
    expect(paidLabel({ state: "pending", status: "UNCLAIMED", at: null })).toEqual({
      text: "Payout sent, the carrier has not received it yet",
      tone: "text-risk-warn",
    });
  });

  it.each(["RETURNED", "REFUNDED", "REVERSED", "returned"])("names a %s payout as returned, not failed", (status) => {
    expect(paidKind({ state: "failed", status, at: null })).toBe("returned");
    expect(paidLabel({ state: "failed", status, at: null })).toEqual({
      text: "Payout returned by PayPal",
      tone: "text-risk-critical",
    });
  });

  it("gives the next step for every state but a confirmed payout", () => {
    expect(paidNext({ state: "confirmed", status: "SUCCESS", at: null })).toBeNull();
    expect(paidNext(undefined)).toMatch(/Check the payout/);
    expect(paidNext({ state: "pending", status: "PENDING", at: null })).toMatch(/still processing/);
    expect(paidNext({ state: "failed", status: "FAILED", at: null })).toMatch(/governance admin/);
    expect(paidNext({ state: "failed", status: "RETURNED", at: null })).toMatch(/money came back/);
  });

  it("is a danger when PayPal reports the payout failed", () => {
    expect(paidLabel({ state: "failed", status: "BLOCKED", at: null })).toEqual({
      text: "Payout failed at PayPal",
      tone: "text-risk-critical",
    });
  });
});
