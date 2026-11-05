import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: vi.fn(),
}));

import { fetchPaymentLedger } from "@/lib/api";

import PaymentsPage from "./page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PaymentsPage />
    </QueryClientProvider>,
  );
}

const STEP = { status: "ok", http_status: 201, error: null };

describe("PaymentsPage", () => {
  beforeEach(() => {
    vi.mocked(fetchPaymentLedger).mockReset();
  });

  it("says so when there are no payments yet", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({ items: [], counts: {} });
    renderPage();
    await waitFor(() => expect(screen.getByText("No payments yet")).toBeInTheDocument());
  });

  it("lists each case with its state, its reason and the PayPal ids, linked to the case", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [
        {
          case_id: "CASE-A",
          state: "paid",
          reason: "captured and paid",
          capture: { ...STEP, paypal_id: "CAP-1" },
          payout: { ...STEP, paypal_id: "PAYOUT-1" },
          at: "2026-11-02T10:00:00+00:00",
        },
        {
          case_id: "CASE-B",
          state: "held",
          reason: "the payment checks hold this payout (PAYEE_MISMATCH); nothing is paid",
        },
      ],
      counts: { paid: 1, held: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("link", { name: "CASE-A" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "CASE-A" })).toHaveAttribute("href", "/cases/CASE-A");
    expect(screen.getByRole("link", { name: "CASE-B" })).toHaveAttribute("href", "/cases/CASE-B");
    expect(screen.getAllByText("Payout accepted by PayPal, not confirmed yet").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Payout held").length).toBeGreaterThan(0);
    expect(screen.getByText("captured and paid")).toBeInTheDocument();
    expect(screen.getByText("CAP-1")).toBeInTheDocument();
    expect(screen.getByText("PAYOUT-1")).toBeInTheDocument();
    expect(screen.getByText(/PAYEE_MISMATCH/)).toBeInTheDocument();
  });

  it("shows a count for each state", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [
        { case_id: "CASE-1", state: "held" },
        { case_id: "CASE-2", state: "held" },
        { case_id: "CASE-3", state: "failed" },
      ],
      counts: { held: 2, failed: 1 },
    });
    renderPage();
    const chips = await screen.findByRole("list", { name: "Payments by state" });
    expect(chips).toHaveTextContent("Payout held2");
    expect(chips).toHaveTextContent("Payment failed1");
  });

  it("counts a paid case as paid only when PayPal confirmed the payout", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [
        { case_id: "CASE-1", state: "paid", payout_status: { state: "confirmed", status: "SUCCESS", at: null } },
        { case_id: "CASE-2", state: "paid", payout_status: { state: "unconfirmed", status: null, at: null } },
        { case_id: "CASE-3", state: "paid", payout_status: { state: "unconfirmed", status: null, at: null } },
        { case_id: "CASE-4", state: "paid", payout_status: { state: "failed", status: "RETURNED", at: null } },
      ],
      counts: { paid: 4 },
    });
    renderPage();
    const chips = await screen.findByRole("list", { name: "Payments by state" });
    expect(chips).toHaveTextContent("Paid to the carrier, confirmed by PayPal1");
    expect(chips).toHaveTextContent("Payout accepted by PayPal, not confirmed yet2");
    expect(chips).toHaveTextContent("Payout failed at PayPal1");
  });

  it("never shows a state it does not know as a success", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [{ case_id: "CASE-X", state: "refunded" as never }],
      counts: { refunded: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByRole("link", { name: "CASE-X" })).toBeInTheDocument());
    expect(screen.getAllByText("Status not recognised").length).toBeGreaterThan(0);
    expect(screen.queryByText("Paid to the carrier")).not.toBeInTheDocument();
  });

  it("shows the error when the ledger cannot be read", async () => {
    vi.mocked(fetchPaymentLedger).mockRejectedValue(new Error("ledger unavailable"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/ledger unavailable/)).toBeInTheDocument());
  });
});
