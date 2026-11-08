import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: vi.fn().mockReturnValue(new Promise(() => {})),
}));

import PaymentsPage from "./page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PaymentsPage />
    </QueryClientProvider>,
  );
}

describe("PaymentsPage introduction", () => {
  it("says the carrier is paid after a published decision, a person's or the agent's inside a limit", () => {
    renderPage();
    expect(screen.getByText(/paid to the carrier after a published decision/)).toBeInTheDocument();
    expect(screen.getByText(/inside a limit a person set/)).toBeInTheDocument();
  });

  it("no longer says that a person released the shipment", () => {
    renderPage();
    expect(screen.queryByText(/after a person released/)).not.toBeInTheDocument();
  });
});
