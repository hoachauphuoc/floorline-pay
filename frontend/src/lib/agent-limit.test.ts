import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ALLOWED_POST, PASSWORD_ONLY_POST, matchWrite, policyKey } from "./proxy-policy";

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("the agent limit through the proxy", () => {
  it("allows publishing the limit, and only that exact path", () => {
    expect(matchWrite("payments/agent/limit", ALLOWED_POST)).toBe(true);
    expect(matchWrite("payments/agent/limit/extra", ALLOWED_POST)).toBe(false);
    expect(matchWrite("payments/agent", ALLOWED_POST)).toBe(false);
  });

  it("asks for a password sign-in", () => {
    expect(PASSWORD_ONLY_POST.has("payments/agent/limit")).toBe(true);
    expect(PASSWORD_ONLY_POST.has(policyKey("payments/agent/limit"))).toBe(true);
  });
});

describe("publishAgentLimit", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("posts the amount and the reason", async () => {
    const reply = { agent_limit: { limit_usd: "1000", published_by: "admin@forwarder.example" } };
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(reply));
    globalThis.fetch = fetchMock;
    const { publishAgentLimit } = await import("@/lib/api");
    expect(await publishAgentLimit(1000, "first month")).toEqual(reply);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/payments/agent/limit");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ limit_usd: 1000, reason: "first month" });
  });

  it("raises the refusal text", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "the agent limit must stay below the second-approval threshold of 2500 USD" }, 400),
    ) as unknown as typeof fetch;
    const { publishAgentLimit } = await import("@/lib/api");
    await expect(publishAgentLimit(2500, "too high")).rejects.toThrow("below the second-approval threshold");
  });
});
