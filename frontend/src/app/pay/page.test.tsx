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

const LEDGER = {
  items: [
    { case_id: "C1", state: "paid", payout_status: { state: "confirmed", status: "SUCCESS", at: null } },
    { case_id: "C2", state: "paid", payout_status: { state: "unconfirmed", status: null, at: null } },
    { case_id: "C3", state: "paid", payout_status: { state: "pending", status: "PENDING", at: null } },
    { case_id: "C4", state: "paid", payout_status: { state: "failed", status: "RETURNED", at: null } },
  ],
  counts: { paid: 4, voided: 1, held: 2 },
};

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
    expect(await screen.findByLabelText("Paid to carriers, confirmed by PayPal: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Payouts accepted, not confirmed yet: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Payouts failed at PayPal: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Returned to shippers: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Held by the checks: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Sent for payout today in USD: 1250.00")).toBeInTheDocument();
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
