import { describe, expect, it } from "vitest";

import { ALLOWED_POST, PASSWORD_ONLY_POST, matchWrite } from "./proxy-policy";

describe("the booking writes through the proxy", () => {
  it("allows creating an order and authorizing one order id", () => {
    expect(matchWrite("payments/orders", ALLOWED_POST)).toBe(true);
    expect(matchWrite("payments/orders/5O190127TN364715T/authorize", ALLOWED_POST)).toBe(true);
  });

  it("does not allow the template of the id-carrying form as a route of its own", () => {
    expect(matchWrite("payments/orders/authorize", ALLOWED_POST)).toBe(false);
  });

  it("refuses an order id with unexpected characters or a different action", () => {
    expect(matchWrite("payments/orders/lower-case/authorize", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/orders/..%2Fx/authorize", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/orders/5O190127TN364715T/capture", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/orders/5O190127TN364715T/authorize/extra", ALLOWED_POST)).toBe(false);
  });

  it("never lets a browser capture a payment or pay anyone", () => {
    for (const path of [
      "payments/capture",
      "payments/payout",
      "payments/payouts",
      "payments/authorizations/0VF52814937998046/capture",
      "payments/cases",
    ]) {
      expect(matchWrite(path, ALLOWED_POST)).toBe(false);
    }
  });

  it("is open to the one-click judge, because it only holds money in the sandbox", () => {
    expect(PASSWORD_ONLY_POST.has("payments/orders")).toBe(false);
    expect(PASSWORD_ONLY_POST.has("payments/orders/authorize")).toBe(false);
  });
});
