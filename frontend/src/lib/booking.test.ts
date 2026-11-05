import { describe, expect, it } from "vitest";

import {
  BOOKING_KEY,
  EMPTY_BOOKING,
  bookingProblem,
  clearBooking,
  loadBooking,
  orderRequest,
  saveBooking,
  shipmentFromForm,
  type BookingForm,
} from "./booking";

function form(overrides: Partial<BookingForm> = {}): BookingForm {
  return {
    ...EMPTY_BOOKING,
    shipment_id: "SHP-PAY-1",
    shipping_cost: "1300",
    shipper_company: "Saigon Plywood Co",
    carrier_payee_email: "payments@mekong-haulage.example",
    ...overrides,
  };
}

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => void data.delete(key),
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

describe("bookingProblem", () => {
  it("accepts a form with a shipment id and a freight above zero", () => {
    expect(bookingProblem(form())).toBeNull();
  });

  it("asks for a shipment id", () => {
    expect(bookingProblem(form({ shipment_id: "  " }))).toContain("shipment id");
  });

  it.each(["", "   "])("asks for the shipper when it is %j, because a block on a shipper is looked up by its name", (value) => {
    expect(bookingProblem(form({ shipper_company: value }))).toContain("shipper");
  });

  it.each(["", "abc", "0", "-5", "NaN"])("refuses a freight of %j", (value) => {
    expect(bookingProblem(form({ shipping_cost: value }))).toContain("freight");
  });

  it("does not require a payee, because a missing payee is a finding, not a typo", () => {
    expect(bookingProblem(form({ carrier_payee_email: "" }))).toBeNull();
  });
});

describe("shipmentFromForm", () => {
  it("sends numbers as numbers and leaves blank fields out", () => {
    const shipment = shipmentFromForm(form({ weight_kg: "22000", hs_code: " 4412.33 " }));
    expect(shipment.weight_kg).toBe(22000);
    expect(shipment.shipping_cost).toBe(1300);
    expect(shipment.hs_code).toBe("4412.33");
    expect(shipment).not.toHaveProperty("origin");
    expect(shipment).not.toHaveProperty("carrier_name");
  });

  it("books in US dollars", () => {
    expect(shipmentFromForm(form()).currency).toBe("USD");
  });

  it("pays out the booked freight when no payout amount is typed", () => {
    expect(shipmentFromForm(form()).payout_amount).toBe(1300);
  });

  it("keeps a payout amount that is typed, even above the freight", () => {
    expect(shipmentFromForm(form({ payout_amount: "4000" })).payout_amount).toBe(4000);
  });
});

describe("orderRequest", () => {
  it("asks for the freight, in dollars, and returns to this site", () => {
    expect(orderRequest(form({ shipment_id: " SHP-PAY-1 " }), "https://floorline-pay.example")).toEqual({
      amount: 1300,
      currency: "USD",
      reference_id: "SHP-PAY-1",
      return_url: "https://floorline-pay.example/book/return",
      cancel_url: "https://floorline-pay.example/book",
      booking: form({ shipment_id: "SHP-PAY-1" }),
    });
  });
});

describe("the booking kept across PayPal", () => {
  it("gives the form back for the same order", () => {
    const storage = memoryStorage();
    saveBooking("5O190127TN364715T", form(), storage);
    expect(loadBooking("5O190127TN364715T", storage)).toEqual(form());
  });

  it("gives nothing back for another order", () => {
    const storage = memoryStorage();
    saveBooking("5O190127TN364715T", form(), storage);
    expect(loadBooking("OTHER-ORDER", storage)).toBeNull();
  });

  it("gives nothing back when nothing was stored or the store is unreadable", () => {
    expect(loadBooking("5O190127TN364715T", memoryStorage())).toBeNull();
    expect(loadBooking("5O190127TN364715T", null)).toBeNull();
  });

  it.each(["not json", "null", '{"order_id":"X","form":"text"}', '{"form":{}}'])(
    "gives nothing back for a damaged record %j",
    (raw) => {
      const storage = memoryStorage();
      storage.setItem(BOOKING_KEY, raw);
      expect(loadBooking("X", storage)).toBeNull();
    },
  );

  it("keeps only string fields of the known names", () => {
    const storage = memoryStorage();
    storage.setItem(
      BOOKING_KEY,
      JSON.stringify({ order_id: "X", form: { shipment_id: "SHP-1", shipping_cost: 5, extra: "no" } }),
    );
    const loaded = loadBooking("X", storage);
    expect(loaded?.shipment_id).toBe("SHP-1");
    expect(loaded?.shipping_cost).toBe("");
    expect(loaded).not.toHaveProperty("extra");
  });

  it("forgets the booking once cleared", () => {
    const storage = memoryStorage();
    saveBooking("X", form(), storage);
    clearBooking(storage);
    expect(loadBooking("X", storage)).toBeNull();
  });

  it("does nothing, and does not throw, when there is no storage", () => {
    expect(() => saveBooking("X", form(), null)).not.toThrow();
    expect(() => clearBooking(null)).not.toThrow();
  });
});
