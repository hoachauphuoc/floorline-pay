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

describe("the carrier registry through the proxy", () => {
  it("allows reading the registry, and only that exact path", () => {
    expect(ALLOWED_GET.has("carriers")).toBe(true);
    expect(matchRead("carriers")).toBe(true);
    expect(matchRead("carriers/mekonghaulageco")).toBe(false);
  });

  it("allows registering a payee and verifying one registration of one carrier", () => {
    expect(matchWrite("carriers/payee", ALLOWED_POST)).toBe(true);
    expect(matchWrite("carriers/mekonghaulageco/payee/2/verify", ALLOWED_POST)).toBe(true);
  });

  it("does not allow the template of the id-carrying form as a route of its own", () => {
    expect(matchWrite("carriers/payee/verify", ALLOWED_POST)).toBe(false);
  });

  it.each([
    "carriers/Mekong/payee/2/verify",
    "carriers/mekong-haulage/payee/2/verify",
    "carriers/mekong/payee/two/verify",
    "carriers/mekong/payee/1234567890/verify",
    "carriers/mekong/payee/2/approve",
    "carriers/mekong/payee/2/verify/extra",
    "carriers/mekong/other/2/verify",
  ])("refuses %s", (path) => {
    expect(matchWrite(path, ALLOWED_POST)).toBe(false);
  });

  it("asks for a password sign-in for both writes, by the policy key of the id-carrying form", () => {
    expect(PASSWORD_ONLY_POST.has("carriers/payee")).toBe(true);
    expect(policyKey("carriers/mekonghaulageco/payee/2/verify")).toBe("carriers/payee/verify");
    expect(PASSWORD_ONLY_POST.has(policyKey("carriers/mekonghaulageco/payee/2/verify"))).toBe(true);
    expect(PASSWORD_ONLY_POST.has(policyKey("carriers/payee"))).toBe(true);
  });
});

describe("the carrier registry calls", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("reads the registry through the proxy", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ items: [] }));
    globalThis.fetch = fetchMock;
    const { fetchCarriers } = await import("@/lib/api");
    expect(await fetchCarriers()).toEqual({ items: [] });
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/carriers");
  });

  it("registers a payee with the carrier and account as the body", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ carrier_key: "k", version: 1, status: "pending_verification" }, 201),
    );
    globalThis.fetch = fetchMock;
    const { registerCarrierPayee } = await import("@/lib/api");
    await registerCarrierPayee({ carrier_name: "Mekong", payee_email: "a@b.example" });
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/carriers/payee");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ carrier_name: "Mekong", payee_email: "a@b.example" });
  });

  it("verifies one registration with the callback note as the body", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ carrier_key: "k", version: 2, status: "verified" }),
    );
    globalThis.fetch = fetchMock;
    const { verifyCarrierPayee } = await import("@/lib/api");
    await verifyCarrierPayee("mekonghaulageco", 2, "called them");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/carriers/mekonghaulageco/payee/2/verify");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ callback_note: "called them" });
  });

  it("raises the registry's refusal text", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "a payee must be verified by a different person than the one who registered it" }, 403),
    ) as unknown as typeof fetch;
    const { verifyCarrierPayee } = await import("@/lib/api");
    await expect(verifyCarrierPayee("k", 1, "n")).rejects.toThrow("different person");
  });
});
