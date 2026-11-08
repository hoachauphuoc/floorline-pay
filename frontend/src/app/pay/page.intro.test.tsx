import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PayPage />
    </QueryClientProvider>,
  );
}

describe("PayPage introduction", () => {
  beforeEach(() => {
    mocks.fetchPaymentLedger.mockReset();
    mocks.fetchPaymentControls.mockReset();
    mocks.fetchPaymentLedger.mockResolvedValue({ items: [], counts: { paid: 0, voided: 0, held: 0 } });
    mocks.fetchPaymentControls.mockResolvedValue({
      pause: { paused: false },
      limits: null,
      limits_are_defaults: true,
      usage_today: { tenant_usd: "0.00", carriers: {} },
    });
  });

  it("says funds reach the carrier on a published decision, a person's or the agent's inside a limit", () => {
    renderPage();
    expect(screen.getByText(/only on a published decision/)).toBeInTheDocument();
    expect(screen.queryByText(/only after a person releases/)).not.toBeInTheDocument();
  });

  it("says the agent may pay a clean case only below a limit a person published", () => {
    renderPage();
    expect(screen.getByText(/may pay a clean case only below a limit a person published/)).toBeInTheDocument();
    expect(screen.queryByText(/Only a person releases a payout/)).not.toBeInTheDocument();
  });
});
