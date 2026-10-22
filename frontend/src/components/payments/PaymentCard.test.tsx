import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PaymentState } from "@/lib/api";

import { PaymentCard } from "./PaymentCard";

function make(overrides: Partial<PaymentState> = {}): PaymentState {
  return { case_id: "CASE-1", payment_case: true, state: "awaiting_decision", ...overrides };
}

describe("PaymentCard", () => {
  it.each([
    ["awaiting_decision", "Funds held, awaiting a decision"],
    ["no_authorization", "No funds held"],
    ["paid", "Paid to the carrier"],
    ["voided", "Hold released to the shipper"],
    ["held", "Payout held"],
    ["pending", "Waiting to retry"],
    ["failed", "Payment failed"],
    ["unknown", "Status not recognised"],
  ] as const)("says what the %s state means", (state, label) => {
    render(<PaymentCard state={make({ state })} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("never shows a state it does not know as a success", () => {
    const stranger = make({ state: "refunded" as PaymentState["state"] });
    render(<PaymentCard state={stranger} />);
    expect(screen.getByText("Status not recognised")).toBeInTheDocument();
    expect(screen.queryByText("Paid to the carrier")).not.toBeInTheDocument();
  });

  it("shows the reason the settler gave", () => {
    render(
      <PaymentCard state={make({ state: "held", reason: "the payment checks hold this payout" })} />,
    );
    expect(screen.getByText("the payment checks hold this payout")).toBeInTheDocument();
  });

  it("lists the findings that would hold the payout in words", () => {
    render(<PaymentCard state={make({ findings: ["PAYEE_MISMATCH", "PAYOUT_ABOVE_FREIGHT"] })} />);
    expect(screen.getByText("Payee mismatch")).toBeInTheDocument();
    expect(screen.getByText("Payout above freight")).toBeInTheDocument();
  });

  it("shows the PayPal ids of the steps that ran, and no others", () => {
    const step = { status: "ok", http_status: 201, error: null };
    render(
      <PaymentCard
        state={make({
          state: "paid",
          capture: { ...step, paypal_id: "CAP-1" },
          payout: { ...step, paypal_id: "PAYOUT-1" },
        })}
      />,
    );
    expect(screen.getByText("Captured")).toBeInTheDocument();
    expect(screen.getByText("CAP-1")).toBeInTheDocument();
    expect(screen.getByText("Paid out")).toBeInTheDocument();
    expect(screen.getByText("PAYOUT-1")).toBeInTheDocument();
    expect(screen.queryByText("Voided")).not.toBeInTheDocument();
  });

  it("shows a voided hold with its id", () => {
    const voided = { status: "ok", http_status: 204, paypal_id: "VOID-1", error: null };
    render(<PaymentCard state={make({ state: "voided", void: voided })} />);
    expect(screen.getByText("Voided")).toBeInTheDocument();
    expect(screen.getByText("VOID-1")).toBeInTheDocument();
  });
});
