import { describe, expect, it } from "vitest";

import { EMPTY_BOOKING, bookingFromQuery } from "./booking";

describe("bookingFromQuery", () => {
  it("reads the form fields named in the link", () => {
    const preset = bookingFromQuery("?shipper_company=Lotus%20Demo%20Ceramics%20Co%20Ltd&hs_code=6912.00&shipping_cost=1500");
    expect(preset).toEqual({
      shipper_company: "Lotus Demo Ceramics Co Ltd",
      hs_code: "6912.00",
      shipping_cost: "1500",
    });
  });

  it("accepts a query without the leading question mark and trims the values", () => {
    expect(bookingFromQuery("origin=%20hanoi%20")).toEqual({ origin: "hanoi" });
  });

  it("ignores every key that is not a form field", () => {
    const preset = bookingFromQuery("?evil=1&approval_url=https%3A%2F%2Fexample.com&__proto__=x&released=true");
    expect(preset).toEqual({});
  });

  it("never fills the carrier PayPal account, which decides where the payout goes", () => {
    const preset = bookingFromQuery("?carrier_payee_email=attacker%40example.com&carrier_name=Mekong%20Haulage%20Co");
    expect(preset).toEqual({ carrier_name: "Mekong Haulage Co" });
  });

  it("ignores empty values and values longer than 200 characters", () => {
    const long = "a".repeat(201);
    expect(bookingFromQuery(`?hs_code=&cargo_description=${long}&destination=hanoi`)).toEqual({ destination: "hanoi" });
  });

  it("returns only keys that exist on the form", () => {
    const keys = Object.keys(bookingFromQuery("?shipment_id=SHP-PAY-DEMO-17&weight_kg=100"));
    for (const key of keys) {
      expect(Object.keys(EMPTY_BOOKING)).toContain(key);
    }
    expect(keys.sort()).toEqual(["shipment_id", "weight_kg"]);
  });
});
