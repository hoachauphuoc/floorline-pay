import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

describe("PaymentCard retry", () => {
  const failed = make({ state: "failed", reason: "PayPal captured the funds but did not pay the carrier" });

  it("offers no retry unless the payment failed and the page can retry", () => {
    const { rerender } = render(<PaymentCard state={failed} />);
    expect(screen.queryByRole("button", { name: "Retry settlement" })).not.toBeInTheDocument();
    rerender(<PaymentCard state={make({ state: "paid" })} onRetry={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Retry settlement" })).not.toBeInTheDocument();
  });

  it("keeps the button off until a reason is written, then sends the trimmed reason", async () => {
    const onRetry = vi.fn().mockResolvedValue({});
    render(<PaymentCard state={failed} onRetry={onRetry} />);
    const button = screen.getByRole("button", { name: "Retry settlement" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason for the retry"), { target: { value: "  topped up the balance " } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(onRetry).toHaveBeenCalledWith("topped up the balance"));
    expect(await screen.findByText(/Sent to the settler again/)).toBeInTheDocument();
  });

  it("shows why a retry was refused", async () => {
    const onRetry = vi.fn().mockRejectedValue(new Error("only a settlement that ended failed can be retried"));
    render(<PaymentCard state={failed} onRetry={onRetry} />);
    fireEvent.change(screen.getByLabelText("Reason for the retry"), { target: { value: "checked" } });
    fireEvent.click(screen.getByRole("button", { name: "Retry settlement" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("only a settlement that ended failed");
  });
});

describe("PaymentCard payout confirmation", () => {
  const step = { status: "ok", http_status: 201, error: null };
  const paid = (payout_status?: PaymentState["payout_status"]) =>
    make({
      state: "paid",
      capture: { ...step, paypal_id: "CAP-1" },
      payout: { ...step, paypal_id: "PAYOUT-1" },
      payout_status,
    });
  const waiting = "Payout accepted by PayPal, waiting for its confirmation";

  it.each([
    ["confirmed", "Payout confirmed by PayPal"],
    ["unconfirmed", waiting],
    ["pending", waiting],
    ["failed", "PayPal reports the payout failed"],
  ] as const)("says a %s payout in words", (state, words) => {
    render(<PaymentCard state={paid({ state, status: null, at: null })} />);
    expect(screen.getByText(words)).toBeInTheDocument();
  });

  it("treats a payout with no status as waiting, not as confirmed", () => {
    render(<PaymentCard state={paid()} />);
    expect(screen.getByText(waiting)).toBeInTheDocument();
    expect(screen.queryByText("Payout confirmed by PayPal")).not.toBeInTheDocument();
  });

  it("never shows a status it does not know as confirmed", () => {
    const stranger = { state: "settled" as "confirmed", status: null, at: null };
    render(<PaymentCard state={paid(stranger)} />);
    expect(screen.getByText("Payout status not recognised")).toBeInTheDocument();
    expect(screen.queryByText("Payout confirmed by PayPal")).not.toBeInTheDocument();
  });

  it("says nothing about a payout when none was made", () => {
    render(<PaymentCard state={make({ state: "voided" })} />);
    expect(screen.queryByText(/Payout/)).not.toBeInTheDocument();
  });
});
