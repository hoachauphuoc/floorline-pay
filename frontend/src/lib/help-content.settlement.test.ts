import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { helpFor } from "@/lib/help-content";

const APP = path.resolve(__dirname, "..", "app");

const SETTLEMENT: Array<[string, string, RegExp]> = [
  ["settlement.frozen", "Frozen parties", /pays nothing to a frozen party/],
  ["settlement.lift", "Lift a freeze", /kept on the audit trail/],
  ["settlement.unbound", "Holds with no case", /never became a case/],
  ["settlement.events", "PayPal events waiting", /before Floorline could match them/],
  ["settlement.payouts", "Payouts", /Only Confirmed by PayPal means the carrier has the money/],
];

const CARRIERS: Array<[string, string, RegExp]> = [
  ["carriers.register", "Register a payee", /stays pending until a different person verifies it/],
  ["carriers.request", "Message that asked for the change", /payment-diversion fraud/],
  ["carriers.callback", "Callback note", /cannot verify it/],
];

function source(page: string): string {
  return readFileSync(path.join(APP, page, "page.tsx"), "utf8");
}

describe("help for Settlement and Carriers", () => {
  it.each([...SETTLEMENT, ...CARRIERS])("%s explains itself", (id, title, phrase) => {
    expect(helpFor(id)?.title).toBe(title);
    expect(helpFor(id)?.body).toMatch(phrase);
  });

  it("never says that only a person pays, or that a model lowers risk", () => {
    for (const [id] of [...SETTLEMENT, ...CARRIERS]) {
      const entry = helpFor(id);
      expect(`${entry?.body} ${entry?.note ?? ""}`).not.toMatch(/only a person (releases|pays)|only after a person|model (can|may) lower/i);
    }
  });

  it("puts a question mark on every Settlement heading", () => {
    const text = source("settlement");
    for (const [id] of SETTLEMENT) expect(text).toContain(`<HelpDot id="${id}"`);
  });

  it("puts a question mark on the Carriers heading and the two form fields", () => {
    const text = source("carriers");
    for (const [id] of CARRIERS) expect(text).toContain(`<HelpDot id="${id}"`);
  });

  it("describes the Carriers screen as the payee register, not as credit standing", () => {
    const entry = helpFor("nav:/carriers");
    expect(entry?.body).toMatch(/PayPal account/);
    expect(`${entry?.body} ${entry?.note ?? ""}`).not.toMatch(/credit standing|cap kept/);
  });
});
