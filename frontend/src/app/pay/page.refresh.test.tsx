import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchPaymentLedger: vi.fn(),
  fetchPaymentControls: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: mocks.fetchPaymentLedger,
  fetchPaymentControls: mocks.fetchPaymentControls,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a", name: "Tenant A", descriptor: "Test tenant" } }),
}));

import PayPage from "./page";

describe("PayPage refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.fetchPaymentLedger.mockReset();
    mocks.fetchPaymentControls.mockReset();
    mocks.fetchPaymentLedger.mockResolvedValue({ items: [], counts: {} });
    mocks.fetchPaymentControls.mockResolvedValue({
      pause: { paused: false },
      limits: null,
      limits_are_defaults: true,
      usage_today: { tenant_usd: "0.00", carriers: {} },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks for the ledger and the controls once and does not poll them while the read quota is the limit", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PayPage />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mocks.fetchPaymentControls).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(46_000);
    expect(mocks.fetchPaymentLedger).toHaveBeenCalledTimes(1);
    expect(mocks.fetchPaymentControls).toHaveBeenCalledTimes(1);
  });
});
