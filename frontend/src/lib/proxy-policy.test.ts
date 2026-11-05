import { describe, expect, it } from "vitest";

import {
  ALLOWED_GET,
  ALLOWED_GET_BY_ID,
  ALLOWED_POST,
  describedReads,
  describedWrites,
  matchRead,
  matchWrite,
  ALLOWED_PUT,
  PASSWORD_ONLY_POST,
  PASSWORD_ONLY_PUT,
  policyKey,
} from "./proxy-policy";

describe("matchRead", () => {
  it("allows the exact reads and the id-carrying ones", () => {
    expect(matchRead("orchestrator/state")).toBe(true);
    expect(matchRead("auth/whoami")).toBe(true);
    expect(matchRead("auth/policy")).toBe(true);
    expect(matchRead("orchestrator/case/CASE-1")).toBe(true);
    expect(matchRead("orchestrator/case/CASE-1/dossier")).toBe(true);
    expect(matchRead("orchestrator/case/../dossier")).toBe(false);
    expect(matchRead("review/CASE-1/document")).toBe(true);
    expect(matchRead("compliance/audit/AUD-9")).toBe(true);
    expect(matchRead("worklist")).toBe(true);
    expect(matchRead("outcomes")).toBe(true);
  });

  it("refuses traversal hidden in an id and routes nobody listed", () => {
    expect(matchRead("orchestrator/case/..")).toBe(false);
    expect(matchRead("review/../document")).toBe(false);
    expect(matchRead("orchestrator/reset")).toBe(false);
    expect(matchRead("review/CASE-1/decide")).toBe(false);
  });
});

describe("matchWrite", () => {
  it("resolves the review routes by shape", () => {
    expect(matchWrite("review/CASE-1/decide", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/deep-review", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/request-evidence", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/reevaluate", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/approve", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/approve-exception", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/execute", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/second-approve", ALLOWED_POST)).toBe(true);
  });

  it("resolves the evidence sub-routes by shape", () => {
    expect(matchWrite("review/CASE-1/evidence/evreq-1/link", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/evidence/evreq-1/cancel", ALLOWED_POST)).toBe(true);
    expect(matchWrite("review/CASE-1/evidence/evreq-1/decide", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/CASE-1/evidence/../link", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/CASE-1/evidence/evreq-1/link/extra", ALLOWED_POST)).toBe(false);
  });

  it("does not forward the id-less templates as routes of their own", () => {
    expect(matchWrite("review/decide", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/deep-review", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/request-evidence", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/reevaluate", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/approve", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/approve-exception", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/execute", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/second-approve", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/evidence/link", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/evidence/cancel", ALLOWED_POST)).toBe(false);
    expect(matchWrite("cases/activity", ALLOWED_POST)).toBe(false);
  });

  it("forwards a case's activity report by shape, and nothing else under cases/", () => {
    expect(matchWrite("cases/CASE-1/activity", ALLOWED_POST)).toBe(true);
    expect(matchWrite("cases/../activity", ALLOWED_POST)).toBe(false);
    expect(matchWrite("cases/CASE-1/decide", ALLOWED_POST)).toBe(false);
    expect(matchWrite("cases/CASE-1/activity/extra", ALLOWED_POST)).toBe(false);
  });

  it("refuses the still-unproxied destructive routes and a traversal", () => {
    expect(matchWrite("orchestrator/drain", ALLOWED_POST)).toBe(false);
    expect(matchWrite("admin/backfill-rollups", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/../../orchestrator/reset/decide", ALLOWED_POST)).toBe(false);
    expect(matchWrite("review/CASE-1/document", ALLOWED_POST)).toBe(false);
  });

  it("forwards Clear board, and marks it as needing a password sign-in", () => {
    // Allowed is not the same as open: the BFF and Flask both refuse it to any
    // session that did not sign in with a password.
    expect(matchWrite("orchestrator/reset", ALLOWED_POST)).toBe(true);
    expect(PASSWORD_ONLY_POST.has("orchestrator/reset")).toBe(true);
    for (const path of PASSWORD_ONLY_POST) expect(ALLOWED_POST.has(path)).toBe(true);
  });

  it("marks publish, revoke, the prefilter, approve and execute as password-only", () => {
    expect([...PASSWORD_ONLY_POST].sort()).toEqual([
      "carriers/payee",
      "carriers/payee/verify",
      "governance/publish",
      "governance/revoke",
      "orchestrator/reset",
      "payments/decision/redeliver",
      "payments/freezes/lift",
      "payments/limits",
      "payments/pause",
      "payments/resume",
      "review/approve",
      "review/approve-exception",
      "review/execute",
      "review/second-approve",
    ]);
    expect([...PASSWORD_ONLY_PUT]).toEqual(["governance/prefilter-rules"]);
    for (const path of PASSWORD_ONLY_PUT) expect(ALLOWED_PUT.has(path)).toBe(true);
    // Simulate writes nothing and stays with the one-click judge. Publish does
    // not: review finding A01 showed a deny-all publish suspends the agent.
    expect(PASSWORD_ONLY_POST.has("governance/simulate")).toBe(false);
  });

  it("forwards the sanctions-news scan the Governance screen offers", () => {
    expect(matchWrite("governance/tavily-scan", ALLOWED_POST)).toBe(true);
  });
});

describe("the lists the Access Control screen shows", () => {
  it("include every read the proxy forwards, id routes too", () => {
    const shown = describedReads();
    // The hand-copied list this replaced showed 14 of 17.
    expect(shown).toHaveLength(ALLOWED_GET.size + ALLOWED_GET_BY_ID.length);
    expect(shown).toContain("orchestrator/case/<id>");
    expect(shown).toContain("orchestrator/case/<id>/dossier");
    expect(shown).toContain("review/<id>/document");
  });

  it("spell the review writes with their id segment", () => {
    const shown = describedWrites().map((w) => `${w.method} ${w.path}`);
    expect(shown).toContain("POST review/<id>/decide");
    expect(shown).toContain("POST review/<id>/request-evidence");
    expect(shown).toContain("POST review/<id>/approve");
    expect(shown).toContain("POST review/<id>/execute");
    expect(shown).toContain("POST review/<id>/approve-exception");
    expect(shown).toContain("POST review/<id>/second-approve");
    expect(shown).toContain("POST review/<id>/evidence/<id>/link");
    expect(shown).toContain("POST review/<id>/evidence/<id>/cancel");
    expect(shown).toContain("POST cases/<id>/activity");
    expect(shown).toContain("PUT governance/prefilter-rules");
    expect(shown).not.toContain("POST review/decide");
    expect(shown).not.toContain("POST cases/activity");
  });
});

describe("policyKey", () => {
  it("maps the id-carrying forms to the template the policy sets use", () => {
    expect(policyKey("review/CASE-1/approve")).toBe("review/approve");
    expect(policyKey("review/CASE-1/execute")).toBe("review/execute");
    expect(policyKey("review/CASE-1/approve-exception")).toBe("review/approve-exception");
    expect(policyKey("review/CASE-1/second-approve")).toBe("review/second-approve");
    expect(policyKey("review/CASE-1/evidence/evreq-9/cancel")).toBe("review/evidence/cancel");
    expect(policyKey("cases/CASE-1/activity")).toBe("cases/activity");
    expect(policyKey("orchestrator/reset")).toBe("orchestrator/reset");
    expect(policyKey("evidence/respond")).toBe("evidence/respond");
    // Every policy set is written in templates, so a mapped path must land in them.
    expect(PASSWORD_ONLY_POST.has(policyKey("review/CASE-1/approve"))).toBe(true);
    expect(ALLOWED_POST.has(policyKey("review/CASE-1/request-evidence"))).toBe(true);
  });

  it("leaves a path that is not an id-carrying form alone", () => {
    expect(policyKey("review/../orchestrator/reset")).toBe("review/../orchestrator/reset");
    expect(policyKey("review/approve")).toBe("review/approve");
  });
});

describe("the payout check write", () => {
  const path = "payments/cases/CASE-1/payout/refresh";

  it("is forwarded and needs no password, because it only reads PayPal", () => {
    expect(matchWrite(path, ALLOWED_POST)).toBe(true);
    expect(PASSWORD_ONLY_POST.has(policyKey(path))).toBe(false);
  });

  it("refuses the bare template, an id that is not a case id, a path that climbs and a longer path", () => {
    expect(matchWrite("payments/status/refresh", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/CASE 1/payout/refresh", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/../payout/refresh", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/CASE-1/payout/refresh/extra", ALLOWED_POST)).toBe(false);
  });
});

describe("the settlement retry write", () => {
  const path = "payments/cases/CASE-1/decision/redeliver";

  it("is forwarded, mapped to its policy key, and needs a password sign-in", () => {
    expect(matchWrite(path, ALLOWED_POST)).toBe(true);
    expect(policyKey(path)).toBe("payments/decision/redeliver");
    expect(PASSWORD_ONLY_POST.has(policyKey(path))).toBe(true);
  });

  it("refuses the bare template, an id that is not a case id, a path that climbs and a longer path", () => {
    expect(matchWrite("payments/decision/redeliver", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/CASE 1/decision/redeliver", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/../decision/redeliver", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/cases/CASE-1/decision/redeliver/extra", ALLOWED_POST)).toBe(false);
  });
});
