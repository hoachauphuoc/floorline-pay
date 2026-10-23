import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EMPTY_BOOKING, shipmentFromForm } from "@/lib/booking";

import BookPage from "./page";

describe("the booking leaves the payee on file to the carrier registry", () => {
  it("does not ask the shipper for the account on file", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BookPage />
      </QueryClientProvider>,
    );
    expect(screen.queryByLabelText("Carrier PayPal account on file")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Carrier PayPal account for this load")).toBeInTheDocument();
  });

  it("keeps the payee on file out of the form and out of the shipment", () => {
    expect(EMPTY_BOOKING).not.toHaveProperty("carrier_registered_payee_email");
    const shipment = shipmentFromForm({
      ...EMPTY_BOOKING,
      shipment_id: "SHP-1",
      shipping_cost: "100",
    });
    expect(shipment).not.toHaveProperty("carrier_registered_payee_email");
  });
});
