import { describe, expect, it } from "vitest";

import { EMPTY_BOOKING, shipmentFromForm } from "./booking";

describe("the booking receiver company", () => {
  it("starts blank", () => {
    expect(EMPTY_BOOKING).toMatchObject({ receiver_company: "" });
  });

  it("sends the receiver company in the shipment, and leaves a blank one out", () => {
    const filled = shipmentFromForm({
      ...EMPTY_BOOKING,
      shipment_id: "SHP-PAY-1",
      shipping_cost: "20",
      receiver_company: " Hanoi Garment Retail Co ",
    });
    expect(filled).toMatchObject({ receiver_company: "Hanoi Garment Retail Co" });
    const blank = shipmentFromForm({ ...EMPTY_BOOKING, shipment_id: "SHP-PAY-1", shipping_cost: "20" });
    expect(blank).not.toHaveProperty("receiver_company");
  });
});
