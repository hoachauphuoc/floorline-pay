import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

import { helpFor } from "@/lib/help-content";
import { setHelpMode } from "@/lib/help-mode";

import PayPage from "./page";

const ENTRIES: Array<[string, string, RegExp]> = [
  ["pay.paid-confirmed", "Paid to carriers, confirmed by PayPal", /PayPal has since confirmed/],
  ["pay.paid-accepted", "Payouts accepted, not confirmed yet", /not confirmed it yet/],
  ["pay.paid-waiting", "Payouts sent, not received yet", /still pending/],
  ["pay.paid-failed", "Payouts failed at PayPal", /payout failed/],
  ["pay.paid-returned", "Payouts returned by PayPal", /reversed/],
  ["pay.voided", "Returned to shippers", /voided the PayPal hold/],
  ["pay.held", "Held by the checks", /refused to move money/],
  ["pay.decision-not-sent", "Decisions not sent", /never reached the settler/],
  ["pay.sent-today", "Sent for payout today in USD", /since midnight UTC/],
];

const LEDGER = {
  items: [{ case_id: "C1", state: "paid", payout_status: { state: "confirmed", status: "SUCCESS", at: null } }],
  counts: { paid: 1, voided: 1, held: 2 },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PayPage />
    </QueryClientProvider>,
  );
}

describe("PayPage help", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setHelpMode(true);
    mocks.fetchPaymentLedger.mockReset();
    mocks.fetchPaymentControls.mockReset();
    mocks.fetchPaymentLedger.mockResolvedValue(LEDGER);
    mocks.fetchPaymentControls.mockResolvedValue({
      pause: { paused: false },
      limits: null,
      limits_are_defaults: true,
      usage_today: { tenant_usd: "1250.00", carriers: {} },
    });
  });
  afterEach(() => setHelpMode(false));

  it.each(ENTRIES)("%s explains itself", (id, title, phrase) => {
    expect(helpFor(id)?.title).toBe(title);
    expect(helpFor(id)?.body).toMatch(phrase);
  });

  it("never says that only a person pays, or that a model lowers risk", () => {
    for (const [id] of ENTRIES) {
      const entry = helpFor(id);
      expect(`${entry?.body} ${entry?.note ?? ""}`).not.toMatch(/only a person (releases|pays)|only after a person|model (can|may) lower/i);
    }
  });

  it("shows a question mark beside every live figure with help on", async () => {
    renderPage();
    for (const [, title] of ENTRIES) {
      expect(await screen.findByRole("button", { name: `What is ${title}?` })).toBeInTheDocument();
    }
  });

  it("shows no question mark with help off", async () => {
    setHelpMode(false);
    renderPage();
    await screen.findByLabelText("Paid to carriers, confirmed by PayPal: 1");
    expect(screen.queryByRole("button", { name: /^What is / })).not.toBeInTheDocument();
  });
});
