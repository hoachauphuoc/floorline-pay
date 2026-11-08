import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createPaymentOrder: vi.fn(),
}));

import { helpFor } from "@/lib/help-content";
import { setHelpMode } from "@/lib/help-mode";

import BookPage from "./page";

const ENTRIES: Array<[string, string, RegExp]> = [
  ["book.shipping_cost", "Freight to pay (USD)", /held, not taken/],
  ["book.carrier_payee_email", "Carrier PayPal account for this load", /holds the payout for a person/],
  ["book.payout_amount", "Payout to the carrier (USD)", /blank means the freight/],
  ["book.after-paypal", "After PayPal", /without a second hold/],
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BookPage />
    </QueryClientProvider>,
  );
}

describe("BookPage help", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setHelpMode(true);
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

  it("shows a question mark beside the money fields and after the PayPal button with help on", () => {
    renderPage();
    for (const [, title] of ENTRIES) {
      expect(screen.getByRole("button", { name: `What is ${title}?` })).toBeInTheDocument();
    }
  });

  it("shows no question mark with help off", () => {
    setHelpMode(false);
    renderPage();
    expect(screen.queryByRole("button", { name: /^What is / })).not.toBeInTheDocument();
  });
});
