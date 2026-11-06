import { describe, expect, it } from "vitest";

import { EMPTY_BOOKING, shipmentFromForm } from "./booking";

describe("the booking countries", () => {
  it("starts with both countries blank", () => {
    expect(EMPTY_BOOKING).toMatchObject({ shipper_country: "", receiver_country: "" });
  });

  it("sends both countries in the shipment, and leaves a blank one out", () => {
    const filled = shipmentFromForm({
      ...EMPTY_BOOKING,
      shipment_id: "SHP-PAY-1",
      shipping_cost: "20",
      shipper_country: " Vietnam ",
      receiver_country: "Vietnam",
    });
    expect(filled).toMatchObject({ shipper_country: "Vietnam", receiver_country: "Vietnam" });
    const blank = shipmentFromForm({ ...EMPTY_BOOKING, shipment_id: "SHP-PAY-1", shipping_cost: "20" });
    expect(blank).not.toHaveProperty("shipper_country");
    expect(blank).not.toHaveProperty("receiver_country");
  });
});
