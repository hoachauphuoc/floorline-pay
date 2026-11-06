import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ALLOWED_GET,
  ALLOWED_POST,
  PASSWORD_ONLY_POST,
  matchRead,
  matchWrite,
  policyKey,
} from "./proxy-policy";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("the payment controls through the proxy", () => {
  it("allows reading the controls, and only that exact path", () => {
    expect(ALLOWED_GET.has("payments/controls")).toBe(true);
    expect(matchRead("payments/controls")).toBe(true);
    expect(matchRead("payments/controls/extra")).toBe(false);
  });

  it.each(["payments/pause", "payments/resume", "payments/limits"])("allows %s", (path) => {
    expect(matchWrite(path, ALLOWED_POST)).toBe(true);
  });

  it.each(["payments/pause", "payments/resume", "payments/limits"])(
    "asks for a password sign-in for %s",
    (path) => {
      expect(PASSWORD_ONLY_POST.has(path)).toBe(true);
      expect(PASSWORD_ONLY_POST.has(policyKey(path))).toBe(true);
    },
  );

  it("still lets no browser capture a payment or pay anyone", () => {
    for (const path of ["payments/capture", "payments/payout", "payments/resume/extra"]) {
      expect(matchWrite(path, ALLOWED_POST)).toBe(false);
    }
  });
});

describe("the payment controls calls", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("reads the controls through the proxy", async () => {
    const reply = { pause: { paused: false }, limits: null, limits_are_defaults: false, usage_today: { tenant_usd: "0", carriers: {} } };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(reply));
    globalThis.fetch = fetchMock;
    const { fetchPaymentControls } = await import("@/lib/api");
    expect(await fetchPaymentControls()).toEqual(reply);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/payments/controls");
  });

  it.each([
    ["pausePayments", "payments/pause"],
    ["resumePayments", "payments/resume"],
  ] as const)("%s posts the reason to %s", async (name, path) => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ paused: true }));
    globalThis.fetch = fetchMock;
    const api = await import("@/lib/api");
    await api[name]("a reason");
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/proxy/${path}`);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ reason: "a reason" });
  });

  it("publishes the limits with the reason", async () => {
    const limits = { max_payout_usd: 2000, carrier_daily_usd: 4000, tenant_daily_usd: 8000, carrier_daily_count: 3 };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ limits }));
    globalThis.fetch = fetchMock;
    const { publishPaymentLimits } = await import("@/lib/api");
    await publishPaymentLimits(limits, "quarter budget");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/payments/limits");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ limits, reason: "quarter budget" });
  });

  it("raises the controls' refusal text", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "limits must satisfy max_payout_usd <= carrier_daily_usd" }, 400),
    ) as unknown as typeof fetch;
    const { publishPaymentLimits } = await import("@/lib/api");
    await expect(
      publishPaymentLimits({ max_payout_usd: 9, carrier_daily_usd: 1, tenant_daily_usd: 1, carrier_daily_count: 1 }, "x"),
    ).rejects.toThrow("limits must satisfy");
  });
});
