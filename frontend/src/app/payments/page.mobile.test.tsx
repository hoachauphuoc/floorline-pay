import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchPaymentLedger: vi.fn(),
}));

import { fetchPaymentLedger } from "@/lib/api";

import PaymentsPage from "./page";

describe("PaymentsPage on a phone", () => {
  it("lets the state and the reason wrap instead of widening the table", async () => {
    vi.mocked(fetchPaymentLedger).mockResolvedValue({
      items: [
        {
          case_id: "CASE-B",
          state: "held",
          reason: "the payment checks hold this payout (PAYEE_MISMATCH); nothing is paid",
        },
      ],
      counts: { held: 1 },
    } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PaymentsPage />
      </QueryClientProvider>,
    );
    const table = await screen.findByRole("table");
    for (const text of ["Payout held", /PAYEE_MISMATCH/]) {
      const tokens = (within(table).getByText(text).closest("td")?.className ?? "").split(" ");
      expect(tokens).toContain("whitespace-normal");
      expect(tokens).not.toContain("whitespace-nowrap");
    }
  });
});
