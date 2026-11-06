import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ALLOWED_GET_BY_ID, matchRead } from "./proxy-policy";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("the payment state read", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("is allowed through the proxy for one case id and nothing else", () => {
    expect(matchRead("payments/cases/CASE-1")).toBe(true);
    expect(matchRead("payments/cases")).toBe(false);
    expect(matchRead("payments/cases/CASE-1/extra")).toBe(false);
    expect(matchRead("payments/cases/..%2Fsecrets")).toBe(false);
    expect(matchRead("payments/cases/a b")).toBe(false);
    expect(ALLOWED_GET_BY_ID.some(([prefix]) => prefix === "payments/cases")).toBe(true);
  });

  it("does not open any payments write through the proxy", () => {
    expect(matchRead("payments/orders")).toBe(false);
  });

  it("reads the case's payment state through the proxy with the id encoded", async () => {
    const reply = { case_id: "CASE 1", payment_case: true, state: "paid" };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(reply));
    globalThis.fetch = fetchMock;
    const { fetchPaymentState } = await import("@/lib/api");
    const state = await fetchPaymentState("CASE 1");
    expect(state.state).toBe("paid");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/payments/cases/CASE%201");
  });

  it("raises the upstream error text when the case is not found", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "case not found" }, 404),
    ) as unknown as typeof fetch;
    const { fetchPaymentState } = await import("@/lib/api");
    await expect(fetchPaymentState("CASE-9")).rejects.toThrow("case not found");
  });
});
