import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: vi.fn(),
}));

import { fetchPaymentLedger } from "@/lib/api";
import { helpFor } from "@/lib/help-content";
import { setHelpMode } from "@/lib/help-mode";

import PaymentsPage from "./page";

const ENTRIES: Array<[string, string, RegExp]> = [
  ["payments.state", "State", /last did with this case/],
  ["payments.why", "Why", /reason the settler recorded/],
  ["payments.captured", "Captured", /money was taken/],
  ["payments.paid-out", "Paid out", /not that it arrived/],
  ["payments.voided", "Voided", /released the shipper's hold/],
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PaymentsPage />
    </QueryClientProvider>,
  );
}

describe("PaymentsPage help", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setHelpMode(true);
    vi.mocked(fetchPaymentLedger).mockReset();
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [{ case_id: "CASE-B", state: "held", reason: "the payment checks hold this payout (PAYEE_MISMATCH); nothing is paid" }],
      counts: { held: 1 },
    } as never);
  });
  afterEach(() => setHelpMode(false));

  it.each(ENTRIES)("%s explains itself", (id, title, phrase) => {
    expect(helpFor(id)?.title).toBe(title);
    expect(helpFor(id)?.body).toMatch(phrase);
  });

  it("shows a question mark in each column header with help on", async () => {
    renderPage();
    await screen.findByRole("table");
    for (const [, title] of ENTRIES) {
      expect(await screen.findByRole("button", { name: `What is ${title}?` })).toBeInTheDocument();
    }
  });

  it("shows no question mark with help off", async () => {
    setHelpMode(false);
    renderPage();
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /^What is / })).not.toBeInTheDocument();
  });
});
