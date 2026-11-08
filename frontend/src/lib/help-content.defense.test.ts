import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { helpFor } from "@/lib/help-content";

const ENTRIES: Array<[string, string, RegExp]> = [
  ["defense.replay", "The attack replay", /neither PayPal nor a model/],
  ["defense.catches", "What the checks catch", /synthetic corpus/],
];

describe("help for Defense", () => {
  it.each(ENTRIES)("%s explains itself", (id, title, phrase) => {
    expect(helpFor(id)?.title).toBe(title);
    expect(helpFor(id)?.body).toMatch(phrase);
  });

  it("never says that only a person pays, or that a model lowers risk", () => {
    for (const [id] of ENTRIES) {
      const entry = helpFor(id);
      expect(`${entry?.body} ${entry?.note ?? ""}`).not.toMatch(/only a person (releases|pays)|only after a person|model (can|may) lower/i);
    }
  });

  it("puts a question mark on the summary line and on the catch-rate heading", () => {
    const text = readFileSync(path.resolve(__dirname, "..", "app", "defense", "page.tsx"), "utf8");
    for (const [id] of ENTRIES) expect(text).toContain(`<HelpDot id="${id}"`);
  });
});
