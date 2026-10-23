import { describe, expect, it } from "vitest";

import {
  ALLOWED_POST,
  PASSWORD_ONLY_POST,
  describedReads,
  matchRead,
  matchWrite,
} from "./proxy-policy";

/**
 * The reads and the one write of the Defense and Settlement screens.
 *
 * The replay, its evaluation table, the frozen parties and the risk of one case are reads for anyone
 * signed in; lifting a freeze loosens a hold, so it is a write that only a password sign-in may make.
 */
describe("the proxy policy for the Defense and Settlement screens", () => {
  it.each(["defense/scenarios", "defense/evaluation", "payments/freezes", "payments/cases/CASE-1/risk", "payments/cases/CASE-1"])(
    "forwards the read %s",
    (path) => {
      expect(matchRead(path)).toBe(true);
    },
  );

  it.each(["defense", "defense/scenarios/extra", "payments/freezes/lift", "payments/cases/CASE-1/risk/extra", "payments/cases/../risk"])(
    "does not forward the read %s",
    (path) => {
      expect(matchRead(path)).toBe(false);
    },
  );

  it("describes the new reads", () => {
    const shown = describedReads();
    expect(shown).toContain("defense/scenarios");
    expect(shown).toContain("defense/evaluation");
    expect(shown).toContain("payments/freezes");
  });

  it("forwards lifting a freeze, and only for a password sign-in", () => {
    expect(matchWrite("payments/freezes/lift", ALLOWED_POST)).toBe(true);
    expect(PASSWORD_ONLY_POST.has("payments/freezes/lift")).toBe(true);
  });

  it("forwards no write that could move money", () => {
    for (const path of ALLOWED_POST) {
      expect(path).not.toMatch(/capture|payout|settle|void/);
    }
  });
});
