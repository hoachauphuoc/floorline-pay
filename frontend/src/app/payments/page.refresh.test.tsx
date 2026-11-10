import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: vi.fn(),
}));

import { fetchPaymentLedger } from "@/lib/api";

import PaymentsPage from "./page";

describe("PaymentsPage refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(fetchPaymentLedger).mockReset();
    vi.mocked(fetchPaymentLedger).mockResolvedValue({ items: [], counts: {} });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks for the ledger again every 15 seconds now that the project is billed", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PaymentsPage />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("No payments yet")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(46_000);
    expect(fetchPaymentLedger).toHaveBeenCalledTimes(4);
  });
});
