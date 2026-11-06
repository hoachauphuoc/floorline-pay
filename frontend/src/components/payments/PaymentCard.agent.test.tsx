import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PaymentState } from "@/lib/api";

import { PaymentCard } from "./PaymentCard";

const AGENT = { limit_usd: "1000", published_by: "admin@forwarder.example" };

function make(overrides: Partial<PaymentState> = {}): PaymentState {
  return { case_id: "CASE-1", payment_case: true, state: "paid", ...overrides };
}

describe("PaymentCard and the agent", () => {
  it("says the agent paid, under whose limit", () => {
    render(<PaymentCard state={make({ paid_by_agent: AGENT })} />);
    expect(
      screen.getByText("Paid by the agent, below the 1000 USD limit published by admin@forwarder.example."),
    ).toBeInTheDocument();
  });

  it("says nothing about the agent on a person's release", () => {
    render(<PaymentCard state={make({ paid_by_agent: null })} />);
    expect(screen.queryByText(/Paid by the agent/)).not.toBeInTheDocument();
  });

  it("does not claim the agent paid while the payment failed", () => {
    render(<PaymentCard state={make({ state: "failed", paid_by_agent: AGENT })} />);
    expect(screen.queryByText(/Paid by the agent/)).not.toBeInTheDocument();
  });

  it("offers no retry on a case the agent gave to a person", () => {
    render(<PaymentCard state={make({ state: "failed", handed_to_person: true })} onRetry={async () => {}} />);
    expect(screen.queryByRole("button", { name: "Retry settlement" })).not.toBeInTheDocument();
  });

  it("still offers the retry on a failed payment that stayed with the agent", () => {
    render(<PaymentCard state={make({ state: "failed", paid_by_agent: AGENT })} onRetry={async () => {}} />);
    expect(screen.getByRole("button", { name: "Retry settlement" })).toBeInTheDocument();
  });
});
