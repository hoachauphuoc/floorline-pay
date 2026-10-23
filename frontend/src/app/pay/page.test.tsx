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

const LEDGER = { items: [], counts: { paid: 3, voided: 1, held: 2 } };

function controls(paused: boolean) {
  return {
    pause: { paused },
    limits: null,
    limits_are_defaults: true,
    usage_today: { tenant_usd: "1250.00", carriers: {} },
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PayPage />
    </QueryClientProvider>,
  );
}

describe("PayPage", () => {
  beforeEach(() => {
    mocks.fetchPaymentLedger.mockReset();
    mocks.fetchPaymentControls.mockReset();
    mocks.fetchPaymentLedger.mockResolvedValue(LEDGER);
    mocks.fetchPaymentControls.mockResolvedValue(controls(false));
  });

  it("names the four steps and links each to the screen where it happens", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Floorline Pay" })).toBeInTheDocument();
    const hrefs = [
      ["Book and hold", "/book"],
      ["Screen the payout", "/defense"],
      ["A person releases", "/review"],
      ["Paid or returned", "/payments"],
    ];
    for (const [title, href] of hrefs) {
      expect(screen.getByRole("link", { name: new RegExp(title) })).toHaveAttribute("href", href);
    }
  });

  it("shows the live figures and that payments are running", async () => {
    renderPage();
    expect(await screen.findByLabelText("Paid to carriers: 3")).toBeInTheDocument();
    expect(screen.getByLabelText("Returned to shippers: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Held by the checks: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Paid today in USD: 1250.00")).toBeInTheDocument();
    expect(screen.getByText("Payments are running")).toBeInTheDocument();
  });

  it("says when payments are paused", async () => {
    mocks.fetchPaymentControls.mockResolvedValue(controls(true));
    renderPage();
    expect(await screen.findByText("Payments are paused")).toBeInTheDocument();
  });

  it("shows an error instead of figures when the ledger cannot be read, and keeps the steps", async () => {
    mocks.fetchPaymentLedger.mockRejectedValue(new Error("down"));
    renderPage();
    expect(await screen.findByText("That request failed")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Paid to carriers/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Book and hold/ })).toBeInTheDocument();
  });
});
