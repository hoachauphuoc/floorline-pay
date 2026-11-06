import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaymentCard } from "./PaymentCard";

describe("PaymentCard PayPal mark", () => {
  it("carries the PayPal logo next to the Payment heading", () => {
    render(<PaymentCard state={{ case_id: "CASE-1", payment_case: true, state: "paid" }} />);
    expect(screen.getByAltText("PayPal")).toHaveAttribute("src", "/paypal/paypal-logo.png");
    expect(screen.getByRole("heading", { name: "Payment" })).toBeInTheDocument();
  });

  it("keeps the state text readable", () => {
    render(<PaymentCard state={{ case_id: "CASE-1", payment_case: true, state: "voided" }} />);
    expect(screen.getByText("Hold released to the shipper")).toBeInTheDocument();
  });
});
