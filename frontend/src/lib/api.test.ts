import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Two fixes that both turn a failure into something the reader can act on.
 *
 * 1. `fetchAuditTrail` returned the raw payload, so a response without `items` made
 *    `trail.data?.items ?? []` at the call site produce `[]` -- and the screen rendered
 *    "No audit records match" over a populated trail. `fetchReviewQueue` had already been
 *    hardened for exactly this, with a comment recording the same consequence; the fix
 *    was applied to one sibling and not the other.
 *
 * 2. There was no client-side timeout. The BFF proxy has one, but it governs the proxy's
 *    call to the backend rather than the browser's call to the proxy, and with
 *    `retry: false` on nearly every query a hung handler left a skeleton on screen with
 *    nothing to end it.
 */

const ORIGINAL_FETCH = globalThis.fetch;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("fetchAuditTrail normalisation", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("returns an empty array rather than undefined when items is missing", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ next_cursor: null }),
    ) as unknown as typeof fetch;

    const { fetchAuditTrail } = await import("@/lib/api");
    const page = await fetchAuditTrail();

    // The important part is that `items` is an array, not undefined. An undefined here
    // reaches `?? []` at the call site and becomes an empty table with no error.
    expect(Array.isArray(page.items)).toBe(true);
    expect(page.items).toEqual([]);
  });

  it("passes real rows through unchanged", async () => {
    const rows = [{ audit_id: "A-1" }, { audit_id: "A-2" }];
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ items: rows, next_cursor: "cur" }),
    ) as unknown as typeof fetch;

    const { fetchAuditTrail } = await import("@/lib/api");
    const page = await fetchAuditTrail();

    expect(page.items).toHaveLength(2);
    expect(page.next_cursor).toBe("cur");
  });

  it("normalises a missing next_cursor to null rather than undefined", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ items: [] }),
    ) as unknown as typeof fetch;

    const { fetchAuditTrail } = await import("@/lib/api");
    const page = await fetchAuditTrail();

    expect(page.next_cursor).toBeNull();
  });
});

describe("client request timeout", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("attaches an abort signal to every request", async () => {
    // Typed as the real fetch signature so `mock.calls[0][1]` is the RequestInit rather
    // than an index into an empty tuple.
    const spy = vi.fn<typeof fetch>(async () => jsonResponse({ items: [] }));
    globalThis.fetch = spy;

    const { fetchAuditTrail } = await import("@/lib/api");
    await fetchAuditTrail();

    const init = spy.mock.calls[0]?.[1];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it("reports a timeout in words an operator can act on", async () => {
    // What the browser actually throws on an aborted fetch. Its own message is "signal
    // timed out", which is accurate and means nothing on a card.
    globalThis.fetch = vi.fn(async () => {
      throw new DOMException("signal timed out", "TimeoutError");
    }) as unknown as typeof fetch;

    const { fetchAuditTrail, ApiError } = await import("@/lib/api");

    await expect(fetchAuditTrail()).rejects.toThrow(ApiError);
    await expect(fetchAuditTrail()).rejects.toThrow(/took longer than/i);
  });

  it("leaves a non-timeout network error message intact", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;

    const { fetchAuditTrail } = await import("@/lib/api");
    await expect(fetchAuditTrail()).rejects.toThrow(/Failed to fetch/);
  });
});

describe("answers that leave the outcome unknown (N08)", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("reads a gateway timeout (504) as unreachable, like a 502", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ error: "upstream request timeout" }, 504),
    ) as unknown as typeof fetch;

    const { executeApproval, ApiError } = await import("@/lib/api");
    const failure = await executeApproval("CASE-1", "evapr-1").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as InstanceType<typeof ApiError>).kind).toBe("unreachable");
    expect((failure as InstanceType<typeof ApiError>).status).toBe(504);
  });

  it("reads an answer whose body breaks off as unreachable, not as a raw error", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => {
        throw new TypeError("network error");
      },
    }) as unknown as Response) as unknown as typeof fetch;

    const { approveCase, ApiError } = await import("@/lib/api");
    const failure = await approveCase("CASE-1", "release", "Reviewed").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as InstanceType<typeof ApiError>).kind).toBe("unreachable");
    expect((failure as InstanceType<typeof ApiError>).message).toMatch(/dropped while the answer was read/);
  });

  it("keeps a refusal the server sent (403) as an answer", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse(
        { error: "Releasing needs a password sign-in." }, 403,
      ),
    ) as unknown as typeof fetch;

    const { executeApproval, ApiError } = await import("@/lib/api");
    const failure = await executeApproval("CASE-1", "evapr-1").catch((error: unknown) => error);
    expect((failure as InstanceType<typeof ApiError>).kind).toBe("http");
  });

  it("reads a 2xx whose body is not JSON as a parse failure", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => "<html>gateway</html>",
    }) as unknown as Response) as unknown as typeof fetch;

    const { executeApproval, ApiError } = await import("@/lib/api");
    const failure = await executeApproval("CASE-1", "evapr-1").catch((error: unknown) => error);
    expect((failure as InstanceType<typeof ApiError>).kind).toBe("parse");
  });
});

describe("writes that answer slowly or with a refusal", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "false");
  });

  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("returns an unreadable document as an answer, not an error", async () => {
    // 422 is "this file could not be transcribed". Throwing it put the generic red
    // failure card over an ordinary answer about the document.
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ accepted: false, error: "document could not be transcribed" }, 422),
    ) as unknown as typeof fetch;

    const { uploadDocument } = await import("@/lib/api");
    const result = await uploadDocument(new File(["x"], "scan.png", { type: "image/png" }));
    expect(result.accepted).toBe(false);
    expect(result.error).toMatch(/could not be transcribed/);
  });

  it("gives an upload the long timeout, not the 20-second read ceiling", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    try {
      globalThis.fetch = vi.fn(async () =>
        jsonResponse({ accepted: true, case_id: "CASE-1" }, 202),
      ) as unknown as typeof fetch;

      const { uploadDocument, LONG_WRITE_TIMEOUT_MS } = await import("@/lib/api");
      await uploadDocument(new File(["x"], "scan.png", { type: "image/png" }));
      expect(timeout).toHaveBeenCalledWith(LONG_WRITE_TIMEOUT_MS);
      expect(LONG_WRITE_TIMEOUT_MS).toBeGreaterThan(120_000);
    } finally {
      timeout.mockRestore();
    }
  });

  it("gives a submitted shipment the long timeout, because it runs the workflow before it answers", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    try {
      globalThis.fetch = vi.fn(async () =>
        jsonResponse({ case_id: "CASE-1" }, 202),
      ) as unknown as typeof fetch;

      const { submitShipmentEvent, LONG_WRITE_TIMEOUT_MS } = await import("@/lib/api");
      await submitShipmentEvent({ shipment_id: "SHP-1" });
      expect(timeout).toHaveBeenCalledWith(LONG_WRITE_TIMEOUT_MS);
    } finally {
      timeout.mockRestore();
    }
  });

  it("carries a password-required refusal through to the screen", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse(
        {
          error: "password_session_required",
          detail: "This action needs a password sign-in.",
          required_auth: "password",
          session_method: "one_click",
        },
        403,
      ),
    ) as unknown as typeof fetch;

    const { resetBoard, ApiError } = await import("@/lib/api");
    const failure = await resetBoard().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as InstanceType<typeof ApiError>).requiredAuth).toBe("password");
    expect((failure as InstanceType<typeof ApiError>).status).toBe(403);
  });
});
