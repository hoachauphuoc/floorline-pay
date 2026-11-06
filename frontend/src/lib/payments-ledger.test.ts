import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ALLOWED_GET, matchRead } from "./proxy-policy";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("the payment ledger read", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("is an allowed read through the proxy, and only that exact path", () => {
    expect(ALLOWED_GET.has("payments/ledger")).toBe(true);
    expect(matchRead("payments/ledger")).toBe(true);
    expect(matchRead("payments/ledger/extra")).toBe(false);
  });

  it("reads the ledger through the proxy", async () => {
    const reply = { items: [], counts: {} };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(reply));
    globalThis.fetch = fetchMock;
    const { fetchPaymentLedger } = await import("@/lib/api");
    const ledger = await fetchPaymentLedger();
    expect(ledger).toEqual(reply);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/payments/ledger");
  });

  it("raises the upstream error text when the ledger cannot be read", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "ledger unavailable" }, 503),
    ) as unknown as typeof fetch;
    const { fetchPaymentLedger } = await import("@/lib/api");
    await expect(fetchPaymentLedger()).rejects.toThrow("ledger unavailable");
  });
});
